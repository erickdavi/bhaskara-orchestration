"""O envelope canonico de log, num lugar so.

Ate o Checkpoint 3 cada handler carregava a sua propria funcao `log()`:

    def log(**fields):
        print(json.dumps(fields, ensure_ascii=False, allow_nan=False))

Seis copias identicas, e cada chamada escolhendo os seus campos. O resultado
era log estruturado — ja consultavel por campo no Logs Insights — mas sem duas
coisas que o Checkpoint 4 exige.

**Nivel.** Sem `level`, uma equacao recusada e uma equacao resolvida sao a mesma
linha para quem filtra. Nao da para alarmar sobre erro, nem para pedir a
plataforma que descarte o que nao interessa antes de cobrar a ingestao.

**Correlacao consistente.** O `validate` gravava `execution`, o `dispatcher`
gravava `batch`, o `delta` nao gravava nem um nem outro. Reconstituir o caminho
de uma equacao pelos ~7 estados exigia saber de cor qual campo cada funcao usa.

Aqui as duas coisas passam a ser automaticas: quem chama diz o que aconteceu, e
o envelope vem junto.

## O que toda linha carrega

    event         o que aconteceu — a chave de agrupamento de toda query
    level         INFO, WARN ou ERROR
    service       qual das sete funcoes emitiu
    state         em qual estado da state machine ela rodava
    execution     a chave de idempotencia: o correlation id da equacao
    batch_id      a carga a que a equacao pertence
    request_id    a invocacao da Lambda
    attempt       $$.State.RetryCount: 0 na primeira tentativa
    cold_start    esta invocacao pagou inicializacao
    duration_ms   milissegundos desde o inicio da invocacao

Os quatro campos de correlacao **ja trafegavam no payload** antes deste modulo:
a ASL passa `state` e `retry_count` para toda Task, e o `meta` carrega
`idempotency_key` e `batch_id` desde o dispatcher. Nada mudou no contrato entre
estados — o que mudou e que agora eles sao registrados.

## Por que print, e nao logging

O runtime da Lambda prefixa as linhas do modulo `logging` com nivel, timestamp
e requestId em texto, o que quebraria o JSON puro que o Logs Insights consulta
por campo. Existe o formato JSON nativo do runtime (`log_format = "JSON"`), que
resolveria isso e ainda permitiria filtrar por nivel antes da ingestao — mas
ele embrulha o stdout dentro de um campo `message`, e o EMF do proximo ciclo
precisa do `_aws` na raiz da linha. A escolha entre os dois esta medida em
docs/cycle-08.md.
"""

import json
import time

INFO = "INFO"
WARN = "WARN"
ERROR = "ERROR"

# A ordem em que os campos aparecem na linha. Nao e estetica: uma linha de log
# e lida por humano antes de ser lida por query, e o que identifica o evento
# tem de vir antes do que o descreve.
ENVELOPE = (
    "event",
    "level",
    "service",
    "state",
    "execution",
    "batch_id",
    "request_id",
    "attempt",
    "cold_start",
    "duration_ms",
)

# True ate a primeira invocacao deste processo. Na Lambda, um processo novo e
# exatamente um ambiente de execucao novo — que e o que "cold start" significa.
_cold = True


def invocation(service, event=None, context=None):
    """Abre a invocacao e devolve a funcao que emite as linhas dela.

        log = invocation("validate", event, context)
        log("equation_validated", **validated)
        log("equation_rejected", level=ERROR, reason=str(error))

    O emissor guarda o instante de abertura, entao `duration_ms` sai de graca em
    toda linha — e a ultima linha de um handler e, na pratica, a duracao dele.
    """
    global _cold

    started = time.perf_counter()
    cold = _cold
    _cold = False

    base = correlation(event)
    base["service"] = service
    base["request_id"] = getattr(context, "aws_request_id", None)
    base["cold_start"] = cold

    def log(event_name, level=INFO, **fields):
        line = dict(base)
        line["event"] = event_name
        line["level"] = level
        line["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)

        # Quem chama pode sobrescrever um campo do envelope — o dispatcher sabe
        # a `execution` e o `batch_id` de cada mensagem do lote, que o evento da
        # SQS nao carrega no formato do fluxo.
        line.update(fields)

        emit(line)

    return log


def correlation(event):
    """Extrai do payload os campos que ligam uma linha as suas irmas.

    Tolerante de proposito: o mesmo modulo serve as Tasks (que recebem `meta`,
    `state` e `retry_count`), ao dispatcher (que recebe `Records` da SQS) e aos
    dois handlers de borda (que recebem um evento de API Gateway). Onde o campo
    nao existe, ele simplesmente nao aparece na linha.
    """
    if not isinstance(event, dict):
        return {}

    meta = event.get("meta")
    meta = meta if isinstance(meta, dict) else {}

    return {
        "state": event.get("state"),
        "execution": meta.get("idempotency_key"),
        "batch_id": meta.get("batch_id"),
        "attempt": event.get("retry_count"),
    }


def emit(line):
    """Escreve uma linha, na ordem do envelope e sem os campos vazios."""
    ordered = {name: line[name] for name in ENVELOPE if line.get(name) is not None}

    # cold_start=False cai fora do filtro acima por ser falsy, e a ausencia do
    # campo seria lida como "nao se sabe". Ele volta explicitamente.
    ordered["cold_start"] = bool(line.get("cold_start"))

    # Os campos do evento entram depois, na ordem em que quem chamou os passou.
    # O `name not in ENVELOPE` e o que impede um campo de envelope vazio de
    # voltar aqui como null: ele foi filtrado acima justamente por nao existir.
    for name, value in line.items():
        if name not in ordered and name not in ENVELOPE:
            ordered[name] = value

    # default=str: telemetria nao pode derrubar regra de negocio. Um Decimal
    # vindo do DynamoDB faria o json.dumps levantar TypeError e o handler
    # falharia ao **registrar** que deu certo. allow_nan=False continua: NaN e
    # Infinity nao sao JSON valido e o Logs Insights nao os parseia.
    print(json.dumps(ordered, ensure_ascii=False, allow_nan=False, default=str))


def reset():
    """Volta a marcar a proxima invocacao como fria.

    Existe para a suite: o pytest roda tudo em um processo, entao sem isto
    apenas o primeiro teste do dia veria `cold_start: true`. Nao e chamado por
    nenhum handler.
    """
    global _cold

    _cold = True
