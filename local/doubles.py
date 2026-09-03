"""Dubles em memoria da SQS, do DynamoDB e do Step Functions.

Servem a dois usos que precisam se comportar igual: os testes (onde nenhum
cliente boto3 real pode existir) e o simulador local (onde o fluxo inteiro roda
sem AWS). Manter os dois em cima do mesmo duble evita a armadilha classica de
um teste que passa contra um mock generoso e uma demonstracao que passa contra
outro.

O que eles imitam e o **contrato que o codigo usa**, nao o servico inteiro:
as chamadas, os nomes dos parametros, o formato dos AttributeValue do DynamoDB
e os erros pelo codigo que o handler compara. Nada alem disso.
"""

import json
import threading


class ClientError(Exception):
    """Erro no formato que o botocore levanta, com `response` consultavel.

    O codigo de producao nao importa botocore para comparar uma string: ele le
    `error.response["Error"]["Code"]`. Esta classe reproduz exatamente essa
    forma, entao o caminho de tratamento exercitado no teste e o mesmo que roda
    na nuvem.
    """

    def __init__(self, code, message=""):
        super().__init__("%s: %s" % (code, message))
        self.response = {"Error": {"Code": code, "Message": message}}


class DynamoDB:
    """Tabela unica, indexada pela chave primaria `pk`."""

    def __init__(self):
        self.items = {}
        self.puts = 0
        self.conditional_failures = 0

    def put_item(self, TableName, Item, ConditionExpression=None):  # noqa: N803 - assinatura do boto3
        self.puts += 1
        pk = Item["pk"]["S"]

        if ConditionExpression == "attribute_not_exists(pk)" and pk in self.items:
            self.conditional_failures += 1
            raise ClientError(
                "ConditionalCheckFailedException",
                "The conditional request failed",
            )

        self.items[pk] = json.loads(json.dumps(Item))

        return {}

    def get_item(self, TableName, Key):  # noqa: N803
        item = self.items.get(Key["pk"]["S"])

        return {"Item": json.loads(json.dumps(item))} if item else {}

    def query(
        self,
        TableName,  # noqa: N803
        IndexName=None,  # noqa: N803
        KeyConditionExpression=None,  # noqa: N803
        ExpressionAttributeValues=None,  # noqa: N803
        Limit=100,  # noqa: N803
        ScanIndexForward=True,  # noqa: N803
    ):
        """Suporta a unica consulta que o painel faz: por batch_id."""
        wanted = (ExpressionAttributeValues or {}).get(":batch_id", {}).get("S")

        found = [
            item
            for item in self.items.values()
            if wanted is None or item["batch_id"]["S"] == wanted
        ]

        found.sort(key=lambda item: int(item["created_at"]["N"]), reverse=not ScanIndexForward)

        return {"Items": [json.loads(json.dumps(item)) for item in found[:Limit]], "Count": len(found)}


class Queue:
    def __init__(self, url):
        self.url = url
        self.messages = []
        self.in_flight = {}
        self.received = 0


class SQS:
    """Filas em memoria, com o suficiente do contrato da SQS.

    Nao ha visibilidade nem reentrega automatica: quem quer demonstrar retry
    usa o modo caos, que e deterministico. Um duble com temporizador tornaria a
    demonstracao dependente de relogio — e o teste, instavel.
    """

    def __init__(self):
        self.queues = {}
        self._lock = threading.Lock()
        self._next_id = 1

    def queue(self, url):
        return self.queues.setdefault(url, Queue(url))

    def send_message(self, QueueUrl, MessageBody, MessageAttributes=None):  # noqa: N803
        with self._lock:
            message_id = "msg-%d" % self._next_id
            self._next_id += 1

        body = MessageBody if isinstance(MessageBody, str) else json.dumps(MessageBody)

        self.queue(QueueUrl).messages.append(
            {
                "messageId": message_id,
                "body": body,
                "attributes": {"ApproximateReceiveCount": "1"},
                "messageAttributes": MessageAttributes or {},
            }
        )

        return {"MessageId": message_id}

    def send_message_batch(self, QueueUrl, Entries):  # noqa: N803
        successful = []

        for entry in Entries:
            sent = self.send_message(
                QueueUrl=QueueUrl,
                MessageBody=entry["MessageBody"],
                MessageAttributes=entry.get("MessageAttributes"),
            )
            successful.append({"Id": entry["Id"], "MessageId": sent["MessageId"]})

        return {"Successful": successful, "Failed": []}

    def send_from_workflow(self, payload):
        """Integracao direta `arn:aws:states:::sqs:sendMessage` do Catch.

        O Step Functions serializa o MessageBody em JSON quando ele e um
        objeto — e por isso que aqui tambem.
        """
        return self.send_message(
            QueueUrl=payload["QueueUrl"],
            MessageBody=payload["MessageBody"],
            MessageAttributes=payload.get("MessageAttributes"),
        )

    def receive_message(self, QueueUrl, MaxNumberOfMessages=10, **kwargs):  # noqa: N803
        queue = self.queue(QueueUrl)
        taken, queue.messages = queue.messages[:MaxNumberOfMessages], queue.messages[MaxNumberOfMessages:]
        queue.received += len(taken)

        return {"Messages": taken}

    def get_queue_attributes(self, QueueUrl, AttributeNames=None):  # noqa: N803
        queue = self.queue(QueueUrl)

        return {
            "Attributes": {
                "ApproximateNumberOfMessages": str(len(queue.messages)),
                "ApproximateNumberOfMessagesNotVisible": str(len(queue.in_flight)),
            }
        }

    def depth(self, url):
        return len(self.queue(url).messages)


class ExecutionAlreadyExists(ClientError):
    """Nome de execucao repetido — a camada 1 da idempotencia em acao."""

    def __init__(self, name):
        super().__init__("ExecutionAlreadyExists", "Execution Already Exists: '%s'" % name)
        self.name = name


class StepFunctions:
    """Executa a state machine de verdade, pelo interpretador, ao ser chamado.

    O Step Functions real inicia a execucao de forma assincrona e devolve o ARN
    na hora. Aqui a execucao roda **sincronamente** dentro do StartExecution:
    numa demonstracao de linha de comando, o assincronismo so acrescentaria
    espera e nao mudaria nada do que se quer ver. O dispatcher nao percebe a
    diferenca — ele nunca olha o resultado da execucao.
    """

    ARN_PREFIX = "arn:aws:states:us-east-1:000000000000:execution:bhaskara"

    def __init__(self, engine):
        self.engine = engine
        self.executions = {}
        self.order = []
        self.rejected = 0

    def start_execution(self, stateMachineArn=None, name=None, input=None):  # noqa: N803, A002
        if name in self.executions:
            self.rejected += 1
            raise ExecutionAlreadyExists(name)

        execution = self.engine.start(json.loads(input) if isinstance(input, str) else input, name=name)

        self.executions[name] = execution
        self.order.append(name)

        return {"executionArn": "%s:%s" % (self.ARN_PREFIX, name), "startDate": execution.started_at}

    def counts(self):
        counts = {"SUCCEEDED": 0, "FAILED": 0, "RUNNING": 0}

        for execution in self.executions.values():
            counts[execution.status] = counts.get(execution.status, 0) + 1

        return counts
