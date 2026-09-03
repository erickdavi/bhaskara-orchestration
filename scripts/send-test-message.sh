#!/usr/bin/env bash
#
# Publica direto na fila orders, sem passar pela API.
#
# Serve para mostrar que o fluxo nao depende de HTTP: a mensagem entra pela
# fila, o dispatcher a transforma em execucao e a state machine roda. E o
# caminho mais curto para ver o sistema funcionando.
#
#   ./scripts/send-test-message.sh              # uma equacao com duas raizes
#   ./scripts/send-test-message.sh 5            # cinco equacoes variadas
#   ./scripts/send-test-message.sh --invalid    # uma equacao que sera recusada
#   ./scripts/send-test-message.sh --chaos      # com falha injetada, para ver o retry
set -euo pipefail

cd "$(dirname "$0")/.."

QUEUE_URL="$(terraform -chdir=infra output -raw orders_queue_url)"

EQUATIONS=(
  '{"a": 1, "b": -5, "c": 6}'
  '{"a": 1, "b": -4, "c": 4}'
  '{"a": 1, "b": 0, "c": 5}'
  '{"a": -3, "b": 21, "c": 132}'
  '{"a": 2, "b": -7, "c": 3}'
)

INVALID=('{"a": 0, "b": 2, "c": 3}' '{isto nao e json' '{"a": "1", "b": -5, "c": 6}')

send() {
  local body="$1"
  shift

  aws sqs send-message \
    --queue-url "$QUEUE_URL" \
    --message-body "$body" \
    "$@" \
    --query MessageId --output text
}

case "${1:-1}" in
  --invalid)
    for body in "${INVALID[@]}"; do
      echo "recusada  $body  ->  $(send "$body")"
    done
    ;;
  --chaos)
    body='{"a": 1, "b": -5, "c": 6}'
    chaos='{"state":"Delta","fails":2}'
    echo "com caos  $body  ->  $(send "$body" --message-attributes \
      "Chaos={DataType=String,StringValue='$chaos'}")"
    echo "A execucao vai falhar duas vezes em Delta e concluir na terceira."
    ;;
  *)
    for ((i = 0; i < ${1:-1}; i++)); do
      body="${EQUATIONS[$((i % ${#EQUATIONS[@]}))]}"
      echo "$body  ->  $(send "$body")"
    done
    ;;
esac

echo
echo "Acompanhe: aws stepfunctions list-executions --state-machine-arn \"\$(terraform -chdir=infra output -raw state_machine_arn)\" --max-results 5"
