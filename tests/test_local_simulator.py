"""Testes da demonstracao local.

O que importa aqui e a **contabilidade**: toda mensagem publicada precisa
terminar em algum lugar contavel — execucao concluida, recusada pela
idempotencia, ou na dead-letter. Um numero que nao fecha na demonstracao e
exatamente o tipo de coisa que ninguem percebe olhando a tela.
"""

import pytest

from local import runtime
from local.simulator import main, parse_args, run


@pytest.fixture
def stack(aws):
    return aws


def options(quantity=40, invalid=10, duplicates=10, chaos=10, seed=1):
    return parse_args(
        [
            str(quantity),
            "--invalid", str(invalid),
            "--duplicates", str(duplicates),
            "--chaos", str(chaos),
            "--seed", str(seed),
        ]
    )


def test_a_contabilidade_fecha(stack):
    resumo = run(options(quantity=80), stack)

    assert resumo["started"] + resumo["deduplicated"] == resumo["published"] == 80
    assert resumo["succeeded"] + resumo["failed"] == resumo["started"]


def test_os_tres_ramos_sao_exercitados(stack):
    resumo = run(options(quantity=120, invalid=0, chaos=0), stack)

    assert all(total > 0 for total in resumo["branches"].values())
    assert sum(resumo["branches"].values()) == resumo["succeeded"]


def test_sem_invalidas_e_sem_caos_nada_vai_para_a_dead_letter(stack):
    resumo = run(options(quantity=60, invalid=0, chaos=0), stack)

    assert resumo["failed"] == 0
    assert resumo["reasons"] == {}


def test_invalidas_terminam_na_dead_letter_com_motivo(stack):
    resumo = run(options(quantity=80, invalid=40, chaos=0), stack)

    assert resumo["failed"] > 0
    assert "InvalidEquation (permanente, sem retry)" in resumo["reasons"]


def test_o_caos_produz_recuperacao_e_recusa(stack):
    resumo = run(options(quantity=200, invalid=0, chaos=60, seed=5), stack)

    assert resumo["recovered"] > 0, "retry se recuperando"
    assert "TransientFailure (caos esgotou o retry)" in resumo["reasons"]


def test_duplicatas_sao_evitadas_pela_camada_1(stack):
    resumo = run(options(quantity=100, duplicates=50, invalid=0, chaos=0, seed=4), stack)

    assert resumo["deduplicated"] > 0
    assert resumo["started"] < resumo["published"]


def test_a_tabela_guarda_um_item_por_execucao_concluida(stack):
    resumo = run(options(quantity=80, chaos=0), stack)

    assert resumo["items"] == resumo["stored"]


def test_pedir_duplicatas_muda_a_ordem_de_grandeza_da_deduplicacao(aws, monkeypatch):
    """Sem pedir duplicatas, elas ainda acontecem — e esta certo.

    Duas equacoes identicas geradas por acaso na mesma carga sao, para todos os
    efeitos, a mesma equacao: mesma chave, uma execucao. O que muda ao pedir
    duplicatas nao e o comportamento, e a frequencia.
    """
    sem = run(options(quantity=120, duplicates=0, invalid=0, chaos=0, seed=6), runtime.build_local_stack(monkeypatch.setattr))
    com = run(options(quantity=120, duplicates=50, invalid=0, chaos=0, seed=6), runtime.build_local_stack(monkeypatch.setattr))

    assert com["deduplicated"] > sem["deduplicated"] * 3
    assert sem["started"] + sem["deduplicated"] == 120


def test_a_carga_e_reproduzivel(aws, monkeypatch):
    primeira = run(options(quantity=50, seed=99), runtime.build_local_stack(monkeypatch.setattr))
    segunda = run(options(quantity=50, seed=99), runtime.build_local_stack(monkeypatch.setattr))

    for campo in ("started", "deduplicated", "succeeded", "failed", "branches", "reasons"):
        assert primeira[campo] == segunda[campo]


def test_a_demonstracao_roda_pela_linha_de_comando(capsys):
    assert main(["30", "--seed", "2"]) == 0

    saida = capsys.readouterr().out

    assert "Execucoes iniciadas" in saida
    assert "dead-letter" in saida
