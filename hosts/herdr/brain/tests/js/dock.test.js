/* node --test suite for the operator dock (S3, doc 06 — PWA half).
 *
 * The dock renders the four TOML-driven surfaces behind the FIXED
 * contract GET /operator/config?agent=<family> → quick_replies /
 * commands / key_presets / launchers. Pins held here:
 *  - rows are validated client-side: a row without id+label (and its
 *    own field) never becomes a button;
 *  - a command with a trailing space FILLS the composer, one without
 *    sends through the verified reply path;
 *  - launchers with no_prompts need a second tap, and the 409 answers
 *    confirmation_required (re-arm) and cwd_missing (error) honestly;
 *  - labels from the operator's TOML are rendered as TEXT, never
 *    markup — an injection-looking label must appear literally. */

const test = require("node:test");
const assert = require("node:assert");
const { createDock, dockModel, commandAction, launcherTap, launchOutcome } =
  require("../../src/herdr_brain/static/dock.js");

const FULL_CONFIG = {
  quick_replies: [
    { id: "qr1", label: "Adelante", text: "sí, continúa" },
    { id: "qr2", label: "Para", text: "para ahora" },
  ],
  commands: [
    { id: "cmd1", label: "Compactar", command: "/compact" },
    { id: "cmd2", label: "Buscar", command: "/grep " },
  ],
  key_presets: [
    { id: "k1", label: "Reintentar", keys: "up enter" },
  ],
  launchers: [
    { id: "l1", label: "Shell aquí", no_prompts: false },
    { id: "l2", label: "Todo el panel", no_prompts: true },
  ],
};

/* ---- dockModel: row validation ---- */

test("a full config passes through as renderable groups", () => {
  assert.deepEqual(dockModel(FULL_CONFIG), FULL_CONFIG);
});

test("malformed rows are dropped, the rest of the surface survives", () => {
  const model = dockModel({
    quick_replies: [
      FULL_CONFIG.quick_replies[0],
      { id: "x" },                 /* no label */
      { label: "y", text: "t" },   /* no id */
      { id: "z", label: "z" },     /* no text */
    ],
    commands: [{ id: "c", label: "c", command: "/x" }, { id: "c2", label: "c2" }],
    key_presets: [{ id: "k", label: "k", keys: "enter" }, "garbage"],
    launchers: [{ id: "l", label: "l" }, { id: "l2" }],
  });
  assert.deepEqual(model.quick_replies, [FULL_CONFIG.quick_replies[0]]);
  assert.deepEqual(model.commands, [{ id: "c", label: "c", command: "/x" }]);
  assert.deepEqual(model.key_presets, [{ id: "k", label: "k", keys: "enter" }]);
  assert.deepEqual(model.launchers, [{ id: "l", label: "l", no_prompts: false }]);
});

test("a null or non-object config hides the dock", () => {
  assert.equal(dockModel(null), null);
  assert.equal(dockModel(undefined), null);
  assert.equal(dockModel("nope"), null);
});

test("an all-empty config also hides the dock", () => {
  assert.equal(dockModel({ quick_replies: [], commands: [], key_presets: [], launchers: [] }), null);
});

/* ---- command tap policy ---- */

test("a command with a trailing space only fills the composer", () => {
  assert.deepEqual(commandAction("/grep "), { fill: true, value: "/grep " });
  assert.deepEqual(commandAction("/compact"), { fill: false, value: "/compact" });
});

test("a bare slash command with no args still sends", () => {
  assert.deepEqual(commandAction("/clear"), { fill: false, value: "/clear" });
});

/* ---- launcher two-tap ---- */

test("a no_prompts launcher arms on the first tap and sends on the second", () => {
  assert.deepEqual(launcherTap({ no_prompts: true }, "idle"), { next: "armed" });
  assert.deepEqual(launcherTap({ no_prompts: true }, "armed"), { next: "send" });
});

test("a plain launcher sends on the first tap", () => {
  assert.deepEqual(launcherTap({ no_prompts: false }, "idle"), { next: "send" });
});

/* ---- launch outcome policy (409 handling) ---- */

test("200 resolves ok", () => {
  assert.deepEqual(launchOutcome(200, null), { ok: true });
});

test("409 confirmation_required re-arms for a second tap", () => {
  assert.deepEqual(launchOutcome(409, { detail: "confirmation_required" }), {
    retry: "confirmation_required",
  });
});

test("409 cwd_missing is an honest error, never a retry", () => {
  assert.deepEqual(launchOutcome(409, { detail: "cwd_missing" }), { error: "cwd_missing" });
});

test("other statuses are plain errors", () => {
  assert.deepEqual(launchOutcome(404, { detail: "not found" }), { error: "http" });
  assert.deepEqual(launchOutcome(500, null), { error: "http" });
});

/* ---- createDock: rendering and wiring against fakes ---- */

function matchesSel(node, sel) {
  if (sel === "button") return node.tagName === "button";
  const attr = sel.match(/^\[([a-zA-Z-]+)="([^"]*)"\]$/);
  if (attr) return node.attributes[attr[1]] === attr[2];
  throw new Error("fake DOM: unsupported selector " + sel);
}

function makeElement(tag) {
  const node = {
    tagName: tag, attributes: {}, children: [], textContent: "",
    listeners: {},
    setAttribute(name, value) { this.attributes[name] = value; },
    getAttribute(name) { return this.attributes[name]; },
    appendChild(child) { this.children.push(child); child.parentNode = node; return child; },
    addEventListener(type, handler) { this.listeners[type] = handler; },
    querySelectorAll(sel) {
      const out = [];
      const visit = (n) => {
        for (const c of n.children) { if (matchesSel(c, sel)) out.push(c); visit(c); }
      };
      visit(node);
      return out;
    },
    classList: {
      _t: new Set(),
      add(...cls) { for (const c of cls) this._t.add(c); },
      remove(c) { this._t.delete(c); },
      toggle(c, force) {
        const want = force === undefined ? !this._t.has(c) : !!force;
        if (want) this._t.add(c); else this._t.delete(c);
      },
      contains(c) { return this._t.has(c); },
    },
    click() { if (this.listeners.click) this.listeners.click(); },
  };
  return node;
}

function makeDeps() {
  const calls = { sends: [], fills: [], posts: [], hidden: 0, shown: 0, toasts: [] };
  const root = makeElement("div");
  return {
    calls, root,
    deps: {
      doc: { createElement: makeElement, createTextNode: (t) => ({ nodeType: 3, text: t }) },
      root: root,
      t: (key) => "T:" + key,
      toast: (text) => calls.toasts.push(text),
      sendReply: (text) => calls.sends.push(text),
      fillComposer: (text) => calls.fills.push(text),
      post: (url, body) => {
        calls.posts.push({ url, body });
        return Promise.resolve({ status: 200, json: () => Promise.resolve({}) });
      },
      show: () => { calls.shown += 1; },
      hide: () => { calls.hidden += 1; },
    },
  };
}

test("a 404 config keeps the dock hidden", async () => {
  const { deps, calls } = makeDeps();
  const dock = createDock(Object.assign(deps, {
    getConfig: () => Promise.resolve(null),
  }));
  await dock.update("claude", "w1:p1");
  assert.equal(calls.shown, 0);
  assert.equal(calls.hidden, 1);
});

test("quick replies send through the verified reply path", async () => {
  const { deps, calls } = makeDeps();
  const dock = createDock(Object.assign(deps, {
    getConfig: () => Promise.resolve(FULL_CONFIG),
  }));
  await dock.update("claude", "w1:p1");
  assert.equal(calls.shown, 1);
  const buttons = deps.root.querySelectorAll("button");
  const qr = buttons.find((b) => b.attributes["data-row"] === "qr:qr1");
  assert.ok(qr, "quick reply button rendered");
  assert.equal(qr.textContent, "Adelante");
  qr.click();
  assert.deepEqual(calls.sends, ["sí, continúa"]);
  assert.deepEqual(calls.fills, []);
});

test("a command with trailing space fills the composer, never sends", async () => {
  const { deps, calls } = makeDeps();
  const dock = createDock(Object.assign(deps, {
    getConfig: () => Promise.resolve(FULL_CONFIG),
  }));
  await dock.update("claude", "w1:p1");
  const cmd = deps.root.querySelectorAll("button").find((b) => b.attributes["data-row"] === "cmd:cmd2");
  cmd.click();
  assert.deepEqual(calls.fills, ["/grep "]);
  assert.deepEqual(calls.sends, []);
});

test("a key preset posts the exact payload to /operator/preset", async () => {
  const { deps, calls } = makeDeps();
  const dock = createDock(Object.assign(deps, {
    getConfig: () => Promise.resolve(FULL_CONFIG),
  }));
  await dock.update("opencode", "w2:p3");
  const preset = deps.root.querySelectorAll("button").find((b) => b.attributes["data-row"] === "preset:k1");
  preset.click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(calls.posts, [{ url: "/operator/preset", body: { pane_id: "w2:p3", id: "k1" } }]);
});

test("a no_prompts launcher needs two taps before it posts", async () => {
  const { deps, calls } = makeDeps();
  const dock = createDock(Object.assign(deps, {
    getConfig: () => Promise.resolve(FULL_CONFIG),
  }));
  await dock.update("opencode", "w2:p3");
  const launcher = deps.root.querySelectorAll("button").find((b) => b.attributes["data-row"] === "launch:l2");
  launcher.click();
  assert.deepEqual(calls.posts, [], "first tap only arms");
  assert.equal(launcher.classList.contains("dock-armed"), true);
  launcher.click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(calls.posts, [
    { url: "/operator/launch", body: { id: "l2", confirm: true } },
  ]);
});

test("a plain launcher posts on the first tap", async () => {
  const { deps, calls } = makeDeps();
  const dock = createDock(Object.assign(deps, {
    getConfig: () => Promise.resolve(FULL_CONFIG),
  }));
  await dock.update("opencode", "w2:p3");
  const launcher = deps.root.querySelectorAll("button").find((b) => b.attributes["data-row"] === "launch:l1");
  launcher.click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(calls.posts, [
    { url: "/operator/launch", body: { id: "l1", confirm: true } },
  ]);
});

test("a 409 confirmation_required from the server re-arms the row", async () => {
  const { deps, calls } = makeDeps();
  let reply = { status: 200, json: () => Promise.resolve({}) };
  const dock = createDock(Object.assign(deps, {
    getConfig: () => Promise.resolve(FULL_CONFIG),
    post: (url, body) => {
      calls.posts.push({ url, body });
      return Promise.resolve(reply);
    },
  }));
  await dock.update("opencode", "w2:p3");
  const launcher = deps.root.querySelectorAll("button").find((b) => b.attributes["data-row"] === "launch:l1");
  launcher.click();  /* plain launcher: posts with confirm */
  await new Promise((resolve) => setTimeout(resolve, 0));
  reply = { status: 409, json: () => Promise.resolve({ detail: "confirmation_required" }) };
  launcher.click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(launcher.classList.contains("dock-armed"), true, "server 409 re-arms the row");
  assert.deepEqual(calls.toasts, []);
});

test("a 409 cwd_missing toasts an error and disarms", async () => {
  const { deps, calls } = makeDeps();
  const dock = createDock(Object.assign(deps, {
    getConfig: () => Promise.resolve(FULL_CONFIG),
    post: () => Promise.resolve({ status: 409, json: () => Promise.resolve({ detail: "cwd_missing" }) }),
  }));
  await dock.update("opencode", "w2:p3");
  const launcher = deps.root.querySelectorAll("button").find((b) => b.attributes["data-row"] === "launch:l1");
  launcher.click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(calls.toasts, ["T:dock.cwd_missing"]);
  assert.equal(launcher.classList.contains("dock-armed"), false);
});

test("injection-looking labels render as literal text, never markup", async () => {
  const hostile = {
    quick_replies: [
      { id: "evil", label: '<img src=x onerror="alert(1)">', text: "x" },
      { id: "evil2", label: "<script>alert(2)</script>", text: "y" },
    ],
    commands: [], key_presets: [], launchers: [],
  };
  const { deps } = makeDeps();
  const dock = createDock(Object.assign(deps, {
    getConfig: () => Promise.resolve(hostile),
  }));
  await dock.update("claude", "w1:p1");
  const buttons = deps.root.querySelectorAll("button");
  const evil = buttons.find((b) => b.attributes["data-row"] === "qr:evil");
  assert.equal(evil.textContent, '<img src=x onerror="alert(1)">');
  const elementChildren = evil.children.filter((c) => c.nodeType === 1 && c.tagName === "img");
  assert.deepEqual(elementChildren, []);
  const evil2 = buttons.find((b) => b.attributes["data-row"] === "qr:evil2");
  assert.equal(evil2.textContent, "<script>alert(2)</script>");
});

test("a config that arrives empty after a full one hides the dock", async () => {
  const { deps, calls } = makeDeps();
  let config = FULL_CONFIG;
  const dock = createDock(Object.assign(deps, { getConfig: () => Promise.resolve(config) }));
  await dock.update("claude", "w1:p1");
  assert.equal(calls.shown, 1);
  config = null;
  await dock.update("claude", "w1:p1");
  assert.equal(calls.hidden, 1);
});
