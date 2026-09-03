"""Monta o sys.path que a Lambda tem em runtime.

Cada funcao publicada recebe um zip com os seus modulos na **raiz**: o validate
leva handler.py, calculator.py, quadratic.py e chaos.py lado a lado. Por isso
os imports dentro de src/ sao planos — `from calculator import calculate` — e
nao `from src.shared.calculator import ...`.

Para rodar esse mesmo codigo fora da Lambda, alguem precisa reproduzir esse
caminho. Fazer isso em um lugar so, aqui, evita a divergencia classica: o
conftest do pytest fazendo de um jeito e o simulador de outro, com um teste
verde e uma demonstracao quebrada.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HANDLERS = os.path.join(ROOT, "src", "handlers")


def paths():
    found = [ROOT, os.path.join(ROOT, "src", "shared")]

    if os.path.isdir(HANDLERS):
        found += [
            os.path.join(HANDLERS, name)
            for name in sorted(os.listdir(HANDLERS))
            if os.path.isdir(os.path.join(HANDLERS, name))
        ]

    return found


def setup():
    for path in paths():
        if path not in sys.path:
            sys.path.insert(0, path)
