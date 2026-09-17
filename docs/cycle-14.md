# Ciclo 14 — a camada que precisa existir antes do pipeline

Primeiro ciclo do Checkpoint 5. Nenhuma linha de Python mudou, e nenhum workflow
foi escrito ainda. O ciclo inteiro trata de uma coisa: o pipeline não tem como
existir enquanto o state do Terraform for um arquivo na máquina de quem aplica.

## O problema

Nos três checkpoints anteriores o `terraform apply` sempre partiu do mesmo
computador, e o `terraform.tfstate` local funcionou. Um runner do GitHub começa
sem esse arquivo, e sem ele o Terraform não sabe que os 71 recursos existem: ele
tenta criar tudo de novo e encontra 71 nomes já ocupados.

Então o Checkpoint 5 começa movendo o state para um bucket no S3, com trava de
concorrência. A trava vai no próprio bucket, um recurso do Terraform 1.10 em
diante, o que dispensa a tabela DynamoDB que as versões antigas exigiam.

## As duas camadas

Existe uma dependência circular óbvia: o pipeline precisa de credenciais e do
bucket de state para rodar, e esses recursos não podem ser criados por ele. A
saída é separar.

A camada nova, em `infra/bootstrap/`, é aplicada uma vez só a partir da máquina
do autor. Ela cria o bucket de state, o provedor de identidade do GitHub, duas
funções de acesso e uma política de fronteira. Depois do primeiro `apply`, ela
move o próprio state para dentro do bucket que acabou de criar.

A stack que já existia ganhou apenas a declaração do backend e a fronteira nas
oito funções de acesso. Nada mais mudou nela.

## Autenticação sem chave guardada

O caminho comum seria criar uma chave de acesso da AWS e guardá-la nos segredos
do repositório. Uma chave dessas não expira, vale de qualquer origem, e continua
valendo depois de vazar até alguém perceber.

O que foi feito é federação por OIDC: o GitHub emite um token de identidade a
cada execução, a AWS confia nesse emissor e devolve credenciais temporárias de
uma hora. Não existe segredo da AWS no repositório nem na configuração do
GitHub.

A confiança é restrita por duas condições. O público do token precisa ser o
serviço de credenciais da AWS, o que impede reaproveitar um token emitido para
outra finalidade. E o assunto do token precisa casar com o repositório e a
referência de origem.

São duas funções, com condições diferentes:

| Função | Assunto aceito | Pode |
| --- | --- | --- |
| `…-ci-plan` | pull request ou qualquer branch | ler o estado e calcular o plano |
| `…-ci-deploy` | exatamente `refs/heads/main` | criar, alterar e remover recursos |

A condição do deploy usa igualdade exata, e não padrão com curinga. Um curinga
em `refs/heads/*` daria poder de aplicar a qualquer branch que alguém
conseguisse criar, o que anularia a separação entre revisar e aplicar.

## A fronteira de permissão

O deploy precisa criar funções de acesso, porque a stack tem oito delas, uma por
Lambda. Isso abre um caminho de escalada que vale enunciar com clareza: quem
consegue alterar o Terraform faz o pipeline criar uma função com permissão de
administrador e depois a assume.

O controle é uma política de fronteira, que funciona como teto. Ela não concede
nada; ela limita o que qualquer política anexada consegue conceder. A política
do deploy exige que toda função criada a carregue, e não inclui o verbo que a
removeria. O conteúdo da fronteira é a união exata do que as oito funções usam
hoje, mais uma negação explícita de qualquer ação sobre identidade.

As oito funções existentes passaram a carregá-la. Depois de aplicar, uma carga
de dez equações confirmou que nada quebrou: as dez foram gravadas e não houve
um único `AccessDenied` em nenhuma das cinco funções do fluxo.

## Testando permissão sem poder assumir a função

A função de deploy só aceita ser assumida pelo GitHub, o que é o comportamento
certo e impede testá-la da máquina. O simulador da IAM resolve isso, respondendo
o que aconteceria sem executar nada:

```text
lambda:UpdateFunctionCode   function:…-dev-delta          allowed
iam:PutRolePolicy           role/…-dev-delta-role         allowed
s3:PutObject                …-dashboard/app.js            allowed

iam:PutRolePolicy           role/…-ci-deploy              explicitDeny
iam:DeleteRolePermissionsBoundary  role/…-dev-delta-role  explicitDeny
s3:DeleteBucket             …-tfstate                     explicitDeny
lambda:UpdateFunctionCode   function:outro-projeto        implicitDeny
```

As três negações explícitas são a parte que importa: o pipeline não altera as
funções que lhe dão poder, não remove a fronteira e não apaga o bucket do state.
A quarta linha mostra o efeito do prefixo, que limita tudo ao projeto.

## O que a varredura encontrou

O `trivy` reprovou a política de deploy por usar `s3:*`. A ação estava restrita
ao prefixo do projeto, o que parecia suficiente, exceto que o bucket do state
começa com o mesmo prefixo. As ações destrutivas ali já estavam negadas
explicitamente, mas apagar uma versão antiga do state não estava.

A correção mira o padrão de nome do bucket do painel, que tem o número da conta
no fim, e enumera os doze verbos que o Terraform precisa em vez de usar curinga.
O simulador confirma o resultado: `s3:DeleteObjectVersion` no bucket do state
passou a ser negado.

Apareceu também um achado que já existia desde o ciclo 11 e ninguém tinha visto:
o tópico SNS dos alarmes usa chave gerenciada pela AWS e não uma chave própria.
Ficou registrado no `.trivyignore` com o motivo, junto dos outros do mesmo tipo.
O que mudou não foi a configuração, e sim a forma de ler o relatório, por
severidade em vez de pelo resumo.

Cinco outras reprovações do `checkov`, todas sobre curinga em política de IAM,
receberam anotação no próprio bloco em vez de entrarem na lista global de
exceções. Cada uma tem o motivo ao lado: leitura que a API não deixa restringir,
identificador sorteado pela AWS, API de conta sem ARN, e a fronteira, que é um
teto e perderia o sentido se fosse restrita.

## A migração

O momento de maior risco do checkpoint, feito com rede: cópia do arquivo antes,
migração, e conferência de que o resultado é o mesmo.

```text
recursos no state antes:   71
recursos no state depois:  71
terraform plan:            No changes
```

## Critério de pronto

- 333 testes verdes, nenhum tocando na AWS;
- `checkov` com 257 aprovações, 0 reprovações e 5 exceções anotadas no código;
- `trivy` sem achados de severidade média ou superior;
- `terraform plan` dizendo "nenhuma mudança" nas duas camadas;
- dez equações processadas de ponta a ponta depois da fronteira entrar.
