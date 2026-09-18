# Ciclo 15 — os tres estagios que rodam antes de qualquer credencial existir

Segundo ciclo do Checkpoint 5. O ciclo 14 criou a camada que o pipeline precisa
para existir; este escreve o pipeline, menos a parte que aplica. Sao tres
estagios: qualidade, seguranca e plano.

## O linter que o projeto nao tinha

Ate aqui o estilo do codigo era mantido a mao. Isso funciona enquanto uma pessoa
so escreve, e falha em silencio no dia em que um estagio precisa reprovar uma
entrega por conta propria — um estagio de qualidade sem criterio nao reprova
nada, ele so gasta minuto de runner.

O `ruff` entrou com configuracao propria em `ruff.toml`. O conjunto selecionado
e o padrao (`E4`, `E7`, `E9`, `F`) mais seis familias, cada uma por um motivo
declarado no arquivo. Duas decisoes merecem registro.

**As familias `N`, `A` e `BLE` entraram por causa de comentarios que ja
existiam.** O codigo carregava 24 anotacoes `noqa: N803`, `noqa: A002` e
`noqa: BLE001`, escritas nos checkpoints anteriores para registrar decisoes
deliberadas: a assinatura em CamelCase que o boto3 impoe, o parametro `format`
do `BaseHTTPRequestHandler`, o `except Exception` que vira erro nomeado. Sem
selecionar as familias, o proprio ruff marcava esses comentarios como inuteis
(`RUF100`) e a correcao automatica os apagaria. A ferramenta apagaria a
justificativa em vez de verificar a regra, que e o resultado exatamente
invertido.

**`N818` ficou ignorada.** Ela exige que toda excecao termine em `Error`. As
excecoes do simulador levam de proposito o nome do erro equivalente na AWS:
`ExecutionAlreadyExists` e literalmente o que o Step Functions devolve, e
`TaskFailure` e o nome do evento no historico. Renomea-las quebraria a
correspondencia com o servico que o simulador imita, que e a unica razao de
esses nomes existirem.

`E501` tambem ficou de fora. O formatador ja quebra linha de codigo; o que
sobraria sao comentarios em prosa, e reescrever paragrafo para caber em 88
colunas e trabalho sem leitor.

## O que o lint encontrou

129 achados na primeira passada. 112 sairam com correcao automatica — a maioria
`%`-formatting anterior ao f-string, import fora de ordem e import morto. Os 24
restantes precisaram de decisao, e tres valem nota:

**`B017`, duas ocorrencias.** Dois testes usavam `pytest.raises(Exception)` para
verificar que o caos injetado propaga a falha. Esse teste passa com qualquer
erro, inclusive um `TypeError` vindo de um refactor quebrado — ele continuaria
verde medindo a falha errada. Passaram a esperar `TransientFailure`, que e o que
o `chaos.py` de fato levanta.

**`E741`, tres ocorrencias.** Variavel chamada `l` em compreensao de lista sobre
linhas de log. Virou `linha`.

**`SIM102`.** Um `if` aninhado no validador da state machine virou uma condicao
so de quatro termos.

Depois disso, `ruff format` reformatou 31 dos 39 arquivos Python. A suite ficou
verde nas duas etapas, e a demonstracao local foi conferida de verdade, porque
quatro dos `%`-formatting reescritos faziam alinhamento de coluna (`%-46s`,
`%6s`) — o tipo de mudanca que passa no teste e sai torta na tela. As colunas do
relatorio continuam alinhadas.

O formatador tambem tentou reescrever um bloco de codigo dentro de
`docs/cycle-12.md`: o ruff 0.16 formata Python embutido em Markdown. Os
documentos de ciclo citam o codigo como ele era no dia, e deixar a ferramenta
reescrever essas citacoes seria corrigir a ata da reuniao depois dela. Markdown
saiu do escopo do ruff, e o `docs/cycle-12.md` voltou ao que estava.

Um detalhe do formatador merece registro: ao quebrar uma assinatura longa em
`test_status_handler.py`, ele deixou o `# noqa: N803` numa linha sem argumento
nenhum, e o lint voltou a reprovar. A anotacao desceu para cada parametro.

## Os tres estagios

Um arquivo, `.github/workflows/ci-cd.yml`, com tres jobs.

**Qualidade** e **seguranca** rodam em paralelo e nao recebem credencial
nenhuma. Isso nao e detalhe de desempenho: a suite so pode rodar sem credencial
porque a fixture `autouse` do `conftest.py` troca os clientes boto3 por dubles
em memoria. Um teste que escapasse disso falharia aqui, no runner, que e onde se
quer descobrir.

| Estagio | O que roda |
| --- | --- |
| Qualidade | `ruff check`, `ruff format --check`, `terraform fmt -check -recursive`, os 333 testes |
| Seguranca | `checkov` (le o `.checkov.yaml`), `trivy fs` com os tres scanners |
| Plano | `terraform init`, `validate` e `plan`, assumindo a funcao de leitura |

O estagio de plano e o unico que fala com a AWS, e depende dos dois anteriores.
Ele assume a funcao `…-ci-plan` por OIDC, que nao tem um verbo de escrita fora
do bucket do state — onde escreve apenas o arquivo de trava, para que dois
planos simultaneos nao leiam um state em meio a reescrita.

## Tres decisoes no workflow

**Acoes fixadas por commit, nao por tag.** `actions/checkout@v7` aponta para uma
tag que o dono do repositorio pode mover para outro commit quando quiser. Num
pipeline que assume funcao na AWS, isso e uma porta aberta de terceiro. Cada
`uses` carrega o SHA completo, com a versao legivel no comentario ao lado.

**`-detailed-exitcode` no plano.** Sem a flag, plano sem mudanca, plano com
mudanca e plano que falhou chegam todos como sucesso, e um erro de configuracao
passaria batido. Com ela, 0 e 2 seguem e 1 reprova. A distincao importa porque
plano com mudanca e o caso normal de um pull request, nao uma falha.

**`-lockfile=readonly` no init.** Faz o init reprovar se alguem alterar a versao
de um provider sem atualizar o `.terraform.lock.hcl` versionado, em vez de
resolver silenciosamente para outra coisa. Foi conferido numa copia limpa do
repositorio, sem `.terraform/`, que e a situacao do runner.

**O cancelamento e condicional.** Em branch de trabalho, uma execucao nova
cancela a anterior, porque so interessa o ultimo commit. Em `main` nao cancela:
a partir do ciclo 16 essa fila carrega o `terraform apply`, e cancelar um apply
no meio deixa a infraestrutura em estado desconhecido.

## O unico segredo, e por que ele nao e um segredo de verdade

O workflow le `secrets.AWS_PLAN_ROLE_ARN`. Nao existe chave da AWS em lugar
nenhum: o ARN sozinho nao da acesso a nada, porque quem nao satisfaz a condicao
de confianca do OIDC recebe `AccessDenied` da propria AWS mesmo com o ARN em
maos. Ele esta nos segredos do repositorio porque e ali que o enunciado pede que
a configuracao de acesso viva, e porque o custo de trata-lo como segredo e zero.

## Criterio de pronto

Cada linha abaixo foi executada, nao presumida:

```text
ruff check                    All checks passed!
ruff format --check           39 files already formatted
terraform fmt -check -rec     sem diferenca
pytest                        333 passed
checkov                       257 aprovadas, 0 reprovadas, 5 puladas
trivy fs (MEDIUM+)            exit 0
terraform init -lockfile=readonly  (copia limpa)  exit 0
terraform validate                                exit 0
actionlint .github/workflows/ci-cd.yml            exit 0
./run.sh demo 40              relatorio alinhado, 40 de 40 mensagens
```

O que **nao** foi verificado aqui: a execucao real do estagio de plano. A funcao
de leitura so aceita ser assumida pelo GitHub, entao ela nao roda da maquina —
pelo mesmo motivo, e com a mesma intencao, que a funcao de deploy nao rodava no
ciclo 14. A prova do estagio de plano e a primeira execucao do workflow, que
precisa do segredo configurado e de um push. Fica para a abertura do ciclo 16.
