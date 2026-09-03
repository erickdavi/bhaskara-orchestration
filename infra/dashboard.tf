# O painel: bucket S3 privado servido por CloudFront com Origin Access Control.
#
# O bucket nao e publico e nao tem website hosting. So a distribuicao consegue
# ler os objetos, e a policy do bucket restringe isso ao ARN desta distribuicao
# especifica — nao "qualquer CloudFront", que e o erro classico dessa
# configuracao.
#
# O que NAO esta aqui e tao importante quanto o que esta: a chave de API. O
# config.js gerado abaixo leva apenas a URL da API. A pagina e publica na
# internet; uma chave embutida nela seria uma chave publicada.

resource "aws_s3_bucket" "dashboard" {
  bucket = "${local.name_prefix}-dashboard-${data.aws_caller_identity.current.account_id}"

  # O destroy precisa funcionar com objetos dentro. Sao arquivos estaticos
  # regenerados pelo apply; nao ha o que preservar.
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "dashboard" {
  bucket = aws_s3_bucket.dashboard.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "dashboard" {
  bucket = aws_s3_bucket.dashboard.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_ownership_controls" "dashboard" {
  bucket = aws_s3_bucket.dashboard.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

locals {
  # Os arquivos do painel e o content-type de cada um. Sem o content-type
  # correto, o browser recebe o CSS como application/octet-stream e ignora.
  dashboard_files = {
    "index.html" = "text/html; charset=utf-8"
    "styles.css" = "text/css; charset=utf-8"
    "app.js"     = "application/javascript; charset=utf-8"
    "flow.js"    = "application/javascript; charset=utf-8"
  }
}

resource "aws_s3_object" "dashboard" {
  for_each = local.dashboard_files

  bucket       = aws_s3_bucket.dashboard.id
  key          = each.key
  source       = "${path.module}/../web/${each.key}"
  content_type = each.value

  # etag pelo conteudo: um `apply` depois de editar o painel republica o
  # arquivo. Sem isso o Terraform nao perceberia a mudanca.
  etag = filemd5("${path.module}/../web/${each.key}")
}

resource "aws_s3_object" "config" {
  bucket       = aws_s3_bucket.dashboard.id
  key          = "config.js"
  content_type = "application/javascript; charset=utf-8"

  # Apenas a URL da API. A chave e digitada pelo operador no painel e fica so
  # no localStorage do browser dele.
  content = "window.BHASKARA_CONFIG = ${jsonencode({ apiBase = aws_apigatewayv2_api.this.api_endpoint })};\n"
}

resource "aws_cloudfront_origin_access_control" "dashboard" {
  name                              = "${local.name_prefix}-oac"
  description                       = "Acesso do CloudFront ao bucket privado do painel"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# Politicas gerenciadas pela AWS, por nome em vez de ID: o ID e um UUID que nao
# diz nada em uma revisao de codigo.
data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_response_headers_policy" "security" {
  name = "Managed-SecurityHeadersPolicy"
}

resource "aws_cloudfront_distribution" "dashboard" {
  enabled             = true
  default_root_object = "index.html"
  comment             = "${local.name_prefix} — painel do fluxo"

  # PriceClass_100: America do Norte e Europa. Nao ha por que pagar por pontos
  # de presenca na Asia para um painel de laboratorio.
  price_class = "PriceClass_100"

  origin {
    domain_name              = aws_s3_bucket.dashboard.bucket_regional_domain_name
    origin_id                = "dashboard"
    origin_access_control_id = aws_cloudfront_origin_access_control.dashboard.id
  }

  default_cache_behavior {
    target_origin_id       = "dashboard"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]

    # Cache desligado de proposito. O painel muda a cada apply, e um cache de
    # 24 h faria "editei e nao mudou nada" virar a experiencia padrao. Sao
    # quatro arquivos pequenos servidos algumas vezes por demonstracao.
    cache_policy_id            = data.aws_cloudfront_cache_policy.disabled.id
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.security.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

data "aws_iam_policy_document" "dashboard" {
  statement {
    sid       = "LeituraApenasPorEstaDistribuicao"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.dashboard.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    # Sem esta condicao, qualquer distribuicao CloudFront de qualquer conta
    # poderia ler o bucket.
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.dashboard.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "dashboard" {
  bucket = aws_s3_bucket.dashboard.id
  policy = data.aws_iam_policy_document.dashboard.json

  depends_on = [aws_s3_bucket_public_access_block.dashboard]
}
