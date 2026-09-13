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

from chaos import TransientFailure, maybe_fail
from metrics import counter
from observability import ERROR, WARN, invocation

COEFFICIENTS = ("a", "b", "c")

STATE_NAME = "Validate"

# Qual das sete funcoes emitiu a linha. Constante explicita, e nao variavel de
# ambiente: o simulador local roda as sete no mesmo processo, e uma variavel so
# daria o mesmo nome para todas.
SERVICE = "validate"


class InvalidEquation(Exception):
    """Erro permanente: a equacao nunca vai funcionar, reentregar nao ajuda.

    O nome desta classe vira o `errorType` da Lambda e e o mesmo que aparece em
    `ErrorEquals` no YAML, com `MaxAttempts: 0`. Renomear uma coisa sem a outra
    faria a state machine reentregar 3 vezes uma mensagem irrecuperavel.

    O `reason` e um codigo curto de um conjunto fechado, e nao a frase de erro:
    ele vira dimensao da metrica `ValidationRejected`, e uma dimensao cujo valor
    inclui o nome do coeficiente ou o texto do JSONDecodeError seria uma serie
    temporal nova por mensagem malformada.
    """

    def __init__(self, message, reason="unknown"):
        super().__init__(message)
        self.reason = reason


def lambda_handler(event, context):
    log = invocation(SERVICE, event, context)

    try:
        maybe_fail(event, STATE_NAME)

        equation = coerce(event)

        missing = [name for name in COEFFICIENTS if name not in equation]

        if missing:
            raise InvalidEquation(
                "Coeficientes ausentes: %s." % ", ".join(missing), "missing_coefficient"
            )

        validated = {name: coefficient(equation[name], name) for name in COEFFICIENTS}

        if validated["a"] == 0:
            # A mesma regra que o calculator aplica, verificada uma etapa antes: o
            # estado Delta nao deve descobrir isso no meio da conta.
            raise InvalidEquation("O valor de 'a' nao pode ser zero.", "a_is_zero")
    except InvalidEquation as error:
        # A recusa vira linha de erro antes de virar excecao. A state machine
        # registra o **nome** do erro, que e o que ela usa para rotear; a frase
        # que diz qual coeficiente estava errado so existe aqui.
        log(
            "equation_rejected",
            level=ERROR,
            measures=[counter("ValidationRejected", Reason=error.reason)],
            error_type="InvalidEquation",
            reason=error.reason,
            detail=str(error),
        )
        raise
    except TransientFailure as error:
        # Caos e falha pedida pela carga. Registrada como WARN, com evento e
        # metrica proprios, para nao poluir a contagem de erro real na analise.
        log(
            "chaos_injected",
            level=WARN,
            measures=[counter("ChaosInjected")],
            error_type="TransientFailure",
            detail=str(error),
        )
        raise

    log("equation_validated", measures=[], **validated)

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
            "A equacao deve ser um objeto JSON, e nao %s." % type(equation).__name__,
            "equation_not_object",
        )

    raw = (event.get("meta") or {}).get("raw_body")

    if raw is None:
        raise InvalidEquation("Mensagem sem equacao.", "missing_equation")

    try:
        # parse_constant intercepta NaN, Infinity e -Infinity, que o json.loads
        # aceita por padrao apesar de a RFC 8259 nao os permitir. Sem isso eles
        # atravessariam a validacao de tipo abaixo — sao float.
        parsed = json.loads(raw, parse_constant=reject_constant)
    except json.JSONDecodeError as error:
        raise InvalidEquation(
            "Corpo da mensagem nao e JSON valido: %s" % error, "malformed_json"
        ) from None

    if not isinstance(parsed, dict):
        raise InvalidEquation("Corpo da mensagem deve ser um objeto JSON.", "body_not_object")

    return parsed


def coefficient(value, name):
    # isinstance(True, int) e True em Python: sem excluir bool explicitamente,
    # {"a": true} viraria a = 1 e a equacao seria resolvida com um coeficiente
    # que o remetente nunca quis enviar.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidEquation(
            "O coeficiente '%s' deve ser um numero, e nao %s." % (name, type(value).__name__),
            "coefficient_not_number",
        )

    if not math.isfinite(value):
        raise InvalidEquation(
            "O coeficiente '%s' deve ser um numero finito." % name, "coefficient_not_finite"
        )

    return value


def reject_constant(name):
    raise InvalidEquation("Os coeficientes nao aceitam o literal %s." % name, "nan_literal")
