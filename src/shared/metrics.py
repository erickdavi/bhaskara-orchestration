"""Metricas de negocio no formato EMF, montadas como estrutura de dados.

Este modulo nao escreve nada. Ele monta o bloco que o `observability.py`
anexa a linha de log — a separacao existe para que a regra mais importante
daqui, a de cardinalidade, seja testavel sem capturar stdout.

## Por que EMF, e nao PutMetricData

`PutMetricData` e uma chamada de API sincrona dentro do caminho quente: soma
latencia a toda invocacao, consome parte do timeout, precisa de permissao IAM
propria e pode falhar — obrigando a decidir, dentro da regra de negocio, o que
fazer quando a telemetria da erro.

O **Embedded Metric Format** inverte isso. A metrica vai escrita no proprio log,
num bloco `_aws` na raiz da linha, e o CloudWatch a extrai do lado dele. Custo
de rede no handler: zero. Permissao adicional: nenhuma.

A medicao em docs/cycle-08.md confirmou que o `print()` atravessa o formato JSON
do runtime sem ser embrulhado — que e a condicao para o `_aws` chegar na raiz.

## A linha e uma so

O bloco EMF nao substitui a linha de log: ele entra **nela**. Uma unica linha
carrega o que aconteceu, os campos de correlacao e a metrica.

Sao duas vantagens. A ingestao nao dobra — seria pagar duas vezes pelo mesmo
evento. E, de um pico no grafico, da para saltar direto para as linhas que o
produziram, porque a mesma linha que virou ponto no grafico carrega
`execution` e `batch_id`.

## A regra de cardinalidade

Uma metrica do CloudWatch e identificada pelo nome **mais o conjunto de valores**
das suas dimensoes. `HandlerDuration{Service=delta}` e
`HandlerDuration{Service=root}` sao duas metricas, cobradas como duas.

Entao usar `execution` como dimensao criaria uma metrica nova **por equacao
processada** — series infinitas a partir de volume infinito, cada uma cobrada
por mes. E o jeito mais rapido de transformar observabilidade em fatura.

Os ids continuam na linha, como propriedade: seguem pesquisaveis no Logs
Insights, sem virar serie temporal. A diferenca entre as duas coisas e o que
`FORBIDDEN` protege, com teste.
"""

NAMESPACE = "Bhaskara/Orchestration"

COUNT = "Count"
MILLISECONDS = "Milliseconds"

# Campos que jamais podem ser dimensao. Nao e uma lista de estilo: cada um
# destes tem tantos valores distintos quanto equacoes processadas.
FORBIDDEN = frozenset(
    {
        "execution",
        "Execution",
        "execution_name",
        "batch_id",
        "BatchId",
        "message_id",
        "MessageId",
        "request_id",
        "RequestId",
        "idempotency_key",
    }
)


class HighCardinalityDimension(Exception):
    """Tentativa de usar um identificador como dimensao de metrica.

    Levantada na montagem, e nao no console da AWS no fim do mes.
    """


class ConflictingDimension(Exception):
    """Duas metricas da mesma linha querem valores diferentes para a mesma dimensao.

    Em EMF o valor da dimensao e uma propriedade na raiz da linha, e uma raiz
    so tem um valor por chave. Duas metricas com `State` diferente precisam de
    duas linhas.
    """


def counter(name, value=1, **dimensions):
    """Uma contagem: quantas vezes algo aconteceu."""
    return measurement(name, value, COUNT, dimensions)


def duration(name, value_ms, **dimensions):
    """Uma duracao em milissegundos."""
    return measurement(name, value_ms, MILLISECONDS, dimensions)


def measurement(name, value, unit, dimensions):
    for key in dimensions:
        if key in FORBIDDEN:
            raise HighCardinalityDimension(
                f"'{key}' identifica uma equacao e nao pode ser dimensao de '{name}': "
                "seria uma metrica nova por equacao processada. Passe o valor "
                "como campo da linha."
            )

    return {
        "name": name,
        "value": value,
        "unit": unit,
        # Dimensao sem valor nao existe: o `status` nao tem `State`, e declarar
        # State=None criaria a serie "sem estado" em vez de nenhuma serie.
        "dimensions": {
            key: str(value) for key, value in dimensions.items() if value is not None
        },
    }


def block(measurements, timestamp):
    """Monta o bloco EMF de uma linha, ou devolve {} se nao ha o que medir.

    Metricas com o mesmo conjunto de dimensoes vao juntas numa entrada de
    `CloudWatchMetrics`; conjuntos diferentes viram entradas diferentes, o que
    e permitido e comum — a duracao do handler tem dimensao de servico, e a
    distribuicao do discriminante tem dimensao de sinal, na mesma linha.
    """
    if not measurements:
        return {}

    groups = {}
    values = {}
    dimension_values = {}

    for item in measurements:
        keys = tuple(sorted(item["dimensions"]))

        for key, value in item["dimensions"].items():
            if dimension_values.setdefault(key, value) != value:
                raise ConflictingDimension(
                    f"A dimensao '{key}' aparece com '{dimension_values[key]}' e '{value}' na mesma linha; em "
                    "EMF o valor vive na raiz e so pode ser um."
                )

        groups.setdefault(keys, []).append({"Name": item["name"], "Unit": item["unit"]})
        values[item["name"]] = item["value"]

    definitions = [
        {
            "Namespace": NAMESPACE,
            # Uma lista vazia de dimensoes e valida e significa "metrica sem
            # dimensao" — o total, agregado. E o que EquationsSubmitted quer.
            "Dimensions": [list(keys)],
            "Metrics": metrics,
        }
        for keys, metrics in groups.items()
    ]

    emf = {"_aws": {"Timestamp": int(timestamp), "CloudWatchMetrics": definitions}}
    emf.update(dimension_values)
    emf.update(values)

    return emf
