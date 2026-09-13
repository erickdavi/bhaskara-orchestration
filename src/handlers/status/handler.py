"""GET /flow — o estado do fluxo, numa resposta so, para o painel.

O painel precisa responder a tres perguntas ao mesmo tempo: quanto trabalho ha
na fila, por onde as execucoes estao passando e o que ja terminou. Sao tres
fontes diferentes, e esta funcao as junta.

## De onde vem cada numero

    filas          GetQueueAttributes na orders e na dead-letter
    fluxo          FilterLogEvents no log group da state machine
    resultados     Query no indice by_batch da tabela
    recusas        ReceiveMessage na dead-letter, sem apagar nada

A escolha menos obvia e o **fluxo vir do log**, e nao de ListExecutions +
GetExecutionHistory. Para saber quantas execucoes passaram por cada estado
seria preciso pedir o historico de cada execucao — uma chamada por execucao, a
cada poll de 2 segundos. O log group da state machine ja tem todos os eventos
de todas as execucoes, e sai em uma chamada paginada. O historico continua
disponivel para o detalhe de UMA execucao, quando o operador clica nela.

## O agregador e uma funcao pura

`aggregate()` recebe eventos ja parseados e nao conhece a AWS. E o que permite
ao servidor local (local/server.py) alimentar o mesmo painel com os eventos do
interpretador: a demonstracao offline usa o mesmo agregador que a nuvem, em vez
de uma segunda implementacao que se parece com ela.
"""

import json
import os
import time

from api_auth import authorized
from observability import WARN, invocation

SERVICE = "status"

API_KEY = os.environ.get("API_KEY", "")

ORDERS_QUEUE_URL = os.environ.get("ORDERS_QUEUE_URL", "")
DEAD_LETTER_QUEUE_URL = os.environ.get("DEAD_LETTER_QUEUE_URL", "")
RESULTS_TABLE = os.environ.get("RESULTS_TABLE", "")
STATE_MACHINE_ARN = os.environ.get("STATE_MACHINE_ARN", "")
STATE_MACHINE_LOG_GROUP = os.environ.get("STATE_MACHINE_LOG_GROUP", "")

# Janela padrao de eventos quando o painel ainda nao tem cursor. Dois minutos
# cobrem a carga recem-disparada sem varrer o log inteiro no primeiro poll.
DEFAULT_WINDOW_MS = 120000

# Tetos de resposta. O painel faz poll a cada 2 s; uma resposta grande demais
# custa mais em transferencia do que informa.
MAX_EVENTS = 3000
MAX_RESULTS = 50
MAX_DEAD_LETTER = 10
MAX_TIMELINE = 12

_sqs = None
_dynamodb = None
_logs = None
_stepfunctions = None


def lambda_handler(event, context):
    log = invocation(SERVICE, event, context)

    if not authorized(event, API_KEY):
        log("request_unauthorized", level=WARN)
        return response(403, {"error": "Chave de API ausente ou invalida."})

    params = event.get("queryStringParameters") or {}
    now = int(time.time() * 1000)

    since = integer(params.get("since")) or now - DEFAULT_WINDOW_MS

    body = {
        "checked_at": now,
        "cursor": now,
        "queues": queues(),
        "flow": aggregate(fetch_events(since)),
        "results": results(params.get("batch_id"), integer(params.get("results")) or MAX_RESULTS),
        "dead_letter": dead_letter(integer(params.get("dlq")) or MAX_DEAD_LETTER),
    }

    if params.get("execution"):
        # Detalhe de uma execucao so: aqui vale a pena o historico oficial, que
        # traz entrada e saida de cada estado sem depender do log.
        body["execution"] = history(params["execution"])

    # O painel faz poll de 2 em 2 segundos, entao esta linha e a mais frequente
    # do sistema. Ela carrega so contadores — o corpo inteiro da resposta em log
    # dobraria a ingestao para nao dizer nada que o painel ja nao mostre.
    log(
        "flow_reported",
        window_ms=now - since,
        executions=len((body["flow"] or {}).get("executions") or []),
        results=len(body["results"] or []),
        dead_letter=len(body["dead_letter"] or []),
        measures=[],
    )

    return response(200, body)


# ------------------------------------------------------------------- fluxo


def aggregate(events):
    """Transforma eventos do Step Functions em contadores e linhas do tempo.

    Funcao pura: recebe dicionarios ja parseados, no formato que o servico grava
    no CloudWatch, e nao chama nada. Ver o cabecalho do modulo.
    """
    states = {}
    executions = {}

    for item in sorted(events, key=lambda e: (e.get("timestamp") or 0, int(e.get("id") or 0))):
        kind = item.get("type") or ""
        details = item.get("details") or {}
        name = details.get("name")
        arn = item.get("execution_arn") or ""

        execution = executions.setdefault(
            arn,
            {"arn": arn, "name": arn.rsplit(":", 1)[-1], "status": "RUNNING", "steps": [], "started_at": None},
        )

        if kind == "ExecutionStarted":
            execution["started_at"] = item.get("timestamp")
            continue

        if kind in ("ExecutionSucceeded", "ExecutionFailed", "ExecutionAborted", "ExecutionTimedOut"):
            execution["status"] = kind.replace("Execution", "").upper()
            execution["stopped_at"] = item.get("timestamp")
            continue

        if kind.endswith("StateEntered") and name:
            counters(states, name)["entered"] += 1
            execution["steps"].append({"state": name, "at": item.get("timestamp"), "outcome": "entered"})
            continue

        if kind.endswith("StateExited") and name:
            counters(states, name)["exited"] += 1
            step = last_step(execution, name)

            if step and step["outcome"] == "entered":
                step["outcome"] = "ok"
                step["ms"] = (item.get("timestamp") or 0) - step["at"]
                step["detail"] = summarize(details.get("output"))
            continue

        if kind.endswith("Failed") and not kind.startswith("Execution"):
            # TaskFailed nao traz o nome do estado; o estado e o ultimo em que
            # a execucao entrou e ainda nao saiu.
            step = pending_step(execution)

            if step:
                counters(states, step["state"])["failed"] += 1
                step["outcome"] = "failed"
                step["error"] = details.get("error")
                step["detail"] = truncate(details.get("cause"), 140)

    return {
        "states": states,
        "executions": ranked(executions),
        "counts": counts(executions),
    }


def counters(states, name):
    return states.setdefault(name, {"entered": 0, "exited": 0, "failed": 0})


def last_step(execution, name):
    for step in reversed(execution["steps"]):
        if step["state"] == name:
            return step

    return None


def pending_step(execution):
    for step in reversed(execution["steps"]):
        if step["outcome"] == "entered":
            return step

    return None


def counts(executions):
    tallied = {"RUNNING": 0, "SUCCEEDED": 0, "FAILED": 0}

    for execution in executions.values():
        tallied[execution["status"]] = tallied.get(execution["status"], 0) + 1

    return tallied


def ranked(executions):
    """As execucoes mais recentes primeiro, com a linha do tempo de cada uma."""
    ordered = sorted(
        executions.values(),
        key=lambda execution: execution.get("started_at") or 0,
        reverse=True,
    )

    return ordered[:MAX_TIMELINE]


def summarize(output):
    """Extrai do payload de saida o pedaco que interessa a quem olha o painel.

    A ordem das checagens e o que faz a timeline ser legivel. Como os estados
    usam ResultPath (que enxerta em vez de substituir), a saida de um estado
    tardio contem tambem tudo o que os anteriores produziram: o payload do
    Persist ainda tem o delta la dentro. Procurar do mais recente para o mais
    antigo faz cada linha mostrar o que **aquele** estado acrescentou.
    """
    if not output:
        return None

    try:
        payload = json.loads(output) if isinstance(output, str) else output
    except ValueError:
        return None

    if not isinstance(payload, dict):
        return None

    persisted = payload.get("persisted")

    if isinstance(persisted, dict):
        return "ja estava gravada" if persisted.get("duplicate") else "gravada"

    result = payload.get("result")

    if isinstance(result, dict):
        if result.get("roots"):
            return "  ".join(
                "%s=%s" % (root.get("label"), number(root.get("value"))) for root in result["roots"]
            )

        return "sem raizes reais"

    delta = payload.get("delta")

    if isinstance(delta, dict) and "value" in delta:
        return "delta = %s" % number(delta["value"])

    validated = payload.get("validated")

    if isinstance(validated, dict):
        return "a=%s b=%s c=%s" % (number(validated["a"]), number(validated["b"]), number(validated["c"]))

    return None


def number(value):
    if value is None:
        return "?"

    try:
        value = float(value)
    except (TypeError, ValueError):
        return str(value)

    return int(value) if value.is_integer() and abs(value) < 2 ** 53 else round(value, 4)


def truncate(text, size):
    if not text:
        return None

    return text if len(text) <= size else text[: size - 3] + "..."


# ------------------------------------------------------------------ fontes


def fetch_events(since):
    """Le os eventos da state machine no CloudWatch, do cursor para ca."""
    if not STATE_MACHINE_LOG_GROUP:
        return []

    events = []
    token = None

    while len(events) < MAX_EVENTS:
        request = {
            "logGroupName": STATE_MACHINE_LOG_GROUP,
            "startTime": since,
            "limit": 1000,
        }

        if token:
            request["nextToken"] = token

        page = logs().filter_log_events(**request)

        for entry in page.get("events") or []:
            parsed = parse_event(entry)

            if parsed:
                events.append(parsed)

        token = page.get("nextToken")

        if not token:
            break

    return events


def parse_event(entry):
    try:
        payload = json.loads(entry.get("message") or "")
    except ValueError:
        return None

    if not isinstance(payload, dict) or "type" not in payload:
        return None

    return {
        "id": payload.get("id"),
        "type": payload["type"],
        "execution_arn": payload.get("execution_arn"),
        "timestamp": integer(payload.get("event_timestamp")) or entry.get("timestamp"),
        "details": payload.get("details") or {},
    }


def queues():
    return {
        "orders": queue_depth(ORDERS_QUEUE_URL),
        "dead_letter": queue_depth(DEAD_LETTER_QUEUE_URL),
    }


def queue_depth(url):
    if not url:
        return {"visible": 0, "in_flight": 0}

    attributes = sqs().get_queue_attributes(
        QueueUrl=url,
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    ).get("Attributes", {})

    return {
        "visible": int(attributes.get("ApproximateNumberOfMessages", 0)),
        "in_flight": int(attributes.get("ApproximateNumberOfMessagesNotVisible", 0)),
    }


def results(batch_id, limit):
    """Os resultados da carga, do indice by_batch — nunca um Scan."""
    if not RESULTS_TABLE or not batch_id:
        return []

    page = dynamodb().query(
        TableName=RESULTS_TABLE,
        IndexName="by_batch",
        KeyConditionExpression="batch_id = :batch_id",
        ExpressionAttributeValues={":batch_id": {"S": batch_id}},
        Limit=min(limit, MAX_RESULTS),
        ScanIndexForward=False,
    )

    return [readable(item) for item in page.get("Items") or []]


def readable(item):
    return {
        "a": number(item["a"]["S"]),
        "b": number(item["b"]["S"]),
        "c": number(item["c"]["S"]),
        "delta": number(item["delta"]["S"]),
        "sign": item["sign"]["S"],
        "roots": [number(root["S"]) for root in item["roots"]["L"]],
        "created_at": int(item["created_at"]["N"]),
    }


def dead_letter(limit):
    """Espia a fila sem consumir.

    A role do status nao tem DeleteMessage — de proposito. Um painel que
    esvaziasse a dead-letter ao ser aberto destruiria a evidencia que ele
    deveria mostrar.
    """
    if not DEAD_LETTER_QUEUE_URL:
        return []

    received = sqs().receive_message(
        QueueUrl=DEAD_LETTER_QUEUE_URL,
        MaxNumberOfMessages=min(limit, MAX_DEAD_LETTER),
        VisibilityTimeout=0,
        WaitTimeSeconds=0,
        MessageAttributeNames=["All"],
    )

    return [rejected(message) for message in received.get("Messages") or []]


def rejected(message):
    body = message.get("body") or message.get("Body") or ""

    try:
        payload = json.loads(body)
    except ValueError:
        payload = {}

    error = payload.get("error") or {}

    return {
        "source": payload.get("source", "redrive"),
        "equation": payload.get("equation") or (payload.get("meta") or {}).get("raw_body") or body[:120],
        "error": error.get("Error"),
        "cause": truncate(error.get("Cause"), 160),
    }


def history(execution_arn):
    """Historico oficial de uma execucao, para o detalhe sob demanda."""
    page = stepfunctions().get_execution_history(
        executionArn=execution_arn, maxResults=200, includeExecutionData=True
    )

    steps = []

    for entry in page.get("events") or []:
        details = (
            entry.get("stateEnteredEventDetails")
            or entry.get("stateExitedEventDetails")
            or entry.get("taskFailedEventDetails")
            or {}
        )

        steps.append(
            {
                "type": entry.get("type"),
                "state": details.get("name"),
                "at": timestamp_ms(entry.get("timestamp")),
                "error": details.get("error"),
            }
        )

    return {"arn": execution_arn, "steps": steps}


def timestamp_ms(value):
    if value is None:
        return None

    return int(value.timestamp() * 1000) if hasattr(value, "timestamp") else int(value)


def integer(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ clientes


def sqs():
    global _sqs

    if _sqs is None:
        import boto3

        _sqs = boto3.client("sqs")

    return _sqs


def dynamodb():
    global _dynamodb

    if _dynamodb is None:
        import boto3

        _dynamodb = boto3.client("dynamodb")

    return _dynamodb


def logs():
    global _logs

    if _logs is None:
        import boto3

        _logs = boto3.client("logs")

    return _logs


def stepfunctions():
    global _stepfunctions

    if _stepfunctions is None:
        import boto3

        _stepfunctions = boto3.client("stepfunctions")

    return _stepfunctions


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
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
    dynamodb()
    logs()
    stepfunctions()
