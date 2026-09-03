# A tabela guarda o resultado e implementa a camada 2 da idempotencia.
#
# A chave primaria E a chave de idempotencia: e a escrita condicional sobre ela
# (attribute_not_exists(pk)) que garante um item por equacao por carga. Nao ha
# tabela separada de "chaves ja vistas" — seria um segundo lugar para manter
# sincronizado com o primeiro.

resource "aws_dynamodb_table" "results" {
  name = local.results_table_name

  # On-demand: nada de capacidade provisionada num sistema que fica ocioso
  # entre demonstracoes. Sem carga, custo zero.
  billing_mode = "PAY_PER_REQUEST"

  hash_key = "pk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "batch_id"
    type = "S"
  }

  attribute {
    name = "created_at"
    type = "N"
  }

  # O painel lista os resultados de UMA carga. Sem este indice, isso seria um
  # Scan com filtro — que le a tabela inteira e cobra por isso.
  global_secondary_index {
    name            = "by_batch"
    projection_type = "ALL"

    # key_schema no lugar de hash_key/range_key: os dois argumentos antigos
    # estao deprecados no provider AWS 6.x.
    key_schema {
      attribute_name = "batch_id"
      key_type       = "HASH"
    }

    key_schema {
      attribute_name = "created_at"
      key_type       = "RANGE"
    }
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    # Dado de laboratorio com TTL de 24 h nao justifica backup continuo, que e
    # cobrado por GB-mes.
    enabled = false
  }
}
