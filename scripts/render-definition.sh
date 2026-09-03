#!/usr/bin/env bash
#
# Mostra a definicao que o Terraform enviaria ao Step Functions.
#
# O caminho YAML -> templatefile -> yamldecode -> jsonencode tem tres coisas que
# so quebram no apply se ninguem olhar antes:
#
#   - as ancoras YAML (&retry / *retry) precisam ser expandidas;
#   - o $$ do objeto de contexto ($$.State.Name) nao pode ser comido pelo
#     templatefile, que usa $ para interpolar;
#   - todo ${...} precisa ter uma variavel correspondente.
#
# Este script renderiza com ARNs de mentira, so para conferir a forma.
#
#   ./scripts/render-definition.sh          # a definicao inteira
#   ./scripts/render-definition.sh | jq .States.Delta
set -euo pipefail

cd "$(dirname "$0")/../infra"

# O terraform console avalia uma expressao por linha — dai a linha unica.
ACCOUNT="000000000000"
FAKE="arn:aws:lambda:us-east-1:$ACCOUNT:function"

echo "jsonencode(yamldecode(templatefile(\"\${path.module}/../workflow/bhaskara.asl.yaml\", {validate_arn=\"$FAKE:validate\", delta_arn=\"$FAKE:delta\", root_arn=\"$FAKE:root\", persist_arn=\"$FAKE:persist\", dead_letter_url=\"https://sqs.us-east-1.amazonaws.com/$ACCOUNT/dead-letter\"})))" |
  terraform console |
  python3 -c 'import json,sys; print(json.dumps(json.loads(json.loads(sys.stdin.read().strip())), indent=2, ensure_ascii=False))'
