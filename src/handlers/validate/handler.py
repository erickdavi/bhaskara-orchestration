"""Primeiro estado do fluxo: transforma uma mensagem em coeficientes confiaveis.

    Validate -> Delta -> Choice -> Root(s) -> Persist

No Checkpoint 2 um unico worker parseava, validava e calculava. Aqui a
validacao e um estado proprio, e isso muda uma coisa importante: o resto do
fluxo pode assumir que `$.validated` tem tres numeros finitos. Nenhum estado
seguinte repete essa checagem.

O contrato de erro tambem muda de forma. La, o worker classificava o erro e
escolhia o destino da mensagem em codigo. Aqui, o estado apenas **nomeia** o
erro — `InvalidEquation` — e quem decide o que fazer com esse nome e a state
machine, no `Retry`/`Catch` declarado em YAML. O codigo nao sabe que existe uma
dead-letter queue.
"""

import json
import math

from chaos import maybe_fail

COEFFICIENTS = ("a", "b", "c")

STATE_NAME = "Validate"


class InvalidEquation(Exception):
    """Erro permanente: a equacao nunca vai funcionar, reentregar nao ajuda.

    O nome desta classe vira o `errorType` da Lambda e e o mesmo que aparece em
    `ErrorEquals` no YAML, com `MaxAttempts: 0`. Renomear uma coisa sem a outra
    faria a state machine reentregar 3 vezes uma mensagem irrecuperavel.
    """


def lambda_handler(event, context):
    maybe_fail(event, STATE_NAME)

    equation = coerce(event)

    missing = [name for name in COEFFICIENTS if name not in equation]

    if missing:
        raise InvalidEquation("Coeficientes ausentes: %s." % ", ".join(missing))

    validated = {name: coefficient(equation[name], name) for name in COEFFICIENTS}

    if validated["a"] == 0:
        # A mesma regra que o calculator aplica, verificada uma etapa antes: o
        # estado Delta nao deve descobrir isso no meio da conta.
        raise InvalidEquation("O valor de 'a' nao pode ser zero.")

    log(
        event="equation_validated",
        execution=execution_name(event),
        **validated,
    )

    return validated


def coerce(event):
    """Devolve o objeto da equacao, venha ele parseado ou como texto cru.

    O dispatcher inicia a execucao mesmo quando a mensagem nao e JSON valido —
    de proposito. Recusar no dispatcher significaria uma mensagem que nunca
    aparece no fluxo, e o operador ficaria sem entender por que a contagem nao
    fecha. Chegando ate aqui, ela percorre o mesmo caminho de erro que todas as
    outras e termina na dead-letter com o motivo anexado.
    """
    equation = event.get("equation")

    if isinstance(equation, dict):
        return equation

    if equation is not None:
        raise InvalidEquation(
            "A equacao deve ser um objeto JSON, e nao %s." % type(equation).__name__
        )

    raw = (event.get("meta") or {}).get("raw_body")

    if raw is None:
        raise InvalidEquation("Mensagem sem equacao.")

    try:
        # parse_constant intercepta NaN, Infinity e -Infinity, que o json.loads
        # aceita por padrao apesar de a RFC 8259 nao os permitir. Sem isso eles
        # atravessariam a validacao de tipo abaixo — sao float.
        parsed = json.loads(raw, parse_constant=reject_constant)
    except json.JSONDecodeError as error:
        raise InvalidEquation("Corpo da mensagem nao e JSON valido: %s" % error) from None

    if not isinstance(parsed, dict):
        raise InvalidEquation("Corpo da mensagem deve ser um objeto JSON.")

    return parsed


def coefficient(value, name):
    # isinstance(True, int) e True em Python: sem excluir bool explicitamente,
    # {"a": true} viraria a = 1 e a equacao seria resolvida com um coeficiente
    # que o remetente nunca quis enviar.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidEquation(
            "O coeficiente '%s' deve ser um numero, e nao %s." % (name, type(value).__name__)
        )

    if not math.isfinite(value):
        raise InvalidEquation("O coeficiente '%s' deve ser um numero finito." % name)

    return value


def reject_constant(name):
    raise InvalidEquation("Os coeficientes nao aceitam o literal %s." % name)


def execution_name(event):
    return (event.get("meta") or {}).get("idempotency_key")


def log(**fields):
    # print em vez de logging: o runtime da Lambda prefixa as linhas do logging
    # com nivel, timestamp e requestId, o que quebraria o JSON puro que o
    # CloudWatch Logs Insights consulta por campo.
    print(json.dumps(fields, ensure_ascii=False, allow_nan=False))
