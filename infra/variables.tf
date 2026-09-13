variable "aws_region" {
  description = "Regiao AWS. us-east-1 e a regiao de referencia de preco e a mais barata para Lambda, SQS e Step Functions."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Nome base do projeto, usado como prefixo em todos os recursos."
  type        = string
  default     = "bhaskara-orchestration"
}

variable "environment" {
  description = "Ambiente logico (dev, stg, prd). Compoe o nome dos recursos."
  type        = string
  default     = "dev"
}

variable "lambda_runtime" {
  description = "Runtime Python das funcoes."
  type        = string
  default     = "python3.13"
}

variable "lambda_architecture" {
  description = "Arquitetura das funcoes. arm64 (Graviton) e ~20% mais barato por GB-segundo e seguro aqui porque o codigo usa apenas a stdlib."
  type        = string
  default     = "arm64"
}

variable "task_memory_size" {
  description = "Memoria das funcoes que compoem o fluxo (validate, delta, root, persist). 128 MB e o minimo e sobra: a conta e aritmetica de ponto flutuante."
  type        = number
  default     = 128
}

variable "task_timeout" {
  description = "Timeout das funcoes do fluxo, em segundos."
  type        = number
  default     = 10
}

variable "submit_memory_size" {
  description = "Memoria do submit. 256 e nao 128: a funcao gera milhares de payloads e faz centenas de chamadas de rede, e na Lambda a CPU e proporcional a memoria — mais memoria termina antes e pode custar o mesmo ou menos."
  type        = number
  default     = 256
}

variable "submit_timeout" {
  description = "Timeout do submit. 30 s e o teto util: o HTTP API corta a integracao em 30 s de qualquer forma."
  type        = number
  default     = 30
}

variable "submit_max_quantity" {
  description = <<-EOT
  Teto de equacoes por requisicao.

  Menor que o do Checkpoint 2 (5.000) de proposito: la cada mensagem era uma
  invocacao barata de Lambda; aqui cada mensagem vira uma execucao de state
  machine, e a transicao de estado e a unidade de custo.
  EOT
  type        = number
  default     = 2000
}

variable "dispatcher_batch_size" {
  description = <<-EOT
  Quantas mensagens o event source mapping entrega por invocacao do dispatcher.

  3, e nao os 10 do maximo: cada mensagem do lote vira uma execucao, e cada
  execucao invoca ate cinco funcoes. Com lote de 10 e duas invocacoes
  simultaneas do dispatcher, ate 20 execucoes comecam ao mesmo tempo e pedem
  muito mais que as 10 execucoes concorrentes que a conta tem. O primeiro
  deploy mostrou o resultado: equacoes validas na dead-letter por
  Lambda.TooManyRequestsException.
  EOT
  type        = number
  default     = 3
}

variable "dispatcher_max_concurrency" {
  description = <<-EOT
  Teto de invocacoes simultaneas do dispatcher.

  E aqui que se controla a vazao do sistema. A conta tem 10 execucoes
  concorrentes no total, compartilhadas com os outros checkpoints, e a AWS nao
  permite reservar concorrencia quando o limite e 10 (ela exige deixar 10 nao
  reservadas). O minimo aceito pelo event source mapping e 2.
  EOT
  type        = number
  default     = 2
}

variable "status_memory_size" {
  description = "Memoria do status. 256 porque a funcao agrega varias respostas de API numa so resposta."
  type        = number
  default     = 256
}

variable "status_timeout" {
  description = "Timeout do status, em segundos."
  type        = number
  default     = 15
}

variable "queue_visibility_timeout" {
  description = "Tempo que a mensagem fica invisivel apos ser entregue. A AWS recomenda no minimo 6x o timeout da funcao consumidora — aqui, 6 x 15 s do dispatcher."
  type        = number
  default     = 90
}

variable "queue_message_retention" {
  description = "Retencao na fila orders, em segundos (4 dias)."
  type        = number
  default     = 345600
}

variable "dead_letter_retention" {
  description = "Retencao na fila dead-letter, em segundos (14 dias, o maximo)."
  type        = number
  default     = 1209600
}

variable "max_receive_count" {
  description = <<-EOT
  Entregas antes de a SQS mover a mensagem para a dead-letter.

  Esta e a rede de seguranca do proprio dispatcher: se ele falhar antes de
  iniciar a execucao, ninguem mais registra a falha. As falhas de dentro do
  fluxo tem outro caminho — o Catch da state machine, que publica na mesma fila
  com o motivo anexado.
  EOT
  type        = number
  default     = 3
}

variable "queue_receive_wait_time" {
  description = "Long polling em segundos. 20 e o maximo: reduz receives vazios, o que corta requests da SQS sem atrasar a entrega."
  type        = number
  default     = 20
}

variable "results_ttl_hours" {
  description = "Por quanto tempo um resultado fica na tabela. Dado de laboratorio nao fica: o TTL apaga sozinho, sem job de limpeza e sem custo de escrita."
  type        = number
  default     = 24
}

variable "log_retention_days" {
  description = "Retencao dos logs em dias. Valores aceitos pelo CloudWatch: 1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653 ou 0 para reter indefinidamente."
  type        = number
  default     = 7
}

variable "api_throttling_rate_limit" {
  description = "Requisicoes por segundo aceitas pelo stage do HTTP API."
  type        = number
  default     = 5
}

variable "api_throttling_burst_limit" {
  description = "Rajada aceita pelo stage do HTTP API."
  type        = number
  default     = 10
}

variable "xray_enabled" {
  description = "Traco distribuido no X-Ray, nas funcoes e na state machine. A camada gratuita cobre 100.000 traces registrados por mes; uma carga de 100 equacoes gera ~100."
  type        = bool
  default     = true
}

variable "alert_email" {
  description = "E-mail inscrito no topico de alertas. Vazio por padrao: a inscricao exige confirmacao manual por link, que o Terraform nao consegue completar, e o recurso ficaria pendente para sempre no state de quem so quisesse ver o alarme mudar de cor no console."
  type        = string
  default     = ""
}

variable "failed_executions_threshold" {
  description = "Execucoes falhas em 5 minutos que disparam o alarme. Acima de zero de proposito: o modo caos faz o sistema falhar por projeto, e um alarme que dispara na demonstracao ensina a ignora-lo."
  type        = number
  default     = 5
}

variable "throttle_threshold" {
  description = "Throttles somados em 5 minutos que disparam o alarme. A conta tem 10 execucoes concorrentes no total; alguma contencao e esperada em carga."
  type        = number
  default     = 20
}

variable "queue_age_threshold_seconds" {
  description = "Idade da mensagem mais antiga na orders que dispara o alarme. Cinco minutos: acima disso a fila nao esta drenando, esta parada."
  type        = number
  default     = 300
}

variable "tags" {
  description = "Tags adicionais aplicadas a todos os recursos."
  type        = map(string)
  default     = {}
}
