"""A demonstracao local: o fluxo inteiro, sem AWS, sem Docker, sem credencial.

    ./run.sh demo
    ./run.sh demo 200 --duplicates 20 --chaos 15 --seed 7

O que roda aqui e o codigo de producao inteiro — o handler `submit` gerando e
publicando, o `dispatcher` traduzindo mensagem em execucao, e a state machine
do YAML sendo interpretada estado a estado. Trocado por dubles em memoria: so o
transporte (SQS, DynamoDB e o proprio servico Step Functions).

E por isso que a saida abaixo e uma demonstracao, e nao uma animacao: os
numeros vem de contar o que realmente aconteceu.
"""

import argparse
import json
import sys

from local import runtime
from local.doubles import SQS

BRANCHES = {
    "positive": "delta > 0   duas raizes",
    "zero": "delta = 0   raiz dupla",
    "negative": "delta < 0   sem raizes reais",
}


def main(argv=None):
    options = parse_args(argv if argv is not None else sys.argv[1:])

    print(header(options))

    summary = run(options)

    report(summary)

    return 0


def run(options, stack=None):
    """Executa a carga e devolve os numeros, sem imprimir nada.

    Separado de `report` de proposito: e o que permite aos testes conferirem a
    contabilidade sem depender do texto da saida.
    """
    stack = stack if stack is not None else runtime.build_local_stack()

    published = submit(stack, options)

    drain(stack, quiet=not options.verbose)

    return summarize(stack, options, published)


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="./run.sh demo",
        description="Executa o fluxo orquestrado localmente, sem AWS.",
    )
    parser.add_argument("quantity", nargs="?", type=int, default=80, help="quantas equacoes (padrao 80)")
    parser.add_argument("--invalid", type=float, default=10, help="%% de mensagens invalidas (padrao 10)")
    parser.add_argument("--duplicates", type=float, default=10, help="%% de mensagens repetidas (padrao 10)")
    parser.add_argument("--chaos", type=float, default=10, help="%% com falha injetada (padrao 10)")
    parser.add_argument("--seed", type=int, default=None, help="torna a carga reproduzivel")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="mostra os logs estruturados das funcoes, um JSON por evento",
    )

    return parser.parse_args(argv)


def submit(stack, options):
    """Chama o handler de submit exatamente como o API Gateway chamaria."""
    from src.handlers.submit import handler as submit_handler

    event = {
        "headers": {"x-api-key": runtime.LOCAL_API_KEY},
        "body": json.dumps(
            {
                "quantity": options.quantity,
                "invalid_ratio": options.invalid / 100.0,
                "duplicate_ratio": options.duplicates / 100.0,
                "chaos_ratio": options.chaos / 100.0,
                "seed": options.seed,
            }
        ),
    }

    with silenced(not options.verbose):
        response = submit_handler.lambda_handler(event, runtime.Context())

    return json.loads(response["body"])


def drain(stack, quiet=True):
    """Entrega a fila ao dispatcher em lotes de 10, como o event source mapping."""
    from src.handlers.dispatcher import handler as dispatcher

    sqs = stack["sqs"]

    while sqs.depth(runtime.LOCAL_ORDERS_URL):
        received = sqs.receive_message(QueueUrl=runtime.LOCAL_ORDERS_URL, MaxNumberOfMessages=10)

        event = {"Records": [as_record(message) for message in received["Messages"]]}

        with silenced(quiet):
            dispatcher.lambda_handler(event, runtime.Context())


def as_record(message):
    """Converte a mensagem do duble no formato do event source mapping."""
    return {
        "messageId": message["messageId"],
        "body": message["body"],
        "attributes": message["attributes"],
        "messageAttributes": {
            name: {"stringValue": value["StringValue"], "dataType": value["DataType"]}
            for name, value in (message.get("messageAttributes") or {}).items()
        },
        "eventSource": "aws:sqs",
    }


# ------------------------------------------------------------------ relatorio


def summarize(stack, options, published):
    executions = list(stack["stepfunctions"].executions.values())
    rejected = stack["sqs"].queue(runtime.LOCAL_DEAD_LETTER_URL).messages

    succeeded = [e for e in executions if e.status == "SUCCEEDED"]
    failed = [e for e in executions if e.status == "FAILED"]

    return {
        "published": published["published"],
        "batch_id": published["batch_id"],
        "started": len(executions),
        "deduplicated": stack["stepfunctions"].rejected,
        "succeeded": len(succeeded),
        "failed": len(failed),
        "branches": {
            sign: sum(1 for e in succeeded if (e.output.get("delta") or {}).get("sign") == sign)
            for sign in BRANCHES
        },
        "stored": sum(1 for e in succeeded if not (e.output.get("persisted") or {}).get("duplicate")),
        "already_stored": sum(1 for e in succeeded if (e.output.get("persisted") or {}).get("duplicate")),
        "recovered": sum(1 for e in succeeded if any(v["type"] == "TaskFailed" for v in e.events)),
        "reasons": reasons(rejected),
        "items": len(stack["dynamodb"].items),
        "results": [readable(e) for e in succeeded if (e.output.get("result") or {}).get("roots")],
        "rejections": [rejection(m) for m in rejected],
    }


def report(summary):
    print()
    print(line("Mensagens publicadas na fila orders", summary["published"]))
    print(line("Execucoes iniciadas", summary["started"]))
    print(line("  evitadas por idempotencia (camada 1)", summary["deduplicated"]))
    print()
    print("Desfechos")
    print(line("  concluidas", summary["succeeded"]))

    for sign, rotulo in BRANCHES.items():
        print(line("    " + rotulo, summary["branches"][sign]))

    print(line("  gravadas", summary["stored"]))
    print(line("    ja estavam gravadas (camada 2)", summary["already_stored"]))
    print(line("  recuperadas pelo retry", summary["recovered"]))
    print(line("  enviadas a dead-letter", summary["failed"]))

    for motivo, total in sorted(summary["reasons"].items(), key=lambda item: -item[1]):
        print(line("    " + motivo, total))

    print("  " + "-" * 44)
    print(line("  total de mensagens", summary["started"] + summary["deduplicated"]))

    if summary["results"]:
        print()
        print("Amostra dos resultados")

        for texto in summary["results"][:3]:
            print("  " + texto)

    if summary["rejections"]:
        print()
        print("Amostra das recusas (chegaram na dead-letter com o motivo anexado)")

        for corpo, motivo in summary["rejections"][:3]:
            print("  %-34s -> %s" % (truncate(corpo, 34), motivo[:60]))


def rejection(message):
    body = json.loads(message["body"])
    equacao = body.get("equation")
    corpo = json.dumps(equacao) if equacao else (body.get("meta") or {}).get("raw_body")

    return corpo, (body.get("error") or {}).get("Cause", "")


def reasons(messages):
    counted = {}

    for message in messages:
        body = json.loads(message["body"])
        error = (body.get("error") or {}).get("Error", "desconhecido")

        rotulo = {
            "InvalidEquation": "InvalidEquation (permanente, sem retry)",
            "TransientFailure": "TransientFailure (caos esgotou o retry)",
        }.get(error, error)

        counted[rotulo] = counted.get(rotulo, 0) + 1

    return counted


def readable(execution):
    validated = execution.output["validated"]
    roots = [root["value"] for root in execution.output["result"]["roots"]]

    if len(roots) == 1:
        roots = roots * 2

    return "%-28s -> %s" % (equation_text(validated), roots_text(roots))


def equation_text(validated):
    return "%sx^2 %s %sx %s %s = 0" % (
        number(validated["a"]),
        "+" if validated["b"] >= 0 else "-",
        number(abs(validated["b"])),
        "+" if validated["c"] >= 0 else "-",
        number(abs(validated["c"])),
    )


def roots_text(roots):
    if not roots:
        return "sem raizes reais"

    return "x1=%s  x2=%s" % (number(roots[0]), number(roots[1]))


def number(value):
    return int(value) if float(value).is_integer() else round(value, 4)


def truncate(text, size):
    text = text or ""

    return text if len(text) <= size else text[: size - 3] + "..."


def line(label, value):
    return "%-46s %6s" % (label, value)


def header(options):
    return "\n".join(
        [
            "Fluxo   Validate -> Delta -> Choice(delta) -> Root(s) -> Persist",
            "Carga   %d equacoes  ·  %g%% invalidas  ·  %g%% duplicadas  ·  %g%% com caos"
            % (options.quantity, options.invalid, options.duplicates, options.chaos),
        ]
    )


class silenced:
    """Silencia os prints dos handlers, que sao logs estruturados, nao saida.

    Na nuvem essas linhas vao para o CloudWatch; aqui elas atrapalhariam a
    leitura do relatorio. `--quiet` desligado mostra tudo, que e util quando se
    quer ver o JSON de cada evento.
    """

    def __init__(self, quiet):
        self.quiet = quiet
        self.stdout = None

    def __enter__(self):
        if self.quiet:
            import io

            self.stdout = sys.stdout
            sys.stdout = io.StringIO()

    def __exit__(self, *exc):
        if self.stdout is not None:
            sys.stdout = self.stdout
            self.stdout = None

        return False


if __name__ == "__main__":
    raise SystemExit(main())
