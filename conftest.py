"""Reproduz nos testes o sys.path que a Lambda monta em runtime, e desliga a AWS.

O sys.path vive em local/paths.py, e nao aqui, para que os testes e o simulador
usem literalmente o mesmo caminho de import. Duplicar a montagem seria o jeito
mais facil de ter uma suite verde e uma demonstracao quebrada.

A fixture `aws` e **autouse** de proposito: um teste que esquecesse de pedi-la
criaria um cliente boto3 de verdade e passaria a depender de credenciais no
ambiente de quem roda a suite — ou, pior, escreveria numa tabela real.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from local.paths import setup  # noqa: E402 - depende do sys.path acima

setup()

from local import runtime  # noqa: E402


@pytest.fixture(autouse=True)
def aws(monkeypatch):
    """Dubles em memoria no lugar dos clientes boto3, restaurados ao fim."""
    return runtime.bind_doubles(monkeypatch.setattr)
