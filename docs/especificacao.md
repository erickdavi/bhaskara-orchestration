# Especificação — Checkpoint 3: Orquestração e Composição de Serviços

**Projeto:** `bhaskara-orchestration` · **Provedor:** AWS · **Região:** `us-east-1`
**Conta:** <numero-da-conta> · **Status:** especificação para aprovação (nenhum código escrito ainda)

Terceiro repositório da família, **independente** dos anteriores:

| | Checkpoint | Repositório | Arquitetura |
| --- | --- | --- | --- |
| CP1 | função serverless | `bhaskara-api` | síncrona: API Gateway → Lambda → resposta HTTP |
| CP2 | event-driven | `bhaskara-events` | coreografia: SQS → worker → results/DLQ |
| **CP3** | **orquestração** | **`bhaskara-orchestration`** | **coreografia na borda + orquestração no núcleo (Step Functions)** |

Nenhum dos dois repositórios anteriores é alterado. A única coisa reaproveitada
é a regra de negócio (`calculator.py`), copiada literalmente — o que muda é
tudo ao redor dela.

---

## 1. Como cada exigência do enunciado é atendida

O enunciado pede Google Cloud Workflows; a entrega usa **AWS Step Functions**,
que é o serviço equivalente e está explicitamente permitido ("GCP, AWS ou
Azure").

| Exigência do enunciado | Como é atendida |
| --- | --- |
| Orquestração de serviços | State machine **Step Functions Standard** compondo 5 Lambdas |
| Fluxo estruturado em **YAML** | `workflow/bhaskara.asl.yaml` versionado; o Terraform converte com `jsonencode(yamldecode(file(...)))` no `apply` — o YAML é a fonte da verdade, não um subproduto |
| Chamar cada função na ordem correta | `validate → delta → Choice(Δ) → root(s) → persist` |
| Gerenciar respostas | contrato JSON explícito entre estados, com `Parameters`, `ResultSelector` e `ResultPath` (§4) |
| Regras de idempotência | duas camadas: nome determinístico de execução + `ConditionExpression` no DynamoDB (§5) |
| Retry | bloco `Retry` com backoff exponencial em toda Task, diferenciando erro transitório de permanente (§6) |
| Dead-letter queues | `Catch` → integração direta com SQS `dead-letter`, **mais** a DLQ nativa por `redrive_policy` na fila de entrada (§6) |
| Nada de credencial no repositório | `.gitignore` cobrindo `*.tfstate*`, `*.tfvars`, `*.json` de credencial, `.terraform/`, `.env`, `*.zip`; chave de API gerada pelo Terraform e lida por `terraform output` |
| README com execução local | dois caminhos: simulador em Python puro e Step Functions Local em Docker (§8) |
| URL da função ativa entregue em separado | os endpoints saem por `terraform output`; nenhuma URL vai para o README |

---

## 2. Arquitetura

```text
                 ┌──────────────────────────────────────────┐
                 │   Painel — S3 privado + CloudFront/OAC   │
                 │   quantidade · duplicadas% · caos%       │
                 └──────┬─────────────────────────┬─────────┘
      POST /orders      │                         │      GET /flow
                 ┌──────▼─────────────────────────▼─────────┐
                 │  API Gateway HTTP API — x-api-key, 5 rps │
                 └──────┬─────────────────────────┬─────────┘
                 ┌──────▼──────┐           ┌──────▼──────┐
                 │   submit    │           │   status    │
                 └──────┬──────┘           └──────┬──────┘
                        │ SendMessageBatch        │ agrega 3 fontes
                 ┌──────▼──────┐                  │
   COREOGRAFIA   │ SQS orders  │                  │
   ─ ─ ─ ─ ─ ─ ─ └──────┬──────┘─ ─ ─ ─ ─ ─ ─ ─ ─ ┼ ─ ─ ─ ─ ─ ─ ─ ─
                        │ event source mapping    │
                 ┌──────▼──────┐                  │
                 │ dispatcher  │ StartExecution   │
                 └──────┬──────┘ (nome = chave)   │
   ORQUESTRAÇÃO         │                         │
   ═════════════════════▼═════════════════════════│══════════════
     ┌────────────────────────────────────────┐   │
     │ Validate ──► Delta ──► Choice(Δ)       │   │ ListExecutions
     │                │                       │   │ GetExecutionHistory
     │      Δ>0 ──► Parallel ┬─ RootX1 ─┐     │   │
     │                       └─ RootX2 ─┤     │   │
     │      Δ=0 ──► RootDouble ─────────┤     │   │
     │      Δ<0 ──► NoRealRoots ────────┤     │   │
     │                                  ▼     │   │
     │                              Persist   │   │
     │  Retry(backoff) em toda Task           │   │
     │  Catch(States.ALL) ──► DeadLetter      │   │
     └───────┬──────────────────────┬─────────┘   │
   ══════════│══════════════════════│═════════════│══════════════
             │ PutItem              │ SendMessage │
       ┌─────▼──────────┐    ┌──────▼──────────┐  │
       │ DynamoDB       │    │ SQS dead-letter │◄─┘ (leitura)
       │ resultados +   │◄───────────────────────────┘
       │ idempotência   │
       └────────────────┘
```

**Onde está a coreografia e onde está a orquestração**

| Trecho | Estilo | Por quê |
| --- | --- | --- |
| painel → API → `submit` → `orders` | coreografia | quem publica não sabe quem consome; absorve pico e desacopla a borda |
| `orders` → `dispatcher` | fronteira | é aqui que o evento vira execução: o dispatcher traduz mensagem em `StartExecution` |
| dentro da state machine | orquestração | a ordem é explícita, declarada e versionada; o estado vive no orquestrador, não espalhado entre funções |
| `dead-letter`, DynamoDB, painel | coreografia | consumidos por quem quiser, sem o fluxo saber que existem |

A alternância é o ponto pedagógico da entrega: o mesmo cálculo do CP2, que lá
era resolvido por **uma** função reagindo a **uma** mensagem, aqui é composto
por **cinco** funções cuja ordem está declarada em YAML.

---

## 3. Componentes

### Funções Lambda (Python 3.13, arm64, ZIP, apenas stdlib + boto3 do runtime)

| Função | Acionada por | Responsabilidade | Memória / timeout |
| --- | --- | --- | --- |
| `submit` | `POST /orders` | valida a requisição, gera N equações e publica em lotes de 10 na `orders` | 256 MB / 30 s |
| `dispatcher` | ESM da `orders` (lote 10) | por mensagem: monta o input da execução e chama `StartExecution` com nome determinístico | 256 MB / 30 s |
| `validate` | Task | normaliza e valida os coeficientes; erro permanente → `InvalidEquation` | 128 MB / 10 s |
| `delta` | Task | calcula Δ = b² − 4ac e classifica o sinal | 128 MB / 10 s |
| `root` | Task (×2 no `Parallel`, ×1 em Δ=0) | calcula **uma** raiz (`x1`, `x2` ou `double`) pela forma numericamente estável | 128 MB / 10 s |
| `persist` | Task | `PutItem` condicional no DynamoDB e publicação do desfecho | 128 MB / 10 s |
| `status` | `GET /flow` | agrega SQS + Step Functions + DynamoDB no payload do painel | 256 MB / 15 s |

`DeadLetter` **não é uma Lambda**: é a integração direta
`arn:aws:states:::sqs:sendMessage`. Numa conta com 10 execuções concorrentes no
total, cada Lambda a menos no caminho de erro é concorrência preservada para o
caminho feliz.

### Demais recursos

| Recurso | Nome | Observação |
| --- | --- | --- |
| `aws_sfn_state_machine` | `…-flow` | tipo **STANDARD**, logging `ALL` no CloudWatch, X-Ray desligado (custo) |
| `aws_sqs_queue` | `…-orders` | entrada; visibility 90 s, long polling 20 s, SSE, `redrive_policy` após 3 entregas |
| `aws_sqs_queue` | `…-dead-letter` | destino do `Catch` **e** do redrive nativo; retenção 14 dias |
| `aws_dynamodb_table` | `…-results` | `PAY_PER_REQUEST`, PK `pk` (chave de idempotência), TTL 24 h, GSI por `batch_id` para o painel |
| `aws_apigatewayv2_*` | `…-dev` | `POST /orders`, `GET /flow`, throttling 5 rps / burst 10 |
| `aws_s3_bucket` + CloudFront | `…-dashboard-<conta>` | bucket privado, OAC, sem chave no bundle |
| `aws_iam_role` ×8 | — | uma por função + uma da state machine, todas com policy inline e ARN restrito |
| `aws_cloudwatch_log_group` ×8 | — | retenção 7 dias |

Estimativa: **~55 recursos gerenciados**, nenhum com custo fixo.

---

## 4. Contrato entre os estados

Input da execução, montado pelo `dispatcher`:

```json
{
  "equation": { "a": 1, "b": -5, "c": 6 },
  "meta": {
    "batch_id": "b-3f9a1c",
    "message_id": "6a7f…",
    "idempotency_key": "9c1d…",
    "submitted_at": 1788012345678,
    "chaos": { "state": "Delta", "fails": 2 }
  }
}
```

| Estado | Recebe | Devolve | Onde grava |
| --- | --- | --- | --- |
| `Validate` | `$.equation`, `$$.State.RetryCount` | `{a, b, c}` normalizados | `$.validated` |
| `Delta` | `$.validated` | `{value, sign}` — `sign` ∈ `positive` \| `zero` \| `negative` | `$.delta` |
| `RootX1` / `RootX2` | `$.validated`, `$.delta`, `label` | `{label, value}` | ramo do `Parallel` |
| `CollectRoots` (`Pass`) | array do `Parallel` | `{roots: [...]}` via `ResultSelector` | `$.result` |
| `RootDouble` | `$.validated`, `$.delta` | `{roots: [{label:"x1"},{label:"x2"}]}` | `$.result` |
| `NoRealRoots` (`Pass`) | — | `{roots: []}` | `$.result` |
| `Persist` | `$.validated`, `$.delta`, `$.result`, `$.meta` | `{stored, duplicate, item}` | `$.persisted` |
| `DeadLetter` | `$.error`, `$.meta`, payload original | — | SQS `dead-letter` |

`$$.State.RetryCount` entra no payload de toda Task de propósito: é o que
permite ao modo caos falhar exatamente nas N primeiras tentativas e ter sucesso
na seguinte — retry observável e **determinístico**, não sorteado.

Cada raiz numa Lambda separada dentro do `Parallel` é uma decisão de
**demonstração**, não de desempenho: calcular duas raízes não justifica duas
invocações. O que justifica é mostrar o `Parallel` funcionando, com dois ramos
concorrentes convergindo para o mesmo `Persist`. Isso fica **registrado como
tal no README** — entregar como se fosse otimização seria desonesto.

---

## 5. Idempotência — duas camadas

**Chave:** `sha256(batch_id + ":" + json_canônico(equation))`, 40 caracteres.
O `batch_id` entra na chave para que duplicatas **dentro de uma carga** sejam
absorvidas sem que uma nova carga com a mesma equação seja recusada.

| Camada | Onde | Mecanismo | O que protege |
| --- | --- | --- | --- |
| 1 | `dispatcher` | `StartExecution(name = "eq-" + chave)`; o Standard recusa nome repetido por 90 dias com `ExecutionAlreadyExists` | reentrega da SQS (at-least-once) e duplicata na própria carga: a mensagem é confirmada sem iniciar segunda execução |
| 2 | `Persist` | `PutItem` com `ConditionExpression: attribute_not_exists(pk)`; em `ConditionalCheckFailed`, lê o item existente e devolve `duplicate: true` | qualquer caminho que escape da camada 1 — retry após timeout parcial, replay manual |

O painel expõe um controle **"% duplicadas"** que faz o `submit` repetir
equações de propósito. O contador *"N execuções evitadas por idempotência"*
sobe ao vivo — é a prova visual de que a regra funciona, e não uma afirmação no
README.

---

## 6. Falhas: retry e dead-letter

A distinção do CP2 entre falha **permanente** e **transitória** é preservada, agora
declarada em YAML:

```yaml
Retry:
  - ErrorEquals: [ "InvalidEquation" ]      # permanente
    MaxAttempts: 0                          # nunca reentregar
  - ErrorEquals: [ "Lambda.TooManyRequestsException",
                   "Lambda.ServiceException",
                   "States.TaskFailed", "States.Timeout" ]
    IntervalSeconds: 1
    MaxAttempts: 3
    BackoffRate: 2.0
    JitterStrategy: FULL
Catch:
  - ErrorEquals: [ "States.ALL" ]
    ResultPath: "$.error"
    Next: DeadLetter
```

| | Permanente | Transitória |
| --- | --- | --- |
| Exemplos | `a = 0`, coeficiente ausente, JSON malformado, overflow | throttling de Lambda (**a conta tem 10 execuções concorrentes**), indisponibilidade momentânea, bug |
| Reentregar ajuda? | não | sim |
| O que acontece | `MaxAttempts: 0` → vai direto para `DeadLetter` com o motivo | 3 tentativas com intervalo 1 s, 2 s, 4 s + jitter; só então `DeadLetter` |
| Como chega na `dead-letter` | `Catch` → `sqs:sendMessage` com `error`, `cause` e o input original | idem, após esgotar o `Retry` |

Há ainda a **DLQ nativa** da `orders` (`maxReceiveCount: 3`): se o próprio
`dispatcher` falhar antes de iniciar a execução, a SQS reentrega e depois move
a mensagem — a mesma fila `dead-letter` recebe os dois caminhos, com o campo
`source` distinguindo `workflow` de `redrive`.

**Modo caos** (`"chaos_ratio"` no `POST /orders`): marca uma fração das
equações para falhar em um estado sorteado, N vezes. `fails ≤ 3` demonstra o
retry se recuperando; `fails > 3` demonstra o esgotamento e a dead-letter. É o
que torna o painel uma demonstração, e não uma tela verde.

---

## 7. O painel

Estático no S3 privado, servido por CloudFront com OAC, **sem chave no bundle**
(digitada pelo operador e guardada só no `localStorage`) — o mesmo modelo do
CP2, que já passou por revisão de segurança.

Três blocos:

1. **Diagrama do fluxo, ao vivo.** SVG desenhado à mão com um nó por estado e
   por fila. Cada nó mostra o contador de execuções que passaram por ele e
   pulsa quando há atividade na janela atual. As arestas se acendem no sentido
   do trânsito. O ramo tomado pelo `Choice` fica evidente: com carga variada,
   os três caminhos acendem em proporções diferentes.
2. **Timeline por execução.** Ao clicar num nó ou numa execução da lista, o
   passo-a-passo real vindo de `GetExecutionHistory`: estado, horário,
   duração, e o dado que saiu dele (`Δ=49`, `x1=3`). Uma execução que sofreu
   retry mostra as tentativas empilhadas com o backoff entre elas; uma que
   falhou mostra onde parou e o motivo que foi para a dead-letter.
3. **Contadores e listas.** Em execução / concluídas / falhas / duplicadas
   evitadas, profundidade das filas, últimos resultados legíveis
   (`6x² + 36x + 54 = 0 → x₁=-3 x₂=-3`) e as mensagens da dead-letter com o
   motivo — sem consumi-las.

Fonte dos dados: `GET /flow`, que combina `GetQueueAttributes` (2 filas),
`ListExecutions` + `GetExecutionHistory` (últimas N) e uma `Query` no GSI do
DynamoDB. Poll de 2 s, com cursor para trazer só o incremento.

> **Nomes de estado são contrato de interface.** O painel mapeia nome de estado
> → nó do diagrama. Um teste falha se a ASL tiver um estado que o diagrama não
> conhece, para que renomear um estado nunca quebre o painel em silêncio.

---

## 8. Execução local — os dois caminhos

### 8.1 Caminho padrão: simulador em Python puro (sem Docker, sem AWS, sem credencial)

```bash
git clone https://github.com/erickdavi/bhaskara-orchestration.git
cd bhaskara-orchestration
./run.sh demo              # executa o fluxo inteiro no terminal
./run.sh web               # painel completo em http://localhost:8000
./run.sh                   # a suíte de testes
```

O simulador não imita o fluxo: ele **interpreta o mesmo
`workflow/bhaskara.asl.yaml`** que o Terraform envia para a AWS. Um mini-engine
ASL em `local/engine.py` (stdlib apenas) implementa o subconjunto usado —
`Task`, `Choice`, `Parallel`, `Pass`, `Succeed`, `Fail`, `Retry`, `Catch`,
`Parameters`, `ResultSelector`, `ResultPath`, JSONPath simples e o objeto de
contexto `$$` — e resolve cada `Task` chamando **o handler real**, em processo.
SQS e DynamoDB viram estruturas em memória.

> Um teste percorre a ASL e falha se ela usar qualquer construção que o engine
> não suporte. É o que impede o local e a nuvem de divergirem: não dá para
> adicionar um `Map` à state machine sem que a suíte cobre o engine também.

Saída esperada:

```text
Fluxo   validate → delta → Choice(Δ) → roots → persist
Carga   80 equações · 10% duplicadas · 10% caos

Execuções iniciadas                        72
  evitadas por idempotência (camada 1)      8
Desfechos
  concluídas                               67
    Δ > 0  duas raízes                      31
    Δ = 0  raiz dupla                       18
    Δ < 0  sem raízes reais                 18
  retry e recuperadas                        4
  enviadas à dead-letter                     5
    InvalidEquation (permanente)             3
    caos esgotou o retry (transitória)       2
```

### 8.2 Caminho avançado: Step Functions Local + LocalStack (Docker)

```bash
docker compose up -d          # amazon/aws-stepfunctions-local + localstack
./scripts/local-aws.sh up     # cria filas, tabela e funções e registra a ASL
./scripts/local-aws.sh demo 50
docker compose down -v
```

Executa a **mesma ASL** no motor oficial da AWS, contra Lambdas, SQS e DynamoDB
do LocalStack. Serve para confirmar que o engine em Python não diverge do
comportamento real — as diferenças, se aparecerem, viram teste.

Fica documentado como **opcional**: exige Docker e ~1,5 GB de imagens, e o
enunciado do professor descreve um `clone + install + start`. O caminho padrão
é o §8.1, que roda em qualquer máquina com Python 3.9+.

---

## 9. Testes

Nenhum teste toca a AWS. Clientes `boto3` substituídos por dublês em fixture
`autouse`, para que um teste que esquecesse a fixture não publicasse de verdade.

| Arquivo | Foco | Casos (estimativa) |
| --- | --- | --- |
| `test_calculator.py` | regra matemática (cópia literal do CP1, com os 18 testes) | 18 |
| `test_validate_handler.py` | normalização, tipos, `bool`, `NaN`/`Infinity`, `InvalidEquation` | 26 |
| `test_delta_handler.py` | Δ, classificação do sinal, overflow, modo caos por `RetryCount` | 20 |
| `test_root_handler.py` | estabilidade numérica, ordem das raízes, rótulos, raiz dupla | 24 |
| `test_persist_handler.py` | `PutItem` condicional, `ConditionalCheckFailed`, TTL, chave | 22 |
| `test_dispatcher_handler.py` | nome determinístico, `ExecutionAlreadyExists`, `batchItemFailures` | 24 |
| `test_submit_handler.py` | chave de API, validação, lotes, duplicadas, caos, orçamento de tempo | 30 |
| `test_status_handler.py` | agregação, parsing do histórico, cursor, espiada na dead-letter | 28 |
| `test_asl_definition.py` | YAML válido; todo `Next` aponta para estado existente; toda Task tem `Retry` e `Catch`; nenhum estado órfão; todo estado tem nó no painel; o JSON renderizado bate com `yamldecode` | 18 |
| `test_workflow_engine.py` | `Choice`, `Parallel`, `Retry` com backoff, `Catch`, JSONPath, `$$` | 30 |
| `test_local_simulator.py` | ponta a ponta em memória; a contabilidade fecha | 10 |
| **Total** | | **~250** |

---

## 10. Segurança

- **Nenhuma credencial no repositório.** `.gitignore` cobrindo `*.tfstate*`,
  `*.tfvars`, `.terraform/`, `.env`, `*.zip`, `*credentials*.json`,
  `*service-account*.json`. As credenciais AWS vêm do ambiente.
- **Chave de API** de 40 caracteres gerada por `random_password`, comparada com
  `hmac.compare_digest`, verificada **antes** do corpo, com falha fechada. Sai
  só por `terraform output -raw api_key`.
- **IAM: oito papéis, nenhum com permissão do outro**, policies inline com ARN
  restrito — nenhuma managed policy (elas concedem sobre `"*"`), nenhum
  `"Resource": "*"`. A role da state machine só pode invocar as cinco Lambdas
  do fluxo e publicar na `dead-letter`; `status` tem apenas verbos de leitura,
  incluindo `ReceiveMessage` na dead-letter **sem** `DeleteMessage`.
- **Painel** em bucket privado, servido só via CloudFront/OAC; chave nunca no
  bundle.
- **Teto de carga** de 2.000 equações por requisição (menor que os 5.000 do
  CP2: aqui cada equação vira uma execução, e o teto protege custo e
  concorrência) + throttling de 5 rps no stage.
- **DynamoDB** com SSE padrão e TTL de 24 h — dado de laboratório não fica.
- **Débito registrado:** as credenciais locais usadas no `apply` são da **root
  account**. O correto é um IAM user dedicado com policy restrita; vai
  documentado no README como limitação conhecida, não escondido.

---

## 11. Custo

| Serviço | Consumo de um demo de 100 equações | Custo |
| --- | --- | --- |
| Step Functions Standard | ~100 execuções × ~9 transições = 900 | free tier cobre 4.000/mês; acima disso US$ 0,025/1.000 → **US$ 0,02** |
| Lambda | ~600 invocações de 128 MB | free tier (1 M/mês) |
| SQS | ~250 requests | free tier (1 M/mês) |
| DynamoDB on-demand | ~100 writes, ~200 reads | free tier |
| CloudFront + S3 | painel | < US$ 0,01 |
| **Total por demo** | | **≈ US$ 0,02–0,05** |

O default do painel é **50 equações**, não 1.000: com uma execução por equação,
a transição é a unidade de custo. O teto é 2.000 e o README diz o que cada
volume custa.

**Concorrência é o limite real, não o preço.** A conta tem 10 execuções
simultâneas no total, compartilhadas com CP1 e CP2 — e a AWS não permite
reservar concorrência quando o limite é 10 (é preciso deixar 10 não
reservadas). O controle fica em outro lugar:

- `maximum_concurrency = 2` no event source mapping da `orders`;
- `Retry` em `Lambda.TooManyRequestsException` em toda Task, para que o
  throttling vire espera e não falha;
- volumes pequenos por padrão.

Isso vai no README como decisão de arquitetura, com o número da conta como
justificativa.

---

## 12. Estrutura do repositório

```text
bhaskara-orchestration/
├── README.md                      # entrega: sem URL pública, com o passo a passo local
├── run.sh                         # testes · demo · painel local
├── docker-compose.yml             # Step Functions Local + LocalStack (opcional)
├── conftest.py                    # sys.path dos testes = sys.path da Lambda
├── requirements.txt
├── workflow/
│   └── bhaskara.asl.yaml          # a state machine — fonte da verdade
├── src/
│   ├── shared/
│   │   ├── calculator.py          # cópia literal do CP1
│   │   ├── api_auth.py            # cópia do CP2
│   │   └── idempotency.py         # a chave, num lugar só
│   └── handlers/
│       ├── submit/{handler,generator}.py
│       ├── dispatcher/handler.py
│       ├── validate/handler.py
│       ├── delta/handler.py
│       ├── root/handler.py
│       ├── persist/handler.py
│       └── status/handler.py
├── local/
│   ├── engine.py                  # interpretador ASL (stdlib)
│   ├── doubles.py                 # SQS, DynamoDB e Step Functions em memória
│   ├── simulator.py               # a demonstração no terminal
│   └── server.py                  # serve o painel + /flow local
├── tests/                         # ~250 casos, nenhum toca a AWS
├── infra/                         # Terraform
│   ├── versions.tf main.tf variables.tf outputs.tf
│   ├── sqs.tf dynamodb.tf iam.tf
│   ├── lambdas.tf                 # as 7 funções, via for_each
│   ├── statemachine.tf            # yamldecode → jsonencode
│   ├── apigateway.tf dashboard.tf
├── web/                           # painel: index.html, styles.css, app.js, flow.js
├── scripts/
│   ├── local-aws.sh               # sobe o stack no LocalStack
│   ├── send-test-message.sh       # publica direto na fila
│   └── run-execution.sh           # StartExecution direto, sem passar pela fila
└── docs/
    ├── especificacao.md           # este documento
    └── cycle-01.md … cycle-07.md  # uma nota por ciclo
```

---

## 13. Plano de execução

Sete ciclos, cada um terminando em commit semântico com a suíte verde — o mesmo
método do CP2, que produziu histórico legível.

| Ciclo | Entrega | Critério de pronto |
| --- | --- | --- |
| 1 | Esqueleto, `calculator.py`, `validate` e `delta`, engine ASL mínimo (`Task`) | `./run.sh demo` executa `validate → delta` a partir do YAML |
| 2 | `Choice`, `Parallel`, `root`, `NoRealRoots` | os três ramos de Δ percorridos e cobertos por teste |
| 3 | DynamoDB, `persist`, idempotência nas duas camadas | duplicata não gera segundo item e devolve `duplicate: true` |
| 4 | `Retry`, `Catch`, `DeadLetter`, modo caos | caos com `fails=2` se recupera; `fails=5` cai na dead-letter |
| 5 | `submit`, `orders`, `dispatcher`, API Gateway, IAM | `POST /orders` gera N execuções; `terraform apply` limpo |
| 6 | `status`, painel com diagrama animado e timeline | fluxo visível ao vivo, incluindo retry e ramo tomado |
| 7 | LocalStack/SFN Local, hardening, README, destroy/apply do zero | clone limpo roda; `destroy` não deixa recurso |

Antes de `git push` público: varredura por segredo no histórico inteiro, não só
no working tree.

---

## 14. Pipeline de entrega (regra global) — o que se aplica

| Etapa | Status | Observação |
| --- | --- | --- |
| 1. Versionamento | ✅ | commit semântico por ciclo |
| 2. Testes unitários | ✅ | ~250 casos, suíte verde por ciclo |
| 3. Build de imagem | ⛔ **N/A** | Lambdas em ZIP, código stdlib puro, zero dependências — não há imagem |
| 4. Scan de imagem | 🔁 **substituído** | `trivy fs` no repositório + `checkov`/`tfsec` no Terraform |
| 5. Push para registry | ⛔ **N/A** | o artefato é o ZIP montado pelo `archive_file` no `plan` |
| 6. Teste funcional | ✅ | carga real na AWS, execuções conferidas no console |
| 7. Teste E2E | ✅ | Playwright no painel: gerar carga, ver o diagrama acender, abrir a timeline |

As etapas 3 e 5 não se aplicam porque não existe imagem de container no
projeto; 4 é cumprida por controle equivalente. Escolha registrada em §Empacotamento
das decisões, não omitida.

---

## 15. Decisões em aberto (nenhuma bloqueia o início)

1. **Retenção do CP1 e CP2 na conta.** Continuam de pé; nada será alterado.
2. **Nome da branch e visibilidade do repositório.** Assumo `main` e repositório
   **público** (o enunciado exige), criado só no ciclo 7, depois da varredura de
   segredos.
3. **X-Ray.** Desligado por padrão. Se você quiser o traço distribuído no
   painel, é uma variável e ~US$ 0,000005 por trace.
