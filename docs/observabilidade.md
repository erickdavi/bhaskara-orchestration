# Observabilidade — Checkpoint 4

## Resumo

O sistema do Checkpoint 3 recebe equações do segundo grau por uma fila e as
resolve numa esteira de cinco funções na AWS. Ele funcionava, mas ninguém
conseguia ver o que estava acontecendo lá dentro enquanto rodava. Este
checkpoint instalou essa visibilidade.

A instalação valeu a pena logo na primeira medição séria. Uma das funções
estava levando quase seis segundos para fazer um trabalho que leva treze
milésimos de segundo, e isso vinha acontecendo desde o checkpoint anterior sem
que aparecesse em lugar nenhum. Depois de descobrir a causa e corrigir, a
demora que um usuário sentiria caiu 60% no pior caso.

O documento conta como isso foi descoberto, o que mais os números mostraram, e
o que ficou proposto para depois.

Os dados brutos estão em [`evidencias/medicoes.md`](evidencias/medicoes.md).

---

## O que foi instalado

Quatro coisas, todas usando serviços nativos da AWS e todas declaradas em
Terraform, junto com o resto da infraestrutura.

**Um formato único de log.** Antes, cada uma das sete funções escrevia suas
mensagens do seu próprio jeito. Agora todas escrevem no mesmo formato, e toda
linha carrega os mesmos campos: qual função escreveu, em que etapa do fluxo ela
estava, quanto tempo levou, se foi a primeira tentativa ou uma repetição, e
um identificador da equação que está sendo processada.

Esse identificador é o que faz o resto funcionar. Como ele acompanha a equação
por todas as etapas, dá para pegar uma equação específica e ver o caminho
inteiro que ela percorreu, incluindo as tentativas que falharam no meio.

**Onze medidas de negócio.** Quantas equações entraram, quantas viraram
execução, quantas foram descartadas por já terem sido processadas, quanto tempo
cada etapa levou, quantas caíram em cada um dos três caminhos possíveis do
cálculo, por que as recusadas foram recusadas. A AWS já media coisas genéricas
como "número de invocações"; nenhuma delas sabia o que é uma equação.

Essas medidas viajam dentro da própria linha de log, num formato que a AWS
chama de EMF. A alternativa seria a função fazer uma chamada de API extra a
cada vez que quisesse registrar um número, o que acrescentaria tempo a toda
invocação e mais um ponto de falha. Do jeito escolhido, o custo em tempo é
zero: a AWS lê a métrica do log depois, por conta própria.

**Rastreamento distribuído (X-Ray).** Desenha o caminho de uma requisição
passando por todos os componentes, com o tempo gasto em cada um. O Checkpoint 3
tinha deixado isso desligado com uma justificativa que fazia sentido na época,
e este checkpoint reverteu a decisão. A justificativa antiga está preservada no
arquivo, com a data e o motivo da mudança.

**Um painel e cinco alarmes.** O painel mostra entrada, latência, falha e
saturação numa tela só. Os alarmes avisam quando alguma coisa sai do lugar, e
cada um deles tem escrito na própria descrição o que a pessoa deve fazer quando
ele disparar — o texto vai junto no e-mail.

---

## O painel

![Painel, faixa de cima](evidencias/01-dashboard-entrada-e-latencia.png)

Na primeira faixa, o gráfico da esquerda mostra quantas equações entraram
contra quantas viraram execução de verdade. A diferença entre as duas linhas
são as equações repetidas que o sistema reconheceu e descartou antes de
processar. Isso já era uma promessa do Checkpoint 3; agora é um número que se
vê subir.

O gráfico do meio mostra os três caminhos possíveis do cálculo, conforme a
equação tenha duas raízes, uma só, ou nenhuma raiz real. O da direita mostra
por que as equações recusadas foram recusadas, separadas em quatro motivos.

Na segunda faixa, à esquerda, o tempo que cada etapa leva. As duas metades do
cálculo em paralelo aparecem como linhas separadas, e isso vai importar mais
adiante. À direita, o tempo total da fila até o resultado gravado, que é o
número que mais se aproxima do que um usuário sentiria.

![Painel, faixa de baixo](evidencias/02-dashboard-falha-saturacao-e-log.png)

A parte de baixo trata de falha e de capacidade: execuções que deram certo e
errado, repetições, quantas invocações precisaram ser inicializadas do zero, o
tamanho das filas, e quantas vezes a AWS recusou executar uma função por falta
de capacidade na conta.

O último quadro traz as linhas de erro e alerta mais recentes das sete funções.
Ele fecha o caminho entre ver que alguma coisa aconteceu no gráfico e ler o que
foi, sem trocar de tela.

---

## O caminho de uma equação

![Rastro de uma equação](evidencias/03-logs-insights-rastro-de-uma-equacao.png)

Esta é a tela que melhor mostra o valor do formato único de log. Uma consulta
com uma condição só, filtrando pelo identificador da equação, devolve as nove
linhas que ela produziu ao atravessar cinco funções diferentes:

```text
dispatcher              execução iniciada
validate    Validate    equação validada          tentativa 0
delta       Delta       falha injetada            tentativa 0    tentativa 1 de 2
delta       Delta       falha injetada            tentativa 1    tentativa 2 de 2
dispatcher              execução repetida, descartada
delta       Delta       discriminante calculado   tentativa 2
root        RootX2      raiz calculada            tentativa 0
root        RootX1      raiz calculada            tentativa 0
persist     Persist     resultado gravado         tentativa 0
```

Dá para acompanhar a história inteira. A equação entrou, foi validada, falhou
duas vezes de propósito na etapa do discriminante — o sistema tem um modo que
injeta falhas para demonstrar a recuperação —, foi refeita na terceira
tentativa, teve as duas raízes calculadas em paralelo e foi gravada.

Duas coisas só são possíveis porque estão registradas em toda linha: o número
da tentativa, que mostra a recuperação acontecendo, e o nome da etapa, que
separa as duas metades do cálculo em paralelo. A mesma função atende as duas, e
sem esse campo as linhas seriam indistinguíveis.

---

## O que a medição encontrou

### A função que levava seis segundos

![Tempo por etapa](evidencias/04-logs-insights-p95-por-estado.png)

A primeira consulta séria depois de instalar tudo perguntou quanto tempo cada
etapa leva. O resultado tinha uma anomalia difícil de ignorar: a etapa de
gravação levava treze milésimos de segundo na maioria das vezes, mas em 5% dos
casos levava **quase seis segundos**. Uma diferença de 440 vezes entre o caso
comum e o caso ruim.

A consulta seguinte separou as invocações em duas populações: as que rodavam
numa função já aquecida e as que precisavam inicializar do zero. A cauda inteira
estava nas frias, e as quentes eram todas rápidas.

Isso apontou para a inicialização, mas o número da inicialização era de apenas
83 milésimos de segundo. Os seis segundos estavam acontecendo **depois**, já
dentro do processamento.

A causa era uma decisão do checkpoint anterior. O código criava a conexão com o
banco de dados de forma preguiçosa, só na hora em que fosse usada pela primeira
vez, com a intenção de manter a inicialização leve. O efeito real era o
contrário: a AWS dá bastante processador durante a fase de inicialização e
raciona depois, então o trabalho pesado estava sendo feito exatamente na janela
em que ele custa mais caro.

A correção são três linhas em quatro arquivos, criando a conexão no fim do
carregamento do módulo em vez de na primeira chamada. O que se ganhou, medido
com duas cargas idênticas de 120 equações:

| | Antes | Depois |
| --- | --- | --- |
| Gravação, invocação fria | 5.972 ms | 243 ms |
| Gravação, tempo cobrado em média | 632 ms | 87 ms |
| Tempo total da fila ao resultado, pior 5% | 7.890 ms | 3.120 ms |
| Tempo total, pior caso absoluto | 14.081 ms | 6.716 ms |
| Primeira requisição de envio | 2.833 ms | 309 ms |

O trabalho não desapareceu. Cerca de 417 milésimos migraram para a fase de
inicialização, que é onde eles custam menos. A mesma tarefa que levava quase
seis segundos no lugar errado leva menos de meio segundo no lugar certo.

Em dinheiro isso é uma fração de centavo nesta escala, e vale dizer isso com
todas as letras. O ganho real está em dois outros lugares: a espera que alguém
sentiria caiu pela metade, e uma função que ocupava seis segundos de uma conta
que só permite dez execuções ao mesmo tempo deixou de atrapalhar as outras.

### Um único arquivo de log respondia por 70% do volume

Comparando quanto cada componente escreve, a máquina de estados sozinha gerava
1,74 MB contra 763 KB das sete funções somadas. Por execução, 18,5 KB contra
8,1 KB.

A causa é uma configuração que manda gravar a entrada e a saída de cada etapa
de cada execução. Como são nove etapas e a equação inteira viaja no meio,
acumula rápido.

Desligar essa configuração foi testado com duas cargas iguais de 30 equações.
O volume caiu de 18.856 para 6.994 bytes por execução, uma redução de 63% nesse
arquivo e de 43% em tudo que o sistema escreve.

A dúvida que impedia a adoção era o que se perde junto. A resposta veio olhando
os eventos campo a campo: o nome da etapa continua sendo gravado, então todos
os contadores e o desenho do fluxo no painel continuam funcionando; o motivo de
uma falha também continua. O que some é o texto de detalhe que o painel mostra
em cada passo, do tipo `delta = 49`.

Esse detalhe passa a vir de uma chamada de API que o próprio código já fazia
para outro caminho. Aqui houve uma correção no projeto: uma versão anterior
deste relatório afirmava que essa alternativa já estava pronta, e não estava. O
código chamava a API mas nunca lia o campo de saída. Foi corrigido, com quatro
testes, e verificado contra a AWS com a configuração já desligada.

Mesmo confirmada, a otimização ficou **desligada**. A razão não tem a ver com
técnica: a stack do Checkpoint 3 está no ar sendo corrigida, e o detalhe em
cada passo do painel faz parte daquela entrega. Trocar o comportamento dela
enquanto está sendo avaliada seria trocar cinquenta e nove centavos por 100 mil
execuções pelo risco de uma nota.

Depois da correção sair, é uma linha de comando:

```bash
terraform apply -var 'state_machine_execution_data=false'
```

### O cálculo em paralelo custa muito mais do que a conta que ele faz

![Execução no Step Functions](evidencias/07-step-functions-parallel.png)

Quando a equação tem duas raízes distintas, o fluxo abre dois ramos
concorrentes, um para cada raiz. O tempo medido de cada cálculo é de **31
microssegundos**.

Para fazer 62 microssegundos de aritmética, o fluxo gasta duas invocações de
função com tempo cobrado de 9 milésimos cada, três transições de estado em vez
de uma, e duas das dez execuções simultâneas que a conta permite. Essa última
parte é a que importa. A análise mostrou que o recurso escasso deste sistema é
capacidade de execução simultânea, muito antes de ser processador.

A recomendação aqui é condicional de propósito. O Checkpoint 3 já registrava no
próprio código que essa divisão foi feita para demonstrar o recurso de execução
paralela, com finalidade didática. A medição não contradiz aquela decisão, ela
apenas coloca um número nela. Em produção, o caminho seria juntar os dois ramos
numa etapa só, que é exatamente o que o fluxo já faz no caso da raiz dupla.
Neste repositório o paralelo fica, porque apagá-lo destruiria a evidência da
entrega anterior.

---

## Duas observações que valem mais que as otimizações

**Nenhuma falha da carga foi real.** Os 40 erros registrados batem exatamente
com as 40 falhas que o modo caos injetou de propósito. Sem a medida que separa
as duas coisas, a leitura seria "o sistema falhou 42% das vezes", uma conclusão
errada tirada de dados corretos. Essa medida quase foi cortada durante o
projeto por questão de custo.

**A instrumentação corrigiu uma decisão bem-intencionada.** A conexão preguiçosa
do banco foi escrita para economizar, com um comentário explicando por quê. Ela
custava seis segundos. Sem medir, continuaria lá, parecendo uma boa ideia.

---

## Os alarmes

![Alarmes](evidencias/06-alarmes.png)

Cinco alarmes, e um deles disparou sozinho durante a carga de teste, sem ter
sido forçado: a fila de mensagens recusadas recebeu conteúdo e o alarme reagiu.

Os outros quatro ficaram em OK, incluindo o de execuções falhando. O limite dele
foi posto em cinco justamente para não disparar durante uma demonstração com
falhas injetadas, e não disparou. Um alarme que toca toda vez que a demonstração
roda é um alarme que as pessoas aprendem a ignorar.

---

## O rastreamento ponta a ponta

![Service map do X-Ray](evidencias/05-xray-service-map.png)

O mapa mostra o caminho completo de uma requisição: o cliente, a função que
recebe o pedido, a que traduz mensagem em execução, a máquina de estados, as
quatro funções de cálculo e a fila que recebe o que foi recusado. O arco
vermelho no centro são as execuções que terminaram recusadas.

---

## Quanto custa

Uma demonstração inteira de 120 equações cabe na camada gratuita da AWS em
todos os serviços: execuções, funções, filas, banco, log e rastreamento.

O custo real aparece em outro lugar. As 24 séries de medidas customizadas são
cobradas por mês, existindo elas sendo usadas ou não. São dez gratuitas e
US$ 0,30 por cada uma acima disso, o que dá **US$ 4,20 por mês** no teto.

Isso corrige uma afirmação do Checkpoint 3. O README dizia que nenhum recurso
tinha custo fixo, e depois deste checkpoint isso deixou de ser verdade. A frase
foi trocada em vez de continuar lá por inércia.

O valor acima é teto, não previsão. A documentação da AWS indica que essas
medidas são cobradas proporcionalmente às horas em que recebem dado, e um
laboratório só publica durante as demonstrações. Isso não foi conferido na
fatura, então o número que se deve assumir é o cheio.

---

## O que ficou de fora, e por quê

**Instrumentar os Checkpoints 1 e 2.** Os três estilos de arquitetura já
convivem no Checkpoint 3, que tem entrada por HTTP, fila e orquestração no mesmo
sistema. Observar os outros dois repositórios veria as mesmas coisas em sistemas
menores.

**Aumentar a memória das funções.** Era uma candidata antes da primeira
otimização. Depois dela, a função de gravação roda em 14 milésimos e usa 95 MB
dos 128 disponíveis. Aumentar só faria sentido se o tempo ainda fosse dominado
por processador, o que deixou de ser o caso.

**Eliminar as inicializações do zero reservando capacidade.** A AWS exige deixar
dez execuções não reservadas, e o limite total da conta é dez. Não há como fazer
nesta conta.

**Notificação por e-mail nos alarmes.** O tópico existe e a inscrição está
pronta numa variável, vazia por padrão. Inscrever um e-mail exige uma
confirmação manual por link que o Terraform não consegue completar sozinho.

**O painel web do projeto entre as evidências.** Abri-lo exige digitar a chave
de API na tela, e uma captura com a chave visível seria uma credencial
versionada num repositório público.

---

## Três defeitos que só a captura de tela encontrou

Vale registrar, porque nenhum deles apareceria nos testes nem no `terraform
apply`, que passavam nos três casos.

O quadro de log do painel respondia "nenhum dado encontrado". A consulta estava
montada com um erro de aspas, e o CloudWatch reportou isso como ausência de
dados em vez de erro de sintaxe. A segunda tentativa de correção também estava
errada, e essa falhou de forma visível, reclamando de uma vírgula. A forma que
funciona usa um seletor por prefixo, que ainda tem a vantagem de pegar uma
oitava função automaticamente se ela existir um dia.

O terceiro caso não era defeito. O quadro de recusas ficava vazio porque
nenhuma carga anterior tinha pedido equações inválidas. O dado não existia e o
quadro estava certo ao dizer isso.

---

## Onde está cada coisa

| | |
| --- | --- |
| Números brutos das medições | [`evidencias/medicoes.md`](evidencias/medicoes.md) |
| Telas do console | [`evidencias/`](evidencias/) |
| Texto pronto para o Canvas | [`entrega-canvas.md`](entrega-canvas.md) |
| Decisão de cada ciclo | [`cycle-08.md`](cycle-08.md) a [`cycle-13.md`](cycle-13.md) |
| Plano original | [`especificacao-cp4.md`](especificacao-cp4.md) |
| Infraestrutura do painel e dos alarmes | [`../infra/observability.tf`](../infra/observability.tf) |
