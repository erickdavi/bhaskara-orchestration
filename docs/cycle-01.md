# Ciclo 1 — o fluxo existe em YAML e roda sem AWS

## O que entrou

- `workflow/bhaskara.asl.yaml` com `Validate -> Delta -> Done` e o bloco de
  `Retry` que todos os estados seguintes vao reusar por ancora YAML.
- `local/engine.py`: interpretador da ASL que executa esse arquivo chamando os
  handlers reais em processo.
- `src/handlers/validate` e `src/handlers/delta`.
- `src/shared/`: `calculator.py` (copia literal do Checkpoint 1),
  `quadratic.py` (adaptadores) e `chaos.py` (falha injetada deterministica).
- 96 testes.

## Decisoes

**O YAML e a fonte da verdade, e nao um artefato gerado.** Terraform e
simulador leem o mesmo arquivo. A alternativa — descrever o fluxo em HCL e
gerar o JSON — daria um arquivo que so o Terraform entende, e a demonstracao
local teria de reimplementar o fluxo por fora. Duas descricoes do mesmo fluxo
divergem no primeiro ajuste.

**O interpretador recusa o que nao implementa.** `validate_definition()`
levanta `Unsupported` diante de qualquer tipo de estado ou campo desconhecido,
e um teste roda essa validacao contra o YAML de verdade. Sem isso, acrescentar
um `Map` a state machine deixaria a demonstracao local silenciosamente
diferente do que roda na nuvem — o pior tipo de divergencia, porque ela passa
nos testes.

**A falha injetada conta tentativas em vez de sortear.** Cada Task recebe
`$$.State.RetryCount` no payload; o modo caos falha enquanto a tentativa for
menor que o numero pedido. Sorteio daria demonstracao diferente a cada execucao
e teste instavel; contagem da sempre o mesmo, e e o numero que aparece na
timeline do painel.

**A ordem dos retriers e regra, nao estilo.** `States.TaskFailed` casa com
`InvalidEquation` tambem. Se o retrier generico vier primeiro, uma equacao
invalida e reentregue tres vezes sem chance nenhuma de sucesso.
`test_erro_permanente_e_avaliado_antes_do_generico` existe para impedir isso.

**`quadratic.py` recalcula em vez de duplicar.** O estado Root chama
`calculate()` inteiro e extrai uma raiz, recalculando o delta que o estado
anterior ja tinha. Sao dois float ops contra o risco de manter duas
implementacoes da forma numericamente estavel — e a segunda nasceria sem os 18
testes que a primeira tem.

## Criterio de pronto

`./run.sh demo` ainda nao existe (ciclo 5 monta a carga), mas o fluxo ja roda:

    python3 -c "from local import runtime; \
      print(runtime.build_engine().start({'equation':{'a':1,'b':-5,'c':6},'meta':{}}).output)"

    Validate -> Delta -> Done, delta = 1, sign = positive

Equacao com `a = 0` termina `FAILED` com erro `InvalidEquation` e **uma** unica
tentativa. Caos em `Delta` com `fails: 2` termina `SUCCEEDED` na terceira, com
esperas de 1 s e 2 s registradas.
