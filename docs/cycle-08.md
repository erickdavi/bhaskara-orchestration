# Ciclo 8 — o envelope canonico de log

Primeiro ciclo do Checkpoint 4. O objetivo era um so: fazer com que toda linha
de log do sistema tenha a mesma forma, com nivel e com correlacao — e decidir,
com medicao, em que formato ela sai.

## O que entrou

- `src/shared/observability.py`: o envelope, num lugar so.
- Os sete handlers migrados. A funcao `log()` que estava **copiada em seis
  deles** deixou de existir.
- `tests/test_observability.py`: 21 casos sobre o contrato do envelope.
- `logging_config` nas sete funcoes, com JSON no log da plataforma.
- `shared/observability.py` no zip das sete, em `infra/main.tf`.

## O que o log era, e o que passou a ser

Antes:

```json
{"event": "delta_calculated", "execution": "k-9c1d4f", "value": 1, "sign": "positive"}
```

Depois:

```json
{"event": "delta_calculated", "level": "INFO", "service": "delta", "state": "Delta",
 "execution": "k-9c1d4f", "batch_id": "b-demo", "request_id": "local-request",
 "attempt": 1, "cold_start": false, "duration_ms": 0.024, "value": 1, "sign": "positive"}
```

Nenhum campo novo veio de lugar nenhum: `state` e `retry_count` ja chegavam a
toda Task pelos `Parameters` da ASL, e `idempotency_key` e `batch_id` ja viajavam
no `meta` desde o dispatcher. **O contrato entre os estados nao mudou** — o que
mudou e que esses campos agora sao registrados em vez de descartados.

O ganho aparece quando se filtra por `execution`:

```text
equation_validated   validate   Validate   attempt 0   INFO
chaos_injected       delta      Delta      attempt 0   WARN   TransientFailure
delta_calculated     delta      Delta      attempt 1   INFO   value 1  sign positive
root_calculated      root       RootX1     attempt 0   INFO   x1 = 3.0
root_calculated      root       RootX2     attempt 0   INFO   x2 = 2.0
result_stored        persist    Persist    attempt 0   INFO   end_to_end_ms
```

Uma equacao, os cinco estados, o retry que falhou e se recuperou, e os dois
ramos do `Parallel` separados por `state` — que era impossivel antes, porque o
handler `root` atende os tres estados e as linhas dele eram indistinguiveis.

## A medicao que decidiu o formato

A especificacao registrou uma duvida em vez de um palpite: o formato JSON nativo
do runtime (`log_format = "JSON"`) embrulha o stdout dentro de um campo
`message`, e o EMF do proximo ciclo precisa do `_aws` na **raiz** da linha. Se
os dois nao convivessem, seria preciso escolher entre nivel filtrado na
plataforma e metrica.

Foi medido numa funcao descartavel (`cp4-emf-probe`, Python 3.13, arm64) criada
e apagada na conta, fora do Terraform. Quatro combinacoes:

| O que a funcao faz | `LogFormat=Text` | `LogFormat=JSON` |
| --- | --- | --- |
| `print(json.dumps(emf))` | linha crua, `_aws` na raiz | **linha crua, `_aws` na raiz** |
| `logger.info({dict})` | `[INFO]\tts\treqId\t{'a': 1}` — repr do Python | `"message": "{'a': 1}"` — repr dentro de string |
| `logger.info(json.dumps({...}))` | idem, prefixado | `"message": "{\"a\": 1}"` — **string escapada** |
| `logger.debug(...)` com nivel INFO | sai | **filtrado antes da ingestao** |

E as duas metricas apareceram no CloudWatch:

```text
$ aws cloudwatch list-metrics --namespace Bhaskara/Probe
Bhaskara/Probe  ProbeText  Service=probe
Bhaskara/Probe  ProbeJson  Service=probe
```

**O conflito nao existe.** O formato JSON converte o que sai pelo modulo
`logging`; o que sai por `print()` atravessa intacto. EMF funciona nos dois.

### As tres conclusoes

**1. Fica o `print`.** Nao por inercia: o caminho do `logging` transforma o
dicionario em repr do Python — `{'event': 'x'}`, com aspas simples — dentro de
um campo `message`. Isso nao e JSON, o Logs Insights nao acessa campo nenhum ali
dentro, e nem passar `json.dumps` resolve: a string continua string, so que
escapada. Todo o valor do log estruturado se perderia.

**2. `log_format = "JSON"` entra assim mesmo**, pelo que ele faz com as linhas
da **plataforma**. O REPORT, que era texto:

```text
REPORT RequestId: dee9b8ff  Duration: 17.21 ms  Billed Duration: 98 ms
       Memory Size: 128 MB  Max Memory Used: 35 MB  Init Duration: 80.52 ms
```

virou:

```json
{"time":"2026-09-13T05:53:48.893Z","type":"platform.report","record":{
 "requestId":"8582be7d","metrics":{"durationMs":1.802,"billedDurationMs":63,
 "memorySizeMB":128,"maxMemoryUsedMB":35,"initDurationMs":60.644},"status":"success"}}
```

As otimizacoes candidatas de memoria e de cold start dependem exatamente desses
tres numeros. A diferenca entre as duas formas e a diferenca entre
`stats avg(record.metrics.billedDurationMs) by @log` e uma expressao regular
sobre texto livre.

**3. `system_log_level` fica em `INFO`, e nao em `WARN`.** `WARN` economiza
bytes — e apaga o `platform.report` junto. Seria medir o custo jogando fora o
dado de custo.

## Dois detalhes que o teste pegou

**Campo vazio virava `null`.** A primeira versao do `emit()` filtrava os campos
sem valor e depois copiava o resto do dicionario de volta — recolocando
exatamente os que tinha acabado de tirar. Toda linha do `submit` e do `status`
sairia com `"state": null, "execution": null, "batch_id": null`. Quem pegou foi
`test_campo_ausente_nao_vira_null`, escrito antes de eu olhar o codigo de novo.

**`False` e `0` sao falsy.** `cold_start: false` e `attempt: 0` desapareceriam de
um filtro ingenuo de campos vazios — e sao os dois valores mais comuns de todos.
A ausencia do campo seria lida como "nao se sabe" em vez de "estava quente" e
"primeira tentativa". Os dois tem teste proprio.

## Uma decisao pequena com efeito grande

`json.dumps(..., default=str)` no emissor. Telemetria nao pode derrubar regra de
negocio: o `persist` le `Decimal` do DynamoDB, e sem isso o handler falharia com
`TypeError` ao **registrar** que a gravacao deu certo. `allow_nan=False`
continua — `NaN` e `Infinity` nao sao JSON valido pela RFC 8259 e transformariam
a linha inteira em texto opaco para o Logs Insights.

## Criterio de pronto

- 280 testes verdes (259 antes, +21 do envelope);
- `./run.sh demo` e `./run.sh web` funcionando sem AWS;
- `terraform validate` limpo e `plan` com **0 to add, 7 to change, 0 to destroy**;
- a decisao de formato tomada com medicao na conta real, e a funcao de teste
  apagada — nenhum recurso orfao.

O `apply` fica para o ciclo 11, quando o dashboard e os alarmes entrarem junto.
Nao ha por que reimplantar a stack tres vezes durante a correcao do CP3.
