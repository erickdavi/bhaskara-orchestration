"""Adaptadores finos entre a regra de negocio e os estados do fluxo.

`calculator.py` e copia literal do Checkpoint 1 e resolve a equacao inteira de
uma vez. O fluxo deste checkpoint precisa das partes em separado: um estado
calcula o discriminante, outro calcula **uma** raiz.

Reescrever as formulas aqui seria manter duas copias da mesma regra, e a copia
nova nasceria sem os 18 testes que a primeira ja tem. Entao estes adaptadores
chamam `calculate()` e extraem o pedaco que interessa.

Isso significa que `root()` recalcula o discriminante que o estado anterior ja
tinha calculado. E desperdicio consciente: sao dois float ops contra o risco de
duas implementacoes divergindo em precisao — e a estabilidade numerica de
`_roots()` e justamente a parte que nao se quer duplicar.
"""

from calculator import calculate

LABELS = ("x1", "x2", "double")


def discriminant(a, b, c):
    """Devolve apenas o delta, com o mesmo contrato de erro do calculator."""
    return calculate(a, b, c)["delta"]


def root(a, b, c, label):
    """Devolve uma raiz. `label` escolhe qual.

    "double" existe para o caso delta = 0, onde as duas raizes coincidem: o
    fluxo chama uma unica vez em vez de abrir um Parallel para calcular duas
    vezes o mesmo numero.
    """
    if label not in LABELS:
        raise ValueError("Raiz desconhecida: %r. Esperado um de %s." % (label, ", ".join(LABELS)))

    result = calculate(a, b, c)

    if "x1" not in result:
        raise ValueError("A equacao nao tem raizes reais: delta = %s." % result["delta"])

    return result["x1" if label == "double" else label]
