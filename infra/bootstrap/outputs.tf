output "state_bucket" {
  description = "Bucket onde o state da stack principal passa a viver."
  value       = aws_s3_bucket.state.id
}

output "plan_role_arn" {
  description = "Funcao assumida pelo workflow para calcular o plano. Vai no campo role-to-assume do job de plano."
  value       = aws_iam_role.plan.arn
}

output "deploy_role_arn" {
  description = "Funcao assumida pelo workflow para aplicar. Vai no campo role-to-assume do job de deploy."
  value       = aws_iam_role.deploy.arn
}

output "permissions_boundary_arn" {
  description = "Fronteira que toda funcao criada pelo pipeline precisa carregar. Vai na variavel permissions_boundary_arn da stack principal."
  value       = aws_iam_policy.boundary.arn
}

output "backend_config" {
  description = "O bloco de backend que a stack principal passa a usar."
  value       = <<-CONF
    terraform {
      backend "s3" {
        bucket       = "${aws_s3_bucket.state.id}"
        key          = "orchestration/terraform.tfstate"
        region       = "${var.aws_region}"
        encrypt      = true
        use_lockfile = true
      }
    }
  CONF
}
