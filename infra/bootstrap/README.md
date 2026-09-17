# Camada de bootstrap

O que existe aqui é aplicado **uma vez só**, da máquina de quem mantém o
projeto, e não pelo pipeline. São os recursos de que o pipeline precisa para
conseguir rodar, e que portanto não podem depender dele: o bucket onde o state
vive, o provedor de identidade que o GitHub usa para se autenticar e as duas
funções de acesso que ele assume.

## O que é criado

O bucket de state, com versionamento, criptografia, bloqueio de acesso público e
uma política que recusa tráfego sem TLS. O provedor OIDC do GitHub. A função
`…-ci-plan`, que só lê. A função `…-ci-deploy`, que aplica. E a política de
fronteira que limita o que qualquer função criada pelo pipeline pode fazer.

## Como aplicar numa conta do zero

A primeira execução acontece sem backend remoto, porque o bucket que vai
guardá-lo ainda não existe. Comente o bloco `backend` em `versions.tf`, aplique,
descomente e migre:

```bash
cd infra/bootstrap
terraform init
terraform apply
# descomente o bloco backend em versions.tf, com o nome do bucket que saiu acima
terraform init -migrate-state
```

A partir daí o state desta camada vive no mesmo bucket da stack principal, sob a
chave `bootstrap/terraform.tfstate`.

## Depois

A stack principal em `infra/` lê o ARN da fronteira do state desta camada, então
ela precisa existir antes. Os três valores que o workflow usa saem daqui:

```bash
terraform output -raw plan_role_arn
terraform output -raw deploy_role_arn
terraform output -raw state_bucket
```

## Por que a separação existe

Sem ela haveria uma dependência circular. O pipeline precisa das funções de
acesso para autenticar, e das funções de acesso é que ele criaria as funções de
acesso. Separar em duas camadas resolve isso e traz um ganho de segurança: os
recursos que dão poder ao pipeline ficam fora do alcance dele. A política de
deploy nega explicitamente qualquer alteração nas próprias funções, na fronteira
e no bucket de state, de modo que uma mudança no Terraform não consegue ampliar
o próprio alcance.

## Verificando as permissões sem aplicar nada

As funções só podem ser assumidas pelo GitHub, o que impede testá-las a partir
da máquina. O simulador da IAM responde sem executar nada:

```bash
ROLE=$(terraform output -raw deploy_role_arn)
aws iam simulate-principal-policy --policy-source-arn "$ROLE" \
  --action-names iam:PutRolePolicy \
  --resource-arns "arn:aws:iam::<conta>:role/bhaskara-orchestration-ci-deploy"
```

O resultado esperado para esse caso é `explicitDeny`: o pipeline não altera a si
mesmo.
