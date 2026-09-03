#!/usr/bin/env bash
#
# Inicia UMA execucao direto no Step Functions e mostra o caminho percorrido.
#
# Pula a fila e o dispatcher: e o jeito mais direto de inspecionar a state
# machine isolada, util quando se quer ver o historico de um caso especifico
# sem procurar a execucao no meio de uma carga.
#
#   ./scripts/run-execution.sh                    # 1x^2 - 5x + 6 = 0
#   ./scripts/run-execution.sh 1 -4 4             # raiz dupla
#   ./scripts/run-execution.sh 1 0 5              # sem raizes reais
#   ./scripts/run-execution.sh 1 -5 6 Delta 2     # com caos em Delta
set -euo pipefail

cd "$(dirname "$0")/.."

A="${1:-1}"; B="${2:--5}"; C="${3:-6}"
CHAOS_STATE="${4:-}"; CHAOS_FAILS="${5:-2}"

ARN="$(terraform -chdir=infra output -raw state_machine_arn)"
NAME="manual-$(date +%s)"

META="{\"batch_id\":\"$NAME\",\"idempotency_key\":\"$NAME\""

if [ -n "$CHAOS_STATE" ]; then
  META="$META,\"chaos\":{\"state\":\"$CHAOS_STATE\",\"fails\":$CHAOS_FAILS}"
fi

META="$META}"

INPUT="{\"equation\":{\"a\":$A,\"b\":$B,\"c\":$C},\"meta\":$META}"

echo "entrada  $INPUT"

EXECUTION_ARN="$(aws stepfunctions start-execution \
  --state-machine-arn "$ARN" --name "$NAME" --input "$INPUT" \
  --query executionArn --output text)"

echo "execucao $EXECUTION_ARN"
echo

# O Standard permite consultar o historico completo — e dele que sai a timeline
# do painel. Aqui, so os estados por onde a execucao passou e como ela terminou.
sleep 3

aws stepfunctions get-execution-history \
  --execution-arn "$EXECUTION_ARN" --max-results 100 \
  --query 'events[?type!=`null`].[type, stateEnteredEventDetails.name, stateExitedEventDetails.name]' \
  --output text | awk '{ printf "  %-28s %s%s\n", $1, $2, $3 }' | sed 's/None//g'

echo
aws stepfunctions describe-execution --execution-arn "$EXECUTION_ARN" \
  --query '{status: status, error: error, cause: cause}' --output table
