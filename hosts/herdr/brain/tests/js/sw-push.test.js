/* node --test suite for the service worker's push handlers (S5).
 *
 * sw.js exports its two PURE decision helpers for exactly this suite
 * (the worker context keeps the listeners; node gets the functions).
 * The pins hold the payload contract: the payload's own tag/title/body
 * ride showNotification, gate actions ride the notification options,
 * and — the deliberate gate-button decision — EVERY click plan is a
 * focus/open, never an approving POST. */

const test = require("node:test");
const assert = require("node:assert");

/* The worker context: sw.js registers its listeners on `self` at load.
 * Node has no `self`, so the suite stubs the worker global first — the
 * handlers themselves stay the file's own, unmodified. */
const workerListeners = {};
globalThis.self = {
  addEventListener(type, handler) { workerListeners[type] = handler; },
  skipWaiting() {},
  registration: { showNotification() { return Promise.resolve(); } },
  clients: {
    matchAll() { return Promise.resolve([]); },
    openWindow() { return Promise.resolve({}); },
  },
};

const {
  pushNotificationOptions,
  clickPlan,
} = require("../../src/herdr_brain/static/sw.js");

test("options carry the payload's tag, body and interaction rule", () => {
  const options = pushNotificationOptions({
    v: 1,
    kind: "blocked",
    pane_id: "w2:p1",
    title: "opencode repo está bloqueado",
    body: "¿Seguimos con el despliegue?",
    tag: "brain-herd",
  });
  assert.equal(options.tag, "brain-herd");
  assert.equal(options.body, "¿Seguimos con el despliegue?");
  assert.equal(options.requireInteraction, true);  // blocked holds the slot
  assert.equal(options.data.paneId, "w2:p1");
  assert.equal(options.data.gate, null);
});

test("a done notification does not hold the lock screen", () => {
  const options = pushNotificationOptions({
    kind: "done", title: "x terminó", body: "fin", tag: "brain-herd",
  });
  assert.equal(options.requireInteraction, false);
});

test("gate actions ride the options with the gate id in the data", () => {
  const options = pushNotificationOptions({
    kind: "blocked",
    title: "bloqueado",
    body: "¿Se envía?",
    tag: "brain-herd",
    actions: [
      { action: "approve", title: "Aprobar", gate: "g_abc123" },
      { action: "reject", title: "Rechazar", gate: "g_abc123" },
    ],
  });
  assert.deepEqual(options.actions, [
    { action: "approve", title: "Aprobar" },
    { action: "reject", title: "Rechazar" },
  ]);
  assert.equal(options.data.gate, "g_abc123");
});

test("a payload without actions adds none", () => {
  const options = pushNotificationOptions({ kind: "cleared", title: "Resuelto", body: "ya no" });
  assert.equal(options.actions, undefined);
  assert.equal(options.tag, "brain-herd");  // payload default
});

test("an unparseable payload still yields a notification shape", () => {
  const options = pushNotificationOptions(null);
  assert.equal(options.tag, "brain-herd");
  assert.equal(options.body, undefined);
});

/* ---- the gate-button decision: never an approving POST ---- */

test("an approve action focuses the gate's deep link, never posts", () => {
  const plan = clickPlan({
    action: "approve",
    notification: { data: { paneId: "w2:p1", gate: "g_abc123" } },
  });
  assert.deepEqual(plan, { focus: true, url: "/?gate=g_abc123" });
});

test("a reject action also only focuses", () => {
  const plan = clickPlan({
    action: "reject",
    notification: { data: { gate: "g_abc123" } },
  });
  assert.deepEqual(plan, { focus: true, url: "/?gate=g_abc123" });
});

test("an action without a gate id lands on the app root", () => {
  const plan = clickPlan({
    action: "approve",
    notification: { data: { gate: null } },
  });
  assert.deepEqual(plan, { focus: true, url: "/" });
});

test("a plain click focuses the app root", () => {
  const plan = clickPlan({
    action: "",
    notification: { data: { paneId: "w2:p1", gate: "g_abc123" } },
  });
  assert.deepEqual(plan, { focus: true, url: "/" });
});
