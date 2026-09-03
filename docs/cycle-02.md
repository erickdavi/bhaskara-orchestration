# Ciclo 2 — o fluxo passa a ter caminhos

## O que entrou

- `ChooseRoots` (Choice), `RootsInParallel` (Parallel com dois ramos),
  `RootDouble` e `NoRealRoots` na definicao.
- `src/handlers/root`: um handler para os tres estados de raiz.
- `tests/test_flow.py`: o fluxo real, ponta a ponta, contra o YAML versionado.
- 117 testes.

## Decisoes

**O Choice compara uma palavra, nao um numero.** O estado Delta devolve
`sign: positive | zero | negative`, e o roteamento compara isso.
`NumericGreaterThan: 0` funcionaria, mas espalharia a regra "delta zero
significa raiz dupla" entre o codigo e o YAML. Com a classificacao no codigo, a
state machine so roteia — e o YAML fica legivel para quem nunca viu Bhaskara.

**Delta = 0 nao abre o Parallel.** As duas raizes sao o mesmo numero; calcular
duas vezes seria desperdicio sem ganho didatico. `RootDouble` calcula uma vez e
o `ResultSelector` com `States.Array($)` da a esse resultado a mesma forma que
os outros dois caminhos entregam: `{ "roots": [...] }`. Um teste verifica que os
tres caminhos coincidem nessa forma — e o que permite ao Persist ter um unico
contrato de entrada.

**Delta < 0 e um Pass, nao uma Lambda.** Invocar uma funcao para devolver lista
vazia seria custo e latencia por nada. Continua aparecendo como um no no
diagrama do painel, que e o objetivo.

**O nome do estado passa a viajar no payload.** Toda Task recebe
`state.$: "$$.State.Name"`. Sem isso o handler de raiz — que atende RootX1,
RootX2 e RootDouble — nao teria como saber onde esta, e o modo caos nao poderia
mirar um ramo especifico do Parallel. Um teste da suite exige esse par de
campos em toda Task.

**Uma raiz por Lambda e demonstracao, nao otimizacao.** Esta escrito no
cabecalho do handler e no README: o custo de rede entre os ramos e maior que a
conta que eles fazem. O que se ganha e ver o Parallel funcionando, que e o
objeto deste checkpoint.

## Criterio de pronto

Os tres ramos percorridos e cobertos por teste:

    1x^2 - 5x + 6 = 0   Validate -> Delta -> ChooseRoots -> RootsInParallel -> RootX1 -> RootX2 -> Done
    1x^2 - 4x + 4 = 0   Validate -> Delta -> ChooseRoots -> RootDouble -> Done
    1x^2 + 0x + 5 = 0   Validate -> Delta -> ChooseRoots -> NoRealRoots -> Done

Caos em `RootX2` com `fails: 2` falha duas vezes **so naquele ramo** e a
execucao termina SUCCEEDED com as duas raizes corretas.

## Dois testes que estavam errados

`test_precisao_quando_b_domina` e `test_coeficientes_negativos` foram escritos
com expectativas erradas — o primeiro trocou x1 por x2 (com b > 0, a raiz de
menor magnitude e x1, pelo contrato do Checkpoint 1), e o segundo escolheu uma
equacao sem raizes reais para testar coeficientes negativos. Corrigidos os
testes, nao o codigo: o comportamento estava certo.
