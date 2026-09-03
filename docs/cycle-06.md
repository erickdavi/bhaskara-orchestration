# Ciclo 6 — o fluxo fica visivel

## O que entrou

- `src/handlers/status`: `GET /flow`, a fonte unica do painel.
- `web/`: painel com diagrama ao vivo, linha do tempo por execucao,
  resultados e dead-letter.
- `local/server.py`: o mesmo painel servido em `http://localhost:8000`,
  alimentado pelo fluxo em memoria (`./run.sh web`).
- `infra/dashboard.tf`: S3 privado + CloudFront com Origin Access Control.
- 258 testes. `terraform plan`: 57 recursos.

## Decisoes

**Os contadores do diagrama vem do log, e nao do historico das execucoes.**
Para saber quantas execucoes passaram por cada estado seria preciso chamar
`GetExecutionHistory` uma vez por execucao, a cada poll de 2 segundos. O log
group da state machine ja tem todos os eventos de todas as execucoes e sai em
uma chamada paginada. O historico oficial continua ali para o detalhe de UMA
execucao, quando o operador clica nela.

**`aggregate()` e uma funcao pura.** Ela recebe eventos ja parseados e nao
conhece a AWS. E o que permite ao servidor local alimentar o mesmo painel com
os eventos do interpretador: a demonstracao offline usa **o mesmo agregador**
que a nuvem, em vez de uma segunda implementacao parecida.

**A ordem das checagens em `summarize()` e o que torna a timeline legivel.**
Com `ResultPath` enxertando em vez de substituir, a saida de um estado tardio
contem tudo o que os anteriores produziram — o payload do Persist ainda tem o
delta la dentro. Procurando do mais recente para o mais antigo, cada linha
mostra o que **aquele** estado acrescentou. Escrito na ordem ingenua, a
timeline inteira repetia `delta = -48`, que foi exatamente o que apareceu na
primeira renderizacao do painel.

**Ambar para estado que falhou, vermelho so para a dead-letter.** Na primeira
versao qualquer falha pintava o no de vermelho, e um painel com retry
funcionando normalmente ficava coberto de alarme. A maioria dessas falhas foi
recuperada; vermelho ficou reservado para o que de fato terminou recusado.

**O painel nao pode apagar a dead-letter.** A role do status tem
`ReceiveMessage` e nao tem `DeleteMessage`. Um painel que consumisse a fila ao
ser aberto destruiria a evidencia que ele existe para mostrar.

**Cache do CloudFront desligado.** Sao quatro arquivos pequenos servidos
algumas vezes por demonstracao. Com cache, "editei o painel e nada mudou"
viraria a experiencia padrao depois de cada apply.

**Nomes de estado sao contrato de interface.** O painel mapeia nome de estado
para no do diagrama, e `test_todo_estado_da_asl_tem_no_no_painel_ou_justificativa`
le a lista direto do `web/flow.js`. Renomear um estado no YAML sem mexer no
painel quebraria o teste — em vez de apagar um no em silencio.

## Criterio de pronto

    ./run.sh web
    -> painel em http://localhost:8000, sem AWS e sem configuracao

Com 40 equacoes, 12% invalidas, 30% com caos e 15% duplicadas:

    Validate 35   (5 evitadas pela idempotencia)
    Delta 30      (5 recusadas na validacao)
    Parallel 12 · RootDouble 13 · NoRealRoots 5   = 30
    Persist 30    concluidas 28 · recusadas 7
    dead-letter 7 com o motivo de cada uma

A linha do tempo de uma execucao com raiz dupla:

    Validate    a=3 b=-48 c=192
    Delta       delta = 0
    ChooseRoots
    RootDouble  double=8
    Persist     gravada
