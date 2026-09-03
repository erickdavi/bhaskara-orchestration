# Ciclo 3 — o resultado e gravado uma vez so

## O que entrou

- `src/shared/idempotency.py`: a chave, num lugar so.
- `src/handlers/persist`: escrita condicional no DynamoDB.
- `local/doubles.py`: SQS, DynamoDB e Step Functions em memoria.
- Fixture `aws` **autouse** no `conftest.py`.
- 139 testes.

## Decisoes

**Duas camadas de idempotencia, nao uma.** A camada 1 (nome de execucao
recusado pelo Step Functions) chega no ciclo 5, junto com o dispatcher. Esta e
a camada 2: `PutItem` com `attribute_not_exists(pk)`. A primeira depende de a
reentrega acontecer *depois* de a execucao ter comecado; a segunda cobre o
resto — replay manual, reprocessamento de DLQ, qualquer mudanca futura no
dispatcher.

**Duplicata nao e falha.** O estado devolve `duplicate: true` e segue para
`Done`. Tratar conflito como erro mandaria para a dead-letter uma execucao que
fez exatamente a coisa certa, e o painel contaria como falha algo que e
sucesso.

**O `batch_id` entra na chave.** Sem ele, `{"a":1,"b":-5,"c":6}` seria
processada uma unica vez em 90 dias na conta inteira, e a segunda demonstracao
do dia apareceria vazia. Com ele, a deduplicacao acontece dentro da carga — que
e onde ela e util e onde o painel consegue mostra-la.

**Numeros vao para a tabela como texto.** O tipo N do DynamoDB representa de
1e-130 a 1e125; um float64 vai a 1e308. Uma equacao com coeficientes extremos
produziria raizes que o tipo N recusa, e a escrita falharia com erro de
validacao — que viraria retry e depois dead-letter, por um resultado correto.
Nada e filtrado nem ordenado por esses campos.

**O codigo nao importa botocore para tratar erro.** `put_once()` le
`error.response["Error"]["Code"]`. Assim o duble de teste levanta um erro com a
mesma forma do real, e o caminho exercitado no teste e o mesmo que roda na
nuvem — sem depender de botocore estar instalado para rodar a suite.

**Os dubles sao ligados num lugar so.** `runtime.bind_doubles()` recebe o
setter — `monkeypatch.setattr` nos testes, `setattr` no simulador — e liga os
mesmos objetos nos dois casos. A fixture e autouse: um teste que a esquecesse
criaria cliente boto3 de verdade.

## Criterio de pronto

    primeira execucao   stored: true,  duplicate: false   1 item na tabela
    segunda execucao    stored: false, duplicate: true    1 item na tabela

A duplicata devolve o item **que esta gravado**, e nao o que veio na segunda
execucao — verificado por um teste que manda raizes diferentes de proposito.
