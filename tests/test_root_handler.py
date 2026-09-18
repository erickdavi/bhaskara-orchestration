"""Testes do estado Root — o mesmo handler atende RootX1, RootX2 e RootDouble.

A parte delicada nao e a formula (o calculator ja tem 18 testes para ela), e sim
a **estabilidade numerica** que o rotulo pede: x1 e x2 nao sao "a de cima e a de
baixo" da formula escolar, e a implementacao existe justamente para nao perder
precisao quando b^2 >> 4ac.
"""

import json

import pytest
from chaos import TransientFailure

from src.handlers.root.handler import lambda_handler


class Context:
    aws_request_id = "req-1"


def event(a, b, c, label, state=None, meta=None, retry_count=0):
    return {
        "validated": {"a": a, "b": b, "c": c},
        "delta": {"value": b * b - 4 * a * c},
        "label": label,
        "state": state,
        "meta": meta if meta is not None else {"idempotency_key": "k1"},
        "retry_count": retry_count,
    }


def test_x1_e_a_raiz_com_mais_delta():
    assert lambda_handler(event(1, -5, 6, "x1"), Context()) == {
        "label": "x1",
        "value": 3.0,
    }


def test_x2_e_a_raiz_com_menos_delta():
    assert lambda_handler(event(1, -5, 6, "x2"), Context()) == {
        "label": "x2",
        "value": 2.0,
    }


def test_raiz_dupla_devolve_o_rotulo_double():
    assert lambda_handler(event(1, -4, 4, "double"), Context()) == {
        "label": "double",
        "value": 2.0,
    }


def test_as_duas_raizes_satisfazem_a_equacao():
    for a, b, c in ((1, -5, 6), (-3, 21, 132), (5, 5, -150), (2, -7, 3)):
        x1 = lambda_handler(event(a, b, c, "x1"), Context())["value"]
        x2 = lambda_handler(event(a, b, c, "x2"), Context())["value"]

        assert a * x1 * x1 + b * x1 + c == pytest.approx(0, abs=1e-9)
        assert a * x2 * x2 + b * x2 + c == pytest.approx(0, abs=1e-9)


def test_precisao_quando_b_domina():
    """A forma escolar erraria 25% aqui; a implementacao estavel, nao.

    Para a=1, b=1e8, c=1 a raiz de menor magnitude e x1 — o contrato diz que x1
    usa +sqrt(delta), e com b positivo e essa a que sofre o cancelamento
    catastrofico na forma direta. A resposta correta e -1e-08.
    """
    x1 = lambda_handler(event(1, 1e8, 1, "x1"), Context())["value"]
    x2 = lambda_handler(event(1, 1e8, 1, "x2"), Context())["value"]

    assert x1 == pytest.approx(-1e-08, rel=1e-9)
    assert x2 == pytest.approx(-1e08, rel=1e-9)


def test_coeficientes_negativos():
    """-3x^2 + 21x + 132 = 0 tem raizes -4 e 11."""
    x1 = lambda_handler(event(-3, 21, 132, "x1"), Context())["value"]
    x2 = lambda_handler(event(-3, 21, 132, "x2"), Context())["value"]

    assert sorted([x1, x2]) == pytest.approx([-4.0, 11.0])


def test_rotulo_desconhecido_e_erro():
    with pytest.raises(ValueError, match="Raiz desconhecida"):
        lambda_handler(event(1, -5, 6, "x3"), Context())


def test_equacao_sem_raizes_reais_e_erro():
    """Defesa em profundidade: o Choice ja impede este estado de ser alcancado."""
    with pytest.raises(ValueError, match="nao tem raizes reais"):
        lambda_handler(event(1, 0, 5, "x1"), Context())


def test_caos_pode_mirar_um_ramo_especifico_do_parallel():
    meta = {"chaos": {"state": "RootX2", "fails": 2}}

    assert (
        lambda_handler(event(1, -5, 6, "x1", state="RootX1", meta=meta), Context())[
            "value"
        ]
        == 3.0
    )

    with pytest.raises(TransientFailure):
        lambda_handler(event(1, -5, 6, "x2", state="RootX2", meta=meta), Context())


def test_caos_respeita_a_tentativa_atual():
    meta = {"chaos": {"state": "RootX1", "fails": 1}}

    with pytest.raises(TransientFailure):
        lambda_handler(
            event(1, -5, 6, "x1", state="RootX1", meta=meta, retry_count=0), Context()
        )

    assert lambda_handler(
        event(1, -5, 6, "x1", state="RootX1", meta=meta, retry_count=1), Context()
    )


def test_log_carrega_o_estado_de_origem(capsys):
    lambda_handler(event(1, -5, 6, "x1", state="RootX1"), Context())

    registro = json.loads(capsys.readouterr().out.strip())

    assert registro["event"] == "root_calculated"
    assert registro["state"] == "RootX1"
