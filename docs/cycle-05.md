# Ciclo 5 — a borda, a fila e a infraestrutura

## O que entrou

- `src/handlers/submit` (+ `generator.py`): `POST /orders` vira N mensagens.
- `src/handlers/dispatcher`: mensagem vira execucao, com a camada 1 da
  idempotencia.
- `local/simulator.py`: a demonstracao de linha de comando (`./run.sh demo`).
- `infra/`: 38 recursos — SQS, DynamoDB, 6 Lambdas, state machine, HTTP API,
  7 roles.
- `scripts/`: publicar na fila, iniciar uma execucao e renderizar a definicao.
- 231 testes.

## Decisoes

**O submit publica; ele nao inicia execucoes.** Disparar 2.000 `StartExecution`
de dentro de uma requisicao HTTP seria a maneira mais rapida de derrubar a
demonstracao numa conta com 10 execucoes concorrentes. A fila absorve o pico e
o Step Functions recebe trabalho no ritmo que a conta aguenta.

**O acelerador do sistema e o `maximum_concurrency` do event source mapping.**
A AWS nao permite reservar concorrencia quando o limite da conta e 10 — ela
exige deixar 10 nao reservadas. Entao o controle mudou de lugar: o dispatcher
roda com no maximo 2 invocacoes simultaneas (o minimo aceito pelo servico), e o
`Retry` em `Lambda.TooManyRequestsException` transforma throttling em espera,
nao em falha.

**Teto de 2.000 equacoes, e nao os 5.000 do Checkpoint 2.** La cada mensagem
era uma invocacao barata de Lambda; aqui cada mensagem vira uma execucao de
state machine, e a transicao de estado e a unidade de custo.

**Mensagem invalida tambem vira execucao.** O dispatcher poderia recusar um
corpo que nao e JSON, mas nao recusa: ele passa o texto cru em `meta.raw_body`
e o estado Validate o recusa dentro do fluxo. Assim toda mensagem aparece no
diagrama e a contagem fecha. Recusar no dispatcher produziria mensagens que
somem sem rastro.

**Recusa por idempotencia nao e falha do lote.** `ExecutionAlreadyExists` faz o
dispatcher confirmar a mensagem e seguir. Devolver para retry faria a SQS
reentregar tres vezes algo que ja foi processado, e terminar na dead-letter uma
equacao que deu certo.

**Uma unica excecao ao "nenhum Resource *".** A entrega de logs do Step
Functions e configurada por uma API de conta que nao aceita ARN de recurso; a
propria AWS instrui a conceder assim. Esta comentada no `iam.tf`, e o escopo
real fica limitado pelo `log_destination` declarado na state machine.

**O ARN da state machine e montado a mao no `locals`.** A state machine precisa
dos ARNs das funcoes e o dispatcher precisa do ARN dela — um ciclo. O nome e
deterministico, entao o ARN tambem e. Mesmo truque que o Checkpoint 2 usou para
a fila e a DLQ.

## Criterio de pronto

    ./run.sh demo 80 --seed 7

    Mensagens publicadas na fila orders    80
    Execucoes iniciadas                    66
      evitadas por idempotencia            14
    concluidas                             59   (19 + 21 + 19 nos tres ramos)
    recuperadas pelo retry                  4
    enviadas a dead-letter                  7
    total de mensagens                     80    <- a conta fecha

`terraform validate` limpo e `terraform plan` com 38 recursos a criar.
`./scripts/render-definition.sh` mostra a definicao renderizada: ancoras YAML
expandidas, `$$.State.Name` intacto e todos os `${...}` substituidos.

## Um detalhe que quase passou

O `templatefile` interpreta `${...}` **inclusive dentro de comentarios** do
YAML. O cabecalho do arquivo explicava os placeholders usando a propria
sintaxe, e o `terraform validate` falhou com "Invalid expression" apontando
para a linha do comentario. Corrigido escapando com `$${...}`, que e como o
proprio Terraform escapa um cifrao literal.
