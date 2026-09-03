"""Testes do submit — a borda publica do sistema.

Duas coisas sao testadas com mais rigor que o resto: a chave de API e o teto de
quantidade. Este endpoint transforma uma requisicao em ate 2.000 execucoes de
state machine; aberto, ele seria um gerador de custo para quem o encontrasse.
"""

import json

import pytest

from local import runtime
from src.handlers.submit import handler as submit
from src.handlers.submit.handler import lambda_handler

CHAVE = runtime.LOCAL_API_KEY


class Context:
    aws_request_id = "req-1"

    def __init__(self, remaining=30000):
        self._remaining = remaining

    def get_remaining_time_in_millis(self):
        return self._remaining


@pytest.fixture
def queue(aws):
    return aws["sqs"].queue(runtime.LOCAL_ORDERS_URL)


def event(body=None, key=CHAVE, **campos):
    if body is None:
        body = json.dumps(dict({"quantity": 5}, **campos))

    headers = {"x-api-key": key} if key is not None else {}

    return {"headers": headers, "body": body}


def call(**kwargs):
    resposta = lambda_handler(event(**kwargs), Context())

    return resposta["statusCode"], json.loads(resposta["body"])


# ------------------------------------------------------------------ chave


def test_sem_chave_responde_403_e_nao_publica_nada(queue):
    status, corpo = call(key=None)

    assert status == 403
    assert queue.messages == []
    assert "Chave" in corpo["error"]


def test_chave_errada_responde_403(queue):
    status, _ = call(key="chave-errada")

    assert status == 403
    assert queue.messages == []


def test_chave_e_verificada_antes_do_corpo(queue):
    """Responder 400 a um corpo invalido diria ao anonimo que a chave acertou."""
    status, _ = call(key="chave-errada", body="{invalido")

    assert status == 403


def test_sem_chave_configurada_nada_passa(monkeypatch, queue):
    """Falha fechada: um erro de deploy nao pode virar endpoint aberto."""
    monkeypatch.setattr(submit, "API_KEY", "")

    status, _ = call()

    assert status == 403


# -------------------------------------------------------------- requisicao


def test_publica_a_quantidade_pedida(queue):
    status, corpo = call(quantity=25)

    assert status == 202
    assert corpo["published"] == 25
    assert len(queue.messages) == 25


def test_responde_202_e_nao_200():
    """As mensagens foram aceitas; o processamento acontece em outro lugar."""
    status, corpo = call()

    assert status == 202
    assert "result" not in corpo


def test_publica_em_lotes_de_dez(queue):
    _, corpo = call(quantity=25)

    assert corpo["batches"] == 3


def test_corpo_ausente_e_400():
    status, corpo = call(body="")

    assert status == 400
    assert "quantity" in corpo["error"]


def test_corpo_invalido_e_400():
    status, _ = call(body="{isto nao e json")

    assert status == 400


def test_corpo_que_nao_e_objeto_e_400():
    status, _ = call(body="[1, 2, 3]")

    assert status == 400


def test_quantidade_ausente_e_400():
    status, _ = call(body=json.dumps({}))

    assert status == 400


def test_quantidade_zero_e_400():
    status, _ = call(quantity=0)

    assert status == 400


def test_quantidade_acima_do_teto_e_400():
    status, corpo = call(quantity=submit.MAX_QUANTITY + 1)

    assert status == 400
    assert str(submit.MAX_QUANTITY) in corpo["error"]


def test_quantidade_booleana_e_400():
    """isinstance(True, int) e True: sem excluir bool, true publicaria 1."""
    status, _ = call(body=json.dumps({"quantity": True}))

    assert status == 400


def test_quantidade_fracionaria_e_400():
    status, _ = call(body=json.dumps({"quantity": 2.5}))

    assert status == 400


@pytest.mark.parametrize("campo", ["invalid_ratio", "duplicate_ratio", "chaos_ratio"])
def test_proporcao_fora_do_intervalo_e_400(campo):
    status, _ = call(body=json.dumps({"quantity": 5, campo: 1.5}))

    assert status == 400


@pytest.mark.parametrize("campo", ["invalid_ratio", "duplicate_ratio", "chaos_ratio"])
def test_proporcao_que_nao_e_numero_e_400(campo):
    status, _ = call(body=json.dumps({"quantity": 5, campo: "muito"}))

    assert status == 400


@pytest.mark.parametrize("campo", ["invalid_ratio", "duplicate_ratio", "chaos_ratio"])
def test_proporcao_e_opcional_e_zero_por_padrao(campo, queue):
    """Gerar lixo sem que ninguem tenha pedido seria surpreendente."""
    call(quantity=30, seed=1)

    assert all(json.loads(m["body"]) for m in queue.messages)
    assert all("Chaos" not in (m["messageAttributes"] or {}) for m in queue.messages)


# ------------------------------------------------------------------ carga


def test_cada_mensagem_leva_o_batch_id(queue):
    _, corpo = call(quantity=5)

    identificadores = {m["messageAttributes"]["BatchId"]["StringValue"] for m in queue.messages}

    assert identificadores == {corpo["batch_id"]}


def test_batch_id_muda_a_cada_requisicao():
    _, primeira = call()
    _, segunda = call()

    assert primeira["batch_id"] != segunda["batch_id"]


def test_batch_id_pode_ser_informado():
    """O painel precisa correlacionar a carga que ele mesmo disparou."""
    _, corpo = call(body=json.dumps({"quantity": 2, "batch_id": "b-do-painel"}))

    assert corpo["batch_id"] == "b-do-painel"


def test_caos_viaja_como_atributo_e_nao_no_corpo(queue):
    call(quantity=40, chaos_ratio=1.0, seed=5)

    for message in queue.messages:
        assert "Chaos" in message["messageAttributes"]
        assert "chaos" not in message["body"]

        chaos = json.loads(message["messageAttributes"]["Chaos"]["StringValue"])

        assert chaos["fails"] >= 1
        assert chaos["state"]


def test_o_corpo_publicado_e_so_a_equacao(queue):
    """Quem publica direto na fila manda so a equacao; o formato e o mesmo."""
    call(quantity=10, seed=2)

    corpo = json.loads(queue.messages[0]["body"])

    assert set(corpo) == {"a", "b", "c"}


def test_seed_torna_a_carga_reproduzivel(aws):
    call(quantity=20, seed=42)
    primeira = [m["body"] for m in aws["sqs"].queue(runtime.LOCAL_ORDERS_URL).messages]

    aws["sqs"].queues.clear()

    call(quantity=20, seed=42)
    segunda = [m["body"] for m in aws["sqs"].queue(runtime.LOCAL_ORDERS_URL).messages]

    assert primeira == segunda


def test_duplicatas_repetem_mensagens_ja_publicadas(queue):
    call(quantity=60, duplicate_ratio=0.5, seed=11)

    corpos = [m["body"] for m in queue.messages]

    assert len(set(corpos)) < len(corpos)


def test_a_resposta_conta_quantas_levam_caos():
    _, corpo = call(quantity=40, chaos_ratio=1.0, seed=5)

    assert corpo["chaos"] == 40


# ------------------------------------------------------------ tempo e falha


def test_para_antes_do_timeout_e_avisa(queue):
    """Uma resposta honesta e melhor que um timeout do API Gateway."""
    resposta = lambda_handler(event(quantity=500), Context(remaining=1000))
    corpo = json.loads(resposta["body"])

    assert corpo["truncated"] is True
    assert corpo["published"] == 0
    assert "Reenvie" in corpo["detail"]


def test_falha_parcial_de_lote_nao_descarta_o_resto(monkeypatch, queue):
    def parcial(QueueUrl, Entries):  # noqa: N803
        return {"Successful": Entries[:8], "Failed": [{"Id": "m9", "Message": "throttled"}] * 2}

    monkeypatch.setattr(submit, "_sqs", type("Fake", (), {"send_message_batch": staticmethod(parcial)})())

    _, corpo = call(quantity=10)

    assert corpo["published"] == 8
    assert corpo["failed"] == 2


def test_log_de_publicacao_sai_como_json(capsys):
    call(quantity=3)

    eventos = [json.loads(linha)["event"] for linha in capsys.readouterr().out.strip().splitlines()]

    assert eventos == ["batch_requested", "batch_published"]
