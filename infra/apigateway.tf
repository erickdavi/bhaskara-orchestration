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

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_rate_limit  = var.api_throttling_rate_limit
    throttling_burst_limit = var.api_throttling_burst_limit
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
