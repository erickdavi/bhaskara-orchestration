"""A fronteira: onde o evento vira execucao.

    SQS orders --> dispatcher --> StartExecution

Ate aqui o sistema e coreografia — quem publica nao sabe quem consome. Daqui
para frente e orquestracao — a ordem esta declarada em YAML. Esta funcao e a
unica que conhece os dois lados, e o seu trabalho e so traduzir.

## E aqui que mora a camada 1 da idempotencia

O nome da execucao e derivado do conteudo da mensagem, e o Step Functions
Standard **recusa** nome repetido nos ultimos 90 dias com
`ExecutionAlreadyExists`. Numa fila standard, entregue ao menos uma vez, isso
resolve a reentrega sem lookup nenhum: a segunda tentativa e recusada pelo
servico, o dispatcher confirma a mensagem e segue.

Recusa nao e falha. Devolver a mensagem para retry porque "ja existe" faria a
SQS reentregar tres vezes uma mensagem que ja foi processada, e terminar na DLQ
uma equacao que deu certo.

## Mensagem invalida tambem vira execucao

Um corpo que nao e JSON valido poderia ser recusado aqui mesmo. Nao e: ele vira
execucao com `meta.raw_body`, e o estado Validate o recusa la dentro. Assim
toda mensagem aparece no fluxo — o painel mostra a recusa acontecendo, e a
contagem fecha. Recusar aqui produziria mensagens que somem sem deixar rastro
no diagrama.
"""

import json
import os
import time

from idempotency import execution_name, key
from metrics import counter
from observability import ERROR, WARN, invocation

STATE_MACHINE_ARN = os.environ.get("STATE_MACHINE_ARN", "")

SERVICE = "dispatcher"

ALREADY_EXISTS = "ExecutionAlreadyExists"

_stepfunctions = None


def lambda_handler(event, context):
    log = invocation(SERVICE, event, context)

    records = event.get("Records") or []

    log("batch_received", batch_size=len(records))

    failures = []
    started = duplicates = 0

    for record in records:
        message_id = record.get("messageId")

        try:
            if dispatch(record, log):
                started += 1
            else:
                duplicates += 1
        except Exception as error:  # noqa: BLE001 - ver docstring do modulo
            # Erro inesperado: devolver o messageId faz a SQS reentregar apenas
            # esta mensagem. As demais do lote ja foram confirmadas — e para
            # isso que serve o ReportBatchItemFailures.
            log(
                "dispatch_failed",
                level=ERROR,
                message_id=message_id,
                error_type=type(error).__name__,
                error=str(error),
            )
            failures.append({"itemIdentifier": message_id})

    # As duas metricas saem sempre, inclusive valendo zero: um lote sem
    # duplicata e um dado, e a serie com buraco nao distingue "nenhuma" de
    # "ninguem mediu".
    log(
        "batch_dispatched",
        measures=[
            counter("ExecutionsStarted", started),
            counter("ExecutionsDeduplicated", duplicates),
        ],
        started=started,
        duplicates=duplicates,
        failed=len(failures),
    )

    return {"batchItemFailures": failures}


def dispatch(record, log):
    """Inicia a execucao. Devolve False se ela ja existia."""
    body = record.get("body")
    batch_id = attribute(record, "BatchId") or record.get("messageId") or "adhoc"

    idempotency_key = key(batch_id, body)
    name = execution_name(idempotency_key)

    payload = {
        "equation": parse(body),
        "meta": {
            "batch_id": batch_id,
            "message_id": record.get("messageId"),
            "idempotency_key": idempotency_key,
            "submitted_at": int(time.time() * 1000),
            "receive_count": receive_count(record),
        },
    }

    if payload["equation"] is None:
        # O corpo nao era JSON: segue como texto cru para o Validate recusar
        # dentro do fluxo, com motivo, em vez de sumir aqui.
        payload["meta"]["raw_body"] = body

    chaos = attribute(record, "Chaos")

    if chaos:
        try:
            payload["meta"]["chaos"] = json.loads(chaos)
        except ValueError:
            log("chaos_attribute_ignored", level=WARN, message_id=record.get("messageId"))

    try:
        stepfunctions().start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            name=name,
            input=json.dumps(payload, ensure_ascii=False, allow_nan=False),
        )
    except Exception as error:  # noqa: BLE001 - distingue recusa de falha
        if error_code(error) != ALREADY_EXISTS:
            raise

        # `execution` carrega a chave de idempotencia, e nao o nome da execucao,
        # para casar com o que os estados do fluxo gravam: e esse campo que liga
        # esta linha as ~7 linhas seguintes da mesma equacao.
        log(
            "execution_deduplicated",
            message_id=record.get("messageId"),
            execution=idempotency_key,
            execution_name=name,
            batch_id=batch_id,
        )

        return False

    log(
        "execution_started",
        message_id=record.get("messageId"),
        execution=idempotency_key,
        execution_name=name,
        batch_id=batch_id,
    )

    return True


def parse(body):
    """Devolve o objeto da equacao, ou None se o corpo nao for JSON de objeto."""
    if not isinstance(body, str):
        return None

    try:
        parsed = json.loads(body)
    except ValueError:
        return None

    return parsed if isinstance(parsed, dict) else None


def attribute(record, name):
    attributes = record.get("messageAttributes") or {}
    attribute = attributes.get(name) or {}

    # O evento da Lambda usa stringValue; a resposta do proprio boto3 usa
    # StringValue. Aceitar os dois deixa o mesmo codigo servir ao event source
    # mapping e a um receive_message manual.
    return attribute.get("stringValue") or attribute.get("StringValue")


def receive_count(record):
    raw = (record.get("attributes") or {}).get("ApproximateReceiveCount")

    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def error_code(error):
    response = getattr(error, "response", None) or {}

    return (response.get("Error") or {}).get("Code")


def stepfunctions():
    global _stepfunctions

    if _stepfunctions is None:
        import boto3

        _stepfunctions = boto3.client("stepfunctions")

    return _stepfunctions


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
    stepfunctions()
