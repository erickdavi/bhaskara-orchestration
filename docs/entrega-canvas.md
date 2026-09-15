# Entrega no Canvas — Checkpoint 4

Dois campos. O primeiro recebe o link; o segundo recebe o bloco de texto que
começa depois da linha divisória.

---

## Parte 1 — Campo de URL

```
https://github.com/erickdavi/bhaskara-orchestration
```

O enunciado pede para instrumentar o pipeline dos checkpoints anteriores, então
a entrega vive no mesmo repositório do Checkpoint 3. A tag `cp3-entrega` marca o
estado que foi entregue naquele checkpoint, para que a correção dele continue
possível.

---

## Parte 2 — Caixa de texto

Copiar daqui para baixo.

---

Checkpoint 4 — Observabilidade
Repositório: https://github.com/erickdavi/bhaskara-orchestration
Relatório completo, com as telas do console: docs/observabilidade.md

O pipeline do Checkpoint 3 foi instrumentado com log estruturado que permite
rastrear uma equação pelas cinco funções, onze métricas de negócio escritas
dentro da própria linha de log, rastreamento distribuído no X-Ray e um painel do
CloudWatch com cinco alarmes, tudo declarado em Terraform. Os dados apontaram
três otimizações. A primeira já está implementada: o cliente do banco era criado
na primeira invocação, e não na inicialização da função, onde a AWS concede mais
processamento, o que derrubava a invocação fria para quase seis segundos;
corrigido, ela caiu para 243 milissegundos e a latência da fila até o resultado
caiu de 7.890 para 3.120 milissegundos nos piores cinco por cento. A segunda
está confirmada e desligada por escolha, já que a infraestrutura do Checkpoint 3
segue em correção: o log da máquina de estados respondia por setenta por cento
do volume total, e desligar o registro do conteúdo de cada etapa corta quarenta
e três por cento de tudo que o sistema escreve, preservando os contadores do
painel. A terceira é proposta para produção: o cálculo paralelo das duas raízes
gasta duas invocações de função e três transições de estado para trinta e um
microssegundos de aritmética por raiz.

---

## Endpoints ativos, caso queira entregar a URL viva

Rodar antes de colar. Nenhum destes valores está versionado:

```bash
cd infra
terraform output -raw dashboard_url            # painel do fluxo
terraform output -raw api_key                  # chave para colar no painel
terraform output -raw cloudwatch_dashboard_url # painel de observabilidade
```

O painel do CloudWatch e o mapa do X-Ray só abrem para quem estiver autenticado
na conta. Para a correção valem as telas dentro de `docs/observabilidade.md`.
