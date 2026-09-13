"""Borda do sistema: uma requisicao HTTP vira N mensagens na fila orders.

    POST /orders   {"quantity": 50, "duplicate_ratio": 0.1, "chaos_ratio": 0.1}

Esta funcao **nao** inicia execucoes. Ela publica na fila, e quem transforma
mensagem em execucao e o dispatcher, do outro lado. A diferenca importa: o
enfileiramento absorve o pico, e o Step Functions recebe trabalho no ritmo em
que a conta aguenta — com 10 execucoes concorrentes no total, disparar 2.000
StartExecution de dentro de uma requisicao HTTP seria a maneira mais rapida de
derrubar a demonstracao.

Tres restricoes moldam a implementacao, as mesmas do Checkpoint 2:

    SendMessageBatch   maximo de 10 mensagens por chamada
    API Gateway        corta a integracao em 30 s, sem excecao
    concorrencia       uma invocacao por requisicao, nao uma por mensagem

Dai o guarda de tempo: antes de cada lote a funcao consulta quanto lhe resta e,
se estiver perto do fim, para e responde com o que conseguiu publicar. Uma
resposta honesta de "publiquei 700" e melhor que um timeout, que deixaria o
cliente sem saber quantas mensagens foram parar na fila.
"""

import json
import os
import time
import uuid

from api_auth import authorized
from generator import generate
from metrics import counter
from observability import ERROR, WARN, invocation

ORDERS_QUEUE_URL = os.environ.get("ORDERS_QUEUE_URL", "")

API_KEY = os.environ.get("API_KEY", "")

# Teto menor que o do Checkpoint 2 (5.000): la cada mensagem era uma invocacao
# barata de Lambda; aqui cada mensagem vira uma execucao de state machine, e a
# transicao de estado e a unidade de custo. 2.000 equacoes sao ~18.000
# transicoes — o suficiente para uma carga grande e longe de um susto na fatura.
MAX_QUANTITY = int(os.environ.get("MAX_QUANTITY", "2000"))

SERVICE = "submit"

BATCH_SIZE = 10

TIME_SAFETY_MARGIN_MS = 3000

_sqs = None


class InvalidRequest(Exception):
    """Erro do cliente: vira 400, nao 500."""


def lambda_handler(event, context):
    log = invocation(SERVICE, event, context)

    started = time.monotonic()

    if not authorized(event, API_KEY):
        # WARN, e nao ERROR: uma chamada sem chave e o sistema funcionando como
        # projetado. Mas precisa de nivel proprio — e o que o metric filter do
        # access log conta para mostrar varredura automatizada no dashboard.
        log("request_unauthorized", level=WARN)
        return response(403, {"error": "Chave de API ausente ou invalida."})

    try:
        options = parse_request(event)
    except InvalidRequest as error:
        log("request_rejected", level=WARN, reason=str(error))
        return response(400, {"error": str(error)})

    # O batch_id entra na chave de idempotencia de cada mensagem. E o que faz a
    # deduplicacao valer dentro desta carga e nao entre cargas diferentes — sem
    # ele, a segunda demonstracao do dia apareceria vazia.
    batch_id = options.pop("batch_id", None) or new_batch_id()

    log("batch_requested", batch_id=batch_id, **options)

    published, failed, batches, truncated, chaos = publish(batch_id, options, context, log)

    body = {
        "batch_id": batch_id,
        "requested": options["quantity"],
        "published": published,
        "batches": batches,
        "chaos": chaos,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }

    if failed:
        body["failed"] = failed

    if truncated:
        body["truncated"] = True
        body["detail"] = (
            "A funcao parou antes do timeout. Reenvie a diferenca em uma nova "
            "requisicao ou divida a carga em solicitacoes menores."
        )

    log("batch_published", measures=[counter("EquationsSubmitted", published)], **body)

    # 202 e nao 200: as mensagens foram aceitas para processamento, que
    # acontece depois e em outro lugar. Nenhum resultado esta nesta resposta.
    return response(202, body)


def new_batch_id():
    return "b-" + uuid.uuid4().hex[:10]


def parse_request(event):
    body = event.get("body")

    if not body:
        raise InvalidRequest('Corpo da requisicao ausente. Envie {"quantity": N}.')

    try:
        payload = json.loads(body)
    except (TypeError, json.JSONDecodeError):
        raise InvalidRequest("Corpo da requisicao nao e JSON valido.") from None

    if not isinstance(payload, dict):
        raise InvalidRequest("Corpo da requisicao deve ser um objeto JSON.")

    return {
        "quantity": quantity_of(payload),
        "invalid_ratio": ratio_of(payload, "invalid_ratio"),
        "duplicate_ratio": ratio_of(payload, "duplicate_ratio"),
        "chaos_ratio": ratio_of(payload, "chaos_ratio"),
        "seed": payload.get("seed"),
        "batch_id": payload.get("batch_id"),
    }


def quantity_of(payload):
    if "quantity" not in payload:
        raise InvalidRequest("O campo 'quantity' e obrigatorio.")

    quantity = payload["quantity"]

    # isinstance(True, int) e True: sem excluir bool, {"quantity": true}
    # publicaria uma mensagem em vez de recusar a requisicao.
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise InvalidRequest("O campo 'quantity' deve ser um numero inteiro.")

    if quantity < 1:
        raise InvalidRequest("O campo 'quantity' deve ser no minimo 1.")

    if quantity > MAX_QUANTITY:
        raise InvalidRequest("O campo 'quantity' deve ser no maximo %d." % MAX_QUANTITY)

    return quantity


def ratio_of(payload, name):
    """Proporcoes opcionais, zero por padrao.

    Gerar mensagem invalida, duplicada ou com falha injetada sem que ninguem
    tenha pedido seria surpreendente. Elas existem para alimentar a
    demonstracao quando se quer ver os caminhos de erro funcionando.
    """
    ratio = payload.get(name, 0)

    if isinstance(ratio, bool) or not isinstance(ratio, (int, float)):
        raise InvalidRequest("O campo '%s' deve ser um numero." % name)

    if not 0 <= ratio <= 1:
        raise InvalidRequest("O campo '%s' deve estar entre 0 e 1." % name)

    return float(ratio)


def publish(batch_id, options, context, log):
    """Publica em lotes de 10, parando se o tempo acabar."""
    published = failed = batches = chaos = 0
    truncated = False

    messages = generate(
        options["quantity"],
        options["invalid_ratio"],
        options["duplicate_ratio"],
        options["chaos_ratio"],
        options["seed"],
    )

    for batch in chunks(messages, BATCH_SIZE):
        if out_of_time(context):
            truncated = True
            break

        entries = []

        for index, (body, chaos_config) in enumerate(batch):
            attributes = {
                "BatchId": {"DataType": "String", "StringValue": batch_id},
            }

            if chaos_config:
                chaos += 1
                # O caos viaja como atributo, e nao dentro do corpo: o corpo e
                # a equacao, e quem publica direto na fila (scripts, console da
                # AWS) precisa poder mandar so a equacao.
                attributes["Chaos"] = {
                    "DataType": "String",
                    "StringValue": json.dumps(chaos_config),
                }

            entries.append(
                {"Id": "m%d" % index, "MessageBody": body, "MessageAttributes": attributes}
            )

        result = sqs().send_message_batch(QueueUrl=ORDERS_QUEUE_URL, Entries=entries)

        rejected = result.get("Failed") or []

        if rejected:
            # Falha parcial do lote: a SQS aceita algumas entradas e recusa
            # outras. Registrar em vez de levantar — perder 3 de 1.000 nao
            # justifica descartar as 997 ja publicadas.
            log(
                "batch_partially_rejected",
                level=ERROR,
                batch_id=batch_id,
                batch_number=batches,
                failed=len(rejected),
                first_error=rejected[0].get("Message"),
            )

        published += len(result.get("Successful") or [])
        failed += len(rejected)
        batches += 1

    return published, failed, batches, truncated, chaos


def out_of_time(context):
    if context is None:
        return False

    remaining = getattr(context, "get_remaining_time_in_millis", None)

    if remaining is None:
        return False

    return remaining() < TIME_SAFETY_MARGIN_MS


def chunks(iterable, size):
    batch = []

    for item in iterable:
        batch.append(item)

        if len(batch) == size:
            yield batch
            batch = []

    if batch:
        yield batch


def sqs():
    global _sqs

    if _sqs is None:
        import boto3

        _sqs = boto3.client("sqs")

    return _sqs


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, ensure_ascii=False, allow_nan=False),
    }


# Aquecimento na inicializacao — ver docs/cycle-12.md.
#
# A medicao de 120 equacoes mostrou o `persist` levando **5.972 ms** numa
# invocacao fria contra 13,6 ms numa quente, com `initDurationMs` de apenas
# 83 ms. Os seis segundos nao estavam na inicializacao: estavam **dentro do
# handler**, no `import boto3` e no `boto3.client()` preguicosos, que rodam na
# primeira chamada com a CPU racionada dos 128 MB.
#
# A Lambda concede CPU ampliada durante a fase de inicializacao,
# independentemente da memoria configurada. Chamar o acessor aqui move o custo
# para dentro dessa janela.
#
# O `if` e o que preserva os testes: a variavel so existe dentro da Lambda.
# Localmente e na suite o cliente continua preguicoso, e os dubles em memoria
# entram antes de qualquer boto3 ser importado.
if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    sqs()
