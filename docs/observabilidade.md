# Observabilidade — Checkpoint 4

Este documento e a entrega do Checkpoint 4: o que foi instrumentado, como se le
o que ele produz, e as tres otimizacoes que a instrumentacao revelou.

Todos os numeros citados aqui foram **medidos na AWS**, em cargas reais de
13/09/2026. Os dados brutos estao em
[`evidencias/medicoes.md`](evidencias/medicoes.md); as sete telas do console,
com o que cada uma prova, em
[`evidencias/README.md`](evidencias/README.md).

---

## 1. O que foi instrumentado

| Camada | Antes do CP4 | Depois |
| --- | --- | --- |
| Log estruturado | JSON solto, sem nivel, correlacao inconsistente | envelope canonico de 10 campos, nas 7 funcoes |
| Nivel de severidade | inexistente | INFO / WARN / ERROR |
| Metricas de negocio | nenhuma | 11 metricas em EMF, 24 series |
| Metricas nativas | so as que a AWS emite | as mesmas, agora num painel |
| Traco distribuido | desligado por decisao | X-Ray ativo nas 7 funcoes e na state machine |
| Painel de operacao | nao existia | 1 dashboard, 10 widgets, em Terraform |
| Alarmes | nenhum | 5, com acao escrita em cada um |
| Consultas | ad hoc, no navegador de quem escreveu | 5 versionadas em Terraform |

Detalhe de cada decisao nos ciclos [08](cycle-08.md), [09](cycle-09.md),
[10](cycle-10.md), [11](cycle-11.md) e [12](cycle-12.md).

### O envelope de log

Toda linha das sete funcoes tem a mesma forma:

```json
{"event": "delta_calculated", "level": "INFO", "service": "delta", "state": "Delta",
 "execution": "9c1d4f…", "batch_id": "b-fbc1be4f0e", "request_id": "8f2e…",
 "attempt": 1, "cold_start": false, "duration_ms": 0.024,
 "value": 1, "sign": "positive"}
```

O campo que faz o resto valer e o `execution` — a chave de idempotencia, que
acompanha a equacao pelos cinco estados. Filtrar por ele devolve o caminho
inteiro de uma unica equacao, incluindo as tentativas que falharam:

```text
equation_validated   validate   Validate   attempt 0   INFO
chaos_injected       delta      Delta      attempt 0   WARN   TransientFailure
delta_calculated     delta      Delta      attempt 1   INFO   value 1  sign positive
root_calculated      root       RootX1     attempt 0   INFO   x1 = 3.0
root_calculated      root       RootX2     attempt 0   INFO   x2 = 2.0
result_stored        persist    Persist    attempt 0   INFO   end_to_end_ms 497
```

### As metricas

| Metrica | Unidade | Responde |
| --- | --- | --- |
| `EquationsSubmitted` | Count | volume de entrada |
| `ExecutionsStarted` | Count | quantas viraram execucao |
| `ExecutionsDeduplicated` | Count | idempotencia, camada 1 |
| `PersistDuplicate` | Count | idempotencia, camada 2 |
| `EquationsByDeltaSign` | Count · `Sign` | distribuicao dos tres ramos do `Choice` |
| `ValidationRejected` | Count · `Reason` | por que uma equacao e recusada |
| `HandlerDuration` | ms · `Service`, `State` | p50/p95/p99 por estado |
| `EndToEndLatency` | ms | da fila ate a gravacao |
| `ColdStart` | Count | invocacoes que pagam inicializacao |
| `RetryAttempt` | Count | invocacoes que sao reentrega |
| `ChaosInjected` | Count | falha pedida, separada da falha real |
| `UnauthorizedRequests` | Count | 403 no access log (metric filter) |

**Nenhum identificador e dimensao.** `execution`, `batch_id`, `message_id` e
`request_id` ficam na linha como campo — pesquisaveis, sem virar serie
temporal. Um id como dimensao criaria uma metrica nova por equacao processada,
cobrada por mes. Ha teste que falha se alguem tentar.

---

## 2. Analise de performance

### Onde o tempo e gasto

Medido em 94 execucoes reais:

| Estado | p50 | p95 | O que faz |
| --- | --- | --- | --- |
| `Persist` | 13,4 ms | **5.939,7 ms** | grava no DynamoDB |
| `Delta` | 0,032 ms | 0,044 ms | `b² − 4ac` |
| `RootX1` | 0,031 ms | 0,039 ms | uma raiz |
| `RootX2` | 0,032 ms | 0,034 ms | a outra raiz |
| `Validate` | 0,021 ms | 0,027 ms | valida coeficientes |

Duas leituras saltam daqui.

**A matematica e gratuita.** Os quatro estados de calculo somados custam menos
de **0,12 ms**. Tudo o mais que o sistema gasta e transporte, orquestracao e
I/O. Qualquer otimizacao que mexa na conta esta otimizando 0,1% do problema.

**Uma unica funcao domina a cauda.** O `Persist` tem p50 de 13 ms e p95 de 5,9
segundos — 443 vezes maior. Foi o fio que levou a otimizacao 1.

### A latencia que o usuario sentiria

`EndToEndLatency` mede da publicacao na fila ate a gravacao:

| | media | p50 | p95 | maxima |
| --- | --- | --- | --- | --- |
| Antes | 1.551 ms | 497 ms | 7.890 ms | 14.081 ms |
| Depois | 1.068 ms | 509 ms | 3.120 ms | 6.716 ms |

O p50 nao mudou — o caminho quente ja estava rapido. O que mudou foi a cauda,
que e onde vive a experiencia ruim.

### O que limita a vazao

A conta tem **10 execucoes Lambda concorrentes no total**, compartilhadas com
os Checkpoints 1 e 2. Na carga de 120 equacoes:

| funcao | throttles |
| --- | --- |
| root | 10 |
| delta | 7 |
| persist | 6 |
| dispatcher | 3 |
| validate | 2 |

28 estrangulamentos, e 44 `RetryAttempt`. O `Retry` da state machine fez o seu
trabalho — nenhuma equacao valida foi para a dead-letter por falta de
concorrencia —, mas cada reentrega e uma invocacao a mais disputando o mesmo
teto. **Concorrencia, e nao CPU, e o recurso escasso deste sistema.**

### Confiabilidade

94 execucoes, 89 concluidas, 5 na dead-letter. E o numero que importa:

> **Os 40 erros de Lambda batem exatamente com os 40 `ChaosInjected`.**

Nenhuma falha foi real. Sem a metrica que separa a falha pedida da falha
inesperada, a leitura seria "42% de falha em 94 execucoes" — uma conclusao
errada extraida de dados corretos.

---

## 3. Analise de custo

### O que a carga de 120 equacoes consumiu

| Servico | Consumo medido | Custo |
| --- | --- | --- |
| Step Functions | 94 execucoes × ~9 transicoes ≈ 850 | camada gratuita cobre 4.000/mes |
| Lambda | ~450 invocacoes, arm64, 128–256 MB | camada gratuita (1 M/mes) |
| SQS | ~300 requisicoes | camada gratuita (1 M/mes) |
| DynamoDB on-demand | 89 escritas | camada gratuita |
| CloudWatch Logs | **2,5 MB ingeridos** | camada gratuita (5 GB/mes) |
| X-Ray | ~94 traces | camada gratuita (100.000/mes) |

Uma demonstracao inteira cabe na camada gratuita. **O custo real deste projeto
nao esta no consumo: esta nas series de metrica, que sao cobradas por mes e nao
por uso.**

### O que a observabilidade custa por mes

| Item | Camada gratuita | Este projeto | Custo de tabela |
| --- | --- | --- | --- |
| Metricas customizadas | 10 | **24 series** | 14 × US$ 0,30 = **US$ 4,20** |
| Dashboard | 3 | 1 | US$ 0 |
| Alarmes | 10 | 5 | US$ 0 |
| Consultas salvas | — | 5 | US$ 0 |
| X-Ray | 100k traces/mes | ~100 por demo | US$ 0 |
| Logs | 5 GB/mes | ~2,5 MB por demo | US$ 0 |

**US$ 4,20/mes** se as 24 series receberem dado o mes inteiro. Um laboratorio
so publica metrica durante as demonstracoes, e a documentacao da AWS indica que
metrica customizada e cobrada proporcionalmente as horas em que recebe dado —
o que reduziria muito esse valor. **Nao confirmei isso na fatura**, entao o
numero acima e o teto, nao a previsao.

Isso corrige uma afirmacao do Checkpoint 3. O README dizia "nenhum recurso com
custo fixo". Depois do CP4 nao e mais verdade: as series de metrica sao custo
fixo mensal enquanto existirem.

### O que a instrumentacao custou em latencia

**Zero.** O EMF escreve a metrica no proprio log, e o CloudWatch a extrai do
lado dele — nao ha chamada de API no caminho quente. A alternativa,
`PutMetricData`, somaria uma chamada sincrona a toda invocacao. Num sistema
cujos estados de calculo levam 0,03 ms, isso teria multiplicado a duracao por
ordens de grandeza.

---

## 4. As tres otimizacoes

### Otimizacao 1 — mover a inicializacao do boto3 para a fase de init

**Estado: implementada e medida.**

**O problema.** O `Persist` tinha p50 de 13,4 ms e p95 de 5.939,7 ms. A consulta
que separa frio de quente mostrou onde estava a cauda:

| cold_start | n | media | p50 | maximo |
| --- | --- | --- | --- | --- |
| quente | 79 | 13,39 ms | 13,65 ms | 25,79 ms |
| **frio** | 10 | **5.972,17 ms** | 5.939,69 ms | 6.221,11 ms |

E o `initDurationMs` da mesma funcao era **83 ms**. Os seis segundos nao
estavam na inicializacao — estavam dentro do handler.

**A causa.** O Checkpoint 3 escreveu os clientes boto3 de forma preguicosa, de
proposito, para manter a inicializacao leve:

```python
def dynamodb():
    global _dynamodb
    if _dynamodb is None:
        import boto3                        # <- na primeira invocacao
        _dynamodb = boto3.client("dynamodb") # <- carrega o modelo do servico
    return _dynamodb
```

A intencao era boa e o efeito e o oposto. O `import boto3` e o
`boto3.client()` passam a rodar **na primeira invocacao**, com a CPU racionada
de uma funcao de 128 MB. A Lambda concede CPU ampliada durante a fase de
inicializacao, **independentemente da memoria configurada** — o codigo estava
fazendo o trabalho mais pesado exatamente na janela onde ele custa mais caro.

**A correcao.** Tres linhas no fim de quatro modulos:

```python
if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    dynamodb()
```

O acessor preguicoso continua existindo — e ele que permite aos testes
substituirem o cliente por um duble em memoria. O `if` garante que o
aquecimento so acontece dentro da Lambda; na suite e no simulador local nenhum
boto3 e importado.

**O resultado**, com duas cargas identicas de 120 equacoes:

| | Antes | Depois | |
| --- | --- | --- | --- |
| `persist` frio, no handler | 5.972 ms | 243 ms | **24x mais rapido** |
| `persist`, billed medio | 632 ms | 87 ms | **7,3x mais barato** |
| `persist`, init | 83 ms | 500 ms | +417 ms |
| ponta a ponta, p95 | 7.890 ms | 3.120 ms | **−60%** |
| ponta a ponta, maxima | 14.081 ms | 6.716 ms | **−52%** |
| `submit`, 1a requisicao | 2.833 ms | 309 ms | **9,2x mais rapido** |

O trabalho nao desapareceu: 417 ms migraram para a inicializacao. O que mudou e
**onde** ele acontece — o mesmo import custa ~5.700 ms no handler e ~417 ms no
init, e a diferenca e so a CPU que a plataforma concede em cada fase.

**Honestidade sobre o ganho em dinheiro.** Em custo de Lambda isso e uma frecao
de centavo nesta escala — mesmo a um milhao de equacoes, a economia seria
inferior a US$ 1. O ganho e de **latencia** e de **concorrencia**: uma funcao
que ocupa 6 segundos de um teto de 10 execucoes simultaneas e uma funcao que
estrangula as outras seis.

---

### Otimizacao 2 — parar de gravar o payload de cada estado no log da state machine

**Estado: confirmada e medida. Desligada por escolha, com a chave no lugar.**

**O problema.** Volume de log ingerido por 94 execucoes:

| grupo | bytes | por execucao |
| --- | --- | --- |
| **state machine** | **1.741.065** | **18,5 KB** |
| as 7 funcoes somadas | 762.875 | 8,1 KB |

**Um unico log group responde por 70% da ingestao** — mais que o dobro das sete
funcoes juntas. A causa esta declarada em `infra/statemachine.tf`:
`include_execution_data = true` grava a **entrada e a saida de cada estado** de
cada execucao. Com nove estados por execucao e a equacao inteira viajando no
payload, sao 18,5 KB por equacao resolvida.

**A medicao.** Duas cargas identicas de 30 equacoes com 20% de caos, a segunda
com o parametro desligado:

| | bytes | execucoes | por execucao | por evento |
| --- | --- | --- | --- | --- |
| `include_execution_data = true` | 546.813 | 29 | 18.856 B | 622,1 B |
| `include_execution_data = false` | 209.824 | 30 | **6.994 B** | **302,8 B** |

**−63% de bytes por execucao** no log da state machine, ou −51% por evento. No
sistema inteiro, a ingestao cai de 26,6 KB para 15,1 KB por execucao — **−43%**.

A US$ 0,50/GB, 100.000 execucoes custariam US$ 0,94 so nesse log group contra
US$ 0,35 depois. **Cinquenta e nove centavos por 100.000 execucoes** — e o
numero honesto, e ele e pequeno. O argumento aqui nao e a fatura desta conta: e
que 43% de um custo que cresce linearmente com o uso desaparece sem que nada
importante saia junto.

**O que sobrevive, verificado evento a evento.** Esta era a duvida que impedia a
adocao, e a resposta so podia vir da medicao:

| campo | usado para | sobrevive? |
| --- | --- | --- |
| `details.name` em `StateEntered` / `StateExited` | todos os contadores e o diagrama do painel | **sim** |
| `details.error` e `details.cause` em `LambdaFunctionFailed` | o motivo da falha na linha do tempo | **sim** |
| `details.output` em `StateExited` | o texto de detalhe de cada passo (`delta = 49`, `x1=3`) | **nao** |

Com o parametro desligado, o `GET /flow` devolveu os contadores completos —
`Validate` 30/30, `Delta` 30/30 com 3 falhas, `RootsInParallel` 11/11,
`Persist` 28/28 — e a linha do tempo com estado e duracao de cada passo. Apenas
o campo `detail` veio vazio, exatamente como previsto.

**O plano B, e uma correcao do que este relatorio dizia antes.** A versao
anterior deste documento afirmava que o detalhe passaria a vir de
`GetExecutionHistory`, "que o codigo ja implementa". **Estava errado.** O
`status` ja fazia a chamada, com `includeExecutionData=True`, mas extraia
apenas tipo, estado, horario e erro — nunca o `output`. O plano B nao existia;
existia o meio dele.

A correcao e uma linha, e agora esta no codigo, com quatro testes:

```python
"detail": summarize(details.get("output")),
```

Verificado contra a AWS com o log **sem** execution data, o detalhe sob demanda
voltou inteiro:

```text
Validate         a=-3 b=-36 c=-60
Delta            delta = 576
RootsInParallel  x1=-10  x2=-2
Persist          gravada
```

A API nao depende da configuracao de log: ela entrega entrada e saida de
qualquer jeito. O que muda e **quando** o payload e pago — em toda execucao,
para sempre, no log; ou so nas execucoes que alguem abre, na API.

**Por que fica desligada mesmo assim.** `var.state_machine_execution_data`
continua com o padrao `true`, e a razao nao e tecnica: a stack do Checkpoint 3
esta no ar para correcao, e a linha do tempo agregada do painel — com o detalhe
inline em cada passo — e parte daquela entrega. Trocar o comportamento dela
enquanto esta sendo avaliada seria otimizar o artefato errado.

Depois da correcao do CP3, e uma palavra:

```bash
terraform apply -var 'state_machine_execution_data=false'
```

### Otimizacao 3 — colapsar o `Parallel` das raizes num unico estado

**Estado: proposta, com o custo medido e uma recomendacao qualificada.**

**O problema.** Quando Δ > 0, o fluxo abre um `Parallel` com dois ramos, cada um
invocando a funcao `root` para calcular uma raiz. O custo medido de cada conta:

| estado | p50 | p95 |
| --- | --- | --- |
| `RootX1` | 0,031 ms | 0,039 ms |
| `RootX2` | 0,032 ms | 0,034 ms |

**Trinta e um microssegundos.** Para fazer 0,063 ms de aritmetica somada, o
fluxo gasta:

- 2 invocacoes de Lambda, com `billed_ms` medio de 9,05 ms cada — **288 vezes o
  tempo da conta**;
- ~3 transicoes de state machine em vez de 1;
- 2 slots do teto de 10 execucoes concorrentes da conta.

**O ganho.** Por 1.000 execucoes com Δ > 0: 2.000 transicoes a menos
(US$ 0,05), 1.000 invocacoes a menos (centavos), e — o que importa — **1.000
ocupacoes a menos** do recurso que a analise de performance identificou como
escasso. Os 10 throttles da funcao `root` na carga medida sao consequencia
direta disto.

**A recomendacao e qualificada, e isso e deliberado.** O Checkpoint 3 ja
registrava, no cabecalho do proprio `root/handler.py`, que a divisao e escolha
**de demonstracao e nao de desempenho** — o objeto daquele checkpoint era
mostrar o `Parallel` funcionando. A medicao nao contradiz aquela decisao: ela a
quantifica.

Entao a proposta e condicional:

- **em producao**, colapsar `RootsInParallel` num unico estado `Roots` que
  devolve as duas raizes — o `Persist` ja aceita a mesma forma, porque o
  caminho de Δ = 0 (`RootDouble`) ja faz exatamente isso;
- **neste repositorio**, manter como esta, porque o `Parallel` e o que o
  Checkpoint 3 entrega, e apaga-lo destruiria a evidencia da entrega anterior.

Propor "remova o `Parallel`" sem essa distincao seria confundir um artefato
didatico com um defeito.

---

## 5. O que a instrumentacao mudou na forma de trabalhar

Tres coisas que so aparecem depois de instrumentar, e que valem mais que
qualquer uma das otimizacoes:

**O p95 e uma pergunta, nao um numero.** Um p95 de 5,9 segundos ao lado de um
p50 de 13 ms nao diz "esta lento" — diz "ha duas populacoes aqui". A metrica
por si nao resolveu nada; a **consulta seguinte**, separando frio de quente,
resolveu.

**Medir a falha injetada foi o que impediu a conclusao errada.** Os 40 erros da
carga eram os 40 caos. A metrica que quase foi cortada por custo de
cardinalidade e a que separa "o sistema falhou" de "eu mandei o sistema
falhar".

**A instrumentacao corrigiu uma decisao bem-intencionada do checkpoint
anterior.** O boto3 preguicoso foi escrito para economizar; ele custava 6
segundos. Sem medicao, ele continuaria la, com um comentario explicando por que
era uma boa ideia.

**Verificar a saida de uma otimizacao vale tanto quanto medir a entrada.** A
otimizacao 2 dependia de um plano B que este relatorio afirmava ja existir no
codigo. Existia a chamada de API, nao a extracao do dado — a otimizacao teria
sido adotada e o painel teria perdido o detalhe em silencio. Quem encontrou foi
o teste de ponta a ponta contra a AWS, nao a leitura do codigo.

---

## 6. O que ficou de fora, e por que

- **Instrumentar os Checkpoints 1 e 2.** Os tres estilos arquiteturais convivem
  no CP3 — borda HTTP sincrona, coreografia por fila e orquestracao. Observar os
  outros dois repositorios veria as mesmas coisas em sistemas menores.
- **Metricas do CloudWatch dentro do painel web.** Exigiria IAM novo e
  `GetMetricData` cobrado por requisicao, a cada poll de 2 segundos. O dashboard
  do CloudWatch ja e a tela de metrica.
- **Alarme com notificacao ativa.** O topico SNS existe; a inscricao por e-mail
  fica em `var.alert_email`, vazia por padrao, porque exige confirmacao manual
  por link que o Terraform nao completa.
- **Adotar a otimizacao 2 agora.** Ela esta confirmada e a chave esta no lugar
  (`var.state_machine_execution_data`), mas o padrao continua `true` enquanto a
  stack do Checkpoint 3 estiver em correcao: a linha do tempo com detalhe
  inline e parte daquela entrega.
- **Aumentar a memoria das funcoes.** Era a candidata 3 original. Depois da
  otimizacao 1, o `persist` quente roda em 14 ms e a memoria de pico e 95 MB de
  128 — aumentar so faria sentido se a duracao ainda fosse dominada por CPU, e
  nao e mais. **A otimizacao 1 tornou esta desnecessaria**, e propo-la assim
  mesmo seria encher a lista.
- **Provisioned concurrency para eliminar cold start.** Impossivel nesta conta:
  a AWS exige deixar 10 execucoes nao reservadas, e o teto total e 10.
