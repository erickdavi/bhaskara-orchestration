# Oito papeis: um por funcao e um para a state machine. Nenhum tem a permissao
# do outro.
#
# Policies inline com ARN restrito, e nao managed policies: a
# AWSLambdaBasicExecutionRole concede logs sobre "*" (todos os log groups da
# conta) e a AWSLambdaSQSQueueExecutionRole concede SQS sobre "*" (todas as
# filas). Para um projeto de laboratorio isso passaria despercebido; e
# justamente por isso que vale escrever a policy certa.
#
# Ha exatamente UMA excecao a regra de nao usar "*" como Resource, e ela esta
# comentada onde aparece: a entrega de logs do Step Functions, que o proprio
# servico exige assim.

locals {
  # Cada funcao escreve apenas no seu proprio log group.
  function_log_statements = {
    for name, _ in local.functions : name => [
      {
        Sid      = "EscreveNoProprioLogGroup"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.function[name].arn}:*"
      }
    ]
  }

  function_statements = {
    # Publica na fila e nada mais. Nao consome, nao le, nao inicia execucao.
    submit = [
      {
        Sid      = "PublicaNaOrders"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.orders.arn
      }
    ]

    # Consome a fila e inicia execucoes. Espelho do submit: quem publica nao
    # consome, quem consome nao publica.
    dispatcher = [
      {
        Sid    = "ConsomeAOrders"
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
        ]
        Resource = aws_sqs_queue.orders.arn
      },
      {
        Sid      = "IniciaExecucoes"
        Effect   = "Allow"
        Action   = ["states:StartExecution"]
        Resource = local.state_machine_arn
      }
    ]

    # As tres funcoes de calculo nao tocam em nada alem do proprio log. Sao
    # funcoes puras com um print no fim.
    validate = []
    delta    = []
    root     = []

    persist = [
      {
        Sid      = "GravaOResultado"
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:GetItem"]
        Resource = aws_dynamodb_table.results.arn
      }
    ]

    # O status e o unico que enxerga tudo — e o unico sem nenhum verbo de
    # escrita. Ele consegue espiar a dead-letter (ReceiveMessage) e nunca
    # esvazia-la: nao tem DeleteMessage. Um painel que apagasse a evidencia ao
    # ser aberto seria pior que nao ter painel.
    status = [
      {
        Sid      = "LeAsFilas"
        Effect   = "Allow"
        Action   = ["sqs:GetQueueAttributes"]
        Resource = [aws_sqs_queue.orders.arn, aws_sqs_queue.dead_letter.arn]
      },
      {
        Sid      = "EspiaADeadLetter"
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage"]
        Resource = aws_sqs_queue.dead_letter.arn
      },
      {
        Sid    = "LeOHistoricoDeUmaExecucao"
        Effect = "Allow"
        Action = [
          "states:ListExecutions",
          "states:DescribeExecution",
          "states:GetExecutionHistory",
        ]
        Resource = [
          local.state_machine_arn,
          "arn:${data.aws_partition.current.partition}:states:${var.aws_region}:${data.aws_caller_identity.current.account_id}:execution:${local.state_machine_name}:*",
        ]
      },
      {
        Sid      = "LeOsEventosDoFluxo"
        Effect   = "Allow"
        Action   = ["logs:FilterLogEvents"]
        Resource = "${aws_cloudwatch_log_group.state_machine.arn}:*"
      },
      {
        Sid      = "ConsultaOsResultadosDaCarga"
        Effect   = "Allow"
        Action   = ["dynamodb:Query", "dynamodb:GetItem"]
        Resource = [aws_dynamodb_table.results.arn, "${aws_dynamodb_table.results.arn}/index/by_batch"]
      }
    ]
  }
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "function" {
  for_each = local.functions

  name               = "${local.name_prefix}-${each.key}-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "function" {
  for_each = local.functions

  name = "${local.name_prefix}-${each.key}-policy"
  role = aws_iam_role.function[each.key].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      local.function_log_statements[each.key],
      local.function_statements[each.key],
    )
  })
}

# ------------------------------------------------------------ state machine

data "aws_iam_policy_document" "states_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "state_machine" {
  name               = "${local.name_prefix}-states-role"
  assume_role_policy = data.aws_iam_policy_document.states_assume.json
}

resource "aws_iam_role_policy" "state_machine" {
  name = "${local.name_prefix}-states-policy"
  role = aws_iam_role.state_machine.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "InvocaApenasAsFuncoesDoFluxo"
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = [for name in local.flow_functions : aws_lambda_function.this[name].arn]
      },
      {
        Sid      = "PublicaNaDeadLetter"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.dead_letter.arn
      },
      {
        # A UNICA permissao com Resource "*" do projeto, e nao por escolha: a
        # entrega de logs do Step Functions e configurada por uma API de conta
        # (CloudWatch Logs delivery), que nao aceita ARN de recurso. A propria
        # documentacao da AWS instrui a conceder assim. O escopo real fica
        # limitado pelo log_destination declarado na state machine.
        Sid    = "EntregaDeLogsExigeResourceCuringa"
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery",
          "logs:GetLogDelivery",
          "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery",
          "logs:ListLogDeliveries",
          "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies",
          "logs:DescribeLogGroups",
        ]
        Resource = "*"
      }
    ]
  })
}
