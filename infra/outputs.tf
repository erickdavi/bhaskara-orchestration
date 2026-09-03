output "api_key" {
  description = "Chave de API das duas rotas. Cole no painel; nunca versione."
  value       = random_password.api_key.result
  sensitive   = true
}

output "submit_url" {
  description = "POST /orders — gera a carga."
  value       = "${aws_apigatewayv2_api.this.api_endpoint}/orders"
}

output "flow_url" {
  description = "GET /flow — estado do fluxo, consumido pelo painel."
  value       = "${aws_apigatewayv2_api.this.api_endpoint}/flow"
}

output "api_base_url" {
  description = "Base da API, para colar no campo de configuracao do painel."
  value       = aws_apigatewayv2_api.this.api_endpoint
}

output "state_machine_arn" {
  description = "ARN da state machine. Abra no console para ver o fluxo desenhado."
  value       = aws_sfn_state_machine.flow.arn
}

output "state_machine_console_url" {
  description = "Link direto para as execucoes no console do Step Functions."
  value       = "https://${var.aws_region}.console.aws.amazon.com/states/home?region=${var.aws_region}#/statemachines/view/${aws_sfn_state_machine.flow.arn}"
}

output "orders_queue_url" {
  description = "Fila de entrada. Publicar aqui aciona o fluxo sem passar por HTTP."
  value       = aws_sqs_queue.orders.url
}

output "dead_letter_queue_url" {
  description = "Fila das mensagens recusadas, com o motivo anexado."
  value       = aws_sqs_queue.dead_letter.url
}

output "results_table" {
  description = "Tabela dos resultados e da idempotencia."
  value       = aws_dynamodb_table.results.name
}

output "state_machine_log_group" {
  description = "Log group dos eventos do fluxo, lido pelo painel."
  value       = local.state_machine_log_group
}

output "function_names" {
  description = "As sete funcoes publicadas."
  value       = { for name, function in aws_lambda_function.this : name => function.function_name }
}

output "dashboard_url" {
  description = "O painel. Abra, cole a chave de API e dispare uma carga."
  value       = "https://${aws_cloudfront_distribution.dashboard.domain_name}"
}
