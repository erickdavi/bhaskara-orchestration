# Checkpoint 3 — Orquestração e composição de serviços

Uma equação do segundo grau entra por uma fila, atravessa **cinco funções
Lambda cuja ordem está declarada em YAML**, e sai gravada — ou recusada, com o
motivo anexado. A orquestração é feita pelo **AWS Step Functions**; um painel
web mostra o fluxo acontecendo, estado por estado.

> **Checkpoint 3 — orquestração.** O [Checkpoint 1](https://github.com/erickdavi/bhaskara-api)
> é uma API serverless **síncrona** e o [Checkpoint 2](https://github.com/erickdavi/bhaskara-events)
> é uma arquitetura **orientada a eventos**. Este projeto é independente dos
> dois: a mesma regra de negócio, uma composição completamente diferente ao
> redor dela.

```bash
git clone https://github.com/erickdavi/bhaskara-orchestration.git
cd bhaskara-orchestration
./run.sh demo          # o fluxo inteiro no terminal, sem AWS
./run.sh web           # o painel em http://localhost:8000, sem AWS
./run.sh               # 258 testes, sem AWS
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
│   │   └── api_auth.py            # verificação da chave de API
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
├── tests/                         # 258 casos, nenhum toca a AWS
├── infra/                         # Terraform — 57 recursos
├── web/                           # o painel publicado no S3
├── scripts/                       # publicar na fila, executar, renderizar, LocalStack
└── docs/                          # especificação e uma nota por ciclo
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

**258 casos, nenhum toca a AWS.** Os clientes boto3 são substituídos por dublês
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

Alguns testes existem para proteger **acoplamentos que não são óbvios**:

* a definição não pode usar construção que o interpretador não execute — senão
  a demonstração local passaria a mentir sobre o que roda na nuvem;
* todo estado da ASL precisa ter nó no painel (ou estar numa lista de exceções
  com justificativa) — renomear um estado apagaria um nó em silêncio;
* o retrier do erro permanente precisa vir antes do genérico.

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

## Custos

| Serviço | Consumo de uma carga de 100 equações | Custo |
| --- | --- | --- |
| Step Functions Standard | ~100 execuções × ~9 transições = 900 | free tier cobre 4.000/mês; acima, US$ 0,025/1.000 → **US$ 0,02** |
| Lambda | ~600 invocações de 128 MB | free tier |
| SQS | ~250 requests | free tier |
| DynamoDB on-demand | ~100 writes | free tier |
| CloudFront + S3 | o painel | < US$ 0,01 |
| **Total por demonstração** | | **≈ US$ 0,02 – 0,05** |

Nenhum recurso tem custo fixo. O padrão do painel é **50 equações**, e não
1.000: com uma execução por equação, a **transição de estado** é a unidade de
custo.

## Limpeza

```bash
cd infra
terraform destroy
```

Remove os 57 recursos, inclusive os log groups (criados pelo Terraform
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
que é o objeto deste checkpoint.

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

## Decisões de arquitetura

Cada ciclo de desenvolvimento tem uma nota em [`docs/`](docs/), com o que foi
decidido e por quê. A especificação completa está em
[`docs/especificacao.md`](docs/especificacao.md).

| Ciclo | Entrega |
| --- | --- |
| [1](docs/cycle-01.md) | o fluxo existe em YAML e roda sem AWS |
| [2](docs/cycle-02.md) | Choice, Parallel e as três formas de raiz |
| [3](docs/cycle-03.md) | persistência idempotente |
| [4](docs/cycle-04.md) | Catch, dead-letter e modo caos |
| [5](docs/cycle-05.md) | borda HTTP, fila, dispatcher e infraestrutura |
| [6](docs/cycle-06.md) | GET /flow e o painel ao vivo |
| [7](docs/cycle-07.md) | LocalStack, README e entrega |
