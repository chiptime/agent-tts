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

  /* Wizard steps (S4): the server parses the screen's ``N/M`` marker
   * into `wizard: {step, steps}`. Only that metadata makes a dialog a
   * wizard — anything else (no key, malformed numbers, a current step
   * beyond the total) renders as the plain dialog it always was. */
  function wizardOf(structured) {
    var wizard = structured && !structured.unread ? structured.wizard : null;
    if (!wizard) return null;
    var step = Number(wizard.step);
    var steps = Number(wizard.steps);
    if (!isFinite(step) || !isFinite(steps) ||
        Math.floor(step) !== step || Math.floor(steps) !== steps ||
        step < 1 || steps < 1 || step > steps) return null;
    return { step: step, steps: steps };
  }

  /* The stepper's state machine: PWA-side orchestration over the
   * EXISTING guarded answers. Each step is captured locally with the
   * same option/choices/text model; Next posts ONE step at a time as
   * the exact payloads the endpoint already takes ({option} /
   * {choices} / {option, text}); the LAST step stops at a review whose
   * Confirm sends it and whose Edit returns to a capture WITHOUT
   * sending. Back rewinds only the current capture's local selections
   * — a submitted step can never be un-sent (Back then says why). The
   * server's drift refusal is the safety net: a 409 response, a dialog
   * that vanishes mid-wizard, or a next step that is not the expected
   * one ABORTS the machine, which never retries on its own.
   *
   * `send` is the transport (app.js owns the fetch): it takes a ready
   * payload and resolves {ok, status, body}. */
  function wizardSession(send) {
    var mode = "idle";      // idle | capturing | waiting | reviewing | aborted
    var paneId = "";
    var steps = 0;
    var current = 0;        // the step being captured (or re-captured)
    var expected = 0;       // the step the screen must show after a send
    var finishing = false;  // waiting for the wizard to END, not advance
    var error = "";
    var answers = [];       // one record per captured step, in step order
    var capture = null;     // {structured, fingerprint, pick, selection, freeText}

    function reset() {
      mode = "idle";
      steps = 0; current = 0; expected = 0;
      finishing = false; error = "";
      answers = []; capture = null;
    }

    function abort(reason) {
      mode = "aborted";
      error = reason;
      capture = null;
    }

    function seedCapture(structured) {
      capture = {
        structured: structured,
        fingerprint: structured.fingerprint,
        pick: null,
        selection: null,
        freeText: null,
      };
      if (structured.kind === "multiselect") {
        capture.selection = selectionModel(structured.options);
      }
      var options = structured.options || [];
      for (var i = 0; i < options.length; i++) {
        if (options[i] && options[i].free_text === true) {
          capture.freeText = { option: i, value: "" };
          break;
        }
      }
    }

    function recordFor(step) {
      for (var i = 0; i < answers.length; i++) {
        if (answers[i].step === step) return answers[i];
      }
      return null;
    }

    /* A capture becomes the step's record — the display facts (labels,
     * text) plus what a re-capture (Edit) needs to restore. */
    function upsertRecord(submitted) {
      var existing = recordFor(current);
      var record = existing || { step: current };
      record.question = capture.structured.question;
      record.structured = capture.structured;
      record.pick = capture.pick;
      record.choices = capture.selection ? capture.selection.choices() : null;
      record.text = capture.freeText ? capture.freeText.value : "";
      record.submitted = submitted;
      if (!existing) {
        answers.push(record);
        answers.sort(function (a, b) { return a.step - b.step; });
      }
    }

    function capturePayload() {
      if (!capture) return null;
      if (capture.selection) {
        var chosen = capture.selection.choices();
        return chosen.length
          ? choicesPayload(paneId, capture.fingerprint, chosen) : null;
      }
      if (capture.pick != null) {
        return answerPayload(paneId, capture.fingerprint, capture.pick);
      }
      if (capture.freeText && capture.freeText.value) {
        return freeTextPayload(
          paneId, capture.fingerprint, capture.freeText.option,
          capture.freeText.value);
      }
      return null;
    }

    function labelsOfRecord(record) {
      var options = (record.structured && record.structured.options) || [];
      if (record.choices) {
        return record.choices
          .map(function (index) { return options[index] && options[index].label; })
          .filter(function (label) { return label != null; });
      }
      if (record.pick != null && options[record.pick]) {
        return [options[record.pick].label];
      }
      return [];
    }

    function post(payload, onOk) {
      return Promise.resolve(send(payload)).then(
        function (result) {
          if (result && result.ok) {
            onOk();
            return { ok: true };
          }
          abort(result && result.status === 409 ? "changed" : "fail");
          return { ok: false, reason: error };
        },
        function () {
          abort("fail");
          return { ok: false, reason: "fail" };
        }
      );
    }

    function feed(structured, pane) {
      pane = pane || "";
      if (paneId && pane && pane !== paneId) reset();
      if (pane) paneId = pane;
      var meta = wizardOf(structured);

      if (mode === "aborted") {
        /* Only a fresh step-1 wizard on screen restarts the stepper;
         * the screen that caused the abort never does. */
        if (meta && meta.step === 1) reset();
        else return;
      }

      if (mode === "idle") {
        if (meta) {
          steps = meta.steps;
          current = meta.step;
          expected = meta.step;
          seedCapture(structured);
          mode = "capturing";
        }
        return;
      }

      if (mode === "waiting") {
        if (!meta) {
          if (finishing) reset();  // the dialog left: the wizard is done
          else abort("changed");   // it vanished between steps instead
          return;
        }
        if (finishing && meta.step === 1) {
          /* A NEW wizard call began the moment the last one ended. */
          reset();
          feed(structured, pane);
          return;
        }
        if (meta.step === expected && meta.steps === steps) {
          current = expected;
          seedCapture(structured);
          mode = "capturing";
          return;
        }
        if (meta.step === current && capture &&
            structured.fingerprint === capture.fingerprint) {
          return;  // the pane has not redrawn the next step yet
        }
        abort("changed");  // not the step this wizard is on: drift
        return;
      }

      /* capturing / reviewing: the current step's dialog must stay on
       * screen while the user decides. A re-render of the SAME step
       * refreshes the capture (pointer moves change the fingerprint),
       * so a Next always posts against the screen being looked at. */
      if (!meta) {
        abort("changed");
        return;
      }
      if (meta.step === current && meta.steps === steps) {
        if (capture) {
          capture.structured = structured;
          capture.fingerprint = structured.fingerprint;
        }
        return;
      }
      abort("changed");
    }

    function captureDirty() {
      if (!capture) return false;
      if (capture.selection) {
        var seed = selectionModel(capture.structured.options);
        return JSON.stringify(capture.selection.choices()) !==
          JSON.stringify(seed.choices());
      }
      if (capture.freeText && capture.freeText.value) return true;
      return capture.pick != null;
    }

    function anySubmittedBefore() {
      for (var i = 0; i < answers.length; i++) {
        if (answers[i].submitted && answers[i].step < current) return true;
      }
      return false;
    }

    return {
      feed: feed,

      /* Local capture — the same answering shapes, held locally. */
      pick: function (index) {
        if (mode !== "capturing" || !capture || capture.selection) return false;
        var option = (capture.structured.options || [])[index];
        if (!option) return false;
        capture.pick = index;
        return true;
      },
      toggle: function (index) {
        if (mode !== "capturing" || !capture || !capture.selection) return false;
        capture.selection.toggle(index);
        return true;
      },
      setText: function (value) {
        if (mode !== "capturing" || !capture || !capture.freeText) return false;
        capture.freeText.value = String(value || "").trim();
        return true;
      },
      pickedIndex: function () {
        return capture ? capture.pick : null;
      },
      isSelected: function (index) {
        return !!(capture && capture.selection &&
          capture.selection.isSelected(index));
      },
      freeTextOption: function () {
        return capture && capture.freeText ? capture.freeText.option : null;
      },
      freeTextValue: function () {
        return capture && capture.freeText ? capture.freeText.value : "";
      },

      /* Next: a non-last step posts now and waits for the next step's
       * dialog; the LAST step is only recorded — the review owns its
       * send. Re-capturing an already-submitted step (Edit) posts
       * honestly again: the server's fingerprint guard is the arbiter,
       * never the client's optimism. */
      next: function () {
        if (mode !== "capturing" || !capture) {
          return Promise.resolve({ ok: false, reason: "mode" });
        }
        var payload = capturePayload();
        if (!payload) return Promise.resolve({ ok: false, reason: "empty" });
        if (current === steps) {
          upsertRecord(false);
          mode = "reviewing";
          return Promise.resolve({ ok: true });
        }
        return post(payload, function () {
          upsertRecord(true);
          expected = current + 1;
          mode = "waiting";
        });
      },

      /* Confirm sends ONLY the pending last step. */
      confirm: function () {
        if (mode !== "reviewing" || !capture) {
          return Promise.resolve({ ok: false, reason: "mode" });
        }
        var payload = capturePayload();
        if (!payload) return Promise.resolve({ ok: false, reason: "empty" });
        return post(payload, function () {
          upsertRecord(true);
          finishing = true;
          expected = current + 1;
          mode = "waiting";
        });
      },

      /* Back rewinds LOCAL selections of the current capture only; it
       * can never un-send a step. When there is nothing local to
       * rewind it explains why (a sent step / the first step). */
      back: function () {
        if (mode !== "capturing" || !capture) {
          return { ok: false, reason: "review" };
        }
        if (!captureDirty()) {
          return { ok: false, reason: anySubmittedBefore() ? "sent" : "first" };
        }
        seedCapture(capture.structured);
        return { ok: true };
      },

      /* Edit returns to a step's capture WITHOUT sending anything. */
      edit: function (step) {
        if (mode !== "reviewing") return false;
        var record = recordFor(Number(step));
        if (!record) return false;
        current = record.step;
        seedCapture(record.structured);
        if (capture.selection && record.choices) {
          for (var i = 0; i < (record.structured.options || []).length; i++) {
            var want = record.choices.indexOf(i) !== -1;
            if (want !== capture.selection.isSelected(i)) {
              capture.selection.toggle(i);
            }
          }
        }
        if (record.pick != null) capture.pick = record.pick;
        if (record.text && capture.freeText) capture.freeText.value = record.text;
        mode = "capturing";
        return true;
      },

      /* The render snapshot: everything the stepper paints, no DOM. */
      view: function () {
        return {
          mode: mode,
          paneId: paneId,
          steps: steps,
          current: current,
          waiting: mode === "waiting",
          progress: mode === "idle" || mode === "aborted"
            ? null : { step: current, steps: steps },
          structured: capture ? capture.structured : null,
          canBack: mode === "capturing" && captureDirty(),
          backReason: mode === "capturing" && !captureDirty()
            ? (anySubmittedBefore() ? "sent" : "first") : "",
          answers: answers.map(function (record) {
            return {
              step: record.step,
              question: record.question,
              labels: labelsOfRecord(record),
              text: record.text || "",
              submitted: record.submitted,
            };
          }),
          error: error,
        };
      },
    };
  }

  /* The stepper's own chrome (agent text rides verbatim above it). */
  function wizardProgressLabel(step, steps) {
    return tr("dialogs.wizard_progress", "Paso {step} de {steps}")
      .replace("{step}", String(step)).replace("{steps}", String(steps));
  }

  function wizardNextLabel() {
    return tr("dialogs.wizard_next", "Siguiente");
  }

  function wizardBackLabel() {
    return tr("dialogs.wizard_back", "Atrás");
  }

  function wizardBackDisabledReason(reason) {
    if (reason === "sent") {
      return tr(
        "dialogs.wizard_back_sent",
        "Los pasos ya enviados no se pueden deshacer; Atrás solo limpia " +
        "lo marcado en el paso actual."
      );
    }
    return tr(
      "dialogs.wizard_back_empty",
      "No hay nada marcado en este paso."
    );
  }

  function wizardReviewTitle() {
    return tr("dialogs.wizard_review_title", "Revisa tus respuestas");
  }

  function wizardConfirmLabel() {
    return tr("dialogs.wizard_confirm", "Confirmar y enviar");
  }

  function wizardEditLabel() {
    return tr("dialogs.wizard_edit", "Editar");
  }

  /* The abort card: drift the server refused (409) or the stepper
   * itself saw (an unexpected step). Nothing further was sent. */
  function wizardChangedMessage() {
    return tr(
      "dialogs.wizard_changed",
      "El asistente cambió antes de terminar (la pantalla o los pasos " +
      "difieren de lo revisado). No se envió nada más; mira la pantalla y " +
      "responde al diálogo actual, o usa el teclado."
    );
  }

  /* Model pickers (parity-f7d S2): a `kind: "model_picker"` payload
   * renders through the SAME option buttons every select uses — the
   * recipes stay server-side, so the card needs no new machinery, only
   * its own chrome. */
  function isModelPicker(structured) {
    return !!(
      structured && !structured.unread && structured.kind === "model_picker"
    );
  }

  function modelPickerTitle() {
    return tr("dialogs.model_picker_title", "Cambiar modelo");
  }

  /* The picker's rows, in the shape the search card paints: the clean
   * MODEL name (primary), the provider (secondary text; a harness that
   * sends only `description` keeps it as the secondary line) and the
   * `current` mark. `index` is the ORIGINAL position in the payload —
   * the only thing a tap ever sends — so filtering can never shift
   * what an answer means. Null for anything that is not a model
   * picker. */
  function modelPickerRows(structured) {
    if (!isModelPicker(structured)) return null;
    return (structured.options || []).map(function (option, index) {
      var model = option.model != null && option.model !== ""
        ? option.model : option.label;
      var provider = option.provider != null && option.provider !== ""
        ? option.provider : option.description;
      return {
        index: index,
        model: String(model == null ? "" : model),
        provider: String(provider == null ? "" : provider),
        current: option.current === true,
      };
    });
  }

  /* Case- and accent-insensitive folding: "ÑANDÚ" matches "nandu". */
  function foldSearchText(text) {
    var value = String(text == null ? "" : text);
    if (value.normalize) value = value.normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    return value.toLowerCase();
  }

  /* Autocomplete over the rows: every whitespace-separated token of the
   * query must be a substring of the model name or of the provider (the
   * secondary text). A blank query keeps every row, in order. Pure — no
   * DOM, no network. */
  function filterModelRows(rows, query) {
    var list = rows || [];
    var tokens = foldSearchText(query).split(/\s+/).filter(Boolean);
    if (!tokens.length) return list.slice();
    return list.filter(function (row) {
      var haystack = foldSearchText(row.model) + " " + foldSearchText(row.provider);
      return tokens.every(function (token) {
        return haystack.indexOf(token) !== -1;
      });
    });
  }

  /* ── the FULL catalog (GET /pane/models, opencode only) ────────────
   * The picker on screen paints a window of its list (recents,
   * favourites, a few more), so a card built from it can never offer
   * every model. The server lists the operator's connected models and
   * a tap answers by NAME: the server types it into the real picker's
   * Search, walks the screen's pointer to that model and presses Enter
   * only once a fresh read shows the pointer on it. */
  var CATALOG_HARNESSES = { opencode: true, opencode2: true, antigravity: true };

  function wantsModelCatalog(structured) {
    return isModelPicker(structured) &&
      CATALOG_HARNESSES[String(structured.agent || "").toLowerCase()] === true;
  }

  function modelCatalogUrl(paneId) {
    return "/pane/models?pane_id=" + encodeURIComponent(String(paneId));
  }

  /* Catalog entries → the rows the search card paints (the SAME shape
   * as modelPickerRows, so filterModelRows serves both). `index` is the
   * entry's position in the catalog. Malformed entries are skipped,
   * never thrown on. When the server knew no current model, the row the
   * SCREEN's picker marks current (name + provider) is marked instead —
   * only when exactly one row matches. */
  function modelCatalogRows(models, structured) {
    var rows = [];
    (Array.isArray(models) ? models : []).forEach(function (entry) {
      if (!entry || typeof entry !== "object") return;
      var name = typeof entry.name === "string" ? entry.name.trim() : "";
      if (!name) return;
      var provider = typeof entry.provider_name === "string" && entry.provider_name.trim()
        ? entry.provider_name.trim()
        : (typeof entry.provider === "string" ? entry.provider : "");
      rows.push({
        index: rows.length,
        model: name,
        provider: provider,
        current: entry.current === true,
      });
    });
    if (!rows.some(function (row) { return row.current; }) && structured) {
      var onScreen = (structured.options || []).filter(function (option) {
        return option && option.current === true;
      })[0];
      if (onScreen) {
        var wantedModel = foldSearchText(onScreen.model || onScreen.label);
        var wantedProvider = foldSearchText(onScreen.provider || "");
        var hits = rows.filter(function (row) {
          return foldSearchText(row.model) === wantedModel &&
            (!wantedProvider || foldSearchText(row.provider) === wantedProvider);
        });
        if (hits.length === 1) hits[0].current = true;
      }
    }
    return rows;
  }

  /* The catalog tap's wire shape: a NAME (+ provider when known), never
   * an option index — the picker may not list the model at all. The
   * fingerprint guard is the same one every dialog answer carries. */
  function catalogAnswerPayload(paneId, fingerprint, row) {
    var payload = {
      pane_id: paneId,
      fingerprint: fingerprint,
      model_name: row.model,
    };
    if (row.provider) payload.provider_name = row.provider;
    return payload;
  }

  function modelSearchPlaceholder() {
    return tr("dialogs.model_search_placeholder", "Buscar modelo…");
  }

  function modelSearchNone() {
    return tr("dialogs.model_search_none", "Ningún modelo coincide.");
  }

  /* The "Cambiar modelo" affordance (S2): which command opens THIS
   * harness's picker. The server owns the verified table; this mirror
   * only decides whether the button is offered — the switch itself is
   * the guarded answer path. Null for a harness without one.
   * pi draws omp's Switch Model box (same TUI): /switch opens it
   * (PWA-only for now — the voice table verifies it later). opencode
   * is live-verified on 1.19.5: the composer command is /models. */
  var MODEL_SWITCH_COMMANDS = {
    claude: "/model",
    grok: "/model",
    omp: "/switch",
    pi: "/switch",
    opencode: "/models",
    opencode2: "/models",
    // POST-09 (agy 1.5.18, live-verified 2026-11-26): /model opens the
    // Switch Model picker; the catalog card answers by NAME and the
    // guarded walk commits it (the ←/→ Effort gauge stays out of scope).
    antigravity: "/model",
    agy: "/model",
  };

  function modelSwitchOffer(harness) {
    var command = MODEL_SWITCH_COMMANDS[String(harness || "").toLowerCase()];
    if (!command) return null;
    return { command: command, label: tr("dialogs.model_switch", "Cambiar modelo") };
  }

  /* The exact /pane/reply body the affordance sends: the command is
   * typed into the pane exactly like a user reply, and the picker card
   * appears when the pane blocks on its own. */
  function modelSwitchPayload(paneId, command) {
    return { pane_id: paneId, text: command };
  }

  function modelSwitchOpenedToast(agentName) {
    return tr("dialogs.model_switch_open", "Abriendo el selector de modelos…")
      .replace("{agent}", agentName || "");
  }

  function modelSwitchNoneToast() {
    return tr(
      "dialogs.model_switch_none",
      "Este agente no puede cambiar de modelo desde aquí."
    );
  }

  /* The modal /view read (F7d fix): the poll URL the model-switch
   * flow drives. `modal=1` asks the server to recognize the dialog
   * REGARDLESS of pane status — a USER-opened opencode picker leaves
   * the pane idle — reading the full window. */
  function modalViewUrl(paneId) {
    return "/view?pane_id=" + encodeURIComponent(paneId) + "&modal=1";
  }

  var MODEL_SWITCH_POLL_INTERVAL_MS = 700;
  var MODEL_SWITCH_POLL_TIMEOUT_MS = 10000;

  /* The model-switch poll, as a pure machine (F7d fix): after the
   * "Cambiar modelo" tap sends the harness's picker command, the PWA
   * polls /view?pane_id=…&modal=1 until a structured dialog appears.
   * Timers and the fetch are injected, so the three exits are exact:
   * the FIRST structured payload renders (onStructured) and stops the
   * poll; a budget of payloads with no dialog ends in onNone (the
   * none-toast) and stops; cancel() (the sheet closed) stops with no
   * callback at all. A failed fetch counts as no-dialog — a transport
   * hiccup must not eat the budget's purpose. Bounded: at most
   * ceil(timeout/interval) fetches, one timer alive at any time. */
  function createModelSwitchPoller(deps) {
    var opts = deps || {};
    var fetchView = opts.fetchView;
    var onStructured = opts.onStructured || function () {};
    var onNone = opts.onNone || function () {};
    var intervalMs = opts.intervalMs || MODEL_SWITCH_POLL_INTERVAL_MS;
    var timeoutMs = opts.timeoutMs || MODEL_SWITCH_POLL_TIMEOUT_MS;
    var setTimeoutFn = opts.setTimeoutFn || function (fn, ms) {
      return setTimeout(fn, ms);
    };
    var clearTimeoutFn = opts.clearTimeoutFn || clearTimeout;
    var timer = null;
    var pane = null;
    var ticksLeft = 0;

    function stopped() {
      return pane === null;
    }

    function schedule() {
      timer = setTimeoutFn(poll, intervalMs);
    }

    function poll() {
      timer = null;
      if (stopped()) return;
      fetchView(pane).then(function (payload) {
        settle(payload && payload.structured ? payload : null);
      }, function () {
        settle(null);
      });
    }

    function settle(structuredPayload) {
      if (stopped()) return;
      if (structuredPayload) {
        var done = pane;
        stop();
        onStructured(structuredPayload, done);
        return;
      }
      ticksLeft -= 1;
      if (ticksLeft <= 0) {
        stop();
        onNone();
        return;
      }
      schedule();
    }

    function stop() {
      if (timer !== null) clearTimeoutFn(timer);
      timer = null;
      pane = null;
      ticksLeft = 0;
    }

    return {
      start: function (paneId) {
        if (!paneId) return;
        stop();
        pane = paneId;
        ticksLeft = Math.max(1, Math.ceil(timeoutMs / intervalMs));
        schedule();
      },
      cancel: stop,
      active: function () {
        return pane !== null;
      },
    };
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
    wizardOf: wizardOf,
    wizardSession: wizardSession,
    wizardProgressLabel: wizardProgressLabel,
    wizardNextLabel: wizardNextLabel,
    wizardBackLabel: wizardBackLabel,
    wizardBackDisabledReason: wizardBackDisabledReason,
    wizardReviewTitle: wizardReviewTitle,
    wizardConfirmLabel: wizardConfirmLabel,
    wizardEditLabel: wizardEditLabel,
    wizardChangedMessage: wizardChangedMessage,
    isModelPicker: isModelPicker,
    modelPickerTitle: modelPickerTitle,
    modelPickerRows: modelPickerRows,
    filterModelRows: filterModelRows,
    modelSearchPlaceholder: modelSearchPlaceholder,
    modelSearchNone: modelSearchNone,
    wantsModelCatalog: wantsModelCatalog,
    modelCatalogUrl: modelCatalogUrl,
    modelCatalogRows: modelCatalogRows,
    catalogAnswerPayload: catalogAnswerPayload,
    modelSwitchOffer: modelSwitchOffer,
    modelSwitchPayload: modelSwitchPayload,
    modelSwitchOpenedToast: modelSwitchOpenedToast,
    modelSwitchNoneToast: modelSwitchNoneToast,
    modalViewUrl: modalViewUrl,
    createModelSwitchPoller: createModelSwitchPoller,
  };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    global.Dialogs = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
