# Federacao de identidade com o GitHub.
#
# A alternativa comum seria gerar uma chave de acesso da AWS e guarda-la nos
# segredos do repositorio. Uma chave dessas nao expira, vale de qualquer origem
# e continua valendo depois de vazar, ate alguem perceber e revogar.
#
# Aqui o GitHub emite um token de identidade a cada execucao do workflow, a AWS
# confia nesse emissor e devolve credenciais temporarias de uma hora. Nao existe
# segredo da AWS no repositorio nem na configuracao do GitHub.

resource "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"

  # O unico publico aceito. Sem esta restricao, um token emitido para outro
  # servico da AWS seria aceito aqui.
  client_id_list = ["sts.amazonaws.com"]

  # A AWS valida a cadeia de certificados do emissor por conta propria desde
  # 2023, e mantem o campo por compatibilidade. As duas impressoes abaixo sao
  # as publicadas pelo GitHub; deixa-las corretas custa nada e evita depender
  # de um comportamento nao documentado.
  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd",
  ]
}

locals {
  oidc_provider = "token.actions.githubusercontent.com"

  # O assunto do token no formato imutavel.
  #
  # Ate 15/07/2026 o GitHub emitia `repo:<dono>/<nome>:<contexto>`. Repositorios
  # criados depois dessa data usam `repo:<dono>@<id>/<nome>@<id>:<contexto>`, com
  # o identificador numerico ao lado de cada nome. Este repositorio nasceu em
  # 03/09/2026 e ja emite o formato novo — uma condicao escrita com os nomes
  # antigos nao casa com nada, e a AWS recusa com AccessDenied.
  #
  # A mudanca nao e burocracia: nome de dono e de repositorio podem ser trocados,
  # e quem adquirisse o nome antigo herdaria a confianca escrita aqui. O
  # identificador numerico nao se transfere.
  #
  # O prefixo pode ser conferido a qualquer momento:
  #   gh api repos/<dono>/<nome>/actions/oidc/customization/sub --jq .sub_claim_prefix
  owner = split("/", var.github_repository)[0]
  nome  = split("/", var.github_repository)[1]

  subject = "repo:${local.owner}@${var.github_owner_id}/${local.nome}@${var.github_repository_id}"

  plan_role_name   = "${var.project_name}-ci-plan"
  deploy_role_name = "${var.project_name}-ci-deploy"
  boundary_name    = "${var.project_name}-ci-boundary"

  boundary_arn = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:policy/${local.boundary_name}"
}

# ------------------------------------------------------------------ confianca

# Leitura: qualquer pull request e qualquer push, inclusive em branch de
# trabalho. E o que permite ver o plano antes de aprovar uma mudanca.
data "aws_iam_policy_document" "plan_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_provider}:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "${local.oidc_provider}:sub"
      values = [
        "${local.subject}:pull_request",
        "${local.subject}:ref:refs/heads/*",
      ]
    }
  }
}

# Escrita: somente push na branch de deploy, e somente deste repositorio.
#
# A condicao usa igualdade exata, e nao padrao com curinga. Um curinga em
# `refs/heads/*` aqui daria poder de aplicar a qualquer branch que alguem
# conseguisse criar, o que anularia a separacao entre revisar e aplicar.
data "aws_iam_policy_document" "deploy_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_provider}:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_provider}:sub"
      values   = ["${local.subject}:ref:refs/heads/${var.deploy_branch}"]
    }
  }
}

resource "aws_iam_role" "plan" {
  name               = local.plan_role_name
  description        = "Assumida pelo GitHub Actions para calcular o plano. Nao altera nada."
  assume_role_policy = data.aws_iam_policy_document.plan_assume.json

  # Uma hora e o teto do que o workflow precisa, e o minimo que a AWS aceita
  # para credencial federada.
  max_session_duration = 3600
}

resource "aws_iam_role" "deploy" {
  name               = local.deploy_role_name
  description        = "Assumida pelo GitHub Actions em push na branch principal para aplicar a infraestrutura."
  assume_role_policy = data.aws_iam_policy_document.deploy_assume.json

  max_session_duration = 3600
}
