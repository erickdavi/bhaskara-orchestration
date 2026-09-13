# Ciclo 10 — X-Ray, e uma decisao revertida por escrito

Ciclo curto, so de infraestrutura: nenhuma linha de Python mudou. O que mudou
foi uma decisao registrada no Checkpoint 3.

## O que entrou

- `tracing_config { mode = "Active" }` nas sete funcoes.
- `tracing_configuration { enabled = true }` na state machine.
- As permissoes de X-Ray nas oito roles.
- `var.xray_enabled`, para desligar tudo em um lugar so.
- Dois skips removidos do `.checkov.yaml`.

## A reversao

O Checkpoint 3 deixou o X-Ray desligado e escreveu o motivo no proprio arquivo:

> X-Ray desligado: o traco distribuido nao acrescenta nada que o historico da
> execucao ja nao mostre neste fluxo, e e cobrado por trace.

Aquilo estava certo sob a premissa daquele checkpoint. A pergunta era **"o
fluxo esta correto?"** — e para isso o `GetExecutionHistory` basta: ele mostra
cada estado, a entrada, a saida e o erro.

A premissa mudou. A pergunta agora e **"onde o tempo e gasto?"**, e o historico
nao responde. Ele da a duracao de cada estado, mas nao separa a invocacao da
Lambda do overhead de transicao do Step Functions — que e exatamente a
distincao de que a otimizacao candidata do `Parallel` depende. E nao mostra os
dois ramos sobrepostos no tempo, que e como se ve se o paralelismo esta
acontecendo de fato.

O comentario antigo nao foi apagado. Ele esta no arquivo, com a data da
reversao e o motivo dela. Um repositorio que muda de ideia sem dizer que mudou
perde o valor de ter registrado a ideia anterior.

## Active, e nao PassThrough

`PassThrough` e o padrao da Lambda: a funcao so participa do trace se quem a
chamou ja tiver decidido amostrar. Com a state machine tracada isso quase
funcionaria — mas `Active` faz a funcao amostrar tambem quando invocada
direto, que e como `scripts/run-execution.sh` e qualquer teste manual a chamam.
Sem isso, o service map mostraria a state machine ligada a caixas vazias.

O atributo ficou como ternario, e nao como bloco `dynamic`:

```hcl
mode = var.xray_enabled ? "Active" : "PassThrough"
```

Um `dynamic` esconde o atributo da analise estatica. Foi o que aconteceu na
primeira tentativa: o checkov passou a reprovar `CKV_AWS_50` nas sete funcoes,
sem ter como saber que a resposta era "sim". A forma estatica diz a mesma coisa
e continua verificavel.

## A segunda excecao ao `Resource: "*"`

O projeto tinha uma regra e uma excecao: nenhuma policy com `Resource: "*"`,
salvo a entrega de logs do Step Functions, que o servico exige assim. Agora sao
duas.

`xray:PutTraceSegments` e `xray:PutTelemetryRecords` nao tem ARN a que
restringir — um segmento de trace nao pertence a nada que exista antes de ser
criado. As duas acoes so **escrevem** telemetria: nenhuma le trace de ninguem,
nenhuma altera configuracao. A role da state machine leva duas a mais,
`GetSamplingRules` e `GetSamplingTargets`, porque e ela quem decide se um trace
sera amostrado; as duas sao de leitura.

O cabecalho do `iam.tf` foi atualizado de "exatamente UMA excecao" para "DUAS".
Um comentario que conta errado e pior que nenhum comentario.

## O skip que virou resto

`CKV_AWS_50` e `CKV_AWS_284` estavam no `.checkov.yaml` com esta justificativa:

> X-Ray (50/284) nao acrescenta ao que o historico da execucao ja mostra neste
> fluxo.

A frase deixou de ser verdade no momento em que o X-Ray foi ligado. Os dois
sairam da lista e passaram a ser verificados de verdade — e passam.

Vale como regra: **um skip cuja justificativa deixou de valer e pior que
nenhum skip.** Ele parece uma decisao revisada e e so um resto de uma decisao
antiga, e o proximo a ler vai supor que alguem pensou no assunto recentemente.

## Custo

Camada gratuita: 100.000 traces registrados por mes. Uma carga de 100 equacoes
gera ~100 traces. Acima disso, US$ 5,00 por milhao. **Nao ha custo real neste
projeto** — o que havia era a duvida sobre se valia a complexidade, e o
checkpoint respondeu.

## Criterio de pronto

- 329 testes verdes — nenhum toca em Terraform, e nenhum precisou mudar;
- `terraform validate` limpo; `plan` com **0 to add, 16 to change, 0 to
  destroy** (7 funcoes, 8 policies, 1 state machine);
- `checkov` com 161 aprovadas, 0 reprovadas e dois skips a menos.
