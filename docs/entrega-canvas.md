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
grau numa esteira de cinco funções Lambda orquestradas pelo Step Functions, com
fila de entrada, banco DynamoDB e fila de mensagens recusadas. Ele funcionava,
porém era opaco por dentro: cada função registrava log do seu próprio jeito e
não havia nenhuma métrica além das que a AWS emite sozinha, que contam
invocações e erros sem saber o que é uma equação.

A instrumentação seguiu quatro frentes. A primeira padronizou o formato do log,
de modo que toda linha carregue os mesmos campos e uma equação possa ser
rastreada pelas cinco funções a partir do identificador dela. A segunda criou
onze métricas de negócio, escritas dentro da própria linha de log para que
nenhuma chamada de API extra entre no caminho de execução. A terceira ligou o
rastreamento distribuído do X-Ray, que o Checkpoint 3 havia deixado desligado
com justificativa registrada no código. A quarta reuniu tudo num painel do
CloudWatch, com cinco alarmes e cinco consultas salvas, tudo declarado em
Terraform. As três otimizações abaixo saem dos dados que essas quatro frentes
produziram em cargas reais na AWS.

**Primeira otimização — onde a conexão com a AWS é criada. Implementada e
medida.** A consulta de duração por etapa mostrou que a gravação levava treze
milésimos de segundo na maior parte das vezes e quase seis segundos em cinco por
cento dos casos. Separando as invocações frias das quentes, a lentidão inteira
estava nas frias, mas o tempo de inicialização em si era de apenas oitenta e
três milésimos, o que indicava que os seis segundos aconteciam já dentro do
processamento. A causa era o cliente do banco de dados criado de forma
preguiçosa, apenas no primeiro uso, decisão tomada no checkpoint anterior para
deixar a inicialização leve. O efeito era o oposto, porque a AWS concede
processamento ampliado durante a inicialização e o raciona depois, de forma que
o trabalho pesado acontecia na janela mais cara. Movendo a criação da conexão
para o carregamento do módulo, medido com duas cargas idênticas de cento e vinte
equações, a invocação fria caiu de 5.972 para 243 milissegundos, o tempo cobrado
médio caiu de 632 para 87 milissegundos e a latência da fila até o resultado
caiu de 7.890 para 3.120 milissegundos nos cinco por cento piores casos. Em
dinheiro a economia é uma fração de centavo nesta escala; o ganho está na espera
e na capacidade liberada, já que a conta permite apenas dez execuções
simultâneas.

**Segunda otimização — o volume de log da máquina de estados. Confirmada e
medida, desligada por escolha.** Comparando quanto cada componente escreve, a
máquina de estados sozinha gerava 1,74 megabyte contra 763 kilobytes das sete
funções somadas, respondendo por setenta por cento de tudo que o sistema
registra. A causa é a configuração que grava a entrada e a saída de cada uma das
nove etapas de cada execução. Testado com duas cargas iguais de trinta equações,
desligar isso derruba o volume de 18.856 para 6.994 bytes por execução, uma
redução de sessenta e três por cento naquele componente e de quarenta e três por
cento no total. Inspecionando os eventos campo a campo, o nome da etapa e o
motivo de uma falha continuam gravados, de modo que os contadores e o desenho do
fluxo no painel seguem funcionando; perde-se apenas o texto de detalhe de cada
passo, que passa a vir de uma chamada de API já existente no código. A
otimização ficou desligada mesmo confirmada, porque a infraestrutura do
Checkpoint 3 continua no ar para correção e esse detalhe faz parte daquela
entrega. A chave para ligá-la está pronta numa variável do Terraform.

**Terceira otimização — o cálculo paralelo das duas raízes. Proposta, com
ressalva.** Quando o discriminante é positivo, o fluxo abre dois ramos
concorrentes e calcula uma raiz em cada um. Cada cálculo leva trinta e um
microssegundos. Para executar sessenta e dois microssegundos de aritmética, o
sistema gasta duas invocações de função com nove milissegundos cobrados cada,
três transições de estado em vez de uma, e duas das dez execuções simultâneas
que a conta permite, o que importa porque a análise apontou a capacidade
simultânea como o recurso escasso deste sistema. A recomendação é condicional de
propósito: o Checkpoint 3 já registrava no próprio código que essa divisão foi
feita para demonstrar o recurso de execução paralela, com finalidade didática, e
a medição apenas coloca um número naquela decisão. Em produção o caminho seria
juntar os dois ramos numa etapa só, que é o que o fluxo já faz quando a raiz é
dupla; neste repositório o paralelo permanece, porque removê-lo apagaria a
evidência da entrega anterior.

Vale registrar uma observação que talvez valha mais que as três otimizações. Os
quarenta erros registrados durante a carga de teste coincidem exatamente com as
quarenta falhas que o modo de caos injetou de propósito. Sem a métrica que
separa uma coisa da outra, a leitura seria de que o sistema falhou em quarenta e
dois por cento das execuções, uma conclusão errada extraída de dados corretos.

Sobre o custo da própria observabilidade, uma demonstração completa cabe na
camada gratuita da AWS em todos os serviços envolvidos, mas as vinte e quatro
séries de métricas customizadas são cobradas por mês independentemente do uso, o
que dá até quatro dólares e vinte centavos mensais. Isso invalidou uma afirmação
do Checkpoint 3 de que a infraestrutura não tinha recurso de custo fixo, e o
README foi corrigido.

Quanto à segurança, não há credencial no repositório. A chave de API é gerada
pelo Terraform e sai por `terraform output`, a URL da função ativa não está
versionada, e o número da conta foi tarjado nas telas de evidência. A varredura
de segredos cobriu o histórico inteiro do Git. O `checkov` passa com 167
verificações e nenhuma reprovação, e o `trivy` não acusa achados de severidade
média ou superior.

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
