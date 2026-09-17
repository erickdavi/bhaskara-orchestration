# A politica da funcao que aplica a infraestrutura.
#
# Duas ideias organizam o que vem abaixo. A primeira e o prefixo: quase todo
# servico da AWS aceita restringir a acao ao nome do recurso, e todos os
# recursos deste projeto comecam com `bhaskara-orchestration`. A segunda e a
# fronteira de permissao, que trata do unico servico onde restringir por nome
# nao basta, porque criar identidade e o caminho por onde se escapa de qualquer
# outra restricao.
#
# Onde aparece Resource com curinga, e porque a API nao aceita outra coisa. Cada
# caso desses tem o motivo escrito ao lado.

data "aws_iam_policy_document" "deploy" {
  #checkov:skip=CKV_AWS_356:Tres casos usam curinga, cada um comentado onde aparece: o vinculo entre fila e funcao tem identificador sorteado pela AWS, a entrega de log do Step Functions e API de conta, e a criacao de distribuicao do CloudFront nao tem recurso antes de existir. Todo o resto e restrito pelo prefixo do projeto.
  #checkov:skip=CKV_AWS_111:Os verbos de escrita sem restricao sao os tres casos acima. A escalada por IAM, que e o risco real, esta fechada pela fronteira obrigatoria na criacao e pela negacao explicita de remove-la.

  statement {
    sid       = "LeituraParaORefresh"
    effect    = "Allow"
    actions   = local.acoes_leitura
    resources = ["*"]
  }

  statement {
    sid       = "GerenciaAsFuncoesDoProjeto"
    effect    = "Allow"
    actions   = ["lambda:*"]
    resources = [local.arn_lambda]
  }

  # O identificador de um vinculo entre fila e funcao e um UUID sorteado pela
  # AWS no momento da criacao, sem nome e sem prefixo. Nao ha o que restringir.
  statement {
    sid    = "GerenciaOVinculoEntreFilaEFuncao"
    effect = "Allow"

    actions = [
      "lambda:CreateEventSourceMapping",
      "lambda:UpdateEventSourceMapping",
      "lambda:DeleteEventSourceMapping",
    ]

    resources = ["*"]
  }

  statement {
    sid       = "GerenciaAsFilas"
    effect    = "Allow"
    actions   = ["sqs:*"]
    resources = [local.arn_sqs]
  }

  statement {
    sid       = "GerenciaATabela"
    effect    = "Allow"
    actions   = ["dynamodb:*"]
    resources = [local.arn_dynamodb, "${local.arn_dynamodb}/index/*"]
  }

  statement {
    sid       = "GerenciaAMaquinaDeEstados"
    effect    = "Allow"
    actions   = ["states:*"]
    resources = [local.arn_states]
  }

  statement {
    sid       = "GerenciaOsGruposDeLog"
    effect    = "Allow"
    actions   = ["logs:*"]
    resources = ["${local.arn_logs}", "${local.arn_logs}:*"]
  }

  # A entrega de log do Step Functions e configurada por uma API de conta, que
  # nao aceita ARN de recurso. E a mesma excecao ja documentada na stack
  # principal, agora refletida aqui.
  statement {
    sid    = "EntregaDeLogEhApiDeConta"
    effect = "Allow"

    actions = [
      "logs:CreateLogDelivery",
      "logs:GetLogDelivery",
      "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutResourcePolicy",
      "logs:DeleteResourcePolicy",
      "logs:DescribeResourcePolicies",
    ]

    resources = ["*"]
  }

  statement {
    sid       = "GerenciaOTopicoDeAlertas"
    effect    = "Allow"
    actions   = ["sns:*"]
    resources = [local.arn_sns]
  }

  # O bucket do painel, e somente ele.
  #
  # A primeira versao usava `s3:*` sobre o prefixo do projeto, o que alcancava
  # tambem o bucket do state, cujo nome comeca igual. As acoes destrutivas ali
  # ja estavam negadas mais abaixo, mas apagar versao antiga do state nao
  # estava. Enumerar os verbos e mirar o nome do painel fecha os dois furos.
  statement {
    sid    = "GerenciaOBucketDoPainel"
    effect = "Allow"

    actions = [
      "s3:CreateBucket",
      "s3:DeleteBucket",
      "s3:PutBucketPolicy",
      "s3:DeleteBucketPolicy",
      "s3:PutBucketPublicAccessBlock",
      "s3:PutBucketOwnershipControls",
      "s3:PutEncryptionConfiguration",
      "s3:PutBucketTagging",
      "s3:PutBucketVersioning",
      "s3:PutObject",
      "s3:GetObject",
      "s3:DeleteObject",
    ]

    resources = [
      "${local.arn_painel}",
      "${local.arn_painel}/*",
    ]
  }

  # Alarme aceita restricao por nome; painel do CloudWatch tambem. As acoes de
  # criar painel exigem o nome no ARN, que segue o mesmo prefixo.
  statement {
    sid    = "GerenciaAlarmesEPaineis"
    effect = "Allow"

    actions = [
      "cloudwatch:PutMetricAlarm",
      "cloudwatch:DeleteAlarms",
      "cloudwatch:TagResource",
      "cloudwatch:UntagResource",
    ]

    resources = [local.arn_alarme]
  }

  statement {
    sid       = "GerenciaOPainel"
    effect    = "Allow"
    actions   = ["cloudwatch:PutDashboard", "cloudwatch:DeleteDashboards"]
    resources = [local.arn_dashboard]
  }

  # Consulta salva do Logs Insights nao tem ARN proprio: ela pertence a conta.
  statement {
    sid    = "GerenciaAsConsultasSalvas"
    effect = "Allow"

    actions = [
      "logs:PutQueryDefinition",
      "logs:DeleteQueryDefinition",
      "logs:DescribeQueryDefinitions",
    ]

    resources = ["*"]
  }

  # O HTTP API identifica recursos por um caminho gerado pela AWS, sem nome. O
  # curinga fica limitado a regiao do projeto.
  statement {
    sid       = "GerenciaAApi"
    effect    = "Allow"
    actions   = ["apigateway:*"]
    resources = ["arn:${local.partition}:apigateway:${var.aws_region}::/*"]
  }

  # Distribuicao do CloudFront recebe o identificador na criacao, entao a acao
  # de criar nao tem recurso a que se prender.
  statement {
    sid    = "GerenciaADistribuicao"
    effect = "Allow"

    actions = [
      "cloudfront:CreateDistribution",
      "cloudfront:UpdateDistribution",
      "cloudfront:DeleteDistribution",
      "cloudfront:TagResource",
      "cloudfront:UntagResource",
      "cloudfront:CreateOriginAccessControl",
      "cloudfront:UpdateOriginAccessControl",
      "cloudfront:DeleteOriginAccessControl",
    ]

    resources = ["*"]
  }

  # ------------------------------------------------------------------- IAM

  # Criar funcao de acesso, sempre com a fronteira anexada. Sem esta condicao,
  # esta seria a permissao que anula todas as outras.
  #
  # A condicao cobre somente os verbos que recebem a fronteira no proprio
  # pedido. Anexar politica a uma funcao que ja existe fica no bloco seguinte,
  # sem condicao, porque a fronteira ja esta na funcao e continua limitando o
  # efeito de qualquer politica que venha depois.
  statement {
    sid    = "CriaFuncoesSomenteComFronteira"
    effect = "Allow"

    actions = [
      "iam:CreateRole",
      "iam:PutRolePermissionsBoundary",
    ]

    resources = [local.arn_role]

    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [aws_iam_policy.boundary.arn]
    }
  }

  statement {
    sid    = "AlteraERemoveAsFuncoesDoProjeto"
    effect = "Allow"

    actions = [
      "iam:DeleteRole",
      "iam:PutRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:UpdateRole",
      "iam:UpdateRoleDescription",
      "iam:UpdateAssumeRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
    ]

    resources = [local.arn_role]
  }

  # O verbo que desfaria todo o controle acima. Negado explicitamente para que
  # nem uma alteracao futura nas listas de Allow o reintroduza por descuido.
  statement {
    sid       = "NuncaRemoveAFronteira"
    effect    = "Deny"
    actions   = ["iam:DeleteRolePermissionsBoundary"]
    resources = ["*"]
  }

  # Entregar a funcao de acesso ao servico que vai usa-la. Restrito aos servicos
  # do projeto, de modo que nao sirva para entregar uma funcao a um recurso
  # qualquer criado por fora.
  statement {
    sid       = "EntregaFuncaoAosServicosDoProjeto"
    effect    = "Allow"
    actions   = ["iam:PassRole"]
    resources = [local.arn_role]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["lambda.amazonaws.com", "states.amazonaws.com"]
    }
  }

  # --------------------------------------------------------- state do backend

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

  # ------------------------------------------------- o que o pipeline nao toca
  #
  # Negacao explicita sobre os proprios recursos que dao poder ao pipeline. Sem
  # ela, o prefixo do projeto incluiria as duas funcoes de acesso e a fronteira,
  # e uma alteracao no Terraform poderia ampliar o proprio alcance.
  statement {
    sid    = "NaoMexeNoQueLheDaPoder"
    effect = "Deny"

    actions = [
      "iam:*",
      "s3:PutBucketPolicy",
      "s3:DeleteBucketPolicy",
      "s3:DeleteBucket",
      "s3:PutBucketVersioning",
    ]

    resources = [
      aws_iam_role.plan.arn,
      aws_iam_role.deploy.arn,
      aws_iam_policy.boundary.arn,
      aws_iam_openid_connect_provider.github.arn,
      aws_s3_bucket.state.arn,
      "${aws_s3_bucket.state.arn}/*",
    ]
  }
}

resource "aws_iam_role_policy" "deploy" {
  name   = "${local.deploy_role_name}-policy"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}
