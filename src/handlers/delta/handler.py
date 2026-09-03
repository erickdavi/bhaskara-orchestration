"""Segundo estado: calcula o discriminante e classifica o sinal.

    delta = b^2 - 4ac

O estado nao devolve so o numero: devolve tambem `sign`, uma das tres palavras
`positive`, `zero` ou `negative`. O `Choice` seguinte compara **essa palavra**,
e nao o numero.

A diferenca importa. Um `NumericGreaterThan: 0` no YAML funcionaria, mas
espalharia a regra "delta zero significa raiz dupla" entre o codigo e a
definicao do fluxo. Com a classificacao aqui, a state machine so roteia — quem
sabe matematica e o codigo, e o YAML fica legivel para quem nunca viu Bhaskara.
"""

import json

from chaos import maybe_fail
from quadratic import discriminant

STATE_NAME = "Delta"

POSITIVE = "positive"
ZERO = "zero"
NEGATIVE = "negative"


def lambda_handler(event, context):
    maybe_fail(event, STATE_NAME)

    validated = event.get("validated") or {}

    value = discriminant(validated["a"], validated["b"], validated["c"])

    result = {"value": value, "sign": classify(value)}

    log(
        event="delta_calculated",
        execution=execution_name(event),
        **result,
    )

    return result


def classify(value):
    if value > 0:
        return POSITIVE

    if value == 0:
        return ZERO

    return NEGATIVE


def execution_name(event):
    return (event.get("meta") or {}).get("idempotency_key")


def log(**fields):
    print(json.dumps(fields, ensure_ascii=False, allow_nan=False))
