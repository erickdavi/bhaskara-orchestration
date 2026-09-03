# Ciclo 4 — o caminho de erro

## O que entrou

- `Catch` em toda Task de topo e no `Parallel`, todos levando ao mesmo lugar.
- `DeadLetter`: integracao direta `arn:aws:states:::sqs:sendMessage`, sem Lambda.
- `Rejected`: estado `Fail`, para que a recusa apareca como falha no console.
- 149 testes.

## Decisoes

**Nao ha Lambda no caminho de erro.** Duas razoes. A conta tem 10 execucoes
concorrentes no total, compartilhadas com os checkpoints anteriores — uma
funcao a menos aqui e concorrencia preservada para o caminho feliz, justamente
quando as coisas ja vao mal. E o caminho de erro precisa ser o mais simples
possivel: uma funcao que falhasse ao registrar a falha transformaria um
problema visivel em uma mensagem perdida.

**O Catch do Parallel fica no Parallel.** Em ASL, o `Next` de um estado so pode
apontar para estados do mesmo nivel, e `DeadLetter` esta fora do ramo. A falha
de um ramo falha o Parallel, e e ali que o Catch age. Isso virou tambem uma
correcao no interpretador: um erro de ramo agora e convertido em falha do
Parallel, como o servico faz — antes ele subia direto para a execucao e o Catch
nunca agia.

**A execucao recusada termina como FAILED.** `DeadLetter` publica e vai para um
estado `Fail`. Terminar em `Succeed` esconderia a recusa numa lista de
sucessos, e o painel contaria como processada uma equacao que ninguem
processou.

**O motivo viaja duas vezes.** No corpo (`error.Error` e `error.Cause`) e como
message attribute. A DLQ nativa da SQS move o payload original sem dizer por
que; um operador olhando a fila precisa do motivo sem abrir cada mensagem.

**`source: workflow` no corpo.** A mesma fila recebe dois tipos de mensagem: as
que o Catch publica e as que a SQS move por `redrive_policy` quando o proprio
dispatcher falha (ciclo 5). O painel precisa distinguir.

## Criterio de pronto

    caos fails: 2   ->  duas falhas em Delta, execucao SUCCEEDED na terceira
    caos fails: 9   ->  4 tentativas (1 + 3 retries), dead-letter, FAILED
    a = 0           ->  1 tentativa, dead-letter, FAILED, nada gravado

A mensagem na dead-letter carrega `source`, a equacao original, o motivo no
corpo e o motivo no atributo.
