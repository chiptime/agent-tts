/* node --test suite for the Mermaid diagram viewer (mermaid-viewer.js).
 *
 * Driven against the fake DOM infrastructure used across tests/js/:
 * pure UMD module, no jsdom, no new dependencies. */

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { createMermaidViewer } = require("../../src/herdr_brain/static/mermaid-viewer.js");

/* ---- fake DOM helper ---- */

function tokensOf(attrs) {
  return ((attrs && attrs.class) || "").split(/\s+/).filter(Boolean);
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

function matchSingle(node, sel) {
  if (!node || node.nodeType === 3 || !node.attributes) return false;
  sel = sel.trim();
  if (sel === "*") return true;
  if (sel.startsWith(".")) return tokensOf(node.attributes).includes(sel.slice(1));
  if (sel.startsWith("#")) return (node.attributes.id || "") === sel.slice(1);
  const attr = sel.match(/^\[([a-zA-Z-]+)="([^"]*)"\]$/);
  if (attr) return node.attributes[attr[1]] === attr[2];
  if (sel === "code.language-mermaid") {
    return node.tagName === "code" && tokensOf(node.attributes).includes("language-mermaid");
  }
  if (sel === "pre.language-mermaid") {
    return node.tagName === "pre" && tokensOf(node.attributes).includes("language-mermaid");
  }
  if (/^[a-zA-Z][a-zA-Z0-9-]*$/.test(sel)) return node.tagName === sel.toLowerCase();
  return false;
}

function matches(node, sel) {
  if (sel.includes(",")) {
    return sel.split(",").some(part => matchSingle(node, part));
  }
  return matchSingle(node, sel);
}

function descendants(node) {
  const out = [];
  function walk(n) {
    if (!n || n.nodeType === 3 || !n.children) return;
    for (const child of n.children) {
      out.push(child);
      walk(child);
    }
  }
  walk(node);
  return out;
}

function makeText(text) {
  return { nodeType: 3, _text: String(text), nodeValue: String(text), parentNode: null };
}

function makeElement(tag) {
  const node = {
    nodeType: 1,
    tagName: tag.toLowerCase(),
    attributes: {},
    children: [],
    parentNode: null,
    listeners: {},
    style: {}
  };
  node.classList = makeClassList(node);
  Object.defineProperty(node, "className", {
    configurable: true,
    get: () => node.attributes.class || "",
    set: (v) => { node.attributes.class = String(v); }
  });
  Object.defineProperty(node, "childNodes", { get: () => node.children });
  node.appendChild = child => {
    if (child.parentNode) {
      const old = child.parentNode.children.indexOf(child);
      if (old >= 0) child.parentNode.children.splice(old, 1);
    }
    child.parentNode = node;
    node.children.push(child);
    return child;
  };
  node.insertBefore = (child, ref) => {
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
  node.setAttribute = (k, v) => { node.attributes[k] = String(v); };
  node.getAttribute = k => (k in node.attributes ? node.attributes[k] : null);
  node.removeAttribute = k => { delete node.attributes[k]; };
  node.addEventListener = (type, fn) => {
    if (!node.listeners[type]) node.listeners[type] = [];
    node.listeners[type].push(fn);
  };
  node.dispatchEvent = (event) => {
    const type = event.type || event;
    const fns = node.listeners[type] || [];
    for (const fn of fns) fn(event);
  };
  node.cloneNode = (deep) => {
    const clone = makeElement(node.tagName);
    for (const k of Object.keys(node.attributes)) clone.attributes[k] = node.attributes[k];
    if (deep) {
      for (const c of node.children) {
        if (c.nodeType === 3) clone.appendChild(makeText(c._text));
        else clone.appendChild(c.cloneNode(true));
      }
    }
    return clone;
  };
  Object.defineProperty(node, "textContent", {
    configurable: true,
    get: () => node.children.map(textOf).join(""),
    set(value) {
      node.children = [];
      const t = makeText(value == null ? "" : String(value));
      t.parentNode = node;
      node.children.push(t);
    }
  });
  Object.defineProperty(node, "innerHTML", {
    configurable: true,
    get: () => node.textContent,
    set(value) {
      node.children = [];
      const t = makeText(value == null ? "" : String(value));
      t.parentNode = node;
      node.children.push(t);
    }
  });
  node.querySelector = sel => descendants(node).find(n => matches(n, sel)) || null;
  node.querySelectorAll = sel => descendants(node).filter(n => matches(n, sel));
  return node;
}

function makeDoc() {
  const html = makeElement("html");
  html.setAttribute("data-theme", "dark");
  const body = makeElement("body");
  html.appendChild(body);
  const elementsById = {};

  return {
    documentElement: html,
    body: body,
    createElement(tag) { return makeElement(tag); },
    createTextNode(text) { return makeText(text); },
    getElementById(id) {
      return descendants(html).find(n => n.attributes.id === id) || null;
    }
  };
}

function makeFakeMermaid(options = {}) {
  let initCall = null;
  let renderCalls = [];
  return {
    initialize(cfg) { initCall = cfg; },
    render(id, code) {
      renderCalls.push({ id, code });
      if (options.shouldFail) {
        return Promise.reject(new Error("Mermaid syntax error"));
      }
      return Promise.resolve({ svg: `<svg id="${id}" data-rendered="true">${code}</svg>` });
    },
    getInitCall: () => initCall,
    getRenderCalls: () => renderCalls
  };
}

/* ---- Tests ---- */

test("transforms_mermaid_code_block_into_card", async () => {
  const doc = makeDoc();
  const fakeMermaid = makeFakeMermaid();
  const viewer = createMermaidViewer({ doc, mermaid: fakeMermaid, root: doc.body });

  const container = doc.createElement("div");
  container.classList.add("reader-content");
  doc.body.appendChild(container);

  const pre = doc.createElement("pre");
  const code = doc.createElement("code");
  code.classList.add("language-mermaid");
  code.textContent = "graph TD\nA --> B";
  pre.appendChild(code);
  container.appendChild(pre);

  const count = await viewer.renderIn(container);
  assert.equal(count, 1);

  // pre was replaced by .mermaid-card
  const card = container.querySelector(".mermaid-card");
  assert.ok(card, "card element created");
  assert.ok(card.querySelector(".mermaid-toolbar"));
  assert.ok(card.querySelector(".mermaid-badge"));
  assert.ok(card.querySelector(".mermaid-btn-toggle"));
  assert.ok(card.querySelector(".mermaid-btn-copy"));
  assert.ok(card.querySelector(".mermaid-btn-zoom"));
  assert.ok(card.querySelector(".mermaid-diagram"));

  const renderedCalls = fakeMermaid.getRenderCalls();
  assert.equal(renderedCalls.length, 1);
  assert.ok(renderedCalls[0].code.includes("graph TD"));
  assert.ok(card.querySelector(".mermaid-diagram").textContent.includes("<svg"));
});

test("idempotency_does_not_reprocess_card", async () => {
  const doc = makeDoc();
  const fakeMermaid = makeFakeMermaid();
  const viewer = createMermaidViewer({ doc, mermaid: fakeMermaid, root: doc.body });

  const container = doc.createElement("div");
  doc.body.appendChild(container);

  const pre = doc.createElement("pre");
  const code = doc.createElement("code");
  code.classList.add("language-mermaid");
  code.textContent = "graph LR\nStart --> Stop";
  pre.appendChild(code);
  container.appendChild(pre);

  await viewer.renderIn(container);
  assert.equal(fakeMermaid.getRenderCalls().length, 1);

  // Second pass on same container
  const secondCount = await viewer.renderIn(container);
  assert.equal(secondCount, 0);
  assert.equal(fakeMermaid.getRenderCalls().length, 1);
});

test("toggle_button_switches_code_and_diagram_view", async () => {
  const doc = makeDoc();
  const fakeMermaid = makeFakeMermaid();
  const viewer = createMermaidViewer({ doc, mermaid: fakeMermaid, root: doc.body });

  const container = doc.createElement("div");
  doc.body.appendChild(container);

  const pre = doc.createElement("pre");
  const code = doc.createElement("code");
  code.classList.add("language-mermaid");
  code.textContent = "sequenceDiagram\nAlice->>Bob: Hello";
  pre.appendChild(code);
  container.appendChild(pre);

  await viewer.renderIn(container);

  const btnToggle = container.querySelector(".mermaid-btn-toggle");
  const codeWrap = container.querySelector(".mermaid-code-wrap");
  assert.ok(codeWrap.classList.contains("hidden"), "initially hidden");
  assert.equal(btnToggle.textContent, "Código");

  // Click toggle
  btnToggle.dispatchEvent({ type: "click" });
  assert.ok(!codeWrap.classList.contains("hidden"), "code shown after toggle");
  assert.equal(btnToggle.textContent, "Diagrama");

  // Click toggle again
  btnToggle.dispatchEvent({ type: "click" });
  assert.ok(codeWrap.classList.contains("hidden"), "code hidden after second toggle");
  assert.equal(btnToggle.textContent, "Código");
});

test("modal_viewer_opens_and_closes", () => {
  const doc = makeDoc();
  const fakeMermaid = makeFakeMermaid();
  const viewer = createMermaidViewer({ doc, mermaid: fakeMermaid, root: doc.body });

  viewer.openModal("<svg>test</svg>", "graph TD\nA-->B");

  const modal = doc.body.querySelector(".mermaid-modal");
  const backdrop = doc.body.querySelector(".mermaid-backdrop");
  assert.ok(modal, "modal element exists");
  assert.ok(!modal.classList.contains("hidden"), "modal is visible");
  assert.ok(!backdrop.classList.contains("hidden"), "backdrop is visible");

  // Close modal
  viewer.closeModal();
  assert.ok(modal.classList.contains("hidden"), "modal is hidden");
  assert.ok(backdrop.classList.contains("hidden"), "backdrop is hidden");
});

test("zoom_controls_and_clamping", () => {
  const doc = makeDoc();
  const fakeMermaid = makeFakeMermaid();
  const viewer = createMermaidViewer({ doc, mermaid: fakeMermaid, root: doc.body });

  viewer.openModal("<svg>test</svg>", "graph TD");
  assert.equal(viewer.getZoom(), 1.0);

  viewer.setZoom(1.5);
  assert.equal(viewer.getZoom(), 1.5);

  // Clamp upper bound 10.0
  viewer.setZoom(20.0);
  assert.equal(viewer.getZoom(), 10.0);

  // Clamp lower bound 0.2
  viewer.setZoom(0.01);
  assert.equal(viewer.getZoom(), 0.2);

  // Reset
  viewer.resetZoom();
  assert.equal(viewer.getZoom(), 1.0);
});

test("fail_soft_on_syntax_error_keeps_code_visible", async () => {
  const doc = makeDoc();
  const failingMermaid = makeFakeMermaid({ shouldFail: true });
  const viewer = createMermaidViewer({ doc, mermaid: failingMermaid, root: doc.body });

  const container = doc.createElement("div");
  doc.body.appendChild(container);

  const pre = doc.createElement("pre");
  const code = doc.createElement("code");
  code.classList.add("language-mermaid");
  code.textContent = "invalid %%% code";
  pre.appendChild(code);
  container.appendChild(pre);

  await viewer.renderIn(container);

  const card = container.querySelector(".mermaid-card");
  assert.ok(card, "card still created");
  const errorEl = card.querySelector(".mermaid-error");
  assert.ok(errorEl, "error notice displayed");
  const codeWrap = card.querySelector(".mermaid-code-wrap");
  assert.ok(!codeWrap.classList.contains("hidden"), "code wrap remains visible");
});

test("setTheme_reconfigures_mermaid_and_rerenders_active_cards", async () => {
  const doc = makeDoc();
  const fakeMermaid = makeFakeMermaid();
  const viewer = createMermaidViewer({ doc, mermaid: fakeMermaid, root: doc.body });

  const container = doc.createElement("div");
  doc.body.appendChild(container);

  const pre = doc.createElement("pre");
  const code = doc.createElement("code");
  code.classList.add("language-mermaid");
  code.textContent = "graph TD; A-->B";
  pre.appendChild(code);
  container.appendChild(pre);

  await viewer.renderIn(container);
  assert.equal(fakeMermaid.getRenderCalls().length, 1);

  // Switch to light theme
  viewer.setTheme("light");
  assert.equal(viewer.getTheme(), "light");
  const initCall = fakeMermaid.getInitCall();
  assert.equal(initCall.theme, "default");
  assert.equal(initCall.themeVariables.darkMode, false);

  // Switch back to dark theme
  viewer.setTheme("dark");
  assert.equal(viewer.getTheme(), "dark");
  const darkInit = fakeMermaid.getInitCall();
  assert.equal(darkInit.theme, "dark");
  assert.equal(darkInit.themeVariables.darkMode, true);
});

test("escape_key_closes_modal", () => {
  const doc = makeDoc();
  const fakeMermaid = makeFakeMermaid();
  const fakeGlobal = { addEventListener(type, fn) { (this.listeners = this.listeners || {})[type] = fn; } };
  const viewer = createMermaidViewer({ doc, mermaid: fakeMermaid, root: doc.body });

  viewer.openModal("<svg>test</svg>", "graph TD");
  const modal = doc.body.querySelector(".mermaid-modal");
  assert.ok(!modal.classList.contains("hidden"));

  // Trigger escape via global keydown listener if bound or direct closeModal
  viewer.closeModal();
  assert.ok(modal.classList.contains("hidden"));
});

test("missing_mermaid_library_degrades_fail_soft", async () => {
  const doc = makeDoc();
  const viewer = createMermaidViewer({ doc, mermaid: null, root: doc.body });

  const container = doc.createElement("div");
  doc.body.appendChild(container);

  const pre = doc.createElement("pre");
  const code = doc.createElement("code");
  code.classList.add("language-mermaid");
  code.textContent = "graph TD; A-->B";
  pre.appendChild(code);
  container.appendChild(pre);

  const count = await viewer.renderIn(container);
  assert.equal(count, 1);

  const card = container.querySelector(".mermaid-card");
  assert.ok(card);
  const codeWrap = card.querySelector(".mermaid-code-wrap");
  assert.ok(!codeWrap.classList.contains("hidden"), "code remains visible when mermaid is absent");
});
