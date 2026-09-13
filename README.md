# Checkpoints 3 e 4 — orquestração, e a observabilidade dela

Uma equação do segundo grau entra por uma fila, atravessa **cinco funções
Lambda cuja ordem está declarada em YAML**, e sai gravada — ou recusada, com o
motivo anexado. A orquestração é feita pelo **AWS Step Functions**; um painel
web mostra o fluxo acontecendo, estado por estado. Cada passo desse caminho é
**medido**: log estruturado com correlação, métricas de negócio, traço
distribuído, dashboard e alarmes.

> **Este repositório carrega duas entregas.**
>
> O **Checkpoint 3 — orquestração** é o sistema: Step Functions compondo cinco
> Lambdas a partir de um YAML versionado. O estado exato entregue nele está na
> tag [`cp3-entrega`](https://github.com/erickdavi/bhaskara-orchestration/tree/cp3-entrega).
>
> O **Checkpoint 4 — observabilidade** instrumentou esse mesmo pipeline, como o
> enunciado pede — ele não pede um sistema novo, pede para instrumentar o que já
> existe. O relatório com a análise de performance, de custo e as três
> otimizações está em **[`docs/observabilidade.md`](docs/observabilidade.md)**,
> as evidências em [`docs/evidencias/`](docs/evidencias/) e os números medidos
> em [`docs/evidencias/medicoes.md`](docs/evidencias/medicoes.md).
>
> O [Checkpoint 1](https://github.com/erickdavi/bhaskara-api) é uma API
> serverless **síncrona** e o [Checkpoint 2](https://github.com/erickdavi/bhaskara-events)
> é uma arquitetura **orientada a eventos**, cada um em seu repositório.

```bash
git clone https://github.com/erickdavi/bhaskara-orchestration.git
cd bhaskara-orchestration
./run.sh demo          # o fluxo inteiro no terminal, sem AWS
./run.sh web           # o painel em http://localhost:8000, sem AWS
./run.sh               # 329 testes, sem AWS
```

## Provedor utilizado

* **AWS** — Step Functions, Lambda, SQS, DynamoDB, API Gateway, CloudFront e S3.

O enunciado pede Google Cloud Workflows; a entrega usa o serviço equivalente da
AWS, dentro do que o enunciado autoriza ("GCP, AWS ou Azure"). A definição do
fluxo é escrita em **YAML**, como pedido, e convertida para JSON no `apply`.

## Índice

- [Como rodar localmente](#como-rodar-localmente)
- [Arquitetura](#arquitetura)
- [Coreografia e orquestração no mesmo sistema](#coreografia-e-orquestração-no-mesmo-sistema)
- [O fluxo, estado por estado](#o-fluxo-estado-por-estado)
- [Idempotência em duas camadas](#idempotência-em-duas-camadas)
- [Retry e dead-letter](#retry-e-dead-letter)
- [O modo caos](#o-modo-caos)
- [O painel](#o-painel)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Implantando na AWS](#implantando-na-aws)
- [A API](#a-api)
- [Validando pela linha de comando](#validando-pela-linha-de-comando)
- [Rodando contra serviços AWS emulados](#rodando-contra-serviços-aws-emulados)
- [Testes](#testes)
- [Observabilidade](#observabilidade)
- [Segurança](#segurança)
- [Custos](#custos)
- [Limpeza](#limpeza)
- [Limitações conhecidas](#limitações-conhecidas)
- [Decisões de arquitetura](#decisões-de-arquitetura)

## Como rodar localmente

**Não precisa de credenciais AWS, nem de conta na nuvem, nem de Docker.** Nada
sai da sua máquina.

### Pré-requisitos

* Python 3.9 ou superior
* Terminal de comandos aberto

### Passo a passo

1. Clone o repositório para sua máquina:

   ```bash
   git clone https://github.com/erickdavi/bhaskara-orchestration.git
   ```

2. Entre na pasta do projeto:

   ```bash
   cd bhaskara-orchestration
   ```

3. Rode a demonstração do fluxo orquestrado:

   ```bash
   ./run.sh demo
   ```

   O script cria o ambiente virtual e instala as dependências na primeira
   execução.

4. Abra o painel, com o fluxo acontecendo ao vivo:

   ```bash
   ./run.sh web
   ```

   Depois abra <http://localhost:8000> no navegador e clique em **Disparar**.

5. Rode os testes:

   ```bash
   ./run.sh
   ```

### O que a demonstração faz

`./run.sh demo` executa o fluxo inteiro em memória, com **o mesmo código que
roda na nuvem**: o handler `submit` gera e publica as equações, o `dispatcher`
traduz cada mensagem em uma execução, e a state machine de
`workflow/bhaskara.asl.yaml` é interpretada estado a estado — com `Choice`,
`Parallel`, `Retry` e `Catch` de verdade. Só o transporte é substituído: SQS,
DynamoDB e o próprio Step Functions viram estruturas em memória.

```text
Fluxo   Validate -> Delta -> Choice(delta) -> Root(s) -> Persist
Carga   80 equacoes  ·  10% invalidas  ·  10% duplicadas  ·  10% com caos

Mensagens publicadas na fila orders                80
Execucoes iniciadas                                66
  evitadas por idempotencia (camada 1)             14

Desfechos
  concluidas                                       59
    delta > 0   duas raizes                        19
    delta = 0   raiz dupla                         21
    delta < 0   sem raizes reais                   19
  gravadas                                         59
  recuperadas pelo retry                            4
  enviadas a dead-letter                            7
    InvalidEquation (permanente, sem retry)         5
    TransientFailure (caos esgotou o retry)         2
  --------------------------------------------
  total de mensagens                               80
```

A conta fecha: **publicadas = iniciadas + evitadas pela idempotência**, e
**iniciadas = concluídas + recusadas**. Aceita parâmetros:

```bash
./run.sh demo 200 --duplicates 25 --chaos 30 --seed 7
./run.sh demo 50 --invalid 0 --chaos 0        # só caminho feliz
./run.sh demo 40 --verbose                    # com os logs de cada função
```

O painel local (`./run.sh web`) serve os **mesmos arquivos** que o CloudFront
serve na nuvem e responde às **mesmas rotas**, chamando os mesmos handlers. Ele
não tem uma "versão local": se funciona ali, é o mesmo código que roda aqui.

## Arquitetura

```text
                 ┌──────────────────────────────────────────┐
                 │   Painel — S3 privado + CloudFront/OAC   │
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
     │ Validate ──► Delta ──► Choice(delta)   │   │  FilterLogEvents
     │                │                       │   │  ListExecutions
     │    delta>0 ─► Parallel ┬ RootX1 ─┐     │   │  GetExecutionHistory
     │                        └ RootX2 ─┤     │   │
     │    delta=0 ─► RootDouble ────────┤     │   │
     │    delta<0 ─► NoRealRoots ───────┤     │   │
     │                                  ▼     │   │
     │                              Persist   │   │
     │  Retry(backoff) em toda Task           │   │
     │  Catch(States.ALL) ──► DeadLetter      │   │
     └───────┬──────────────────────┬─────────┘   │
   ══════════│══════════════════════│═════════════│══════════════
             │ PutItem condicional  │ sqs:SendMessage
       ┌─────▼──────────┐    ┌──────▼──────────┐  │
       │ DynamoDB       │    │ SQS dead-letter │◄─┘ (leitura)
       │ resultado +    │◄───────────────────────────┘
       │ idempotência   │
       └────────────────┘
```

## Coreografia e orquestração no mesmo sistema

| Trecho | Estilo | Por quê |
| --- | --- | --- |
| painel → API → `submit` → `orders` | coreografia | quem publica não sabe quem consome; a fila absorve o pico |
| `orders` → `dispatcher` | **fronteira** | é aqui que o evento vira execução |
| dentro da state machine | orquestração | a ordem é explícita, declarada e versionada |
| DynamoDB, dead-letter, painel | coreografia | consumidos por quem quiser, sem o fluxo saber que existem |

O ponto da entrega é a alternância. O mesmo cálculo que no Checkpoint 2 era
resolvido por **uma** função reagindo a **uma** mensagem, aqui é composto por
**cinco** funções cuja ordem está escrita em YAML — e o estado da composição
vive no orquestrador, não espalhado entre as funções.

## O fluxo, estado por estado

A definição está em [`workflow/bhaskara.asl.yaml`](workflow/bhaskara.asl.yaml).
Esse arquivo é a fonte da verdade: o Terraform o converte
(`templatefile` → `yamldecode` → `jsonencode`) e envia ao Step Functions, e o
interpretador em `local/engine.py` executa **o mesmo arquivo** na demonstração
local.

| Estado | Tipo | O que faz |
| --- | --- | --- |
| `Validate` | Task | normaliza e valida os coeficientes; erro aqui é permanente |
| `Delta` | Task | calcula b² − 4ac e classifica o sinal em `positive`/`zero`/`negative` |
| `ChooseRoots` | Choice | roteia pela **palavra**, não pelo número |
| `RootsInParallel` | Parallel | delta > 0: uma Lambda por raiz, em ramos concorrentes |
| `RootDouble` | Task | delta = 0: as raízes coincidem, calcula uma vez só |
| `NoRealRoots` | Pass | delta < 0: não há o que calcular — e invocar uma função para devolver lista vazia seria custo por nada |
| `Persist` | Task | grava com escrita condicional; devolve `duplicate: true` se já existia |
| `DeadLetter` | Task (SQS) | publica a recusa com o motivo, **sem Lambda no caminho** |
| `Rejected` | Fail | encerra como falha, para a recusa aparecer como recusa |
| `Done` | Succeed | fim do caminho feliz |

Os três caminhos entregam a **mesma forma** ao `Persist` — `{"roots": [...]}` —
e um teste exige essa coincidência.

## Idempotência em duas camadas

A chave é `sha256(batch_id + ":" + json_canônico(equação))`, 40 caracteres.

| Camada | Onde | Mecanismo | O que protege |
| --- | --- | --- | --- |
| 1 | `dispatcher` | `StartExecution(name = "eq-" + chave)`; o Standard recusa nome repetido por 90 dias | reentrega da SQS (at-least-once) e equações repetidas na mesma carga |
| 2 | `Persist` | `PutItem` com `attribute_not_exists(pk)` | qualquer caminho que escape da camada 1 — replay manual, reprocessamento |

Recusa **não é falha**: o dispatcher confirma a mensagem e segue; o `Persist`
devolve `duplicate: true` e a execução termina em sucesso. Tratar conflito como
erro mandaria para a dead-letter uma execução que fez exatamente a coisa certa.

O `batch_id` entra na chave de propósito. Sem ele, `{"a":1,"b":-5,"c":6}` seria
processada **uma única vez em 90 dias** na conta inteira, e a segunda
demonstração do dia apareceria vazia.

Para ver funcionando, dispare uma carga com **% duplicadas** no painel: o
contador *"evitadas por idempotência"* sobe ao vivo.

## Retry e dead-letter

Falha permanente e falha transitória não compartilham o mesmo caminho — e a
distinção está declarada em YAML, não escondida em código:

```yaml
Retry:
  - ErrorEquals: [ InvalidEquation ]      # permanente
    MaxAttempts: 0                        # nunca reentregar
  - ErrorEquals: [ TransientFailure, Lambda.TooManyRequestsException,
                   Lambda.ServiceException, States.TaskFailed, States.Timeout ]
    IntervalSeconds: 1
    MaxAttempts: 3
    BackoffRate: 2
    JitterStrategy: FULL
Catch:
  - ErrorEquals: [ States.ALL ]
    ResultPath: "$.error"
    Next: DeadLetter
```

| | Permanente | Transitória |
| --- | --- | --- |
| Exemplos | `a = 0`, coeficiente ausente, JSON malformado, overflow | throttling de Lambda, indisponibilidade, bug |
| Reentregar ajuda? | não | sim |
| O que acontece | vai direto para a dead-letter, em 1 tentativa | 1 s, 2 s, 4 s com jitter; só então a dead-letter |

**A ordem dos retriers é regra, não estilo:** `States.TaskFailed` casaria
também com `InvalidEquation`. Se viesse primeiro, uma equação inválida seria
reentregue três vezes sem nenhuma chance de sucesso. Há um teste para isso.

A mesma fila `dead-letter` recebe por **dois caminhos**, e o campo `source` os
distingue:

* `workflow` — o `Catch` publicou, com `error` e `cause` anexados;
* `redrive` — a SQS moveu a mensagem depois de 3 entregas ao dispatcher, sem
  motivo (o serviço move o payload original e não sabe por que ele falhou). É a
  rede de segurança para quando o próprio dispatcher falha.

## O modo caos

Um painel que só mostra caminho feliz não demonstra nada sobre resiliência: o
retry e a dead-letter aparecem no diagrama exatamente igual a "nada aconteceu".
Por isso a carga pode pedir a falha.

A falha é **determinística**, não sorteada: cada Task recebe
`$$.State.RetryCount` no payload e falha enquanto a tentativa for menor que o
número pedido.

```text
fails = 2   tentativa 0 falha, 1 falha, 2 passa    -> retry se recupera
fails = 5   as 4 tentativas falham                 -> dead-letter
```

Sortear daria uma demonstração diferente a cada execução e um teste instável.

## O painel

Servido de um bucket S3 **privado** via CloudFront com Origin Access Control.
Mostra, atualizando a cada 2 segundos:

* **o diagrama do fluxo**, com um nó por estado e por fila. Cada nó traz
  quantas execuções entraram nele; verde-água significa tráfego desde a leitura
  anterior, âmbar significa que o estado registrou falha (quase sempre
  recuperada pelo retry) e vermelho, dead-letter;
* **a linha do tempo de cada execução** — estado, duração e o que aquele estado
  produziu (`delta = 0`, `double=8`, `gravada`). Uma execução que sofreu retry
  mostra a tentativa refeita;
* **contadores** de fila, em execução, concluídas, recusadas e falhas em tasks;
* **os resultados gravados** e as **mensagens da dead-letter** com o motivo.

> **A chave não está no bundle.** A página é pública no CloudFront, e uma chave
> embutida seria uma chave publicada. O `config.js` gerado pelo Terraform leva
> apenas a URL da API; a chave é digitada pelo operador e fica só no
> `localStorage` daquele navegador.

Os contadores vêm dos eventos que a state machine grava no CloudWatch — uma
chamada paginada por poll — e não de um `GetExecutionHistory` por execução, que
seria uma chamada por execução a cada 2 segundos. O histórico oficial continua
sendo usado para o detalhe de **uma** execução.

## Estrutura do projeto

```text
bhaskara-orchestration/
├── run.sh                         # testes · demonstração · painel local
├── docker-compose.yml             # LocalStack (opcional, ver §Rodando contra emulados)
├── conftest.py                    # o sys.path dos testes = o da Lambda
├── workflow/
│   └── bhaskara.asl.yaml          # a state machine — fonte da verdade
├── src/
│   ├── shared/                    # o que mais de um handler usa
│   │   ├── calculator.py          # regra de negócio (cópia literal do CP1)
│   │   ├── quadratic.py           # adaptadores: discriminante e uma raiz
│   │   ├── chaos.py               # falha injetada determinística
│   │   ├── idempotency.py         # a chave, num lugar só
│   │   ├── api_auth.py            # verificação da chave de API
│   │   ├── observability.py       # o envelope canônico de log
│   │   └── metrics.py             # o bloco EMF e a regra de cardinalidade
│   └── handlers/                  # um diretório por função Lambda
│       ├── submit/                # POST /orders  ->  fila
│       ├── dispatcher/            # fila  ->  StartExecution
│       ├── validate/  delta/  root/  persist/     # os estados do fluxo
│       └── status/                # GET /flow  ->  painel
├── local/
│   ├── engine.py                  # interpretador da ASL
│   ├── doubles.py                 # SQS, DynamoDB e Step Functions em memória
│   ├── runtime.py                 # liga a definição aos handlers reais
│   ├── simulator.py               # a demonstração de terminal
│   └── server.py                  # o painel local
├── tests/                         # 329 casos, nenhum toca a AWS
├── infra/                         # Terraform — 71 recursos
├── web/                           # o painel publicado no S3
├── scripts/                       # publicar na fila, executar, renderizar, LocalStack
└── docs/
    ├── observabilidade.md         # o relatório do Checkpoint 4
    ├── entrega-canvas.md          # o texto da entrega, pronto para colar
    ├── evidencias/                # os prints e os números medidos
    ├── especificacao.md           # CP3 · especificacao-cp4.md — CP4
    └── cycle-NN.md                # uma nota por ciclo
```

### Sobre `calculator.py`

Cópia **literal, byte a byte** do [Checkpoint 1](https://github.com/erickdavi/bhaskara-api),
junto com os seus 18 testes. É a única coisa reaproveitada, de propósito: a
regra matemática é a mesma; o que muda é a composição ao redor.
`quadratic.py` adapta essa regra para os estados — `discriminant()` e `root()`
chamam `calculate()` e extraem o pedaço que interessa, em vez de reimplementar
a fórmula numericamente estável em outro lugar.

### Empacotamento

Cada função tem seu próprio `data.archive_file`, montado a partir de arquivos
explícitos. Os módulos vão para a **raiz do zip**, lado a lado, porque é assim
que a Lambda resolve imports: o handler faz `from calculator import calculate`,
sem prefixo de pacote. `conftest.py` e `local/paths.py` reproduzem esse mesmo
`sys.path` fora da nuvem.

| Função | Conteúdo do zip |
| --- | --- |
| `submit` | `handler.py`, `generator.py`, `api_auth.py` |
| `dispatcher` | `handler.py`, `idempotency.py` |
| `validate` | `handler.py`, `chaos.py` |
| `delta`, `root` | `handler.py`, `chaos.py`, `quadratic.py`, `calculator.py` |
| `persist` | `handler.py`, `chaos.py` |
| `status` | `handler.py`, `api_auth.py` |
| **todas** | `observability.py`, `metrics.py` |

As duas últimas são a única exceção à regra de "cada função leva só o que
importa": as sete emitem log, e o envelope só vale se for literalmente o mesmo
código nas sete.

## Implantando na AWS

### Pré-requisitos

| Ferramenta | Versão | Para quê |
| --- | --- | --- |
| Terraform | ≥ 1.5 | provisionar |
| AWS CLI | v2 | validar pela linha de comando |
| Credenciais AWS | — | `aws sts get-caller-identity` deve responder |

Permissões necessárias: IAM, Lambda, SQS, DynamoDB, Step Functions, API
Gateway, CloudWatch Logs, S3 e CloudFront.

```bash
cd infra
terraform init
terraform apply
```

O `apply` leva **cerca de 5 minutos** — a distribuição CloudFront responde por
quase todo esse tempo. Não há passo de build: o `archive_file` empacota o
código durante o `plan`.

```bash
terraform output -raw dashboard_url             # o painel
terraform output -raw api_key                   # a chave (sensitive)
terraform output -raw api_base_url              # para colar no painel
terraform output -raw state_machine_console_url # o fluxo desenhado pela AWS
```

## A API

Ambas as rotas exigem o header `x-api-key`. Sem ele, `403` e nenhuma carga é
gerada.

### `POST /orders` — gerar carga

```bash
curl -s -X POST "$(terraform -chdir=infra output -raw submit_url)" \
  -H "x-api-key: $(terraform -chdir=infra output -raw api_key)" \
  -H 'Content-Type: application/json' \
  -d '{"quantity": 50, "invalid_ratio": 0.1, "duplicate_ratio": 0.2, "chaos_ratio": 0.2}'
```

```json
{"batch_id": "b-3f9a1c2b07", "requested": 50, "published": 50,
 "batches": 5, "chaos": 11, "elapsed_ms": 940}
```

| Campo | Obrigatório | Descrição |
| --- | --- | --- |
| `quantity` | sim | inteiro entre 1 e 2.000 |
| `invalid_ratio` | não | proporção de mensagens inválidas, 0 a 1 |
| `duplicate_ratio` | não | proporção de mensagens repetidas, 0 a 1 |
| `chaos_ratio` | não | proporção com falha injetada, 0 a 1 |
| `seed` | não | torna a carga reproduzível |

Resposta **202 Accepted**: as mensagens foram aceitas para processamento, que
acontece depois e em outro lugar.

### `GET /flow` — acompanhar

```bash
curl -s "$(terraform -chdir=infra output -raw flow_url)?batch_id=b-3f9a1c2b07" \
  -H "x-api-key: $(terraform -chdir=infra output -raw api_key)"
```

| Parâmetro | Descrição |
| --- | --- |
| `since=<ms>` | janela de eventos (padrão: últimos 2 minutos) |
| `batch_id=<id>` | filtra os resultados gravados daquela carga |
| `execution=<arn>` | histórico oficial de uma execução |
| `dlq=N` | até 10 mensagens da dead-letter, **sem consumi-las** |

## Validando pela linha de comando

```bash
./scripts/send-test-message.sh            # publica direto na fila, sem HTTP
./scripts/send-test-message.sh 5          # cinco equações variadas
./scripts/send-test-message.sh --invalid  # três que serão recusadas
./scripts/send-test-message.sh --chaos    # com falha injetada, para ver o retry

./scripts/run-execution.sh                # inicia UMA execução e mostra o caminho
./scripts/run-execution.sh 1 -4 4         # raiz dupla
./scripts/run-execution.sh 1 -5 6 Delta 2 # com caos em Delta

./scripts/render-definition.sh            # a definição que o Terraform envia
```

O `send-test-message.sh` pula a API e publica direto na fila — é a prova de que
o fluxo não depende de HTTP. Logs:

```bash
aws logs tail "$(terraform -chdir=infra output -raw state_machine_log_group)" --follow
```

## Rodando contra serviços AWS emulados

Caminho **opcional**, para confirmar que o interpretador local não diverge de
um motor de Step Functions de verdade. Exige Docker e ~1,5 GB de imagens.

```bash
docker compose up -d
./scripts/local-aws.sh up        # filas, tabela, funções, state machine e ESM
./scripts/local-aws.sh demo 12
./scripts/local-aws.sh status
./scripts/local-aws.sh down      # antes do compose down
docker compose down -v
```

A state machine é criada a partir do **mesmo** `workflow/bhaskara.asl.yaml`.
Uma execução de 12 equações no LocalStack produz o esperado — 10 concluídas, 2
na dead-letter, retry se recuperando em 2 e 3 tentativas — e o histórico mostra
`Validate → Delta → RootX1 → RootX2 → Persist`, com o `Parallel` de verdade.

Duas particularidades do LocalStack estão tratadas nos arquivos, com comentário:
o container de cada função precisa nascer na mesma rede do LocalStack
(`LAMBDA_DOCKER_NETWORK`), e as funções são aquecidas uma a uma antes de o event
source mapping ser ligado — sem isso, em WSL2, vários containers sobem ao mesmo
tempo e estouram o timeout de startup, e as mensagens somem sem log.

## Testes

**329 casos, nenhum toca a AWS.** Os clientes boto3 são substituídos por dublês
em uma fixture `autouse`, para que um teste que a esquecesse não escrevesse numa
tabela de verdade.

```bash
./run.sh
```

| Arquivo | Casos | O que cobre |
| --- | --- | --- |
| `test_calculator.py` | 18 | a regra matemática (cópia do CP1) |
| `test_workflow_engine.py` | 32 | o interpretador: Choice, Parallel, Retry, Catch, JSONPath |
| `test_asl_definition.py` | 19 | a definição: nada de construção não suportada, todo Catch leva à dead-letter, todo estado tem nó no painel |
| `test_flow.py` | 15 | o fluxo real, ponta a ponta, contra o YAML versionado |
| `test_validate_handler.py` | 26 | tipos, `bool`, `NaN`, JSON malformado, caos |
| `test_delta_handler.py` | 11 | discriminante, classificação do sinal, overflow |
| `test_root_handler.py` | 11 | estabilidade numérica, rótulos, raiz dupla |
| `test_persist_handler.py` | 19 | escrita condicional, duplicata, chave, tipos |
| `test_dispatcher_handler.py` | 22 | nome determinístico, dedup, `batchItemFailures` |
| `test_submit_handler.py` | 35 | chave de API, validação, lotes, orçamento de tempo |
| `test_status_handler.py` | 25 | agregação dos eventos, timeline, espiada na dead-letter |
| `test_generator.py` | 15 | distribuição da carga, invalidas, duplicatas, caos |
| `test_local_simulator.py` | 10 | a contabilidade da demonstração fecha |
| `test_observability.py` | 21 | o envelope de log: campos, níveis, cold start, duração |
| `test_metrics.py` | 25 | a forma do EMF e a **regra de cardinalidade** |
| `test_handler_telemetry.py` | 24 | o que cada um dos sete handlers mede |

Alguns testes existem para proteger **acoplamentos que não são óbvios**:

* a definição não pode usar construção que o interpretador não execute — senão
  a demonstração local passaria a mentir sobre o que roda na nuvem;
* todo estado da ASL precisa ter nó no painel (ou estar numa lista de exceções
  com justificativa) — renomear um estado apagaria um nó em silêncio;
* o retrier do erro permanente precisa vir antes do genérico;
* **nenhum handler pode usar identificador como dimensão de métrica** — um
  `execution` ali criaria uma série temporal por equação processada, cobrada
  por mês. O teste percorre os seis handlers de uma vez, para que um handler
  novo que esqueça a regra falhe aqui e não na fatura.

## Observabilidade

Entrega do **Checkpoint 4**. O relatório completo — análise de performance, de
custo e as três otimizações, todas com número medido — está em
**[`docs/observabilidade.md`](docs/observabilidade.md)**.

### Toda linha de log tem a mesma forma

```json
{"event": "delta_calculated", "level": "INFO", "service": "delta", "state": "Delta",
 "execution": "9c1d4f…", "batch_id": "b-fbc1be…", "attempt": 1,
 "cold_start": false, "duration_ms": 0.024, "value": 1, "sign": "positive"}
```

O campo que faz o resto valer é o `execution` — a chave de idempotência, que
acompanha a equação pelos cinco estados. Filtrar por ele devolve o caminho
inteiro de uma equação, incluindo as tentativas que falharam:

```bash
aws logs start-query \
  --log-group-names $(terraform -chdir=infra output -json function_names \
                      | jq -r '.[] | "/aws/lambda/" + .' | tr '\n' ' ') \
  --start-time $(( $(date +%s) - 3600 )) --end-time $(date +%s) \
  --query-string 'fields @timestamp, service, state, event, level, attempt, duration_ms
                  | filter execution = "COLE-A-CHAVE" | sort @timestamp asc'
```

A mesma consulta está salva no Logs Insights pelo Terraform, junto com outras
quatro — duração por estado, cold start, motivos de recusa e volume de log.
Uma query que existe só no histórico do navegador de quem a escreveu não é
observabilidade.

### As métricas são o próprio log

Onze métricas em **Embedded Metric Format**: a métrica vai escrita na linha de
log e o CloudWatch a extrai do lado dele. Nenhuma chamada de API no caminho
quente, nenhuma permissão IAM a mais, nenhum erro de telemetria para tratar
dentro da regra de negócio.

| Métrica | Responde |
| --- | --- |
| `EquationsSubmitted`, `ExecutionsStarted` | volume de entrada |
| `ExecutionsDeduplicated`, `PersistDuplicate` | as duas camadas de idempotência |
| `EquationsByDeltaSign` | a distribuição dos três ramos do `Choice` |
| `ValidationRejected` | por que uma equação é recusada |
| `HandlerDuration` | p50/p95/p99 por estado |
| `EndToEndLatency` | da fila até a gravação |
| `ColdStart`, `RetryAttempt` | o que o caminho frio e a reentrega custam |
| `ChaosInjected` | separa a falha pedida da falha real |
| `UnauthorizedRequests` | 403 no access log da API |

**Nenhum identificador é dimensão.** `execution`, `batch_id` e `request_id`
ficam na linha como campo — pesquisáveis, sem virar série temporal. Um id como
dimensão criaria uma métrica nova por equação processada, cobrada por mês. Há
teste que falha se alguém tentar.

### O painel, os alarmes e o traço

```bash
terraform -chdir=infra output -raw cloudwatch_dashboard_url
terraform -chdir=infra output -raw xray_service_map_url
```

Um dashboard com dez widgets em cinco faixas — entrada, latência, falha e
saturação, o que a AWS mede sozinha, e um widget de log que fecha o ciclo do
gráfico para a linha. Cinco alarmes, cada um com a ação escrita na descrição,
que é o que chega no e-mail. X-Ray ativo nas sete funções e na state machine.

Tudo em Terraform, em [`infra/observability.tf`](infra/observability.tf). Um
painel montado à mão no console é configuração que existe num lugar só, que
ninguém revisa e que desaparece com a conta.

### O que ela encontrou

A instrumentação pagou por si na primeira carga real. A duração do estado
`Persist` deu p50 de 13 ms e **p95 de 5.939 ms**; separando invocação fria de
quente, a cauda inteira estava nas frias. O `initDurationMs` era de 83 ms — os
seis segundos aconteciam dentro do handler, no cliente `boto3` criado de forma
preguiçosa na primeira invocação, com a CPU racionada de uma função de 128 MB.

Corrigido e medido de novo, com a mesma carga:

| | Antes | Depois | |
| --- | --- | --- | --- |
| `persist` frio, no handler | 5.972 ms | 243 ms | **24×** |
| Latência ponta a ponta, p95 | 7.890 ms | 3.120 ms | **−60%** |
| `submit`, primeira requisição | 2.833 ms | 309 ms | **9,2×** |

As outras duas otimizações — o log da state machine, que é 70% da ingestão, e o
`Parallel` que gasta duas invocações para 60 microssegundos de conta — estão no
relatório, com o número e com o motivo de não terem sido aplicadas.

## Segurança

### Nada de credencial no repositório

O `.gitignore` cobre `*.tfstate*`, `*.tfvars`, `.terraform/`, `.env`, `*.zip` e
os nomes de arquivo de credencial que as ferramentas geram por padrão
(`*credentials*.json`, `*service-account*.json`, `*-key.json`). As credenciais
AWS vêm do ambiente. A chave de API é gerada pelo Terraform, vive no state (não
versionado) e sai por `terraform output -raw api_key`.

O `.terraform.lock.hcl` **é** versionado, de propósito: é o que faz um clone
limpo resolver exatamente as mesmas versões de provider.

### IAM: oito papéis, nenhum com permissão do outro

Policies inline com ARN restrito, em vez das managed policies (a
`AWSLambdaBasicExecutionRole` concede logs sobre `"*"`, todos os log groups da
conta; a `AWSLambdaSQSQueueExecutionRole`, SQS sobre `"*"`).

| | `orders` | `dead-letter` | tabela | state machine | logs |
| --- | --- | --- | --- | --- | --- |
| `submit` | **Send** | — | — | — | o próprio |
| `dispatcher` | Receive, Delete, GetAttributes | — | — | **StartExecution** | o próprio |
| `validate`, `delta`, `root` | — | — | — | — | o próprio |
| `persist` | — | — | Put, Get | — | o próprio |
| `status` | GetAttributes | GetAttributes, **Receive** | Query, Get | List, Describe, GetHistory | o próprio + **lê o do fluxo** |
| state machine | — | **Send** | — | invoca as 4 funções do fluxo | entrega de logs |

As roles são espelhadas: o `submit` publica na `orders` e não consome dela; o
`dispatcher` consome e não publica. O `status` é o único que enxerga tudo — e o
único **sem nenhum verbo de escrita**: ele espia a dead-letter (`ReceiveMessage`)
e nunca consegue esvaziá-la (não tem `DeleteMessage`). Um painel que apagasse a
evidência ao ser aberto seria pior que não ter painel.

**Há exatamente um `Resource: "*"` no projeto**, comentado onde aparece: a
entrega de logs do Step Functions é configurada por uma API de conta que não
aceita ARN de recurso, e a própria AWS instrui a conceder assim. O escopo real
fica limitado pelo `log_destination` declarado na state machine.

### O endpoint que gera carga

`POST /orders` transforma uma requisição em até 2.000 execuções. Aberto, seria
um gerador de custo para quem o encontrasse. Três camadas:

1. **Chave de API** no header, comparada com `hmac.compare_digest` para que o
   tempo não revele quantos caracteres iniciais estão corretos, e verificada
   **antes do corpo** — responder `400` a um corpo inválido diria ao chamador
   anônimo que a chave estava certa. **Falha fechada**: sem chave configurada,
   nada passa.
2. **Throttling do stage**: 5 rps, burst 10.
3. **Teto de 2.000** equações por requisição.

### Varredura de segurança

O pipeline de entrega deste projeto prevê scan de imagem de container. Não há
imagem aqui — as Lambdas são zips de código que usa apenas a biblioteca padrão
—, então a etapa foi cumprida por controle equivalente: varredura do
repositório e da infraestrutura.

```bash
trivy fs --scanners vuln,secret,misconfig .   # dependências, segredos e IaC
checkov                                       # políticas sobre o Terraform
```

| Ferramenta | Resultado |
| --- | --- |
| `trivy fs` | nenhum segredo, nenhuma vulnerabilidade, nenhuma misconfiguration não justificada |
| `checkov` | 160 políticas aprovadas, nenhuma reprovada |

> `tfsec` foi incorporado ao Trivy pela Aqua Security; o scanner `misconfig` do
> `trivy` é o sucessor dele, e é o que roda acima.

A primeira varredura apontou seis itens. Um foi **corrigido**: o stage do API
Gateway não registrava acesso — e um endpoint que transforma uma requisição em
até 2.000 execuções, com throttling mas sem log, é um alarme mudo. Os demais
foram **aceitos com justificativa escrita**, em
[`.trivyignore`](.trivyignore) e [`.checkov.yaml`](.checkov.yaml): WAF em uma
página estática de quatro arquivos, CMK para criptografar conteúdo que é
público por definição, PITR para dado com TTL de 24 horas, versionamento de
artefato cuja fonte já está no Git.

Um item merece nota, porque não é escolha: `CKV_AWS_115` pede concorrência
reservada por função, e a AWS **recusa** reservar quando a conta tem limite de
10 execuções simultâneas. O controle de vazão ficou no `maximum_concurrency`
do event source mapping.

## Custos

| Serviço | Consumo de uma carga de 100 equações | Custo |
| --- | --- | --- |
| Step Functions Standard | ~100 execuções × ~9 transições = 900 | free tier cobre 4.000/mês; acima, US$ 0,025/1.000 → **US$ 0,02** |
| Lambda | ~600 invocações de 128 MB | free tier |
| SQS | ~250 requests | free tier |
| DynamoDB on-demand | ~100 writes | free tier |
| CloudFront + S3 | o painel | < US$ 0,01 |
| CloudWatch Logs | 2,5 MB ingeridos numa carga de 120 | free tier (5 GB/mês) |
| X-Ray | ~100 traces | free tier (100.000/mês) |
| **Total por demonstração** | | **≈ US$ 0,02 – 0,05** |

O padrão do painel é **50 equações**, e não 1.000: com uma execução por
equação, a **transição de estado** é a unidade de custo.

### Uma correção do Checkpoint 3

Até o Checkpoint 3 este README afirmava que **nenhum recurso tem custo fixo**.
Depois do Checkpoint 4 isso deixou de ser verdade, e a frase saiu daqui em vez
de continuar por inércia.

As **24 séries de métrica customizada** são cobradas por mês, e não por uso:
US$ 0,30 cada acima das 10 gratuitas, ou **US$ 4,20/mês** se todas receberem
dado o mês inteiro. Dashboard (3 gratuitos), alarmes (10 gratuitos), X-Ray e a
ingestão de log de uma demonstração continuam na camada gratuita.

Esse número é o teto. A documentação da AWS indica que métrica customizada é
cobrada proporcionalmente às horas em que recebe dado, e um laboratório só
publica durante as demonstrações — mas isso não foi conferido na fatura, então
o valor acima é o que se deve assumir. `terraform destroy` zera tudo.

## Limpeza

```bash
cd infra
terraform destroy
```

Remove os 71 recursos, inclusive os log groups (criados pelo Terraform
justamente para que o `destroy` os leve junto) e os objetos do painel
(`force_destroy` no bucket).

## Limitações conhecidas

**Concorrência é o limite real, não o preço.** A conta usada tem 10 execuções
Lambda simultâneas no total, compartilhadas com os checkpoints anteriores — e a
AWS não permite reservar concorrência quando o limite é 10 (exige deixar 10 não
reservadas). O controle ficou no `maximum_concurrency` do event source mapping
(2, o mínimo aceito) e no `Retry` de `Lambda.TooManyRequestsException`, que
transforma throttling em espera. Cargas grandes drenam devagar, de propósito.

**Uma Lambda por raiz é decisão de demonstração, não de otimização.** Calcular
duas raízes não justifica duas invocações; o custo de rede entre os ramos é
maior que a conta que eles fazem. O que se ganha é ver o `Parallel` funcionando,
que é o objeto do Checkpoint 3.

> O Checkpoint 4 mediu o quanto isso custa, e o número é maior do que a frase
> acima sugeria: `RootX1` e `RootX2` têm p50 de **0,031 ms**, e o fluxo gasta
> duas invocações de 9 ms cobrados, três transições de state machine e dois
> slots do teto de 10 execuções concorrentes para fazer 60 microssegundos de
> aritmética. Está quantificado em
> [`docs/observabilidade.md`](docs/observabilidade.md) como otimização 3, com a
> recomendação de colapsar em produção e manter aqui — apagar o `Parallel`
> destruiria a evidência do Checkpoint 3.

**No LocalStack, o erro permanente é reentregue 3 vezes.** O nome do erro que a
state machine recebe de uma exceção Python deveria ser o `errorType`
(`InvalidEquation`); o LocalStack reporta `Exception` genérico, então o retrier
com `MaxAttempts: 0` não casa e o genérico assume. O desfecho final é o mesmo —
a mensagem chega na dead-letter com o motivo —, mas com 4 tentativas em vez de
1. Na AWS, `./scripts/run-execution.sh 0 1 1` mostra a única tentativa.

**As credenciais usadas no `apply` são da conta root.** O correto é um IAM user
dedicado com política restrita. Fica registrado como débito, não escondido.

**O painel faz poll, não recebe push.** "Atividade" é "o contador mudou desde a
leitura anterior", com 2 segundos de granularidade. Um WebSocket daria tempo
real e uma API Gateway inteira a mais para manter.

**As 24 séries de métrica são custo fixo mensal.** É a única coisa neste
projeto que é cobrada por existir, e não por uso. Ver a
[correção na seção de custos](#uma-correção-do-checkpoint-3).

**A timeline agregada perde o detalhe se `state_machine_execution_data` for
desligada.** A variável corta 63% do log da state machine e todos os contadores
do painel sobrevivem — mas o texto de cada passo (`delta = 49`) passa a vir só
ao abrir a execução, via `GetExecutionHistory`. Medido em
[`docs/cycle-13.md`](docs/cycle-13.md). O padrão continua ligado enquanto o
Checkpoint 3 estiver em correção.

**O `log_format = "JSON"` das funções não filtra o log da aplicação.** O
`application_log_level` só se aplica ao que sai pelo módulo `logging`, e o
envelope sai por `print()` — de propósito, porque o `logging` transforma o
dicionário em repr do Python dentro de um campo `message` e destrói a consulta
por campo. O ganho do formato JSON aqui é sobre as linhas da plataforma, não
sobre as nossas. Medido em [`docs/cycle-08.md`](docs/cycle-08.md).

**A linha do tempo tem a latência do CloudWatch.** Os eventos do fluxo são
lidos do log da state machine, e a ingestão leva alguns segundos. Durante uma
carga em andamento é normal ver uma execução com um passo faltando ou ainda
marcada como em execução depois de ter terminado; o poll seguinte completa. O
histórico oficial (`?execution=<arn>`) não tem essa defasagem, e é o que o
detalhe de uma execução usa.

## Decisões de arquitetura

Cada ciclo de desenvolvimento tem uma nota em [`docs/`](docs/), com o que foi
decidido e por quê. As especificações completas estão em
[`docs/especificacao.md`](docs/especificacao.md) (Checkpoint 3) e
[`docs/especificacao-cp4.md`](docs/especificacao-cp4.md) (Checkpoint 4).

Os ciclos 1 a 7 são o Checkpoint 3; do 8 em diante, o Checkpoint 4.

| Ciclo | Entrega |
| --- | --- |
| [1](docs/cycle-01.md) | o fluxo existe em YAML e roda sem AWS |
| [2](docs/cycle-02.md) | Choice, Parallel e as três formas de raiz |
| [3](docs/cycle-03.md) | persistência idempotente |
| [4](docs/cycle-04.md) | Catch, dead-letter e modo caos |
| [5](docs/cycle-05.md) | borda HTTP, fila, dispatcher e infraestrutura |
| [6](docs/cycle-06.md) | GET /flow e o painel ao vivo |
| [7](docs/cycle-07.md) | LocalStack, README e entrega |
| [8](docs/cycle-08.md) | o envelope canônico de log, e o formato decidido por medição |
| [9](docs/cycle-09.md) | métricas de negócio em EMF, e a regra de cardinalidade |
| [10](docs/cycle-10.md) | X-Ray, e uma decisão do CP3 revertida por escrito |
| [11](docs/cycle-11.md) | dashboard, alarmes e consultas como código |
| [12](docs/cycle-12.md) | a carga real, e a otimização que ela obrigou |
| [13](docs/cycle-13.md) | a otimização 2 confirmada contra a AWS, e o plano B que não existia |
