provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.tags
  }
}

data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

locals {
  name_prefix = "${var.project_name}-${var.environment}"

  orders_queue_name      = "${local.name_prefix}-orders"
  dead_letter_queue_name = "${local.name_prefix}-dead-letter"

  # ARN da orders montado a mao para quebrar o ciclo de dependencia entre ela e
  # a dead-letter: a orders referencia a dead-letter no redrive_policy, e a
  # dead-letter precisa referenciar a orders no redrive_allow_policy.
  orders_queue_arn = "arn:${data.aws_partition.current.partition}:sqs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:${local.orders_queue_name}"

  results_table_name = "${local.name_prefix}-results"

  state_machine_name = "${local.name_prefix}-flow"

  # ARN montado a mao, e nao lido do recurso, para quebrar um ciclo: a state
  # machine precisa dos ARNs das funcoes (para invoca-las) e o dispatcher
  # precisa do ARN dela (para inicia-la). O nome e deterministico, entao o ARN
  # tambem e.
  state_machine_arn = "arn:${data.aws_partition.current.partition}:states:${var.aws_region}:${data.aws_caller_identity.current.account_id}:stateMachine:${local.name_prefix}-flow"

  # Log group de state machine precisa comecar com /aws/vendedlogs/states/ —
  # exigencia do proprio Step Functions para entrega de logs.
  state_machine_log_group = "/aws/vendedlogs/states/${local.state_machine_name}"

  src_dir      = "${path.module}/../src"
  workflow_dir = "${path.module}/../workflow"

  # As sete funcoes, com o conteudo exato de cada zip.
  #
  # Os modulos vao para a RAIZ do zip, lado a lado, porque e assim que a Lambda
  # resolve imports: o handler faz "from calculator import calculate", sem
  # prefixo de pacote. conftest.py e local/paths.py reproduzem esse mesmo
  # sys.path fora da nuvem.
  #
  # Cada funcao leva so o que importa. Nao e economia de bytes: e superficie —
  # o zip do validate nao tem por que conter a regra de negocio inteira.
  functions = {
    submit = {
      description = "Recebe POST /orders e publica N equacoes na fila orders"
      files       = ["handlers/submit/handler.py", "handlers/submit/generator.py", "shared/api_auth.py"]
      memory      = var.submit_memory_size
      timeout     = var.submit_timeout
      environment = {
        ORDERS_QUEUE_URL = aws_sqs_queue.orders.url
        API_KEY          = random_password.api_key.result
        MAX_QUANTITY     = tostring(var.submit_max_quantity)
      }
    }

    dispatcher = {
      description = "Consome a fila orders e inicia uma execucao por mensagem"
      files       = ["handlers/dispatcher/handler.py", "shared/idempotency.py"]
      memory      = var.submit_memory_size
      timeout     = var.submit_timeout
      environment = {
        STATE_MACHINE_ARN = local.state_machine_arn
      }
    }

    validate = {
      description = "Estado Validate: normaliza e valida os coeficientes"
      files       = ["handlers/validate/handler.py", "shared/chaos.py"]
      memory      = var.task_memory_size
      timeout     = var.task_timeout
      environment = {}
    }

    delta = {
      description = "Estado Delta: calcula b^2 - 4ac e classifica o sinal"
      files       = ["handlers/delta/handler.py", "shared/chaos.py", "shared/quadratic.py", "shared/calculator.py"]
      memory      = var.task_memory_size
      timeout     = var.task_timeout
      environment = {}
    }

    root = {
      description = "Estados RootX1, RootX2 e RootDouble: calcula uma raiz"
      files       = ["handlers/root/handler.py", "shared/chaos.py", "shared/quadratic.py", "shared/calculator.py"]
      memory      = var.task_memory_size
      timeout     = var.task_timeout
      environment = {}
    }

    persist = {
      description = "Estado Persist: grava o resultado de forma idempotente"
      files       = ["handlers/persist/handler.py", "shared/chaos.py"]
      memory      = var.task_memory_size
      timeout     = var.task_timeout
      environment = {
        RESULTS_TABLE     = aws_dynamodb_table.results.name
        RESULTS_TTL_HOURS = tostring(var.results_ttl_hours)
      }
    }

  }

  # As cinco funcoes que a state machine invoca. O dispatcher e o submit ficam
  # de fora de proposito: a role da state machine nao tem por que poder chamar
  # quem a aciona.
  flow_functions = ["validate", "delta", "root", "persist"]

  tags = merge(
    {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
      Checkpoint  = "3"
    },
    var.tags,
  )
}

# Chave de API de 40 caracteres, gerada pelo Terraform. Vive no state (que nao
# e versionado) e sai por `terraform output -raw api_key`. Nunca no repositorio,
# nunca no bundle do painel.
resource "random_password" "api_key" {
  length  = 40
  special = false
}
