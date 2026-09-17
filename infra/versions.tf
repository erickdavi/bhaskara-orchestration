# Versoes fixadas de proposito.
#
# O .terraform.lock.hcl que acompanha este diretorio E versionado: e ele que
# faz um clone limpo resolver exatamente os mesmos providers, em vez do que
# estiver publicado no dia. Sem lock, "funciona na minha maquina" vira
# "funcionava semana passada".

terraform {
  required_version = ">= 1.10"

  # O state vive no S3 desde o Checkpoint 5.
  #
  # Ate o Checkpoint 4 ele era um arquivo local, o que funcionou enquanto todo
  # apply partiu da mesma maquina. Um runner do GitHub sem acesso ao state nao
  # sabe que os 71 recursos existem e tenta cria-los de novo, entao o backend
  # remoto e a condicao para existir deploy automatizado.
  #
  # use_lockfile guarda a trava de concorrencia no proprio bucket, dispensando a
  # tabela DynamoDB que as versoes antigas do Terraform exigiam.
  backend "s3" {
    bucket       = "bhaskara-orchestration-tfstate-405449670138"
    key          = "orchestration/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }

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
