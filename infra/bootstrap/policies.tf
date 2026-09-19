locals {
  account   = data.aws_caller_identity.current.account_id
  partition = data.aws_partition.current.partition

  # Tudo que a stack principal cria comeca com o nome do projeto. E esse
  # prefixo que transforma "pode mexer na conta" em "pode mexer neste projeto".
  prefixo = "${var.project_name}-*"

  arn_lambda   = "arn:${local.partition}:lambda:${var.aws_region}:${local.account}:function:${local.prefixo}"
  arn_sqs      = "arn:${local.partition}:sqs:${var.aws_region}:${local.account}:${local.prefixo}"
  arn_dynamodb = "arn:${local.partition}:dynamodb:${var.aws_region}:${local.account}:table/${local.prefixo}"
  arn_states   = "arn:${local.partition}:states:${var.aws_region}:${local.account}:stateMachine:${local.prefixo}"
  arn_logs     = "arn:${local.partition}:logs:${var.aws_region}:${local.account}:log-group:*${var.project_name}*"
  arn_sns      = "arn:${local.partition}:sns:${var.aws_region}:${local.account}:${local.prefixo}"
  arn_role     = "arn:${local.partition}:iam::${local.account}:role/${local.prefixo}"
  # O bucket do painel tem o sufixo do numero da conta; o do state tem
  # "tfstate" no meio. Mirar o padrao do painel deixa o state de fora.
  arn_painel    = "arn:${local.partition}:s3:::${var.project_name}-*-dashboard-${local.account}"
  arn_alarme    = "arn:${local.partition}:cloudwatch:${var.aws_region}:${local.account}:alarm:${local.prefixo}"
  arn_dashboard = "arn:${local.partition}:cloudwatch::${local.account}:dashboard/${local.prefixo}"

  # O refresh do Terraform le o estado atual de todos os recursos antes de
  # comparar com o codigo. As duas funcoes precisam dessas acoes: o plano para
  # calcular a diferenca, o deploy para saber o que existe antes de mexer.
  #
  # Acoes de leitura na maioria dos servicos nao aceitam restricao por recurso,
  # entao o curinga em Resource e imposicao da API. O que limita o risco e nao
  # haver aqui um unico verbo de escrita.
  acoes_leitura = [
    "lambda:Get*",
    "lambda:List*",
    "iam:Get*",
    "iam:List*",
    "sqs:Get*",
    "sqs:List*",
    "dynamodb:Describe*",
    "dynamodb:List*",
    "states:Describe*",
    "states:List*",
    "logs:Describe*",
    "logs:List*",
    "logs:Get*",
    "apigateway:GET",
    "cloudfront:Get*",
    "cloudfront:List*",
    "sns:Get*",
    "sns:List*",
    "cloudwatch:Describe*",
    "cloudwatch:Get*",
    "cloudwatch:List*",
    "s3:GetBucket*",
    "s3:GetLifecycleConfiguration",
    "s3:GetEncryptionConfiguration",
    "s3:GetAccelerateConfiguration",
    "s3:GetReplicationConfiguration",
    "s3:ListAllMyBuckets",
    "sts:GetCallerIdentity",
    "tag:GetResources",
  ]
}

# ------------------------------------------------------ fronteira de permissao
#
# O deploy precisa criar funcoes de acesso, porque a stack tem oito delas, uma
# por Lambda. Isso abre um caminho de escalada: quem consegue alterar o
# Terraform faz o pipeline criar uma funcao com permissao de administrador e
# depois a assume.
#
# Esta fronteira e o teto do que qualquer funcao criada pelo pipeline consegue
# fazer, independentemente da politica anexada a ela. A politica do deploy exige
# que toda funcao nova a carregue, entao a tentativa de criar uma funcao mais
# ampla falha na AWS, antes de qualquer revisao de codigo.
#
# O conteudo e a uniao exata do que as oito funcoes existentes usam. Uma funcao
# nova que precise de mais exige alterar este arquivo, que esta fora do alcance
# do proprio pipeline.
data "aws_iam_policy_document" "boundary" {
  #checkov:skip=CKV_AWS_356:Uma fronteira de permissao e um teto, nao uma concessao. Ela nao da acesso a nada; ela limita o que outras politicas conseguem dar. Restringir o recurso aqui inverteria o proposito do mecanismo.
  #checkov:skip=CKV_AWS_111:Mesmo motivo. Os verbos de escrita listados sao o maximo que uma funcao criada pelo pipeline pode alcancar, e a politica dela ainda precisa conceder cada um.

  statement {
    sid    = "TetoDosServicosDoProjeto"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:FilterLogEvents",
      "logs:CreateLogDelivery",
      "logs:GetLogDelivery",
      "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies",
      "logs:DescribeLogGroups",
      "sqs:SendMessage",
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "dynamodb:PutItem",
      "dynamodb:GetItem",
      "dynamodb:Query",
      "states:StartExecution",
      "states:ListExecutions",
      "states:DescribeExecution",
      "states:GetExecutionHistory",
      "lambda:InvokeFunction",
      "xray:PutTraceSegments",
      "xray:PutTelemetryRecords",
      "xray:GetSamplingRules",
      "xray:GetSamplingTargets",
    ]

    resources = ["*"]
  }

  # A negacao que fecha o caminho de escalada. Mesmo que uma politica de
  # administrador seja anexada a uma funcao criada pelo pipeline, ela nao
  # consegue mexer em identidade nenhuma.
  statement {
    sid       = "NuncaMexeEmIdentidade"
    effect    = "Deny"
    actions   = ["iam:*", "sts:AssumeRole", "organizations:*", "account:*"]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "boundary" {
  name        = local.boundary_name
  description = "Teto de permissao das funcoes de acesso criadas pelo pipeline."
  policy      = data.aws_iam_policy_document.boundary.json
}

# ------------------------------------------------------------ funcao de plano

data "aws_iam_policy_document" "plan" {
  #checkov:skip=CKV_AWS_356:As acoes sao todas de leitura, e a maioria dos servicos nao aceita restricao por recurso em List e Describe. Nao ha um unico verbo de escrita neste documento fora do bucket de state, que e restrito por ARN.

  statement {
    sid       = "LeituraParaOPlano"
    effect    = "Allow"
    actions   = local.acoes_leitura
    resources = ["*"]
  }

  # O plano escreve o arquivo de trava para que dois planos simultaneos nao
  # leiam um state em meio a reescrita.
  statement {
    sid       = "StateELockDoBackend"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.state.arn}/*"]
  }

  statement {
    sid       = "ListaOBucketDoState"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.state.arn]
  }

  # O mesmo verbo, no bucket do painel, e pelo mesmo motivo: sem ele o refresh
  # do plano leva 403 no HeadBucket, conclui que o bucket nao existe e calcula
  # um plano que recria o que esta no ar. O plano nao aplica nada, mas um plano
  # que mente sobre a realidade e pior que um plano que falha.
  statement {
    sid       = "ListaOBucketDoPainel"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [local.arn_painel]
  }

  # Os cinco objetos do painel tambem entram no refresh, um a um. O provider le
  # cada um com HeadObject, que pede s3:GetObject, e com GetObjectTagging.
  #
  # Este buraco estava escondido atras do anterior: enquanto o bucket era lido
  # como inexistente, os objetos eram calculados como criacao e nunca chegavam a
  # ser lidos. Consertar a leitura do bucket foi o que revelou a leitura dos
  # objetos — permissao descoberta em camadas, cada uma so visivel depois que a
  # de cima sai da frente.
  #
  # s3:GetObjectAcl ficou de fora: o bucket usa BucketOwnerEnforced, que desliga
  # ACL, e nenhum dos objetos declara o atributo. O provider nao chega a pedir.
  statement {
    sid       = "LeOsObjetosDoPainel"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:GetObjectTagging"]
    resources = ["${local.arn_painel}/*"]
  }
}

resource "aws_iam_role_policy" "plan" {
  name   = "${local.plan_role_name}-policy"
  role   = aws_iam_role.plan.id
  policy = data.aws_iam_policy_document.plan.json
}
