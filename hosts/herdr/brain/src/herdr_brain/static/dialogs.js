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

  /* Multiselect (kind: "multiselect"): the option rows are checkbox
   * TOGGLES held in local state — no option tap ever posts — and one
   * explicit submit button sends the whole selection at once. */
  function isMultiselect(structured) {
    return !!(
      structured && !structured.unread && structured.kind === "multiselect"
    );
  }

  /* The local selection state, seeded from the terminal's own checked
   * glyphs. Rows without a checkbox (the submit row, checked == null)
   * are never selectable. Owns no payload or network surface of its
   * own: only choicesPayload below turns a selection into a POST. */
  function selectionModel(options) {
    var state = (options || []).map(function (option) {
      return option && option.checked === true;
    });
    return {
      toggle: function (index) {
        if (index >= 0 && index < state.length &&
            options[index] && options[index].checked != null) {
          state[index] = !state[index];
        }
      },
      isSelected: function (index) {
        return state[index] === true;
      },
      choices: function () {
        var out = [];
        for (var i = 0; i < state.length; i++) {
          if (state[i] && options[i] && options[i].checked != null) out.push(i);
        }
        return out;
      },
    };
  }

  /* The exact body a multiselect submit sends — the whole selection,
   * one explicit POST. */
  function choicesPayload(paneId, fingerprint, choices) {
    return { pane_id: paneId, fingerprint: fingerprint, choices: choices };
  }

  /* The explicit submit button's own chrome (agent labels ride
   * verbatim above it; only this button is ours). */
  function submitLabel() {
    return tr("dialogs.submit_selection", "Enviar selección");
  }

  /* Free-text options (S3): the row the server marked free_text does
   * not post on tap — the tap OPENS the editor in the card. These are
   * the editor's own chrome and payload; app.js owns the DOM, the
   * destructive guard and the fetch. */
  function isFreeTextOption(structured, index) {
    var option = structured && !structured.unread
      ? (structured.options || [])[index] : null;
    return !!option && option.free_text === true;
  }

  function freeTextPayload(paneId, fingerprint, option, text) {
    return {
      pane_id: paneId,
      fingerprint: fingerprint,
      option: option,
      text: text,
    };
  }

  function editorPlaceholder() {
    return tr("dialogs.free_text_placeholder", "Escribe tu respuesta…");
  }

  function editorSendLabel() {
    return tr("dialogs.free_text_send", "Enviar respuesta");
  }

  /* The second-tap confirm for destructive text: the SAME wording and
   * mechanism the composer uses (doc 03) — the guard is client-side,
   * the server never censors a confirmed answer. Null when the text
   * matched no destructive rule. */
  function confirmFreeText(text, match) {
    if (!match) return null;
    var line = tr(
      "reply.destructive_confirm",
      "Este envío parece destructivo: {match}.\n\n{text}\n\n¿Enviar igualmente?"
    );
    return line.replace("{match}", match).replace("{text}", text);
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
    isMultiselect: isMultiselect,
    selectionModel: selectionModel,
    choicesPayload: choicesPayload,
    submitLabel: submitLabel,
    isFreeTextOption: isFreeTextOption,
    freeTextPayload: freeTextPayload,
    editorPlaceholder: editorPlaceholder,
    editorSendLabel: editorSendLabel,
    confirmFreeText: confirmFreeText,
  };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    global.Dialogs = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
