"""Testes do bloco EMF.

Duas coisas estao sob teste, e a segunda e a que importa.

A primeira e a **forma**: o CloudWatch so extrai a metrica se o `_aws` estiver
na raiz da linha, se o timestamp estiver em milissegundos e se cada dimensao
declarada existir como propriedade. Errar qualquer um dos tres nao produz erro
nenhum — produz silencio, e uma metrica que nunca aparece no console.

A segunda e a **cardinalidade**. Uma metrica e cobrada por combinacao de valores
de dimensao; usar a chave de idempotencia como dimensao criaria uma serie
temporal por equacao processada. O teste existe para que isso falhe aqui, e nao
na fatura do fim do mes.
"""

import json

import pytest

import metrics
from metrics import (
    COUNT,
    MILLISECONDS,
    NAMESPACE,
    ConflictingDimension,
    HighCardinalityDimension,
    block,
    counter,
    duration,
)

TIMESTAMP = 1789279187599


def definitions(emf):
    return emf["_aws"]["CloudWatchMetrics"]


def names(emf):
    return sorted(m["Name"] for group in definitions(emf) for m in group["Metrics"])


# --------------------------------------------------------------------- forma


def test_o_bloco_tem_aws_na_raiz():
    emf = block([counter("EquationsSubmitted", 50)], TIMESTAMP)

    assert "_aws" in emf
    assert emf["_aws"]["CloudWatchMetrics"][0]["Namespace"] == NAMESPACE


def test_o_timestamp_e_inteiro_em_milissegundos():
    emf = block([counter("X")], 1789279187599.7)

    assert emf["_aws"]["Timestamp"] == 1789279187599
    assert isinstance(emf["_aws"]["Timestamp"], int)


def test_o_valor_da_metrica_vira_propriedade_na_raiz():
    emf = block([counter("EquationsSubmitted", 50)], TIMESTAMP)

    assert emf["EquationsSubmitted"] == 50


def test_o_valor_da_dimensao_vira_propriedade_na_raiz():
    # Sem isto a metrica e descartada em silencio: o CloudWatch procura o valor
    # da dimensao entre as propriedades da linha.
    emf = block([counter("EquationsByDeltaSign", Sign="positive")], TIMESTAMP)

    assert emf["Sign"] == "positive"
    assert definitions(emf)[0]["Dimensions"] == [["Sign"]]


def test_toda_dimensao_declarada_existe_como_propriedade():
    emf = block([duration("HandlerDuration", 12.5, Service="delta", State="Delta")], TIMESTAMP)

    for group in definitions(emf):
        for dimensao in group["Dimensions"][0]:
            assert dimensao in emf, "dimensao %s declarada e ausente da raiz" % dimensao


def test_sem_metrica_nao_ha_bloco():
    # Uma linha de log comum nao deve carregar _aws vazio: o CloudWatch cobraria
    # a extracao de nada.
    assert block([], TIMESTAMP) == {}
    assert block(None, TIMESTAMP) == {}


def test_a_unidade_acompanha_o_tipo_da_medida():
    emf = block([counter("A"), duration("B", 1.0)], TIMESTAMP)

    unidades = {m["Name"]: m["Unit"] for g in definitions(emf) for m in g["Metrics"]}

    assert unidades == {"A": COUNT, "B": MILLISECONDS}


def test_o_bloco_e_serializavel():
    emf = block([duration("HandlerDuration", 12.5, Service="root", State="RootX1")], TIMESTAMP)

    assert json.loads(json.dumps(emf))["HandlerDuration"] == 12.5


# ---------------------------------------------------------------- agrupamento


def test_metricas_com_as_mesmas_dimensoes_vao_no_mesmo_grupo():
    emf = block(
        [counter("A", 1, Service="delta"), counter("B", 2, Service="delta")],
        TIMESTAMP,
    )

    assert len(definitions(emf)) == 1
    assert names(emf) == ["A", "B"]


def test_dimensoes_diferentes_viram_grupos_diferentes():
    # A duracao tem dimensao de servico e a distribuicao tem dimensao de sinal,
    # na mesma linha. Sao dois grupos, e isso e valido em EMF.
    emf = block(
        [duration("HandlerDuration", 3.0, Service="delta"), counter("Sinal", 1, Sign="zero")],
        TIMESTAMP,
    )

    assert len(definitions(emf)) == 2
    assert names(emf) == ["HandlerDuration", "Sinal"]


def test_metrica_sem_dimensao_e_o_total_agregado():
    emf = block([counter("EquationsSubmitted", 50)], TIMESTAMP)

    assert definitions(emf)[0]["Dimensions"] == [[]]


def test_dimensao_sem_valor_nao_e_declarada():
    # O submit e o status nao rodam dentro da state machine: nao ha State. Uma
    # dimensao State=None criaria a serie "sem estado" em vez de nenhuma serie.
    emf = block([duration("HandlerDuration", 4.0, Service="submit", State=None)], TIMESTAMP)

    assert definitions(emf)[0]["Dimensions"] == [["Service"]]
    assert "State" not in emf


def test_o_valor_da_dimensao_vira_texto():
    # O CloudWatch trata valor de dimensao como string; deixar um int passar
    # criaria "1" e 1 como duas series conforme o caminho do codigo.
    emf = block([counter("X", 1, Attempt=2)], TIMESTAMP)

    assert emf["Attempt"] == "2"


# -------------------------------------------------------------- cardinalidade


@pytest.mark.parametrize(
    "campo",
    ["execution", "batch_id", "message_id", "request_id", "execution_name", "idempotency_key"],
)
def test_identificador_nao_pode_ser_dimensao(campo):
    with pytest.raises(HighCardinalityDimension) as erro:
        counter("Qualquer", 1, **{campo: "k-abc"})

    assert campo in str(erro.value)


def test_a_recusa_explica_o_que_fazer_no_lugar():
    with pytest.raises(HighCardinalityDimension) as erro:
        counter("X", 1, execution="k-1")

    assert "campo da linha" in str(erro.value)


def test_a_duracao_tambem_e_protegida():
    with pytest.raises(HighCardinalityDimension):
        duration("HandlerDuration", 1.0, batch_id="b-1")


def test_o_conjunto_proibido_cobre_as_duas_grafias():
    # As dimensoes sao escritas em PascalCase e os campos da linha em snake_case;
    # proteger so uma das grafias deixaria a porta aberta.
    assert "BatchId" in metrics.FORBIDDEN
    assert "batch_id" in metrics.FORBIDDEN


def test_dimensao_legitima_passa():
    assert counter("EquationsByDeltaSign", 1, Sign="positive")["dimensions"] == {"Sign": "positive"}


# ------------------------------------------------------------------ conflito


def test_a_mesma_dimensao_com_dois_valores_e_recusada():
    # Em EMF o valor da dimensao vive na raiz da linha, e uma raiz tem um valor
    # por chave. Duas medidas com State diferente precisam de duas linhas.
    with pytest.raises(ConflictingDimension) as erro:
        block([counter("A", 1, State="RootX1"), counter("B", 1, State="RootX2")], TIMESTAMP)

    assert "State" in str(erro.value)


def test_a_mesma_dimensao_com_o_mesmo_valor_convive():
    emf = block(
        [counter("A", 1, State="Delta"), duration("B", 2.0, State="Delta")],
        TIMESTAMP,
    )

    assert emf["State"] == "Delta"
    assert names(emf) == ["A", "B"]
