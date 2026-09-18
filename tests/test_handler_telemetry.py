"""O que cada handler mede — verificado nos sete, num arquivo so.

Os testes de cada handler cobrem a regra de negocio dele. Este cobre uma
propriedade que atravessa os sete: **toda invocacao deixa medida**. Ele fica
separado de proposito. Se estivesse dividido entre `test_validate_handler.py`,
`test_delta_handler.py` e os outros cinco, um handler novo entraria sem
instrumentacao nenhuma e nenhum teste falharia — porque o teste que faltaria e
justamente o que ninguem escreveu.

O que se assegura aqui:

- toda invocacao emite `HandlerDuration` com a dimensao do servico;
- o estado entra na dimensao quando existe, e os tres estados do handler `root`
  ficam separados;
- cada desfecho de negocio tem a sua contagem;
- nenhuma linha usa identificador como dimensao.
"""

import json

import observability
import pytest
from chaos import TransientFailure
from metrics import FORBIDDEN

from local import runtime
from src.handlers.delta.handler import lambda_handler as delta
from src.handlers.dispatcher.handler import lambda_handler as dispatcher
from src.handlers.persist.handler import lambda_handler as persist
from src.handlers.root.handler import lambda_handler as root
from src.handlers.submit.handler import lambda_handler as submit
from src.handlers.validate.handler import InvalidEquation
from src.handlers.validate.handler import lambda_handler as validate


class Context:
    aws_request_id = "req-1"

    def get_remaining_time_in_millis(self):
        return 30000


def lines(capsys):
    return [
        json.loads(linha)
        for linha in capsys.readouterr().out.strip().splitlines()
        if linha.strip()
    ]


def measured(capsys, service=None):
    """A linha de medicao da invocacao: a unica que carrega o bloco EMF.

    O `service` existe por causa do dispatcher: a fila em memoria executa o
    fluxo inteiro em processo, entao a invocacao dele produz tambem as linhas
    dos cinco estados. Nos demais handlers, filtrar por servico e redundante —
    e a asserticao de que ha exatamente uma linha medida por invocacao continua
    valendo dentro do servico filtrado.
    """
    com_metrica = [
        linha
        for linha in lines(capsys)
        if "_aws" in linha and (service is None or linha.get("service") == service)
    ]

    assert len(com_metrica) == 1, f"esperava uma linha medida, veio {len(com_metrica)}"

    return com_metrica[0]


def metric_names(linha):
    return sorted(
        m["Name"] for g in linha["_aws"]["CloudWatchMetrics"] for m in g["Metrics"]
    )


def dimensions(linha):
    return {d for g in linha["_aws"]["CloudWatchMetrics"] for d in g["Dimensions"][0]}


def task(state, **extra):
    payload = {
        "state": state,
        "retry_count": 0,
        "meta": {"idempotency_key": "k-abc", "batch_id": "b-1", "submitted_at": 1},
    }
    payload.update(extra)
    return payload


# ---------------------------------------------------------- toda invocacao


def test_validate_mede_a_duracao(capsys):
    validate(task("Validate", equation={"a": 1, "b": -5, "c": 6}), Context())

    linha = measured(capsys)

    assert "HandlerDuration" in metric_names(linha)
    assert linha["Service"] == "validate"
    assert linha["State"] == "Validate"


def test_delta_mede_a_duracao(capsys):
    delta(task("Delta", validated={"a": 1, "b": -5, "c": 6}), Context())

    assert measured(capsys)["Service"] == "delta"


def test_persist_mede_a_duracao(capsys, aws):
    persist(
        task(
            "Persist",
            validated={"a": 1, "b": -5, "c": 6},
            delta={"value": 1, "sign": "positive"},
            result={"roots": [{"label": "x1", "value": 3.0}]},
        ),
        Context(),
    )

    assert "HandlerDuration" in metric_names(measured(capsys))


@pytest.mark.parametrize("state", ["RootX1", "RootX2", "RootDouble"])
def test_os_tres_estados_do_root_ficam_separados(capsys, state):
    # O mesmo handler atende os tres. Sem State na dimensao, a duracao dos dois
    # ramos do Parallel e a da raiz dupla viravam uma media so.
    root(task(state, validated={"a": 1, "b": -5, "c": 6}, label="x1"), Context())

    linha = measured(capsys)

    assert linha["State"] == state
    assert linha["Service"] == "root"


# ------------------------------------------------------- desfechos medidos


def test_o_sinal_do_discriminante_e_contado(capsys):
    delta(task("Delta", validated={"a": 1, "b": 2, "c": 1}), Context())

    linha = measured(capsys)

    assert linha["EquationsByDeltaSign"] == 1
    assert linha["Sign"] == "zero"


def test_a_recusa_e_contada_com_o_motivo(capsys):
    with pytest.raises(InvalidEquation):
        validate(task("Validate", equation={"a": 0, "b": 1, "c": 1}), Context())

    linha = measured(capsys)

    assert linha["ValidationRejected"] == 1
    assert linha["Reason"] == "a_is_zero"
    assert linha["level"] == "ERROR"


@pytest.mark.parametrize(
    "equation,motivo",
    [
        ({"a": 1, "b": 2}, "missing_coefficient"),
        ({"a": "1", "b": 2, "c": 3}, "coefficient_not_number"),
        ({"a": True, "b": 2, "c": 3}, "coefficient_not_number"),
        ({"a": 0, "b": 2, "c": 3}, "a_is_zero"),
    ],
)
def test_cada_motivo_de_recusa_tem_o_seu_codigo(capsys, equation, motivo):
    # O codigo e um conjunto fechado, e nao a frase do erro: a frase carrega o
    # nome do coeficiente e viraria uma serie temporal por mensagem malformada.
    with pytest.raises(InvalidEquation):
        validate(task("Validate", equation=equation), Context())

    assert measured(capsys)["Reason"] == motivo


def test_o_caos_e_contado_a_parte(capsys):
    # Falha pedida pela carga nao pode entrar na contagem de erro real, senao a
    # analise de confiabilidade mede a propria demonstracao.
    evento = task("Delta", validated={"a": 1, "b": -5, "c": 6})
    evento["meta"]["chaos"] = {"state": "Delta", "fails": 1}

    with pytest.raises(TransientFailure):
        delta(evento, Context())

    linha = measured(capsys)

    assert linha["ChaosInjected"] == 1
    assert linha["State"] == "Delta"
    assert linha["level"] == "WARN"


def test_a_duplicata_no_dynamodb_e_contada(capsys, aws):
    evento = task(
        "Persist",
        validated={"a": 1, "b": -5, "c": 6},
        delta={"value": 1, "sign": "positive"},
        result={"roots": [{"label": "x1", "value": 3.0}]},
    )

    persist(evento, Context())
    capsys.readouterr()

    persist(evento, Context())

    assert measured(capsys)["PersistDuplicate"] == 1


def test_a_latencia_ponta_a_ponta_sai_do_persist(capsys, aws):
    persist(
        task(
            "Persist",
            validated={"a": 1, "b": -5, "c": 6},
            delta={"value": 1, "sign": "positive"},
            result={"roots": [{"label": "x1", "value": 3.0}]},
        ),
        Context(),
    )

    assert "EndToEndLatency" in metric_names(measured(capsys))


def test_sem_submitted_at_nao_se_inventa_latencia(capsys, aws):
    # Uma latencia inventada entrando na media e pior do que uma amostra a menos.
    evento = task(
        "Persist",
        validated={"a": 1, "b": -5, "c": 6},
        delta={"value": 1, "sign": "positive"},
        result={"roots": [{"label": "x1", "value": 3.0}]},
    )
    del evento["meta"]["submitted_at"]

    persist(evento, Context())

    assert "EndToEndLatency" not in metric_names(measured(capsys))


def test_o_dispatcher_conta_iniciadas_e_deduplicadas(capsys, aws):
    body = json.dumps({"a": 1, "b": -5, "c": 6})
    record = {
        "messageId": "m-1",
        "body": body,
        "messageAttributes": {"BatchId": {"stringValue": "b-1"}},
        "attributes": {"ApproximateReceiveCount": "1"},
    }

    dispatcher({"Records": [record, dict(record, messageId="m-2")]}, Context())

    linha = measured(capsys, "dispatcher")

    assert linha["ExecutionsStarted"] == 1
    assert linha["ExecutionsDeduplicated"] == 1


def test_a_contagem_zero_continua_saindo(capsys, aws):
    # Serie com buraco nao distingue "nenhuma duplicata" de "ninguem mediu".
    dispatcher({"Records": []}, Context())

    assert measured(capsys)["ExecutionsDeduplicated"] == 0


def test_o_submit_conta_o_que_publicou(capsys, aws):
    submit(
        {
            "headers": {"x-api-key": runtime.LOCAL_API_KEY},
            "body": json.dumps({"quantity": 4}),
        },
        Context(),
    )

    linha = measured(capsys)

    assert linha["EquationsSubmitted"] == 4
    assert linha["Service"] == "submit"


def test_a_borda_nao_declara_dimensao_de_estado(capsys, aws):
    # submit e status nao rodam dentro da state machine.
    submit(
        {
            "headers": {"x-api-key": runtime.LOCAL_API_KEY},
            "body": json.dumps({"quantity": 1}),
        },
        Context(),
    )

    assert "State" not in dimensions(measured(capsys))


# ------------------------------------------------------------ cardinalidade


def test_nenhum_handler_usa_identificador_como_dimensao(capsys, aws):
    """A garantia do fim do mes, verificada nos sete de uma vez."""
    invocacoes = [
        lambda: validate(
            task("Validate", equation={"a": 1, "b": -5, "c": 6}), Context()
        ),
        lambda: delta(task("Delta", validated={"a": 1, "b": -5, "c": 6}), Context()),
        lambda: root(
            task("RootX1", validated={"a": 1, "b": -5, "c": 6}, label="x1"), Context()
        ),
        lambda: persist(
            task(
                "Persist",
                validated={"a": 1, "b": -5, "c": 6},
                delta={"value": 1, "sign": "positive"},
                result={"roots": [{"label": "x1", "value": 3.0}]},
            ),
            Context(),
        ),
        lambda: dispatcher({"Records": []}, Context()),
        lambda: submit(
            {
                "headers": {"x-api-key": runtime.LOCAL_API_KEY},
                "body": json.dumps({"quantity": 1}),
            },
            Context(),
        ),
    ]

    for invocar in invocacoes:
        invocar()

        for linha in lines(capsys):
            if "_aws" not in linha:
                continue

            assert not dimensions(linha) & FORBIDDEN, (
                "o handler {} usou identificador como dimensao".format(
                    linha.get("service")
                )
            )


def test_a_correlacao_continua_na_linha_medida(capsys):
    """A metrica nao pode custar a rastreabilidade.

    O id nao vira dimensao — mas continua na linha, como campo. E o que permite
    sair de um pico no grafico para as execucoes que o produziram.
    """
    validate(task("Validate", equation={"a": 1, "b": -5, "c": 6}), Context())

    linha = measured(capsys)

    assert linha["execution"] == "k-abc"
    assert linha["batch_id"] == "b-1"
    assert "execution" not in dimensions(linha)


def test_as_metricas_de_plataforma_nao_tem_dimensao(capsys):
    """Cada dimensao e uma serie cobrada por mes; algumas nao pagam o custo.

    ColdStart e RetryAttempt com dimensao de servico seriam 7 e 6 series em vez
    de 1 e 1, para responder o que o `initDurationMs` do platform.report e os
    campos desta mesma linha ja respondem no Logs Insights.
    """
    evento = task("Delta", validated={"a": 1, "b": -5, "c": 6})
    evento["meta"]["chaos"] = {"state": "Delta", "fails": 1}

    with pytest.raises(TransientFailure):
        delta(evento, Context())

    linha = measured(capsys)
    grupos = {
        m["Name"]: tuple(g["Dimensions"][0])
        for g in linha["_aws"]["CloudWatchMetrics"]
        for m in g["Metrics"]
    }

    assert grupos["ChaosInjected"] == ()
    assert grupos["HandlerDuration"] == ("Service", "State")


def test_cold_start_e_retry_nao_criam_serie_por_funcao(capsys):
    # A suite roda em um processo so: sem o reset, o "frio" ja foi gasto por
    # algum teste anterior e a metrica nem apareceria nesta linha.
    observability.reset()

    evento = task("Delta", validated={"a": 1, "b": -5, "c": 6}, retry_count=2)

    delta(evento, Context())

    linha = measured(capsys)
    grupos = {
        m["Name"]: tuple(g["Dimensions"][0])
        for g in linha["_aws"]["CloudWatchMetrics"]
        for m in g["Metrics"]
    }

    assert grupos["ColdStart"] == ()
    assert grupos["RetryAttempt"] == ()
