# Evidências — Checkpoint 4

Sete telas do console da AWS, capturadas em 13/09/2026 contra a stack real em
`us-east-1`, com carga de verdade. Cada uma diz o que prova.

Os números por trás delas estão em [`medicoes.md`](medicoes.md); a análise, em
[`../observabilidade.md`](../observabilidade.md).

---

## 01 — Dashboard: entrada, discriminante, recusas e latência

![Dashboard, faixa de cima](01-dashboard-entrada-e-latencia.png)

A metade de cima do dashboard, criado por Terraform em
[`infra/observability.tf`](../../infra/observability.tf).

**Prova que as métricas de negócio existem e têm dado.** Da esquerda para a
direita, em cima: as submetidas contra as execuções iniciadas — a distância
entre as duas linhas **é** a idempotência; a distribuição dos três ramos do
`Choice`, empilhada; e as recusas separadas por motivo (`a_is_zero`,
`missing_coefficient`, `coefficient_not_number`, `malformed_json`), que só
existem porque `InvalidEquation` carrega um código de conjunto fechado.

Embaixo: a duração p95 por estado, com `RootX1`, `RootX2` e `RootDouble` em
séries separadas — é essa separação que sustenta a otimização 3 —, e a latência
ponta a ponta em média, p95 e p99.

## 02 — Dashboard: falha, saturação e o log dentro do painel

![Dashboard, faixa de baixo](02-dashboard-falha-saturacao-e-log.png)

**Prova que o ciclo do gráfico até a linha se fecha sem trocar de tela.**

As execuções da state machine, o trio retry/caos/cold start — o caos existe
para poder ser **subtraído** do resto —, as duas filas, o throttling somado das
sete funções e a API.

Embaixo, o widget de log com as últimas linhas de `ERROR` e `WARN`: dá para ler
`equation_rejected` com `InvalidEquation` e "O valor de 'a' nao pode ser zero.",
lado a lado com os `chaos_injected` e o número da tentativa em que cada um
falhou.

## 03 — O caminho de uma equação, pelo correlation id

![Rastro de uma equação](03-logs-insights-rastro-de-uma-equacao.png)

**A evidência central do envelope de log.** Uma única cláusula —
`filter execution = "439c89e4…"` — devolve as nove linhas de uma equação, em
ordem, atravessando cinco funções Lambda:

```text
dispatcher            execution_started                        INFO
validate   Validate   equation_validated      attempt 0        INFO
delta      Delta      chaos_injected          attempt 0        WARN   tentativa 1 de 2
delta      Delta      chaos_injected          attempt 1        WARN   tentativa 2 de 2
dispatcher            execution_deduplicated                   INFO
delta      Delta      delta_calculated        attempt 2        INFO
root       RootX2     root_calculated         attempt 0        INFO
root       RootX1     root_calculated         attempt 0        INFO
persist    Persist    result_stored           attempt 0        INFO
```

O retry aparece como `attempt` subindo de 0 para 2 no mesmo estado, e os dois
ramos do `Parallel` aparecem separados porque `state` está na linha — o mesmo
handler `root` atende os três estados de raiz.

## 04 — Duração por estado: p50, p95 e p99

![p95 por estado](04-logs-insights-p95-por-estado.png)

**A base numérica das otimizações 1 e 3.**

| service | state | invocações | p50 | p95 |
| --- | --- | --- | --- | --- |
| persist | Persist | 197 | 15,76 ms | 287,77 ms |
| delta | Delta | 211 | 0,031 ms | 0,041 ms |
| root | RootDouble | 66 | 0,027 ms | 0,039 ms |
| root | RootX2 | 62 | 0,031 ms | 0,039 ms |
| root | RootX1 | 69 | 0,031 ms | 0,036 ms |
| validate | Validate | 217 | 0,021 ms | 0,035 ms |

Duas leituras. A matemática custa **31 microssegundos** — e o fluxo gasta duas
invocações de Lambda e três transições para fazê-la duas vezes em paralelo
(otimização 3). E o `persist` já aparece com p95 de 287 ms: esta captura é
**depois** da otimização 1, que derrubou esse número de 5.939 ms.

## 05 — X-Ray: o pipeline inteiro

![Service map](05-xray-service-map.png)

**Prova que o traço distribuído está ativo ponta a ponta**, revertendo a decisão
do Checkpoint 3 de deixá-lo desligado.

O mapa mostra o caminho completo: cliente → `submit` → `dispatcher` → a state
machine → `validate`, `delta`, `root` e `persist`, mais a fila `dead-letter`
como destino da integração direta. O arco vermelho no nó da state machine são as
execuções que terminaram em `Rejected`.

## 06 — Os cinco alarmes, um deles disparado

![Alarmes](06-alarmes.png)

**Prova que os alarmes existem como código e que disparam de verdade.**

Os cinco criados pelo Terraform, com a condição de cada um visível. O
`dead-letter-com-mensagem` está **In alarm** — não foi forçado: a carga com
`invalid_ratio` e modo caos mandou mensagens para a fila, e o alarme reagiu.

Os outros quatro em OK, incluindo o de execuções falhando: o limite de 5 foi
escolhido para não disparar durante uma demonstração com caos, e não disparou.

## 07 — Uma execução, com o `Parallel` percorrido

![Execução no Step Functions](07-step-functions-parallel.png)

**O contexto do que está sendo observado, e dois números úteis.**

O grafo mostra o caminho tomado em verde: `Validate → Delta → ChooseRoots →
RootsInParallel (RootX1 ‖ RootX2) → Persist → Done`. O `Catch` de cada estado
aponta para `DeadLetter`, que não foi percorrido nesta execução.

Nos detalhes: **13 transições de estado** e um link para o X-Ray trace map —
que só existe porque o `tracing_configuration` foi ligado no ciclo 10.

---

## O que não está aqui, e por quê

**O painel web do projeto.** Ele é a entrega do Checkpoint 3, e não da
observabilidade — e abri-lo exige digitar a chave de API na interface. Uma
captura de tela com a chave visível seria uma credencial versionada no
repositório público, que é exatamente o que a disciplina pede para evitar. O
painel está descrito no [README](../../README.md#o-painel).

**Prints com a URL da API ou a chave.** Nenhuma das sete telas mostra o endpoint
ativo nem a chave. As URLs saem por `terraform output`, e vão para o campo de
comentários do Canvas — não para o repositório.

**O número da conta.** Está tarjado no canto superior direito de todas as telas,
e nos dois ARNs completos da tela 07. É a mesma decisão que já tinha tirado o
número da conta da especificação do Checkpoint 3. Tarja sólida, e não desfoque:
uma tarja diz "isto foi removido de propósito"; um borrão parece defeito de
captura.

## Três defeitos que a captura encontrou

Esta pasta não é só documentação: montar as telas revelou problemas que os
testes e o `terraform apply` não pegariam.

1. **O widget de log respondia "No data found".** A query montava
   `SOURCE /aws/lambda/a' , 'b` — sem a aspa de abertura. Um erro de sintaxe que
   o CloudWatch reportou como ausência de dados, que é a forma mais cara de
   errar.
2. **A segunda tentativa também estava errada**, e essa falhou alto:
   `LogGroupName cannot contain a comma`. A forma que funciona é
   `SOURCE logGroups(namePrefix: [...])`, que ainda resolve as sete funções de
   uma vez e não quebra quando uma oitava entrar.
3. **O widget "Recusas por motivo" ficava vazio** porque nenhuma carga anterior
   tinha usado `invalid_ratio` — o dado não existia, e o widget estava certo.
   Registrado aqui para que a próxima carga de demonstração não repita a
   omissão.
