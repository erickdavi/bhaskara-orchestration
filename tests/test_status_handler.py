"""Testes do status — a fonte unica do painel.

O nucleo aqui e `aggregate()`, que e uma funcao pura sobre os eventos que o
Step Functions grava no CloudWatch. Ela e testada com eventos montados a mao,
no formato exato do servico, porque e assim que a nuvem vai entregar — e e a
mesma funcao que o servidor local usa para alimentar o painel offline.
"""

import json

import pytest

from local import runtime
from src.handlers.status import handler as status
from src.handlers.status.handler import aggregate, lambda_handler, summarize

ARN = "arn:aws:states:us-east-1:000000000000:execution:flow:eq-abc"
OUTRA = "arn:aws:states:us-east-1:000000000000:execution:flow:eq-def"


class Context:
    aws_request_id = "req-1"


def event(**params):
    return {
        "headers": {"x-api-key": runtime.LOCAL_API_KEY},
        "queryStringParameters": params or None,
    }


def call(**params):
    resposta = lambda_handler(event(**params), Context())

    return resposta["statusCode"], json.loads(resposta["body"])


def log_event(kind, arn=ARN, name=None, timestamp=1000, identifier=1, **details):
    if name:
        details["name"] = name

    return {"id": identifier, "type": kind, "execution_arn": arn, "timestamp": timestamp, "details": details}


def execucao_completa(arn=ARN, base=1000):
    """A sequencia de eventos de uma execucao que deu certo."""
    return [
        log_event("ExecutionStarted", arn, timestamp=base, identifier=1),
        log_event("TaskStateEntered", arn, "Validate", base + 10, 2),
        log_event("TaskStateExited", arn, "Validate", base + 40, 3, output=json.dumps({"validated": {"a": 1, "b": -5, "c": 6}})),
        log_event("TaskStateEntered", arn, "Delta", base + 41, 4),
        log_event("TaskStateExited", arn, "Delta", base + 70, 5, output=json.dumps({"delta": {"value": 1, "sign": "positive"}})),
        log_event("ExecutionSucceeded", arn, timestamp=base + 100, identifier=6),
    ]


# ------------------------------------------------------------------ chave


def test_sem_chave_responde_403():
    resposta = lambda_handler({"headers": {}}, Context())

    assert resposta["statusCode"] == 403


def test_chave_errada_responde_403():
    resposta = lambda_handler({"headers": {"x-api-key": "errada"}}, Context())

    assert resposta["statusCode"] == 403


def test_sem_chave_configurada_nada_passa(monkeypatch):
    monkeypatch.setattr(status, "API_KEY", "")

    resposta = lambda_handler(event(), Context())

    assert resposta["statusCode"] == 403


# --------------------------------------------------------------- agregador


def test_conta_entradas_por_estado():
    agregado = aggregate(execucao_completa() + execucao_completa(OUTRA, base=2000))

    assert agregado["states"]["Validate"]["entered"] == 2
    assert agregado["states"]["Delta"]["entered"] == 2


def test_conta_desfechos_das_execucoes():
    eventos = execucao_completa() + [
        log_event("ExecutionStarted", OUTRA, timestamp=2000, identifier=1),
        log_event("ExecutionFailed", OUTRA, timestamp=2100, identifier=2, error="EquationNotProcessed"),
    ]

    assert aggregate(eventos)["counts"] == {"RUNNING": 0, "SUCCEEDED": 1, "FAILED": 1}


def test_execucao_sem_desfecho_conta_como_em_andamento():
    eventos = [
        log_event("ExecutionStarted", timestamp=1000, identifier=1),
        log_event("TaskStateEntered", ARN, "Validate", 1010, 2),
    ]

    assert aggregate(eventos)["counts"]["RUNNING"] == 1


def test_a_linha_do_tempo_segue_a_ordem_dos_estados():
    execucao = aggregate(execucao_completa())["executions"][0]

    assert [passo["state"] for passo in execucao["steps"]] == ["Validate", "Delta"]
    assert all(passo["outcome"] == "ok" for passo in execucao["steps"])


def test_a_linha_do_tempo_traz_a_duracao_de_cada_estado():
    execucao = aggregate(execucao_completa())["executions"][0]

    assert execucao["steps"][0]["ms"] == 30
    assert execucao["steps"][1]["ms"] == 29


def test_estado_que_falhou_e_marcado_com_o_erro():
    eventos = [
        log_event("ExecutionStarted", timestamp=1000, identifier=1),
        log_event("TaskStateEntered", ARN, "Delta", 1010, 2),
        log_event("TaskFailed", ARN, None, 1020, 3, error="TransientFailure", cause="falha simulada"),
    ]

    passo = aggregate(eventos)["executions"][0]["steps"][0]

    assert passo["outcome"] == "failed"
    assert passo["error"] == "TransientFailure"
    assert "falha simulada" in passo["detail"]


def test_o_estado_que_falhou_e_o_ultimo_em_que_a_execucao_entrou():
    """TaskFailed nao carrega o nome do estado — ele e inferido."""
    eventos = [
        log_event("ExecutionStarted", timestamp=1000, identifier=1),
        log_event("TaskStateEntered", ARN, "Validate", 1010, 2),
        log_event("TaskStateExited", ARN, "Validate", 1020, 3, output="{}"),
        log_event("TaskStateEntered", ARN, "Delta", 1030, 4),
        log_event("TaskFailed", ARN, None, 1040, 5, error="TransientFailure"),
    ]

    agregado = aggregate(eventos)

    assert agregado["states"]["Delta"]["failed"] == 1
    assert "failed" not in str(agregado["states"]["Validate"]["failed"] or "")


def test_retry_aparece_como_duas_entradas_no_mesmo_estado():
    """E assim que o painel mostra o retry acontecendo."""
    eventos = [
        log_event("ExecutionStarted", timestamp=1000, identifier=1),
        log_event("TaskStateEntered", ARN, "Delta", 1010, 2),
        log_event("TaskFailed", ARN, None, 1020, 3, error="TransientFailure"),
        log_event("TaskStateEntered", ARN, "Delta", 2020, 4),
        log_event("TaskStateExited", ARN, "Delta", 2030, 5, output="{}"),
    ]

    passos = aggregate(eventos)["executions"][0]["steps"]

    assert [passo["outcome"] for passo in passos] == ["failed", "ok"]
    assert aggregate(eventos)["states"]["Delta"]["entered"] == 2


def test_eventos_fora_de_ordem_sao_ordenados():
    eventos = list(reversed(execucao_completa()))

    execucao = aggregate(eventos)["executions"][0]

    assert [passo["state"] for passo in execucao["steps"]] == ["Validate", "Delta"]


def test_execucoes_mais_recentes_primeiro():
    agregado = aggregate(execucao_completa(base=1000) + execucao_completa(OUTRA, base=5000))

    assert agregado["executions"][0]["arn"] == OUTRA


def test_sem_eventos_a_resposta_e_vazia_e_nao_quebra():
    assert aggregate([]) == {"states": {}, "executions": [], "counts": {"RUNNING": 0, "SUCCEEDED": 0, "FAILED": 0}}


# ----------------------------------------------------------------- resumo


def test_o_resumo_mostra_o_que_o_estado_acrescentou():
    """Com ResultPath, a saida tardia contem tudo o que veio antes."""
    completo = {
        "validated": {"a": 1, "b": -5, "c": 6},
        "delta": {"value": 1, "sign": "positive"},
        "result": {"roots": [{"label": "x1", "value": 3.0}]},
        "persisted": {"duplicate": False},
    }

    assert summarize(json.dumps(completo)) == "gravada"
    assert summarize(json.dumps({k: completo[k] for k in ("validated", "delta", "result")})) == "x1=3"
    assert summarize(json.dumps({k: completo[k] for k in ("validated", "delta")})) == "delta = 1"
    assert summarize(json.dumps({"validated": completo["validated"]})) == "a=1 b=-5 c=6"


def test_o_resumo_distingue_duplicata():
    assert summarize(json.dumps({"persisted": {"duplicate": True}})) == "ja estava gravada"


def test_o_resumo_nomeia_o_caminho_sem_raizes():
    assert summarize(json.dumps({"result": {"roots": []}})) == "sem raizes reais"


def test_o_resumo_aguenta_payload_invalido():
    assert summarize("{quebrado") is None
    assert summarize(None) is None
    assert summarize("[1,2,3]") is None


# ------------------------------------------------------------------ fontes


def test_a_resposta_traz_as_duas_filas(aws):
    aws["sqs"].send_message(QueueUrl=runtime.LOCAL_ORDERS_URL, MessageBody="{}")

    status_code, corpo = call()

    assert status_code == 200
    assert corpo["queues"]["orders"]["visible"] == 1
    assert corpo["queues"]["dead_letter"]["visible"] == 0


def test_os_resultados_vem_do_indice_da_carga(aws):
    gravar(aws, "k1", "b1")
    gravar(aws, "k2", "b2")

    _, corpo = call(batch_id="b1")

    assert len(corpo["results"]) == 1
    assert corpo["results"][0]["a"] == 1


def test_sem_batch_id_nao_lista_resultados(aws):
    """Sem carga escolhida nao ha o que listar — e um Scan sairia caro."""
    gravar(aws, "k1", "b1")

    _, corpo = call()

    assert corpo["results"] == []


def test_a_dead_letter_e_espiada_sem_consumir(aws):
    aws["sqs"].send_message(
        QueueUrl=runtime.LOCAL_DEAD_LETTER_URL,
        MessageBody=json.dumps({"source": "workflow", "equation": {"a": 0}, "error": {"Error": "InvalidEquation", "Cause": "a nao pode ser zero"}}),
    )

    _, corpo = call()

    assert corpo["dead_letter"][0]["error"] == "InvalidEquation"
    assert corpo["dead_letter"][0]["source"] == "workflow"
    assert "zero" in corpo["dead_letter"][0]["cause"]


def test_mensagem_movida_pelo_redrive_aparece_como_redrive(aws):
    """A DLQ nativa move o payload original, sem motivo — e isso precisa aparecer."""
    aws["sqs"].send_message(QueueUrl=runtime.LOCAL_DEAD_LETTER_URL, MessageBody='{"a": 1, "b": 2, "c": 3}')

    _, corpo = call()

    assert corpo["dead_letter"][0]["source"] == "redrive"
    assert corpo["dead_letter"][0]["error"] is None


def test_a_resposta_traz_cursor_para_o_proximo_poll():
    _, corpo = call()

    assert corpo["cursor"] >= corpo["checked_at"] - 1


def test_a_resposta_nao_e_cacheavel():
    resposta = lambda_handler(event(), Context())

    assert resposta["headers"]["Cache-Control"] == "no-store"


def gravar(aws, pk, batch):
    aws["dynamodb"].items[pk] = {
        "pk": {"S": pk},
        "batch_id": {"S": batch},
        "created_at": {"N": "1788000000000"},
        "a": {"S": "1"},
        "b": {"S": "-5"},
        "c": {"S": "6"},
        "delta": {"S": "1"},
        "sign": {"S": "positive"},
        "roots": {"L": [{"S": "3.0"}, {"S": "2.0"}]},
    }


# ----------------------------------------- o historico oficial de uma execucao


class HistoricoFalso:
    """So o verbo que o `history()` usa, com eventos no formato do boto3."""

    def __init__(self, events):
        self.events = events
        self.pedido = None

    def get_execution_history(self, executionArn=None, maxResults=None, includeExecutionData=None):  # noqa: N803
        self.pedido = {
            "arn": executionArn,
            "maxResults": maxResults,
            "includeExecutionData": includeExecutionData,
        }
        return {"events": self.events}


@pytest.fixture
def historico(monkeypatch):
    def montar(events):
        duble = HistoricoFalso(events)
        monkeypatch.setattr(status, "_stepfunctions", duble)
        return duble

    return montar


def saida_de_estado(name, output, timestamp=1789283647):
    return {
        "type": "TaskStateExited",
        "timestamp": timestamp,
        "stateExitedEventDetails": {"name": name, "output": json.dumps(output)},
    }


def test_o_historico_traz_o_detalhe_de_cada_estado(historico):
    """Este caminho e o plano B da otimizacao 2 (docs/observabilidade.md).

    Desligar `include_execution_data` tira o payload do **log**, e com ele o
    detalhe que a linha do tempo agregada mostra. A API `GetExecutionHistory`
    nao depende dessa configuracao: ela entrega entrada e saida de qualquer
    jeito. Por isso o detalhe sob demanda tem de sair daqui — e este teste e o
    que garante que ele sai.
    """
    historico([
        saida_de_estado("Delta", {"delta": {"value": 49, "sign": "positive"}}),
        saida_de_estado("Persist", {"persisted": {"duplicate": False}}),
    ])

    passos = status.history(ARN)["steps"]

    assert passos[0]["state"] == "Delta"
    assert passos[0]["detail"] == "delta = 49"
    assert passos[1]["detail"] == "gravada"


def test_o_historico_pede_os_dados_de_execucao_explicitamente(historico):
    # Sem includeExecutionData a API omite entrada e saida, e o plano B da
    # otimizacao 2 deixaria de existir sem que nada quebrasse visivelmente.
    duble = historico([])

    status.history(ARN)

    assert duble.pedido["includeExecutionData"] is True
    assert duble.pedido["arn"] == ARN


def test_estado_sem_saida_nao_inventa_detalhe(historico):
    historico([{"type": "TaskStateEntered", "timestamp": 1, "stateEnteredEventDetails": {"name": "Validate"}}])

    assert status.history(ARN)["steps"][0]["detail"] is None


def test_o_historico_preserva_o_erro(historico):
    historico([{
        "type": "LambdaFunctionFailed",
        "timestamp": 1,
        "taskFailedEventDetails": {"error": "TransientFailure", "cause": "falha simulada"},
    }])

    assert status.history(ARN)["steps"][0]["error"] == "TransientFailure"
