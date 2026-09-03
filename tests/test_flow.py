"""O fluxo de verdade, ponta a ponta, contra a definicao versionada.

Os outros testes cobrem estados isolados. Estes rodam
`workflow/bhaskara.asl.yaml` inteiro pelo interpretador: e o unico lugar onde
se verifica que os `ResultPath` compoem, que o `Choice` roteia pelo campo certo
e que os tres caminhos entregam a **mesma forma** de resultado ao estado
seguinte.
"""

import json

import pytest

from local import runtime


@pytest.fixture
def engine(aws):
    """O fluxo real, com os dubles ja ligados pela fixture autouse."""
    return aws["engine"]


def run(engine, a, b, c, meta=None):
    return engine.start(
        {"equation": {"a": a, "b": b, "c": c}, "meta": meta or {"idempotency_key": "k1"}}
    )


def test_delta_positivo_percorre_o_parallel(engine):
    execution = run(engine, 1, -5, 6)

    assert execution.status == "SUCCEEDED"
    assert "RootsInParallel" in execution.states_entered()
    assert execution.output["result"]["roots"] == [
        {"label": "x1", "value": 3.0},
        {"label": "x2", "value": 2.0},
    ]


def test_delta_zero_calcula_a_raiz_uma_vez_so(engine):
    execution = run(engine, 1, -4, 4)

    entrados = execution.states_entered()

    assert "RootDouble" in entrados
    assert "RootsInParallel" not in entrados
    assert execution.output["result"]["roots"] == [{"label": "double", "value": 2.0}]


def test_delta_negativo_nao_invoca_nenhuma_lambda_de_raiz(engine):
    execution = run(engine, 1, 0, 5)

    assert "NoRealRoots" in execution.states_entered()
    assert execution.output["result"] == {"roots": []}


def test_os_tres_caminhos_entregam_a_mesma_forma(engine):
    for a, b, c in ((1, -5, 6), (1, -4, 4), (1, 0, 5)):
        resultado = run(engine, a, b, c).output["result"]

        assert set(resultado) == {"roots"}
        assert isinstance(resultado["roots"], list)


def test_o_contexto_da_entrada_sobrevive_ate_o_fim(engine):
    """ResultPath enxerta; nao substitui. O meta original precisa chegar ao fim."""
    execution = run(engine, 1, -5, 6, meta={"idempotency_key": "abc", "batch_id": "b1"})

    assert execution.output["meta"] == {"idempotency_key": "abc", "batch_id": "b1"}
    assert execution.output["equation"] == {"a": 1, "b": -5, "c": 6}
    assert execution.output["validated"] == {"a": 1, "b": -5, "c": 6}
    assert execution.output["delta"] == {"value": 1, "sign": "positive"}


def test_equacao_invalida_vai_para_a_dead_letter_sem_retry(engine, aws):
    execution = run(engine, 0, 1, 1)

    tentativas = [e for e in execution.events if e["type"] == "TaskFailed"]
    recusadas = aws["sqs"].queue(runtime.LOCAL_DEAD_LETTER_URL).messages

    assert execution.status == "FAILED"
    assert execution.error == "EquationNotProcessed"
    assert len(tentativas) == 1, "erro permanente nao e reentregue"
    assert len(recusadas) == 1

    corpo = json.loads(recusadas[0]["body"])

    assert corpo["source"] == "workflow"
    assert corpo["error"]["Error"] == "InvalidEquation"
    assert "nao pode ser zero" in corpo["error"]["Cause"]
    assert corpo["equation"] == {"a": 0, "b": 1, "c": 1}


def test_caos_em_um_ramo_do_parallel_e_recuperado_pelo_retry(engine):
    meta = {"idempotency_key": "k1", "chaos": {"state": "RootX2", "fails": 2}}

    execution = run(engine, 1, -5, 6, meta=meta)

    falhas = [e for e in execution.events if e["type"] == "TaskFailed"]

    assert execution.status == "SUCCEEDED"
    assert [e["details"]["name"] for e in falhas] == ["RootX2", "RootX2"]
    assert execution.output["result"]["roots"][1]["value"] == 2.0


def test_caos_que_esgota_o_retry_vai_para_a_dead_letter(engine, aws):
    meta = {"idempotency_key": "k1", "chaos": {"state": "Delta", "fails": 9}}

    execution = run(engine, 1, -5, 6, meta=meta)

    falhas = [e for e in execution.events if e["type"] == "TaskFailed"]
    recusadas = aws["sqs"].queue(runtime.LOCAL_DEAD_LETTER_URL).messages

    assert execution.status == "FAILED"
    assert len(falhas) == 4, "a tentativa original mais os 3 retries"
    assert json.loads(recusadas[0]["body"])["error"]["Error"] == "TransientFailure"


def test_falha_dentro_de_um_ramo_do_parallel_e_capturada_pelo_parallel(engine, aws):
    """Em ASL, o Next de um estado so aponta para o mesmo nivel.

    Por isso o Catch fica no Parallel: a falha do ramo falha o Parallel, e e
    dali que o fluxo alcanca a DeadLetter.
    """
    meta = {"idempotency_key": "k1", "chaos": {"state": "RootX2", "fails": 9}}

    execution = run(engine, 1, -5, 6, meta=meta)

    assert execution.status == "FAILED"
    assert execution.error == "EquationNotProcessed"
    assert len(aws["sqs"].queue(runtime.LOCAL_DEAD_LETTER_URL).messages) == 1


def test_recusa_nao_grava_resultado(engine, aws):
    run(engine, 0, 1, 1)

    assert aws["dynamodb"].items == {}


def test_motivo_vai_tambem_como_atributo_da_mensagem(engine, aws):
    """A DLQ nativa da SQS move o payload sem dizer por que.

    Um operador olhando a fila precisa ver o motivo sem abrir cada corpo.
    """
    run(engine, 0, 1, 1)

    mensagem = aws["sqs"].queue(runtime.LOCAL_DEAD_LETTER_URL).messages[0]

    assert mensagem["messageAttributes"]["RejectionReason"] == {
        "DataType": "String",
        "StringValue": "InvalidEquation",
    }


def test_execucao_bem_sucedida_nao_publica_na_dead_letter(engine, aws):
    run(engine, 1, -5, 6)

    assert aws["sqs"].queue(runtime.LOCAL_DEAD_LETTER_URL).messages == []


def test_o_resultado_e_gravado_nos_tres_caminhos(engine, aws):
    for indice, (a, b, c) in enumerate(((1, -5, 6), (1, -4, 4), (1, 0, 5))):
        run(engine, a, b, c, meta={"idempotency_key": "k%d" % indice, "batch_id": "b1"})

    assert sorted(aws["dynamodb"].items) == ["k0", "k1", "k2"]


def test_a_mesma_execucao_repetida_nao_grava_duas_vezes(engine, aws):
    primeira = run(engine, 1, -5, 6)
    segunda = run(engine, 1, -5, 6)

    assert primeira.output["persisted"]["duplicate"] is False
    assert segunda.output["persisted"]["duplicate"] is True
    assert len(aws["dynamodb"].items) == 1


def test_duplicata_nao_derruba_a_execucao(engine):
    run(engine, 1, -5, 6)

    assert run(engine, 1, -5, 6).status == "SUCCEEDED"
