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

# Placeholder do YAML -> diretorio do handler em src/handlers/.
HANDLERS = {
    "${validate_arn}": "validate",
    "${delta_arn}": "delta",
    "${root_arn}": "root",
    "${persist_arn}": "persist",
}


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

    resources = {}

    # Um import por vez, e nao um bloco unico: os ciclos seguintes acrescentam
    # handlers, e um ImportError coletivo esconderia qual deles faltou.
    for placeholder, module_name in HANDLERS.items():
        try:
            module = __import__("src.handlers.%s.handler" % module_name, fromlist=["handler"])
        except ImportError:
            continue

        resources[placeholder] = lambda_call(module)

    if sqs is not None:
        resources[SQS_SEND_MESSAGE] = sqs.send_from_workflow

    return resources


def build_engine(sqs=None, definition=None, sleeper=None):
    definition = definition or load_definition()

    return Engine(definition, build_resources(sqs), sleeper=sleeper)


# Nomes de recurso usados pelos dubles. Na nuvem eles vem de variavel de
# ambiente; aqui precisam existir e ser estaveis, nada mais.
LOCAL_TABLE = "bhaskara-orchestration-local-results"
LOCAL_DEAD_LETTER_URL = "local://dead-letter"
LOCAL_ORDERS_URL = "local://orders"


def bind_doubles(setter=setattr, sqs=None, dynamodb=None):
    """Liga os handlers a dubles em memoria, no lugar dos clientes boto3.

    `setter` existe para que o pytest possa passar `monkeypatch.setattr` e ter
    a restauracao automatica no fim do teste, enquanto o simulador passa o
    `setattr` normal. O mesmo caminho de ligacao serve aos dois — um duble
    ligado de dois jeitos diferentes seria a maneira mais facil de ter suite
    verde e demonstracao quebrada.

    Devolve os dubles para que quem chamou possa inspeciona-los.
    """
    from local.doubles import SQS, DynamoDB

    sqs = sqs if sqs is not None else SQS()
    dynamodb = dynamodb if dynamodb is not None else DynamoDB()

    from src.handlers.persist import handler as persist

    setter(persist, "_dynamodb", dynamodb)
    setter(persist, "TABLE_NAME", LOCAL_TABLE)

    for module_name, attribute, value in (
        ("dispatcher", "_sqs", sqs),
        ("submit", "_sqs", sqs),
        ("status", "_sqs", sqs),
    ):
        try:
            module = __import__("src.handlers.%s.handler" % module_name, fromlist=["handler"])
        except ImportError:
            continue

        setter(module, attribute, sqs)

    return {"sqs": sqs, "dynamodb": dynamodb}
