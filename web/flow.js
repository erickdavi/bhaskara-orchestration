/* Desenho do fluxo: traduz a resposta de GET /flow em contadores e estados
   visuais dos nos do diagrama.
 *
 * O mapa abaixo e contrato de interface com a state machine: a chave e o nome
 * do estado na ASL. Renomear um estado no YAML sem mexer aqui apagaria um no
 * do diagrama em silencio — por isso tests/test_asl_definition.py compara os
 * dois lados e falha quando eles divergem. */

window.FLOW_NODES = [
  "Validate",
  "Delta",
  "RootsInParallel",
  "RootDouble",
  "NoRealRoots",
  "Persist",
  "DeadLetter",
];

(function () {
  "use strict";

  var previous = {};

  function node(name) {
    return document.querySelector('[data-node="' + name + '"]');
  }

  function counter(name) {
    return document.querySelector('[data-count="' + name + '"]');
  }

  function setCount(name, value) {
    var target = counter(name);

    if (target) {
      target.textContent = value;
    }
  }

  /* Um no fica "aceso" enquanto o contador subir entre dois polls. E o mais
     honesto que da para fazer com poll: nao ha evento em tempo real, entao
     atividade e "mudou desde a ultima leitura". */
  function paint(name, stats) {
    var element = node(name);

    if (!element) {
      return;
    }

    var entered = stats ? stats.entered : 0;
    var failed = stats ? stats.failed : 0;

    setCount(name, entered);

    element.classList.toggle("active", entered > (previous[name] || 0));
    element.classList.toggle("failing", failed > 0);

    previous[name] = entered;
  }

  window.renderFlow = function (payload) {
    var states = (payload.flow && payload.flow.states) || {};

    window.FLOW_NODES.forEach(function (name) {
      paint(name, states[name]);
    });

    var queues = payload.queues || {};

    setCount("queue-orders", (queues.orders || {}).visible || 0);
    setCount("queue-dead-letter", (queues.dead_letter || {}).visible || 0);

    var dispatcher = node("dispatcher");
    var pendentes = (queues.orders || {}).visible || 0;

    setCount("dispatcher", pendentes ? "entregando" : "ocioso");

    if (dispatcher) {
      dispatcher.classList.toggle("active", pendentes > 0);
    }

    var deadLetter = node("DeadLetter");

    if (deadLetter) {
      deadLetter.classList.toggle("failing", ((queues.dead_letter || {}).visible || 0) > 0);
    }
  };

  window.resetFlow = function () {
    previous = {};
  };
})();
