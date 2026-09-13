# Entrega no Canvas — Checkpoint 4

Dois campos. O primeiro é o link; o segundo é para colar o bloco abaixo.

---

## Parte 1 — Campo de URL

```
https://github.com/erickdavi/bhaskara-orchestration
```

O enunciado pede para instrumentar o pipeline dos checkpoints anteriores, então
a entrega vive no mesmo repositório do Checkpoint 3. A tag `cp3-entrega` marca
o estado que foi entregue lá, para que a correção daquele checkpoint continue
possível.

---

## Parte 2 — Caixa de texto

Copiar daqui para baixo.

---

**Checkpoint 4 — Observabilidade**
Repositório: https://github.com/erickdavi/bhaskara-orchestration
Relatório completo, com as telas do console: `docs/observabilidade.md`

O sistema do Checkpoint 3 resolve equações do segundo grau numa esteira de cinco
funções Lambda orquestradas por Step Functions. Ele funcionava, mas não havia
como enxergar o que acontecia lá dentro enquanto rodava. Este checkpoint
instalou log estruturado, métricas de negócio, rastreamento distribuído no
X-Ray, um painel do CloudWatch, cinco alarmes e cinco consultas salvas — tudo
declarado em Terraform.

As otimizações abaixo saem de cargas reais medidas na AWS.

**1. Corrigir onde a conexão com a AWS é criada. Implementada e medida.**
A medição de tempo por etapa mostrou uma anomalia: a etapa de gravação levava 13
milésimos de segundo na maioria das vezes e quase 6 segundos em 5% dos casos.
Separando as invocações que rodavam numa função já aquecida das que precisavam
inicializar, a lentidão inteira estava nas frias. Mas o tempo de inicialização
em si era de apenas 83 milésimos, então os seis segundos estavam acontecendo
depois, já dentro do processamento.

A causa era uma decisão do checkpoint anterior: o cliente do banco de dados era
criado de forma preguiçosa, só na primeira vez que fosse usado, para manter a
inicialização leve. O efeito era o contrário do pretendido, porque a AWS dá
bastante processador durante a inicialização e raciona depois. O trabalho pesado
estava sendo feito justamente na janela em que custa mais caro.

A correção move a criação do cliente para o carregamento do módulo. Medido com
duas cargas idênticas de 120 equações: a invocação fria da gravação caiu de
5.972 ms para 243 ms, o tempo cobrado médio caiu de 632 ms para 87 ms, e o tempo
total da fila até o resultado caiu de 7.890 ms para 3.120 ms nos piores 5%. Em
dinheiro isso é uma fração de centavo nesta escala; o ganho está na espera e na
capacidade liberada, já que a conta permite apenas dez execuções simultâneas.

**2. Parar de gravar o conteúdo de cada etapa no log da máquina de estados.
Confirmada e medida, desligada por escolha.**
Um único grupo de log respondia por 70% de tudo que o sistema escreve: 1,74 MB
contra 763 KB das sete funções somadas. A causa é uma configuração que grava a
entrada e a saída de cada uma das nove etapas de cada execução.

Testado com duas cargas iguais de 30 equações, desligar isso derruba o volume de
18.856 para 6.994 bytes por execução, uma redução de 63% naquele grupo e de 43%
em tudo que o sistema escreve. Verificando evento a evento o que se perde: o
nome da etapa continua gravado, então todos os contadores e o desenho do fluxo
no painel seguem funcionando, e o motivo de uma falha também continua. Some
apenas o texto de detalhe de cada passo, que passa a vir de uma chamada de API
que o código já fazia para outro caminho.

Ficou desligada mesmo assim, por um motivo que não é técnico: a stack do
Checkpoint 3 está no ar sendo corrigida, e o detalhe em cada passo faz parte
daquela entrega. A chave está pronta numa variável do Terraform.

**3. Juntar o cálculo das duas raízes numa etapa só. Proposta, com ressalva.**
Quando a equação tem duas raízes distintas, o fluxo abre dois ramos paralelos.
Cada cálculo leva 31 microssegundos. Para fazer isso, o fluxo gasta duas
invocações de função com 9 milésimos cobrados cada, três transições de estado em
vez de uma, e duas das dez execuções simultâneas da conta.

A recomendação é condicional de propósito. O Checkpoint 3 já registrava no
próprio código que essa divisão foi feita para demonstrar o recurso de execução
paralela, com finalidade didática. A medição não contradiz aquela decisão, só
coloca um número nela. Em produção o caminho seria juntar os dois ramos, que é o
que o fluxo já faz no caso da raiz dupla. Neste repositório o paralelo fica,
porque removê-lo apagaria a evidência da entrega anterior.

**Uma observação que vale mais que as três.** Os 40 erros registrados na carga
batem exatamente com as 40 falhas que o modo caos injetou de propósito. Sem a
métrica que separa falha pedida de falha real, a leitura seria "o sistema falhou
42% das vezes", uma conclusão errada tirada de dados corretos.

**Sobre o custo da observabilidade.** O Checkpoint 3 afirmava que a stack não
tinha nenhum recurso com custo fixo, e isso deixou de valer. As 24 séries de
métricas customizadas são cobradas por mês: dez são gratuitas e as demais custam
US$ 0,30 cada, o que dá US$ 4,20 mensais no teto. O README foi corrigido.

**Segurança.** Nenhuma credencial no repositório. A chave de API é gerada pelo
Terraform e sai por `terraform output`; a URL da função ativa não está
versionada. Varredura de segredos feita no histórico inteiro. O `checkov` passa
com 167 verificações e nenhuma reprovação, e o `trivy` não acusa nada de
severidade média ou superior.

---

## Endpoints ativos, se for entregar a URL viva

Rodar antes de colar. Nenhum destes valores está versionado:

```bash
cd infra
terraform output -raw dashboard_url            # painel do fluxo
terraform output -raw api_key                  # chave para colar no painel
terraform output -raw cloudwatch_dashboard_url # painel de observabilidade
```

O painel do CloudWatch e o mapa do X-Ray só abrem para quem estiver autenticado
na conta. Para a correção valem as telas dentro de `docs/observabilidade.md`.
