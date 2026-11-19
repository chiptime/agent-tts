/* Send tracking (parity-f7g S5) — PURE logic, no DOM.
 *
 * A receipt says what the terminal did with the text at send time;
 * the tracker follows the turn AFTER it. The server (send_tracking.py)
 * publishes every state change as one SSE event (`send.track`) and
 * stamps the tracker onto the receipt listing (GET /pane/receipts);
 * this module holds everything decidable without the DOM: the closed
 * state set and its Spanish labels, the m:ss elapsed formatting, the
 * card model (terminal detection, the stalled warning, the masked
 * error line), the per-pane tracker map (a new send replaces the
 * pane's card; a superseded event about an older receipt never
 * overwrites the newer one) and the hot-poll cadence (1.5 s for the
 * first ~12 s after a send, then the normal 5 s). app.js owns the
 * fetching (readFetch) and the DOM painting (text nodes only).
 *
 * The map never regresses (correction batch): a terminal state is
 * final and a start for a receipt the map already knows never
 * regresses it; every update carries the server's per-receipt `seq`
 * and a lower seq than the one held is a stale delivery, ignored; a
 * dismissed receipt id stays dismissed (bounded per-pane memory) —
 * only a NEW receipt id can show a card again. */
(function (global) {
  "use strict";

  /* The closed state vocabulary, mirroring send_tracking.py. */
  var NON_TERMINAL_STATES = ["sent", "started", "working"];
  var TERMINAL_STATES = [
    "done", "blocked", "error", "no_start", "pane_gone", "superseded", "expired",
  ];

  /* Receipt outcomes that start a tracker — the same tracked set the
   * server hooks in POST /pane/reply. typed_not_submitted/discarded/
   * refused wrote nothing; nothing is in flight for them. */
  var TRACKED_OUTCOMES = ["sent", "effective", "unknown"];

  /* The Spanish line of every state. "Enviado"/"Empezó"/"Trabajando"
   * carry a ticking "· m:ss" suffix while non-terminal (cardModel);
   * every terminal line is complete on its own. */
  var STATE_LABELS = {
    sent: "Enviado",
    started: "Empezó",
    working: "Trabajando",
    done: "Terminó",
    blocked: "Te espera en un diálogo",
    error: "Error",
    no_start: "No arrancó en 15 s — revisa el agente",
    pane_gone: "El pane se cerró",
    superseded: "Reemplazado por un envío nuevo",
    expired: "Sin noticias tras 30 min",
  };

  /* A state a newer server may introduce: honest generic copy (still
   * non-terminal, still ticks, still converges through polling) — a
   * bare key must never reach the card. */
  var UNKNOWN_STATE_LABEL = "En marcha";

  var STALLED_LINE = "Parece colgado (3 min sin actividad)";
  var ERROR_PREFIX = "Error";

  /* Hot poll (S5): refresh every 1.5 s for the first ~12 s after a
   * send, then return to the normal 5 s cadence. */
  var HOT_WINDOW_MS = 12000;
  var HOT_EVERY_MS = 1500;
  var NORMAL_EVERY_MS = 5000;

  /* i18n (same seam as receipts.js): the page injects the dictionary
   * runtime's t() after boot; without it the built-in Spanish tables
   * above stay the single source. A drifted or missing key falls back
   * to the built-in line, never shows a raw key. */
  var translator = null;

  function useTranslator(tFn) {
    translator = typeof tFn === "function" ? tFn : null;
  }

  function tr(key, fallback) {
    if (!translator) return fallback;
    var value = translator(key);
    return value && value !== key ? value : fallback;
  }

  function isTerminal(state) {
    return TERMINAL_STATES.indexOf(state) !== -1;
  }

  function isKnownState(state) {
    return Object.prototype.hasOwnProperty.call(STATE_LABELS, state);
  }

  function stateLabel(state) {
    var known = isKnownState(state) ? state : "__unknown__";
    return known === "__unknown__"
      ? tr("track.state_unknown", UNKNOWN_STATE_LABEL)
      : tr("track.state_" + known, STATE_LABELS[known]);
  }

  function stalledLabel() {
    return tr("track.stalled", STALLED_LINE);
  }

  /* m:ss, floor-clamped: the tracker's hard cap is 30 min, so minutes
   * never need an hour column; garbage never renders as "NaN:NaN". */
  function formatElapsed(seconds) {
    var total = Math.floor(Number(seconds));
    if (!isFinite(total) || total < 0) total = 0;
    var minutes = Math.floor(total / 60);
    var rest = total % 60;
    return minutes + ":" + (rest < 10 ? "0" + rest : rest);
  }

  /* The masked error line: "Error: <message>" (the server masks the
   * text before it leaves; the card renders it as a text node only). */
  function errorText(error) {
    var prefix = tr("track.state_error", ERROR_PREFIX);
    var detail = error && (error.message || error.name);
    return detail ? prefix + ": " + detail : prefix;
  }

  /* The card model: {state, terminal, stalled, stateText, elapsedText,
   * dismissible}. elapsedText ("· m:ss") rides every NON-terminal
   * state except while stalled — that line self-describes its 3 min —
   * and never rides a terminal one. */
  function cardModel(entry, nowSeconds) {
    if (!entry || !entry.state) return null;
    var state = entry.state;
    var terminal = isTerminal(state);
    var stalled = !!entry.stalled && !terminal;
    var stateText;
    if (stalled) {
      stateText = stalledLabel();
    } else if (state === "error") {
      stateText = errorText(entry.error);
    } else {
      stateText = stateLabel(state);
    }
    var elapsedText = "";
    if (!terminal && !stalled && typeof entry.sent_at === "number" &&
        isFinite(entry.sent_at)) {
      elapsedText = "· " + formatElapsed(Math.max(0, nowSeconds - entry.sent_at));
    }
    return {
      state: state,
      terminal: terminal,
      stalled: stalled,
      stateText: stateText,
      elapsedText: elapsedText,
      dismissible: terminal,
    };
  }

  /* The initial snapshot synthesized from a POST /pane/reply receipt
   * (the response carries no `track` — the first SSE event or receipts
   * poll converges it). `effective` is born terminal `done`, exactly
   * like the server; untracked outcomes return null. */
  function initialTrack(outcome, sentAtSeconds) {
    if (TRACKED_OUTCOMES.indexOf(outcome) === -1) return null;
    return {
      state: outcome === "effective" ? "done" : "sent",
      sent_at: Number(sentAtSeconds) || 0,
      stalled: false,
    };
  }

  function entryFromEvent(event, fallbackSentAt, nowSeconds) {
    var sentAt = typeof event.sent_at === "number"
      ? event.sent_at
      : (typeof fallbackSentAt === "number"
        ? fallbackSentAt
        : nowSeconds - (Number(event.elapsed_s) || 0));
    return {
      receipt_id: event.receipt_id,
      pane_id: event.pane_id,
      state: event.state,
      seq: typeof event.seq === "number" && isFinite(event.seq)
        ? event.seq
        : null,
      sent_at: sentAt,
      stalled: !!event.stalled,
      last_activity_at: typeof event.last_activity_at === "number"
        ? event.last_activity_at
        : null,
      error: event.state === "error" && event.error
        ? { name: String(event.error.name || ""), message: String(event.error.message || "") }
        : null,
    };
  }

  /* The per-pane map. One visible tracker per pane: the newest receipt
   * owns the card; a superseded event about an OLDER receipt (one the
   * map already replaced) never overwrites the newer entry. Terminal
   * entries never regress; a lower seq than the one held for the
   * receipt is a stale delivery, ignored; a dismissed receipt id stays
   * dismissed — only a NEW receipt id can show a card again. */

  /* Bounded memory of dismissed receipt ids per pane (FIFO). */
  var DISMISSED_KEEP = 16;

  function createPaneTracks(clockMs) {
    var clock = clockMs || function () { return Date.now(); };
    var byPane = {};
    var dismissedByPane = {};

    function dismissed(paneId, receiptId) {
      var ids = dismissedByPane[paneId];
      return !!ids && ids.indexOf(String(receiptId)) !== -1;
    }

    function rememberDismissed(paneId, receiptId) {
      var ids = dismissedByPane[paneId] || (dismissedByPane[paneId] = []);
      var id = String(receiptId);
      if (ids.indexOf(id) === -1) ids.push(id);
      while (ids.length > DISMISSED_KEEP) ids.shift();
    }

    function get(paneId) {
      return byPane[paneId] || null;
    }

    function start(paneId, receipt) {
      if (!receipt || !receipt.id) return byPane[paneId] || null;
      var existing = byPane[paneId];
      if (existing && existing.receipt_id === String(receipt.id)) {
        /* The receipt is already known — its terminal SSE event may
         * have beaten this POST response here. Never regress it. */
        return existing;
      }
      if (dismissed(paneId, receipt.id)) return byPane[paneId] || null;
      var init = initialTrack(receipt.outcome, clock() / 1000);
      if (!init) return byPane[paneId] || null;
      byPane[paneId] = {
        receipt_id: String(receipt.id),
        pane_id: paneId,
        state: init.state,
        seq: 0,  /* any server event (seq ≥ 1) outranks the synthesis */
        sent_at: init.sent_at,
        stalled: false,
        last_activity_at: null,
        error: null,
      };
      return byPane[paneId];
    }

    function applyEvent(event) {
      if (!event || !event.pane_id || !isKnownState(event.state)) return null;
      if (dismissed(event.pane_id, event.receipt_id)) {
        return byPane[event.pane_id] || null;
      }
      var stored = byPane[event.pane_id];
      if (stored && stored.state !== "superseded" &&
          stored.receipt_id !== event.receipt_id &&
          event.state === "superseded") {
        return stored;  // about an older send the map already replaced
      }
      if (!stored) {
        byPane[event.pane_id] = entryFromEvent(event, null, clock() / 1000);
        return byPane[event.pane_id];
      }
      if (stored.receipt_id !== event.receipt_id) {
        byPane[event.pane_id] = entryFromEvent(
          event, null, clock() / 1000);
        return byPane[event.pane_id];
      }
      if (typeof event.seq === "number" && typeof stored.seq === "number" &&
          event.seq < stored.seq) {
        return stored;  // stale delivery: a lower seq never wins
      }
      var next = entryFromEvent(event, stored.sent_at, clock() / 1000);
      if (isTerminal(stored.state) && !isTerminal(next.state)) {
        return stored;  // terminal is final; a late event cannot reopen it
      }
      byPane[event.pane_id] = next;
      return next;
    }

    function applyReceipts(paneId, receipts) {
      if (!receipts || !receipts.length) return byPane[paneId] || null;
      var newest = null;
      for (var i = 0; i < receipts.length; i++) {
        if (receipts[i] && receipts[i].track) { newest = receipts[i]; break; }
      }
      if (!newest) return byPane[paneId] || null;  // never regress on a miss
      return applyEvent({
        receipt_id: newest.id,
        pane_id: paneId,
        state: newest.track.state,
        seq: newest.track.seq,
        sent_at: newest.track.sent_at,
        elapsed_s: newest.track.elapsed_s,
        stalled: newest.track.stalled,
        last_activity_at: newest.track.last_activity_at,
        error: newest.track.error,
      });
    }

    function dismiss(paneId) {
      var entry = byPane[paneId];
      if (!entry) return false;
      rememberDismissed(paneId, entry.receipt_id);
      delete byPane[paneId];
      return true;
    }

    function hasNonTerminal(paneId) {
      if (paneId) {
        var entry = byPane[paneId];
        return !!entry && !isTerminal(entry.state);
      }
      for (var key in byPane) {
        if (Object.prototype.hasOwnProperty.call(byPane, key) &&
            !isTerminal(byPane[key].state)) return true;
      }
      return false;
    }

    return {
      get: get,
      start: start,
      applyEvent: applyEvent,
      applyReceipts: applyReceipts,
      dismiss: dismiss,
      hasNonTerminal: hasNonTerminal,
    };
  }

  /* The polling cadence (S5): due immediately after a send, every
   * 1.5 s inside the ~12 s hot window, then every 5 s. */
  function fetchDue(nowMs, lastFetchMs, hotUntilMs) {
    if (lastFetchMs === null || lastFetchMs === undefined) return true;
    var span = nowMs - lastFetchMs;
    var every = nowMs < hotUntilMs ? HOT_EVERY_MS : NORMAL_EVERY_MS;
    return span >= every;
  }

  var api = {
    NON_TERMINAL_STATES: NON_TERMINAL_STATES,
    TERMINAL_STATES: TERMINAL_STATES,
    TRACKED_OUTCOMES: TRACKED_OUTCOMES,
    HOT_WINDOW_MS: HOT_WINDOW_MS,
    HOT_EVERY_MS: HOT_EVERY_MS,
    NORMAL_EVERY_MS: NORMAL_EVERY_MS,
    useTranslator: useTranslator,
    isTerminal: isTerminal,
    stateLabel: stateLabel,
    stalledLabel: stalledLabel,
    formatElapsed: formatElapsed,
    errorText: errorText,
    cardModel: cardModel,
    initialTrack: initialTrack,
    createPaneTracks: createPaneTracks,
    fetchDue: fetchDue,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { SendTrack: api };
  }
  global.SendTrack = api;
})(typeof window !== "undefined" ? window : globalThis);
