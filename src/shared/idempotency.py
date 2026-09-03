"""A chave que decide se duas mensagens sao "a mesma coisa".

Ela e usada em dois lugares, de proposito:

  1. como **nome da execucao** no StartExecution — o Step Functions Standard
     recusa nome repetido por 90 dias, entao uma mensagem reentregue pela SQS
     nao vira segunda execucao;

  2. como **chave primaria** no DynamoDB, com escrita condicional — se alguma
     coisa escapar da camada 1, o resultado ainda nao e gravado duas vezes.

Uma camada so nao bastaria. A primeira depende de a mensagem ter sido
reentregue *depois* de o dispatcher ter iniciado a execucao; a segunda cobre o
caso em que a execucao comecou duas vezes por qualquer outro motivo — replay
manual, reprocessamento de DLQ, uma alteracao futura no dispatcher.

## Por que o batch_id entra na chave

Sem ele, a equacao `{"a":1,"b":-5,"c":6}` seria processada **uma vez em 90
dias** na conta inteira: a segunda carga de demonstracao apareceria vazia, o
que e um comportamento absurdo para quem esta demonstrando. Com o batch_id, a
deduplicacao acontece dentro da carga — que e onde ela e util e onde o painel
consegue mostra-la — e cada nova carga recomeca do zero.

Para uma mensagem publicada direto na fila, sem batch_id, o dispatcher usa o
proprio messageId da SQS: unica por definicao, entao um envio manual repetido
sempre roda.
"""

import hashlib
import json

# 40 caracteres hexadecimais = 160 bits. Sobra para o volume deste projeto e
# cabe no limite de 80 caracteres do nome de execucao, com o prefixo.
KEY_LENGTH = 40

EXECUTION_PREFIX = "eq-"


def canonical(body):
    """Texto canonico de uma equacao, para que a ordem das chaves nao importe.

    `{"a":1,"b":2,"c":3}` e `{"c":3,"b":2,"a":1}` descrevem a mesma equacao e
    precisam produzir a mesma chave. Corpo que nao e JSON valido entra como
    texto cru — ele tambem precisa de chave, porque tambem vira execucao.
    """
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            return body.strip()

    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def key(batch_id, body):
    material = "%s:%s" % (batch_id, canonical(body))

    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:KEY_LENGTH]


def execution_name(idempotency_key):
    """Nome da execucao no Step Functions.

    O servico aceita ate 80 caracteres e recusa nome repetido nos ultimos 90
    dias — e essa recusa, e nao um lookup nosso, que implementa a camada 1.
    """
    return EXECUTION_PREFIX + idempotency_key
