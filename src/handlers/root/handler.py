"""Calcula **uma** raiz. Tres estados do fluxo usam esta mesma funcao.

    RootX1 e RootX2   ramos do Parallel, quando delta > 0
    RootDouble        estado unico, quando delta = 0

Uma funcao por raiz e escolha de **demonstracao**, nao de desempenho: calcular
duas raizes nao justifica duas invocacoes de Lambda, e o custo de rede entre os
ramos e maior que a conta que eles fazem. O que justifica e mostrar o `Parallel`
funcionando de verdade, com dois ramos concorrentes convergindo para o mesmo
estado seguinte — que e o objeto deste checkpoint.

Quando delta = 0 o fluxo **nao** abre o Parallel: as duas raizes sao o mesmo
numero, e calcular duas vezes seria desperdicio sem nenhum ganho didatico. Esse
caso vira uma raiz com rotulo "double", que o Persist expande em x1 e x2.
"""

import json

from chaos import maybe_fail
from quadratic import root

STATE_NAME = "Root"


def lambda_handler(event, context):
    maybe_fail(event, STATE_NAME)

    validated = event.get("validated") or {}
    label = event.get("label")

    value = root(validated["a"], validated["b"], validated["c"], label)

    result = {"label": label, "value": value}

    log(
        event="root_calculated",
        execution=(event.get("meta") or {}).get("idempotency_key"),
        state=event.get("state"),
        **result,
    )

    return result


def log(**fields):
    print(json.dumps(fields, ensure_ascii=False, allow_nan=False))
