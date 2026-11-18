/* Structural wiring tests for the push deep link (S5): a notification
 * tap opens the app ON THE CHAT the notification is about.
 *
 * LIMITS, stated plainly: app.js is a single browser-only IIFE (DOM,
 * EventSource, live audio element) that node --test cannot load, so
 * the boot path cannot be EXECUTED here. These tests pin the wiring
 * SHAPE — the boot deep link (applied after the shared context,
 * consumed once) and the worker→page pane-switch message — while
 * tests/js/sw-push.test.js exercises the pure click plan the worker
 * side delegates to. Live notification taps are device territory.
 */

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const appSrc = fs.readFileSync(
  path.join(__dirname, "../../src/herdr_brain/static/app.js"), "utf8");

test("a deepLinkPane helper reads ?pane_id= once and decodes it", () => {
  assert.ok(/function deepLinkPane\(\)/.test(appSrc), "helper exists");
  assert.ok(appSrc.includes("[?&]pane_id=([^&]+)"),
    "the helper matches the pane_id query param");
  assert.ok(appSrc.includes("decodeURIComponent(m[1])"),
    "the captured value is decoded back to the pane id");
});

test("the boot applies the deep link after the shared context, before the first render", () => {
  const start = appSrc.indexOf("adoptCallContext().then(function () {");
  assert.ok(start !== -1, "boot adopt anchor found");
  const apply = appSrc.indexOf("var linked = deepLinkPane();");
  const refresh = appSrc.indexOf("refreshState();", start);
  assert.ok(apply !== -1 && apply > start && apply < refresh,
    "the deep link is applied between the shared-context adopt and the " +
    "first refreshState — the tap is the operator's latest intent, so it " +
    "beats the last shared pane");
});

test("the deep link is consumed once: the URL is cleaned before selecting", () => {
  const clean = appSrc.indexOf('history.replaceState(null, "", "/")');
  const select = appSrc.indexOf("if (linked !== selectedPane) selectAgent(linked);");
  assert.ok(clean !== -1 && select !== -1 && clean < select,
    "replaceState runs first so even a stale link cleans the URL and a " +
    "reload never jumps back to the notification's pane");
});

test("the page listens for the worker's brain-open-pane message", () => {
  assert.ok(appSrc.includes('data.type !== "brain-open-pane"'),
    "the serviceWorker message listener filters brain-open-pane");
  assert.ok(
    appSrc.includes("if (!lastHerd.length || herdEntry(target)) selectAgent(target);"),
    "a pane switch only applies when the herd knows the pane (or has not " +
    "loaded yet) — a stale notification cannot hijack the shared selection");
});

test("the message listener lives inside the serviceWorker feature check", () => {
  const guard = appSrc.indexOf('if ("serviceWorker" in navigator) {');
  const listen = appSrc.indexOf(
    'navigator.serviceWorker.addEventListener("message"');
  assert.ok(guard !== -1 && listen !== -1 && listen > guard,
    "the listener is registered on the serviceWorker container only when " +
    "the browser has one");
});
