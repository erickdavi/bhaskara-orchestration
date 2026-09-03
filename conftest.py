"""Reproduz nos testes o sys.path que a Lambda monta em runtime.

A logica vive em local/paths.py, e nao aqui, para que os testes e o simulador
usem literalmente o mesmo caminho de import. Duplicar a montagem seria o jeito
mais facil de ter uma suite verde e uma demonstracao quebrada.
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from local.paths import setup  # noqa: E402 - depende do sys.path acima

setup()
