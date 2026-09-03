# Ciclo 7 — o caminho avancado, o README e a entrega

## O que entrou

- `docker-compose.yml` e `scripts/local-aws.sh`: o fluxo inteiro rodando contra
  servicos AWS emulados (LocalStack), a partir do mesmo YAML.
- `README.md`: a entrega, sem nenhuma URL publica.
- Varredura de segredos no historico inteiro.

## O que o LocalStack mostrou

Rodar contra um motor de Step Functions de verdade era o objetivo do caminho
avancado, e ele pagou o custo logo na primeira execucao.

**O `Parallel`, o `Choice`, o `Retry` e o `Catch` se comportam como o
interpretador local diz.** Uma carga de 12 equacoes terminou com 10 concluidas
e 2 na dead-letter; o historico de uma execucao com caos mostra tres falhas em
sequencia no mesmo estado e o sucesso na quarta tentativa, e o caminho
`Validate -> Delta -> RootX1 -> RootX2 -> Persist` com os dois ramos.

**Uma divergencia real apareceu.** O nome do erro que a state machine recebe de
uma excecao Python deveria ser o `errorType` — `InvalidEquation`. O LocalStack
reporta `Exception` generico, e com isso o retrier de `MaxAttempts: 0` nao casa
e o generico assume: a equacao com `a = 0` foi reentregue tres vezes antes de
ir para a dead-letter. O desfecho e o mesmo, o custo nao. Esta registrado nas
limitacoes conhecidas do README, com o comando que verifica o comportamento na
AWS.

**Dois problemas de ambiente, com correcao no arquivo.** O container de cada
funcao precisa nascer na mesma rede do LocalStack (`LAMBDA_DOCKER_NETWORK`),
senao ele sobe e nao consegue mais falar com quem o criou — a invocacao morre em
"timed out during startup", sem nenhum erro no codigo. E, em WSL2, subir varios
containers ao mesmo tempo estoura esse timeout: as funcoes passaram a ser
aquecidas uma a uma **antes** de o event source mapping ser ligado. Sem isso, o
primeiro lote sumia sem log.

O `down` do script tambem remove os containers de funcao: o
`LAMBDA_KEEPALIVE_MS` os mantem vivos fora do compose, e `docker compose down`
nao sabe da existencia deles.

## Decisoes

**O caminho avancado e opcional, e o README diz isso.** Ele exige Docker e
~1,5 GB de imagens, e o enunciado descreve um "clone + install + start". O
caminho padrao — `./run.sh demo` e `./run.sh web` — roda em qualquer maquina
com Python 3.9+.

**O README nao tem URL publica.** O enunciado e explicito: a URL da funcao ativa
vai no campo de comentarios do Canvas, nao no repositorio. Todos os comandos do
README leem a URL de `terraform output`.

## Criterio de pronto

- `./run.sh` em clone limpo: 258 testes verdes;
- `./run.sh demo` e `./run.sh web` funcionando sem AWS;
- `terraform validate` limpo e `terraform plan` com 57 recursos;
- LocalStack executando o mesmo YAML com os desfechos esperados;
- nenhum segredo no historico do Git.
