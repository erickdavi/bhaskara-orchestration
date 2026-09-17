# A camada que existe antes do pipeline.
#
# Tudo aqui e criado uma vez so, a partir da maquina de quem mantem o projeto,
# porque sao justamente os recursos de que o pipeline precisa para conseguir
# rodar: o bucket onde o state vive, o provedor de identidade que o GitHub usa
# para se autenticar e as duas funcoes de acesso que ele assume.
#
# O state desta camada comeca local e e movido para dentro do proprio bucket
# depois do primeiro apply. O procedimento esta no README deste diretorio.

terraform {
  required_version = ">= 1.10"

  # O state desta camada mora dentro do bucket que ela mesma cria. Na primeira
  # vez este bloco nao existia, o apply rodou com state local e em seguida o
  # `terraform init -migrate-state` moveu o arquivo para ca. O procedimento
  # completo, para quem for reconstruir a conta do zero, esta no README deste
  # diretorio.
  backend "s3" {
    bucket       = "bhaskara-orchestration-tfstate-405449670138"
    key          = "bootstrap/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project    = var.project_name
      ManagedBy  = "terraform"
      Layer      = "bootstrap"
      Checkpoint = "5"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
