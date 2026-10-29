/* node --test suite for the i18n runtime (S4, doc 09).
 *
 * The runtime is the PWA's own dictionary store: `t(key, vars)` with
 * {slot} interpolation, a subscription list the app re-renders on,
 * localStorage persistence and <html lang> sync. Spanish is the SOURCE
 * language: the es dictionary must carry the exact strings the app
 * rendered before i18n existed (byte-identical default), en is a full
 * sibling, and a missing key falls back to es before ever showing a
 * raw key to the operator. */

const test = require("node:test");
const assert = require("node:assert");
const { createI18n } = require("../../src/herdr_brain/static/i18n.js");
const es = require("../../src/herdr_brain/static/i18n/es.js");
const en = require("../../src/herdr_brain/static/i18n/en.js");

/* ---- fake DOM/storage surface (plain objects, no jsdom) ---- */

function makeDoc() {
  const setAttrs = [];
  return {
    documentElement: {
      lang: "es",
      setAttribute(name, value) { setAttrs.push([name, value]); this[name === "lang" ? "lang" : name] = value; },
      style: { props: {}, setProperty(name, value) { this.props[name] = value; } },
    },
    _setAttrs: setAttrs,
    querySelectorAll() { return []; },
  };
}

function makeStorage(initial) {
  const store = Object.assign({}, initial);
  return {
    getItem: (k) => (k in store ? store[k] : null),
    setItem(k, v) { store[k] = String(v); },
    removeItem(k) { delete store[k]; },
    _dump: store,
  };
}

function makeI18n(overrides) {
  return createI18n(Object.assign({
    doc: makeDoc(),
    storage: makeStorage(),
    dicts: { es: es, en: en },
  }, overrides));
}

/* ---- the dictionaries ---- */

test("es and en expose exactly the same key set", () => {
  const esKeys = Object.keys(es).sort();
  const enKeys = Object.keys(en).sort();
  assert.deepEqual(enKeys, esKeys);
  assert.ok(esKeys.length >= 60, "the migrated UI surface is at least this big");
});

test("es pins a few pre-i18n strings byte-identical", () => {
  assert.equal(es["call.start"], "📞 Llamar");
  assert.equal(es["settings.title"], "Ajustes");
  assert.equal(es["tabs.history"], "Historial");
  assert.equal(es["glance.empty"], "Sin conversación del agente todavía");
  assert.equal(es["reply.placeholder"], "Responde al agente…");
});

test("every es value is a non-empty string", () => {
  for (const [key, value] of Object.entries(es)) {
    assert.equal(typeof value, "string", key);
    assert.ok(value.length > 0, key);
  }
});

/* ---- t(): lookup, interpolation, fallback ---- */

test("t() renders the es string by default", () => {
  const i18n = makeI18n();
  assert.equal(i18n.t("settings.title"), "Ajustes");
});

test("t() interpolates {slot} values", () => {
  const i18n = makeI18n({
    dicts: { es: { http: "código {status} en {where}" }, en: { http: "code {status} in {where}" } },
  });
  assert.equal(i18n.t("http", { status: 503, where: "/tts" }), "código 503 en /tts");
  i18n.setLocale("en");
  assert.equal(i18n.t("http", { status: 503, where: "/tts" }), "code 503 in /tts");
});

test("a key missing in the current locale falls back to es", () => {
  const i18n = makeI18n({
    dicts: { es: { only_es: "valor es" }, en: {} },
  });
  i18n.setLocale("en");
  assert.equal(i18n.t("only_es"), "valor es");
});

test("a key missing everywhere returns the key itself (debuggable)", () => {
  assert.equal(makeI18n().t("no.such.key"), "no.such.key");
});

test("a key with vars but no vars passed keeps its slots visible", () => {
  const i18n = makeI18n({ dicts: { es: { hi: "hola {name}" } } });
  assert.equal(i18n.t("hi"), "hola {name}");
});

/* ---- locales, persistence, <html lang> ---- */

test("locales() lists es first and en second", () => {
  assert.deepEqual(makeI18n().locales(), ["es", "en"]);
});

test("default locale is es with <html lang> untouched", () => {
  const doc = makeDoc();
  const i18n = createI18n({ doc, storage: makeStorage(), dicts: { es: es, en: en } });
  assert.equal(i18n.getLocale(), "es");
  assert.equal(doc.documentElement.lang, "es");
});

test("setLocale switches, persists, syncs <html lang> and notifies", () => {
  const doc = makeDoc();
  const storage = makeStorage();
  const seen = [];
  const i18n = createI18n({ doc, storage, dicts: { es: es, en: en } });
  i18n.subscribe((locale) => seen.push(locale));
  assert.equal(i18n.setLocale("en"), true);
  assert.equal(i18n.getLocale(), "en");
  assert.equal(storage.getItem("brain:locale:v1"), "en");
  assert.equal(doc.documentElement.lang, "en");
  assert.deepEqual(seen, ["en"]);
  const off = i18n.subscribe(() => seen.push("never"));
  off();
  i18n.setLocale("es");
  assert.deepEqual(seen, ["en", "es"]);
});

test("setLocale with an unknown code is a no-op returning false", () => {
  const i18n = makeI18n();
  assert.equal(i18n.setLocale("fr"), false);
  assert.equal(i18n.getLocale(), "es");
});

test("a persisted locale is restored at creation", () => {
  const i18n = makeI18n({ storage: makeStorage({ "brain:locale:v1": "en" }) });
  assert.equal(i18n.getLocale(), "en");
});

test("a corrupt persisted locale falls back to es", () => {
  const i18n = makeI18n({ storage: makeStorage({ "brain:locale:v1": "klingon" }) });
  assert.equal(i18n.getLocale(), "es");
});

/* ---- applyDom: data-i18n and attribute variants ---- */

function domDoc() {
  const label = {
    tagName: "span", attributes: { "data-i18n": "settings.title" },
    children: [], textContent: "Ajustes",
    setAttribute(name, value) { this.attributes[name] = value; },
    getAttribute(name) { return this.attributes[name]; },
  };
  const input = {
    tagName: "input",
    attributes: { "data-i18n-placeholder": "reply.placeholder" },
    children: [], textContent: "",
    setAttribute(name, value) { this.attributes[name] = value; },
    getAttribute(name) { return this.attributes[name]; },
  };
  const titled = {
    tagName: "button",
    attributes: { "data-i18n-title": "settings.close_title", title: "Cerrar ajustes" },
    children: [], textContent: "✕",
    setAttribute(name, value) { this.attributes[name] = value; },
    getAttribute(name) { return this.attributes[name]; },
  };
  return {
    documentElement: { lang: "es", setAttribute() {} },
    querySelectorAll(selector) {
      if (selector === "[data-i18n]") return [label];
      if (selector === "[data-i18n-placeholder]") return [input];
      if (selector === "[data-i18n-title]") return [titled];
      return [];
    },
    _label: label, _input: input, _titled: titled,
  };
}

test("applyDom paints textContent and attributes from the dictionary", () => {
  const doc = domDoc();
  const i18n = createI18n({ doc, storage: makeStorage(), dicts: { es: es, en: en } });
  i18n.setLocale("en");
  assert.equal(doc._label.textContent, en["settings.title"]);
  assert.equal(doc._input.attributes.placeholder, en["reply.placeholder"]);
  assert.equal(doc._titled.attributes.title, en["settings.close_title"]);
  i18n.setLocale("es");
  assert.equal(doc._label.textContent, es["settings.title"]);
  assert.equal(doc._input.attributes.placeholder, es["reply.placeholder"]);
});
