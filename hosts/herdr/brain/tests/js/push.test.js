/* node --test suite for the PWA Web Push card (S5).
 *
 * The fake DOM implements only the surface push.js touches (the same
 * plain-object approach as pairing.test.js: no jsdom, no dependencies).
 * The seams are injected exactly as app.js wires them, so the suite
 * pins the wire shapes: GET /push/key, POST /push/subscribe with the
 * PushManager's subscription, POST /push/unsubscribe, POST /push/test —
 * and the enable flow's permission + registration order. */

const test = require("node:test");
const assert = require("node:assert");
const { createPushUI } = require("../../src/herdr_brain/static/push.js");

/* ---- fake DOM (test infrastructure, pairing.test.js subset) ---- */

function makeNode() {
  const node = {
    attributes: {},
    children: [],
    textContent: "",
    value: "",
    disabled: false,
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
    focus() {},
  };
  return node;
}

function makeDoc() {
  const ids = {};
  return {
    createElement: () => makeNode(),
    createTextNode: (text) => ({ textContent: text }),
    getElementById(id) {
      if (!ids[id]) ids[id] = makeNode();
      return ids[id];
    },
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

const SUBSCRIPTION = {
  endpoint: "https://push.example.invalid/f/abc",
  keys: { p256dh: "BIGPUBLICKEY", auth: "AUTHKEY" },
};

function makePushManager(subscribeResult) {
  const calls = { subscribe: [], getSubscription: 0, unsubscribe: 0 };
  const pushManager = {
    getSubscription() {
      calls.getSubscription++;
      return Promise.resolve(null);
    },
    subscribe(options) {
      calls.subscribe.push(options);
      return Promise.resolve(
        Object.assign({ toJSON: () => SUBSCRIPTION }, subscribeResult || SUBSCRIPTION)
      );
    },
  };
  return { pushManager, calls };
}

function makeUI(overrides) {
  const http = makeRequest();
  const doc = makeDoc();
  const toasts = [];
  const registration = makePushManager();
  let permission = "granted";
  const ui = createPushUI(Object.assign({
    request: http.request,
    doc,
    getRegistration: () => Promise.resolve({ pushManager: registration.pushManager }),
    notificationPermission: () => permission,
    requestPermission: () => Promise.resolve(permission),
    showToast: (text, ms) => toasts.push({ text, ms }),
  }, overrides));
  return { ui, http, doc, toasts, registration, setPermission: (p) => { permission = p; } };
}

function findButton(root, className) {
  const walk = (node) => {
    for (const child of node.children || []) {
      if (child.attributes["class"] === className) return child;
      const found = walk(child);
      if (found) return found;
    }
    return null;
  };
  return walk(root);
}

/* ---- refresh / render ---- */

test("refresh reads /push/key and renders the card", async () => {
  const { ui, doc } = makeUI();
  ui.state().serverKeys = null;
  const http = makeRequest();
  http.responder.queue = [
    { status: 200, json: { enabled: true, public_key: "PUBKEY" } },
  ];
  const card = createPushUI({ request: http.request, doc });
  await card.refresh();
  assert.equal(http.calls[0].url, "/push/key");
  const root = doc.getElementById("push-box");
  assert.ok(root.children.length > 0);
});

test("a brain that could not create keys renders the doctor hint and no buttons", async () => {
  const { doc } = makeUI();
  const http = makeRequest();
  http.responder.queue = [{ status: 200, json: { enabled: false, public_key: null } }];
  const card = createPushUI({ request: http.request, doc });
  await card.refresh();
  const root = doc.getElementById("push-box");
  const texts = root.children.map((c) => c.textContent).join(" ");
  assert.match(texts, /no pudo crear sus claves de push/);
  assert.equal(findButton(root, "push-actions"), null);
});

/* ---- enable ---- */

test("enable asks permission, subscribes with the public key and posts", async () => {
  const made = makeUI();
  made.http.responder.queue = [
    { status: 200, json: { enabled: true, public_key: "PUBKEY" } },  // refresh
    { status: 200, json: { endpoint: SUBSCRIPTION.endpoint } },       // subscribe
  ];
  await made.ui.refresh();

  const outcome = await made.ui.enable(findButton(
    made.doc.getElementById("push-box"), "push-enable"
  ));

  assert.equal(outcome, "ok");
  const call = made.http.calls.find((c) => c.url === "/push/subscribe");
  assert.equal(call.options.method, "POST");
  const body = JSON.parse(call.options.body);
  assert.equal(body.endpoint, SUBSCRIPTION.endpoint);
  assert.deepEqual(body.keys, SUBSCRIPTION.keys);
  // The VAPID key reached PushManager as bytes derived from the base64.
  const options = made.registration.calls.subscribe[0];
  assert.equal(options.userVisibleOnly, true);
  assert.ok(options.applicationServerKey instanceof Uint8Array);
  assert.ok(options.applicationServerKey.length > 0);
  assert.equal(made.ui.state().endpoint, SUBSCRIPTION.endpoint);
});

test("a denied permission keeps the card off and explains", async () => {
  const made = makeUI();
  made.setPermission("denied");
  made.http.responder.queue = [
    { status: 200, json: { enabled: true, public_key: "PUBKEY" } },
  ];
  await made.ui.refresh();

  const outcome = await made.ui.enable(findButton(
    made.doc.getElementById("push-box"), "push-enable"
  ));

  assert.equal(outcome, null);
  assert.match(made.toasts[0].text, /no se pudieron activar los avisos/i);
  assert.equal(made.registration.calls.subscribe.length, 0);
});

test("a refused subscription (422) surfaces the server detail", async () => {
  const made = makeUI();
  made.http.responder.queue = [
    { status: 200, json: { enabled: true, public_key: "PUBKEY" } },
    { status: 422, json: { detail: "subscription needs keys.p256dh and keys.auth" } },
  ];
  await made.ui.refresh();
  await made.ui.enable(findButton(
    made.doc.getElementById("push-box"), "push-enable"
  ));
  assert.match(made.toasts[0].text, /keys\.p256dh/);
  assert.equal(made.ui.state().endpoint, null);
});

/* ---- disable ---- */

test("disable unsubscribes the server first, then the browser", async () => {
  const made = makeUI();
  made.http.responder.queue = [
    { status: 200, json: { enabled: true, public_key: "PUBKEY" } },
    { status: 200, json: { endpoint: SUBSCRIPTION.endpoint } },
    { status: 200, json: { ok: true } },
  ];
  await made.ui.refresh();
  await made.ui.enable(findButton(
    made.doc.getElementById("push-box"), "push-enable"
  ));

  const removed = { unsubscribed: false };
  const ui2 = createPushUI({
    request: made.http.request,
    doc: made.doc,
    getRegistration: () => Promise.resolve({
      pushManager: {
        getSubscription: () => Promise.resolve({
          unsubscribe: () => { removed.unsubscribed = true; return Promise.resolve(true); },
        }),
      },
    }),
    notificationPermission: () => "granted",
    requestPermission: () => Promise.resolve("granted"),
    showToast: () => {},
  });
  // Seed the second UI with an active endpoint via enable state.
  ui2.state().endpoint = SUBSCRIPTION.endpoint;
  ui2.state().publicKey = "PUBKEY";

  const outcome = await ui2.disable(findButton(
    made.doc.getElementById("push-box"), "push-disable"
  ));

  assert.equal(outcome, "ok");
  const call = made.http.calls.find((c) => c.url === "/push/unsubscribe");
  assert.deepEqual(JSON.parse(call.options.body), { endpoint: SUBSCRIPTION.endpoint });
  assert.equal(removed.unsubscribed, true);
  assert.equal(ui2.state().endpoint, null);
});

/* ---- test ---- */

test("sendTest posts /push/test and reports the sent count", async () => {
  const made = makeUI();
  made.http.responder.queue = [
    { status: 200, json: { enabled: true, public_key: "PUBKEY" } },
    { status: 200, json: { endpoint: SUBSCRIPTION.endpoint } },
    { status: 200, json: { sent: 2 } },
  ];
  await made.ui.refresh();
  await made.ui.enable(findButton(
    made.doc.getElementById("push-box"), "push-enable"
  ));

  await made.ui.sendTest(findButton(
    made.doc.getElementById("push-box"), "push-test"
  ));

  const call = made.http.calls.find((c) => c.url === "/push/test");
  assert.equal(call.options.method, "POST");
  assert.match(made.toasts[made.toasts.length - 1].text, /2/);
});

test("a zero-count test is reported honestly", async () => {
  const made = makeUI();
  made.http.responder.queue = [
    { status: 200, json: { sent: 0 } },
  ];
  made.ui.state().endpoint = "https://push.example.invalid/x";
  made.ui.state().publicKey = "PUBKEY";
  made.ui.state().serverKeys = true;
  made.ui.render();  // seed the card with the active-subscription shape
  await made.ui.sendTest(findButton(
    made.doc.getElementById("push-box"), "push-test"
  ));
  assert.match(made.toasts[0].text, /No se envió nada/);
});

/* ---- a subscription issued for another key is replaced ---- */

function keyBytes(b64url) {
  const padding = "=".repeat((4 - (b64url.length % 4)) % 4);
  return Uint8Array.from(Buffer.from((b64url + padding).replace(/-/g, "+").replace(/_/g, "/"), "base64"));
}

function existingSubscription(serverKeyB64, calls) {
  return Object.assign({
    toJSON: () => SUBSCRIPTION,
    options: { applicationServerKey: serverKeyB64 === null ? null : keyBytes(serverKeyB64).buffer },
    unsubscribe() { calls.unsubscribe++; return Promise.resolve(true); },
  }, SUBSCRIPTION);
}

async function enableWithExisting(existingKey) {
  const made = makeUI();
  const calls = made.registration.calls;
  made.registration.pushManager.getSubscription = () => {
    calls.getSubscription++;
    return Promise.resolve(existingSubscription(existingKey, calls));
  };
  made.http.responder.queue = [
    { status: 200, json: { enabled: true, public_key: "BRAINKEY" } },
    { status: 200, json: { endpoint: SUBSCRIPTION.endpoint } },
  ];
  await made.ui.refresh();
  const outcome = await made.ui.enable(findButton(made.doc.getElementById("push-box"), "push-enable"));
  return { made, calls, outcome };
}

test("an existing subscription for the brain's own key is reused", async () => {
  const { calls, outcome } = await enableWithExisting("BRAINKEY");
  assert.equal(outcome, "ok");
  assert.equal(calls.unsubscribe, 0);
  assert.equal(calls.subscribe.length, 0);
});

test("an existing subscription for another server key is replaced", async () => {
  const { calls, outcome } = await enableWithExisting("OTHERKEY");
  assert.equal(outcome, "ok");
  assert.equal(calls.unsubscribe, 1);
  assert.equal(calls.subscribe.length, 1);
  assert.deepEqual(Array.from(calls.subscribe[0].applicationServerKey), Array.from(keyBytes("BRAINKEY")));
});

test("a subscription that does not expose its key is replaced, never trusted", async () => {
  const { calls, outcome } = await enableWithExisting(null);
  assert.equal(outcome, "ok");
  assert.equal(calls.unsubscribe, 1);
  assert.equal(calls.subscribe.length, 1);
});

test("a single-bit key mismatch replaces the old subscription", async () => {
  const changed = new Uint8Array(keyBytes("BRAINKEY"));
  changed[0] ^= 1;
  const { calls, outcome } = await enableWithExisting(Buffer.from(changed).toString("base64url"));
  assert.equal(outcome, "ok");
  assert.equal(calls.unsubscribe, 1);
  assert.equal(calls.subscribe.length, 1);
  assert.deepEqual(Array.from(calls.subscribe[0].applicationServerKey), Array.from(keyBytes("BRAINKEY")));
});

test("denied permission never mutates an existing subscription or the server", async () => {
  const made = makeUI();
  made.setPermission("denied");
  made.http.responder.queue = [
    { status: 200, json: { enabled: true, public_key: "BRAINKEY" } },
  ];
  await made.ui.refresh();
  const outcome = await made.ui.enable(findButton(made.doc.getElementById("push-box"), "push-enable"));
  assert.equal(outcome, null);
  assert.equal(made.registration.calls.getSubscription, 0);
  assert.equal(made.registration.calls.unsubscribe, 0);
  assert.equal(made.registration.calls.subscribe.length, 0);
  assert.deepEqual(made.http.calls.map((call) => call.url), ["/push/key"]);
});
