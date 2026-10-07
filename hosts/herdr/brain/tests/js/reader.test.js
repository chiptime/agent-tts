/* node --test suite for the UMD reader client (reader-html-integration).
 *
 * The fake DOM below implements ONLY the Decision 1 enforced surface
 * (createElement/createTextNode, append/remove/replaceChild/insertBefore,
 * text.splitText, element.normalize, querySelector(All), attributes,
 * classList, textContent, set-only innerHTML with a write tracker,
 * parentNode, addEventListener) — a plain object literal, no jsdom, no new
 * dependencies. */

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
  const node = { nodeType: 3, _text: String(text), nodeValue: String(text), parentNode: null };
  node.splitText = offset => {
    const tail = makeText(node._text.slice(offset));
    node._text = node.nodeValue = node._text.slice(0, offset);
    const parent = node.parentNode;
    if (parent) {
      const i = parent.children.indexOf(node);
      if (i >= 0) {
        parent.children.splice(i + 1, 0, tail);
        tail.parentNode = parent;
      }
    }
    return tail;
  };
  return node;
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
  Object.defineProperty(node, "childNodes", { get: () => node.children });
  node.appendChild = child => {
    child.parentNode = node;
    node.children.push(child);
    return child;
  };
  node.insertBefore = (child, ref) => {
    // Real-DOM semantics: the child leaves its previous parent first.
    if (child.parentNode) {
      const old = child.parentNode.children.indexOf(child);
      if (old >= 0) child.parentNode.children.splice(old, 1);
    }
    const i = node.children.indexOf(ref);
    if (i === -1) return node.appendChild(child);
    node.children.splice(i, 0, child);
    child.parentNode = node;
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
  node.normalize = () => {
    const merged = [];
    for (const child of node.children) {
      const last = merged[merged.length - 1];
      if (child.nodeType === 3 && last && last.nodeType === 3) {
        last._text = last.nodeValue = last._text + child._text;
        child.parentNode = null;   // real DOM detaches merged-away nodes
      } else {
        merged.push(child);
      }
    }
    node.children = merged;
    for (const child of merged) {
      if (child.nodeType === 1 && child.normalize) child.normalize();
    }
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

/* Suffix extraction follows the current DOM boundary, not equal text or IDs. */
function suffixSurface(html) {
  const h = harness();
  const { content } = readerSurface(h.doc);
  h.reader.mountTurn(content, { text: "fallback", html });
  return { ...h, content };
}

test("suffix starts at the later repeated sentence and includes unanchored tail", () => {
  const { reader, content } = suffixSurface(
    '<p><span class="tts-sent" data-sent-idx="0">Echo.</span> ' +
    '<span class="tts-sent" data-sent-idx="1">Echo.</span></p><p>Final tail.</p>');
  assert.equal(reader.readableSuffix(content, 1), "Echo.\nFinal tail.");
});

test("suffix includes the primary and every inline continuation exactly once", () => {
  const { reader, content, doc } = suffixSurface(
    '<p><span class="tts-sent" data-sent-idx="0">Earlier.</span> ' +
    '<span class="tts-sent" data-sent-idx="1">Read <strong>this</strong></span>' +
    '<em class="tts-sent-cont" data-sent-idx="1"> emphasized</em>' +
    '<span class="tts-sent-cont" data-sent-idx="1"> ending.</span></p>' +
    '<p><span class="tts-sent" data-sent-idx="2">Next.</span></p>');
  const html = content.innerHTML;
  const text = content.textContent;
  const writes = doc.writeLog().length;
  assert.equal(reader.readableSuffix(content, "1"), "Read this emphasized ending.\nNext.");
  assert.equal(content.innerHTML, html);
  assert.equal(content.textContent, text);
  assert.equal(doc.writeLog().length, writes);
  assert.equal(content.querySelectorAll(".tts-selected").length, 0);
});

test("container anchors traverse nested lists, cells and code without duplication", () => {
  const { reader, content } = suffixSurface(
    '<p><span class="tts-sent" data-sent-idx="0">Earlier.</span></p>' +
    '<ul class="tts-sent" data-sent-idx="1"><li>One</li><li>Two <em>items</em></li></ul>' +
    '<table class="tts-sent" data-sent-idx="2"><tr><td>A</td><td>B</td></tr></table>' +
    '<pre class="tts-sent" data-sent-idx="3"><code>print("safe")</code></pre>');
  assert.equal(reader.readableSuffix(content, 1), 'One\nTwo items\nA B\nprint("safe")');
  assert.equal(reader.readableSuffix(content, 2), 'A B\nprint("safe")');
  assert.equal(reader.readableSuffix(content, 3), 'print("safe")');
});

test("nested primary and continuation anchors do not duplicate their descendant text", () => {
  const { reader, content } = suffixSurface(
    '<p><span class="tts-sent" data-sent-idx="0">Earlier.</span></p>' +
    '<p class="tts-sent" data-sent-idx="1">Read <strong class="tts-sent-cont" data-sent-idx="1">' +
    '<span class="tts-sent" data-sent-idx="1">this</span></strong> once.</p><p>Tail.</p>');
  assert.equal(reader.readableSuffix(content, 1), "Read this once.\nTail.");
});

test("suffix is scoped to the current owning container with duplicate sentence IDs", () => {
  const one = suffixSurface('<p class="tts-sent" data-sent-idx="1" id="tts-sent-1">First.</p>');
  const two = suffixSurface('<p class="tts-sent" data-sent-idx="1" id="tts-sent-1">Second.</p>');
  assert.equal(one.reader.readableSuffix(two.content, 1), "Second.");
  two.reader.mountTurn(two.content, { html: '<p class="tts-sent" data-sent-idx="1">Current.</p>' });
  assert.equal(two.reader.readableSuffix(two.content, 1), "Current.");
});

test("invalid or continuation-only boundaries cannot silently read the whole turn", () => {
  const { reader, content } = suffixSurface(ANCHORED_HTML);
  for (const index of [-1, 99, 0.5, NaN, null, undefined, '0"]', "", " 0"]) {
    assert.equal(reader.readableSuffix(content, index), "", String(index));
  }
  assert.equal(reader.readableSuffix(null, 0), "");
  const orphan = suffixSurface('<span class="tts-sent-cont" data-sent-idx="0">Orphan.</span>');
  assert.equal(orphan.reader.readableSuffix(orphan.content, 0), "");
});

/* Progress maps use synthesis offsets, not text identity or a second clock. */
const Karaoke = require("../../src/herdr_brain/static/karaoke.js");

test("readable replay maps repeated sentences in ordered UTF-16 raw ranges", () => {
  const { reader, content } = suffixSurface(
    '<p class="tts-sent" data-sent-idx="0">Echo.</p>' +
    '<p class="tts-sent" data-sent-idx="1">Echo.</p>' +
    '<p class="tts-sent" data-sent-idx="2">Echo.</p>');
  const replay = reader.readableReplay(content, 1);
  assert.equal(replay.rawText, reader.readableSuffix(content, 1));
  assert.equal(replay.rawText, "Echo.\nEcho.");
  assert.deepEqual(replay.sentences, [
    { sentIdx: 1, raw: [0, 6] }, { sentIdx: 2, raw: [6, 11] }
  ]);
  for (const record of [replay, replay.sentences, ...replay.sentences,
    ...replay.sentences.map(sentence => sentence.raw), replay.sources, ...replay.sources]) {
    assert.ok(Object.isFrozen(record));
  }
});

test("readable replay merges continuations and nested same-index anchors without duplicate text", () => {
  const { reader, content, doc } = suffixSurface(
    '<p class="tts-sent" data-sent-idx="1">Read <strong class="tts-sent-cont" data-sent-idx="1">' +
    '<span class="tts-sent" data-sent-idx="1">this</span></strong></p>' +
    '<p class="tts-sent-cont" data-sent-idx="1">once.</p>' +
    '<p class="tts-sent" data-sent-idx="2">Next.</p>');
  const writes = doc.writeLog().length;
  const replay = reader.readableReplay(content, 1);
  assert.equal(replay.rawText, "Read this\nonce.\nNext.");
  assert.deepEqual(replay.sentences, [
    { sentIdx: 1, raw: [0, 16] }, { sentIdx: 2, raw: [16, 21] }
  ]);
  assert.equal(reader.selectSentence(content, reader.sentenceForChunk(replay, { raw: [10, 16] })), 4);
  assert.equal(doc.writeLog().length, writes, "mapping and selection do not replace HTML");
});

test("readable replay keeps unanchored text and block separators in the preceding sentence range", () => {
  const { reader, content } = suffixSurface(
    '<p class="tts-sent" data-sent-idx="1">Start.</p><p>Between\t  anchors.</p>' +
    '<table class="tts-sent" data-sent-idx="2"><tr><td>A</td><td>B</td></tr></table>' +
    '<p>Unanchored tail.</p>');
  const replay = reader.readableReplay(content, 1);
  const first = "Start.\nBetween anchors.\n";
  const second = "A B\nUnanchored tail.";
  assert.equal(replay.rawText, first + second);
  assert.deepEqual(replay.sentences, [
    { sentIdx: 1, raw: [0, first.length] },
    { sentIdx: 2, raw: [first.length, first.length + second.length] }
  ]);
  assert.equal(reader.sentenceForChunk(replay, { raw: [7, first.length] }), 1);
  assert.equal(reader.sentenceForChunk(replay, { raw: [first.length, replay.rawText.length] }), 2);
});

test("full readable replay and suffix extraction share one normalization with Unicode offsets", () => {
  const { reader, content } = suffixSurface(
    '<p>  Intro\t </p><p class="tts-sent" data-sent-idx="0">\t😀\u00a0cafe\u0301.  </p>' +
    '<p class="tts-sent" data-sent-idx="1">\r\n Next. </p>');
  const full = reader.readableReplay(content);
  assert.equal(full.rawText, "Intro\n😀 cafe\u0301.\nNext.");
  assert.deepEqual(full.sentences, [
    { sentIdx: 0, raw: [6, 16] }, { sentIdx: 1, raw: [16, 21] }
  ]);
  const suffix = reader.readableReplay(content, 1);
  assert.equal(suffix.rawText, "Next.");
  assert.equal(suffix.rawText, reader.readableSuffix(content, 1));
  assert.deepEqual(suffix.sentences, [{ sentIdx: 1, raw: [0, 5] }]);
  assert.equal(reader.sentenceForChunk(full, { raw: [0, 6] }), null, "unanchored intro has no invented sentence");
});

test("readable replay skips hidden controls and cannot start at an excluded or orphan boundary", () => {
  const { reader, content } = suffixSurface(
    '<p class="tts-sent" data-sent-idx="0" hidden="">Hidden.</p>' +
    '<p class="tts-sent" data-sent-idx="1">Visible.<button>Copy</button>' +
    '<span aria-hidden="true">Decoration</span></p>' +
    '<p class="tts-sent-cont" data-sent-idx="2">Orphan.</p>');
  const replay = reader.readableReplay(content, 1);
  assert.equal(replay.rawText, "Visible.\nOrphan.");
  assert.deepEqual(replay.sentences, [
    { sentIdx: 1, raw: [0, 9] }, { sentIdx: 2, raw: [9, 16] }
  ]);
  for (const index of [0, 2, -1, null, "", "01", 0.5, Number.MAX_SAFE_INTEGER + 1]) {
    assert.deepEqual(reader.readableReplay(content, index), { rawText: "", sentences: [], sources: [] });
  }
  assert.deepEqual(reader.readableReplay(null), { rawText: "", sentences: [], sources: [] });
});

test("one popup chunk spanning sentences selects its first overlapping DOM range", () => {
  const { reader, content } = suffixSurface(
    '<p class="tts-sent" data-sent-idx="0">Earlier.</p>' +
    '<p class="tts-sent" data-sent-idx="1">First.</p>' +
    '<p class="tts-sent" data-sent-idx="2">Second.</p>');
  const replay = reader.readableReplay(content, 1);
  const plan = Karaoke.createPlan("short-suffix", replay.rawText);
  assert.equal(plan.chunks.length, 1);
  assert.equal(reader.sentenceForChunk(replay, plan.chunks[0]), 1,
    "do not redistribute this one window's duration between DOM sentences");
  for (const chunk of [null, { raw: [replay.rawText.length, replay.rawText.length + 1] }]) {
    assert.equal(reader.sentenceForChunk(replay, chunk), null);
  }
});

test("several popup chunks inside one DOM sentence keep its shared sentence index", () => {
  const sentence = "Readable words without sentence punctuation ".repeat(8).trim();
  const { reader, content } = suffixSurface(
    '<p class="tts-sent" data-sent-idx="4">' + sentence + '</p>' +
    '<p class="tts-sent" data-sent-idx="5">Next.</p>');
  const replay = reader.readableReplay(content, 4);
  const plan = Karaoke.createPlan("long-sentence", replay.rawText);
  assert.ok(plan.chunks.length > 3);
  for (const chunk of plan.chunks) {
    const expected = chunk.raw[0] < sentence.length + 1 ? 4 : 5;
    assert.equal(reader.sentenceForChunk(replay, chunk), expected);
  }
});

/* ---- shared popup-chunk DOM ranges: green paints the chunk window ---- */

const TAIL_ESTA = "Esta sesión queda protegida, junto con Whisper y el servidor de síntesis.";
const TAIL_LAS = "Las terminales antiguas de las pruebas consumen muy poco; cerrarlas apenas ayudaría.";
const TAIL_QUESTION = "¿Cuáles de estas sesiones puedes cerrar sin interrumpir trabajo que quieras conservar?";
const TAIL_REQUEST = "Indícame sus PID o terminales; si todas siguen trabajando, no cerraremos ninguna.";
const TAIL_HTML =
  '<p class="tts-sent" data-sent-idx="9">' + TAIL_ESTA + '</p>' +
  '<p class="tts-sent" data-sent-idx="10">' + TAIL_LAS + '</p>' +
  '<p class="tts-sent" data-sent-idx="11">' + TAIL_QUESTION + " " + TAIL_REQUEST + '</p>';
const norm = text => text.replace(/\s+/g, " ").trim();

function tailWindows(reader, content) {
  const replay = reader.readableReplay(content, 9);
  assert.equal(replay.rawText,
    TAIL_ESTA + "\n" + TAIL_LAS + "\n" + TAIL_QUESTION + " " + TAIL_REQUEST);
  const plan = Karaoke.createPlan("tail-suffix", replay.rawText);
  assert.ok(plan.chunks.length >= 6, "the exact tail spans several popup windows");
  const question = plan.chunks.find(chunk =>
    chunk.popupText.includes("quieras") && !chunk.popupText.includes("Indícame"));
  const request = plan.chunks.find(chunk => chunk.popupText.includes("no cerraremos ninguna"));
  assert.ok(question && request && question.globalIndex < request.globalIndex,
    "deterministic question and request windows over the exact tail");
  return { replay, plan, question, request };
}

test("a shared-anchor popup window maps to its exact DOM text portion, not the whole anchor", () => {
  const { reader, content } = suffixSurface(TAIL_HTML);
  const { replay, question } = tailWindows(reader, content);
  const runs = reader.rawRanges(replay, question.raw[0], question.raw[1]);
  assert.ok(runs.length >= 1, "the window covers visible leaf text");
  const runText = runs.map(run => run.node.nodeValue.slice(run.start, run.end)).join(" ");
  assert.equal(norm(runText), norm(question.popupText));
  assert.ok(!runText.includes("Indícame"), "the question window must not cover the request text");
  assert.ok(!runText.includes("Las terminales"), "the window stays inside its raw range");
});

test("painting a chunk window marks only the owned leaves and clears without a trace", () => {
  const { reader, content } = suffixSurface(TAIL_HTML);
  const { replay, question } = tailWindows(reader, content);
  const textBefore = content.textContent;
  const painted = reader.paintRawRange(content, replay, question.raw[0], question.raw[1]);
  assert.ok(painted >= 1, "at least one leaf run is painted");
  const anchor = content.querySelector('[data-sent-idx="11"]');
  assert.equal(anchor.classList.contains("tts-selected"), false,
    "the shared owning anchor must not be painted wholesale");
  const marks = content.querySelectorAll(".tts-range");
  assert.equal(marks.length, painted);
  for (const mark of marks) {
    assert.ok(mark.classList.contains("tts-selected"), "marks reuse the existing green CSS class");
    assert.ok(mark.getAttribute("data-tts-range") !== null, "marks carry ownership");
  }
  const green = Array.from(marks).map(mark => mark.textContent).join(" ");
  assert.equal(norm(green), norm(question.popupText));
  assert.equal(content.textContent, textBefore, "painting never changes the visible text");
  reader.clearPaintedRanges(content);
  assert.equal(content.querySelectorAll(".tts-range").length, 0);
  assert.equal(anchor.children.length, 1, "unwrapping heals the split text nodes");
  assert.equal(anchor.children[0].nodeType, 3);
  assert.equal(anchor.children[0].nodeValue, TAIL_QUESTION + " " + TAIL_REQUEST);
  assert.equal(content.textContent, textBefore);
});

test("repainting the current window is idempotent and replaces cleanly on advance", () => {
  const { reader, content } = suffixSurface(TAIL_HTML);
  const { replay, question, request } = tailWindows(reader, content);
  const painted = reader.paintRawRange(content, replay, question.raw[0], question.raw[1]);
  // Progress fires repeatedly inside one window: no mark may accumulate.
  reader.paintRawRange(content, replay, question.raw[0], question.raw[1]);
  reader.paintRawRange(content, replay, question.raw[0], question.raw[1]);
  assert.equal(content.querySelectorAll(".tts-range").length, painted);
  // Marks are transparent to the ownership extraction (host re-extracts each sync).
  const fresh = reader.readableReplay(content, 9);
  assert.equal(fresh.rawText, replay.rawText);
  assert.deepEqual(fresh.sentences, replay.sentences);
  const moved = reader.paintRawRange(content, fresh, request.raw[0], request.raw[1]);
  assert.ok(moved >= 1);
  const green = Array.from(content.querySelectorAll(".tts-range")).map(mark => mark.textContent).join(" ");
  assert.equal(norm(green), norm(request.popupText));
  assert.ok(!green.includes("¿Cuáles"), "the later window leaves the question unpainted");
});

test("a window spanning several anchors paints contiguous runs without unrelated gaps", () => {
  const { reader, content } = suffixSurface(
    '<p><span class="tts-sent" data-sent-idx="1">Read <strong>this</strong></span>' +
    '<em class="tts-sent-cont" data-sent-idx="1"> emphasized</em>' +
    '<span class="tts-sent-cont" data-sent-idx="1"> ending.</span></p>' +
    '<p><span class="tts-sent" data-sent-idx="2">Next phrase.</span></p>' +
    '<ul class="tts-sent" data-sent-idx="3"><li>One</li><li>Two <em>items</em></li></ul>' +
    '<table class="tts-sent" data-sent-idx="4"><tr><td>A</td><td>B</td></tr></table>' +
    '<pre class="tts-sent" data-sent-idx="5"><code>print("safe")</code></pre><p>Final tail.</p>');
  const replay = reader.readableReplay(content, 1);
  const plan = Karaoke.createPlan("multi-anchor", replay.rawText);
  const window = plan.chunks.find(chunk => chunk.popupText.includes("Final tail"));
  assert.ok(window, "deterministic multi-anchor window");
  const runs = reader.rawRanges(replay, window.raw[0], window.raw[1]);
  assert.ok(runs.length > 1, "the window spans several DOM leaves");
  const runText = runs.map(run => run.node.nodeValue.slice(run.start, run.end)).join(" ");
  assert.equal(norm(runText), norm(window.popupText));
  assert.ok(!runText.includes("Read") && !runText.includes("Next phrase"),
    "earlier anchors stay unpainted");
  const painted = reader.paintRawRange(content, replay, window.raw[0], window.raw[1]);
  assert.equal(painted, runs.length);
  const green = Array.from(content.querySelectorAll(".tts-range")).map(mark => mark.textContent).join(" ");
  assert.equal(norm(green), norm(window.popupText));
});

test("collapsed DOM whitespace stays inside one painted run", () => {
  const { reader, content } = suffixSurface(
    '<p class="tts-sent" data-sent-idx="0">alpha   beta</p>' +
    '<p class="tts-sent" data-sent-idx="1">Next.</p>');
  const replay = reader.readableReplay(content, 0);
  assert.equal(replay.rawText, "alpha beta\nNext.");
  const runs = reader.rawRanges(replay, 0, "alpha beta".length);
  assert.equal(runs.length, 1, "one leaf run spans the collapsed spaces");
  assert.equal(runs[0].node.nodeValue.slice(runs[0].start, runs[0].end), "alpha   beta",
    "the DOM leaf keeps its own whitespace inside the run");
});

test("long Unicode pieces and repeated sentences paint the owning occurrence", () => {
  const texts = Array.from({ length: 300 }, (_, i) =>
    `Frase ${String(i).padStart(3, "0")} 😀 con acentos áéíóú para llenar una ventana del popup.`);
  const html = texts.map((text, i) => `<p class="tts-sent" data-sent-idx="${i}">${text}</p>`).join("");
  const { reader, content } = suffixSurface(html);
  const replay = reader.readableReplay(content, 1);
  const plan = Karaoke.createPlan("unicode-pieces", replay.rawText);
  assert.ok(plan.pieces.length > 2, "the suffix spans several TTS pieces");
  const late = plan.chunks[plan.chunks.length - 1];
  const painted = reader.paintRawRange(content, replay, late.raw[0], late.raw[1]);
  assert.ok(painted >= 1);
  const green = Array.from(content.querySelectorAll(".tts-range")).map(mark => mark.textContent).join(" ");
  assert.equal(norm(green), norm(late.popupText));
  assert.ok(green.includes("😀"), "UTF-16 offsets keep astral glyphs intact");
  reader.clearPaintedRanges(content);
  assert.equal(content.querySelectorAll(".tts-range").length, 0);
  // Repeated identical sentences paint only the clicked boundary's occurrence.
  const echo = suffixSurface(
    '<p class="tts-sent" data-sent-idx="0">Echo.</p>' +
    '<p class="tts-sent" data-sent-idx="1">Echo.</p>');
  const echoReplay = echo.reader.readableReplay(echo.content, 1);
  const echoRuns = echo.reader.rawRanges(echoReplay, 0, 5);
  assert.equal(echoRuns.length, 1);
  assert.equal(echoRuns[0].node.parentNode,
    echo.content.querySelector('[data-sent-idx="1"]'),
    "the later DOM occurrence owns the window, not the repeated text");
});

test("clearing painted ranges leaves anchor-level selections untouched", () => {
  const { reader, content } = suffixSurface(TAIL_HTML);
  reader.selectSentence(content, 9);
  const { replay, question } = tailWindows(reader, content);
  reader.paintRawRange(content, replay, question.raw[0], question.raw[1]);
  reader.clearPaintedRanges(content);
  assert.equal(content.querySelectorAll(".tts-range").length, 0);
  assert.ok(content.querySelector('[data-sent-idx="9"]').classList.contains("tts-selected"),
    "host-owned anchor selections survive range clearing");
});

test("painting rejects leaf sources extracted from a foreign attached container", () => {
  const h = harness();
  const one = makeElement("div"); one.classList.add("reader-content");
  const two = makeElement("div"); two.classList.add("reader-content");
  h.reader.mountTurn(one, { text: "x", html: TAIL_HTML });
  h.reader.mountTurn(two, { text: "x", html: TAIL_HTML });
  const ownerText = one.textContent;
  const foreignText = two.textContent;
  const foreign = h.reader.readableReplay(two, 9);
  assert.ok(foreign.sources.length > 0, "precondition: foreign replay holds leaf origins");
  // A replay from ANOTHER attached response must never paint anywhere:
  // neither into its own (foreign) container nor into the target owner.
  const painted = h.reader.paintRawRange(one, foreign, 0, foreign.rawText.length);
  assert.equal(painted, 0, "foreign origins paint nothing");
  assert.equal(two.querySelectorAll(".tts-range").length, 0, "foreign container untouched");
  assert.equal(one.querySelectorAll(".tts-range").length, 0, "owner container untouched");
  assert.equal(one.textContent, ownerText);
  assert.equal(two.textContent, foreignText);
});

test("mixed-owner sources paint only the target container's own leaves", () => {
  const h = harness();
  const one = makeElement("div"); one.classList.add("reader-content");
  const two = makeElement("div"); two.classList.add("reader-content");
  h.reader.mountTurn(one, { text: "x", html: TAIL_HTML });
  h.reader.mountTurn(two, { text: "x", html: TAIL_HTML });
  const own = h.reader.readableReplay(one, 9);
  const foreign = h.reader.readableReplay(two, 9);
  // Hand-built replay whose early sources are the owner's and whose late
  // sources belong to the other response: only own leaves may be painted.
  const mixed = {
    rawText: own.rawText,
    sentences: own.sentences,
    sources: own.sources.map((origin, i) => (i < 4 ? origin : foreign.sources[i]))
  };
  const painted = h.reader.paintRawRange(one, mixed, 0, mixed.sources.length);
  assert.ok(painted >= 1, "own leaves still paint");
  assert.equal(two.querySelectorAll(".tts-range").length, 0, "foreign container untouched");
  const marks = one.querySelectorAll(".tts-range");
  for (const mark of marks) {
    let ancestor = mark.parentNode;
    while (ancestor && ancestor !== one) ancestor = ancestor.parentNode;
    assert.ok(ancestor === one, "every painted mark lives inside the owner container");
  }
});

test("equal sentence maps select only the owning container and survive a same-content remount", () => {
  const html = '<p class="tts-sent" data-sent-idx="1" id="tts-sent-1">Echo.</p>' +
    '<p class="tts-sent" data-sent-idx="2" id="tts-sent-2">Next.</p>';
  const one = suffixSurface(html);
  const two = suffixSurface(html);
  const replay = two.reader.readableReplay(two.content, 1);
  const chunk = { raw: [6, replay.rawText.length] };
  two.reader.selectSentence(two.content, two.reader.sentenceForChunk(replay, chunk));
  assert.equal(one.content.querySelectorAll(".tts-selected").length, 0);
  assert.equal(two.content.querySelector(".tts-selected").getAttribute("data-sent-idx"), "2");
  two.reader.mountTurn(two.content, { html });
  const remounted = two.reader.readableReplay(two.content, 1);
  // The sentence mapping survives the remount; the DOM origins are the
  // remount's fresh leaves, so range painting rebinds instead of reusing
  // detached nodes.
  assert.equal(remounted.rawText, replay.rawText);
  assert.deepEqual(remounted.sentences, replay.sentences);
  assert.notEqual(remounted.sources[0].node, replay.sources[0].node);
  two.reader.selectSentence(two.content, two.reader.sentenceForChunk(replay, chunk));
  assert.equal(two.content.querySelector(".tts-selected").getAttribute("data-sent-idx"), "2");
  two.content.querySelector('[data-sent-idx="2"]').setAttribute("data-sent-idx", "7");
  assert.notDeepEqual(two.reader.readableReplay(two.content, 1).sentences, replay.sentences,
    "same text with changed indices is not the original mapping");
});

test("controller progress maps >8000-character pieces using the popup's original global raw offsets", async () => {
  const texts = Array.from({ length: 230 }, (_, i) =>
    `Sentence ${String(i).padStart(3, "0")} has enough distinct readable words to fill one popup window.`);
  const html = texts.map((text, i) => `<p class="tts-sent" data-sent-idx="${i}">${text}</p>`).join("");
  const { reader, content } = suffixSurface(html);
  const replay = reader.readableReplay(content, 1);
  const plan = Karaoke.createPlan("many-pieces", replay.rawText);
  assert.ok(plan.pieces.length > 2);
  assert.equal(plan.chunks.length, texts.length - 1);
  const loads = [];
  const events = [];
  const ctl = Karaoke.createController({
    fetch: async () => ({ ok: true, json: async () => ({ audio_url: "/audio/fixture.mp3" }) }),
    player: { load(item, callbacks) {
      const handle = { duration: 100, currentTime: 0, play: async () => {}, dispose() {} };
      loads.push({ item, callbacks, handle });
      return handle;
    } },
    onProgress(event) { events.push(event); }
  });
  ctl.select(plan);
  for (const piece of plan.pieces) {
    await flush();
    const load = loads.at(-1);
    assert.equal(load.item.pieceIndex, piece.pieceIndex);
    for (const localIndex of [0, Math.floor(piece.chunks.length * 0.6), piece.chunks.length - 1]) {
      load.handle.currentTime = (localIndex + 0.2) / piece.chunks.length * 100;
      load.callbacks.timeupdate();
      const event = events.at(-1);
      const globalIndex = piece.chunkOffset + localIndex;
      assert.equal(event.activeGlobalIndex, globalIndex);
      assert.equal(event.chunk, plan.chunks[globalIndex]);
      assert.equal(reader.sentenceForChunk(replay, event.chunk), globalIndex + 1);
      assert.equal(event.chunk.popupText, texts[globalIndex + 1]);
    }
    load.callbacks.ended();
  }
  assert.equal(events.at(-1).chunk, null);
  assert.equal(ctl.state().phase, "idle");
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
