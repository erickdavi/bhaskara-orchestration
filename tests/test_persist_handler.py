"""Testes do estado Persist e da chave de idempotencia.

O comportamento sob teste nao e "gravou": e **quantas vezes** gravou, e o que
acontece na segunda. Idempotencia que so funciona no caminho feliz nao e
idempotencia; por isso os testes cobrem a duplicata, o item que volta dela e a
recusa em transformar conflito em falha.
"""

import pytest

from idempotency import canonical, execution_name, key
from local.doubles import ClientError
from src.handlers.persist.handler import lambda_handler


class Context:
    aws_request_id = "req-1"


@pytest.fixture
def table(aws):
    """A tabela em memoria ligada pela fixture autouse do conftest."""
    return aws["dynamodb"]


def event(pk="k1", batch="b1", a=1, b=-5, c=6, delta=1, sign="positive", roots=None):
    return {
        "validated": {"a": a, "b": b, "c": c},
        "delta": {"value": delta, "sign": sign},
        "result": {"roots": roots if roots is not None else [
            {"label": "x1", "value": 3.0},
            {"label": "x2", "value": 2.0},
        ]},
        "meta": {"idempotency_key": pk, "batch_id": batch, "message_id": "msg-1"},
        "state": "Persist",
        "retry_count": 0,
    }


# ------------------------------------------------------------------- chave


def test_ordem_das_chaves_nao_muda_a_chave():
    assert key("b1", '{"a":1,"b":-5,"c":6}') == key("b1", '{"c":6,"a":1,"b":-5}')


def test_equacoes_diferentes_tem_chaves_diferentes():
    assert key("b1", '{"a":1,"b":-5,"c":6}') != key("b1", '{"a":1,"b":-5,"c":7}')


def test_cargas_diferentes_nao_deduplicam_entre_si():
    """Sem isso, a segunda demonstracao do dia apareceria vazia."""
    assert key("b1", '{"a":1,"b":-5,"c":6}') != key("b2", '{"a":1,"b":-5,"c":6}')


def test_corpo_invalido_tambem_recebe_chave():
    assert len(key("b1", "{isto nao e json")) == 40


def test_canonical_ignora_espacos_no_corpo_invalido():
    assert canonical("  {isto nao e json  ") == "{isto nao e json"


def test_nome_da_execucao_cabe_no_limite_do_servico():
    assert len(execution_name(key("b1", '{"a":1,"b":2,"c":3}'))) <= 80


# ----------------------------------------------------------------- gravacao


def test_primeira_gravacao_registra_o_item(table):
    resultado = lambda_handler(event(), Context())

    assert resultado["stored"] is True
    assert resultado["duplicate"] is False
    assert table.puts == 1
    assert list(table.items) == ["k1"]


def test_item_guarda_a_equacao_e_as_raizes(table):
    lambda_handler(event(), Context())

    item = table.items["k1"]

    assert item["batch_id"]["S"] == "b1"
    assert item["sign"]["S"] == "positive"
    assert [root["S"] for root in item["roots"]["L"]] == ["3.0", "2.0"]


def test_raiz_dupla_vira_dois_valores_iguais(table):
    lambda_handler(event(roots=[{"label": "double", "value": 2.0}]), Context())

    assert [root["S"] for root in table.items["k1"]["roots"]["L"]] == ["2.0", "2.0"]


def test_sem_raizes_reais_grava_lista_vazia(table):
    lambda_handler(event(roots=[], sign="negative", delta=-20), Context())

    assert table.items["k1"]["roots"]["L"] == []


def test_numeros_vao_como_texto_para_nao_estourar_o_tipo_n(table):
    """O tipo N do DynamoDB vai ate 1e125; um float64 vai a 1e308."""
    lambda_handler(event(a=1e-200, b=1e200, c=1.0, roots=[{"label": "x1", "value": 1e250}]), Context())

    item = table.items["k1"]

    assert item["b"]["S"] == "1e+200"
    assert item["roots"]["L"][0]["S"] == "1e+250"


def test_ttl_e_gravado_no_futuro(table):
    lambda_handler(event(), Context())

    item = table.items["k1"]

    assert int(item["expires_at"]["N"]) > int(item["created_at"]["N"]) / 1000


def test_execucao_sem_chave_e_erro(table):
    payload = event()
    payload["meta"] = {}

    with pytest.raises(ValueError, match="chave de idempotencia"):
        lambda_handler(payload, Context())


# -------------------------------------------------------------- idempotencia


def test_segunda_gravacao_da_mesma_chave_nao_duplica(table):
    lambda_handler(event(), Context())
    resultado = lambda_handler(event(), Context())

    assert resultado["duplicate"] is True
    assert resultado["stored"] is False
    assert len(table.items) == 1
    assert table.conditional_failures == 1


def test_duplicata_devolve_o_resultado_ja_gravado(table):
    lambda_handler(event(), Context())

    # A segunda tentativa chega com raizes diferentes de proposito: o que deve
    # voltar e o que **esta na tabela**, e nao o que veio na segunda execucao.
    resultado = lambda_handler(event(roots=[{"label": "x1", "value": 99.0}]), Context())

    assert resultado["result"]["roots"] == [3, 2]


def test_duplicata_nao_e_falha_do_estado(table):
    lambda_handler(event(), Context())

    resultado = lambda_handler(event(), Context())

    assert resultado["key"] == "k1"


def test_chaves_diferentes_convivem(table):
    lambda_handler(event(pk="k1"), Context())
    lambda_handler(event(pk="k2"), Context())

    assert sorted(table.items) == ["k1", "k2"]


def test_falha_que_nao_e_conflito_sobe_como_erro(table, monkeypatch):
    """Indisponibilidade nao pode ser confundida com duplicata."""

    def explode(**kwargs):
        raise ClientError("ProvisionedThroughputExceededException", "slow down")

    monkeypatch.setattr(table, "put_item", explode)

    with pytest.raises(ClientError):
        lambda_handler(event(), Context())


def test_resultado_legivel_converte_texto_em_numero(table):
    resultado = lambda_handler(event(), Context())

    assert resultado["result"]["a"] == 1
    assert resultado["result"]["roots"] == [3, 2]
    assert isinstance(resultado["result"]["delta"], int)
