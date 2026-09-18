"""Testes do gerador de carga.

A carga nao serve para "ter volume": ela serve para acender, no painel, os
caminhos que este checkpoint precisa demonstrar. Por isso os testes verificam
distribuicao e intencao — os tres ramos aparecendo, o caos mirando estados que
a equacao realmente visita, as duplicatas sendo duplicatas de verdade.
"""

import json
from collections import Counter

import pytest

from src.handlers.submit.generator import CHAOS_STATES, generate


def bodies(*args, **kwargs):
    return [body for body, _ in generate(*args, **kwargs)]


def outcome_of(body):
    equation = json.loads(body)
    delta = equation["b"] ** 2 - 4 * equation["a"] * equation["c"]

    return (
        "two_roots" if delta > 0 else ("double_root" if delta == 0 else "no_real_roots")
    )


def test_gera_a_quantidade_pedida():
    assert len(bodies(50, seed=1)) == 50


def test_sem_proporcoes_tudo_e_equacao_valida():
    for body in bodies(60, seed=1):
        equation = json.loads(body)

        assert set(equation) == {"a", "b", "c"}
        assert equation["a"] != 0


def test_os_tres_desfechos_aparecem():
    """Sorteando a, b e c soltos, raiz dupla praticamente nunca sairia."""
    contagem = Counter(outcome_of(body) for body in bodies(300, seed=2))

    assert set(contagem) == {"two_roots", "double_root", "no_real_roots"}
    assert min(contagem.values()) > 50


def test_raizes_de_delta_positivo_sao_inteiras():
    """Numeros redondos deixam o resultado conferivel a olho no painel."""
    for body in bodies(120, seed=3):
        equation = json.loads(body)
        delta = equation["b"] ** 2 - 4 * equation["a"] * equation["c"]

        if delta <= 0:
            continue

        raiz = (-equation["b"] + delta**0.5) / (2 * equation["a"])

        assert raiz == pytest.approx(round(raiz))


def test_mesma_seed_gera_a_mesma_carga():
    assert bodies(30, 0.2, 0.2, 0.2, seed=9) == bodies(30, 0.2, 0.2, 0.2, seed=9)


def test_seeds_diferentes_geram_cargas_diferentes():
    assert bodies(30, seed=1) != bodies(30, seed=2)


# ------------------------------------------------------------------ invalidas


def test_proporcao_de_invalidas_e_respeitada():
    invalidas = sum(
        1 for body in bodies(400, invalid_ratio=0.25, seed=4) if not valida(body)
    )

    assert 70 < invalidas < 130


def test_todos_os_tipos_de_invalida_aparecem():
    """Cada tipo exercita um caminho de recusa diferente do Validate."""
    tipos = set()

    for body in bodies(600, invalid_ratio=1.0, seed=5):
        try:
            payload = json.loads(body)
        except ValueError:
            tipos.add("malformed_json")
            continue

        if len(payload) < 3:
            tipos.add("missing_coefficient")
        elif isinstance(payload["a"], str):
            tipos.add("coefficient_as_string")
        elif isinstance(payload["a"], bool):
            tipos.add("coefficient_as_boolean")
        elif payload["a"] == 0:
            tipos.add("a_is_zero")

    assert len(tipos) == 5


def valida(body):
    try:
        payload = json.loads(body)
    except ValueError:
        return False

    return (
        set(payload) == {"a", "b", "c"}
        and all(
            isinstance(v, (int, float)) and not isinstance(v, bool)
            for v in payload.values()
        )
        and payload["a"] != 0
    )


# ---------------------------------------------------------------- duplicatas


def test_duplicatas_repetem_mensagens_anteriores():
    corpos = bodies(200, duplicate_ratio=0.4, seed=6)

    assert len(set(corpos)) < len(corpos) * 0.8


def test_sem_duplicatas_pedidas_a_repeticao_e_rara():
    corpos = bodies(100, seed=7)

    assert len(set(corpos)) > 90


def test_a_duplicata_repete_a_mensagem_inteira_e_nao_so_o_corpo():
    """A repeticao copia o par (corpo, caos), e nao sorteia caos de novo.

    Duas mensagens com o mesmo corpo e caos diferente nao quebram nada — a
    camada 1 da idempotencia descarta a segunda antes de qualquer estado rodar
    —, mas a repeticao intencional precisa ser uma copia fiel, senao o painel
    mostraria "duplicada" para algo que na verdade e outra mensagem.
    """
    pares = [
        (body, json.dumps(chaos, sort_keys=True))
        for body, chaos in generate(200, chaos_ratio=0.5, duplicate_ratio=0.5, seed=8)
    ]

    assert len(set(pares)) < len(pares) * 0.8


# --------------------------------------------------------------------- caos


def test_sem_caos_pedido_nenhuma_mensagem_traz_caos():
    assert all(chaos is None for _, chaos in generate(50, seed=1))


def test_proporcao_de_caos_e_respeitada():
    com_caos = sum(1 for _, chaos in generate(400, chaos_ratio=0.25, seed=9) if chaos)

    assert 70 < com_caos < 130


def test_o_caos_mira_estados_que_a_equacao_realmente_visita():
    """Pedir caos em RootDouble para delta > 0 nunca falharia — e a carga
    entregaria menos falhas do que o operador pediu."""
    for body, chaos in generate(400, chaos_ratio=1.0, seed=10):
        if chaos is None:
            continue

        esperado = CHAOS_STATES["invalid" if not valida(body) else outcome_of(body)]

        assert chaos["state"] in esperado


def test_o_caos_traz_os_dois_desfechos():
    """Ate 3 falhas o retry se recupera; 5 esgota e vai para a dead-letter."""
    fails = {
        chaos["fails"] for _, chaos in generate(400, chaos_ratio=1.0, seed=11) if chaos
    }

    assert fails <= {1, 2, 3, 5}
    assert 5 in fails
    assert fails & {1, 2, 3}
