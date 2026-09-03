# HTTP API com duas rotas: uma para alimentar a fila, outra para observar.
#
#   POST /orders   submit    gera a carga
#   GET  /flow     status    devolve o estado do fluxo para o painel
#
# Nenhuma rota executa a equacao. Quem processa e a state machine, acionada por
# mensagem — nao ha URL que resolva uma equacao neste sistema, e essa e a
# diferenca de arquitetura em relacao ao Checkpoint 1.
#
# O HTTP API nao tem API key nativa (isso e recurso do REST API v1). A
# alternativa no gateway seria um Lambda authorizer: mais uma funcao, mais uma
# role e mais um log group para comparar duas strings. A verificacao acontece
# dentro de cada handler, com hmac.compare_digest, e o throttling do stage
# limita o abuso de quem nem tem a chave.

resource "aws_apigatewayv2_api" "this" {
  name          = local.name_prefix
  protocol_type = "HTTP"
  description   = "Borda do Checkpoint 3: gera carga e observa o fluxo"

  cors_configuration {
    # O painel e servido de outro dominio (CloudFront), entao o browser exige
    # CORS. Os metodos e headers sao os minimos que as duas rotas usam.
    allow_origins = ["*"]
    allow_methods = ["GET", "POST", "OPTIONS"]
    allow_headers = ["content-type", "x-api-key"]
    max_age       = 300
  }
}

# Log de acesso do stage.
#
# Este endpoint transforma uma requisicao em ate 2.000 execucoes de state
# machine. Se alguem descobrir a URL e comecar a bater nela, a chave de API vai
# barrar — mas sem log de acesso nao ha como saber que isso aconteceu, de onde
# veio, nem quantas vezes. Throttling sem observabilidade e um alarme mudo.
resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/apigateway/${local.name_prefix}"
  retention_in_days = var.log_retention_days
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_rate_limit  = var.api_throttling_rate_limit
    throttling_burst_limit = var.api_throttling_burst_limit
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api.arn

    # Uma linha JSON por requisicao, para que o Logs Insights consulte por
    # campo. `status` e `ip` sao o que interessa numa investigacao: 403 em
    # sequencia do mesmo IP e alguem tentando adivinhar a chave.
    format = jsonencode({
      requestId      = "$context.requestId"
      ip             = "$context.identity.sourceIp"
      requestTime    = "$context.requestTime"
      routeKey       = "$context.routeKey"
      status         = "$context.status"
      responseLength = "$context.responseLength"
      integrationErr = "$context.integrationErrorMessage"
      latency        = "$context.responseLatency"
    })
  }
}

locals {
  routes = {
    submit = "POST /orders"
    status = "GET /flow"
  }
}

resource "aws_apigatewayv2_integration" "this" {
  for_each = local.routes

  api_id                 = aws_apigatewayv2_api.this.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.this[each.key].invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "this" {
  for_each = local.routes

  api_id    = aws_apigatewayv2_api.this.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.this[each.key].id}"
}

resource "aws_lambda_permission" "api" {
  for_each = local.routes

  statement_id  = "AllowInvokeFrom${title(each.key)}Route"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.this[each.key].function_name
  principal     = "apigateway.amazonaws.com"

  # Restrito ao metodo e caminho exatos, em vez de liberar a API inteira.
  source_arn = "${aws_apigatewayv2_api.this.execution_arn}/*/${split(" ", each.value)[0]}${split(" ", each.value)[1]}"
}
