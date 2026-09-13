# Ciclo 12 — a carga real, e o que ela revelou

O `apply` entrou na stack que ja estava no ar: **13 recursos criados, 16
alterados, nenhum destruido**. Depois, 120 equacoes de verdade.

Este era para ser o ciclo de coletar numeros. Ele coletou — e o primeiro numero
mudou o codigo.

## A carga

```bash
curl -X POST "$(terraform -chdir=infra output -raw submit_url)" \
  -H "x-api-key: $(terraform -chdir=infra output -raw api_key)" \
  -d '{"quantity":120,"duplicate_ratio":0.15,"chaos_ratio":0.12}'
```

Os numeros completos estao em `docs/evidencias/medicoes.md`. O resumo:
120 submetidas, 94 execucoes, **26 evitadas pela idempotencia**, 40 falhas
injetadas, 29 cold starts, 28 throttles, 5 execucoes na dead-letter.

**Dois alarmes dispararam sozinhos** — `dead-letter-com-mensagem` e
`throttling-de-lambda` —, o que e a evidencia de que eles funcionam. Os outros
tres ficaram em OK, inclusive o de execucoes falhando: o limite de 5 foi bem
escolhido, porque as falhas do caos ficaram abaixo dele.

## O numero que nao era esperado

A duracao por estado saiu assim:

```text
persist   Persist     p50 13,4 ms    p95 5.939,7 ms
delta     Delta       p50  0,032 ms  p95     0,044 ms
root      RootX1      p50  0,031 ms  p95     0,039 ms
validate  Validate    p50  0,021 ms  p95     0,027 ms
```

Um p95 de **5,9 segundos** num handler cujo p50 e 13 milissegundos. Uma metrica
so nao explica nada; a consulta seguinte explicou:

```text
cold_start   n    media (ms)   p50        maximo
0 (quente)   79      13,39      13,65      25,79
1 (frio)     10   5.972,17   5.939,69   6.221,11
```

**A cauda inteira era cold start.** E o `initDurationMs` da mesma funcao era
83 ms — ou seja, os seis segundos **nao estavam na inicializacao**. Estavam
dentro do handler.

## A causa

O Checkpoint 3 escreveu os clientes boto3 assim, de proposito:

```python
def dynamodb():
    global _dynamodb
    if _dynamodb is None:
        import boto3
        _dynamodb = boto3.client("dynamodb")
    return _dynamodb
```

A intencao era boa — manter a inicializacao leve e nao pagar por um cliente que
talvez nao fosse usado. O efeito medido e o oposto: o `import boto3` e o
`boto3.client()` (que carrega o modelo JSON do servico) passam a rodar **na
primeira invocacao**, com a CPU racionada de uma funcao de 128 MB.

A Lambda concede CPU ampliada durante a fase de inicializacao,
**independentemente da memoria configurada**. O codigo estava fazendo o
trabalho mais pesado exatamente na janela onde ele custa mais caro.

## A correcao, e por que ela nao quebra os testes

```python
if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    dynamodb()
```

Tres linhas no fim de quatro modulos: `persist`, `dispatcher`, `submit` e
`status`.

O acessor preguicoso **continua existindo** — e ele que permite aos testes
substituirem o cliente por um duble em memoria, atribuindo `_dynamodb` antes da
primeira chamada. O `if` garante que o aquecimento so acontece dentro da
Lambda, onde a variavel `AWS_LAMBDA_FUNCTION_NAME` sempre existe. Na suite e no
simulador local, nenhum boto3 e importado.

## O antes e o depois

Segunda carga, parametros identicos:

| | Antes | Depois | |
| --- | --- | --- | --- |
| `persist` frio, no handler | 5.972 ms | 243 ms | **24x** |
| `persist`, billed medio | 632 ms | 87 ms | **7,3x** |
| `persist`, init | 83 ms | 500 ms | +417 ms |
| ponta a ponta, p95 | 7.890 ms | 3.120 ms | **−60%** |
| ponta a ponta, maxima | 14.081 ms | 6.716 ms | **−52%** |
| `submit`, 1a requisicao | 2.833 ms | 309 ms | **9,2x** |

O trabalho nao sumiu: 417 ms migraram para a inicializacao. O que mudou foi
**onde** — o mesmo import custa ~5.700 ms no handler e ~417 ms no init. A
diferenca e a CPU que a plataforma concede em cada fase.

## O que mais os numeros mostraram

**O log da state machine e 70% da ingestao.** 1.741.065 bytes contra 762.875 das
sete funcoes somadas — 18,5 KB por execucao, contra 8,1 KB de todas as Lambdas
juntas. Vira a otimizacao 2, com uma restricao que a medicao tambem revelou: o
painel do projeto **depende** desse dado. O `status` le
`details.output` do log para montar a linha do tempo de cada execucao, entao
desligar `include_execution_data` cortaria o custo e a funcionalidade junto.

**O `Parallel` custa duas invocacoes para 60 microssegundos de conta.** RootX1
com p50 de 0,031 ms e RootX2 com 0,032 ms. O CP3 ja registrava que a divisao
era didatica; agora ha o numero. Vira a otimizacao 3.

**Nenhuma falha foi real.** Os 40 erros de Lambda batem exatamente com os 40
`ChaosInjected`. Sem a metrica que separa os dois, a leitura seria "o sistema
falhou 40 vezes em 94 execucoes" — 42% de falha inventada. A metrica que a
especificacao quase cortou por cardinalidade e a que impede a conclusao errada.

## Uma lição de shell que custou uma rodada

O primeiro script de medicao devolveu `Log group '1000' does not exist`. A
variavel de acumulacao chamava-se `GROUPS`, que em bash e **variavel especial
somente-leitura** com os group IDs do usuario. A atribuicao foi silenciosamente
ignorada e sobrou o `1000` do proprio usuario. Renomear resolveu.

## Criterio de pronto

- stack aplicada e idempotente (`plan` seguinte: *No changes*);
- duas cargas de 120 equacoes medidas ponta a ponta;
- os numeros das cinco candidatas na mao, em `docs/evidencias/medicoes.md`;
- 329 testes verdes depois da correcao;
- dois alarmes exercitados de verdade, por carga real.

Falta a captura das telas do console, que exige sessao autenticada.
