"""Testes do dispatcher — a fronteira entre coreografia e orquestracao.

O que se verifica aqui e principalmente **o nome da execucao**, porque e ele
que implementa a camada 1 da idempotencia. Um nome nao deterministico faria a
reentrega da SQS virar processamento duplicado sem que nada quebrasse de forma
visivel: os numeros do painel simplesmente nao fechariam.
"""

import json

import pytest

from idempotency import execution_name, key
from local import runtime
from local.doubles import ClientError, ExecutionAlreadyExists
from src.handlers.dispatcher import handler as dispatcher
from src.handlers.dispatcher.handler import lambda_handler


class Context:
    aws_request_id = "req-1"


EQUACAO = '{"a": 1, "b": -5, "c": 6}'


def record(message_id="msg-1", body=EQUACAO, batch="b1", chaos=None, receive_count=1):
    attributes = {}

    if batch:
        attributes["BatchId"] = {"stringValue": batch, "dataType": "String"}

    if chaos:
        attributes["Chaos"] = {"stringValue": json.dumps(chaos), "dataType": "String"}

    return {
        "messageId": message_id,
        "body": body,
        "attributes": {"ApproximateReceiveCount": str(receive_count)},
        "messageAttributes": attributes,
        "eventSource": "aws:sqs",
    }


def call(*records):
    return lambda_handler({"Records": list(records)}, Context())


@pytest.fixture
def sfn(aws):
    return aws["stepfunctions"]


# ---------------------------------------------------------------- execucao


def test_uma_mensagem_vira_uma_execucao(sfn):
    resultado = call(record())

    assert resultado == {"batchItemFailures": []}
    assert len(sfn.executions) == 1


def test_o_nome_da_execucao_vem_do_conteudo(sfn):
    call(record())

    assert list(sfn.executions) == [execution_name(key("b1", EQUACAO))]


def test_a_entrada_da_execucao_carrega_equacao_e_meta(sfn):
    call(record())

    entrada = list(sfn.executions.values())[0].input

    assert entrada["equation"] == {"a": 1, "b": -5, "c": 6}
    assert entrada["meta"]["batch_id"] == "b1"
    assert entrada["meta"]["message_id"] == "msg-1"
    assert entrada["meta"]["idempotency_key"] == key("b1", EQUACAO)
    assert entrada["meta"]["receive_count"] == 1


def test_um_lote_vira_varias_execucoes(sfn):
    call(*[record(message_id="m%d" % i, body='{"a": 1, "b": %d, "c": 1}' % i) for i in range(10)])

    assert len(sfn.executions) == 10


# ------------------------------------------------------------ idempotencia


def test_a_mesma_mensagem_reentregue_nao_vira_segunda_execucao(sfn):
    call(record(message_id="msg-1"))
    call(record(message_id="msg-1", receive_count=2))

    assert len(sfn.executions) == 1
    assert sfn.rejected == 1


def test_reentrega_recusada_nao_e_falha(sfn):
    """Devolver para retry faria a SQS reentregar algo que ja foi processado."""
    call(record())

    assert call(record()) == {"batchItemFailures": []}


def test_equacoes_iguais_na_mesma_carga_deduplicam(sfn):
    call(record(message_id="a"), record(message_id="b"))

    assert len(sfn.executions) == 1


def test_a_ordem_das_chaves_nao_cria_execucao_nova(sfn):
    call(record(body='{"a": 1, "b": -5, "c": 6}'))
    call(record(body='{"c": 6, "b": -5, "a": 1}'))

    assert len(sfn.executions) == 1


def test_cargas_diferentes_processam_a_mesma_equacao(sfn):
    call(record(batch="b1"))
    call(record(batch="b2"))

    assert len(sfn.executions) == 2


def test_mensagem_sem_batch_id_usa_o_proprio_message_id(sfn):
    """Envio manual repetido precisa rodar sempre; messageId e unico."""
    call(record(message_id="m1", batch=None))
    call(record(message_id="m2", batch=None))

    assert len(sfn.executions) == 2


# ------------------------------------------------------- corpos problematicos


def test_corpo_invalido_ainda_vira_execucao(sfn):
    """Recusar aqui produziria mensagem que some sem rastro no diagrama."""
    call(record(body="{isto nao e json"))

    entrada = list(sfn.executions.values())[0].input

    assert entrada["equation"] is None
    assert entrada["meta"]["raw_body"] == "{isto nao e json"


def test_corpo_invalido_termina_na_dead_letter(aws):
    call(record(body="{isto nao e json"))

    recusadas = aws["sqs"].queue(runtime.LOCAL_DEAD_LETTER_URL).messages

    assert len(recusadas) == 1
    assert json.loads(recusadas[0]["body"])["error"]["Error"] == "InvalidEquation"


def test_corpo_que_e_lista_vira_raw_body(sfn):
    call(record(body="[1, 2, 3]"))

    assert list(sfn.executions.values())[0].input["equation"] is None


def test_dois_corpos_invalidos_diferentes_nao_colidem(sfn):
    call(record(message_id="a", body="{quebrado 1"), record(message_id="b", body="{quebrado 2"))

    assert len(sfn.executions) == 2


# ------------------------------------------------------------------- caos


def test_o_caos_do_atributo_entra_no_meta(sfn):
    call(record(chaos={"state": "Delta", "fails": 2}))

    assert list(sfn.executions.values())[0].input["meta"]["chaos"] == {"state": "Delta", "fails": 2}


def test_caos_invalido_e_ignorado_sem_derrubar_a_mensagem(sfn):
    registro = record()
    registro["messageAttributes"]["Chaos"] = {"stringValue": "{quebrado", "dataType": "String"}

    call(registro)

    assert "chaos" not in list(sfn.executions.values())[0].input["meta"]


def test_atributo_em_maiuscula_tambem_e_lido(sfn):
    """O evento da Lambda usa stringValue; a resposta do boto3, StringValue."""
    registro = record(batch=None)
    registro["messageAttributes"]["BatchId"] = {"StringValue": "b9", "DataType": "String"}

    call(registro)

    assert list(sfn.executions.values())[0].input["meta"]["batch_id"] == "b9"


# ------------------------------------------------------------------ falhas


def test_falha_inesperada_vira_retry_so_daquela_mensagem(monkeypatch, sfn):
    original = sfn.start_execution

    def instavel(**kwargs):
        if kwargs["name"].endswith(key("b1", EQUACAO)):
            raise ClientError("ThrottlingException", "slow down")

        return original(**kwargs)

    monkeypatch.setattr(sfn, "start_execution", instavel)

    resultado = call(record(message_id="ruim"), record(message_id="bom", body='{"a":2,"b":4,"c":1}'))

    assert resultado == {"batchItemFailures": [{"itemIdentifier": "ruim"}]}
    assert len(sfn.executions) == 1


def test_uma_falha_nao_derruba_o_lote_inteiro(monkeypatch, sfn):
    def sempre_falha(**kwargs):
        raise ClientError("ServiceUnavailable", "")

    monkeypatch.setattr(sfn, "start_execution", sempre_falha)

    resultado = call(record(message_id="a"), record(message_id="b", body='{"a":2,"b":4,"c":1}'))

    assert len(resultado["batchItemFailures"]) == 2


def test_execucao_ja_existente_nao_entra_em_batch_item_failures(monkeypatch, sfn):
    def ja_existe(**kwargs):
        raise ExecutionAlreadyExists(kwargs["name"])

    monkeypatch.setattr(sfn, "start_execution", ja_existe)

    assert call(record())["batchItemFailures"] == []


def test_lote_vazio_nao_quebra():
    assert lambda_handler({}, Context()) == {"batchItemFailures": []}


def test_log_conta_iniciadas_e_duplicadas(capsys):
    call(record(message_id="a"), record(message_id="b"))

    linhas = [json.loads(linha) for linha in capsys.readouterr().out.strip().splitlines()]
    resumo = [linha for linha in linhas if linha["event"] == "batch_dispatched"][0]

    assert resumo["started"] == 1
    assert resumo["duplicates"] == 1
