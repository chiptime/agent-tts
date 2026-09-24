/* node --test suite for the UMD reader client (reader-html-integration).
 *
 * The fake DOM below implements ONLY the Decision 1 enforced surface
 * (createElement/createTextNode, append/remove/replaceChild,
 * querySelector(All), attributes, classList, textContent, set-only
 * innerHTML with a write tracker, parentNode, addEventListener) — a plain
 * object literal, no jsdom, no new dependencies. */

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { createReader } = require("../../src/herdr_brain/static/reader.js");

/* ---- fake DOM (test infrastructure) ---- */

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
    toggle(c) {
      if (this.contains(c)) this.remove(c); else this.add(c);
    },
    contains(c) {
      return tokensOf(node.attributes).includes(c);
    }
  };
}

function textOf(node) {
  return node.nodeType === 3 ? node._text : node.children.map(textOf).join("");
}

function matches(node, sel) {
  if (sel === "*") return true;
  if (sel.startsWith(".")) return tokensOf(node.attributes).includes(sel.slice(1));
  const attr = sel.match(/^\[([a-zA-Z-]+)="([^"]*)"\]$/);
  if (attr) return node.attributes[attr[1]] === attr[2];
  if (/^[a-zA-Z][a-zA-Z0-9-]*$/.test(sel)) return node.tagName === sel.toLowerCase();
  throw new Error("fake DOM: unsupported selector " + sel);
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
    parentNode: null,
    listeners: []
  };
  node.classList = makeClassList(node);
  node.appendChild = child => {
    child.parentNode = node;
    node.children.push(child);
    return child;
  };
  node.removeChild = child => {
    const i = node.children.indexOf(child);
    if (i >= 0) node.children.splice(i, 1);
    child.parentNode = null;
  };
  node.replaceChild = (nu, old) => {
    const i = node.children.indexOf(old);
    if (i >= 0) node.children[i] = nu;
    nu.parentNode = node;
    old.parentNode = null;
  };
  node.setAttribute = (k, v) => { node.attributes[k] = String(v); };
  node.getAttribute = k => (k in node.attributes ? node.attributes[k] : null);
  node.removeAttribute = k => { delete node.attributes[k]; };
  node.addEventListener = (type, fn) => { node.listeners.push({ type, fn }); };
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
      writeLog.push({ node, html: String(html) });   // the write tracker
      node.children = [];
      for (const child of parseHtml(String(html))) node.appendChild(child);
    }
  });
  node.querySelectorAll = sel => descendants(node).filter(el => el.nodeType === 1 && matches(el, sel));
  node.querySelector = sel => node.querySelectorAll(sel)[0] || null;
  return node;
}

function parseHtml(html) {
  const frag = makeElement("#frag");
  const stack = [frag];
  const re = /<\/?([a-zA-Z][a-zA-Z0-9-]*)((?:\s+[a-zA-Z-]+="[^"]*")*)\s*>|([^<]+)/g;
  let m;
  while ((m = re.exec(html)) !== null) {
    if (m[3] !== undefined) {
      if (m[3]) stack[stack.length - 1].appendChild(makeText(m[3]));
    } else if (m[0][1] === "/") {
      if (stack.length > 1) stack.pop();
    } else {
      const el = makeElement(m[1]);
      const attrRe = /([a-zA-Z-]+)="([^"]*)"/g;
      let am;
      while ((am = attrRe.exec(m[2] || "")) !== null) el.attributes[am[1]] = am[2];
      stack[stack.length - 1].appendChild(el);
      stack.push(el);
    }
  }
  return frag.children;
}

function makeDoc() {
  writeLog = [];
  return {
    createElement: makeElement,
    createTextNode: makeText,
    writeLog: () => writeLog
  };
}

/* ---- harness ---- */

function resp(spec) {
  const status = spec.status || 200;
  return { status, ok: status >= 200 && status < 300, json: () => Promise.resolve(spec.json || {}) };
}

function scriptedHttp(plan, calls) {
  return () => {
    calls.push(true);
    return Promise.resolve(resp(plan.length ? plan.shift() : { status: 500 }));
  };
}

function deferredHttp() {
  const calls = [];
  const pending = [];
  const http = () => {
    calls.push(true);
    return new Promise(resolve => pending.push(resolve));
  };
  return { http, calls, respond: spec => pending.shift()(resp(spec)) };
}

function harness(opts = {}) {
  const doc = makeDoc();
  const http = opts.http || scriptedHttp(opts.plan || [], (opts._calls = []));
  const reader = createReader({ doc, http });
  return { doc, reader, http };
}

/* A .reader-content div inside a .gturn inside #glance-turns. */
function readerSurface(doc) {
  const glance = makeElement("div");
  glance.attributes.id = "glance-turns";
  const gturn = makeElement("div");
  gturn.classList.add("gturn");
  const content = makeElement("div");
  content.classList.add("reader-content");
  gturn.appendChild(content);
  glance.appendChild(gturn);
  doc.glance = glance;   // test-side convenience handle
  return { glance, gturn, content };
}

const ANCHORED_HTML =
  '<p><span class="tts-sent" data-sent-idx="0" data-para-idx="0" id="tts-sent-0">uno</span></p>' +
  '<p><span class="tts-sent-cont" data-sent-idx="0">uno (cont)</span></p>' +
  '<p><span class="tts-sent" data-sent-idx="1" data-para-idx="1" id="tts-sent-1">dos</span></p>';

const COVERAGE_MAP = { version: 1, contract: "reader-pipeline/anchors@1", alignment: "coverage",
  total_sents: 2, total_paras: 2,
  engine: { lang: "es", max_chars: 0, summarize: false, lexicon_fp: "stub" }, sentences: [] };

const SNAPSHOT = {
  pane_id: "w1:p9", session_id: "ses_x",
  turns: [
    { turn_id: "t0", role: "user", text: "pregunta", html: ANCHORED_HTML, map: COVERAGE_MAP },
    { turn_id: "t1", role: "assistant", text: "respuesta", html: null, map: null }
  ]
};

const flush = () => new Promise(resolve => setImmediate(resolve));

/* ---- 4.1 mounting ---- */

test("mounts_html_into_reader_content_only", () => {
  const h = harness();
  const { content } = readerSurface(h.doc);
  const mounted = h.reader.mountTurn(content, { text: "hola", html: ANCHORED_HTML });
  assert.equal(mounted, true);
  assert.ok(content.querySelector(".tts-sent"));
  assert.equal(content.querySelectorAll('[data-sent-idx="1"]').length, 1);
  for (const entry of h.doc.writeLog()) {
    assert.ok(entry.node.classList.contains("reader-content"),
      "innerHTML assigned outside .reader-content");
  }
});

test("never_assigns_innerHTML_outside_reader_content", () => {
  const h = harness();
  const plain = makeElement("div");          // NOT .reader-content
  h.reader.mountTurn(plain, { text: "hola", html: ANCHORED_HTML });
  assert.equal(h.doc.writeLog().length, 0);  // refused: no write at all
  assert.equal(plain.textContent, "hola");   // strict text fallback
});

test("text_rendering_uses_textContent", () => {
  const h = harness();
  const { content } = readerSurface(h.doc);
  h.reader.mountTurn(content, { text: "solo texto", html: null });
  assert.equal(content.textContent, "solo texto");
  assert.equal(h.doc.writeLog().length, 0);
});

/* ---- 4.2 fallbacks ---- */

test("null_html_falls_back_to_text", () => {
  const h = harness();
  const { content } = readerSurface(h.doc);
  h.reader.mountTurn(content, { text: "visible", html: null });
  assert.equal(content.textContent, "visible");
});

test("network_failure_keeps_text", async () => {
  const h = harness({ http: () => Promise.reject(new Error("offline")) });
  const { content } = readerSurface(h.doc);
  h.reader.mountTurn(content, { text: "texto vigente", html: null });
  await h.reader.requestSnapshot("w1:p9", "ses_x");
  await flush();
  assert.equal(content.textContent, "texto vigente");  // unchanged, kept
});

test("insertion_throw_falls_back_to_text", () => {
  const h = harness();
  const content = makeElement("div");
  content.classList.add("reader-content");
  Object.defineProperty(content, "innerHTML", {
    get() { return ""; },
    set() { throw new Error("parse error"); }
  });
  h.reader.mountTurn(content, { text: "respaldo", html: ANCHORED_HTML });
  assert.equal(content.textContent, "respaldo");
});

test("container_never_left_empty", async () => {
  const h = harness({ http: () => Promise.reject(new Error("offline")) });
  const { content } = readerSurface(h.doc);
  // null html + failed snapshot: the existing text stays readable.
  h.reader.mountTurn(content, { text: "con texto", html: null });
  await h.reader.requestSnapshot("w1:p9", "ses_x");
  assert.equal(content.textContent, "con texto");
  // Hostile html that neutralizes to nothing: the text guard refills.
  const hostile = makeElement("div");
  hostile.classList.add("reader-content");
  h.reader.mountTurn(hostile, { text: "limpio", html: "<script>x</script>" });
  assert.equal(hostile.textContent, "limpio");
});

/* ---- 4.3 sentence selection ---- */

test("selects_primary_and_continuations", () => {
  const h = harness();
  const { content } = readerSurface(h.doc);
  h.reader.mountTurn(content, { text: "x", html: ANCHORED_HTML });
  const hits = h.reader.selectSentence(content, 0);
  assert.equal(hits, 2);  // primary .tts-sent + .tts-sent-cont
  const selected = content.querySelectorAll(".tts-selected");
  assert.equal(selected.length, 2);
  assert.ok(selected.every(el => el.getAttribute("data-sent-idx") === "0"));
  // sentence 1 untouched
  assert.equal(content.querySelector('[data-sent-idx="1"]').classList.contains("tts-selected"), false);
});

test("selection_scoped_to_owning_turn", () => {
  const h = harness();
  const one = makeElement("div"); one.classList.add("reader-content");
  const two = makeElement("div"); two.classList.add("reader-content");
  for (const c of [one, two]) {
    h.reader.mountTurn(c, { text: "x", html: ANCHORED_HTML });
  }
  h.reader.selectSentence(two, 0);
  assert.equal(one.querySelectorAll(".tts-selected").length, 0);  // untouched
  assert.equal(two.querySelectorAll(".tts-selected").length, 2);
});

test("container_anchor_selectable", () => {
  const h = harness();
  const { content } = readerSurface(h.doc);
  h.reader.mountTurn(content, {
    text: "x",
    html: '<pre class="tts-sent" data-sent-idx="4"><code>ls -la</code></pre>' +
          '<table class="tts-sent" data-sent-idx="5"><tr><td>celda</td></tr></table>' +
          '<ul class="tts-sent" data-sent-idx="6"><li>item</li></ul>' +
          '<ol class="tts-sent" data-sent-idx="7"><li>uno</li></ol>'
  });
  for (const idx of [4, 5, 6, 7]) {
    assert.equal(h.reader.selectSentence(content, idx), 1);
    const el = content.querySelector(`[data-sent-idx="${idx}"]`);
    assert.ok(el.classList.contains("tts-selected"), `anchor ${idx} selected`);
  }
});

test("empty_virtual_span_tolerated", () => {
  const h = harness();
  const { content } = readerSurface(h.doc);
  h.reader.mountTurn(content, { text: "x", html: ANCHORED_HTML });
  assert.equal(h.reader.selectSentence(content, 99), 0);  // 0 hits is legal
  assert.equal(content.querySelectorAll(".tts-selected").length, 0);
});

test("coverage_alignment_selectable", () => {
  const h = harness();
  const { content } = readerSurface(h.doc);
  h.reader.mountTurn(content, { text: "x", html: ANCHORED_HTML, map: COVERAGE_MAP });
  assert.equal(h.reader.selectSentence(content, 1), 1);
  assert.ok(content.querySelector('[data-sent-idx="1"]').classList.contains("tts-selected"));
});

test("selection_emits_no_playback_call", () => {
  const calls = [];
  const h = harness({ http: scriptedHttp([], calls) });
  const { content } = readerSurface(h.doc);
  h.reader.mountTurn(content, { text: "x", html: ANCHORED_HTML });
  const listenersBefore = JSON.stringify(content.querySelectorAll("*").map(e => e.listeners.length));
  h.reader.selectSentence(content, 0);
  assert.equal(calls.length, 0);  // no http/player/IPC of any kind
  assert.equal(
    JSON.stringify(content.querySelectorAll("*").map(e => e.listeners.length)),
    listenersBefore
  );
});

/* ---- 4.4 link hardening ---- */

test("http_links_hardened", () => {
  const h = harness();
  const { content } = readerSurface(h.doc);
  h.reader.mountTurn(content, {
    text: "x",
    html: '<p>mirá <a href="http://ejemplo.com/x?y=1">esto</a></p>'
  });
  const a = content.querySelector("a");
  assert.equal(a.getAttribute("target"), "_blank");
  assert.equal(a.getAttribute("rel"), "noopener noreferrer");
});

test("https_links_hardened", () => {
  const h = harness();
  const { content } = readerSurface(h.doc);
  h.reader.mountTurn(content, {
    text: "x",
    html: '<p><a href="https://ejemplo.com/seguro">seguro</a></p>'
  });
  const a = content.querySelector("a");
  assert.equal(a.getAttribute("target"), "_blank");
  assert.equal(a.getAttribute("rel"), "noopener noreferrer");
});

test("non_http_scheme_not_anchor", () => {
  const h = harness();
  const { content } = readerSurface(h.doc);
  h.reader.mountTurn(content, {
    text: "x",
    html: '<p>antes <a href="javascript:alert(1)">pincha aquí</a> después</p>'
  });
  assert.equal(content.querySelectorAll("a").length, 0);   // no anchor remains
  assert.ok(content.textContent.includes("pincha aquí"));  // content survives
});

/* ---- 4.5 snapshot discipline ---- */

test("stale_response_discarded_on_pane_change", async () => {
  const d = deferredHttp();
  const h = harness({ http: d.http });
  const pending = h.reader.requestSnapshot("w1:p1", "ses_a");
  h.reader.syncIdentity("w1:p2", null);   // user switches pane mid-flight
  d.respond({ json: SNAPSHOT });
  assert.equal(await pending, undefined); // discarded, never merged
});

test("stale_response_discarded_on_session_change", async () => {
  const d = deferredHttp();
  const h = harness({ http: d.http });
  const pending = h.reader.requestSnapshot("w1:p1", "ses_a");
  h.reader.syncIdentity("w1:p1", "ses_b");   // session changed mid-flight
  d.respond({ json: SNAPSHOT });
  assert.equal(await pending, undefined);
});

test("current_snapshot_applies_and_replaces_as_unit", async () => {
  const d = deferredHttp();
  const h = harness({ http: d.http });
  const first = h.reader.requestSnapshot("w1:p9", "ses_x");
  d.respond({ json: SNAPSHOT });
  const applied = await first;
  assert.equal(applied.length, 2);
  // A second (current) snapshot replaces the first wholesale.
  const second = h.reader.requestSnapshot("w1:p9", "ses_x");
  d.respond({ json: { pane_id: "w1:p9", session_id: "ses_x", turns: [SNAPSHOT.turns[0]] } });
  assert.equal((await second).length, 1);
});

test("generation_increments_on_identity_change", () => {
  const h = harness();
  const before = h.reader.currentGeneration();
  h.reader.syncIdentity("w1:p1", "ses_a");
  assert.equal(h.reader.currentGeneration(), before + 1);
  h.reader.syncIdentity("w1:p1", "ses_a");     // same identity: no bump
  assert.equal(h.reader.currentGeneration(), before + 1);
  h.reader.syncIdentity("w1:p1", "ses_b");     // session change bumps
  assert.equal(h.reader.currentGeneration(), before + 2);
  h.reader.syncIdentity("w1:p2", null);        // pane change bumps
  assert.equal(h.reader.currentGeneration(), before + 3);
});

/* ---- 4.6 reader-always-formatted: load-time mounting ---- */

test("mounts_formatted_turns_on_surface_load_without_expansion", async () => {
  const d = deferredHttp();
  const h = harness({ http: d.http });
  const one = makeElement("div"); one.classList.add("reader-content");
  const two = makeElement("div"); two.classList.add("reader-content");
  const pending = h.reader.loadRendered("w1:p9", "ses_x", () => [
    { container: one, turn: { role: "user", text: "pregunta" } },
    { container: two, turn: { role: "assistant", text: "respuesta" } }
  ]);
  d.respond({ json: SNAPSHOT });
  const turns = await pending;
  assert.equal(turns.length, 2);
  assert.equal(d.calls.length, 1);   // fetch ran on load: no gesture anywhere
  assert.ok(one.querySelector(".tts-sent"));   // cached turn: formatted at once
  assert.equal(two.textContent, "respuesta");  // cold turn: text until html arrives
});

test("expansion_does_not_gate_rendering", async () => {
  const d = deferredHttp();
  const h = harness({ http: d.http });
  const truncated = makeElement("div");
  truncated.classList.add("reader-content");
  // Host truncation state travels WITH the pair; the reader must ignore it.
  const truncatedTurn = { turn_id: "t1", role: "assistant", text: "respuesta",
                          expanded: false, hasMore: true };
  const snapshot = {
    pane_id: "w1:p9", session_id: "ses_x",
    turns: [{ turn_id: "t1", role: "assistant", text: "respuesta",
              html: ANCHORED_HTML, map: COVERAGE_MAP }]
  };
  const pending = h.reader.loadRendered("w1:p9", "ses_x", () => [
    { container: truncated, turn: truncatedTurn }
  ]);
  d.respond({ json: snapshot });
  await pending;
  assert.equal(d.calls.length, 1);                 // truncation suppressed nothing
  assert.ok(truncated.querySelector(".tts-sent")); // formatted while still truncated
});

test("cold_turns_upgrade_on_subsequent_refresh", async () => {
  // Fake render queue: first poll returns html null (over budget), the
  // next poll returns the same turn rendered — progressive cold fill.
  const pollSnapshot = (html) => ({
    pane_id: "w1:p9", session_id: "ses_x",
    turns: [{ turn_id: "t1", role: "assistant", text: "larga",
              html, map: html ? COVERAGE_MAP : null }]
  });
  const h = harness({ plan: [{ json: pollSnapshot(null) }, { json: pollSnapshot(ANCHORED_HTML) }] });
  const cold = makeElement("div"); cold.classList.add("reader-content");
  const pairs = () => [{ container: cold, turn: { role: "assistant", text: "larga" } }];
  await h.reader.loadRendered("w1:p9", "ses_x", pairs);
  assert.equal(cold.textContent, "larga");    // text first
  assert.equal(h.doc.writeLog().length, 0);   // nothing formatted yet
  await h.reader.loadRendered("w1:p9", "ses_x", pairs);
  assert.ok(cold.querySelector(".tts-sent")); // upgraded in place on refresh
  assert.equal(h.doc.writeLog().length, 1);   // exactly one formatted write
});

/* ---- 4.7 security regressions ---- */

test("malicious_markdown_no_script_vector", () => {
  const h = harness();
  const { content } = readerSurface(h.doc);
  h.reader.mountTurn(content, {
    text: "informe limpio",
    html: '<p>ok</p><script>alert(1)</script>' +
          '<p><span onmouseover="evil()" onclick="evil()">clic</span></p>' +
          '<a href="https://ok.example/bien">bien</a>'
  });
  assert.equal(content.querySelectorAll("script").length, 0);  // no script node
  const span = content.querySelector("span");
  assert.equal(span.getAttribute("onmouseover"), null);        // handlers stripped
  assert.equal(span.getAttribute("onclick"), null);
  assert.ok(content.textContent.includes("ok"));               // content survives
  const a = content.querySelector("a");
  assert.equal(a.getAttribute("rel"), "noopener noreferrer");  // still hardened
});

test("no_inline_handlers_introduced", () => {
  const h = harness();
  const { content } = readerSurface(h.doc);
  h.reader.mountTurn(content, { text: "x", html: ANCHORED_HTML });
  h.reader.selectSentence(content, 0);   // interactions are classList only
  for (const el of content.querySelectorAll("*")) {
    for (const key of Object.keys(el.attributes)) {
      assert.ok(!/^on/i.test(key), `inline handler ${key} introduced`);
    }
  }
});

test("reader_source_has_no_global_document", () => {
  const source = fs.readFileSync(
    path.join(__dirname, "../../src/herdr_brain/static/reader.js"), "utf-8"
  );
  assert.ok(!/\bdocument\b/.test(source),
    "reader.js must receive its DOM surface by injection (Decision 1)");
});
