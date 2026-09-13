# O painel de operacao, os alarmes e as consultas — como codigo.
#
# Um dashboard montado a mao no console e uma configuracao que existe em um
# lugar so, que ninguem revisa e que desaparece com a conta. Aqui ele nasce do
# mesmo `apply` que cria a state machine, some no mesmo `destroy`, e a mudanca
# nele aparece no diff.
#
# Nao confundir com o painel web do projeto (dashboard.tf): aquele mostra o
# **fluxo** — quais execucoes estao correndo, por qual estado passaram, o que
# caiu na dead-letter. Este mostra o **sistema** — latencia, erro, custo,
# saturacao. Sao perguntas diferentes, e a segunda e o objeto do Checkpoint 4.

locals {
  # A janela dos widgets. Cinco minutos e o periodo em que uma carga de
  # laboratorio cabe inteira; periodos maiores achatam o pico que se quer ver.
  metric_period = 300

  dashboard_name = "${local.name_prefix}-observabilidade"

  # As funcoes na ordem do fluxo, e nao em ordem alfabetica: o dashboard e lido
  # da esquerda para a direita como o sistema e percorrido.
  ordered_functions = ["submit", "dispatcher", "validate", "delta", "root", "persist", "status"]
}

# ---------------------------------------------------------------- dashboard

resource "aws_cloudwatch_dashboard" "observability" {
  dashboard_name = local.dashboard_name

  dashboard_body = jsonencode({
    widgets = [
      # --- faixa 1: a entrada -------------------------------------------
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "Entrada — equacoes submetidas e execucoes iniciadas"
          region = var.aws_region
          view   = "timeSeries"
          stat   = "Sum"
          period = local.metric_period
          metrics = [
            [local.metrics_namespace, "EquationsSubmitted", { label = "submetidas" }],
            [".", "ExecutionsStarted", { label = "execucoes iniciadas" }],
            # A idempotencia como linha do grafico, e nao como afirmacao no
            # README: a distancia entre as duas primeiras linhas e esta.
            [".", "ExecutionsDeduplicated", { label = "evitadas por idempotencia" }],
            [".", "PersistDuplicate", { label = "duplicatas barradas na gravacao" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 6
        height = 6
        properties = {
          title   = "Discriminante — os tres ramos do Choice"
          region  = var.aws_region
          view    = "timeSeries"
          stacked = true
          stat    = "Sum"
          period  = local.metric_period
          metrics = [
            [local.metrics_namespace, "EquationsByDeltaSign", "Sign", "positive", { label = "delta > 0" }],
            ["...", "zero", { label = "delta = 0" }],
            ["...", "negative", { label = "delta < 0" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 18
        y      = 0
        width  = 6
        height = 6
        properties = {
          title  = "Recusas por motivo"
          region = var.aws_region
          view   = "timeSeries"
          stat   = "Sum"
          period = local.metric_period
          metrics = [
            [local.metrics_namespace, "ValidationRejected", "Reason", "a_is_zero"],
            ["...", "missing_coefficient"],
            ["...", "coefficient_not_number"],
            ["...", "malformed_json"],
          ]
        }
      },

      # --- faixa 2: latencia --------------------------------------------
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "Duracao por estado — p95"
          region = var.aws_region
          view   = "timeSeries"
          stat   = "p95"
          period = local.metric_period
          # A base da analise de otimizacao. Os dois ramos do Parallel aparecem
          # separados: e o que permite dizer se dividir a conta em duas Lambdas
          # custa mais do que rende.
          metrics = [
            [local.metrics_namespace, "HandlerDuration", "Service", "validate", "State", "Validate"],
            ["...", "delta", ".", "Delta"],
            ["...", "root", ".", "RootX1"],
            ["...", "root", ".", "RootX2"],
            ["...", "root", ".", "RootDouble"],
            ["...", "persist", ".", "Persist"],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "Latencia ponta a ponta — da fila ate a gravacao"
          region = var.aws_region
          view   = "timeSeries"
          period = local.metric_period
          # Media e p99 no mesmo grafico: a media diz como o sistema se comporta,
          # o p99 diz o que o pior caso custa a quem espera.
          metrics = [
            [local.metrics_namespace, "EndToEndLatency", { stat = "Average", label = "media" }],
            [".", ".", { stat = "p95", label = "p95" }],
            [".", ".", { stat = "p99", label = "p99" }],
          ]
        }
      },

      # --- faixa 3: falha e saturacao -----------------------------------
      {
        type   = "metric"
        x      = 0
        y      = 12
        width  = 8
        height = 6
        properties = {
          title  = "Execucoes da state machine"
          region = var.aws_region
          view   = "timeSeries"
          stat   = "Sum"
          period = local.metric_period
          metrics = [
            ["AWS/States", "ExecutionsStarted", "StateMachineArn", local.state_machine_arn],
            [".", "ExecutionsSucceeded", ".", "."],
            [".", "ExecutionsFailed", ".", "."],
            [".", "ExecutionsTimedOut", ".", "."],
          ]
        }
      },
      {
        type   = "metric"
        x      = 8
        y      = 12
        width  = 8
        height = 6
        properties = {
          title  = "Retry, caos e cold start"
          region = var.aws_region
          view   = "timeSeries"
          stat   = "Sum"
          period = local.metric_period
          metrics = [
            # ChaosInjected existe para poder ser SUBTRAIDO do resto: sem
            # separar a falha pedida da falha real, a analise de confiabilidade
            # mede a propria demonstracao.
            [local.metrics_namespace, "RetryAttempt", { label = "invocacoes que sao reentrega" }],
            [".", "ChaosInjected", { label = "falhas injetadas de proposito" }],
            [".", "ColdStart", { label = "invocacoes frias" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 16
        y      = 12
        width  = 8
        height = 6
        properties = {
          title  = "Filas — profundidade e idade"
          region = var.aws_region
          view   = "timeSeries"
          period = local.metric_period
          metrics = [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", local.orders_queue_name, { stat = "Maximum", label = "orders — na fila" }],
            [".", "ApproximateAgeOfOldestMessage", ".", ".", { stat = "Maximum", label = "orders — idade da mais antiga (s)" }],
            [".", "ApproximateNumberOfMessagesVisible", ".", local.dead_letter_queue_name, { stat = "Maximum", label = "dead-letter" }],
          ]
        }
      },

      # --- faixa 4: o que a AWS mede sozinha ----------------------------
      {
        type   = "metric"
        x      = 0
        y      = 18
        width  = 12
        height = 6
        properties = {
          title  = "Lambda — throttling e erro"
          region = var.aws_region
          view   = "timeSeries"
          stat   = "Sum"
          period = local.metric_period
          # Throttles e a metrica mais importante deste projeto: a conta tem 10
          # execucoes concorrentes no total, e o retry so transforma throttling
          # em espera enquanto houver paciencia no backoff.
          metrics = concat(
            [for name in local.ordered_functions : [
              "AWS/Lambda", "Throttles", "FunctionName", "${local.name_prefix}-${name}",
              { label = "throttles ${name}" }
            ]],
            [for name in local.ordered_functions : [
              "AWS/Lambda", "Errors", "FunctionName", "${local.name_prefix}-${name}",
              { label = "erros ${name}" }
            ]],
          )
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 18
        width  = 12
        height = 6
        properties = {
          title  = "API Gateway — requisicoes, latencia e recusa"
          region = var.aws_region
          view   = "timeSeries"
          period = local.metric_period
          metrics = [
            ["AWS/ApiGateway", "Count", "ApiId", aws_apigatewayv2_api.this.id, { stat = "Sum", label = "requisicoes" }],
            [".", "4xx", ".", ".", { stat = "Sum", label = "4xx (inclui a chave errada)" }],
            [".", "5xx", ".", ".", { stat = "Sum", label = "5xx" }],
            [".", "Latency", ".", ".", { stat = "p95", label = "latencia p95" }],
          ]
        }
      },

      # --- faixa 5: o log, dentro do painel ------------------------------
      {
        type   = "log"
        x      = 0
        y      = 24
        width  = 24
        height = 6
        properties = {
          title  = "Ultimas recusas e falhas, com o motivo"
          region = var.aws_region
          view   = "table"
          # O widget de log fecha o ciclo: do grafico que mostra que algo
          # aconteceu para a linha que diz o que foi, sem trocar de tela.
          query = join("\n| ", [
            "SOURCE ${join("' , '", [for name in local.ordered_functions : "/aws/lambda/${local.name_prefix}-${name}"])}",
            "fields @timestamp, service, state, event, execution, error_type, detail",
            "filter level in [\"ERROR\", \"WARN\"]",
            "sort @timestamp desc",
            "limit 40",
          ])
        }
      },
    ]
  })
}

# ------------------------------------------------------------------ alarmes
#
# Cinco, e nao quinze. Um alarme que dispara sem que ninguem saiba o que fazer
# treina quem o recebe a ignora-lo, e a partir dai o proximo tambem sera
# ignorado. Cada um destes tem uma acao obvia associada, escrita ao lado.

resource "aws_sns_topic" "alerts" {
  name = "${local.name_prefix}-alertas"

  # Diferente do DynamoDB, do S3 e dos log groups, um topico SNS nasce **sem**
  # criptografia — nao ha padrao a herdar. Por isso aqui a chave e ligada em vez
  # de dispensada: `alias/aws/sns` e gerenciada pela AWS e nao tem custo mensal,
  # ao contrario de uma CMK propria. Nao ha o que trocar por US$ 1/mes.
  kms_master_key_id = "alias/aws/sns"
}

# A inscricao e opcional e vem vazia por padrao. Inscrever um e-mail exige uma
# confirmacao manual, por link, que o Terraform nao consegue completar — o
# recurso ficaria "pending confirmation" para sempre no state de quem so quisesse
# ver o alarme mudar de cor no console.
resource "aws_sns_topic_subscription" "email" {
  count = var.alert_email == "" ? 0 : 1

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_metric_alarm" "dead_letter" {
  alarm_name        = "${local.name_prefix}-dead-letter-com-mensagem"
  alarm_description = "Ha equacao recusada esperando na dead-letter. Acao: abrir o painel, ler o motivo anexado e decidir entre corrigir a origem e descartar."

  namespace   = "AWS/SQS"
  metric_name = "ApproximateNumberOfMessagesVisible"
  dimensions  = { QueueName = local.dead_letter_queue_name }

  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"

  # missing = notBreaching: fila sem metrica e fila sem mensagem. Sem isto o
  # alarme fica em INSUFFICIENT_DATA quando o sistema esta parado, que e
  # justamente o estado normal de um laboratorio.
  treat_missing_data = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "executions_failed" {
  alarm_name        = "${local.name_prefix}-execucoes-falhando"
  alarm_description = "Execucoes falhando acima do esperado para uma carga com caos. Acao: comparar ExecutionsFailed com ChaosInjected — se a diferenca for grande, a falha nao foi pedida."

  namespace   = "AWS/States"
  metric_name = "ExecutionsFailed"
  dimensions  = { StateMachineArn = local.state_machine_arn }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = var.failed_executions_threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
}

# Throttling e a metrica que mais importa neste projeto: a conta tem 10
# execucoes Lambda concorrentes no total, compartilhadas com os checkpoints
# anteriores. O Retry transforma throttling em espera — mas so ate esgotar as
# seis tentativas do retrier de Lambda.TooManyRequestsException.
resource "aws_cloudwatch_metric_alarm" "throttling" {
  alarm_name        = "${local.name_prefix}-throttling-de-lambda"
  alarm_description = "As funcoes estao sendo estranguladas por falta de concorrencia. Acao: reduzir dispatcher_max_concurrency ou pedir aumento da quota da conta, que e gratuito."

  threshold           = var.throttle_threshold
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"

  # Metric math em vez de sete alarmes: o que interessa e se a conta saturou,
  # e nao qual das sete funcoes chegou primeiro na fila do estrangulamento.
  metric_query {
    id          = "total"
    expression  = join(" + ", [for index in range(length(local.ordered_functions)) : "f${index}"])
    label       = "throttles somados"
    return_data = true
  }

  dynamic "metric_query" {
    for_each = { for index, name in local.ordered_functions : index => name }

    content {
      id = "f${metric_query.key}"

      metric {
        namespace   = "AWS/Lambda"
        metric_name = "Throttles"
        dimensions  = { FunctionName = "${local.name_prefix}-${metric_query.value}" }
        period      = 300
        stat        = "Sum"
      }
    }
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "queue_draining" {
  alarm_name        = "${local.name_prefix}-fila-drenando-devagar"
  alarm_description = "A mensagem mais antiga da orders esta esperando demais. Acao: e esperado logo apos uma carga grande; se persistir com a fila vazia, o event source mapping parou."

  namespace   = "AWS/SQS"
  metric_name = "ApproximateAgeOfOldestMessage"
  dimensions  = { QueueName = local.orders_queue_name }

  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  threshold           = var.queue_age_threshold_seconds
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "api_errors" {
  alarm_name        = "${local.name_prefix}-api-com-erro-5xx"
  alarm_description = "A borda esta devolvendo erro de servidor. Acao: ver o log do submit ou do status — 5xx aqui e defeito nosso, nao do cliente."

  namespace   = "AWS/ApiGateway"
  metric_name = "5xx"
  dimensions  = { ApiId = aws_apigatewayv2_api.this.id }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
}

# ------------------------------------------------------------ metric filter
#
# A unica metrica que nao vem do EMF. O 403 acontece **antes** de qualquer
# handler rodar quando a chave esta ausente — o handler registra o seu proprio
# `request_unauthorized`, mas uma requisicao barrada pelo throttling do stage
# nem chega la. O access log ve as duas.
resource "aws_cloudwatch_log_metric_filter" "unauthorized" {
  name           = "${local.name_prefix}-requisicoes-recusadas"
  log_group_name = aws_cloudwatch_log_group.api.name

  pattern = "{ $.status = \"403\" }"

  metric_transformation {
    name      = "UnauthorizedRequests"
    namespace = local.metrics_namespace
    value     = "1"
    unit      = "Count"

    # Sem o default, a serie fica com buracos em vez de zeros, e nao da para
    # distinguir "ninguem tentou" de "ninguem mediu".
    default_value = 0
  }
}

# --------------------------------------------------- consultas versionadas
#
# Cinco consultas do Logs Insights, salvas pelo Terraform. Uma query que existe
# so no historico do navegador de quem a escreveu nao e observabilidade: e
# conhecimento tacito de uma pessoa so.

locals {
  function_log_groups = [for name in local.ordered_functions : "/aws/lambda/${local.name_prefix}-${name}"]
}

resource "aws_cloudwatch_query_definition" "trace_execution" {
  name            = "${local.dashboard_name}/1 — o caminho de uma equacao"
  log_group_names = local.function_log_groups

  # Troque a chave e veja os ~7 estados de uma unica equacao, em ordem,
  # incluindo as tentativas que falharam. E para isto que o correlation id
  # existe.
  query_string = <<-QUERY
    fields @timestamp, service, state, event, level, attempt, duration_ms, detail
    | filter execution = "COLE-A-CHAVE-AQUI"
    | sort @timestamp asc
  QUERY
}

resource "aws_cloudwatch_query_definition" "latency_by_state" {
  name            = "${local.dashboard_name}/2 — duracao por estado (p50, p95, p99)"
  log_group_names = local.function_log_groups

  query_string = <<-QUERY
    fields service, state, duration_ms
    | filter ispresent(duration_ms) and ispresent(state)
    | stats count(*) as invocacoes,
            pct(duration_ms, 50) as p50,
            pct(duration_ms, 95) as p95,
            pct(duration_ms, 99) as p99
        by service, state
    | sort p95 desc
  QUERY
}

resource "aws_cloudwatch_query_definition" "cold_starts" {
  name            = "${local.dashboard_name}/3 — cold start e tempo de inicializacao"
  log_group_names = local.function_log_groups

  # `record.metrics.initDurationMs` so existe porque o log da plataforma esta
  # em JSON (ciclo 8). Em texto, isto seria uma expressao regular sobre a linha
  # de REPORT.
  query_string = <<-QUERY
    fields @logStream, record.metrics.initDurationMs as init_ms,
           record.metrics.billedDurationMs as billed_ms,
           record.metrics.maxMemoryUsedMB as memoria_mb
    | filter type = "platform.report"
    | stats count(*) as invocacoes,
            sum(ispresent(init_ms)) as frias,
            avg(init_ms) as init_medio,
            avg(billed_ms) as billed_medio,
            max(memoria_mb) as memoria_pico
        by @log
    | sort frias desc
  QUERY
}

resource "aws_cloudwatch_query_definition" "rejections" {
  name            = "${local.dashboard_name}/4 — por que as equacoes foram recusadas"
  log_group_names = local.function_log_groups

  query_string = <<-QUERY
    fields reason, detail, execution
    | filter event = "equation_rejected"
    | stats count(*) as total by reason
    | sort total desc
  QUERY
}

resource "aws_cloudwatch_query_definition" "log_volume" {
  name            = "${local.dashboard_name}/5 — quanto log cada execucao produz"
  log_group_names = concat(local.function_log_groups, [local.state_machine_log_group])

  # A consulta que fundamenta a otimizacao de custo de log: o `level = ALL` com
  # `include_execution_data` da state machine gera muito mais byte por execucao
  # que as sete funcoes somadas. Aqui isso vira numero.
  query_string = <<-QUERY
    stats sum(strlen(@message)) as bytes, count(*) as linhas by @log
    | sort bytes desc
  QUERY
}
