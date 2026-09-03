"""A definicao da state machine sob teste.

O YAML nao e documentacao: ele e executado em dois lugares — pelo Step
Functions na nuvem e pelo interpretador em local/engine.py. Estes testes
existem para que os dois nunca divirjam.

O mais importante deles e `test_definicao_e_suportada_pelo_interpretador`: se
alguem acrescentar um `Map` ou um `Wait` ao fluxo, a suite quebra aqui, antes
de a demonstracao local passar a mentir sobre o que roda na nuvem.
"""

import pytest

from local import runtime
from local.engine import ALLOWED_FIELDS, Unsupported, validate_definition


@pytest.fixture
def definition():
    return runtime.load_definition()


def test_yaml_carrega_e_tem_estrutura_de_state_machine(definition):
    assert definition["StartAt"] in definition["States"]
    assert definition["Comment"]


def test_definicao_e_suportada_pelo_interpretador(definition):
    validate_definition(definition)


def test_todo_placeholder_tem_implementacao_local(definition):
    resources = runtime.build_resources()

    faltando = runtime.placeholders(definition) - set(resources)

    assert faltando == set(), "sem binding local para: %s" % ", ".join(sorted(faltando))


def test_todo_estado_tem_tipo_conhecido(definition):
    for name, state in definition["States"].items():
        assert state["Type"] in ALLOWED_FIELDS, name


def test_toda_task_tem_retry(definition):
    for name, state in states_of(definition):
        if state["Type"] == "Task" and state.get("Resource", "").startswith("${"):
            assert state.get("Retry"), "%s sem Retry" % name


def test_erro_permanente_e_avaliado_antes_do_generico(definition):
    """A ordem dos retriers e regra de negocio, nao estilo.

    O Step Functions usa o primeiro retrier que casa. States.TaskFailed casa
    com InvalidEquation tambem — se vier antes, uma equacao invalida seria
    reentregue tres vezes sem nenhuma chance de sucesso.
    """
    for name, state in states_of(definition):
        retriers = state.get("Retry") or []
        erros = [rule["ErrorEquals"] for rule in retriers]

        permanentes = [i for i, e in enumerate(erros) if "InvalidEquation" in e]
        genericos = [i for i, e in enumerate(erros) if "States.TaskFailed" in e]

        if permanentes and genericos:
            assert min(permanentes) < min(genericos), name


def test_retry_permanente_nao_reentrega(definition):
    for name, state in states_of(definition):
        for rule in state.get("Retry") or []:
            if "InvalidEquation" in rule["ErrorEquals"]:
                assert rule["MaxAttempts"] == 0, name


def test_backoff_e_exponencial(definition):
    for name, state in states_of(definition):
        for rule in state.get("Retry") or []:
            if rule.get("MaxAttempts") == 0:
                continue

            assert rule["BackoffRate"] > 1, name
            assert rule["IntervalSeconds"] >= 1, name


def test_definicao_invalida_e_recusada():
    with pytest.raises(Unsupported):
        validate_definition({"StartAt": "A", "States": {"A": {"Type": "Map", "End": True}}})


def test_estado_que_aponta_para_lugar_nenhum_e_recusado():
    with pytest.raises(Unsupported):
        validate_definition({"StartAt": "A", "States": {"A": {"Type": "Pass", "Next": "B"}}})


def states_of(definition, states=None):
    """Percorre estados de topo e de dentro de Parallel."""
    states = states if states is not None else definition["States"]

    for name, state in states.items():
        yield name, state

        for branch in state.get("Branches") or []:
            for item in states_of(definition, branch["States"]):
                yield item


def test_toda_task_recebe_nome_do_estado_e_tentativa(definition):
    """Sem esses dois campos o modo caos nao consegue mirar nem contar.

    `state.$` diz a funcao em qual estado ela roda — o handler de raiz atende
    tres — e `retry_count.$` e o que torna a falha injetada deterministica.
    """
    for name, state in states_of(definition):
        if state["Type"] != "Task" or not state.get("Resource", "").startswith("${"):
            continue

        parameters = state.get("Parameters") or {}

        assert parameters.get("state.$") == "$$.State.Name", name
        assert parameters.get("retry_count.$") == "$$.State.RetryCount", name


def test_todo_caminho_do_choice_leva_a_um_resultado(definition):
    """Os tres ramos precisam entregar a mesma forma ao estado seguinte."""
    states = definition["States"]
    choice = states["ChooseRoots"]

    destinos = [c["Next"] for c in choice["Choices"]] + [choice["Default"]]

    assert len(destinos) == 3

    for destino in destinos:
        assert states[destino].get("ResultPath") == "$.result", destino


def test_toda_task_tem_catch_ou_esta_dentro_de_um_parallel_que_tem(definition):
    """Nenhuma Task pode falhar sem que a mensagem chegue a dead-letter.

    Dentro de um Parallel a Task nao pode ter o proprio Catch: em ASL, o Next
    de um estado so aponta para estados do mesmo nivel, e DeadLetter e de fora.
    A falha do ramo falha o Parallel, e o Catch vive la.
    """
    topo = definition["States"]

    for name, state in topo.items():
        if state["Type"] in ("Task", "Parallel") and name != "DeadLetter":
            assert state.get("Catch"), "%s sem Catch" % name

    for name, state in topo.items():
        for branch in state.get("Branches") or []:
            assert state.get("Catch"), "ramo de %s sem Catch no Parallel" % name


def test_todo_catch_leva_a_dead_letter(definition):
    for name, state in states_of(definition):
        for rule in state.get("Catch") or []:
            assert rule["Next"] == "DeadLetter", name
            assert rule["ResultPath"] == "$.error", name


def test_a_dead_letter_publica_direto_na_sqs(definition):
    """Sem Lambda no caminho de erro: menos concorrencia e menos a falhar."""
    dead_letter = definition["States"]["DeadLetter"]

    assert dead_letter["Resource"] == "arn:aws:states:::sqs:sendMessage"
    assert dead_letter["Parameters"]["MessageBody"]["source"] == "workflow"
    assert dead_letter["Parameters"]["MessageAttributes"]["RejectionReason"]["StringValue.$"] == "$.error.Error"


def test_a_recusa_termina_a_execucao_como_falha(definition):
    """Terminar em Succeed esconderia a recusa numa lista de sucessos."""
    assert definition["States"]["DeadLetter"]["Next"] == "Rejected"
    assert definition["States"]["Rejected"]["Type"] == "Fail"


def test_todo_placeholder_do_arquivo_e_conhecido():
    """Placeholder com nome errado viraria texto vazio no apply."""
    conhecidos = {name.strip("${}") for name in runtime.HANDLERS} | set(runtime.LOCAL_SUBSTITUTIONS)

    assert runtime.raw_placeholders() <= conhecidos


# Estados que de proposito nao viram no no diagrama, e por que.
SEM_NO_NO_DIAGRAMA = {
    "ChooseRoots": "o Choice e desenhado como a bifurcacao entre os tres ramos",
    "RootX1": "aparece dentro do no do Parallel",
    "RootX2": "aparece dentro do no do Parallel",
    "Rejected": "estado terminal Fail; o que interessa e a fila de dead-letter",
    "Done": "estado terminal Succeed; o desfecho aparece nos contadores",
}


def diagram_nodes():
    """Le a lista de nos do painel direto do JavaScript.

    Um teste que dependesse de uma copia da lista aqui nao pegaria nada: o que
    se quer verificar e que o **painel** conhece os estados que existem.
    """
    import os
    import re

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "flow.js")

    with open(path, encoding="utf-8") as handle:
        source = handle.read()

    declaration = re.search(r"window\.FLOW_NODES\s*=\s*\[(.*?)\]", source, re.S)

    return set(re.findall(r'"([^"]+)"', declaration.group(1)))


def test_todo_estado_da_asl_tem_no_no_painel_ou_justificativa(definition):
    """Renomear um estado no YAML apagaria um no do diagrama em silencio."""
    nos = diagram_nodes()

    for name, _ in states_of(definition):
        assert name in nos or name in SEM_NO_NO_DIAGRAMA, (
            "o estado %s nao aparece no painel e nao esta na lista de excecoes" % name
        )


def test_todo_no_do_painel_existe_na_asl(definition):
    """O contrario tambem: um no orfao mostraria um contador que nunca sobe."""
    estados = {name for name, _ in states_of(definition)}

    assert diagram_nodes() <= estados
