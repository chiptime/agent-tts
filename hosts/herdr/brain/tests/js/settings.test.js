/* node --test suite for the Ajustes appearance controls (S4, doc 09).
 *
 * Per-device settings in localStorage: theme auto|claro|oscuro on the
 * existing data-theme attribute, chat text size in three steps over a
 * CSS variable, Zen mode (hide header/chips, keep the call button) and
 * haptics (navigator.vibrate on gate/receipt events, row hidden where
 * unsupported). The theme cycle is pinned to PRESERVE the pre-S4
 * toggle contract: the first tap from auto flips the system
 * resolution — one tap from a light system still lands on dark. */

const test = require("node:test");
const assert = require("node:assert");
const { createSettings, nextThemeMode, resolveThemeMode, hapticsSupported, HAPTIC_PATTERNS } =
  require("../../src/herdr_brain/static/settings.js");

/* ---- theme cycle (pure) ---- */

test("first tap from auto flips the system resolution (preserved pin)", () => {
  assert.equal(nextThemeMode("auto", false), "dark");
  assert.equal(nextThemeMode("auto", true), "light");
  assert.equal(nextThemeMode(null, false), "dark");
});

test("the cycle visits every state and returns to auto", () => {
  /* light system: auto -> dark -> light -> auto */
  assert.equal(nextThemeMode("dark", false), "light");
  assert.equal(nextThemeMode("light", false), "auto");
  /* dark system: auto -> light -> dark -> auto */
  assert.equal(nextThemeMode("light", true), "dark");
  assert.equal(nextThemeMode("dark", true), "auto");
});

test("resolveThemeMode maps stored values and garbage", () => {
  assert.equal(resolveThemeMode("dark", false), "dark");
  assert.equal(resolveThemeMode("light", false), "light");
  assert.equal(resolveThemeMode(null, false), "auto");
  assert.equal(resolveThemeMode("auto", false), "auto");
  assert.equal(resolveThemeMode("klingon", false), "auto");
});

/* ---- haptics (pure) ---- */

test("hapticsSupported checks the navigator's vibrate function", () => {
  assert.equal(hapticsSupported({ vibrate: () => {} }), true);
  assert.equal(hapticsSupported({}), false);
  assert.equal(hapticsSupported(undefined), false);
});

test("gate and receipt events have distinct patterns", () => {
  assert.notDeepEqual(HAPTIC_PATTERNS.gate, HAPTIC_PATTERNS.receipt);
  assert.ok(Array.isArray(HAPTIC_PATTERNS.gate));
  assert.ok(Array.isArray(HAPTIC_PATTERNS.receipt));
});

/* ---- createSettings wiring against fakes ---- */

function makeDoc() {
  const body = {
    attributes: {}, children: [],
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
    setAttribute(name, value) { this.attributes[name] = value; },
  };
  const root = {
    style: { props: {}, setProperty(name, value) { this.props[name] = value; } },
    setAttribute() {},
  };
  return { body, root, documentElement: root };
}

function makeStorage(initial) {
  const store = Object.assign({}, initial);
  return {
    getItem: (k) => (k in store ? store[k] : null),
    setItem(k, v) { store[k] = String(v); },
    _dump: store,
  };
}

function rowElement() {
  const node = {
    attributes: {}, children: [], textContent: "",
    hidden: false,
    listeners: {},
    setAttribute(name, value) { this.attributes[name] = value; },
    getAttribute(name) { return this.attributes[name]; },
    addEventListener(type, handler) { this.listeners[type] = handler; },
    click() { if (this.listeners.click) this.listeners.click(); },
    classList: {
      _t: new Set(),
      add(c) { this._t.add(c); }, remove(c) { this._t.delete(c); },
      toggle(c, force) { const w = force === undefined ? !this._t.has(c) : !!force; if (w) this._t.add(c); else this._t.delete(c); },
      contains(c) { return this._t.has(c); },
    },
  };
  return node;
}

function makeDeps(overrides) {
  const doc = makeDoc();
  const storage = makeStorage();
  const buzzes = [];
  const localePosts = [];
  const rows = {
    font: rowElement(), zen: rowElement(), zenExit: rowElement(), haptics: rowElement(), language: rowElement(),
  };
  const deps = Object.assign({
    doc: doc,
    storage: storage,
    navigator: { vibrate: (pattern) => { buzzes.push(pattern); return true; } },
    rows: rows,
    labels: { font: () => "Texto: Normal", zen: () => "Modo Zen", haptics: () => "Háptica", language: () => "Idioma: Español" },
    i18n: {
      getLocale: () => "es",
      locales: () => ["es", "en"],
      setLocale: () => true,
      subscribe: () => () => {},
    },
    postLocale: (code) => { localePosts.push(code); return Promise.resolve(); },
  }, overrides);
  return { deps, doc, storage, rows, buzzes, localePosts };
}

test("font cycle walks three steps and writes the CSS variable", () => {
  const { deps, doc, rows } = makeDeps();
  const settings = createSettings(deps);
  assert.equal(doc.documentElement.style.props["--font-scale"], "1");
  rows.font.click();
  assert.equal(doc.documentElement.style.props["--font-scale"], "1.15");
  rows.font.click();
  assert.equal(doc.documentElement.style.props["--font-scale"], "0.9");
  rows.font.click();
  assert.equal(doc.documentElement.style.props["--font-scale"], "1");
  rows.font.click();
  assert.equal(doc.documentElement.style.props["--font-scale"], "1.15");
  assert.equal(rows.font.textContent, "Texto: Normal");
});

test("font choice persists and is restored at creation", () => {
  const first = makeDeps();
  createSettings(first.deps);
  first.rows.font.click();  /* large */
  const second = makeDeps({ storage: first.storage });
  createSettings(second.deps);
  assert.equal(second.doc.documentElement.style.props["--font-scale"], "1.15");
});

test("zen toggle hides the chrome and keeps the call button", () => {
  const { deps, doc, rows } = makeDeps();
  createSettings(deps);
  assert.equal(doc.body.classList.contains("zen"), false);
  rows.zen.click();
  assert.equal(doc.body.classList.contains("zen"), true);
  rows.zen.click();
  assert.equal(doc.body.classList.contains("zen"), false);
});

test("zen choice persists", () => {
  const first = makeDeps();
  createSettings(first.deps);
  first.rows.zen.click();
  const second = makeDeps({ storage: first.storage });
  createSettings(second.deps);
  assert.equal(second.doc.body.classList.contains("zen"), true);
});

test("zenExit button exits zen mode", () => {
  const { deps, doc, rows } = makeDeps();
  const settings = createSettings(deps);
  rows.zen.click();
  assert.equal(doc.body.classList.contains("zen"), true);
  rows.zenExit.click();
  assert.equal(doc.body.classList.contains("zen"), false);
  assert.equal(settings.get().zen, false);
});

test("setZen programmatic exit", () => {
  const { deps, doc } = makeDeps();
  const settings = createSettings(deps);
  settings.setZen(true);
  assert.equal(doc.body.classList.contains("zen"), true);
  assert.equal(settings.get().zen, true);
  settings.setZen(false);
  assert.equal(doc.body.classList.contains("zen"), false);
  assert.equal(settings.get().zen, false);
});

test("the haptics row is hidden when vibrate is unsupported", () => {
  const { deps, rows } = makeDeps({ navigator: {} });
  createSettings(deps);
  assert.equal(rows.haptics.hidden, true);
});

test("the haptics row shows where vibrate exists and buzzes on events", () => {
  const { deps, rows, buzzes } = makeDeps();
  const settings = createSettings(deps);
  assert.equal(rows.haptics.hidden, false);
  settings.buzz("gate");
  assert.deepEqual(buzzes, [HAPTIC_PATTERNS.gate]);
  settings.buzz("receipt");
  assert.deepEqual(buzzes, [HAPTIC_PATTERNS.gate, HAPTIC_PATTERNS.receipt]);
  /* disabling stops the buzzes */
  rows.haptics.click();
  settings.buzz("gate");
  assert.deepEqual(buzzes, [HAPTIC_PATTERNS.gate, HAPTIC_PATTERNS.receipt]);
});

test("the language row cycles locales and publishes the locale", () => {
  const calls = [];
  let localeNow = "es";
  const { deps, rows, localePosts } = makeDeps({
    i18n: {
      getLocale: () => localeNow,
      locales: () => ["es", "en"],
      setLocale: (code) => { calls.push(code); localeNow = code; return true; },
      subscribe: () => () => {},
    },
  });
  createSettings(deps);
  rows.language.click();
  rows.language.click();
  assert.deepEqual(calls, ["en", "es"]);
  assert.deepEqual(localePosts, ["en", "es"]);
});

test("an unknown stored settings payload falls back to defaults", () => {
  const { deps, doc, rows } = makeDeps({ storage: makeStorage({ "herdr-brain-settings-v1": "not json" }) });
  createSettings(deps);
  assert.equal(doc.body.classList.contains("zen"), false);
  assert.equal(doc.documentElement.style.props["--font-scale"], "1");
  assert.equal(rows.haptics.hidden, false);
});
