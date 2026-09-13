# Ciclo 13 — a otimizacao 2, confirmada contra a AWS

O relatorio do ciclo anterior deixou a otimizacao 2 como **proposta**, com uma
frase honesta: "falta confirmar que o evento sem `include_execution_data` ainda
traz `details.name`. E uma medicao de dez minutos, e nao um palpite — mas nao
foi feita."

Este ciclo fez a medicao. Ela confirmou a otimizacao **e revelou que o relatorio
estava errado sobre outra coisa.**

## O metodo

Duas cargas identicas de 30 equacoes com 20% de caos, separadas apenas pelo
parametro:

```bash
curl -X POST "$SUBMIT" -H "x-api-key: $KEY" \
  -d '{"quantity":30,"duplicate_ratio":0,"chaos_ratio":0.2}'

terraform apply -var 'state_machine_execution_data=false'

# mesma carga de novo
```

O parametro virou `var.state_machine_execution_data` antes do teste. Trocar o
valor a mao no `.tf` e desfazer depois deixaria a medicao sem rastro; como
variavel, a troca e uma linha de comando e o padrao continua declarado.

## O que a medicao deu

| | bytes | execucoes | por execucao | por evento |
| --- | --- | --- | --- | --- |
| `include_execution_data = true` | 546.813 | 29 | 18.856 B | 622,1 B |
| `include_execution_data = false` | 209.824 | 30 | **6.994 B** | **302,8 B** |

**−63% por execucao** no log da state machine. No sistema inteiro, de 26,6 KB
para 15,1 KB por execucao — **−43% da ingestao total**.

## O que sobrevive — a pergunta que travava a adocao

Nao dava para responder lendo documentacao: "execution data" nao esta definido
com precisao suficiente para saber se o **nome do estado** conta como dado de
execucao. So olhando o evento:

```json
// TaskStateEntered, com include_execution_data = false
{"details":{"name":"Validate"}, "type":"TaskStateEntered", "id":"2", ...}

// LambdaFunctionFailed, com include_execution_data = false
{"details":{"cause":"{\"errorMessage\": \"Falha simulada em Delta...\"}",
            "error":"TransientFailure"}, "type":"LambdaFunctionFailed", ...}
```

| campo | serve para | sobrevive |
| --- | --- | --- |
| `details.name` | contadores e diagrama do painel | **sim** |
| `details.error`, `details.cause` | motivo da falha | **sim** |
| `details.output` | detalhe de cada passo (`delta = 49`) | **nao** |

E o `GET /flow` com o parametro desligado, contra a AWS:

```text
Validate         entered=30  exited=30  failed=1
Delta            entered=30  exited=30  failed=3
RootsInParallel  entered=11  exited=11  failed=1
Persist          entered=28  exited=28  failed=0
desfechos: SUCCEEDED 28, FAILED 2

Validate  ok  ms=110  detail=None
Delta     ok  ms=105  detail=None
```

Tudo funciona. So o `detail` esvazia — exatamente o previsto, e nada alem.

## O erro que a verificacao encontrou

O relatorio dizia que o detalhe passaria a vir de `GetExecutionHistory`, "que o
codigo **ja implementa**".

Nao implementava. O `status` fazia a chamada, com `includeExecutionData=True`,
e montava cada passo assim:

```python
{"type": ..., "state": details.get("name"), "at": ..., "error": details.get("error")}
```

O `output` nunca era lido. **O plano B nao existia — existia o meio dele.** Se a
otimizacao tivesse sido adotada com base naquela frase, o painel teria perdido
o detalhe em silencio, nos dois caminhos, e a causa estaria escondida atras de
uma afirmacao que parecia verificada.

A correcao e uma linha:

```python
"detail": summarize(details.get("output")),
```

Mais quatro testes, incluindo um que assegura que a chamada continua pedindo
`includeExecutionData=True` — sem isso a API omite o payload e o plano B
deixaria de existir de novo, sem nada quebrar visivelmente.

Verificado contra a AWS com o log ja sem execution data:

```text
Validate         a=-3 b=-36 c=-60
Delta            delta = 576
RootsInParallel  x1=-10  x2=-2
Persist          gravada
```

## A decisao: confirmada, e desligada

`var.state_machine_execution_data` fica com o padrao `true`.

O motivo nao e tecnico. A stack do Checkpoint 3 esta no ar para correcao, e a
linha do tempo com detalhe inline em cada passo e parte daquela entrega.
Trocar o comportamento dela enquanto esta sendo avaliada seria otimizar o
artefato errado — economizar cinquenta e nove centavos por 100.000 execucoes as
custas de uma nota.

Depois da correcao, e uma palavra:

```bash
terraform apply -var 'state_machine_execution_data=false'
```

A stack foi devolvida ao padrao e verificada: o detalhe inline voltou
(`delta = 0`, `double=12`, `gravada`), e o `plan` seguinte diz *No changes*.

## O que este ciclo ensina

**Verificar a saida de uma otimizacao vale tanto quanto medir a entrada.** A
medicao do problema — 70% da ingestao num log group — estava certa desde o
ciclo 12. O que faltava verificar era o **outro lado**: o que se perde, e se o
plano para compensar existe de fato. Nao existia.

Quem encontrou foi o teste ponta a ponta contra a AWS. A leitura do codigo nao
tinha encontrado, porque `get_execution_history(includeExecutionData=True)` na
tela parece exatamente o que faz falta — o que falta e a linha seguinte.

## Criterio de pronto

- 333 testes verdes (329 + 4 do historico);
- as duas cargas medidas, com os eventos inspecionados campo a campo;
- o plano B implementado e verificado contra a AWS;
- a stack de volta ao padrao, `plan` limpo;
- relatorio e texto do Canvas atualizados — a otimizacao 2 passou de proposta a
  confirmada, e a afirmacao errada foi corrigida no lugar onde estava.
