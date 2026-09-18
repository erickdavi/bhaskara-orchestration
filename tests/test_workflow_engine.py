"""Testes do interpretador da ASL.

O interpretador e a peca que permite rodar o fluxo sem AWS, entao um erro aqui
nao aparece como falha: aparece como uma demonstracao local que passa e uma
execucao na nuvem que se comporta de outro jeito. Por isso os testes cobrem as
regras do proprio Step Functions — ordem dos retriers, o que States.TaskFailed
captura, o que ResultPath faz com a entrada — e nao apenas "o fluxo rodou".
"""

import pytest

from local.engine import (
    ABSENT,
    Engine,
    TaskFailure,
    Unsupported,
    apply_payload,
    error_matches,
    place,
    resolve,
)


def engine(states, start_at="A", resources=None, **kwargs):
    definition = {"StartAt": start_at, "States": states}

    return Engine(definition, resources or {}, **kwargs)


# ------------------------------------------------------------------ caminhos


def test_resolve_le_caminho_simples():
    assert resolve("$.a.b", {"a": {"b": 7}}, {}) == 7


def test_resolve_devolve_a_entrada_inteira_para_cifrao():
    data = {"a": 1}

    assert resolve("$", data, {}) is data


def test_resolve_le_o_objeto_de_contexto():
    context = {"State": {"RetryCount": 2}}

    assert resolve("$$.State.RetryCount", {}, context) == 2


def test_resolve_falha_em_caminho_inexistente():
    with pytest.raises(TaskFailure):
        resolve("$.nao.existe", {}, {})


def test_resolve_recusa_caminho_que_nao_e_jsonpath():
    with pytest.raises(Unsupported):
        resolve("a.b", {}, {})


# ------------------------------------------------------------------- payload


def test_parameters_resolvem_chaves_com_sufixo():
    template = {"x.$": "$.a", "fixo": 1, "aninhado": {"y.$": "$.b"}}

    assert apply_payload(template, {"a": 10, "b": 20}, {}) == {
        "x": 10,
        "fixo": 1,
        "aninhado": {"y": 20},
    }


def test_intrinseca_states_array_empacota_o_resultado():
    assert apply_payload({"roots.$": "States.Array($)"}, {"label": "x1"}, {}) == {
        "roots": [{"label": "x1"}]
    }


def test_intrinseca_desconhecida_e_recusada():
    with pytest.raises(Unsupported):
        apply_payload({"x.$": "States.Format('{}', $.a)"}, {"a": 1}, {})


# ---------------------------------------------------------------- ResultPath


def test_result_path_ausente_substitui_a_entrada():
    assert place({"a": 1}, ABSENT, {"b": 2}) == {"b": 2}


def test_result_path_null_descarta_o_resultado():
    assert place({"a": 1}, None, {"b": 2}) == {"a": 1}


def test_result_path_enxerta_sem_perder_a_entrada():
    assert place({"a": 1}, "$.saida", {"b": 2}) == {"a": 1, "saida": {"b": 2}}


def test_result_path_nao_muta_a_entrada_original():
    entrada = {"a": {"interno": 1}}

    place(entrada, "$.saida", {"b": 2})

    assert entrada == {"a": {"interno": 1}}


# ------------------------------------------------------------------- estados


def test_task_chama_o_recurso_e_grava_no_result_path():
    states = {
        "A": {
            "Type": "Task",
            "Resource": "${f}",
            "Parameters": {"valor.$": "$.entrada"},
            "ResultPath": "$.saida",
            "End": True,
        }
    }

    resources = {"${f}": lambda payload: {"dobro": payload["valor"] * 2}}

    execution = engine(states, resources=resources).start({"entrada": 21})

    assert execution.status == "SUCCEEDED"
    assert execution.output == {"entrada": 21, "saida": {"dobro": 42}}


def test_pass_com_result_estatico():
    states = {
        "A": {
            "Type": "Pass",
            "Result": {"roots": []},
            "ResultPath": "$.result",
            "End": True,
        }
    }

    assert engine(states).start({"x": 1}).output == {"x": 1, "result": {"roots": []}}


def test_choice_roteia_pelo_sinal():
    states = {
        "A": {
            "Type": "Choice",
            "Choices": [
                {
                    "Variable": "$.delta.sign",
                    "StringEquals": "positive",
                    "Next": "Duas",
                },
                {"Variable": "$.delta.sign", "StringEquals": "zero", "Next": "Dupla"},
            ],
            "Default": "Nenhuma",
        },
        "Duas": {
            "Type": "Pass",
            "Result": "duas",
            "ResultPath": "$.saida",
            "End": True,
        },
        "Dupla": {
            "Type": "Pass",
            "Result": "dupla",
            "ResultPath": "$.saida",
            "End": True,
        },
        "Nenhuma": {
            "Type": "Pass",
            "Result": "nenhuma",
            "ResultPath": "$.saida",
            "End": True,
        },
    }

    for sign, esperado in (
        ("positive", "duas"),
        ("zero", "dupla"),
        ("negative", "nenhuma"),
    ):
        execution = engine(states).start({"delta": {"sign": sign}})

        assert execution.output["saida"] == esperado


def test_parallel_roda_os_dois_ramos_e_junta_os_resultados():
    states = {
        "A": {
            "Type": "Parallel",
            "Branches": [
                {
                    "StartAt": "X1",
                    "States": {
                        "X1": {
                            "Type": "Task",
                            "Resource": "${f}",
                            "Parameters": {"label": "x1"},
                            "End": True,
                        }
                    },
                },
                {
                    "StartAt": "X2",
                    "States": {
                        "X2": {
                            "Type": "Task",
                            "Resource": "${f}",
                            "Parameters": {"label": "x2"},
                            "End": True,
                        }
                    },
                },
            ],
            "ResultSelector": {"roots.$": "$"},
            "ResultPath": "$.result",
            "End": True,
        }
    }

    resources = {"${f}": lambda payload: {"label": payload["label"], "value": 1}}

    saida = engine(states, resources=resources).start({"a": 1}).output

    assert [root["label"] for root in saida["result"]["roots"]] == ["x1", "x2"]


def test_succeed_encerra_a_execucao():
    states = {"A": {"Type": "Pass", "Next": "Fim"}, "Fim": {"Type": "Succeed"}}

    assert engine(states).start({"a": 1}).status == "SUCCEEDED"


def test_fail_encerra_com_erro_nomeado():
    states = {"A": {"Type": "Fail", "Error": "Recusada", "Cause": "porque sim"}}

    execution = engine(states).start({})

    assert (execution.status, execution.error, execution.cause) == (
        "FAILED",
        "Recusada",
        "porque sim",
    )


# --------------------------------------------------------------------- retry


class Instavel:
    """Falha nas primeiras N chamadas, como o modo caos faz na demonstracao."""

    def __init__(self, falhas, erro=RuntimeError):
        self.falhas = falhas
        self.erro = erro
        self.chamadas = 0

    def __call__(self, payload):
        self.chamadas += 1

        if self.chamadas <= self.falhas:
            raise self.erro(f"falha {self.chamadas}")

        return {"ok": True, "chamadas": self.chamadas}


RETRY = [
    {"ErrorEquals": ["InvalidEquation"], "MaxAttempts": 0},
    {
        "ErrorEquals": ["States.TaskFailed"],
        "IntervalSeconds": 1,
        "MaxAttempts": 3,
        "BackoffRate": 2,
    },
]


def task(**extra):
    state = {"Type": "Task", "Resource": "${f}", "End": True, "Retry": RETRY}
    state.update(extra)
    return {"A": state}


def test_retry_recupera_dentro_do_maximo():
    recurso = Instavel(falhas=2)

    execution = engine(task(), resources={"${f}": recurso}).start({})

    assert execution.status == "SUCCEEDED"
    assert recurso.chamadas == 3


def test_retry_desiste_apos_o_maximo():
    recurso = Instavel(falhas=9)

    execution = engine(task(), resources={"${f}": recurso}).start({})

    assert execution.status == "FAILED"
    assert recurso.chamadas == 4, "1 tentativa original + 3 retries"


def test_backoff_dobra_a_cada_tentativa():
    dormidas = []
    recurso = Instavel(falhas=3)

    engine(task(), resources={"${f}": recurso}, sleeper=dormidas.append).start({})

    assert dormidas == [1, 2, 4]


def test_erro_permanente_nao_e_reentregue():
    class InvalidEquation(Exception):
        pass

    recurso = Instavel(falhas=9, erro=InvalidEquation)

    execution = engine(task(), resources={"${f}": recurso}).start({})

    assert execution.status == "FAILED"
    assert recurso.chamadas == 1, "MaxAttempts 0 no primeiro retrier que casa"


def test_ordem_dos_retriers_e_respeitada():
    """States.TaskFailed antes do especifico reentregaria o erro permanente."""
    invertido = list(reversed(RETRY))

    class InvalidEquation(Exception):
        pass

    recurso = Instavel(falhas=9, erro=InvalidEquation)

    engine(task(Retry=invertido), resources={"${f}": recurso}).start({})

    assert recurso.chamadas == 4


def test_states_all_captura_erro_reservado():
    assert error_matches(["States.ALL"], "States.Timeout")


def test_task_failed_nao_captura_erro_reservado():
    assert not error_matches(["States.TaskFailed"], "States.Timeout")
    assert error_matches(["States.TaskFailed"], "InvalidEquation")


# --------------------------------------------------------------------- catch


def test_catch_desvia_o_fluxo_e_anexa_o_erro():
    states = {
        "A": {
            "Type": "Task",
            "Resource": "${f}",
            "Catch": [
                {
                    "ErrorEquals": ["States.ALL"],
                    "ResultPath": "$.error",
                    "Next": "Recusa",
                }
            ],
            "End": True,
        },
        "Recusa": {
            "Type": "Pass",
            "ResultPath": "$.destino",
            "Result": "dlq",
            "End": True,
        },
    }

    def falha(payload):
        raise ValueError("coeficiente ausente")

    execution = engine(states, resources={"${f}": falha}).start({"id": 1})

    assert execution.status == "SUCCEEDED"
    assert execution.output["destino"] == "dlq"
    assert execution.output["error"]["Error"] == "ValueError"
    assert "coeficiente ausente" in execution.output["error"]["Cause"]


def test_catch_so_age_depois_de_esgotar_o_retry():
    recurso = Instavel(falhas=9)

    states = {
        "A": {
            "Type": "Task",
            "Resource": "${f}",
            "Retry": RETRY,
            "Catch": [
                {
                    "ErrorEquals": ["States.ALL"],
                    "ResultPath": "$.error",
                    "Next": "Recusa",
                }
            ],
            "End": True,
        },
        "Recusa": {"Type": "Succeed"},
    }

    engine(states, resources={"${f}": recurso}).start({})

    assert recurso.chamadas == 4


def test_erro_sem_catch_derruba_a_execucao():
    states = {"A": {"Type": "Task", "Resource": "${f}", "End": True}}

    def falha(payload):
        raise KeyError("a")

    execution = engine(states, resources={"${f}": falha}).start({})

    assert execution.status == "FAILED"
    assert execution.error == "KeyError"


# -------------------------------------------------------------------- eventos


def test_eventos_registram_a_sequencia_de_estados():
    states = {
        "A": {"Type": "Pass", "Next": "B"},
        "B": {"Type": "Succeed"},
    }

    execution = engine(states).start({})

    tipos = [evento["type"] for evento in execution.events]

    assert tipos[0] == "ExecutionStarted"
    assert tipos[-1] == "ExecutionSucceeded"
    assert execution.states_entered() == ["A", "B"]


def test_evento_de_falha_carrega_o_nome_do_erro():
    recurso = Instavel(falhas=1)

    execution = engine(task(), resources={"${f}": recurso}).start({})

    falhas = [e for e in execution.events if e["type"] == "TaskFailed"]

    assert falhas[0]["details"]["error"] == "RuntimeError"
    assert [e["details"]["attempt"] for e in falhas] == [0]


def test_recurso_sem_binding_e_recusado():
    states = {"A": {"Type": "Task", "Resource": "${inexistente}", "End": True}}

    with pytest.raises(Unsupported):
        engine(states).start({})


def test_falha_de_ramo_e_capturada_pelo_catch_do_parallel():
    """Semantica do Step Functions: o erro do ramo vira erro do Parallel."""
    states = {
        "A": {
            "Type": "Parallel",
            "Branches": [
                {
                    "StartAt": "X",
                    "States": {"X": {"Type": "Task", "Resource": "${f}", "End": True}},
                },
            ],
            "Catch": [
                {
                    "ErrorEquals": ["States.ALL"],
                    "ResultPath": "$.error",
                    "Next": "Recusa",
                }
            ],
            "End": True,
        },
        "Recusa": {
            "Type": "Pass",
            "Result": "dlq",
            "ResultPath": "$.destino",
            "End": True,
        },
    }

    def falha(payload):
        raise RuntimeError("ramo quebrou")

    execution = engine(states, resources={"${f}": falha}).start({})

    assert execution.status == "SUCCEEDED"
    assert execution.output["destino"] == "dlq"
    assert execution.output["error"]["Error"] == "RuntimeError"
