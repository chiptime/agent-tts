/* node --test suite for the service worker's OFFLINE SHELL routing
 * decisions (S4, doc 09) — the sw-cache half of sw.js.
 *
 * sw.js keeps its push/notificationclick handlers untouched and gains
 * a cache discipline with its own pure helpers, exported the same way
 * pushNotificationOptions/clickPlan are. The pins here hold the risk-1
 * and risk-2 invariants of doc 09: only explicit shell assets are
 * cacheable, only 200 non-redirected basic responses enter the cache,
 * and activate sweeps every brain cache that is not the current build. */

const test = require("node:test");
const assert = require("node:assert");

/* The worker context: sw.js registers its listeners on `self` at load
 * and routes by origin against self.location. Node has no `self`, so
 * the suite stubs the worker global first (same shape as
 * sw-push.test.js, plus the location the routing pins need). */
globalThis.self = {
  addEventListener() {},
  skipWaiting() {},
  location: { origin: "https://brain.example" },
};

const sw = require("../../src/herdr_brain/static/sw.js");

const ORIGIN = "https://brain.example";

/* A rendered index: every asset ref stamped ?v=<hash> by server.py and
 * the footer hash visible (exactly what GET / returns in production). */
const RENDERED_INDEX = `<!doctype html>
<html lang="es"><head>
<link rel="manifest" href="/manifest.webmanifest?v=ab69618">
<link rel="icon" href="/icon.svg?v=ab69618">
</head><body>
<script src="/app.js?v=ab69618"></script>
<script src="/toast.js?v=ab69618"></script>
<script src="/i18n.js?v=ab69618"></script>
<script src="/i18n/es.js?v=ab69618"></script>
<div id="version-footer">v<span id="app-version">vab69618</span></div>
</body></html>`;

/* The raw static index (dev fallback, no stamps rendered). */
const RAW_INDEX = `<!doctype html><html lang="es"><head>
<link rel="manifest" href="/manifest.webmanifest"></head><body>
<script src="/app.js"></script>
<div id="version-footer">v<span id="app-version">dev</span></div>
</body></html>`;

/* ---- shellFromIndex: the shell list comes from the rendered index ---- */

test("shellFromIndex reads the build from the footer and stamps every ref", () => {
  const shell = sw.shellFromIndex(RENDERED_INDEX);
  assert.equal(shell.build, "ab69618");
  assert.equal(shell.urls[0], "/");
  assert.ok(shell.urls.includes("/app.js?v=ab69618"));
  assert.ok(shell.urls.includes("/toast.js?v=ab69618"));
  assert.ok(shell.urls.includes("/i18n/es.js?v=ab69618"));
  assert.ok(shell.urls.includes("/manifest.webmanifest?v=ab69618"));
  for (const url of shell.urls) {
    assert.ok(url === "/" || url.includes("?v="), "precached asset URLs are stamped: " + url);
  }
});

test("shellFromIndex on a raw dev index keeps bare URLs and build dev", () => {
  const shell = sw.shellFromIndex(RAW_INDEX);
  assert.equal(shell.build, "dev");
  assert.ok(shell.urls.includes("/"));
  assert.ok(shell.urls.includes("/app.js"));
});

test("an empty or unparseable index still yields / with build dev", () => {
  const shell = sw.shellFromIndex("");
  assert.equal(shell.build, "dev");
  assert.deepEqual(shell.urls, ["/"]);
});

test("the cache name is namespaced by the build", () => {
  assert.equal(sw.cacheName("ab69618"), "brain-shell-ab69618");
  assert.equal(sw.cacheName("dev"), "brain-shell-dev");
});

/* ---- fetchPlan: only the explicit shell may ever hit a cache ---- */

test("root navigations are network-first with shell fallback", () => {
  assert.equal(sw.fetchPlan(new URL(ORIGIN + "/"), "GET"), "navigation");
  assert.equal(sw.fetchPlan(new URL(ORIGIN + "/?pane_id=w1:p2"), "GET"), "navigation");
  assert.equal(sw.fetchPlan(new URL(ORIGIN + "/index.html"), "GET"), "navigation");
});

test("explicit shell assets are cache-first, stamped or bare", () => {
  assert.equal(sw.fetchPlan(new URL(ORIGIN + "/app.js?v=ab69618"), "GET"), "asset");
  assert.equal(sw.fetchPlan(new URL(ORIGIN + "/i18n/en.js"), "GET"), "asset");
  assert.equal(sw.fetchPlan(new URL(ORIGIN + "/sw.js"), "GET"), "asset");
});

test("the static /history.js asset is NOT the /history API", () => {
  assert.equal(sw.fetchPlan(new URL(ORIGIN + "/history.js?v=ab69618"), "GET"), "asset");
  assert.equal(sw.fetchPlan(new URL(ORIGIN + "/history"), "GET"), "bypass");
});

test("every API route is bypassed, denylisted or not", () => {
  for (const path of [
    "/events", "/ask", "/transcribe", "/audio/x.mp3", "/history",
    "/view", "/herd", "/push/key", "/msg/send", "/operator/config",
    "/pane/reply", "/pane/keys", "/approval/current", "/call/context",
    "/call-history", "/devices", "/pair", "/build", "/conversation",
  ]) {
    assert.equal(sw.fetchPlan(new URL(ORIGIN + path), "GET"), "bypass", path);
  }
});

test("non-GET requests are always bypassed", () => {
  assert.equal(sw.fetchPlan(new URL(ORIGIN + "/app.js"), "POST"), "bypass");
  assert.equal(sw.fetchPlan(new URL(ORIGIN + "/ask"), "POST"), "bypass");
});

test("cross-origin requests are always bypassed", () => {
  assert.equal(sw.fetchPlan(new URL("https://evil.example/app.js"), "GET"), "bypass");
});

test("an unknown same-origin file is bypassed (default deny)", () => {
  assert.equal(sw.fetchPlan(new URL(ORIGIN + "/mystery.js"), "GET"), "bypass");
});

/* ---- response admission: only 200 non-redirected basic ---- */

test("a 200 basic non-redirected response is cacheable", () => {
  assert.equal(sw.cacheableResponse(200, false, "basic"), true);
});

test("redirects, errors and opaque responses are never cached", () => {
  assert.equal(sw.cacheableResponse(302, true, "basic"), false);
  assert.equal(sw.cacheableResponse(301, true, "opaqueredirect"), false);
  assert.equal(sw.cacheableResponse(204, false, "basic"), false);
  assert.equal(sw.cacheableResponse(206, false, "basic"), false);
  assert.equal(sw.cacheableResponse(404, false, "basic"), false);
  assert.equal(sw.cacheableResponse(500, false, "basic"), false);
  assert.equal(sw.cacheableResponse(200, false, "opaque"), false);
});

/* ---- activate sweep: delete every other brain cache ---- */

test("activate sweeps every brain-shell cache but the current build", () => {
  assert.deepEqual(
    sw.cachesToSweep(
      ["brain-shell-old1", "brain-shell-old2", "brain-shell-keep", "someone-else"],
      "keep"
    ),
    ["brain-shell-old1", "brain-shell-old2"]
  );
});

test("the sweep leaves unrelated caches alone", () => {
  assert.deepEqual(sw.cachesToSweep(["push-service", "other"], "keep"), []);
});
