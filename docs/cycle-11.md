# Ciclo 11 — o painel de operacao, os alarmes e as consultas

Os dois ciclos anteriores produziram sinal: log com envelope e onze metricas.
Este ciclo o transforma em tela. Tudo em Terraform — um dashboard montado a mao
no console e configuracao que existe em um lugar so, que ninguem revisa e que
desaparece com a conta.

## O que entrou

`infra/observability.tf`, com **13 recursos novos**:

| Recurso | Quantidade |
| --- | --- |
| `aws_cloudwatch_dashboard` | 1, com 10 widgets em 5 faixas |
| `aws_cloudwatch_metric_alarm` | 5 |
| `aws_sns_topic` | 1 |
| `aws_cloudwatch_query_definition` | 5 |
| `aws_cloudwatch_log_metric_filter` | 1 |

Mais quatro `outputs` com os links diretos do console — dashboard, service map,
Logs Insights e o ARN do topico.

## Dois paineis, duas perguntas

O projeto ja tinha um painel (`dashboard.tf`, S3 + CloudFront). Ele nao foi
tocado, e os dois convivem porque respondem coisas diferentes:

| | Painel web do projeto | Dashboard CloudWatch |
| --- | --- | --- |
| Pergunta | **o que o fluxo esta fazendo?** | **como o sistema esta se comportando?** |
| Mostra | execucoes correndo, estado por estado, o que caiu na dead-letter | latencia, erro, saturacao, custo |
| Fonte | `GET /flow` — SQS, Step Functions, DynamoDB | metricas do CloudWatch |
| Publico | quem esta demonstrando | quem esta operando |

## As cinco faixas

**Entrada.** Submetidas, iniciadas, evitadas por idempotencia e duplicatas
barradas na gravacao, no mesmo grafico. A distancia entre a primeira e a
segunda linha **e** a idempotencia — deixa de ser afirmacao do README e vira
medida.

**Latencia.** Duracao p95 por estado, com `RootX1` e `RootX2` em series
separadas — e disso que a analise do `Parallel` vai depender. Ao lado, a
latencia ponta a ponta em media, p95 e p99: a media diz como o sistema se
comporta, o p99 diz o que o pior caso custa a quem espera.

**Falha e saturacao.** Execucoes da state machine, retry/caos/cold start, e as
duas filas. O `ChaosInjected` esta ali para poder ser **subtraido**: sem separar
a falha pedida da falha real, a analise de confiabilidade mede a propria
demonstracao.

**O que a AWS mede sozinha.** Throttles e erros das sete funcoes, e a API. O
throttling e a metrica mais importante do projeto — a conta tem 10 execucoes
concorrentes no total.

**O log, dentro do painel.** Um widget de consulta com as ultimas 40 linhas de
`ERROR` e `WARN` das sete funcoes. Fecha o ciclo: do grafico que mostra que algo
aconteceu para a linha que diz o que foi, sem trocar de tela.

## Cinco alarmes, e nao quinze

Um alarme que dispara sem que ninguem saiba o que fazer treina quem o recebe a
ignora-lo — e a partir dai o proximo tambem sera ignorado. Cada um tem a acao
escrita na propria descricao, que e o que aparece no e-mail:

| Alarme | Dispara quando | Acao escrita nele |
| --- | --- | --- |
| dead-letter com mensagem | qualquer mensagem parada | ler o motivo anexado e decidir entre corrigir a origem e descartar |
| execucoes falhando | > 5 em 5 min | comparar com `ChaosInjected`; se a diferenca for grande, a falha nao foi pedida |
| throttling de Lambda | > 20 somados em 5 min | reduzir `dispatcher_max_concurrency` ou pedir aumento de quota, que e gratuito |
| fila drenando devagar | mensagem mais antiga > 5 min, duas vezes seguidas | esperado apos carga grande; se persistir, o event source mapping parou |
| API com 5xx | qualquer um | 5xx na borda e defeito nosso, nao do cliente |

Tres detalhes que mudam se o alarme e util ou ruido:

**`treat_missing_data = "notBreaching"`** em todos. Fila sem metrica e fila sem
mensagem. Sem isso, o estado normal de um laboratorio parado seria
`INSUFFICIENT_DATA` em cinco alarmes.

**O limite de execucoes falhas e 5, e nao 0.** O modo caos faz o sistema falhar
por projeto. Um alarme que dispara toda vez que a demonstracao roda e um alarme
que ninguem le.

**Throttling usa metric math** somando as sete funcoes, em vez de sete alarmes.
O que interessa e se a conta saturou, nao qual funcao chegou primeiro na fila do
estrangulamento.

O topico SNS existe sem inscricao. Inscrever um e-mail exige confirmacao manual
por link, que o Terraform nao consegue completar — o recurso ficaria "pending
confirmation" para sempre no state de quem so queria ver o alarme mudar de cor.
`var.alert_email` liga a inscricao para quem quiser.

## As consultas versionadas

Cinco `aws_cloudwatch_query_definition`. Uma query que existe so no historico do
navegador de quem a escreveu nao e observabilidade — e conhecimento tacito de
uma pessoa so.

1. **o caminho de uma equacao** — cole o correlation id e veja os ~7 estados em
   ordem, com as tentativas que falharam;
2. **duracao por estado** — p50, p95 e p99, a base da analise;
3. **cold start e inicializacao** — le `record.metrics.initDurationMs`, que so
   existe como campo porque o ciclo 8 pos o log da plataforma em JSON;
4. **por que as equacoes foram recusadas** — contagem por `reason`;
5. **quanto log cada execucao produz** — bytes por log group, que e o numero da
   otimizacao de custo de log.

## O unico sinal que nao vem do EMF

O `403` acontece **antes** de qualquer handler rodar. O `submit` registra o seu
proprio `request_unauthorized` quando a chave esta errada, mas uma requisicao
barrada pelo throttling do stage nunca chega ao codigo. O access log ve as duas,
e o metric filter as conta.

`default_value = 0` no metric transformation: sem ele a serie fica com buracos
em vez de zeros, e nao da para distinguir "ninguem tentou" de "ninguem mediu".

## Uma correcao de seguranca, nao uma excecao

O checkov reprovou `CKV_AWS_26` — topico SNS sem criptografia. Diferente do
DynamoDB, do S3 e dos log groups, **um topico SNS nasce sem criptografia
nenhuma**: nao ha padrao a herdar.

Os skips de KMS que ja existiam no projeto trocavam uma CMK de US$ 1/mes por
criptografia gerenciada pela AWS que ja estava ligada. Aqui nao havia nada
ligado, e `alias/aws/sns` e gerenciada pela AWS e **nao tem custo mensal**. Nao
havia o que trocar: foi correcao, e nao mais uma linha na lista de excecoes.

## Criterio de pronto

- `terraform validate` limpo; `plan` com **13 to add, 16 to change, 0 to
  destroy**;
- `checkov` com 167 aprovadas e 0 reprovadas;
- 329 testes verdes — nada aqui e Python;
- os quatro links do console saindo por `terraform output`.

O `apply` fica para o ciclo 12, junto com a carga real: nao ha por que
reimplantar a stack duas vezes.
