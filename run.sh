#!/usr/bin/env bash
#
# Bootstrap do ambiente e execucao local. Nao precisa de credenciais AWS.
#
#   ./run.sh                 roda os testes
#   ./run.sh demo            simula o fluxo orquestrado (80 equacoes)
#   ./run.sh demo 200 --duplicates 20 --chaos 15
#   ./run.sh web             sobe o painel em http://localhost:8000
#
set -euo pipefail

cd "$(dirname "$0")"

VENV_DIR=".venv"

if [ ! -d "$VENV_DIR" ]; then
  echo "Criando ambiente virtual em $VENV_DIR ..."
  python3 -m venv "$VENV_DIR"
fi

PYTHON="$VENV_DIR/bin/python"

# Diferente do Checkpoint 2, aqui as dependencias sao necessarias tambem para a
# demonstracao: o simulador le a state machine em YAML, e YAML nao esta na
# biblioteca padrao. As Lambdas continuam sem nenhuma dependencia — o YAML e
# lido pelo Terraform na nuvem e pelo simulador aqui, nunca em runtime.
if ! "$PYTHON" -c 'import pytest, yaml' >/dev/null 2>&1; then
  echo "Instalando dependencias ..."
  "$PYTHON" -m pip install --quiet --disable-pip-version-check -r requirements.txt
fi

case "${1:-}" in
  demo)
    shift
    exec "$PYTHON" -m local.simulator "$@"
    ;;
  web)
    shift
    exec "$PYTHON" -m local.server "$@"
    ;;
  *)
    exec "$PYTHON" -m pytest "$@"
    ;;
esac
