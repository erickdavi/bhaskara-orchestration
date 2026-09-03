"""Testes do estado Delta.

Alem da conta, o que se verifica aqui e a classificacao: `sign` e o campo que o
Choice compara, entao um erro nele nao aparece como resultado errado — aparece
como o fluxo inteiro tomando o ramo errado.
"""

import json

import pytest

from chaos import TransientFailure
from src.handlers.delta.handler import lambda_handler


class Context:
    aws_request_id = "req-1"


def event(a, b, c, meta=None, retry_count=0):
    return {
        "validated": {"a": a, "b": b, "c": c},
        "meta": meta if meta is not None else {"idempotency_key": "k1"},
        "retry_count": retry_count,
    }


def test_delta_positivo():
    assert lambda_handler(event(1, -5, 6), Context()) == {"value": 1, "sign": "positive"}


def test_delta_zero():
    assert lambda_handler(event(1, -4, 4), Context()) == {"value": 0, "sign": "zero"}


def test_delta_negativo():
    resultado = lambda_handler(event(1, 0, 5), Context())

    assert resultado["value"] == -20
    assert resultado["sign"] == "negative"


def test_delta_com_coeficientes_negativos():
    assert lambda_handler(event(-1, -13, -60), Context())["sign"] == "negative"


def test_delta_com_floats():
    resultado = lambda_handler(event(0.5, 2.0, 1.0), Context())

    assert resultado["value"] == pytest.approx(2.0)
    assert resultado["sign"] == "positive"


def test_delta_zero_por_construcao_e_classificado_como_zero():
    """a(x - r)^2 tem delta exatamente zero — o caso que o sorteio nunca da."""
    for r in range(-5, 6):
        for a in (1, -3, 4):
            resultado = lambda_handler(event(a, -2 * a * r, a * r * r), Context())

            assert resultado["sign"] == "zero", (a, r)


def test_overflow_vira_value_error():
    with pytest.raises(ValueError):
        lambda_handler(event(1e-320, 1e200, 1), Context())


def test_a_zero_ainda_falha_se_escapar_da_validacao():
    """Defesa em profundidade: o estado anterior ja recusa, este nao confia."""
    with pytest.raises(ValueError):
        lambda_handler(event(0, 1, 1), Context())


def test_caos_falha_e_depois_passa():
    meta = {"chaos": {"state": "Delta", "fails": 1}}

    with pytest.raises(TransientFailure):
        lambda_handler(event(1, -5, 6, meta=meta, retry_count=0), Context())

    assert lambda_handler(event(1, -5, 6, meta=meta, retry_count=1), Context())["value"] == 1


def test_caos_de_outro_estado_nao_afeta_este():
    meta = {"chaos": {"state": "Validate", "fails": 9}}

    assert lambda_handler(event(1, -5, 6, meta=meta), Context())["value"] == 1


def test_log_sai_como_json_de_uma_linha(capsys):
    lambda_handler(event(1, -5, 6), Context())

    registro = json.loads(capsys.readouterr().out.strip())

    assert registro["event"] == "delta_calculated"
    assert registro["sign"] == "positive"
