"""O painel, servido localmente, alimentado pelo fluxo em memoria.

    ./run.sh web        ->  http://localhost:8000

Serve os mesmos arquivos de `web/` que o CloudFront serve na nuvem e responde
as mesmas duas rotas — `POST /orders` e `GET /flow` — chamando os mesmos
handlers. O que muda e so o transporte: as filas, a tabela e o proprio Step
Functions sao dubles em memoria.

Duas consequencias praticas:

  - da para ver o diagrama acendendo, a timeline preenchendo e a dead-letter
    recebendo, sem conta AWS e sem gastar um centavo;

  - o painel nao tem uma "versao local". Se ele funciona aqui, e o mesmo codigo
    que vai funcionar la — inclusive o agregador dos eventos, que e literalmente
    a mesma funcao (`status.aggregate`).

O event source mapping da SQS e imitado por uma thread que entrega um lote a
cada intervalo. O intervalo existe de proposito: sem ele a carga inteira seria
processada antes do primeiro poll do painel, e o diagrama iria de vazio a
concluido sem passar pelo meio — que e justamente o que se quer ver.
"""

import json
import os
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from local import runtime
from local.simulator import as_record, silenced

WEB_DIR = os.path.join(runtime.ROOT, "web")

# Intervalo entre lotes entregues ao dispatcher, em segundos.
BATCH_INTERVAL = 0.35

EXECUTION_ARN_PREFIX = "arn:aws:states:local:000000000000:execution:bhaskara-local"


class Stack:
    """O ambiente local, montado uma vez e compartilhado pelas requisicoes."""

    def __init__(self, verbose=False):
        self.verbose = verbose
        self.doubles = runtime.build_local_stack()
        self.lock = threading.Lock()
        self.draining = False

    @property
    def sqs(self):
        return self.doubles["sqs"]

    @property
    def dynamodb(self):
        return self.doubles["dynamodb"]

    @property
    def stepfunctions(self):
        return self.doubles["stepfunctions"]

    def submit(self, body):
        from src.handlers.submit import handler as submit

        event = {"headers": {"x-api-key": runtime.LOCAL_API_KEY}, "body": body}

        with silenced(not self.verbose):
            response = submit.lambda_handler(event, runtime.Context())

        self.start_draining()

        return response

    def start_draining(self):
        with self.lock:
            if self.draining:
                return

            self.draining = True

        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        """Imita o event source mapping: um lote de ate 10 por vez."""
        from src.handlers.dispatcher import handler as dispatcher

        try:
            while self.sqs.depth(runtime.LOCAL_ORDERS_URL):
                received = self.sqs.receive_message(
                    QueueUrl=runtime.LOCAL_ORDERS_URL, MaxNumberOfMessages=10
                )

                event = {
                    "Records": [as_record(message) for message in received["Messages"]]
                }

                with silenced(not self.verbose):
                    dispatcher.lambda_handler(event, runtime.Context())

                time.sleep(BATCH_INTERVAL)
        finally:
            with self.lock:
                self.draining = False

    # ------------------------------------------------------------ GET /flow

    def flow(self, params):
        from src.handlers.status import handler as status

        now = int(time.time() * 1000)
        since = int(params.get("since", [0])[0] or 0)
        batch_id = (params.get("batch_id") or [None])[0]

        return {
            "checked_at": now,
            "cursor": now,
            "queues": {
                "orders": {
                    "visible": self.sqs.depth(runtime.LOCAL_ORDERS_URL),
                    "in_flight": 0,
                },
                "dead_letter": {
                    "visible": self.sqs.depth(runtime.LOCAL_DEAD_LETTER_URL),
                    "in_flight": 0,
                },
            },
            # O mesmo agregador que roda na nuvem, sobre os eventos do
            # interpretador convertidos para o formato do log do servico.
            "flow": status.aggregate(self.events(since)),
            "results": self.results(batch_id),
            "dead_letter": [
                status.rejected(message)
                for message in self.sqs.queue(runtime.LOCAL_DEAD_LETTER_URL).messages[
                    -10:
                ]
            ],
        }

    def events(self, since):
        events = []

        for name, execution in list(self.stepfunctions.executions.items()):
            arn = f"{EXECUTION_ARN_PREFIX}:{name}"

            for event in execution.events:
                if event["timestamp"] < since:
                    continue

                events.append(
                    {
                        "id": event["id"],
                        "type": event["type"],
                        "execution_arn": arn,
                        "timestamp": event["timestamp"],
                        "details": {
                            key: (
                                json.dumps(value)
                                if key in ("input", "output")
                                else value
                            )
                            for key, value in event["details"].items()
                        },
                    }
                )

        return events

    def results(self, batch_id):
        from src.handlers.status import handler as status

        items = [
            item
            for item in self.dynamodb.items.values()
            if batch_id is None or item["batch_id"]["S"] == batch_id
        ]

        items.sort(key=lambda item: int(item["created_at"]["N"]), reverse=True)

        return [status.readable(item) for item in items[:50]]


class Handler(SimpleHTTPRequestHandler):
    stack = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def do_GET(self):
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(self.path)

        if parsed.path == "/flow":
            return self.json(200, self.stack.flow(parse_qs(parsed.query)))

        if parsed.path == "/config.js":
            # Na nuvem este arquivo e gerado pelo Terraform e leva apenas a URL
            # da API — a chave e digitada pelo operador. Aqui ele tambem leva a
            # chave, porque "aqui" e a memoria da propria maquina de quem roda.
            return self.script(
                "window.BHASKARA_CONFIG = {};".format(
                    json.dumps(
                        {"apiBase": "", "apiKey": runtime.LOCAL_API_KEY, "local": True}
                    )
                )
            )

        return super().do_GET()

    def do_POST(self):
        if self.path != "/orders":
            return self.json(404, {"error": "Rota inexistente."})

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8")

        response = self.stack.submit(body)

        return self.json(response["statusCode"], json.loads(response["body"]))

    def json(self, status, payload):
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def script(self, source):
        encoded = source.encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/javascript; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):  # noqa: A002 - assinatura da stdlib
        """Silencia o log de acesso: o poll de 2 s inundaria o terminal."""


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        prog="./run.sh web", description="Painel local do fluxo."
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--verbose", action="store_true", help="mostra os logs das funcoes"
    )

    options = parser.parse_args(argv)

    Handler.stack = Stack(verbose=options.verbose)

    server = ThreadingHTTPServer((options.host, options.port), Handler)

    print(f"Painel em http://{options.host}:{options.port}")
    print("Nada sai desta maquina: filas, tabela e execucoes vivem em memoria.")
    print("Ctrl+C para parar.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nate mais.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
