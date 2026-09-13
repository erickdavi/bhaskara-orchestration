# Especificação — Checkpoint 4: Observabilidade do pipeline

**Projeto:** `bhaskara-orchestration` (o mesmo repositório do CP3, instrumentado)
**Provedor:** AWS · **Região:** `us-east-1` · **Status:** proposta, aguardando aprovação

O CP4 rompe o padrão dos anteriores: não é um sistema novo. O enunciado pede
para **instrumentar o pipeline construído nas aulas anteriores** com logging e
métricas, comprovar com evidências visuais e propor 2 a 3 otimizações
fundamentadas.

| | Checkpoint | Repositório | O que é |
| --- | --- | --- | --- |
| CP1 | função serverless | `bhaskara-api` | síncrona: API Gateway → Lambda |
| CP2 | event-driven | `bhaskara-events` | coreografia: SQS → worker |
| CP3 | orquestração | `bhaskara-orchestration` | Step Functions compondo 5 Lambdas |
| **CP4** | **observabilidade** | **`bhaskara-orchestration`** | **os três estilos instrumentados no sistema mais completo** |

---

## 1. Por que no repositório do CP3, e como isso não atrapalha a correção dele

O CP3 é o pipeline mais completo dos três — tem borda HTTP síncrona,
coreografia por fila e orquestração no núcleo, no mesmo sistema. Instrumentar
os três repositórios triplicaria o trabalho para observar as mesmas três coisas.

**Risco real:** a stack do CP3 está aplicada na AWS desde 03/09 esperando
correção, e o repositório é o que o professor tem em mãos. Commitar a
instrumentação na `main` muda a entrega do CP3 debaixo do corretor.

**Mitigação, antes de qualquer commit:**

```bash
git tag -a cp3-entrega -m "Estado entregue no Checkpoint 3, em 03/09/2026"
git push origin cp3-entrega
```

O README ganha uma nota no topo apontando a tag, para que quem corrige o CP3
saiba onde está o estado entregue. A `main` segue com o CP4.

---

## 2. Como cada exigência do enunciado é atendida

| Exigência | Como é atendida |
| --- | --- |
| Logging estruturado | módulo único `src/shared/observability.py`; envelope canônico com nível, correlation id, duração e cold start nos 7 handlers |
| Coleta de métricas | **EMF** (Embedded Metric Format) — a métrica sai no próprio log, sem chamada de API extra no caminho quente; mais as métricas nativas de Lambda, SQS, DynamoDB, Step Functions e API Gateway |
| Ferramentas nativas da nuvem | CloudWatch Logs, Metrics, Logs Insights, Alarms, Dashboards e AWS X-Ray — nada de terceiro |
| Evidências visuais | `docs/evidencias/` com prints numerados de carga real, cada um com legenda dizendo o que prova |
| Análise crítica de performance e custo | `docs/observabilidade.md`, com números medidos — não estimados |
| 2 a 3 otimizações fundamentadas | escolhidas **depois** de medir, entre cinco candidatas identificadas (§8) |
| Nenhuma credencial no repositório | `.gitignore` do CP3 mantido; varredura de segredos no histórico antes do push |
| README completo | seção de observabilidade + índice das evidências + resumo das otimizações |
| Resumo para a caixa de texto do Canvas | `docs/entrega-canvas.md`, pronto para copiar |

---

## 3. Diagnóstico — o que já existe e o que falta

| | CP3 hoje | Depois do CP4 |
| --- | --- | --- |
| Log JSON nos handlers | ✅ `print(json.dumps(fields))` em 6 dos 7 | ✅ envelope canônico, com nível |
| Nível de severidade | ❌ tudo indistinto | ✅ INFO / WARN / ERROR, filtrável na plataforma |
| Correlation id | ⚠️ inconsistente (`execution` em uns, `batch` em outros) | ✅ os mesmos campos em toda linha |
| Duração por estado | ❌ | ✅ medida no handler e emitida como métrica |
| Cold start | ❌ invisível | ✅ marcado e contado |
| Latência ponta a ponta | ❌ | ✅ `submitted_at` → gravação, medida no `persist` |
| Métricas de negócio | ❌ nenhuma | ✅ EMF (§5) |
| Dashboard CloudWatch | ❌ (o "dashboard" do repo é o painel web) | ✅ um, em Terraform |
| Alarmes | ❌ | ✅ cinco, em Terraform |
| Queries do Logs Insights | ❌ ad hoc | ✅ versionadas com `aws_cloudwatch_query_definition` |
| X-Ray | ❌ desligado por decisão registrada | ✅ ligado — o CP4 é o que justifica reverter |

O logging não está ausente: `print(json.dumps(fields))` **já é** log estruturado
e já é consultável por campo no Logs Insights. O que falta é nível, duração,
correlação consistente — e métrica, que não existe em lugar nenhum.

---

## 4. Logging — o envelope canônico

Hoje a função `log()` está **copiada em seis handlers**, cada um com campos ad
hoc. Passa a existir uma só, em `src/shared/observability.py`, com envelope fixo:

```json
{
  "event": "equation_validated",
  "level": "INFO",
  "service": "validate",
  "state": "Validate",
  "execution": "9c1d…",
  "batch_id": "b-3f9a1c",
  "request_id": "8f2e…",
  "attempt": 0,
  "cold_start": false,
  "duration_ms": 1.8,
  "a": 1, "b": -5, "c": 6
}
```

| Campo | De onde vem | Para que serve |
| --- | --- | --- |
| `event` | literal no handler | a chave de agrupamento em toda query |
| `level` | INFO / WARN / ERROR | filtro e alarme; hoje uma recusa e um sucesso são indistinguíveis |
| `service` | variável de ambiente `POWERTOOLS_SERVICE`-like, definida no Terraform | separa as 7 funções sem depender do nome do log group |
| `state` | `$$.State.Name`, já no payload | o mesmo handler `root` atende três estados — sem isto, ficam somados |
| `execution` | `meta.idempotency_key`, já existente | **o correlation id**: liga as ~7 linhas de uma mesma equação |
| `batch_id` | `meta.batch_id`, já existente | liga as N equações de uma mesma carga |
| `attempt` | `$$.State.RetryCount`, já no payload | separa a primeira tentativa das reentregas |
| `cold_start` | flag de módulo, `True` na primeira invocação | quantifica o custo de inicialização |
| `duration_ms` | medido no handler | o dado da análise de performance |

Os três primeiros campos de correlação **já trafegam no payload** — a ASL já
passa `state` e `retry_count` para toda Task. Não há mudança de contrato entre
estados: só passam a ser registrados.

**Uma decisão a validar na nuvem, não a assumir.** O runtime Python 3.13 tem
formato de log JSON nativo (`logging_config { log_format = "JSON" }`), que
adiciona nível, timestamp e requestId sozinho e permite filtrar por nível
**antes da ingestão** — economia direta de custo. O problema é que ele
**embrulha** o que sai no stdout dentro de um campo `message`, e o EMF precisa
do `_aws` na raiz da linha para o CloudWatch extrair a métrica.

Isso é conflito potencial entre as duas metades do checkpoint. **O ciclo 1
testa os dois formatos contra a conta real e decide com o resultado**; se o
JSON nativo quebrar o EMF, fica o formato TEXT com o envelope acima montado à
mão, que é o que já funciona hoje. A escolha e o motivo vão para o README —
não vou afirmar na especificação um comportamento que ainda não medi.

---

## 5. Métricas — EMF, e por que não `PutMetricData`

`PutMetricData` é uma chamada de API síncrona dentro do caminho quente: soma
latência a toda invocação, consome parte do timeout e pode falhar, obrigando a
tratar erro de telemetria dentro da lógica de negócio. O **EMF** resolve isso
escrevendo a métrica no próprio log; o CloudWatch a extrai do lado dele. Custo
de rede no handler: zero.

### Métricas emitidas

Namespace `Bhaskara/Orchestration`.

| Métrica | Unidade | Emitida por | Dimensões | O que responde |
| --- | --- | --- | --- | --- |
| `EquationsSubmitted` | Count | `submit` | — | volume de entrada |
| `ExecutionsStarted` | Count | `dispatcher` | — | quantas viraram execução |
| `ExecutionsDeduplicated` | Count | `dispatcher` | — | **idempotência camada 1 funcionando** |
| `PersistDuplicate` | Count | `persist` | — | idempotência camada 2 |
| `EquationsByDeltaSign` | Count | `delta` | `Sign` | distribuição dos três ramos do `Choice` |
| `ValidationRejected` | Count | `validate` | `Reason` | por que uma equação é recusada |
| `HandlerDuration` | Milliseconds | todos | `Service`, `State` | **p50/p95/p99 por estado** — a base da análise |
| `ColdStart` | Count | todos | `Service` | fração de invocações que pagam inicialização |
| `EndToEndLatency` | Milliseconds | `persist` | — | `submitted_at` → gravado: a latência que o usuário sentiria |
| `ChaosInjected` | Count | todos | `State` | separa falha injetada de falha real na análise |
| `RetryAttempt` | Count | todos | `State` | quantas invocações são reentrega |

Dead-letter não precisa de métrica customizada: `NumberOfMessagesSent` da fila
já é nativa e gratuita.

### A regra de cardinalidade

**`execution`, `batch_id`, `message_id` e `request_id` nunca são dimensão.** Uma
dimensão nova cria uma métrica nova, cobrada por métrica por mês — usar um id
como dimensão gera custo ilimitado a partir de volume ilimitado. Eles vão no
corpo do EMF como propriedade: continuam pesquisáveis no Logs Insights, sem
virar série temporal.

Dimensões usadas: `Service` (7 valores), `State` (9), `Sign` (3), `Reason` (~6).
Total de séries: **~15 métricas**, dentro de uma ordem de grandeza controlada.

---

## 6. X-Ray

O CP3 registrou a decisão de deixar o X-Ray desligado, com justificativa: "o
traço distribuído não acrescenta nada que o histórico da execução já não mostre
neste fluxo, e é cobrado por trace". O CP4 muda a premissa — o objeto do
checkpoint agora **é** a observabilidade, e o service map é a evidência visual
mais forte que a AWS produz de um pipeline composto.

| Onde | Mudança |
| --- | --- |
| State machine | `tracing_configuration { enabled = true }` |
| As 7 Lambdas | `tracing_config { mode = "Active" }` |
| IAM | `xray:PutTraceSegments` e `xray:PutTelemetryRecords` nas 7 roles e na role da state machine |

Custo: a camada gratuita cobre 100.000 traces registrados por mês; um demo de
100 equações gera ~100 traces. **Fica dentro do gratuito.**

A decisão anterior não é apagada do README — passa a ter data e motivo da
reversão. Um repositório que muda de ideia sem dizer que mudou perde o valor do
histórico.

---

## 7. Infraestrutura nova — `infra/observability.tf`

| Recurso | Quantidade | Detalhe |
| --- | --- | --- |
| `aws_cloudwatch_dashboard` | 1 | widgets em quatro faixas: entrada, fluxo, erros e custo |
| `aws_cloudwatch_metric_alarm` | 5 | DLQ com mensagem; execuções falhas; throttling de Lambda; idade da mensagem mais antiga na `orders`; 5xx na API |
| `aws_sns_topic` | 1 | destino dos alarmes; inscrição por e-mail opcional via variável, vazia por padrão |
| `aws_cloudwatch_query_definition` | 5 | queries do Logs Insights versionadas |
| `aws_cloudwatch_log_metric_filter` | 1 | conta `403` no access log da API — tentativa de uso sem chave |

As cinco queries salvas: p95 por estado · execuções por sinal de Δ · rastro
completo de uma execução pelo correlation id · cold starts por função · top
motivos de recusa.

**Impacto no custo fixo.** O CP3 afirmava "nenhum recurso com custo fixo". Com
o CP4 isso muda um pouco, e a afirmação precisa ser corrigida no README:

| Item | Camada gratuita | Depois dela |
| --- | --- | --- |
| Dashboard | 3 grátis | US$ 3,00/mês cada |
| Alarmes | 10 grátis | US$ 0,10/mês cada |
| Métricas customizadas | 10 grátis | US$ 0,30/mês cada — **~5 acima do limite ≈ US$ 1,50/mês** |
| X-Ray | 100k traces/mês | US$ 5,00/milhão |
| Logs | 5 GB/mês | US$ 0,50/GB ingerido |

Estimativa realista com uso de laboratório: **US$ 0 a 2,00/mês**, e zero se a
stack for destruída depois da correção.

---

## 8. A análise de otimização — cinco candidatas, três entregues

O enunciado pede 2 a 3 otimizações **fundamentadas**. Fundamentar significa
medir primeiro. Estas são as candidatas; **quais três entram no relatório
depende do que os números disserem** — inclusive a possibilidade de uma delas
se mostrar irrelevante e ser descartada com o número na mão.

| # | Candidata | O que se mede | Hipótese |
| --- | --- | --- | --- |
| 1 | **Uma Lambda por raiz no `Parallel`** | duração dos dois ramos vs. estado único; transições por execução | o `Parallel` custa 2 invocações + ~4 transições para uma conta que leva microssegundos; colapsar em um estado corta custo por execução com Δ>0. O CP3 já admite que a divisão é didática — aqui ela ganha o número |
| 2 | **`log level = ALL` + `include_execution_data` na state machine** | bytes ingeridos por execução | é o maior gerador de log do sistema; medir GB/1.000 execuções e propor `ERROR` fora de demonstração, com o US$/GB |
| 3 | **Memória de 128 MB nas tasks** | p95 de duração × preço por GB-s em 128 / 256 / 512 MB | mais memória pode reduzir o billed duration o suficiente para sair mais barato — ou não, e o número decide |
| 4 | **`maximum_concurrency = 2` no event source mapping** | tempo de drenagem de 200 equações; fração do tempo em backoff de throttling | quantificar o custo da quota de 10 execuções concorrentes e o ganho de pedir aumento de quota (gratuito) |
| 5 | **Cold start** | fração de invocações frias e o p95 delas | provisioned concurrency não cabe na quota de 10; se o número for alto, a resposta honesta pode ser "não há o que fazer nesta conta" — e isso também é um resultado |

Cada otimização entregue terá: o número medido, a mudança proposta, o ganho
estimado em latência e em dólares, e o custo da mudança. Nenhuma será proposta
sem medição — o enunciado pede "oportunidades reais", não uma lista de boas
práticas.

---

## 9. Evidências visuais

`docs/evidencias/`, com um `README.md` que diz o que cada print prova.

| # | Print | O que comprova |
| --- | --- | --- |
| 01 | Dashboard CloudWatch, carga em andamento | métricas nativas e customizadas juntas |
| 02 | Logs Insights: linha estruturada expandida | o envelope canônico com todos os campos |
| 03 | Logs Insights: rastro de uma execução pelo correlation id | as ~7 linhas de uma equação, em ordem |
| 04 | Logs Insights: p95 por estado | a base da otimização 1 e 3 |
| 05 | Métrica `EquationsByDeltaSign` | os três ramos do `Choice` em proporção |
| 06 | Métrica `ExecutionsDeduplicated` | idempotência funcionando sob carga |
| 07 | X-Ray service map | o pipeline inteiro, ponta a ponta |
| 08 | X-Ray trace de uma execução com retry | o backoff visível no tempo |
| 09 | Alarme em ALARM (DLQ com mensagem, via modo caos) | o alarme dispara de verdade |
| 10 | Step Functions: execução com o `Parallel` | a orquestração, para contexto |

Os prints saem de **carga real na stack aplicada**, não de mock. A captura usa
o Playwright do MCP contra o console da AWS — **isso exige você logado no
console**; é o único passo do plano que não roda sozinho.

---

## 10. Testes

A suíte do CP3 tem 258 casos e nenhum toca a AWS. O CP4 mantém a regra.

| Arquivo | Foco | Casos (estimativa) |
| --- | --- | --- |
| `test_observability.py` (novo) | envelope canônico: campos obrigatórios, níveis, cold start só na primeira, `duration_ms` numérico, JSON sem `NaN` | 24 |
| `test_metrics.py` (novo) | forma do EMF: `_aws` na raiz, timestamp em ms, dimensão declarada existe no corpo, unidade válida, **nenhum id como dimensão** | 20 |
| `test_*_handler.py` (7 existentes) | asserção nova: cada handler emite os eventos e métricas esperados | +21 |
| `test_asl_definition.py` | asserção nova: toda Task passa `state` e `retry_count` | +2 |
| `test_flow.py` | asserção nova: uma execução completa produz o rastro correlacionável ponta a ponta | +3 |
| **Total** | | **~328** |

O teste de cardinalidade é o mais importante da lista: é o que impede alguém de
adicionar `batch_id` como dimensão daqui a seis meses e descobrir a conta no
fim do mês.

---

## 11. Plano de execução — seis ciclos

Mesmo método dos anteriores: cada ciclo termina em commit semântico com a suíte
verde e uma nota em `docs/cycle-NN.md`. A numeração continua de onde o CP3
parou — os ciclos do CP4 são **08 a 13**, para que o histórico seja contínuo.

| Ciclo | Entrega | Critério de pronto |
| --- | --- | --- |
| **0** | tag `cp3-entrega` empurrada, nota no README | o estado corrigido do CP3 é recuperável por tag |
| **08** | `shared/observability.py`, 7 handlers migrados, decisão TEXT vs JSON validada na conta real | suíte verde; uma linha real do CloudWatch colada na nota do ciclo |
| **09** | EMF: as 11 métricas, duração, cold start, latência ponta a ponta | métrica aparecendo no console a partir de carga real |
| **10** | X-Ray nas Lambdas e na state machine, IAM ajustado | service map com os 7 componentes |
| **11** | `infra/observability.tf`: dashboard, 5 alarmes, 5 queries, metric filter | `terraform apply` limpo; alarme dispara com o modo caos |
| **12** | carga real, medição e captura das 10 evidências | os números das 5 candidatas na mão |
| **13** | `docs/observabilidade.md` com as 3 otimizações, README, `docs/entrega-canvas.md`, varredura de segredos, push | clone limpo roda; nenhum segredo no histórico |

---

## 12. Pipeline de entrega (regra global) — o que se aplica

| Etapa | Status | Observação |
| --- | --- | --- |
| 1. Versionamento | ✅ | commit semântico por ciclo |
| 2. Testes unitários | ✅ | ~328 casos, suíte verde por ciclo |
| 3. Build de imagem | ⛔ N/A | Lambdas em ZIP, stdlib pura — não há imagem |
| 4. Scan de imagem | 🔁 substituído | `trivy fs` no repositório + `checkov` no Terraform, como no CP3 |
| 5. Push para registry | ⛔ N/A | o artefato é o ZIP montado pelo `archive_file` |
| 6. Teste funcional | ✅ | carga real na AWS, ciclo 12 |
| 7. Teste E2E | ✅ | Playwright: painel + console da AWS na captura das evidências |

---

## 13. O que fica de fora, e por quê

- **Instrumentar o CP1 e o CP2.** Os três estilos arquiteturais já convivem no
  CP3; instrumentar os outros dois observaria as mesmas coisas em sistemas
  menores. Registrado como escolha, não como esquecimento.
- **Métricas de CloudWatch no painel web.** O painel lê SQS, Step Functions e
  DynamoDB. Buscar métrica do CloudWatch de dentro dele exigiria IAM novo e
  `GetMetricData` cobrado por requisição, a cada poll de 2 s. O dashboard
  CloudWatch já é a tela de métrica, e é o que o enunciado pede em print.
- **Alarme com notificação por e-mail ativa.** O tópico SNS é criado; a
  inscrição fica em variável vazia por padrão. Inscrever um e-mail exige
  confirmação manual fora do Terraform e não acrescenta nada à correção.
- **Corrigir o débito da credencial root.** Continua registrado como limitação
  conhecida. É trabalho de IAM, não de observabilidade — misturar os dois
  atrapalharia a revisão dos dois.

---

## 14. Decisões em aberto

1. **A stack do CP3 no ar.** O ciclo 12 exige carga real. Se a nota do CP3 já
   saiu, dá para aplicar, medir e destruir no mesmo dia. Se ainda não saiu, a
   stack fica de pé com a instrumentação — que só melhora o que o professor vê.
2. **Prazo.** Venceu em 10/09. O plano acima assume a extensão que você vai
   negociar; se ela não vier, os ciclos 08, 09, 11, 12 e 13 são o mínimo que
   fecha o enunciado, e o 10 (X-Ray) sai.
