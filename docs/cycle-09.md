# Ciclo 9 — metricas de negocio em EMF

O sistema tinha log estruturado desde o ciclo anterior e metrica nenhuma. As
unicas series existentes eram as que a AWS emite sozinha — `Duration`,
`Errors`, `Throttles`, profundidade de fila. Nenhuma delas sabe o que e uma
equacao.

## O que entrou

- `src/shared/metrics.py`: o bloco EMF, montado como estrutura de dados.
- `observability.log(..., measures=[...])`: a linha de log passa a poder **ser**
  a metrica.
- Onze medidas ligadas aos sete handlers.
- `tests/test_metrics.py` (25 casos) e `tests/test_handler_telemetry.py` (24).

## A linha e uma so

O bloco EMF nao vai numa linha separada: entra na linha de log que ja existia.

```json
{"event": "delta_calculated", "level": "INFO", "service": "delta", "state": "Delta",
 "execution": "k-9c1d4f", "batch_id": "b-demo", "attempt": 1, "cold_start": false,
 "duration_ms": 0.024, "value": 1, "sign": "positive",
 "_aws": {"Timestamp": 1789279187599, "CloudWatchMetrics": [
    {"Namespace": "Bhaskara/Orchestration", "Dimensions": [["Sign"]],
     "Metrics": [{"Name": "EquationsByDeltaSign", "Unit": "Count"}]},
    {"Namespace": "Bhaskara/Orchestration", "Dimensions": [["Service", "State"]],
     "Metrics": [{"Name": "HandlerDuration", "Unit": "Milliseconds"},
                 {"Name": "RetryAttempt", "Unit": "Count"}]}]},
 "Sign": "positive", "Service": "delta", "State": "Delta",
 "EquationsByDeltaSign": 1, "HandlerDuration": 0.024, "RetryAttempt": 1}
```

Duas razoes. A ingestao nao dobra — duas linhas seriam pagar duas vezes pelo
mesmo evento. E de um pico no grafico da para saltar direto para as execucoes
que o produziram, porque a linha que virou ponto no grafico carrega `execution`
e `batch_id`.

## Tres metricas saem de graca em toda invocacao

Passar `measures` — mesmo vazio, `measures=[]` — marca a linha como o ponto de
medicao da invocacao, e o modulo acrescenta `HandlerDuration`, `ColdStart` e
`RetryAttempt` sozinho. Repetir isso em sete handlers seriam sete oportunidades
de escrever `handler_duration` num deles e nunca mais ver os dois juntos no
mesmo grafico.

Uma execucao completa, com caos pedido no estado Delta:

```text
validate  equation_validated   ColdStart, HandlerDuration
delta     chaos_injected       ChaosInjected, HandlerDuration
delta     delta_calculated     EquationsByDeltaSign, HandlerDuration, RetryAttempt
root      root_calculated      HandlerDuration                        (RootX1)
root      root_calculated      HandlerDuration                        (RootX2)
persist   result_stored        EndToEndLatency, HandlerDuration
```

## O erro de estimativa da especificacao

A especificacao dizia "~15 metricas, dentro de uma ordem de grandeza
controlada" e US$ 1,50/mes acima da camada gratuita. Contando as series que o
conjunto realmente cria:

| Metrica | Dimensoes | Series |
| --- | --- | --- |
| `HandlerDuration` | `Service`, `State` | 9 |
| `ColdStart` | `Service` | 7 |
| `RetryAttempt` | `Service`, `State` | 6 |
| `ChaosInjected` | `State` | 6 |
| `ValidationRejected` | `Reason` | ate 9 |
| `EquationsByDeltaSign` | `Sign` | 3 |
| cinco metricas sem dimensao | — | 5 |
| **Total** | | **45** |

**45, e nao 15.** A US$ 0,30 por metrica por mes, US$ 13,50 de tabela — quase
dez vezes o que estava escrito. O erro foi contar nomes de metrica em vez de
combinacoes de valores de dimensao, que e a unidade que o CloudWatch cobra.

### O corte

Tres dimensoes nao pagavam o proprio custo, e sairam:

| Metrica | Antes | Depois | Por que da para cortar |
| --- | --- | --- | --- |
| `ColdStart` | `Service` — 7 series | sem dimensao — 1 | `record.metrics.initDurationMs` do platform.report ja da o numero por funcao no Logs Insights, de graca |
| `RetryAttempt` | `Service`+`State` — 6 | sem dimensao — 1 | `attempt` e `state` estao na propria linha; a metrica existe para o grafico ao longo do tempo, o detalhe fica no log |
| `ChaosInjected` | `State` — 6 | sem dimensao — 1 | o que o dashboard precisa e separar falha injetada de falha real; qual estado sofreu o caos e pergunta de investigacao, nao de painel |

**De 45 para 24 series** (29 no pior caso, se os nove motivos de recusa
ocorrerem todos). O que se perdeu foi granularidade de grafico em tres
metricas; o que se manteve foi a resposta a mesma pergunta, uma consulta de
distancia.

A regra que sobrou vale a pena escrever: **uma dimensao so se justifica quando a
pergunta que ela responde precisa de um grafico ao longo do tempo.** Se a
resposta cabe numa consulta pontual, ela pertence ao log.

## O que o teste protege

`test_metrics.py` cobre a forma — `_aws` na raiz, timestamp em milissegundos,
toda dimensao declarada presente como propriedade. Errar qualquer um dos tres
nao produz erro: produz silencio, e uma metrica que nunca aparece no console.

Mas os dois testes que importam sao outros.

**`FORBIDDEN`.** `execution`, `batch_id`, `message_id`, `request_id`,
`execution_name` e `idempotency_key` levantam excecao se aparecerem como
dimensao — nas duas grafias, snake_case e PascalCase, porque proteger so uma
deixaria a porta aberta. Sao os campos que tem tantos valores distintos quanto
equacoes processadas: cada um seria uma serie temporal nova por equacao. O
teste existe para que isso falhe no `pytest`, e nao na fatura.

**`test_nenhum_handler_usa_identificador_como_dimensao`.** Percorre os seis
handlers de uma vez. O outro teste protege o modulo; este protege o uso — um
handler novo que esqueca a regra falha aqui.

E `test_a_correlacao_continua_na_linha_medida`, que fecha o raciocinio: o id
nao vira dimensao **e continua na linha**. As duas coisas ao mesmo tempo sao o
ponto do EMF.

## Um efeito colateral util

`ValidationRejected` precisava de um motivo curto para ser dimensao, e a frase
do erro nao serve — ela carrega o nome do coeficiente e o texto do
`JSONDecodeError`, o que criaria uma serie por mensagem malformada. Entao
`InvalidEquation` ganhou um `reason` de um conjunto fechado de nove codigos:
`a_is_zero`, `missing_coefficient`, `coefficient_not_number`, `malformed_json`
e mais cinco.

O ganho passou do grafico. A frase continua no log, agora em `detail`, e o
codigo aparece tambem no `error_type` — a recusa ficou classificavel sem ler
texto livre, o que nenhuma das duas coisas era antes.

## Criterio de pronto

- 329 testes verdes (280 + 25 + 24);
- uma execucao completa emitindo as onze medidas nos pontos certos;
- `terraform validate` limpo, `plan` com 0 to add, 7 to change;
- `checkov` 160 aprovadas, 0 reprovadas; `trivy` sem achados MEDIUM ou acima;
- a estimativa de custo corrigida — e a correcao registrada aqui, nao apagada
  da especificacao.
