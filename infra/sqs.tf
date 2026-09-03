# Duas filas, com papeis bem diferentes.
#
#   orders        entrada do sistema; o event source mapping a entrega ao
#                 dispatcher em lotes.
#
#   dead-letter   destino de tudo que nao deu certo, por dois caminhos que
#                 chegam ao mesmo lugar:
#
#                   1. o Catch da state machine publica aqui, com o motivo
#                      anexado (source: workflow);
#                   2. a SQS move para ca as mensagens que o dispatcher nao
#                      conseguiu processar depois de max_receive_count
#                      entregas (redrive nativo, sem motivo — o servico move o
#                      payload original e nao sabe por que ele falhou).
#
# O campo `source` no corpo distingue os dois, e e por isso que ele existe.

resource "aws_sqs_queue" "orders" {
  name = local.orders_queue_name

  visibility_timeout_seconds = var.queue_visibility_timeout
  message_retention_seconds  = var.queue_message_retention
  receive_wait_time_seconds  = var.queue_receive_wait_time

  # Criptografia gerenciada pela SQS: sem chave para administrar e sem custo de
  # KMS. Uma chave propria so faria sentido com requisito de segregacao que
  # este projeto nao tem.
  sqs_managed_sse_enabled = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dead_letter.arn
    maxReceiveCount     = var.max_receive_count
  })
}

resource "aws_sqs_queue" "dead_letter" {
  name = local.dead_letter_queue_name

  message_retention_seconds = var.dead_letter_retention
  sqs_managed_sse_enabled   = true

  # So a orders pode usar esta fila como destino de redrive. Sem isso, qualquer
  # fila da conta poderia despejar aqui.
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [local.orders_queue_arn]
  })
}
