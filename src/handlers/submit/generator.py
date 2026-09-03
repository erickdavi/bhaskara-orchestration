"""Geracao da carga publicada na fila orders.

Separado do handler de proposito: o handler cuida de HTTP e de SQS, este modulo
cuida de "como e uma carga interessante". Sao dois motivos de mudanca
diferentes, e este e o unico dos dois que da para testar sem nocao nenhuma de
requisicao ou de fila.

Uma carga interessante para **este** checkpoint nao e so um monte de equacao.
Ela precisa exercitar, de forma visivel no painel:

    os tres ramos do Choice     equacoes construidas a partir do desfecho
    o retry se recuperando      caos com fails <= 3
    a dead-letter               caos com fails > 3 e equacoes invalidas
    a idempotencia              equacoes repetidas de proposito

Por isso a geracao parte do desfecho desejado e nao de coeficientes ao acaso:
sorteando a, b e c soltos, raiz dupla (delta = 0) praticamente nunca apareceria,
porque exige b^2 == 4ac exato — e o ramo RootDouble ficaria apagado no diagrama.
"""

import json
import random

OUTCOMES = ("two_roots", "double_root", "no_real_roots")

# Categorias de mensagem invalida, uma para cada caminho de recusa do estado
# Validate. Servem para alimentar a dead-letter com trafego realista.
INVALID_KINDS = (
    "malformed_json",
    "missing_coefficient",
    "coefficient_as_string",
    "coefficient_as_boolean",
    "a_is_zero",
)

# Estados que cada desfecho realmente percorre. Pedir caos em RootDouble para
# uma equacao de delta > 0 nao falharia nunca — o estado nem seria visitado — e
# a carga entregaria menos falhas do que o operador pediu.
CHAOS_STATES = {
    "two_roots": ("Validate", "Delta", "RootX1", "RootX2", "Persist"),
    "double_root": ("Validate", "Delta", "RootDouble", "Persist"),
    "no_real_roots": ("Validate", "Delta", "Persist"),
    "invalid": ("Validate",),
}

# Quantas vezes a falha injetada se repete. Ate 3 o Retry se recupera; 5 esgota
# as tentativas e a mensagem termina na dead-letter. A proporcao de 1 para 3
# existe para que o painel mostre os dois desfechos na mesma carga.
RECOVERABLE_FAILS = (1, 2, 3)
FATAL_FAILS = 5
FATAL_SHARE = 0.25


def generate(quantity, invalid_ratio=0.0, duplicate_ratio=0.0, chaos_ratio=0.0, seed=None):
    """Produz `quantity` mensagens: (corpo ja serializado, caos ou None).

    Devolve o corpo como string, e nao dicionario, porque parte das mensagens
    invalidas nao e JSON valido — nao haveria como representa-las de outro
    jeito.

    `seed` torna a carga reproduzivel: mesmo seed, mesma sequencia. Sem ele,
    cada chamada gera uma carga diferente.
    """
    rng = random.Random(seed)

    previous = []

    for _ in range(quantity):
        if previous and duplicate_ratio > 0 and rng.random() < duplicate_ratio:
            # Repetir uma mensagem ja publicada e o que faz a idempotencia
            # aparecer: a segunda nao vira execucao nova.
            yield rng.choice(previous)
            continue

        message = build(rng, invalid_ratio, chaos_ratio)

        previous.append(message)

        yield message


def build(rng, invalid_ratio, chaos_ratio):
    if invalid_ratio > 0 and rng.random() < invalid_ratio:
        return invalid_payload(rng), chaos_for(rng, "invalid", chaos_ratio)

    outcome = rng.choice(OUTCOMES)

    return json.dumps(equation(rng, outcome)), chaos_for(rng, outcome, chaos_ratio)


def chaos_for(rng, outcome, chaos_ratio):
    if chaos_ratio <= 0 or rng.random() >= chaos_ratio:
        return None

    fails = FATAL_FAILS if rng.random() < FATAL_SHARE else rng.choice(RECOVERABLE_FAILS)

    return {"state": rng.choice(CHAOS_STATES[outcome]), "fails": fails}


def equation(rng, outcome):
    if outcome == "two_roots":
        return two_roots(rng)

    if outcome == "double_root":
        return double_root(rng)

    return no_real_roots(rng)


def two_roots(rng):
    """Constroi a equacao a partir de duas raizes inteiras distintas.

    Partindo de r1 e r2, os coeficientes saem da forma fatorada
    a(x - r1)(x - r2): b = -a(r1 + r2) e c = a * r1 * r2. O delta e positivo por
    construcao e as raizes sao numeros redondos — o que deixa o resultado
    conferivel a olho no painel.
    """
    r1 = rng.randint(-12, 12)
    r2 = rng.randint(-12, 12)

    while r2 == r1:
        r2 = rng.randint(-12, 12)

    a = nonzero(rng, -6, 6)

    return {"a": a, "b": -a * (r1 + r2), "c": a * r1 * r2}


def double_root(rng):
    """Raiz dupla: delta = 0 exatamente, o caso que o sorteio nunca produz."""
    r = rng.randint(-12, 12)
    a = nonzero(rng, -6, 6)

    return {"a": a, "b": -2 * a * r, "c": a * r * r}


def no_real_roots(rng):
    """Delta negativo: c grande o bastante para que b^2 < 4ac.

    O sinal dos tres coeficientes pode ser invertido no fim: (-a, -b, -c)
    descreve a mesma parabola refletida e tem o mesmo delta.
    """
    a = rng.randint(1, 6)
    b = rng.randint(-30, 30)
    c = b * b // (4 * a) + rng.randint(1, 20)

    if rng.random() < 0.5:
        return {"a": -a, "b": -b, "c": -c}

    return {"a": a, "b": b, "c": c}


def nonzero(rng, low, high):
    value = 0

    while value == 0:
        value = rng.randint(low, high)

    return value


def invalid_payload(rng):
    kind = rng.choice(INVALID_KINDS)

    if kind == "malformed_json":
        return "{isto nao e json"

    if kind == "missing_coefficient":
        missing = rng.choice("abc")
        payload = {"a": 1, "b": -5, "c": 6}
        del payload[missing]
        return json.dumps(payload)

    if kind == "coefficient_as_string":
        return json.dumps({"a": "1", "b": -5, "c": 6})

    if kind == "coefficient_as_boolean":
        return json.dumps({"a": True, "b": -5, "c": 6})

    return json.dumps({"a": 0, "b": rng.randint(-9, 9), "c": rng.randint(-9, 9)})
