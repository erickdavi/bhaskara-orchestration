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

**Checkpoint 4 — Observabilidade**
Repositório: https://github.com/erickdavi/bhaskara-orchestration
Relatório completo, com as telas do console: `docs/observabilidade.md`

O sistema instrumentado é o do Checkpoint 3, que resolve equações do segundo
grau numa esteira de cinco funções Lambda orquestradas pelo Step Functions. A
instrumentação seguiu quatro frentes: um formato único de log que permite
rastrear uma equação pelas cinco funções a partir do identificador dela, onze
métricas de negócio escritas dentro da própria linha de log, sem chamada de API
extra no caminho de execução, o rastreamento distribuído do X-Ray, e um painel
do CloudWatch com cinco alarmes e cinco consultas salvas, tudo em Terraform.

**Primeira otimização, implementada e medida.** A duração por etapa mostrou a
gravação levando treze milésimos de segundo na maior parte das vezes e quase
seis segundos em cinco por cento dos casos, toda a lentidão concentrada nas
invocações frias. A causa era o cliente do banco de dados criado de forma
preguiçosa no primeiro uso, decisão do checkpoint anterior para deixar a
inicialização leve. O efeito era o oposto, porque a AWS concede processamento
ampliado durante a inicialização e o raciona depois. Movendo a criação para o
carregamento do módulo, a invocação fria caiu de 5.972 para 243 milissegundos e
a latência da fila até o resultado caiu de 7.890 para 3.120 milissegundos nos
piores cinco por cento.

**Segunda otimização, confirmada e medida.** A máquina de estados respondia por
setenta por cento de todo o log do sistema, por gravar a entrada e a saída de
cada uma das nove etapas. Desligar esse registro reduz o volume em sessenta e
três por cento naquele componente e quarenta e três por cento no total,
preservando os contadores e o desenho do fluxo no painel. Ficou desligada porque
a infraestrutura do Checkpoint 3 segue no ar para correção e o detalhe removido
faz parte daquela entrega.

**Terceira otimização, proposta.** O cálculo paralelo das duas raízes leva trinta
e um microssegundos por raiz, e gasta para isso duas invocações de função, três
transições de estado e duas das dez execuções simultâneas que a conta permite. A
recomendação vale para produção; aqui o paralelo permanece, porque foi
construído no Checkpoint 3 para demonstrar o recurso.

Uma observação que talvez valha mais que as três: os quarenta erros da carga de
teste coincidem exatamente com as quarenta falhas que o modo de caos injetou.
Sem a métrica que separa as duas, a leitura seria de quarenta e dois por cento
de falha, uma conclusão errada extraída de dados corretos.

Não há credencial no repositório: a chave de API sai por `terraform output`, a
URL ativa não está versionada e o número da conta foi tarjado nas telas.

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
