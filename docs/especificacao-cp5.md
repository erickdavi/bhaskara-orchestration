# Especificação — Checkpoint 5: pipeline de CI/CD

**Projeto:** `bhaskara-orchestration`, o mesmo repositório dos Checkpoints 3 e 4
**Status:** proposta, escrita em 17/09/2026

## O que o enunciado pede

Configurar um pipeline que integre e implante as funções de forma automatizada,
versionar os arquivos de configuração desse pipeline, comprovar uma execução de
deploy bem-sucedida pelos logs da ferramenta e descrever as etapas configuradas.
As credenciais devem ficar nos mecanismos de segredo da própria ferramenta, sem
chave, token ou arquivo sensível no repositório.

## O problema que vem antes do pipeline

O `terraform.tfstate` deste projeto é um arquivo local, ignorado pelo Git. Isso
funcionou nos três checkpoints anteriores, em que o `apply` sempre partiu da
mesma máquina, e deixa de funcionar no momento em que um runner do GitHub tenta
implantar: sem acesso ao state, o Terraform não sabe o que já existe e tenta
criar tudo de novo, encontrando 71 recursos com nome repetido.

Então o Checkpoint 5 começa por mover o state para um bucket S3 com trava de
concorrência. Não é um enfeite de arquitetura, é a condição para que exista
deploy automatizado.

## As decisões

### Autenticação sem chave armazenada

O caminho comum seria gerar uma chave de acesso da AWS e guardá-la nos segredos
do GitHub. Uma chave assim não expira, vale para qualquer origem e, se vazar,
continua valendo até alguém perceber e revogar.

O caminho adotado é federação por OIDC. O GitHub emite um token de identidade
para cada execução do workflow, a AWS confia nesse emissor e entrega credenciais
temporárias de uma hora. Nenhum segredo da AWS existe no repositório ou nas
configurações do GitHub, e a confiança é restrita por condições que amarram o
token ao repositório e à referência de origem.

### Duas funções de acesso, uma por situação

| Função | Assumida quando | Pode |
| --- | --- | --- |
| `…-ci-plan` | a execução vem de um pull request | ler o estado da infraestrutura e calcular o plano |
| `…-ci-deploy` | a execução vem de um push na branch principal | criar, alterar e remover os recursos do projeto |

A separação existe porque um pull request pode vir de qualquer pessoa com
permissão de abrir um. Se houvesse uma função só, revisar uma proposta de
mudança daria a quem a propôs o poder de aplicá-la.

### Limite de permissão nas funções que o pipeline cria

O `deploy` precisa criar funções de acesso, porque a stack tem oito delas, uma
por Lambda. Isso abre um caminho de escalada: quem consegue alterar o Terraform
pode fazer o pipeline criar uma função com permissão de administrador e depois
assumi-la.

O controle contra isso é um limite de permissão. O `deploy` só consegue criar
uma função de acesso se anexar a ela uma política de fronteira específica, e
essa fronteira não concede mais do que os serviços do projeto. A condição fica
na própria política do `deploy`, de modo que a tentativa de criar uma função sem
a fronteira falha na AWS, e não numa revisão de código.

As oito funções existentes passam a carregar a mesma fronteira, o que é uma
alteração de uma linha no Terraform atual.

### Onde cada coisa é criada

Há uma dependência circular óbvia: o pipeline precisa das funções de acesso e do
bucket de state para rodar, e esses recursos não podem ser criados pelo próprio
pipeline. A solução é separar em duas camadas.

A primeira, em `infra/bootstrap/`, é aplicada uma única vez a partir da máquina
do autor, com as credenciais que já existem. Ela cria o bucket de state, o
provedor OIDC, as duas funções de acesso e a política de fronteira. Depois do
primeiro `apply`, ela move o próprio state para dentro do bucket que acabou de
criar.

A segunda é a stack que já existe em `infra/`, que ganha apenas a declaração do
backend remoto. Nada mais muda nela além da fronteira nas oito funções.

## As etapas do pipeline

Um workflow, `.github/workflows/ci-cd.yml`, com quatro estágios encadeados.

**Qualidade.** Lint do Python com `ruff`, formatação do Terraform com
`terraform fmt -check`, e a suíte de 333 testes com `pytest`. Nenhum desses
passos toca a AWS, então rodam em paralelo e sem credencial nenhuma.

**Segurança.** `checkov` sobre o Terraform e `trivy` sobre o repositório,
procurando segredo comprometido e configuração insegura. As duas ferramentas já
eram usadas manualmente no projeto; aqui passam a bloquear a entrega.

**Plano.** `terraform init`, `validate` e `plan`, assumindo a função de leitura.
Em um pull request, o resumo do plano é publicado como comentário, de modo que a
revisão veja o que a mudança faria antes de ela ser aprovada.

**Implantação.** Só acontece em push na branch principal, e só se os três
estágios anteriores passarem. Assume a função de deploy, roda `terraform apply`
e, em seguida, uma verificação funcional: publica uma carga pequena na API real,
espera a fila drenar e confirma que as execuções terminaram com sucesso. Um
deploy que sobe sem erro mas quebra o sistema é um deploy que falhou, e o
estágio precisa perceber isso.

Um controle de concorrência impede que dois deploys rodem ao mesmo tempo, o que
corromperia o state mesmo com a trava do backend segurando a escrita.

## O que fica de fora

Ambientes separados de homologação e produção, que exigiriam duplicar a stack
numa conta que tem dez execuções simultâneas no total. Aprovação manual antes do
deploy, que faz sentido em produção e atrapalharia a demonstração. Verificação
periódica de desvio entre o código e a infraestrutura real, que é útil e não é
o que o enunciado pede.

## Plano de execução

Quatro ciclos, seguindo o método dos anteriores, numerados a partir do 14 para
que o histórico continue contínuo.

| Ciclo | Entrega |
| --- | --- |
| 14 | camada de bootstrap, migração do state para o S3, fronteira de permissão |
| 15 | workflow com os estágios de qualidade, segurança e plano |
| 16 | estágio de implantação e verificação funcional pós-deploy |
| 17 | README com as evidências, texto do Canvas e fechamento |

## Riscos

A migração do state é o momento de maior risco do checkpoint. Um erro ali
significa o Terraform perder de vista 71 recursos que continuam existindo na
AWS. A mitigação é copiar o arquivo antes, conferir com `terraform plan` que o
resultado é "nenhuma mudança" depois da migração, e só então seguir.

O segundo risco é a política do `deploy` ficar restrita demais e o primeiro
deploy automatizado falhar no meio, deixando a stack pela metade. A mitigação é
rodar um `apply` completo a partir da máquina, já autenticado como a função de
deploy, antes de deixar o pipeline fazê-lo sozinho.
