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
    level = "ALL"

    # Ver a otimizacao 2 em docs/observabilidade.md e a medicao em
    # docs/cycle-13.md. Este e o parametro mais caro do projeto em ingestao de
    # log; o valor padrao continua `true` porque o painel depende do que ele
    # grava, e a variavel existe para que a troca seja uma linha e nao um
    # garimpo.
    include_execution_data = var.state_machine_execution_data
  }

  tracing_configuration {
    # Ligado no Checkpoint 4, em 13/09/2026, revertendo a decisao do Checkpoint 3.
    #
    # O que estava escrito aqui era: "o traco distribuido nao acrescenta nada
    # que o historico da execucao ja nao mostre neste fluxo, e e cobrado por
    # trace". Era verdade sob a premissa daquele checkpoint, em que a pergunta
    # era "o fluxo esta correto?" — e para isso o GetExecutionHistory basta.
    #
    # A premissa mudou. A pergunta agora e "onde o tempo e gasto?", e o
    # historico nao responde: ele mostra a duracao de cada estado, mas nao
    # separa a invocacao do overhead de transicao, nem mostra os dois ramos do
    # Parallel sobrepostos no tempo. O service map mostra.
    #
    # O custo continua o mesmo, e continua irrelevante: a camada gratuita cobre
    # 100.000 traces registrados por mes e uma carga de 100 equacoes gera ~100.
    enabled = var.xray_enabled
  }
}
