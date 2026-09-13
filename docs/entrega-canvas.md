# Entrega no Canvas — Checkpoint 4

Dois campos. O primeiro e o link; o segundo e para colar o bloco abaixo.

---

## Parte 1 — Campo de URL

```
https://github.com/erickdavi/bhaskara-orchestration
```

> O Checkpoint 4 pede para instrumentar o pipeline dos checkpoints anteriores,
> e nao construir um sistema novo — por isso a entrega vive no mesmo
> repositorio do Checkpoint 3, e nao num quarto. A tag `cp3-entrega` marca o
> estado entregue no CP3, para que a correcao daquele checkpoint continue
> possivel.

---

## Parte 2 — Caixa de texto

Copiar daqui para baixo.

---

**Checkpoint 4 — Observabilidade do pipeline**
Repositorio: https://github.com/erickdavi/bhaskara-orchestration
Relatorio completo: `docs/observabilidade.md` · Prints: `docs/evidencias/`
Dados brutos das medicoes: `docs/evidencias/medicoes.md`

O pipeline do Checkpoint 3 (Step Functions orquestrando cinco Lambdas) foi
instrumentado com logging estruturado, metricas de negocio em Embedded Metric
Format, traco distribuido no X-Ray, um dashboard do CloudWatch, cinco alarmes e
cinco consultas do Logs Insights — tudo declarado em Terraform. As tres
otimizacoes abaixo saem de duas cargas reais de 120 equacoes medidas na AWS,
nao de estimativa.

**1. Mover a inicializacao do boto3 para a fase de init — IMPLEMENTADA E
MEDIDA.** A metrica de duracao do estado `Persist` mostrou p50 de 13 ms e p95 de
5.939 ms. Separando invocacao fria de quente, a cauda inteira estava nas frias:
5.972 ms de media contra 13,4 ms. O `initDurationMs` da funcao era de apenas
83 ms, ou seja, os seis segundos aconteciam **dentro do handler**, e nao na
inicializacao. A causa era o cliente boto3 criado de forma preguicosa na
primeira invocacao — uma decisao do checkpoint anterior, tomada para manter o
init leve, que produzia o efeito oposto: o `import boto3` e o `boto3.client()`
rodavam com a CPU racionada de uma funcao de 128 MB, em vez de rodarem na fase
de inicializacao, onde a Lambda concede CPU ampliada independentemente da
memoria configurada. A correcao aquece o cliente no fim do modulo, sob
`if os.environ.get("AWS_LAMBDA_FUNCTION_NAME")`, preservando o acessor
preguicoso de que os testes dependem. Resultado medido com a mesma carga:
invocacao fria do `persist` de 5.972 ms para 243 ms (24x), duracao cobrada media
de 632 ms para 87 ms (7,3x), latencia ponta a ponta p95 de 7.890 ms para
3.120 ms (−60%) e maxima de 14.081 ms para 6.716 ms (−52%). O trabalho nao
sumiu: 417 ms migraram para o init, que e onde custam menos.

**2. Parar de gravar o payload de cada estado no log da state machine —
CONFIRMADA E MEDIDA, desligada por escolha.** A consulta de volume mostrou que
um unico log group responde por **70% de toda a ingestao**: 1.741.065 bytes
contra 762.875 das sete funcoes somadas, ou 18,5 KB por execucao contra 8,1 KB
de todas as Lambdas juntas. A causa e `include_execution_data = true` na
configuracao de log da state machine, que grava entrada e saida de cada um dos
nove estados. Medido com duas cargas identicas de 30 equacoes, a segunda com o
parametro desligado: **18.856 bytes por execucao caem para 6.994** (−63% nesse
log group, −43% na ingestao total do sistema). Verificado evento a evento o que
sobrevive: `details.name` continua em `StateEntered` e `StateExited`, entao
todos os contadores e o diagrama do painel funcionam; `details.error` e
`details.cause` continuam em `LambdaFunctionFailed`, entao o motivo da falha
continua visivel. Perde-se apenas `details.output`, o texto de detalhe de cada
passo. Esse detalhe passa a vir de `GetExecutionHistory`, que nao depende da
configuracao de log — e aqui houve uma correcao no proprio projeto: o handler
ja fazia a chamada, mas nunca extraia o `output`, entao o plano B nao existia de
fato. Foi implementado (uma linha, quatro testes) e verificado contra a AWS com
o log ja sem execution data. A otimizacao fica **desligada** mesmo assim, com a
chave pronta em `var.state_machine_execution_data`: a stack do Checkpoint 3
esta no ar para correcao, e a linha do tempo com detalhe inline e parte daquela
entrega — trocar o comportamento dela enquanto esta sendo avaliada seria
otimizar o artefato errado.

**3. Colapsar o `Parallel` das raizes num unico estado — PROPOSTA
QUALIFICADA.** Com Δ > 0 o fluxo abre dois ramos concorrentes, um por raiz. A
metrica de duracao por estado mostrou `RootX1` com p50 de 0,031 ms e `RootX2`
com 0,032 ms: **31 microssegundos de conta**. Para isso, o fluxo gasta duas
invocacoes de Lambda com duracao cobrada media de 9,05 ms cada — 288 vezes o
tempo do calculo —, tres transicoes de state machine em vez de uma, e dois slots
do teto de 10 execucoes concorrentes da conta, que a analise identificou como o
recurso escasso do sistema (28 estrangulamentos na carga medida, 10 deles na
propria funcao `root`). A recomendacao e condicional de proposito: em producao,
colapsar num unico estado que devolve as duas raizes — o `Persist` ja aceita
essa forma, porque o caminho de Δ = 0 ja faz exatamente isso. Neste
repositorio, manter, porque o `Parallel` e o que o Checkpoint 3 entrega e o
proprio codigo ja registrava que a divisao era escolha didatica, nao de
desempenho. A medicao nao contradiz aquela decisao: ela a quantifica.

**Uma observacao que vale mais que as tres.** Os 40 erros de Lambda registrados
na carga batem exatamente com as 40 falhas injetadas pelo modo caos. Sem a
metrica que separa a falha pedida da falha inesperada, a leitura seria "42% de
falha em 94 execucoes" — uma conclusao errada extraida de dados corretos. Essa
metrica quase foi cortada por custo de cardinalidade durante o projeto.

**Sobre o custo da propria observabilidade.** O Checkpoint 3 afirmava que a
stack nao tinha nenhum recurso com custo fixo. Depois do CP4 isso deixou de ser
verdade e o README foi corrigido: as 24 series de metrica customizada sao
cobradas por mes, nao por uso — US$ 4,20/mes de tabela acima da camada gratuita
de 10. Dashboard, alarmes, X-Ray e a ingestao de log de uma demonstracao cabem
na camada gratuita.

**Nenhuma credencial no repositorio.** A chave de API e gerada pelo Terraform e
sai por `terraform output -raw api_key`; a URL da funcao ativa nao esta
versionada. Varredura de segredos feita no historico inteiro; `checkov` com 167
aprovacoes e nenhuma reprovacao; `trivy` sem achados de severidade MEDIA ou
superior.

---

## Endpoints ativos (colar so se for entregar a URL viva)

Rodar antes de colar — nenhum destes valores esta versionado:

```bash
cd infra
terraform output -raw dashboard_url            # painel do fluxo (CloudFront)
terraform output -raw api_key                  # chave para colar no painel
terraform output -raw cloudwatch_dashboard_url # painel de observabilidade
terraform output -raw state_machine_console_url
```

> O dashboard do CloudWatch e o service map do X-Ray so abrem para quem estiver
> autenticado na conta. Para a correcao, o que vale sao os prints em
> `docs/evidencias/` — os links servem para quem tiver acesso.
