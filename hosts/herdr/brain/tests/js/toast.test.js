/* node --test suite for the formatted agent-status toast
 * (toast-formatted-avisos).
 *
 * The fake DOM implements ONLY the injected surface the modules touch
 * (createElement/createTextNode, appendChild/removeChild/replaceChild,
 * querySelector(All), attributes, classList, textContent, set-only
 * innerHTML with a write tracker) — the same plain-object approach as
 * reader.test.js: no jsdom, no new dependencies.
 *
 * The harness composes the REAL reader (createReader) with the REAL
 * toast module exactly as app.js wires them, so the reader's security
 * pipeline (neutralize + hardenLinks) runs for real inside the toast. */

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { createReader } = require("../../src/herdr_brain/static/reader.js");
const { createToast, chunkifyText } = require("../../src/herdr_brain/static/toast.js");

/* ---- fake DOM (test infrastructure, reader.test.js subset) ---- */

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
    parentNode: null
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

/* ---- harness: reader + toast composed exactly like app.js ---- */

function resp(spec) {
  const status = spec.status || 200;
  return { status, ok: status >= 200 && status < 300, json: () => Promise.resolve(spec.json || {}) };
}

const AGENT_TEXT = "respuesta con negrita y código en línea";
const AGENT_HTML =
  '<p><strong>respuesta</strong> con <code>negrita</code> y ' +
  '<span class="tts-sent" data-sent-idx="0">código</span></p>';
const LINK_TEXT = "mirá la documentación";
const LINK_HTML =
  '<p>mirá <a href="https://ejemplo.com/doc">la documentación</a> y ' +
  '<a href="javascript:alert(1)">esto no</a></p>';

const SNAPSHOT = {
  pane_id: "w1:p9", session_id: "ses_x",
  turns: [
    { turn_id: "t0", role: "assistant", text: AGENT_TEXT, html: AGENT_HTML, map: null },
    { turn_id: "t1", role: "assistant", text: LINK_TEXT, html: LINK_HTML, map: null }
  ]
};

function harness(opts = {}) {
  const doc = makeDoc();
  const calls = [];
  const http = opts.http || (() => {
    calls.push(true);
    return Promise.resolve(resp({ json: SNAPSHOT }));
  });
  const reader = createReader({ doc, http });
  const toastEl = makeElement("div");
  toastEl.attributes.id = "toast";
  toastEl.classList.add("hidden");
  const toast = createToast({
    doc,
    el: toastEl,
    mountFormatted: opts.mountFormatted === undefined ? reader.mountTurn : opts.mountFormatted
  });
  return { doc, reader, toast, toastEl, calls };
}

const loadSnapshot = h => h.reader.requestSnapshot("w1:p9", "ses_x");

/* ---- 1.1 formatted mount when the snapshot is available ---- */

test("renders_formatted_html_when_snapshot_available", async () => {
  const h = harness();
  await loadSnapshot(h);
  const html = h.reader.htmlFor("assistant", AGENT_TEXT);   // content-keyed lookup
  assert.ok(html, "precondition: snapshot holds rendered html for this exact text");
  const callsBefore = h.calls.length;
  h.toast.show("🔊 agente: " + AGENT_TEXT, { html: html });
  const body = h.toastEl.querySelector(".toast-content");
  assert.ok(body, "formatted body is mounted inside the toast container");
  assert.ok(body.classList.contains("reader-content"),
    "body carries the reader insertion-guard class");
  assert.ok(body.querySelector(".tts-sent"), "reader payload mounted (anchors intact)");
  assert.ok(body.querySelector("strong"), "bold survives into the excerpt");
  assert.equal(h.toastEl.classList.contains("hidden"), false);
  assert.equal(h.calls.length, callsBefore,
    "announcement is a cache hit: no new renderer call");
});

/* ---- 1.2 plain fallback (watcher avisos and failures) ---- */

test("falls_back_to_textcontent_without_html", () => {
  const h = harness();
  // Watcher template aviso: never rendered, no payload at all.
  h.toast.show("🔊 herdr: despliegue ha terminado");
  assert.equal(h.toastEl.textContent, "🔊 herdr: despliegue ha terminado");
  assert.equal(h.toastEl.querySelector(".toast-content"), null);
  assert.equal(h.doc.writeLog().length, 0);   // zero innerHTML anywhere
  // Lookup miss for an unrendered text: null payload, same plain path.
  const miss = h.reader.htmlFor("assistant", "X ha terminado");
  assert.equal(miss, null);
  h.toast.show("🔊 agente: X ha terminado", { html: miss });
  assert.equal(h.toastEl.textContent, "🔊 agente: X ha terminado");
  assert.equal(h.doc.writeLog().length, 0);
  // Mount throw (renderer/insertion failure): degrade to plain text.
  const failing = harness({
    mountFormatted() { throw new Error("insertion failed"); }
  });
  failing.toast.show("🔊 agente: texto visible", { html: AGENT_HTML });
  assert.equal(failing.toastEl.textContent, "🔊 agente: texto visible");
  assert.equal(failing.toastEl.querySelector(".toast-content"), null);
});

/* ---- 1.3 CSS scroll cap only, payload never mutated ---- */

test("css_scroll_cap_only", async () => {
  const h = harness();
  await loadSnapshot(h);
  const html = h.reader.htmlFor("assistant", AGENT_TEXT);
  h.toast.show("🔊 agente: " + AGENT_TEXT, { html: html });
  const body = h.toastEl.querySelector(".toast-content");
  assert.ok(body.classList.contains("toast-content"),
    "body class applied for the CSS scroll cap");
  // The payload reached innerHTML byte-identical: no slice/regex ever ran.
  const writes = h.doc.writeLog();
  assert.equal(writes.length, 1);
  assert.equal(writes[0].node, body, "innerHTML assigned on the toast body only");
  assert.strictEqual(writes[0].html, html, "payload mounted verbatim");
  // The stylesheet really caps + scrolls: #toast is the single scroll
  // surface for BOTH the plain and the formatted body (full-text replay,
  // 2026-09-25) — the old 8em overflow:hidden clip on .toast-content is
  // gone by decision.
  const css = fs.readFileSync(
    path.join(__dirname, "../../src/herdr_brain/static/index.html"), "utf-8"
  );
  const rule = css.match(/#toast\s*\{[^}]*\}/);
  assert.ok(rule, "#toast scroll-cap rule missing");
  assert.ok(/max-height/.test(rule[0]), "cap rule needs max-height");
  assert.ok(/overflow-y:\s*auto/.test(rule[0]), "cap rule needs overflow-y auto");
  assert.ok(/-webkit-overflow-scrolling:\s*touch/.test(rule[0]),
    "cap rule needs touch scrolling");
});

/* ---- 1.4 links hardened inside the toast ---- */

test("links_hardened_in_toast", async () => {
  const h = harness();
  await loadSnapshot(h);
  const html = h.reader.htmlFor("assistant", LINK_TEXT);
  h.toast.show("🔊 agente: mirá…", { html: html });
  const anchors = h.toastEl.querySelectorAll("a");
  assert.equal(anchors.length, 1);   // non-http scheme never survives as anchor
  assert.equal(anchors[0].getAttribute("href"), "https://ejemplo.com/doc");
  assert.equal(anchors[0].getAttribute("target"), "_blank");
  assert.equal(anchors[0].getAttribute("rel"), "noopener noreferrer");
  assert.ok(h.toastEl.textContent.includes("esto no"),
    "degraded link content survives as plain text");
});

/* ---- 1.5 replay teleprompter: chunkifyText (pure helper) ---- */

test("chunkify_short_text_single_window", () => {
  assert.deepEqual(chunkifyText("Hola mundo"), ["Hola mundo"]);
  assert.deepEqual(chunkifyText("Hola mundo", 80), ["Hola mundo"]);
  // Even below an explicit tiny limit, one window survives intact.
  assert.deepEqual(chunkifyText("Hola", 2), ["Hola"]);
});

test("chunkify_long_text_even_windows", () => {
  const text =
    "Primera frase del turno hablado. Segunda frase con mas palabras. " +
    "Tercera frase que sigue la lectura. Cuarta frase para llenar. " +
    "Quinta frase final del turno.";
  const chunks = chunkifyText(text, 40);
  assert.ok(chunks.length > 1, "long text splits into multiple windows");
  for (const c of chunks) assert.ok(c.length <= 40,
    `window exceeds approxChars: "${c}" (${c.length})`);
  // Sequential coverage: the windows reassemble the single-spaced text.
  assert.equal(chunks.join(" "), text);
  // Default limit (~80): the same text still windows, just coarser.
  const byDefault = chunkifyText(text);
  assert.ok(byDefault.length > 1);
  assert.equal(byDefault.join(" "), text);
  for (const c of byDefault) assert.ok(c.length <= 80);
});

test("chunkify_prefers_sentence_boundaries", () => {
  // The period is the latest boundary inside the window: the cut keeps
  // it with its sentence instead of landing mid-phrase.
  assert.deepEqual(chunkifyText("Una frase. Otra frase.", 12),
    ["Una frase.", "Otra frase."]);
  // Newline is a boundary too: lines never merge mid-window.
  assert.deepEqual(chunkifyText("línea uno\nlínea dos", 10),
    ["línea uno", "línea dos"]);
});

test("chunkify_never_mid_word_except_overlong", () => {
  // Word boundary wins: both words leave the splitter whole.
  assert.deepEqual(chunkifyText("palabra larguísima", 8),
    ["palabra", "larguísima"]);
  // A single word longer than the limit is kept whole (the one
  // tolerated over-limit window) instead of being sliced.
  assert.deepEqual(chunkifyText("supercalifragilistic", 10),
    ["supercalifragilistic"]);
  // The over-long word is followed by normal words: those still cut
  // cleanly at boundaries.
  assert.deepEqual(chunkifyText("corta supercalifragilistic tail", 8),
    ["corta", "supercalifragilistic", "tail"]);
});

test("chunkify_empty_and_whitespace", () => {
  assert.deepEqual(chunkifyText(""), []);
  assert.deepEqual(chunkifyText("   \n\t  "), []);
});
