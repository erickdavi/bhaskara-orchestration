# As sete funcoes, de uma vez.
#
# for_each em cima de local.functions em vez de sete blocos quase iguais: o que
# muda entre elas e conteudo do zip, memoria, timeout e variaveis de ambiente,
# e isso esta declarado em main.tf, num lugar so.
#
# Nao ha passo de build. O archive_file monta o zip durante o plan, a partir de
# arquivos explicitos — nada de "empacota o diretorio e torce".

data "archive_file" "function" {
  for_each = local.functions

  type        = "zip"
  output_path = "${path.module}/build/${local.name_prefix}-${each.key}.zip"

  dynamic "source" {
    for_each = each.value.files

    content {
      content = file("${local.src_dir}/${source.value}")

      # basename: os modulos vao para a raiz do zip, lado a lado. E assim que a
      # Lambda resolve "from calculator import calculate".
      filename = basename(source.value)
    }
  }
}

resource "aws_cloudwatch_log_group" "function" {
  for_each = local.functions

  name              = "/aws/lambda/${local.name_prefix}-${each.key}"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "this" {
  for_each = local.functions

  function_name = "${local.name_prefix}-${each.key}"
  description   = each.value.description

  role    = aws_iam_role.function[each.key].arn
  handler = "handler.lambda_handler"
  runtime = var.lambda_runtime

  architectures = [var.lambda_architecture]
  memory_size   = each.value.memory
  timeout       = each.value.timeout

  filename         = data.archive_file.function[each.key].output_path
  source_code_hash = data.archive_file.function[each.key].output_base64sha256

  dynamic "environment" {
    for_each = length(each.value.environment) > 0 ? [each.value.environment] : []

    content {
      variables = environment.value
    }
  }

  # O log group e criado pelo Terraform, e nao pela primeira invocacao: assim
  # ele tem retencao definida e some no destroy. Criado pela Lambda, ficaria
  # com retencao infinita e sobreviveria ao destroy.
  depends_on = [aws_cloudwatch_log_group.function]
}

# A ponte entre a coreografia e a orquestracao.
resource "aws_lambda_event_source_mapping" "orders" {
  event_source_arn = aws_sqs_queue.orders.arn
  function_name    = aws_lambda_function.this["dispatcher"].arn

  batch_size = var.dispatcher_batch_size

  # Sem isto, uma unica mensagem problematica faria a SQS reentregar o lote
  # inteiro — inclusive as mensagens que ja viraram execucao, que seriam
  # recusadas pela idempotencia mas gastariam invocacao.
  function_response_types = ["ReportBatchItemFailures"]

  scaling_config {
    # O acelerador do sistema. Ver a descricao da variavel: a conta tem 10
    # execucoes concorrentes no total e nao permite reservar concorrencia.
    maximum_concurrency = var.dispatcher_max_concurrency
  }
}
