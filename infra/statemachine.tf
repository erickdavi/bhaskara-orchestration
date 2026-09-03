# A state machine.
#
# A definicao vem de workflow/bhaskara.asl.yaml — o mesmo arquivo que
# local/engine.py interpreta na demonstracao local. O caminho e:
#
#   templatefile   substitui ${validate_arn}, ${dead_letter_url} e companhia
#   yamldecode     YAML -> estrutura Terraform
#   jsonencode     estrutura -> JSON, que e o que a API do servico aceita
#
# Escrever a definicao em HCL geraria um JSON que so o Terraform entende, e a
# demonstracao local teria de reimplementar o fluxo por fora. Duas descricoes
# do mesmo fluxo divergem no primeiro ajuste.

resource "aws_cloudwatch_log_group" "state_machine" {
  name              = local.state_machine_log_group
  retention_in_days = var.log_retention_days
}

resource "aws_sfn_state_machine" "flow" {
  name     = local.state_machine_name
  role_arn = aws_iam_role.state_machine.arn

  # STANDARD, e nao EXPRESS: o historico de 90 dias e consultavel por API, e e
  # dele que sai a timeline do painel. Com EXPRESS o historico so existiria no
  # CloudWatch Logs, com latencia de ingestao e sem GetExecutionHistory.
  type = "STANDARD"

  definition = jsonencode(yamldecode(templatefile("${local.workflow_dir}/bhaskara.asl.yaml", {
    validate_arn    = aws_lambda_function.this["validate"].arn
    delta_arn       = aws_lambda_function.this["delta"].arn
    root_arn        = aws_lambda_function.this["root"].arn
    persist_arn     = aws_lambda_function.this["persist"].arn
    dead_letter_url = aws_sqs_queue.dead_letter.url
  })))

  logging_configuration {
    log_destination = "${aws_cloudwatch_log_group.state_machine.arn}:*"

    # ALL, e nao ERROR: o painel conta quantas execucoes passaram por cada
    # estado, e isso vem dos eventos de entrada e saida de estado. Com ERROR o
    # diagrama so acenderia quando algo desse errado.
    level                  = "ALL"
    include_execution_data = true
  }

  tracing_configuration {
    # X-Ray desligado: o traco distribuido nao acrescenta nada que o historico
    # da execucao ja nao mostre neste fluxo, e e cobrado por trace.
    enabled = false
  }
}
