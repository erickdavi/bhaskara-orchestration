# O bucket onde o state da stack principal passa a viver.
#
# Ate o Checkpoint 4 o state era um arquivo na maquina de quem aplicava. Isso
# funcionou enquanto o apply sempre partiu do mesmo lugar, e impede o deploy
# automatizado: um runner do GitHub sem acesso ao state nao sabe que os 71
# recursos existem e tenta cria-los de novo.

locals {
  state_bucket = "${var.project_name}-tfstate-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket" "state" {
  bucket = local.state_bucket

  # Sem force_destroy de proposito. O bucket do state e a unica coisa neste
  # projeto que nao pode ser recriada: perde-lo significa o Terraform esquecer
  # o que existe na conta. Um `terraform destroy` daqui falha enquanto houver
  # objeto dentro, e essa falha e o comportamento desejado.
  force_destroy = false
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    # A rede de seguranca contra um apply interrompido no meio da escrita.
    # Com versionamento, o state anterior continua recuperavel.
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      # Chave gerenciada pela AWS, como no resto do projeto. Uma chave propria
      # custaria US$ 1/mes para proteger um arquivo que ja nao sai do bucket.
      sse_algorithm = "AES256"
    }

    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket = aws_s3_bucket.state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    id     = "expira-versoes-antigas-do-state"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.state_version_retention_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.state]
}

# Nega qualquer acesso que nao venha por TLS. O Terraform sempre fala HTTPS com
# o S3, entao a politica nao muda nada no uso normal; ela existe para que uma
# ferramenta mal configurada no futuro falhe em vez de trafegar o state, que
# contem a chave de API do projeto, em texto claro.
data "aws_iam_policy_document" "state" {
  statement {
    sid    = "NegaTrafegoSemTLS"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions   = ["s3:*"]
    resources = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = data.aws_iam_policy_document.state.json

  depends_on = [aws_s3_bucket_public_access_block.state]
}
