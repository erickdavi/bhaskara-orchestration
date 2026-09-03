"""Liga a definicao da state machine aos handlers reais, em processo.

Na nuvem, o campo `Resource` de cada Task e o ARN de uma funcao Lambda, e o
Terraform substitui os placeholders `${...}` do YAML no apply. Localmente os
placeholders permanecem como estao e este modulo os resolve para o
`lambda_handler` correspondente.

Consequencia deliberada: o YAML nao tem uma versao "de teste". O mesmo arquivo,
com o mesmo texto, alimenta os dois caminhos — e `tests/test_asl_definition.py`
falha se aparecer no YAML um placeholder que ninguem aqui sabe resolver.
"""

import os

import yaml

from local.engine import Engine

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFINITION_PATH = os.path.join(ROOT, "workflow", "bhaskara.asl.yaml")

# Integracao direta com a SQS, usada pelo caminho de dead-letter. Nao e uma
# Lambda: o Step Functions publica na fila sozinho.
SQS_SEND_MESSAGE = "arn:aws:states:::sqs:sendMessage"


class Context:
    """O minimo do context object da Lambda que os handlers consultam."""

    def __init__(self, request_id="local-request"):
        self.aws_request_id = request_id
        self.function_name = "local"

    def get_remaining_time_in_millis(self):
        return 30000


def load_definition(path=DEFINITION_PATH):
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def placeholders(definition):
    """Todos os ${...} usados como Resource na definicao."""
    found = set()

    def walk(states):
        for state in states.values():
            resource = state.get("Resource")

            if isinstance(resource, str) and resource.startswith("${"):
                found.add(resource)

            for branch in state.get("Branches") or []:
                walk(branch["States"])

    walk(definition["States"])

    return found


def build_resources(sqs=None):
    """Mapeia cada Resource da definicao para um chamavel.

    `sqs` e o duble de fila usado pela integracao direta do dead-letter; fica
    opcional porque os primeiros ciclos do fluxo nao a usam.
    """
    context = Context()

    def lambda_call(module):
        return lambda payload: module.lambda_handler(payload, context)

    from src.handlers.delta import handler as delta
    from src.handlers.validate import handler as validate

    resources = {
        "${validate_arn}": lambda_call(validate),
        "${delta_arn}": lambda_call(delta),
    }

    try:
        from src.handlers.root import handler as root
        from src.handlers.persist import handler as persist
    except ImportError:
        pass
    else:
        resources["${root_arn}"] = lambda_call(root)
        resources["${persist_arn}"] = lambda_call(persist)

    if sqs is not None:
        resources[SQS_SEND_MESSAGE] = sqs.send_from_workflow

    return resources


def build_engine(sqs=None, definition=None, sleeper=None):
    definition = definition or load_definition()

    return Engine(definition, build_resources(sqs), sleeper=sleeper)
