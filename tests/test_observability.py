"""Testes do envelope canonico de log.

O que esta sob teste nao e "o log sai". E o **contrato do envelope**: quais
campos toda linha carrega, de onde eles vem e o que acontece quando faltam.

Isso importa porque as queries do Logs Insights, os alarmes e o dashboard do
Checkpoint 4 sao escritos contra esses nomes de campo. Renomear `execution`
para `execution_id` nao quebraria nenhum handler — quebraria em silencio toda
consulta que correlaciona uma equacao pelos seus sete estados.
"""

import json

import observability
import pytest
from observability import ERROR, INFO, WARN, invocation


class Context:
    aws_request_id = "req-1"


def task_event(**extra):
    """O formato que a ASL entrega a toda Task do fluxo."""
    payload = {
        "state": "Validate",
        "retry_count": 0,
        "meta": {"idempotency_key": "k-abc", "batch_id": "b-1"},
    }
    payload.update(extra)
    return payload


def line(capsys):
    saida = capsys.readouterr().out.strip().splitlines()

    assert len(saida) == 1, f"esperava uma linha, veio {len(saida)}"

    return json.loads(saida[0])


@pytest.fixture(autouse=True)
def frio():
    """Cada teste comeca com o processo frio.

    O pytest roda a suite inteira em um processo, entao sem isto so o primeiro
    teste do arquivo veria cold_start verdadeiro — e a asserticao dependeria da
    ordem de execucao.
    """
    observability.reset()


# ------------------------------------------------------------------ envelope


def test_a_linha_carrega_o_envelope_inteiro(capsys):
    invocation("validate", task_event(), Context())("equation_validated", a=1)

    registro = line(capsys)

    assert registro["event"] == "equation_validated"
    assert registro["level"] == INFO
    assert registro["service"] == "validate"
    assert registro["state"] == "Validate"
    assert registro["execution"] == "k-abc"
    assert registro["batch_id"] == "b-1"
    assert registro["request_id"] == "req-1"
    assert registro["attempt"] == 0
    assert registro["cold_start"] is True
    assert registro["a"] == 1


def test_a_linha_e_json_de_uma_linha_so(capsys):
    invocation("delta", task_event(), Context())("delta_calculated", value=49)

    saida = capsys.readouterr().out

    assert saida.count("\n") == 1
    assert json.loads(saida)["value"] == 49


def test_o_envelope_vem_antes_dos_campos_do_evento(capsys):
    invocation("delta", task_event(), Context())(
        "delta_calculated", value=49, sign="positive"
    )

    chaves = list(json.loads(capsys.readouterr().out).keys())

    # Uma linha de log e lida por humano antes de virar query: o que identifica
    # o evento tem de vir antes do que o descreve.
    assert chaves[:3] == ["event", "level", "service"]
    assert chaves[-2:] == ["value", "sign"]


def test_campo_ausente_nao_vira_null(capsys):
    # O submit e o status recebem evento de API Gateway: nao ha state, nem meta,
    # nem retry_count. Gravar null para cada um poluiria toda linha da borda.
    invocation("submit", {"headers": {}}, Context())("batch_requested", quantity=50)

    registro = line(capsys)

    assert "state" not in registro
    assert "execution" not in registro
    assert "batch_id" not in registro
    assert "attempt" not in registro
    assert registro["service"] == "submit"


def test_evento_que_nao_e_dicionario_nao_quebra(capsys):
    invocation("dispatcher", None, Context())("batch_received", batch_size=0)

    assert line(capsys)["event"] == "batch_received"


def test_sem_context_a_linha_sai_sem_request_id(capsys):
    invocation("validate", task_event())("equation_validated")

    assert "request_id" not in line(capsys)


# --------------------------------------------------------------------- nivel


@pytest.mark.parametrize("nivel", [INFO, WARN, ERROR])
def test_o_nivel_pedido_e_o_nivel_gravado(capsys, nivel):
    invocation("validate", task_event(), Context())("algo", level=nivel)

    assert line(capsys)["level"] == nivel


def test_o_nivel_padrao_e_info(capsys):
    invocation("validate", task_event(), Context())("algo")

    assert line(capsys)["level"] == INFO


# ---------------------------------------------------------------- cold start


def test_a_primeira_invocacao_do_processo_e_fria(capsys):
    invocation("validate", task_event(), Context())("um")

    assert line(capsys)["cold_start"] is True


def test_a_segunda_invocacao_e_quente(capsys):
    invocation("validate", task_event(), Context())("um")
    capsys.readouterr()

    invocation("validate", task_event(), Context())("dois")

    assert line(capsys)["cold_start"] is False


def test_cold_start_falso_continua_no_registro(capsys):
    # False e falsy: um filtro ingenuo de campos vazios o descartaria, e a
    # ausencia do campo seria lida como "nao se sabe" em vez de "estava quente".
    invocation("validate", task_event(), Context())("um")
    capsys.readouterr()

    invocation("validate", task_event(), Context())("dois")

    assert "cold_start" in line(capsys)


def test_todas_as_linhas_de_uma_invocacao_fria_sao_frias(capsys):
    log = invocation("validate", task_event(), Context())
    log("um")
    log("dois")

    registros = [
        json.loads(linha) for linha in capsys.readouterr().out.strip().splitlines()
    ]

    assert [r["cold_start"] for r in registros] == [True, True]


# ------------------------------------------------------------------ duracao


def test_toda_linha_traz_a_duracao_em_ms(capsys):
    invocation("validate", task_event(), Context())("algo")

    assert isinstance(line(capsys)["duration_ms"], float)


def test_a_duracao_cresce_dentro_da_mesma_invocacao(capsys):
    log = invocation("validate", task_event(), Context())
    log("um")
    log("dois")

    registros = [
        json.loads(linha) for linha in capsys.readouterr().out.strip().splitlines()
    ]

    assert registros[1]["duration_ms"] >= registros[0]["duration_ms"]


# ------------------------------------------------------- sobrescrita e tipos


def test_quem_chama_pode_sobrescrever_o_envelope(capsys):
    # O dispatcher sabe a chave de cada mensagem do lote; o evento da SQS que
    # ele recebe nao carrega nenhuma delas no formato do fluxo.
    invocation("dispatcher", {"Records": []}, Context())(
        "execution_started", execution="k-xyz", batch_id="b-9"
    )

    registro = line(capsys)

    assert registro["execution"] == "k-xyz"
    assert registro["batch_id"] == "b-9"


def test_a_sobrescrita_nao_desloca_o_campo_para_o_fim(capsys):
    invocation("dispatcher", {"Records": []}, Context())(
        "execution_started", execution="k-xyz"
    )

    chaves = list(json.loads(capsys.readouterr().out).keys())

    assert chaves.index("execution") < chaves.index("cold_start")


def test_attempt_zero_continua_no_registro(capsys):
    # Mesma armadilha do cold_start: 0 e falsy, e "primeira tentativa" e
    # justamente o valor mais comum de todos.
    invocation("validate", task_event(retry_count=0), Context())("algo")

    assert line(capsys)["attempt"] == 0


def test_valor_nao_serializavel_nao_derruba_o_handler(capsys):
    # Telemetria nao pode quebrar regra de negocio: o persist le Decimal do
    # DynamoDB, e um TypeError aqui faria a funcao falhar ao registrar sucesso.
    from decimal import Decimal

    invocation("persist", task_event(), Context())(
        "result_stored", value=Decimal("3.5")
    )

    assert line(capsys)["value"] == "3.5"


def test_nan_nao_vira_json_invalido():
    # NaN e Infinity nao sao JSON pela RFC 8259, e o Logs Insights nao os
    # parseia: a linha inteira viraria texto opaco.
    with pytest.raises(ValueError):
        invocation("delta", task_event(), Context())(
            "delta_calculated", value=float("nan")
        )
