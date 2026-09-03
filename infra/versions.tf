# Versoes fixadas de proposito.
#
# O .terraform.lock.hcl que acompanha este diretorio E versionado: e ele que
# faz um clone limpo resolver exatamente os mesmos providers, em vez do que
# estiver publicado no dia. Sem lock, "funciona na minha maquina" vira
# "funcionava semana passada".

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }

    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }

    random = {
      source  = "hashicorp/random"
      version = "~> 3.5"
    }
  }
}
