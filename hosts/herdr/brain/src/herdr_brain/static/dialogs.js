/* Dialog button state — PURE logic, no DOM.
 *
 * The cockpit's dialog surface (doc 04): when the server RECOGNIZES the
 * blocked pane's dialog (/view's `structured` field), the PWA renders one
 * native button per option above the manual keypad; when it cannot read
 * the dialog, an explicit card says so and the keypad stays the way out.
 * This module holds everything decidable without the DOM: the button
 * model a structured payload maps to, the exact POST /pane/dialog/answer
 * body a tap sends, and the Spanish status line for every outcome.
 * app.js owns the fetching (withDeviceAuth), the DOM painting
 * (createElement/textContent only — a label is never markup) and the
 * busy state.
 *
   * Server contract (dialogs/model.py): `structured` is ABSENT (treated
   * as null) while no recognizer claimed the pane — working panes,
   * screens only the generic heuristic reads, blocked screens nobody
   * recognized (the keypad is the way out either way). A CLAIM comes as
   * `{unread: true, agent, detail}` when the recognizer positively
   * refuses to make its dialog tappable, and otherwise as `{kind,
   * question, options: [{label, description, checked}], fingerprint,
   * recipe}`. The RECIPES never travel: the tap names an option, and
   * the server re-reads the pane before any key leaves. */
(function (global) {
  "use strict";

  /* One button per option, labels and descriptions verbatim. The index
   * is the position the answer endpoint expects — never the option's
   * printed number, which the server alone maps onto keys. */
  function buttonsFor(structured) {
    if (!structured || structured.unread) return null;
    var options = structured.options || [];
    if (!options.length) return null;
    return options.map(function (option, index) {
      return {
        index: index,
        label: String(option.label == null ? "" : option.label),
        description: String(option.description == null ? "" : option.description),
        checked: option.checked === true,
      };
    });
  }

  /* i18n (S4): the page injects t() after boot; without it (node
   * suite, standalone) the built-in Spanish lines below are the
   * single source. Option labels/questions are AGENT text and ride
   * verbatim — only this module's own chrome translates. */
  var translator = null;

  function useTranslator(tFn) {
    translator = typeof tFn === "function" ? tFn : null;
  }

  function tr(key, fallback) {
    if (!translator) return fallback;
    var value = translator(key);
    return value && value !== key ? value : fallback;
  }

  /* The explicit "cannot read this dialog" card: honest about the state
   * and about the way out, which is always the manual keypad. */
  function unreadCard(agent) {
    return {
      title: tr("dialogs.unread_title", "No puedo leer este diálogo"),
      body: tr(
        "dialogs.unread_body",
        "El agente " + (agent || "") + " espera una respuesta que no sé leer con " +
        "seguridad. Usa el teclado de abajo, nunca se envía nada a ciegas."
      ).replace("{agent}", agent || ""),
    };
  }

  /* The exact body the endpoint expects — nothing else travels. */
  function answerPayload(paneId, fingerprint, option) {
    return { pane_id: paneId, fingerprint: fingerprint, option: option };
  }

  /* The Spanish status line for a tap's outcome. `labels` are the
   * rendered button labels, so the answered line can name the choice. */
  function statusFor(result, labels) {
    labels = labels || [];
    if (result && result.ok) {
      var label = labels[result.option] || "";
      if (!label) return tr("dialogs.answered_plain", "Respondido.");
      if (translator) {
        var line = translator("dialogs.answered");
        if (line && line !== "dialogs.answered") return line.replace("{label}", label);
      }
      return "Respondido: " + label;
    }
    if (result && result.status === 409) {
      return tr(
        "dialogs.changed",
        "El diálogo cambió antes de responder; nada se envió. " +
        "Mira la pantalla y responde al diálogo nuevo, o usa el teclado."
      );
    }
    if (result && result.status === 422) {
      return tr("dialogs.option_gone", "Esa opción ya no está en el diálogo; usa el teclado.");
    }
    return tr("dialogs.fail", "No se pudo responder el diálogo; usa el teclado.");
  }

  var api = {
    buttonsFor: buttonsFor,
    unreadCard: unreadCard,
    answerPayload: answerPayload,
    statusFor: statusFor,
    useTranslator: useTranslator,
  };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    global.Dialogs = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
