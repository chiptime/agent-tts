/* node --test suite for the PWA device pairing module (Panel parity F1).
 *
 * The fake DOM implements only the surface pairing.js touches (the same
 * plain-object approach as toast.test.js: no jsdom, no dependencies).
 * The HTTP seam is injected exactly as app.js wires it, so the suite
 * pins the wire shapes: POST /pair {code,label} -> {token,label},
 * GET /devices -> {enforced,current,devices}, POST /devices/revoke. */

const test = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { createPairingUI, TOKEN_KEY, NOT_PAIRED_DETAIL } =
  require("../../src/herdr_brain/static/pairing.js");

/* ---- fake DOM (test infrastructure, toast.test.js subset) ---- */

function makeNode() {
  const node = {
    attributes: {},
    children: [],
    textContent: "",
    value: "",
    disabled: false,
    hidden: false,
    listeners: {},
    classList: {
      add(c) { node.attributes["class"] = (node.attributes["class"] || "") + " " + c; },
      remove(c) {
        node.attributes["class"] = (node.attributes["class"] || "")
          .split(/\s+/).filter((x) => x && x !== c).join(" ");
      },
      toggle(c, force) {
        const has = (node.attributes["class"] || "").split(/\s+/).includes(c);
        const want = force === undefined ? !has : !!force;
        if (want) node.classList.add(c);
        else node.classList.remove(c);
      },
      contains(c) {
        return (node.attributes["class"] || "").split(/\s+/).includes(c);
      },
    },
    appendChild(child) { node.children.push(child); return child; },
    setAttribute(name, value) { node.attributes[name] = String(value); },
    addEventListener(type, fn) { (node.listeners[type] = node.listeners[type] || []).push(fn); },
    dispatch(type, event) { (node.listeners[type] || []).forEach((fn) => fn(event)); },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    focus() { node.focused = true; },
  };
  return node;
}

function makeDoc() {
  const ids = {};
  const doc = {
    createElement: () => makeNode(),
    createTextNode: (text) => ({ textContent: text }),
    getElementById(id) {
      if (!ids[id]) ids[id] = makeNode();
      return ids[id];
    },
  };
  return doc;
}

/* ---- fake storage + request seam ---- */

function makeStorage() {
  const items = {};
  return {
    getItem: (k) => (k in items ? items[k] : null),
    setItem: (k, v) => { items[k] = String(v); },
    removeItem: (k) => { delete items[k]; },
    items,
  };
}

function makeRequest() {
  const calls = [];
  const responder = { queue: [] };
  const request = (url, options) => {
    calls.push({ url, options: options || {} });
    const next = responder.queue.shift() || { status: 200, json: {} };
    return Promise.resolve({
      status: next.status,
      ok: next.status >= 200 && next.status < 300,
      json: () => Promise.resolve(next.json),
    });
  };
  return { request, calls, responder };
}

function makeUI(overrides) {
  const http = makeRequest();
  const storage = makeStorage();
  const doc = makeDoc();
  const events = [];
  const ui = createPairingUI(Object.assign({
    request: http.request,
    storage,
    doc,
    openSettings: () => events.push("open-settings"),
    onPaired: () => events.push("paired"),
  }, overrides));
  return { ui, http, storage, doc, events };
}

/* ---- the module shape ---- */

test("exports the token key and the not-paired 403 detail", () => {
  assert.equal(TOKEN_KEY, "herdr_brain_device_token");
  assert.equal(NOT_PAIRED_DETAIL, "dispositivo no emparejado");
});

/* ---- pairing ---- */

test("pair posts code and label, stores the token once, refreshes", async () => {
  const { ui, http, storage, events } = makeUI();
  http.responder.queue = [
    { status: 200, json: { token: "tok-1", label: "móvil" } },
    { status: 200, json: { enforced: true, current: "móvil", devices: [
      { label: "móvil", createdAt: 1, lastSeenAt: 2, current: true },
    ] } },
  ];

  await ui.pair("AB23CD45", "móvil");

  assert.deepEqual(http.calls[0], {
    url: "/pair",
    options: { method: "POST", headers: { "content-type": "application/json" },
               body: JSON.stringify({ code: "AB23CD45", label: "móvil" }) },
  });
  assert.equal(storage.items[TOKEN_KEY], "tok-1");
  assert.deepEqual(events, ["paired"]);
  assert.equal(ui.token(), "tok-1");
  assert.equal(ui.isPaired(), true);
});

test("pair failure keeps the old token and reports the reason", async () => {
  const { ui, http, storage } = makeUI();
  storage.items[TOKEN_KEY] = "old-token";
  http.responder.queue = [{ status: 400, json: { detail: "bad-code" } }];

  const outcome = await ui.pair("WRONGWRONG", "x");

  assert.equal(outcome, "bad-code");
  assert.equal(storage.items[TOKEN_KEY], "old-token");
  assert.equal(ui.isPaired(), false); // no /devices call confirmed it
});

/* ---- device status ---- */

test("refresh renders enforced state, devices and the current one", async () => {
  const { ui, http } = makeUI();
  http.responder.queue = [{ status: 200, json: { enforced: true, current: "a",
    devices: [
      { label: "a", createdAt: 10, lastSeenAt: 20, current: true },
      { label: "b", createdAt: 11, lastSeenAt: 21, current: false },
    ] } }];

  const state = await ui.refresh();

  assert.equal(state.enforced, true);
  assert.equal(ui.isPaired(), true);
  assert.equal(http.calls[0].url, "/devices");
  // The stored token rides the status poll as a bearer.
  assert.equal(http.calls[0].options.headers.Authorization, undefined);
});

test("refresh with a stored token sends it as the bearer", async () => {
  const { ui, http, storage } = makeUI();
  storage.items[TOKEN_KEY] = "tok-9";
  http.responder.queue = [
    { status: 200, json: { enforced: true, current: "x", devices: [] } },
  ];
  await ui.refresh();
  assert.equal(http.calls[0].options.headers.Authorization, "Bearer tok-9");
});

test("refresh fails soft when the brain predates pairing", async () => {
  const { ui, http } = makeUI();
  const request = (url) => {
    http.calls.push({ url });
    return Promise.resolve({ status: 404, ok: false, json: () => Promise.resolve({}) });
  };
  const bespoke = createPairingUI({
    request, storage: makeStorage(), doc: makeDoc(),
    openSettings: () => {}, onPaired: () => {},
  });
  const state = await bespoke.refresh();
  assert.equal(state, null);
});

/* ---- revoke ---- */

test("revoke posts the label with the bearer and clears a self-revoke", async () => {
  const { ui, http, storage } = makeUI();
  storage.items[TOKEN_KEY] = "tok-1";
  http.responder.queue = [
    { status: 200, json: { enforced: false, current: null, devices: [] } },
  ];
  await ui.revoke("móvil");
  assert.equal(http.calls[0].url, "/devices/revoke");
  assert.equal(http.calls[0].options.method, "POST");
  assert.equal(http.calls[0].options.headers.Authorization, "Bearer tok-1");
  assert.deepEqual(JSON.parse(http.calls[0].options.body), { label: "móvil" });
  // Revoking yourself drops the local credential.
  assert.equal(storage.items[TOKEN_KEY], undefined);
  assert.equal(ui.isPaired(), false);
});

/* ---- auth headers for the app's write fetches ---- */

test("authHeaders adds the bearer only when a token exists", async () => {
  const plain = makeUI();
  assert.deepEqual(plain.ui.authHeaders({ "content-type": "application/json" }),
    { "content-type": "application/json" });

  const paired = makeUI();
  paired.storage.items[TOKEN_KEY] = "tok-2";
  assert.deepEqual(paired.ui.authHeaders({ "content-type": "application/json" }),
    { "content-type": "application/json", Authorization: "Bearer tok-2" });
});

/* ---- the not-paired 403 offer ---- */

test("offer opens settings and shows the pairing hint", () => {
  const { ui, events, doc } = makeUI();
  ui.offer();
  assert.deepEqual(events, ["open-settings"]);
});

test("isNotPairedResponse matches only the pairing 403 detail", async () => {
  const { ui } = makeUI();
  assert.equal(ui.isNotPairedResponse({ status: 403, detail: NOT_PAIRED_DETAIL }), true);
  assert.equal(ui.isNotPairedResponse({ status: 403, detail: "host no permitido" }), false);
  assert.equal(ui.isNotPairedResponse({ status: 404, detail: NOT_PAIRED_DETAIL }), false);
});
