"""Testes do estado Validate.

O contrato sob teste tem duas metades. A primeira e o que sai: tres numeros
finitos, para que nenhum estado seguinte precise revalidar. A segunda e o
**nome** do erro que sai, porque e por ele que a state machine decide entre
reentregar e desistir — um erro com o nome errado vira 3 retries inuteis ou,
pior, uma equacao invalida reprocessada para sempre.
"""

import json

import pytest
from chaos import TransientFailure

from src.handlers.validate.handler import InvalidEquation, lambda_handler


class Context:
    aws_request_id = "req-1"


def event(equation=None, meta=None, retry_count=0, **extra):
    payload = {
        "equation": equation,
        "meta": meta if meta is not None else {"idempotency_key": "k1"},
        "retry_count": retry_count,
    }
    payload.update(extra)
    return payload


def test_equacao_valida_sai_normalizada():
    assert lambda_handler(event({"a": 1, "b": -5, "c": 6}), Context()) == {
        "a": 1,
        "b": -5,
        "c": 6,
    }


def test_aceita_float_e_negativos():
    assert lambda_handler(event({"a": -2.5, "b": 0, "c": 3.75}), Context()) == {
        "a": -2.5,
        "b": 0,
        "c": 3.75,
    }


def test_campos_extras_sao_ignorados():
    assert lambda_handler(event({"a": 1, "b": 2, "c": 3, "extra": "x"}), Context()) == {
        "a": 1,
        "b": 2,
        "c": 3,
    }


@pytest.mark.parametrize("ausente", ["a", "b", "c"])
def test_coeficiente_ausente_e_erro_permanente(ausente):
    equation = {"a": 1, "b": -5, "c": 6}
    del equation[ausente]

    with pytest.raises(InvalidEquation) as erro:
        lambda_handler(event(equation), Context())

    assert ausente in str(erro.value)


def test_a_igual_a_zero_e_recusado_antes_da_conta():
    with pytest.raises(InvalidEquation, match="nao pode ser zero"):
        lambda_handler(event({"a": 0, "b": 2, "c": 3}), Context())


def test_a_igual_a_zero_em_float_tambem_e_recusado():
    with pytest.raises(InvalidEquation):
        lambda_handler(event({"a": 0.0, "b": 2, "c": 3}), Context())


def test_coeficiente_como_texto_e_recusado():
    with pytest.raises(InvalidEquation, match="str"):
        lambda_handler(event({"a": "1", "b": -5, "c": 6}), Context())


def test_booleano_nao_vira_numero():
    """isinstance(True, int) e True: sem excluir bool, a=true viraria a=1."""
    with pytest.raises(InvalidEquation, match="bool"):
        lambda_handler(event({"a": True, "b": -5, "c": 6}), Context())


def test_none_e_recusado():
    with pytest.raises(InvalidEquation, match="NoneType"):
        lambda_handler(event({"a": None, "b": -5, "c": 6}), Context())


def test_lista_como_coeficiente_e_recusada():
    with pytest.raises(InvalidEquation):
        lambda_handler(event({"a": [1], "b": -5, "c": 6}), Context())


def test_infinito_e_recusado():
    with pytest.raises(InvalidEquation, match="finito"):
        lambda_handler(event({"a": float("inf"), "b": 1, "c": 1}), Context())


def test_nan_e_recusado():
    with pytest.raises(InvalidEquation, match="finito"):
        lambda_handler(event({"a": 1, "b": float("nan"), "c": 1}), Context())


def test_equacao_que_nao_e_objeto_e_recusada():
    with pytest.raises(InvalidEquation, match="objeto JSON"):
        lambda_handler(event([1, 2, 3]), Context())


# ------------------------------------------------- mensagem que nao era JSON


def test_texto_cru_valido_e_parseado():
    """O dispatcher inicia a execucao mesmo sem conseguir parsear o corpo."""
    payload = event(None, meta={"raw_body": '{"a": 2, "b": 4, "c": 1}'})

    assert lambda_handler(payload, Context()) == {"a": 2, "b": 4, "c": 1}


def test_json_malformado_vira_erro_permanente():
    payload = event(None, meta={"raw_body": "{isto nao e json"})

    with pytest.raises(InvalidEquation, match="nao e JSON valido"):
        lambda_handler(payload, Context())


def test_json_que_nao_e_objeto_e_recusado():
    payload = event(None, meta={"raw_body": "[1, 2, 3]"})

    with pytest.raises(InvalidEquation, match="objeto JSON"):
        lambda_handler(payload, Context())


def test_literal_nan_no_corpo_cru_e_recusado():
    """json.loads aceita NaN por padrao, apesar de a RFC 8259 nao permitir."""
    payload = event(None, meta={"raw_body": '{"a": NaN, "b": 1, "c": 1}'})

    with pytest.raises(InvalidEquation):
        lambda_handler(payload, Context())


def test_literal_infinity_no_corpo_cru_e_recusado():
    payload = event(None, meta={"raw_body": '{"a": 1, "b": Infinity, "c": 1}'})

    with pytest.raises(InvalidEquation):
        lambda_handler(payload, Context())


def test_mensagem_sem_equacao_nem_corpo_e_recusada():
    with pytest.raises(InvalidEquation, match="sem equacao"):
        lambda_handler(event(None, meta={}), Context())


# ------------------------------------------------------------------- caos


def test_caos_falha_enquanto_a_tentativa_for_menor_que_o_pedido():
    meta = {"idempotency_key": "k1", "chaos": {"state": "Validate", "fails": 2}}

    for tentativa in (0, 1):
        with pytest.raises(TransientFailure):
            lambda_handler(
                event({"a": 1, "b": 2, "c": 1}, meta=meta, retry_count=tentativa),
                Context(),
            )


def test_caos_deixa_passar_depois_das_falhas_pedidas():
    meta = {"idempotency_key": "k1", "chaos": {"state": "Validate", "fails": 2}}

    assert lambda_handler(
        event({"a": 1, "b": 2, "c": 1}, meta=meta, retry_count=2), Context()
    )


def test_caos_de_outro_estado_nao_afeta_este():
    meta = {"chaos": {"state": "Delta", "fails": 5}}

    assert lambda_handler(event({"a": 1, "b": 2, "c": 1}, meta=meta), Context())


def test_caos_acontece_antes_da_validacao():
    """A falha injetada nao pode ser mascarada por uma equacao invalida."""
    meta = {"chaos": {"state": "Validate", "fails": 1}}

    with pytest.raises(TransientFailure):
        lambda_handler(event({"a": 0}, meta=meta), Context())


def test_log_sai_como_json_de_uma_linha(capsys):
    lambda_handler(event({"a": 1, "b": -5, "c": 6}), Context())

    linhas = capsys.readouterr().out.strip().splitlines()

    assert len(linhas) == 1
    assert json.loads(linhas[0])["event"] == "equation_validated"
