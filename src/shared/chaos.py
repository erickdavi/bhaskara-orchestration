"""Falha injetada de proposito, para tornar o retry visivel na demonstracao.

Um painel que so mostra caminho feliz nao demonstra nada sobre resiliencia: o
retry e o dead-letter aparecem no diagrama exatamente igual a "nada aconteceu".
Este modulo existe para que a carga possa pedir a falha.

A falha e **deterministica**, e nao sorteada. Cada Task recebe
`$$.State.RetryCount` no payload, e a funcao falha enquanto a tentativa atual
for menor que o numero pedido:

    fails = 2   ->  tentativa 0 falha, 1 falha, 2 passa   (retry se recupera)
    fails = 5   ->  as 4 tentativas do Retry falham       (vai para a DLQ)

Sortear a falha daria uma demonstracao diferente a cada execucao e um teste
instavel. Contar tentativas da o mesmo resultado sempre, e e o mesmo numero que
o operador ve subindo na timeline do painel.
"""


class TransientFailure(Exception):
    """Falha inesperada simulada: reentregar pode resolver.

    O nome da classe e o `errorType` que a Lambda devolve, e e por ele que o
    `Retry` da state machine reconhece o erro. Renomear esta classe sem
    renomear o ErrorEquals correspondente quebraria o retry em silencio — ha
    um teste em tests/test_asl_definition.py justamente para isso.
    """


def maybe_fail(event, default_name=None):
    """Levanta TransientFailure se a carga pediu caos neste estado.

    O nome do estado vem do proprio fluxo: toda Task recebe
    `state.$: "$$.State.Name"` nos Parameters. Assim a carga pode pedir caos em
    "RootX1" sem que o handler — que atende tres estados diferentes — precise
    saber em qual deles esta rodando. `default_name` cobre a chamada direta,
    fora da state machine.
    """
    state_name = event.get("state") or default_name
    meta = event.get("meta") or {}
    chaos = meta.get("chaos") or {}

    if chaos.get("state") != state_name:
        return

    fails = chaos.get("fails") or 0
    attempt = event.get("retry_count") or 0

    if attempt >= fails:
        return

    raise TransientFailure(
        f"Falha simulada em {state_name}: tentativa {attempt + 1} de {fails}."
    )
