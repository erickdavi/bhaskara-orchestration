variable "aws_region" {
  description = "Regiao AWS. A mesma da stack principal."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Nome base do projeto, usado como prefixo em todos os recursos."
  type        = string
  default     = "bhaskara-orchestration"
}

variable "github_repository" {
  description = "Repositorio autorizado a assumir as funcoes de acesso, no formato dono/nome. Qualquer outro repositorio recebe AccessDenied da propria AWS, mesmo com um token valido do GitHub."
  type        = string
  default     = "erickdavi/bhaskara-orchestration"
}

variable "deploy_branch" {
  description = "Unica referencia autorizada a assumir a funcao de deploy. Um push em qualquer outra branch roda os testes e o plano, e nao consegue aplicar."
  type        = string
  default     = "main"
}

variable "state_version_retention_days" {
  description = "Por quantos dias as versoes antigas do state ficam guardadas. O versionamento do bucket e a rede de seguranca contra um apply que corrompa o arquivo; 90 dias cobrem qualquer recuperacao plausivel sem acumular custo."
  type        = number
  default     = 90
}
