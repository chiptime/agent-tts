/* Consult state + on-screen report panel (T10 of docs/prds/
 * herdr-brain-on-demand-context.md; FR-14, FR-18, FR-33, D06).
 *
 * Pure UMD module following the announce.js/toast.js house pattern:
 * every DOM touch goes through the injected surface ({ doc, mount,
 * indicatorMount?, notify }) — this file references no browser
 * global, so the Node suite drives it against a plain fake DOM.
 *
 * Mounts: `mount` always hosts the report panel. The transient
 * indicator goes to `mount` too UNLESS a separate `indicatorMount`
 * is provided (the floating "Consultando…" pill inside the call
 * drawer): with separate mounts each mount's visibility invariant is
 * independent — indicator mount shown only while consulting, report
 * mount shown only when a report exists.
 *
 * Events (SSE dicts from the consult event sink, server-wired through
 * the announcement hub):
 *   {"type": "consulting", "state": "start"}                    -> the
 *     visible "Consultando…" indicator (FR-18/D06: visible from
 *     FRESHNESS_CHECK until RENDER). A still-showing previous report
 *     dims (.stale) so it never looks current while re-checking.
 *   {"type": "consulting", "state": "end", "outcome": ...}      ->
 *     clears the indicator. outcome "rendered" is quiet (the report
 *     event follows); the terminal user-facing states
 *     (unable_to_complete / narrow_ask / ask_tz / clarify) surface
 *     their text as ONE transient notice through the injected
 *     notify(text) — DECISION: the toast, reused, because it is
 *     already the app's transient-notice surface (no new CSS/styling
 *     path); the module itself stays DOM-pure and testable.
 *   {"type": "consult_report", "screen", "interval_label",
 *    "timezone_label"}                                          -> renders
 *     the report panel: the SCREEN text verbatim (the engine's
 *     render_screen already includes the per-project References
 *     blocks — FR-14 — so references are displayed, never parsed or
 *     reformatted). Each render REPLACES the previous panel content
 *     (latest wins; a stale report is never left looking current once
 *     a new render arrives — FR-33 spirit).
 *
 * SECURITY (reader-html CSP discipline): the screen text is UNTRUSTED
 * source content. It mounts through createElement + textContent ONLY —
 * innerHTML is never touched anywhere in this module. Multi-line
 * layout is preserved by the .consult-body CSS class
 * (white-space: pre-wrap, same rule as .gt-text/.ap-text) instead of
 * any <pre> markup injection.
 *
 * Spoken-path guard (FR-14): this module holds NO audio seam at all —
 * no Audio construction, no player, no enqueue. What it renders is
 * display-only by construction.
 */

(function (global) {
  "use strict";

  var INDICATOR_TEXT = "⏳ Consultando…";

  /* Terminal-for-this-turn states (query FSM) -> the transient notice
   * text. Spanish on purpose (single Spanish-speaking owner, app.js
   * convention). Unknown outcomes clear the indicator silently. */
  var OUTCOME_NOTICES = {
    unable_to_complete: "No pude completar el informe — vuelve a intentarlo.",
    narrow_ask: "Demasiada información para un informe — acota fechas o proyectos.",
    ask_tz: "Necesito tu zona horaria para responder eso.",
    clarify: "Aclara el período que quieres consultar."
  };

  function createConsultUI(deps) {
    deps = deps || {};
    var doc = deps.doc;
    var mount = deps.mount;
    var indicatorMount = deps.indicatorMount || null;
    var notify = deps.notify || function () {};

    if (!doc || typeof doc.createElement !== "function") {
      throw new Error("createConsultUI: deps.doc with createElement is required");
    }
    if (!mount || typeof mount.appendChild !== "function") {
      throw new Error("createConsultUI: deps.mount is required");
    }
    if (indicatorMount && typeof indicatorMount.appendChild !== "function") {
      throw new Error("createConsultUI: deps.indicatorMount requires appendChild");
    }

    var indicatorHost = indicatorMount || mount;

    var indicator = null;   // lazy: .consult-indicator
    var report = null;      // lazy: .consult-report (one, replaced in place)
    var reportBody = null;  // .consult-body inside the report
    var reportMeta = null;  // .consult-meta inside the report

    /* The module owns the mount's visibility invariant from creation:
     * nothing shown -> hidden, whatever the shipped markup looked like. */
    syncVisibility();

    function ensureIndicator() {
      if (indicator) return indicator;
      indicator = doc.createElement("div");
      indicator.classList.add("consult-indicator", "hidden");
      indicator.setAttribute("role", "status");
      indicator.textContent = INDICATOR_TEXT;   // fixed string: textContent is safe
      indicatorHost.appendChild(indicator);
      return indicator;
    }

    function ensureReport() {
      if (report) return report;
      report = doc.createElement("div");
      report.classList.add("consult-report", "hidden");
      reportMeta = doc.createElement("div");
      reportMeta.classList.add("consult-meta");
      reportBody = doc.createElement("div");
      reportBody.classList.add("consult-body");
      report.appendChild(reportMeta);
      report.appendChild(reportBody);
      mount.appendChild(report);
      return report;
    }

    /* Split mounts: each mount owns an independent invariant — the
     * indicator mount is visible iff the indicator is, the report
     * mount iff a report is. Single mount (legacy): visible iff ANY
     * child surface is. */
    function syncVisibility() {
      var indicatorVisible =
        indicator !== null && !indicator.classList.contains("hidden");
      var reportVisible =
        report !== null && !report.classList.contains("hidden");
      if (indicatorMount) {
        indicatorMount.classList.toggle("hidden", !indicatorVisible);
        mount.classList.toggle("hidden", !reportVisible);
        return;
      }
      mount.classList.toggle("hidden", !(indicatorVisible || reportVisible));
    }

    function showIndicator() {
      ensureIndicator().classList.remove("hidden");
      /* FR-33 spirit: while a NEW consult re-checks evidence, the old
       * panel dims — it must not look current against fresh work. */
      if (report && !report.classList.contains("hidden")) {
        report.classList.add("stale");
      }
      syncVisibility();
    }

    function hideIndicator() {
      if (indicator) indicator.classList.add("hidden");
      syncVisibility();
    }

    function renderReport(evt) {
      ensureReport();
      report.classList.remove("hidden");
      report.classList.remove("stale");   // fresh render: current again
      var meta = "Informe de trabajo";
      if (evt.interval_label) meta += " · " + evt.interval_label;
      if (evt.timezone_label) meta += " · " + evt.timezone_label;
      if (reportMeta.textContent !== meta) reportMeta.textContent = meta;
      /* textContent REPLACES all children: the previous report body is
       * gone before the new one exists — latest wins, atomically. */
      reportBody.textContent = evt.screen;
      syncVisibility();
    }

    function handleEvent(evt) {
      if (!evt || typeof evt.type !== "string") return;
      if (evt.type === "consulting") {
        if (evt.state === "start") showIndicator();
        else if (evt.state === "end") {
          hideIndicator();
          var notice = OUTCOME_NOTICES[evt.outcome];
          if (notice) notify(notice);
        }
        return;
      }
      if (evt.type === "consult_report") {
        if (typeof evt.screen === "string") renderReport(evt);
        return;
      }
      /* Unknown types (transition/system/…): not ours — no-op. */
    }

    return { handleEvent: handleEvent };
  }

  /* ---------------- /ask request timeout policy ----------------
   *
   * ASK_REQUEST_TIMEOUT_MS sizes the browser's fetch budget for the
   * /ask turn as a WHOLE. The consult engine alone has a 60 s server
   * budget, and a real journal shows the full turn (initial model
   * call -> consult_work_status -> consult summarizer -> final model
   * call) completing at ~53 s — the old 30 s fetch cap aborted the
   * request mid-flight and discarded an answer the server had already
   * produced. 90 s = 60 s consult budget + 30 s margin for the initial
   * model tool selection, the final response and TTS response shaping.
   *
   * requestAsk also OWNS the /ask wire shape (POST, JSON content
   * type, text/session_id/pane_id body) so the policy and the request
   * travel together. It is a pure seam: app.js injects its own
   * fetchWithTimeout and gets its promise back unchanged, so the Node
   * suite pins the policy without booting the app.js DOM. All OTHER
   * fetchWithTimeout call sites keep their own defaults. */
  var ASK_REQUEST_TIMEOUT_MS = 90000;

  function requestAsk(fetchWithTimeout, payload) {
    return fetchWithTimeout("/ask", {
      method: "POST",
      headers: { "content-type": "application/json" },
      // payload.speech (optional) carries the speech request identity
      // (speech_request_id + speech_cancel_token); absent => the body is
      // byte-identical to the pre-speech shape.
      body: JSON.stringify(Object.assign({
        text: payload.text,
        session_id: payload.sessionId,
        pane_id: payload.paneId || null
      }, payload.speech || {}))
    }, ASK_REQUEST_TIMEOUT_MS);
  }

  var api = {
    createConsultUI: createConsultUI,
    OUTCOME_NOTICES: OUTCOME_NOTICES,
    requestAsk: requestAsk,
    ASK_REQUEST_TIMEOUT_MS: ASK_REQUEST_TIMEOUT_MS
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    global.Consult = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
