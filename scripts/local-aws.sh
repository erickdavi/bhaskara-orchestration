#!/usr/bin/env bash
#
# Sobe o fluxo inteiro no LocalStack: filas, tabela, as sete funcoes, a state
# machine renderizada do MESMO YAML e o event source mapping.
#
#   docker compose up -d
#   ./scripts/local-aws.sh up          cria tudo
#   ./scripts/local-aws.sh demo 30     dispara uma carga e mostra o resultado
#   ./scripts/local-aws.sh status      contadores e ultimas execucoes
#   ./scripts/local-aws.sh down        apaga os recursos e os containers de funcao
#
# Rode `down` ANTES de `docker compose down -v`: o LocalStack cria um container
# por funcao fora do compose, e o compose nao sabe remove-los.
#
# Nenhuma credencial real e usada: o LocalStack aceita qualquer par de chaves.
set -euo pipefail

cd "$(dirname "$0")/.."

ENDPOINT="${LOCALSTACK_ENDPOINT:-http://localhost:4566}"
REGION="us-east-1"
ACCOUNT="000000000000"
PREFIX="bhaskara-local"

export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-test}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-test}"
export AWS_DEFAULT_REGION="$REGION"

API_KEY="chave-local-de-demonstracao"
TABLE="$PREFIX-results"
ROLE="arn:aws:iam::$ACCOUNT:role/lambda-role"
BUILD="infra/build/localstack"

aws_local() { aws --endpoint-url "$ENDPOINT" "$@"; }

queue_url() { aws_local sqs get-queue-url --queue-name "$1" --query QueueUrl --output text; }

state_machine_arn() {
  aws_local stepfunctions list-state-machines \
    --query "stateMachines[?name=='$PREFIX-flow'].stateMachineArn" --output text
}

# Um zip por funcao, com os modulos na raiz — igual ao que o archive_file monta.
package() {
  local name="$1"; shift

  rm -rf "$BUILD/$name" && mkdir -p "$BUILD/$name"

  for file in "$@"; do
    cp "src/$file" "$BUILD/$name/$(basename "$file")"
  done

  (cd "$BUILD/$name" && zip -qr "../$name.zip" .)
}

create_function() {
  local name="$1" environment="$2"

  aws_local lambda create-function \
    --function-name "$PREFIX-$name" \
    --runtime python3.12 \
    --role "$ROLE" \
    --handler handler.lambda_handler \
    --timeout 30 \
    --zip-file "fileb://$BUILD/$name.zip" \
    --environment "$environment" \
    --query FunctionArn --output text
}

warm() {
  local name="$1" payload="$2"

  # A resposta nao importa: o objetivo e criar o ambiente de execucao. Um erro
  # de payload aquece o container do mesmo jeito.
  aws_local lambda invoke \
    --function-name "$PREFIX-$name" \
    --payload "$(printf '%s' "$payload" | base64 -w0)" \
    --cli-read-timeout 300 \
    /dev/null >/dev/null 2>&1 || true

  echo "   $name"
}

up() {
  echo "== filas"
  aws_local sqs create-queue --queue-name "$PREFIX-dead-letter" >/dev/null
  local dead_letter_arn
  dead_letter_arn="$(aws_local sqs get-queue-attributes --queue-url "$(queue_url "$PREFIX-dead-letter")" \
    --attribute-names QueueArn --query 'Attributes.QueueArn' --output text)"

  aws_local sqs create-queue --queue-name "$PREFIX-orders" \
    --attributes "{\"RedrivePolicy\":\"{\\\"deadLetterTargetArn\\\":\\\"$dead_letter_arn\\\",\\\"maxReceiveCount\\\":\\\"3\\\"}\"}" >/dev/null

  local orders_url dead_letter_url
  orders_url="$(queue_url "$PREFIX-orders")"
  dead_letter_url="$(queue_url "$PREFIX-dead-letter")"

  echo "== tabela"
  aws_local dynamodb create-table \
    --table-name "$TABLE" \
    --attribute-definitions AttributeName=pk,AttributeType=S AttributeName=batch_id,AttributeType=S AttributeName=created_at,AttributeType=N \
    --key-schema AttributeName=pk,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST \
    --global-secondary-indexes "[{\"IndexName\":\"by_batch\",\"KeySchema\":[{\"AttributeName\":\"batch_id\",\"KeyType\":\"HASH\"},{\"AttributeName\":\"created_at\",\"KeyType\":\"RANGE\"}],\"Projection\":{\"ProjectionType\":\"ALL\"}}]" \
    >/dev/null

  echo "== funcoes"
  package validate handlers/validate/handler.py shared/chaos.py
  package delta handlers/delta/handler.py shared/chaos.py shared/quadratic.py shared/calculator.py
  package root handlers/root/handler.py shared/chaos.py shared/quadratic.py shared/calculator.py
  package persist handlers/persist/handler.py shared/chaos.py
  package submit handlers/submit/handler.py handlers/submit/generator.py shared/api_auth.py
  package dispatcher handlers/dispatcher/handler.py shared/idempotency.py

  local validate_arn delta_arn root_arn persist_arn
  validate_arn="$(create_function validate '{"Variables":{}}')"
  delta_arn="$(create_function delta '{"Variables":{}}')"
  root_arn="$(create_function root '{"Variables":{}}')"
  persist_arn="$(create_function persist "{\"Variables\":{\"RESULTS_TABLE\":\"$TABLE\"}}")"

  echo "== state machine (a partir do mesmo workflow/bhaskara.asl.yaml)"
  python3 - "$validate_arn" "$delta_arn" "$root_arn" "$persist_arn" "$dead_letter_url" > "$BUILD/definition.json" <<'PY'
import json, sys, yaml

validate, delta, root, persist, dead_letter = sys.argv[1:6]

with open("workflow/bhaskara.asl.yaml", encoding="utf-8") as handle:
    text = handle.read()

for name, value in (
    ("validate_arn", validate),
    ("delta_arn", delta),
    ("root_arn", root),
    ("persist_arn", persist),
    ("dead_letter_url", dead_letter),
):
    text = text.replace("${%s}" % name, value)

print(json.dumps(yaml.safe_load(text)))
PY

  aws_local stepfunctions create-state-machine \
    --name "$PREFIX-flow" \
    --role-arn "arn:aws:iam::$ACCOUNT:role/states-role" \
    --definition "file://$BUILD/definition.json" \
    --query stateMachineArn --output text

  local machine_arn
  machine_arn="$(state_machine_arn)"

  create_function submit "{\"Variables\":{\"ORDERS_QUEUE_URL\":\"$orders_url\",\"API_KEY\":\"$API_KEY\"}}" >/dev/null
  create_function dispatcher "{\"Variables\":{\"STATE_MACHINE_ARN\":\"$machine_arn\"}}" >/dev/null

  # A Lambda do LocalStack cria a funcao de forma assincrona: invocar enquanto
  # ela esta Pending devolve ResourceConflictException.
  echo "== aguardando as funcoes ficarem ativas"
  for name in validate delta root persist submit dispatcher; do
    aws_local lambda wait function-active-v2 --function-name "$PREFIX-$name"
  done

  # Uma invocacao de cada funcao antes de ligar o event source mapping.
  #
  # O LocalStack sobe um container por funcao na primeira invocacao, e em WSL2
  # isso pode levar mais de um minuto. Se varios comecarem juntos — que e o que
  # acontece quando o ESM entrega o primeiro lote —, todos estouram o timeout
  # de startup e as mensagens somem sem log. Aquecendo um a um, em serie, cada
  # container sobe com folga e fica vivo pelo LAMBDA_KEEPALIVE_MS.
  echo "== aquecendo as funcoes (a primeira invocacao cria o container)"
  warm validate '{"equation":{"a":1,"b":-5,"c":6},"meta":{}}'
  warm delta '{"validated":{"a":1,"b":-5,"c":6},"meta":{}}'
  warm root '{"validated":{"a":1,"b":-5,"c":6},"delta":{"value":1},"label":"x1","meta":{}}'
  warm persist '{"meta":{}}'
  warm dispatcher '{"Records":[]}'

  echo "== event source mapping"
  aws_local lambda create-event-source-mapping \
    --function-name "$PREFIX-dispatcher" \
    --event-source-arn "$(aws_local sqs get-queue-attributes --queue-url "$orders_url" --attribute-names QueueArn --query 'Attributes.QueueArn' --output text)" \
    --batch-size 10 \
    --function-response-types ReportBatchItemFailures \
    --query UUID --output text

  echo
  echo "pronto. ./scripts/local-aws.sh demo 30"
}

demo() {
  local quantity="${1:-30}"

  aws_local lambda invoke \
    --function-name "$PREFIX-submit" \
    --payload "$(printf '{"headers":{"x-api-key":"%s"},"body":"{\\"quantity\\":%d,\\"invalid_ratio\\":0.1,\\"duplicate_ratio\\":0.1,\\"chaos_ratio\\":0.2}"}' "$API_KEY" "$quantity" | base64 -w0)" \
    /dev/stdout --query StatusCode --output text >/dev/null

  echo "carga publicada; aguardando o dispatcher..."
  sleep 12
  status
}

status() {
  local machine_arn
  machine_arn="$(state_machine_arn)"

  echo
  echo "fila orders     $(aws_local sqs get-queue-attributes --queue-url "$(queue_url "$PREFIX-orders")" --attribute-names ApproximateNumberOfMessages --query 'Attributes.ApproximateNumberOfMessages' --output text)"
  echo "dead-letter     $(aws_local sqs get-queue-attributes --queue-url "$(queue_url "$PREFIX-dead-letter")" --attribute-names ApproximateNumberOfMessages --query 'Attributes.ApproximateNumberOfMessages' --output text)"

  for estado in SUCCEEDED FAILED RUNNING; do
    printf "%-15s %s\n" "$estado" \
      "$(aws_local stepfunctions list-executions --state-machine-arn "$machine_arn" --status-filter "$estado" --max-results 1000 --query 'length(executions)' --output text)"
  done

  echo
  echo "resultados na tabela: $(aws_local dynamodb scan --table-name "$TABLE" --select COUNT --query Count --output text)"
}

down() {
  aws_local stepfunctions delete-state-machine --state-machine-arn "$(state_machine_arn)" 2>/dev/null || true
  aws_local dynamodb delete-table --table-name "$TABLE" >/dev/null 2>&1 || true

  for queue in "$PREFIX-orders" "$PREFIX-dead-letter"; do
    aws_local sqs delete-queue --queue-url "$(queue_url "$queue")" 2>/dev/null || true
  done

  for name in validate delta root persist submit dispatcher; do
    aws_local lambda delete-function --function-name "$PREFIX-$name" 2>/dev/null || true
  done

  # O LAMBDA_KEEPALIVE_MS mantem vivo um container por funcao, criado pelo
  # LocalStack fora do compose. `docker compose down` nao os remove — eles
  # ficariam ocupando memoria e segurando a rede.
  local orphans
  orphans="$(docker ps -aq --filter 'name=bhaskara-localstack-lambda-' 2>/dev/null || true)"

  if [ -n "$orphans" ]; then
    docker rm -f $orphans >/dev/null
    echo "containers de funcao removidos."
  fi

  rm -rf "$BUILD"
  echo "recursos removidos."
}

case "${1:-up}" in
  up) up ;;
  demo) shift; demo "$@" ;;
  status) status ;;
  down) down ;;
  *) echo "uso: $0 {up|demo N|status|down}" >&2; exit 1 ;;
esac
