"""Ultimo estado do caminho feliz: grava o resultado uma unica vez.

    ... -> Persist -> Done

E aqui que mora a segunda camada de idempotencia. A primeira vive no
dispatcher, que nao inicia uma execucao com nome ja usado; esta cobre tudo o
que escapar de la — replay manual, reprocessamento de DLQ, uma execucao
iniciada por outro caminho.

O mecanismo e uma escrita condicional: `attribute_not_exists(pk)`. Se o item ja
existe, o DynamoDB recusa a escrita, o handler le o que estava la e devolve
`duplicate: true`. O estado **nao falha** — do ponto de vista do fluxo, "ja
estava gravado" e um desfecho de sucesso, e transformar isso em erro faria a
execucao inteira ir para a dead-letter por ter feito a coisa certa.

## Numeros vao para a tabela como texto

O tipo N do DynamoDB representa de 1e-130 a 1e125; um float64 vai a 1e308. Uma
equacao com coeficientes extremos produz raizes que o tipo N nao aceita, e a
escrita falharia com um erro de validacao — que viraria retry, e depois
dead-letter, por um resultado perfeitamente correto. Guardar como texto (S)
preserva o valor exato de qualquer float; nada aqui e filtrado ou ordenado por
esses campos.
"""

import json
import os
import time

STATE_NAME = "Persist"

TABLE_NAME = os.environ.get("RESULTS_TABLE", "")

# Dado de laboratorio nao fica. O TTL do DynamoDB apaga sozinho, sem job de
# limpeza e sem custo de escrita.
TTL_HOURS = int(os.environ.get("RESULTS_TTL_HOURS", "24"))

CONDITIONAL_FAILURE = "ConditionalCheckFailedException"

_dynamodb = None


def lambda_handler(event, context):
    from chaos import maybe_fail

    maybe_fail(event, STATE_NAME)

    meta = event.get("meta") or {}
    pk = meta.get("idempotency_key")

    if not pk:
        raise ValueError("A execucao chegou ao Persist sem chave de idempotencia.")

    item = build_item(pk, event, meta)

    stored = put_once(item)

    if stored:
        log(event="result_stored", execution=pk, batch=item["batch_id"]["S"])

        return {"stored": True, "duplicate": False, "key": pk, "result": readable(item)}

    existing = fetch(pk)

    log(event="result_duplicate", execution=pk, batch=item["batch_id"]["S"])

    return {"stored": False, "duplicate": True, "key": pk, "result": readable(existing or item)}


def build_item(pk, event, meta):
    validated = event.get("validated") or {}
    delta = event.get("delta") or {}
    roots = expand(((event.get("result") or {}).get("roots")) or [])

    now = int(time.time())

    item = {
        "pk": {"S": pk},
        # O GSI por batch_id e o que o painel consulta para listar os
        # resultados de uma carga sem varrer a tabela inteira.
        "batch_id": {"S": str(meta.get("batch_id") or "adhoc")},
        "created_at": {"N": str(int(time.time() * 1000))},
        "expires_at": {"N": str(now + TTL_HOURS * 3600)},
        "status": {"S": "succeeded"},
        "a": {"S": repr(validated.get("a"))},
        "b": {"S": repr(validated.get("b"))},
        "c": {"S": repr(validated.get("c"))},
        "delta": {"S": repr(delta.get("value"))},
        "sign": {"S": str(delta.get("sign"))},
        "roots": {"L": [{"S": repr(value)} for value in roots]},
    }

    if meta.get("message_id"):
        item["message_id"] = {"S": str(meta["message_id"])}

    return item


def expand(roots):
    """Achata os tres formatos de raiz em uma lista de numeros.

    O ramo do Parallel entrega [{x1}, {x2}], o RootDouble entrega [{double}] e
    o NoRealRoots entrega []. A raiz dupla vira dois valores iguais aqui, e nao
    no fluxo: assim quem le a tabela nao precisa saber que existiu um Choice.
    """
    values = []

    for root in roots:
        values.append(root["value"])

        if root.get("label") == "double":
            values.append(root["value"])

    return values


def put_once(item):
    """Grava se ainda nao existir. Devolve False se ja existia."""
    try:
        dynamodb().put_item(
            TableName=TABLE_NAME,
            Item=item,
            ConditionExpression="attribute_not_exists(pk)",
        )
    except Exception as error:  # noqa: BLE001 - ver comentario abaixo
        # Capturar ClientError por nome exigiria importar botocore aqui so para
        # comparar uma string; o codigo do erro esta no proprio objeto. Qualquer
        # outra falha sobe e vira retry, que e o comportamento correto: nao
        # gravar por indisponibilidade nao pode virar "gravou".
        if error_code(error) != CONDITIONAL_FAILURE:
            raise

        return False

    return True


def fetch(pk):
    response = dynamodb().get_item(TableName=TABLE_NAME, Key={"pk": {"S": pk}})

    return response.get("Item")


def error_code(error):
    response = getattr(error, "response", None) or {}

    return (response.get("Error") or {}).get("Code")


def readable(item):
    """Versao do item para o retorno da execucao e para o painel."""
    return {
        "a": number(item["a"]["S"]),
        "b": number(item["b"]["S"]),
        "c": number(item["c"]["S"]),
        "delta": number(item["delta"]["S"]),
        "sign": item["sign"]["S"],
        "roots": [number(value["S"]) for value in item["roots"]["L"]],
        "batch_id": item["batch_id"]["S"],
    }


def number(text):
    value = float(text)

    return int(value) if value.is_integer() and abs(value) < 2 ** 53 else value


def dynamodb():
    global _dynamodb

    if _dynamodb is None:
        import boto3

        _dynamodb = boto3.client("dynamodb")

    return _dynamodb


def log(**fields):
    print(json.dumps(fields, ensure_ascii=False, allow_nan=False))
