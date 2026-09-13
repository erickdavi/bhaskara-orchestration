# Medicoes — Checkpoint 4

Duas cargas identicas na stack real (`us-east-1`), em 13/09/2026:
**120 equacoes, 15% duplicadas, 12% com caos**. A unica diferenca entre as duas
e a correcao descrita na otimizacao 1.

Numeros extraidos do CloudWatch Logs Insights e do CloudWatch Metrics; os
comandos que os produziram estao em `docs/cycle-12.md`.

---

## Carga 1 — antes

### Contagem (metricas EMF)

| Metrica | Valor |
| --- | --- |
| `EquationsSubmitted` | 120 |
| `ExecutionsStarted` | 94 |
| `ExecutionsDeduplicated` | **26** |
| `PersistDuplicate` | 0 |
| `RetryAttempt` | 44 |
| `ChaosInjected` | 40 |
| `ColdStart` | 29 |

As 26 evitadas pela camada 1 sao as duplicatas dos 15% pedidos: 120 − 26 = 94
execucoes. A camada 2 nao teve o que barrar, que e o esperado quando a camada 1
funciona.

### Duracao do handler, por estado

| service | state | n | p50 (ms) | p95 (ms) | p99 (ms) |
| --- | --- | --- | --- | --- | --- |
| persist | Persist | 100 | 13,403 | **5.939,687** | 6.071,164 |
| delta | Delta | 105 | 0,032 | 0,044 | 0,048 |
| root | RootX1 | 38 | 0,031 | 0,039 | 0,040 |
| root | RootDouble | 28 | 0,026 | 0,036 | 0,047 |
| root | RootX2 | 33 | 0,032 | 0,034 | 0,038 |
| validate | Validate | 100 | 0,021 | 0,027 | 0,032 |

### Onde estava a cauda do persist

| cold_start | n | media (ms) | p50 | p99 | maximo |
| --- | --- | --- | --- | --- | --- |
| 0 (quente) | 79 | 13,39 | 13,65 | 25,79 | 25,79 |
| 1 (frio) | 10 | **5.972,17** | 5.939,69 | 6.221,11 | 6.221,11 |

### Plataforma, por funcao

| funcao | invocacoes | frias | init (ms) | billed medio (ms) | billed p95 | memoria pico (MB) |
| --- | --- | --- | --- | --- | --- | --- |
| submit | 1 | 1 | 96,96 | 2.934 | 2.934 | 93 |
| persist | 100 | 10 | 82,99 | **632,23** | 6.044 | 95 |
| dispatcher | 42 | 2 | 92,91 | 228,31 | 182 | 94 |
| root | 99 | 7 | 77,53 | 10,24 | 75 | 36 |
| validate | 100 | 4 | 84,27 | 8,57 | 35 | 36 |
| delta | 105 | 5 | 88,53 | 8,14 | 44 | 36 |

### Latencia ponta a ponta

| n | media | p50 | p95 | maximo |
| --- | --- | --- | --- | --- |
| 89 | 1.550,96 ms | 497 ms | **7.890 ms** | 14.081 ms |

### Volume de log

| grupo | bytes | linhas | bytes/linha |
| --- | --- | --- | --- |
| **state machine** | **1.741.065** | 2.799 | 622,03 |
| lambda:delta | 176.573 | 336 | 525,51 |
| lambda:persist | 168.411 | 331 | 508,79 |
| lambda:validate | 148.841 | 312 | 477,05 |
| lambda:root | 148.575 | 312 | 476,20 |
| lambda:dispatcher | 117.912 | 292 | 403,81 |
| lambda:submit | 2.563 | 6 | 427,17 |

Sete funcoes somadas: 762.875 bytes. A state machine sozinha: 1.741.065.
**70% da ingestao vem de um unico log group.**

### Throttling e erro

| funcao | throttles | erros |
| --- | --- | --- |
| root | 10 | 8 |
| delta | 7 | 14 |
| persist | 6 | 11 |
| dispatcher | 3 | 0 |
| validate | 2 | 7 |
| submit / status | 0 | 0 |

Os 40 erros somados batem com os 40 `ChaosInjected`: **nenhuma falha real** na
carga. Sem a metrica de caos, esses 40 seriam lidos como defeito.

---

## Carga 2 — depois da otimizacao 1

### Duracao do handler, frio contra quente

| funcao | cold_start | n | media (ms) | p50 | maximo |
| --- | --- | --- | --- | --- | --- |
| persist | 1 | 7 | **243,47** | 286,75 | 288,10 |
| persist | 0 | 89 | 16,89 | 14,59 | 294,74 |
| dispatcher | 1 | 10 | 159,91 | 178,86 | 230,33 |
| dispatcher | 0 | 194 | 57,38 | 65,43 | 104,72 |
| delta | 0 | 108 | 0,034 | 0,032 | 0,109 |
| root | 0 | 109 | 0,030 | 0,028 | 0,108 |
| validate | 0 | 98 | 0,020 | 0,019 | 0,065 |

### Plataforma, por funcao

| funcao | invocacoes | frias | init (ms) | billed medio (ms) | billed maximo |
| --- | --- | --- | --- | --- | --- |
| submit | 1 | 1 | 744,20 | 1.057 | 1.057 |
| dispatcher | 42 | 2 | 399,63 | 121,45 | 687 |
| persist | 96 | 7 | 500,17 | **86,78** | 1.028 |
| root | 110 | 1 | 87,99 | 9,05 | 146 |
| delta | 109 | 1 | 83,71 | 8,28 | 133 |
| validate | 99 | 1 | 88,42 | 8,15 | 92 |

### Latencia ponta a ponta

| n | media | p50 | p95 | maximo |
| --- | --- | --- | --- | --- |
| 93 | 1.067,91 ms | 509 ms | **3.120 ms** | 6.716 ms |

---

## O antes e o depois, lado a lado

| | Antes | Depois | Diferenca |
| --- | --- | --- | --- |
| `persist` frio, tempo no handler | 5.972 ms | 243 ms | **24x mais rapido** |
| `persist`, billed medio | 632 ms | 87 ms | **7,3x mais barato** |
| `persist`, init | 83 ms | 500 ms | +417 ms |
| Latencia ponta a ponta, p95 | 7.890 ms | 3.120 ms | **−60%** |
| Latencia ponta a ponta, maxima | 14.081 ms | 6.716 ms | **−52%** |
| `submit`, primeira requisicao | 2.833 ms | 309 ms | **9,2x mais rapido** |

O trabalho nao desapareceu: 417 ms migraram para a inicializacao. O que mudou e
**onde** ele acontece — e o mesmo `import boto3` mais `boto3.client()` custa
~5.700 ms no handler e ~417 ms na fase de init, porque a Lambda concede CPU
ampliada durante a inicializacao, independentemente da memoria configurada.
