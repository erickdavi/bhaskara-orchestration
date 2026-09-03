"""Interpretador da Amazon States Language, em processo.

Este modulo executa `workflow/bhaskara.asl.yaml` — o **mesmo** arquivo que o
Terraform envia para o Step Functions — chamando os handlers reais das Lambdas
como funcoes Python. E o que permite rodar o fluxo inteiro sem conta AWS,
sem Docker e sem credencial.

O objetivo nao e reimplementar o Step Functions: e implementar exatamente o
subconjunto que esta definicao usa, e **recusar** o resto de forma barulhenta.
`validate_definition()` levanta `Unsupported` diante de qualquer tipo de estado
ou campo que este interpretador nao conheca, e um teste roda essa validacao
contra o YAML de verdade. Assim nao da para acrescentar um `Map` a state
machine e descobrir na nuvem que a demonstracao local nunca exercitou aquele
caminho.

O que esta implementado:

    estados      Task, Choice, Parallel, Pass, Succeed, Fail
    fluxo        Next, End, StartAt, Default
    payload      Parameters, ResultSelector, ResultPath, OutputPath ("$")
    caminhos     JSONPath simples ($.a.b) e objeto de contexto ($$)
    intrinseca   States.Array
    falhas       Retry (ErrorEquals, IntervalSeconds, MaxAttempts, BackoffRate,
                 JitterStrategy) e Catch (ErrorEquals, ResultPath, Next)

O que nao esta, e levanta Unsupported: Map, Wait, InputPath, TimeoutSeconds,
Parameters em Choice, operadores compostos (And/Or/Not) e todas as demais
funcoes intrinsecas.

Os eventos emitidos usam os mesmos nomes de tipo que o Step Functions grava no
CloudWatch (`TaskStateEntered`, `TaskFailed`, `ExecutionSucceeded`, ...), para
que o painel leia o fluxo local e o fluxo na nuvem com o mesmo codigo.
"""

import json
import time

TERMINAL_TYPES = ("Succeed", "Fail")

# Campos aceitos por tipo de estado. Um campo fora desta lista faz
# validate_definition levantar Unsupported — e o que impede a definicao de
# crescer sem que o interpretador cresca junto.
COMMON_FIELDS = {"Type", "Comment", "Next", "End"}

ALLOWED_FIELDS = {
    "Task": COMMON_FIELDS | {"Resource", "Parameters", "ResultSelector", "ResultPath", "Retry", "Catch"},
    "Choice": COMMON_FIELDS | {"Choices", "Default"},
    "Parallel": COMMON_FIELDS | {"Branches", "Parameters", "ResultSelector", "ResultPath", "Retry", "Catch"},
    "Pass": COMMON_FIELDS | {"Result", "Parameters", "ResultPath"},
    "Succeed": COMMON_FIELDS,
    "Fail": COMMON_FIELDS | {"Error", "Cause"},
}

CHOICE_OPERATORS = (
    "StringEquals",
    "NumericEquals",
    "NumericGreaterThan",
    "NumericGreaterThanEquals",
    "NumericLessThan",
    "NumericLessThanEquals",
    "BooleanEquals",
    "IsPresent",
)

RETRY_FIELDS = {"ErrorEquals", "IntervalSeconds", "MaxAttempts", "BackoffRate", "JitterStrategy", "MaxDelaySeconds"}
CATCH_FIELDS = {"ErrorEquals", "ResultPath", "Next"}

ALL_ERRORS = "States.ALL"
TASK_FAILED = "States.TaskFailed"

# Erros States.* nao sao capturados por States.TaskFailed — a regra e do
# proprio Step Functions e muda quais retriers casam com o que.
RESERVED_PREFIX = "States."


class Unsupported(Exception):
    """A definicao usa algo que este interpretador nao implementa."""


class TaskFailure(Exception):
    """Falha nomeada de um estado, no formato que o Retry/Catch compara."""

    def __init__(self, error, cause=""):
        super().__init__("%s: %s" % (error, cause))
        self.error = error
        self.cause = cause


class ExecutionFailed(Exception):
    """A execucao terminou em falha (estado Fail ou erro nao capturado)."""

    def __init__(self, error, cause=""):
        super().__init__("%s: %s" % (error, cause))
        self.error = error
        self.cause = cause


class Execution:
    """O registro de uma execucao: entrada, saida, desfecho e eventos.

    Os eventos sao o que alimenta a timeline do painel. Guardar a lista inteira
    aqui e viavel porque a execucao e curta e vive em memoria; na nuvem esse
    papel e do historico do Step Functions.
    """

    def __init__(self, name, input_data, started_at):
        self.name = name
        self.input = input_data
        self.started_at = started_at
        self.stopped_at = None
        self.status = "RUNNING"
        self.output = None
        self.error = None
        self.cause = None
        self.events = []
        self._next_id = 1

    def emit(self, event_type, timestamp, **details):
        event = {
            "id": self._next_id,
            "type": event_type,
            "timestamp": timestamp,
            "details": {k: v for k, v in details.items() if v is not None},
        }
        self._next_id += 1
        self.events.append(event)
        return event

    def states_entered(self):
        return [e["details"]["name"] for e in self.events if e["type"].endswith("StateEntered")]

    def as_dict(self):
        return {
            "name": self.name,
            "status": self.status,
            "startDate": self.started_at,
            "stopDate": self.stopped_at,
            "error": self.error,
            "cause": self.cause,
        }


def validate_definition(definition):
    """Recusa qualquer construcao que este interpretador nao execute.

    Chamado no construtor do Engine e, por um teste, contra o YAML de verdade.
    """
    if "StartAt" not in definition or "States" not in definition:
        raise Unsupported("A definicao precisa de StartAt e States.")

    _validate_states(definition["States"], definition["StartAt"], "")


def _validate_states(states, start_at, prefix):
    if start_at not in states:
        raise Unsupported("StartAt %r%s nao existe em States." % (start_at, prefix))

    for name, state in states.items():
        where = "%s%s" % (prefix, name)
        state_type = state.get("Type")

        if state_type not in ALLOWED_FIELDS:
            raise Unsupported("Tipo de estado nao suportado em %s: %r." % (where, state_type))

        unknown = set(state) - ALLOWED_FIELDS[state_type]

        if unknown:
            raise Unsupported("Campos nao suportados em %s: %s." % (where, ", ".join(sorted(unknown))))

        if state_type not in TERMINAL_TYPES and state_type != "Choice":
            if not state.get("End") and not state.get("Next"):
                raise Unsupported("O estado %s nao tem Next nem End." % where)

        for target in _targets(state):
            if target not in states:
                raise Unsupported("O estado %s aponta para %r, que nao existe." % (where, target))

        for rule in state.get("Retry") or []:
            unknown = set(rule) - RETRY_FIELDS
            if unknown:
                raise Unsupported("Retry de %s usa %s." % (where, ", ".join(sorted(unknown))))

        for rule in state.get("Catch") or []:
            unknown = set(rule) - CATCH_FIELDS
            if unknown:
                raise Unsupported("Catch de %s usa %s." % (where, ", ".join(sorted(unknown))))

        for choice in state.get("Choices") or []:
            if "Variable" not in choice or "Next" not in choice:
                raise Unsupported("Choice de %s precisa de Variable e Next." % where)

            operators = set(choice) - {"Variable", "Next", "Comment"}

            if len(operators) != 1 or not operators <= set(CHOICE_OPERATORS):
                raise Unsupported(
                    "Operador de Choice nao suportado em %s: %s." % (where, ", ".join(sorted(operators)))
                )

        for index, branch in enumerate(state.get("Branches") or []):
            _validate_states(branch["States"], branch["StartAt"], "%s[%d]." % (where, index))


def _targets(state):
    targets = []

    if state.get("Next"):
        targets.append(state["Next"])

    if state.get("Default"):
        targets.append(state["Default"])

    for choice in state.get("Choices") or []:
        targets.append(choice["Next"])

    for rule in state.get("Catch") or []:
        targets.append(rule["Next"])

    return targets


class Engine:
    """Executa uma definicao ASL resolvendo cada Task contra `resources`.

    `resources` mapeia o valor do campo Resource — o placeholder
    "${validate_arn}" ou o ARN de uma integracao como
    "arn:aws:states:::sqs:sendMessage" — para um chamavel que recebe o payload
    ja resolvido e devolve o resultado.

    `sleeper` existe para que os testes nao esperem o backoff de verdade: por
    padrao o intervalo do Retry e apenas registrado, nao dormido. O simulador
    passa o mesmo padrao — 4 segundos de espera por mensagem transformariam a
    demonstracao em algo intoleravel, e o que se quer mostrar e a sequencia de
    tentativas, nao a duracao.
    """

    def __init__(self, definition, resources, sleeper=None, clock=None):
        validate_definition(definition)

        self.definition = definition
        self.resources = resources
        self.sleeper = sleeper if sleeper is not None else (lambda seconds: None)
        self.clock = clock or (lambda: int(time.time() * 1000))

    # ------------------------------------------------------------------ API

    def start(self, input_data, name=None):
        execution = Execution(name or "exec-%d" % self.clock(), input_data, self.clock())

        context = {
            "Execution": {
                "Name": execution.name,
                "Input": input_data,
                "StartTime": execution.started_at,
            },
            "StateMachine": {"Name": self.definition.get("Comment", "state-machine")[:40]},
            "State": {},
        }

        execution.emit("ExecutionStarted", execution.started_at, input=input_data)

        try:
            output = self._run(self.definition["States"], self.definition["StartAt"], input_data, execution, context)
        except ExecutionFailed as failure:
            execution.status = "FAILED"
            execution.error = failure.error
            execution.cause = failure.cause
            execution.stopped_at = self.clock()
            execution.emit("ExecutionFailed", execution.stopped_at, error=failure.error, cause=failure.cause)
            return execution

        execution.status = "SUCCEEDED"
        execution.output = output
        execution.stopped_at = self.clock()
        execution.emit("ExecutionSucceeded", execution.stopped_at, output=output)

        return execution

    # --------------------------------------------------------------- estados

    def _run(self, states, start_at, data, execution, context):
        name = start_at

        while True:
            state = states[name]
            state_type = state["Type"]

            context["State"] = {"Name": name, "RetryCount": 0, "EnteredTime": self.clock()}

            execution.emit("%sStateEntered" % state_type, self.clock(), name=name, input=data)

            if state_type == "Succeed":
                execution.emit("SucceedStateExited", self.clock(), name=name, output=data)
                return data

            if state_type == "Fail":
                execution.emit("FailStateExited", self.clock(), name=name)
                raise ExecutionFailed(state.get("Error", "States.Fail"), state.get("Cause", ""))

            if state_type == "Choice":
                chosen = self._choose(state, data, name)
                execution.emit("ChoiceStateExited", self.clock(), name=name, next=chosen)
                name = chosen
                continue

            try:
                data = self._execute(state_type, state, data, execution, context, name)
            except _Caught as caught:
                data = caught.data
                name = caught.next_state
                continue

            execution.emit("%sStateExited" % state_type, self.clock(), name=name, output=data)

            if state.get("End"):
                return data

            name = state["Next"]

    def _execute(self, state_type, state, data, execution, context, name):
        """Roda um estado nao terminal, aplicando Retry e Catch."""
        attempt = 0

        while True:
            context["State"]["RetryCount"] = attempt

            try:
                if state_type == "Pass":
                    return self._pass(state, data, context)

                if state_type == "Task":
                    return self._task(state, data, context)

                return self._parallel(state, data, execution, context)

            except TaskFailure as failure:
                execution.emit(
                    "TaskFailed" if state_type == "Task" else "%sFailed" % state_type,
                    self.clock(),
                    name=name,
                    error=failure.error,
                    cause=failure.cause,
                    attempt=attempt,
                )

                delay = self._retry_delay(state.get("Retry") or [], failure.error, attempt)

                if delay is not None:
                    execution.emit(
                        "TaskRetryScheduled" if state_type == "Task" else "%sRetryScheduled" % state_type,
                        self.clock(),
                        name=name,
                        attempt=attempt + 1,
                        delay_seconds=delay,
                    )
                    self.sleeper(delay)
                    attempt += 1
                    continue

                catcher = self._catcher(state.get("Catch") or [], failure.error)

                if catcher is None:
                    raise ExecutionFailed(failure.error, failure.cause) from None

                execution.emit(
                    "%sStateExited" % state_type,
                    self.clock(),
                    name=name,
                    error=failure.error,
                )

                raise _Caught(
                    place(
                        data,
                        catcher.get("ResultPath", ABSENT),
                        {"Error": failure.error, "Cause": failure.cause},
                    ),
                    catcher["Next"],
                ) from None

    def _pass(self, state, data, context):
        if "Result" in state:
            result = state["Result"]
        elif "Parameters" in state:
            result = apply_payload(state["Parameters"], data, context)
        else:
            result = data

        return place(data, state.get("ResultPath", ABSENT), result)

    def _task(self, state, data, context):
        resource = state["Resource"]

        if resource not in self.resources:
            raise Unsupported("Resource sem implementacao local: %s" % resource)

        payload = apply_payload(state.get("Parameters", {}), data, context) if "Parameters" in state else data

        try:
            result = self.resources[resource](payload)
        except TaskFailure:
            raise
        except Exception as error:  # noqa: BLE001 - vira erro nomeado, como na nuvem
            # A Lambda devolve o nome da classe da excecao como errorType, e e
            # esse nome que o ErrorEquals compara. Reproduzir isso aqui e o que
            # faz o Retry local casar com o Retry da nuvem.
            raise TaskFailure(type(error).__name__, str(error)) from None

        if "ResultSelector" in state:
            result = apply_payload(state["ResultSelector"], result, context)

        return place(data, state.get("ResultPath", ABSENT), result)

    def _parallel(self, state, data, execution, context):
        payload = apply_payload(state["Parameters"], data, context) if "Parameters" in state else data

        results = []

        for index, branch in enumerate(state["Branches"]):
            branch_context = dict(context)

            try:
                results.append(
                    self._run(branch["States"], branch["StartAt"], payload, execution, branch_context)
                )
            except ExecutionFailed as failure:
                # A falha de um ramo falha o Parallel inteiro, com o erro do
                # ramo — e e o Retry/Catch do Parallel que decide o que fazer.
                # Sem esta conversao o erro subiria direto para a execucao e o
                # Catch do Parallel nunca agiria: em ASL, o Next de um estado
                # so aponta para o mesmo nivel, entao o ramo nao pode capturar
                # sozinho.
                raise TaskFailure(failure.error, failure.cause) from None

        if "ResultSelector" in state:
            results = apply_payload(state["ResultSelector"], results, context)

        return place(data, state.get("ResultPath", ABSENT), results)

    def _choose(self, state, data, name):
        for choice in state["Choices"]:
            if matches(choice, data):
                return choice["Next"]

        if "Default" not in state:
            raise ExecutionFailed("States.NoChoiceMatched", "Nenhuma regra do Choice %s casou." % name)

        return state["Default"]

    # ---------------------------------------------------------------- falhas

    def _retry_delay(self, retriers, error, attempt):
        """Segundos ate a proxima tentativa, ou None se nao ha mais retry.

        Percorre os retriers na ordem e usa o **primeiro** que casa, como o
        Step Functions faz. E por isso que o retrier de InvalidEquation, com
        MaxAttempts 0, precisa vir antes do que casa States.TaskFailed.
        """
        for rule in retriers:
            if not error_matches(rule["ErrorEquals"], error):
                continue

            max_attempts = rule.get("MaxAttempts", 3)

            if attempt >= max_attempts:
                return None

            interval = rule.get("IntervalSeconds", 1)
            backoff = rule.get("BackoffRate", 2.0)
            delay = interval * (backoff ** attempt)

            if "MaxDelaySeconds" in rule:
                delay = min(delay, rule["MaxDelaySeconds"])

            return delay

        return None

    def _catcher(self, catchers, error):
        for rule in catchers:
            if error_matches(rule["ErrorEquals"], error):
                return rule

        return None


class _Caught(Exception):
    """Sinaliza, internamente, que um Catch desviou o fluxo."""

    def __init__(self, data, next_state):
        super().__init__(next_state)
        self.data = data
        self.next_state = next_state


def error_matches(error_equals, error):
    for candidate in error_equals:
        if candidate == error:
            return True

        if candidate == ALL_ERRORS:
            return True

        # States.TaskFailed casa com qualquer erro da funcao, mas nao com os
        # erros reservados do proprio servico.
        if candidate == TASK_FAILED and not error.startswith(RESERVED_PREFIX):
            return True

    return False


def matches(choice, data):
    variable = resolve(choice["Variable"], data, {}, missing=_MISSING)

    if "IsPresent" in choice:
        return (variable is not _MISSING) == choice["IsPresent"]

    if variable is _MISSING:
        return False

    for operator in CHOICE_OPERATORS:
        if operator not in choice:
            continue

        expected = choice[operator]

        if operator in ("StringEquals", "NumericEquals", "BooleanEquals"):
            return variable == expected

        if operator == "NumericGreaterThan":
            return variable > expected

        if operator == "NumericGreaterThanEquals":
            return variable >= expected

        if operator == "NumericLessThan":
            return variable < expected

        return variable <= expected

    return False


# ------------------------------------------------------------------ payload

_MISSING = object()

# Sentinela de "campo nao declarado", distinto de um campo declarado como null.
ABSENT = object()


def apply_payload(template, data, context):
    """Resolve Parameters/ResultSelector: chaves com sufixo .$ viram caminhos."""
    if isinstance(template, dict):
        resolved = {}

        for key, value in template.items():
            if key.endswith(".$"):
                resolved[key[:-2]] = _resolve_expression(value, data, context)
            else:
                resolved[key] = apply_payload(value, data, context)

        return resolved

    if isinstance(template, list):
        return [apply_payload(item, data, context) for item in template]

    return template


def _resolve_expression(expression, data, context):
    if not isinstance(expression, str):
        raise Unsupported("O valor de uma chave .$ deve ser texto: %r" % (expression,))

    if expression.startswith("States."):
        return _intrinsic(expression, data, context)

    return resolve(expression, data, context)


def _intrinsic(expression, data, context):
    if not expression.startswith("States.Array(") or not expression.endswith(")"):
        raise Unsupported("Funcao intrinseca nao suportada: %s" % expression)

    arguments = expression[len("States.Array("):-1]

    return [
        _resolve_expression(argument.strip(), data, context)
        for argument in arguments.split(",")
        if argument.strip()
    ]


def resolve(path, data, context, missing=None):
    """JSONPath simples: $, $.a.b e $$.State.RetryCount."""
    if not isinstance(path, str) or not path.startswith("$"):
        raise Unsupported("Caminho JSONPath nao suportado: %r" % (path,))

    if path.startswith("$$"):
        current = context
        rest = path[2:]
    else:
        current = data
        rest = path[1:]

    if rest in ("", "."):
        return current

    for part in rest.lstrip(".").split("."):
        if not isinstance(current, dict) or part not in current:
            if missing is not None:
                return missing

            raise TaskFailure("States.Runtime", "Caminho inexistente na entrada: %s" % path)

        current = current[part]

    return current


def place(data, result_path, result):
    """Aplica ResultPath.

    Tres comportamentos, os mesmos do Step Functions:

        ausente ou "$"   o resultado substitui a entrada
        null             o resultado e descartado, a entrada segue intacta
        "$.campo"        o resultado e enxertado nesse campo de uma copia

    O sentinela ABSENT existe porque `state.get("ResultPath")` devolveria None
    tanto para o campo ausente quanto para `ResultPath: null`, que tem
    comportamentos opostos.
    """
    if result_path is ABSENT or result_path == "$":
        return result

    if result_path is None:
        return data

    if not result_path.startswith("$."):
        raise Unsupported("ResultPath nao suportado: %r" % (result_path,))

    merged = json.loads(json.dumps(data)) if isinstance(data, (dict, list)) else data
    current = merged

    parts = result_path[2:].split(".")

    for part in parts[:-1]:
        current = current.setdefault(part, {})

    current[parts[-1]] = result

    return merged
