/* Painel: dispara a carga, faz poll de GET /flow e desenha o resultado.
 *
 * A chave de API nunca vem do bundle. Na nuvem, config.js e gerado pelo
 * Terraform e leva apenas a URL da API — a pagina e publica no CloudFront, e
 * uma chave embutida seria uma chave publicada. O operador digita a chave, que
 * fica so no localStorage deste browser. Rodando local (./run.sh web), o
 * servidor devolve tambem a chave, porque "publico" ali e a propria maquina. */

(function () {
  "use strict";

  var CONFIG = window.BHASKARA_CONFIG || {};
  var STORAGE = "bhaskara-orchestration";
  var POLL_MS = 2000;
  var WINDOW_MS = 120000;

  var state = {
    apiBase: "",
    apiKey: "",
    batchId: null,
    since: Date.now() - WINDOW_MS,
    selected: null,
    timer: null,
  };

  var el = {};

  ["banner", "mode", "config-card", "api-base", "api-key", "save-config", "fire", "batch",
   "quantity", "invalid", "duplicates", "chaos", "kpi-queued", "kpi-running", "kpi-succeeded",
   "kpi-failed", "kpi-retried", "executions", "timeline", "timeline-title", "results",
   "rejections"].forEach(function (id) {
    el[id] = document.getElementById(id);
  });

  /* ------------------------------------------------------------ config */

  function loadConfig() {
    var saved = {};

    try {
      saved = JSON.parse(localStorage.getItem(STORAGE) || "{}");
    } catch (error) {
      saved = {};
    }

    state.apiBase = CONFIG.apiBase !== undefined ? CONFIG.apiBase : (saved.apiBase || "");
    state.apiKey = CONFIG.apiKey || saved.apiKey || "";

    el["api-base"].value = state.apiBase;
    el["api-key"].value = state.apiKey;

    if (CONFIG.local) {
      el["config-card"].hidden = true;
      el.mode.textContent = "local · em memoria";
    } else {
      el.mode.textContent = state.apiKey ? "AWS · configurado" : "AWS · falta a chave";
    }
  }

  function saveConfig() {
    state.apiBase = el["api-base"].value.trim().replace(/\/$/, "");
    state.apiKey = el["api-key"].value.trim();

    try {
      localStorage.setItem(STORAGE, JSON.stringify({ apiBase: state.apiBase, apiKey: state.apiKey }));
    } catch (error) {
      /* Navegador com armazenamento bloqueado: a sessao atual continua
         funcionando, so nao lembra na proxima. */
    }

    el.mode.textContent = state.apiKey ? "AWS · configurado" : "AWS · falta a chave";
    notify("Configuracao salva neste browser.", true);
  }

  /* --------------------------------------------------------------- rede */

  function request(path, options) {
    var settings = options || {};
    var headers = { "x-api-key": state.apiKey };

    if (settings.body) {
      headers["Content-Type"] = "application/json";
    }

    return fetch(state.apiBase + path, {
      method: settings.method || "GET",
      headers: headers,
      body: settings.body,
    }).then(function (response) {
      return response.json().then(function (payload) {
        if (!response.ok) {
          throw new Error(payload.error || "HTTP " + response.status);
        }

        return payload;
      });
    });
  }

  function fire() {
    if (!state.apiKey) {
      return notify("Cole a chave de API antes de disparar a carga.");
    }

    el.fire.disabled = true;

    var body = JSON.stringify({
      quantity: Number(el.quantity.value) || 1,
      invalid_ratio: percent(el.invalid),
      duplicate_ratio: percent(el.duplicates),
      chaos_ratio: percent(el.chaos),
    });

    /* A janela de eventos comeca um segundo antes do disparo: assim o primeiro
       poll ja enxerga as execucoes que sairam na frente. */
    state.since = Date.now() - 1000;
    state.selected = null;

    window.resetFlow();

    request("/orders", { method: "POST", body: body })
      .then(function (payload) {
        state.batchId = payload.batch_id;
        el.batch.textContent = payload.published + " publicadas · carga " + payload.batch_id;
        notify("Carga aceita: " + payload.published + " mensagens na fila orders.", true);
        poll();
      })
      .catch(function (error) {
        notify("Nao consegui disparar a carga: " + error.message);
      })
      .then(function () {
        el.fire.disabled = false;
      });
  }

  function percent(input) {
    return Math.min(100, Math.max(0, Number(input.value) || 0)) / 100;
  }

  function poll() {
    var query = "/flow?since=" + state.since + (state.batchId ? "&batch_id=" + encodeURIComponent(state.batchId) : "");

    request(query)
      .then(render)
      .catch(function (error) {
        notify("Falha ao consultar o fluxo: " + error.message);
      });
  }

  /* ------------------------------------------------------------ desenho */

  function render(payload) {
    window.renderFlow(payload);

    var flow = payload.flow || {};
    var counts = flow.counts || {};
    var executions = flow.executions || [];

    text(el["kpi-queued"], (payload.queues.orders || {}).visible || 0);
    text(el["kpi-running"], counts.RUNNING || 0);
    text(el["kpi-succeeded"], counts.SUCCEEDED || 0);
    text(el["kpi-failed"], counts.FAILED || 0);
    text(el["kpi-retried"], retries(flow.states));

    renderExecutions(executions);
    renderTimeline(executions);
    renderResults(payload.results || []);
    renderRejections(payload.dead_letter || []);
  }

  function retries(states) {
    return Object.keys(states || {}).reduce(function (total, name) {
      return total + (states[name].failed || 0);
    }, 0);
  }

  function renderExecutions(executions) {
    if (!executions.length) {
      return empty(el.executions, "sem execucoes na janela");
    }

    el.executions.innerHTML = "";

    executions.forEach(function (execution) {
      var item = document.createElement("li");

      if (execution.arn === state.selected) {
        item.className = "selected";
      }

      item.innerHTML =
        '<span class="status ' + execution.status + '"></span>' +
        '<span class="name"></span>' +
        '<span class="steps"></span>';

      item.querySelector(".name").textContent = execution.name;
      item.querySelector(".steps").textContent = execution.steps.length + " passos";

      item.addEventListener("click", function () {
        state.selected = execution.arn;
        renderExecutions(executions);
        renderTimeline(executions);
      });

      el.executions.appendChild(item);
    });
  }

  function renderTimeline(executions) {
    var execution = executions.filter(function (candidate) {
      return candidate.arn === state.selected;
    })[0] || executions[0];

    if (!execution) {
      el["timeline-title"].textContent = "nenhuma execucao selecionada";
      return empty(el.timeline, "escolha uma execucao ao lado");
    }

    state.selected = execution.arn;
    el["timeline-title"].textContent = execution.name + " · " + execution.status.toLowerCase();

    el.timeline.innerHTML = "";

    execution.steps.forEach(function (step) {
      var item = document.createElement("li");

      item.className = step.outcome;
      item.innerHTML =
        '<span class="dot">' + (step.outcome === "failed" ? "×" : step.outcome === "ok" ? "✓" : "•") + "</span>" +
        '<span class="state"></span><span class="detail"></span><span class="ms"></span>';

      item.querySelector(".state").textContent = step.state;
      item.querySelector(".detail").textContent = step.error ? step.error + " · " + (step.detail || "") : (step.detail || "");
      item.querySelector(".ms").textContent = step.ms !== undefined ? step.ms + " ms" : "";

      el.timeline.appendChild(item);
    });
  }

  function renderResults(results) {
    if (!results.length) {
      return empty(el.results, "nenhum resultado nesta carga");
    }

    el.results.innerHTML = "";

    results.forEach(function (result) {
      var item = document.createElement("li");

      item.innerHTML = '<span class="eq"></span>' +
        (result.roots.length ? '<span class="roots"></span>' : '<span class="none">sem raizes reais</span>');

      item.querySelector(".eq").textContent = equation(result);

      if (result.roots.length) {
        item.querySelector(".roots").textContent = "x1=" + result.roots[0] + "  x2=" + result.roots[1];
      }

      el.results.appendChild(item);
    });
  }

  function equation(result) {
    return result.a + "x² " + sign(result.b) + " " + Math.abs(result.b) + "x " + sign(result.c) + " " + Math.abs(result.c) + " = 0";
  }

  function sign(value) {
    return value < 0 ? "-" : "+";
  }

  function renderRejections(rejections) {
    if (!rejections.length) {
      return empty(el.rejections, "nada recusado");
    }

    el.rejections.innerHTML = "";

    rejections.forEach(function (rejection) {
      var item = document.createElement("li");

      item.innerHTML = '<span class="what"></span><span class="why"></span>';
      item.querySelector(".what").textContent =
        typeof rejection.equation === "string" ? rejection.equation : JSON.stringify(rejection.equation);
      item.querySelector(".why").textContent = rejection.error || rejection.source;

      el.rejections.appendChild(item);
    });
  }

  function empty(list, message) {
    list.innerHTML = '<li class="empty"></li>';
    list.firstChild.textContent = message;
  }

  function text(element, value) {
    element.textContent = value;
  }

  function notify(message, good) {
    el.banner.hidden = false;
    el.banner.textContent = message;
    el.banner.className = good ? "banner ok" : "banner";
  }

  /* ---------------------------------------------------------- inicializa */

  el["save-config"].addEventListener("click", saveConfig);
  el.fire.addEventListener("click", fire);

  loadConfig();

  if (state.apiKey || CONFIG.local) {
    poll();
  }

  state.timer = setInterval(function () {
    if (state.apiKey || CONFIG.local) {
      poll();
    }
  }, POLL_MS);
})();
