/* node --test suite for the consult state + report panel (T10 of
 * docs/prds/herdr-brain-on-demand-context.md; FR-14, FR-18, FR-33).
 *
 * The fake DOM implements ONLY the injected surface the module touches
 * (createElement, appendChild, classList, textContent, attributes) with
 * a write-tracking innerHTML setter — the same plain-object approach
 * as toast.test.js / reader.test.js: no jsdom, no new dependencies.
 *
 * SECURITY (FR-14/FR-40 discipline from reader-html): the consult
 * report screen text is UNTRUSTED source content. The suite feeds a
 * malicious <script> payload and asserts it lands as literal text —
 * zero innerHTML writes, zero script ELEMENTS in the tree.
 */

const test = require("node:test");
const assert = require("node:assert");
const {
  createConsultUI,
  requestAsk,
  ASK_REQUEST_TIMEOUT_MS
} = require("../../src/herdr_brain/static/consult.js");

/* ---- fake DOM (toast.test.js subset) ---- */

let writeLog = [];

function tokensOf(attrs) {
  return (attrs.class || "").split(/\s+/).filter(Boolean);
}

function makeClassList(node) {
  return {
    add(...cls) {
      const t = tokensOf(node.attributes);
      for (const c of cls) if (!t.includes(c)) t.push(c);
      node.attributes.class = t.join(" ");
    },
    remove(c) {
      node.attributes.class = tokensOf(node.attributes).filter(t => t !== c).join(" ");
    },
    toggle(c, force) {
      const want = force === undefined ? !this.contains(c) : !!force;
      if (want) this.add(c); else this.remove(c);
    },
    contains(c) {
      return tokensOf(node.attributes).includes(c);
    }
  };
}

function textOf(node) {
  return node.nodeType === 3 ? node._text : node.children.map(textOf).join("");
}

function descendants(node, out = []) {
  if (node.nodeType === 3) return out;
  for (const child of node.children) {
    out.push(child);
    descendants(child, out);
  }
  return out;
}

function makeText(text) {
  return { nodeType: 3, _text: String(text), parentNode: null };
}

function makeElement(tag) {
  const node = {
    nodeType: 1,
    tagName: tag.toLowerCase(),
    attributes: {},
    children: [],
    parentNode: null
  };
  node.classList = makeClassList(node);
  node.appendChild = child => {
    child.parentNode = node;
    node.children.push(child);
    return child;
  };
  node.setAttribute = (k, v) => { node.attributes[k] = String(v); };
  node.getAttribute = k => (k in node.attributes ? node.attributes[k] : null);
  node.removeAttribute = k => { delete node.attributes[k]; };
  Object.defineProperty(node, "textContent", {
    configurable: true,
    get: () => node.children.map(textOf).join(""),
    set(value) {
      node.children = [];
      const t = makeText(value);
      t.parentNode = node;
      node.children.push(t);
    }
  });
  Object.defineProperty(node, "innerHTML", {
    configurable: true,
    get: () => node._html || "",
    set(html) {
      node._html = String(html);
      writeLog.push({ node, html: String(html) });
    }
  });
  return node;
}

function makeDoc() {
  return { createElement: makeElement, createTextNode: makeText };
}

/* ---- harness ---- */

function makeUI() {
  const mount = makeElement("section");
  const notices = [];
  const ui = createConsultUI({
    doc: makeDoc(),
    mount,
    notify: text => notices.push(text)
  });
  return { ui, mount, notices };
}

function indicatorOf(mount) {
  return mount.children.find(c => c.classList.contains("consult-indicator"));
}

function reportOf(mount) {
  return mount.children.find(c => c.classList.contains("consult-report"));
}

/* No spoken-path seam: a counting Audio factory proves the module never
 * constructs audio on any event path (FR-14: references/report content
 * never auto-plays — display only). */
let audioConstructed = 0;
globalThis.Audio = function () { audioConstructed += 1; };

function consulting(state, outcome) {
  return outcome === undefined
    ? { type: "consulting", state: state }
    : { type: "consulting", state: state, outcome: outcome };
}

/* ---- indicator (FR-18: visible Consulting state) ---- */

test("consulting start shows the indicator with visible text", () => {
  const { ui, mount } = makeUI();
  ui.handleEvent(consulting("start"));
  const ind = indicatorOf(mount);
  assert.ok(ind, "indicator element mounted");
  assert.strictEqual(ind.textContent, "⏳ Consultando…");
  assert.strictEqual(ind.classList.contains("hidden"), false);
  assert.strictEqual(mount.classList.contains("hidden"), false);
});

test("consulting end with outcome rendered clears the indicator", () => {
  const { ui, mount } = makeUI();
  ui.handleEvent(consulting("start"));
  ui.handleEvent(consulting("end", "rendered"));
  const ind = indicatorOf(mount);
  assert.ok(ind, "indicator stays mounted, just hidden");
  assert.strictEqual(ind.classList.contains("hidden"), true);
  assert.strictEqual(mount.classList.contains("hidden"), true);
});

/* ---- outcome notices (terminal-for-this-turn states) ---- */

for (const outcome of ["unable_to_complete", "narrow_ask", "ask_tz", "clarify"]) {
  test(`consulting end outcome ${outcome} clears indicator and notifies`, () => {
    const { ui, mount, notices } = makeUI();
    ui.handleEvent(consulting("start"));
    notices.length = 0;
    ui.handleEvent(consulting("end", outcome));
    const ind = indicatorOf(mount);
    assert.ok(ind.classList.contains("hidden"), "indicator cleared");
    assert.strictEqual(notices.length, 1, "exactly one transient notice");
    assert.ok(notices[0].length > 10, "notice carries user-facing text");
  });
}

test("unknown outcome on end clears the indicator without a notice", () => {
  const { ui, mount, notices } = makeUI();
  ui.handleEvent(consulting("start"));
  ui.handleEvent(consulting("end", "something_new"));
  assert.ok(indicatorOf(mount).classList.contains("hidden"));
  assert.strictEqual(notices.length, 0);
});

/* ---- report panel (FR-14: references on screen, never spoken) ---- */

const REPORT_A = "Avances:\n- Did auth work\n\nReferences:\n- opencode:s1 (opencode), locator: a, retrieved: 2026-09-30";

test("consult_report renders the screen text as a panel section", () => {
  const { ui, mount, notices } = makeUI();
  ui.handleEvent({
    type: "consult_report",
    screen: REPORT_A,
    interval_label: "hoy",
    timezone_label: "Europe/Madrid"
  });
  const rep = reportOf(mount);
  assert.ok(rep, "report panel mounted");
  assert.strictEqual(rep.classList.contains("hidden"), false);
  const body = rep.children.find(c => c.classList.contains("consult-body"));
  assert.ok(body, "body block exists (CSS pre-wrap surface)");
  assert.strictEqual(body.textContent, REPORT_A, "screen text rendered EXACTLY");
  assert.ok(REPORT_A.includes("References:"), "fixture really carries references");
  assert.ok(body.textContent.includes("\n"), "multi-line text preserved");
  assert.strictEqual(mount.classList.contains("hidden"), false);
  assert.strictEqual(notices.length, 0, "a report is never a transient notice");
});

test("consult_report with a malicious script payload renders as TEXT only", () => {
  const { ui, mount } = makeUI();
  writeLog = [];
  const evil = '<script>alert("xss")</script>\n<img src=x onerror="alert(1)">';
  ui.handleEvent({
    type: "consult_report",
    screen: evil,
    interval_label: "hoy",
    timezone_label: "Europe/Madrid"
  });
  const body = reportOf(mount).children.find(c => c.classList.contains("consult-body"));
  assert.strictEqual(body.textContent, evil, "payload survives as literal text");
  assert.strictEqual(writeLog.length, 0, "NO innerHTML writes anywhere");
  const tags = descendants(mount).map(n => n.tagName);
  assert.strictEqual(tags.includes("script"), false, "no script ELEMENT constructed");
  assert.strictEqual(tags.includes("img"), false, "no img ELEMENT constructed");
});

test("a second consult_report REPLACES the previous panel content", () => {
  const { ui, mount } = makeUI();
  ui.handleEvent({
    type: "consult_report",
    screen: REPORT_A,
    interval_label: "hoy",
    timezone_label: "Europe/Madrid"
  });
  // a new consult dims the old panel (stale, never looking current)
  ui.handleEvent(consulting("start"));
  const rep = reportOf(mount);
  assert.ok(rep.classList.contains("stale"), "old report dims while consulting");
  ui.handleEvent(consulting("end", "rendered"));
  const reportB = "Segundo informe\nReferences:\n- opencode:s2";
  ui.handleEvent({
    type: "consult_report",
    screen: reportB,
    interval_label: "esta semana",
    timezone_label: "Europe/Madrid"
  });
  assert.strictEqual(reportOf(mount), rep, "the panel node is reused");
  assert.strictEqual(rep.classList.contains("stale"), false, "fresh render un-dims");
  const body = rep.children.find(c => c.classList.contains("consult-body"));
  assert.strictEqual(body.textContent, reportB, "latest wins (FR-33 spirit)");
  const meta = rep.children.find(c => c.classList.contains("consult-meta"));
  assert.ok(meta.textContent.includes("esta semana"), "meta label follows the new report");
});

test("consult_report without a string screen is ignored", () => {
  const { ui, mount } = makeUI();
  ui.handleEvent({ type: "consult_report" });
  ui.handleEvent({ type: "consult_report", screen: null });
  ui.handleEvent({ type: "consult_report", screen: 42 });
  assert.strictEqual(reportOf(mount), undefined, "nothing rendered");
  assert.strictEqual(mount.classList.contains("hidden"), true);
});

test("unknown event types are no-ops", () => {
  const { ui, mount, notices } = makeUI();
  ui.handleEvent(null);
  ui.handleEvent({ type: "transition", text: "x" });
  ui.handleEvent({ type: "system", text: "x" });
  assert.strictEqual(mount.children.length, 0);
  assert.strictEqual(notices.length, 0);
});

/* ---- split mounts: floating in-call indicator pill (FR-18/D06) ----
 *
 * The transient "Consultando…" status moves OUT of the report panel
 * into its own indicatorMount (the floating pill inside the call
 * drawer): with separate mounts the two visibility invariants are
 * INDEPENDENT — the pill mount shows only while consulting, the
 * report mount shows only when a report exists. The single-mount
 * behavior pinned above remains the contract when indicatorMount is
 * omitted (legacy callers and the same-mount tests). */

function makeSplitUI() {
  const reportMount = makeElement("section");
  const indicatorMount = makeElement("div");
  const notices = [];
  const ui = createConsultUI({
    doc: makeDoc(),
    mount: reportMount,
    indicatorMount,
    notify: text => notices.push(text)
  });
  return { ui, reportMount, indicatorMount, notices };
}

test("split mounts: creation hides BOTH mounts (independent invariants)", () => {
  const { reportMount, indicatorMount } = makeSplitUI();
  assert.strictEqual(reportMount.classList.contains("hidden"), true,
    "report mount hidden with nothing to show");
  assert.strictEqual(indicatorMount.classList.contains("hidden"), true,
    "indicator mount hidden with nothing to show");
});

test("split mounts: consulting start shows the pill WITHOUT revealing the report mount", () => {
  const { ui, reportMount, indicatorMount } = makeSplitUI();
  ui.handleEvent(consulting("start"));
  const ind = indicatorOf(indicatorMount);
  assert.ok(ind, "indicator element lives in the indicator mount");
  assert.strictEqual(ind.textContent, "⏳ Consultando…");
  assert.strictEqual(ind.classList.contains("hidden"), false, "pill visible");
  assert.strictEqual(indicatorMount.classList.contains("hidden"), false,
    "pill mount visible");
  assert.strictEqual(reportMount.classList.contains("hidden"), true,
    "report panel NOT revealed by a consulting state");
  assert.strictEqual(reportOf(reportMount), undefined,
    "no report element built just by consulting");
});

test("split mounts: report renders in the report mount; pill mount stays hidden", () => {
  const { ui, reportMount, indicatorMount } = makeSplitUI();
  ui.handleEvent({
    type: "consult_report",
    screen: REPORT_A,
    interval_label: "hoy",
    timezone_label: "Europe/Madrid"
  });
  const rep = reportOf(reportMount);
  assert.ok(rep, "report panel lives in the report mount");
  assert.strictEqual(rep.classList.contains("hidden"), false);
  assert.strictEqual(reportMount.classList.contains("hidden"), false);
  assert.strictEqual(indicatorMount.classList.contains("hidden"), true,
    "a report never shows the pill");
  assert.strictEqual(indicatorOf(indicatorMount), undefined,
    "no indicator element built just by a report");
});

test("split mounts: consulting end hides the pill while the rendered report remains", () => {
  const { ui, reportMount, indicatorMount } = makeSplitUI();
  ui.handleEvent({
    type: "consult_report",
    screen: REPORT_A,
    interval_label: "hoy",
    timezone_label: "Europe/Madrid"
  });
  ui.handleEvent(consulting("start"));
  ui.handleEvent(consulting("end", "rendered"));
  const ind = indicatorOf(indicatorMount);
  assert.ok(ind, "pill stays mounted, just hidden");
  assert.strictEqual(ind.classList.contains("hidden"), true, "pill hidden");
  assert.strictEqual(indicatorMount.classList.contains("hidden"), true,
    "pill mount hidden");
  const rep = reportOf(reportMount);
  assert.strictEqual(rep.classList.contains("hidden"), false,
    "rendered report stays visible after the pill clears");
  assert.strictEqual(reportMount.classList.contains("hidden"), false,
    "report mount stays visible");
});

test("split mounts: stale report dimming still crosses the mounts", () => {
  const { ui, reportMount } = makeSplitUI();
  ui.handleEvent({
    type: "consult_report",
    screen: REPORT_A,
    interval_label: "hoy",
    timezone_label: "Europe/Madrid"
  });
  ui.handleEvent(consulting("start"));
  assert.ok(reportOf(reportMount).classList.contains("stale"),
    "old report dims while consulting, even from the pill mount");
  ui.handleEvent(consulting("end", "rendered"));
  ui.handleEvent({
    type: "consult_report",
    screen: "Segundo informe\nReferences:\n- opencode:s2",
    interval_label: "esta semana",
    timezone_label: "Europe/Madrid"
  });
  assert.strictEqual(reportOf(reportMount).classList.contains("stale"), false,
    "fresh render un-dims");
});

test("split mounts: an indicatorMount without appendChild is rejected", () => {
  assert.throws(
    () => createConsultUI({
      doc: makeDoc(),
      mount: makeElement("section"),
      indicatorMount: {}
    }),
    /indicatorMount/
  );
});

/* ---- spoken-path regression guard (trivial but explicit) ---- */

test("no event path ever constructs audio", () => {
  const { ui } = makeUI();
  ui.handleEvent(consulting("start"));
  ui.handleEvent(consulting("end", "unable_to_complete"));
  ui.handleEvent({
    type: "consult_report",
    screen: REPORT_A,
    interval_label: "hoy",
    timezone_label: "Europe/Madrid"
  });
  assert.strictEqual(audioConstructed, 0, "display only — nothing auto-plays");
});

/* ---- /ask request helper (timeout policy seam) ----
 *
 * The browser must NOT abort /ask before the server finishes: the
 * consult engine alone has a 60 s budget, and the journal shows the
 * full turn (initial model call + consult_work_status + consult
 * summarizer + final model call) completing at ~53 s — inside the
 * server, past the old 30 s fetch cap, which discarded answers the
 * server had already produced. The helper OWNS the /ask request
 * (method, JSON content type, snake_case body) and pins ONE named
 * timeout so app.js never sizes or shapes /ask ad hoc. */

test("ASK_REQUEST_TIMEOUT_MS is exactly 90000 (60s consult budget + 30s margin)", () => {
  assert.strictEqual(ASK_REQUEST_TIMEOUT_MS, 90000);
});

test("requestAsk builds the POST /ask request: method, JSON content type, body", () => {
  const calls = [];
  const fake = (url, opts, timeoutMs) => {
    calls.push({ url, opts, timeoutMs });
    return Promise.resolve("response");
  };
  requestAsk(fake, { text: "qué hice hoy", sessionId: "s-1", paneId: "work" });
  assert.strictEqual(calls.length, 1, "exactly one dispatch");
  assert.strictEqual(calls[0].url, "/ask", "url is /ask");
  assert.strictEqual(calls[0].opts.method, "POST", "method is POST");
  assert.strictEqual(
    calls[0].opts.headers["content-type"],
    "application/json",
    "JSON content type header"
  );
  assert.deepStrictEqual(
    JSON.parse(calls[0].opts.body),
    { text: "qué hice hoy", session_id: "s-1", pane_id: "work" },
    "body carries text/session_id/pane_id in the wire format"
  );
});

test("requestAsk merges the optional speech identity into the body; absent leaves it unchanged", () => {
  const bodies = [];
  const fake = (url, opts) => { bodies.push(JSON.parse(opts.body)); return Promise.resolve("r"); };
  requestAsk(fake, {
    text: "hola", sessionId: "s-1", paneId: "work",
    speech: { speech_request_id: "id-12345678", speech_cancel_token: "tok" },
  });
  requestAsk(fake, { text: "hola", sessionId: "s-1", paneId: "work", speech: {} });
  assert.deepStrictEqual(bodies[0], {
    text: "hola", session_id: "s-1", pane_id: "work",
    speech_request_id: "id-12345678", speech_cancel_token: "tok",
  });
  assert.deepStrictEqual(bodies[1], { text: "hola", session_id: "s-1", pane_id: "work" });
});

test("requestAsk normalizes a missing paneId to pane_id: null", () => {
  const calls = [];
  const fake = (url, opts) => {
    calls.push({ opts });
    return Promise.resolve("response");
  };
  requestAsk(fake, { text: "qué hice hoy", sessionId: "s-1" });
  assert.strictEqual(JSON.parse(calls[0].opts.body).pane_id, null, "pane_id null when no pane selected");
});

test("requestAsk passes the fixed 90000ms timeout, not the caller's", () => {
  const calls = [];
  const fake = (url, opts, timeoutMs) => {
    calls.push({ timeoutMs });
    return Promise.resolve("response");
  };
  requestAsk(fake, { text: "qué hice hoy", sessionId: "s-1", paneId: "work" });
  assert.strictEqual(calls[0].timeoutMs, 90000, "timeout is exactly 90000");
  assert.strictEqual(calls[0].timeoutMs, ASK_REQUEST_TIMEOUT_MS, "the named policy constant is used");
});

test("requestAsk returns the injected fetch's promise UNCHANGED", () => {
  const marker = Promise.resolve("the-fetch-promise");
  const fake = () => marker;
  const out = requestAsk(fake, { text: "qué hice hoy", sessionId: "s-1", paneId: "work" });
  assert.strictEqual(out, marker, "promise identity preserved (no wrapping)");
});
