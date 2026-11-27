/* node --test suite for the Ajustes status cards (POST-08).
 *
 * The fake DOM implements only the surface settings.js's
 * createStatusCards touches (same plain-object approach as
 * push.test.js: no jsdom, no dependencies) — plus a hard C1 pin:
 * `innerHTML` THROWS on these nodes, so any use of innerHTML with
 * server data fails the suite instead of passing silently. The seams
 * are injected exactly as app.js wires them (request = readFetch,
 * t = the i18n runtime), so the suite pins the wire shapes:
 * GET /quotas + GET /load fired together on refresh, fail-soft on
 * non-ok/throw, honest unavailable/stale/fail states, and the pure
 * formatters (device-local reset stamp, B/s scaling, percent clamp). */

const test = require("node:test");
const assert = require("node:assert");
const { createStatusCards, formatResetLocal, formatBps, clampPercent } =
  require("../../src/herdr_brain/static/settings.js");

/* ---- fake DOM (test infrastructure, push.test.js subset) ---- */

function makeNode() {
  const node = {
    attributes: {},
    children: [],
    textContent: "",
    value: "",
    disabled: false,
    style: {},
    listeners: {},
    get firstChild() { return node.children[0] || null; },
    removeChild(child) {
      const at = node.children.indexOf(child);
      if (at !== -1) node.children.splice(at, 1);
      return child;
    },
    get innerHTML() { throw new Error("innerHTML is forbidden (C1): use textContent"); },
    set innerHTML(_) { throw new Error("innerHTML is forbidden (C1): use textContent"); },
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
    focus() {},
  };
  return node;
}

function makeDoc() {
  const ids = {};
  return {
    createElement: () => makeNode(),
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
    const next = responder.queue.shift();
    if (next && next.reject) return Promise.reject(new Error("network down"));
    if (next === undefined) return Promise.reject(new Error("no scripted response"));
    return Promise.resolve({
      status: next.status,
      ok: next.status >= 200 && next.status < 300,
      json: () => Promise.resolve(next.json),
    });
  };
  return { request, calls, responder };
}

/* Queue order: [quotasResponse, loadResponse] per refresh. */
function makeCards(translator) {
  const http = makeRequest();
  const doc = makeDoc();
  let t = translator || null;
  const cards = createStatusCards({
    request: http.request,
    doc,
    t: (key) => (t ? t(key) : null),
  });
  return {
    cards, http, doc,
    setTranslator: (fn) => { t = fn; },
    quotasBox: () => doc.getElementById("quotas-box"),
    loadBox: () => doc.getElementById("load-box"),
  };
}

function find(root, className) {
  const walk = (node) => {
    for (const child of node.children || []) {
      if ((child.attributes["class"] || "").split(/\s+/).includes(className)) return child;
      const found = walk(child);
      if (found) return found;
    }
    return null;
  };
  return walk(root);
}

function findAll(root, className) {
  const out = [];
  const walk = (node) => {
    for (const child of node.children || []) {
      if ((child.attributes["class"] || "").split(/\s+/).includes(className)) out.push(child);
      walk(child);
    }
  };
  walk(root);
  return out;
}

const QUOTAS_OK = {
  available: true,
  reason: "",
  stale: false,
  fetched_at: "2026-11-26T17:00:00+00:00",
  providers: [
    {
      provider: "claude",
      display_name: "Claude",
      kind: "subscription",
      label: "5h window",
      used_percent: 42,
      resets_at: "2026-11-26T21:59:00+00:00",
      state: "warning",
      status_indicator: null,
      status_summary: null,
      detail: "session roll soon",
      cost_today_usd: 2.1,
      cost_month_to_date_usd: 14.5,
    },
    {
      provider: "gemini",
      display_name: "Gemini",
      kind: "api",
      label: null,
      used_percent: null,
      resets_at: null,
      state: "ok",
      status_indicator: null,
      status_summary: null,
      detail: null,
      cost_today_usd: null,
      cost_month_to_date_usd: null,
    },
  ],
};

const LOAD_OK = {
  host: "harness",
  available: true,
  ts: "2026-11-26T17:00:00+00:00",
  cpu_percent: 63.4,
  ram_percent: 41,
  disk_percent: 88,
  net_rx_bps: 1234,
  net_tx_bps: 512,
  alerts: { configured: 2, active: 1 },
};

function queueOk(http, quotas, load) {
  http.responder.queue = [
    { status: 200, json: quotas === undefined ? QUOTAS_OK : quotas },
    { status: 200, json: load === undefined ? LOAD_OK : load },
  ];
}

/* ---- pure formatters ---- */

test("formatResetLocal pins the backend's voice format (Thu 18:59) in device-local time", () => {
  const iso = "2026-11-26T21:59:00Z";
  const moment = new Date(iso);
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const hh = String(moment.getHours()).padStart(2, "0");
  const mm = String(moment.getMinutes()).padStart(2, "0");
  assert.equal(formatResetLocal(iso), `${days[moment.getDay()]} ${hh}:${mm}`);
  assert.equal(formatResetLocal(null), null);
  assert.equal(formatResetLocal(""), null);
  assert.equal(formatResetLocal("not-a-date"), null);
  assert.equal(formatResetLocal(42), null);
});

test("formatBps scales B/s through GB/s and refuses non-numbers", () => {
  assert.equal(formatBps(0), "0 B/s");
  assert.equal(formatBps(512), "512 B/s");
  assert.equal(formatBps(1024), "1.0 KB/s");
  assert.equal(formatBps(1234), "1.2 KB/s");
  assert.equal(formatBps(1572864), "1.5 MB/s");
  assert.equal(formatBps(1610612736), "1.5 GB/s");
  assert.equal(formatBps(null), null);
  assert.equal(formatBps(-5), null);
  assert.equal(formatBps(Number.NaN), null);
  assert.equal(formatBps("1234"), null);
});

test("clampPercent keeps bars in 0-100 and only accepts finite numbers", () => {
  assert.equal(clampPercent(42), 42);
  assert.equal(clampPercent(0), 0);
  assert.equal(clampPercent(100), 100);
  assert.equal(clampPercent(150), 100);
  assert.equal(clampPercent(-5), 0);
  assert.equal(clampPercent(Number.NaN), null);
  assert.equal(clampPercent("42"), null);
  assert.equal(clampPercent(null), null);
});

/* ---- refresh wire ---- */

test("refresh fires /quotas and /load together with no-store, never anything else", async () => {
  const made = makeCards();
  queueOk(made.http);
  await made.cards.refresh();
  assert.deepEqual(made.http.calls.map((call) => call.url), ["/quotas", "/load"]);
  for (const call of made.http.calls) assert.equal(call.options.cache, "no-store");
});

test("refresh paints quota rows: name, clamped bar, chip, meta with resets and costs", async () => {
  const made = makeCards();
  queueOk(made.http);
  await made.cards.refresh();
  const box = made.quotasBox();
  const rows = findAll(box, "quota-row");
  assert.equal(rows.length, 2);

  const first = rows[0];
  assert.equal(find(first, "quota-name").textContent, "Claude");
  const chip = find(first, "quota-state");
  assert.ok((chip.attributes["class"] || "").includes("warning"));
  assert.equal(chip.textContent, "warning");
  const fill = find(first, "quota-bar-fill");
  assert.equal(fill.style.width, "42%");
  const meta = find(first, "quota-meta").textContent;
  assert.ok(meta.includes("42%"), meta);
  assert.ok(meta.includes("5h window"), meta);
  assert.ok(meta.includes("session roll soon"), meta);
  assert.ok(/Renueva (Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{2}:\d{2}/.test(meta), meta);
  assert.ok(meta.includes("$2.10 hoy"), meta);
  assert.ok(meta.includes("$14.50 mes"), meta);

  /* gemini: no usable percent -> no bar; ok chip; no meta junk */
  const second = rows[1];
  assert.equal(find(second, "quota-name").textContent, "Gemini");
  assert.equal(find(second, "quota-bar-fill"), null);
  assert.ok((find(second, "quota-state").attributes["class"] || "").includes("ok"));
});

test("stale snapshot shows the cached-data hint", async () => {
  const made = makeCards();
  queueOk(made.http, Object.assign({}, QUOTAS_OK, { stale: true }));
  await made.cards.refresh();
  assert.ok(find(made.quotasBox(), "quota-stale"));
});

test("unavailable quotas answer renders the honest message plus reason, no rows", async () => {
  const made = makeCards();
  queueOk(made.http, { available: false, reason: "ai-quotas-not-found", stale: false, fetched_at: null, providers: [] });
  await made.cards.refresh();
  const box = made.quotasBox();
  assert.equal(findAll(box, "quota-row").length, 0);
  assert.ok(find(box, "push-status"));
  assert.equal(find(box, "quota-reason").textContent, "ai-quotas-not-found");
});

test("load card renders host, metric bars, net line and the alerts row", async () => {
  const made = makeCards();
  queueOk(made.http);
  await made.cards.refresh();
  const box = made.loadBox();
  assert.equal(find(box, "push-status").textContent, "harness");
  const rows = findAll(box, "load-row");
  assert.equal(rows.length, 3);  /* CPU, RAM, Disco */
  const diskRow = rows[2];
  assert.equal(find(diskRow, "load-value").textContent, "88%");
  assert.equal(find(diskRow, "quota-bar-fill").style.width, "88%");
  const net = find(box, "load-net").textContent;
  assert.ok(net.includes("↓ 1.2 KB/s"), net);
  assert.ok(net.includes("↑ 512 B/s"), net);
  const alerts = find(box, "load-alerts").textContent;
  assert.ok(alerts.includes("1/2"), alerts);
});

test("load card with zero configured alerts renders no alerts row", async () => {
  const made = makeCards();
  queueOk(made.http, QUOTAS_OK, Object.assign({}, LOAD_OK, { alerts: { configured: 0, active: 0 } }));
  await made.cards.refresh();
  assert.equal(find(made.loadBox(), "load-alerts"), null);
});

test("load card before the first sample shows the waiting state, no bars", async () => {
  const made = makeCards();
  queueOk(made.http, QUOTAS_OK, {
    host: "harness", available: true, ts: null,
    cpu_percent: null, ram_percent: null, disk_percent: null,
    net_rx_bps: null, net_tx_bps: null, alerts: { configured: 0, active: 0 },
  });
  await made.cards.refresh();
  const box = made.loadBox();
  assert.equal(findAll(box, "load-row").length, 0);
  assert.ok(find(box, "load-waiting"));
});

test("load unavailable renders reason and no metric rows", async () => {
  const made = makeCards();
  queueOk(made.http, QUOTAS_OK, {
    host: "harness", available: false,
    reason: "load metrics read Linux /proc (unavailable on darwin)",
    alerts: { configured: 0, active: 0 },
  });
  await made.cards.refresh();
  const box = made.loadBox();
  assert.equal(findAll(box, "load-row").length, 0);
  assert.equal(find(box, "quota-reason").textContent,
    "load metrics read Linux /proc (unavailable on darwin)");
});

test("a 500 answer fails soft: status line, no throw, the other card still renders", async () => {
  const made = makeCards();
  made.http.responder.queue = [
    { status: 500, json: { detail: "boom" } },
    { status: 200, json: LOAD_OK },
  ];
  await made.cards.refresh();
  assert.equal(made.cards.state().quotas.failed, true);
  assert.equal(made.cards.state().load.failed, false);
  assert.ok(find(made.quotasBox(), "push-status"));
  assert.equal(findAll(made.quotasBox(), "quota-row").length, 0);
  assert.equal(findAll(made.loadBox(), "load-row").length, 3);
  assert.ok(!made.cards.state().busy);
});

test("a rejected request fails soft and the busy flag clears", async () => {
  const made = makeCards();
  made.http.responder.queue = [{ reject: true }, { reject: true }];
  await made.cards.refresh();
  assert.equal(made.cards.state().quotas.failed, true);
  assert.equal(made.cards.state().load.failed, true);
  assert.ok(find(made.quotasBox(), "push-status"));
  assert.ok(!made.cards.state().busy);
});

test("each ↻ refetches only its own endpoint", async () => {
  const made = makeCards();
  queueOk(made.http);
  await made.cards.refresh();
  assert.equal(made.http.calls.length, 2);
  const quotasBtn = find(made.quotasBox(), "cards-refresh");
  assert.ok(find(made.loadBox(), "cards-refresh"));
  quotasBtn.dispatch("click", {});
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(made.http.calls.slice(2).map((call) => call.url), ["/quotas"]);
  find(made.loadBox(), "cards-refresh").dispatch("click", {});
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(made.http.calls.slice(3).map((call) => call.url), ["/load"]);
});

test("refresh while in flight never doubles the wire", async () => {
  const made = makeCards();
  queueOk(made.http);
  const first = made.cards.refresh();
  const second = made.cards.refresh();
  assert.ok(second && typeof second.then === "function");
  await first;
  await second;
  assert.equal(made.http.calls.length, 2);
  assert.ok(!made.cards.state().busy);
});

test("render() repaints cached payloads through the current translator", async () => {
  let suffix = "";
  const made = makeCards((key) => {
    const base = {
      "quotas.title": "AI quotas",
      "load.title": "Host load",
      "quotas.resets": "Resets {when}",
    }[key];
    return base ? base + (key === "quotas.resets" ? "" : suffix) : null;
  });
  queueOk(made.http);
  await made.cards.refresh();
  assert.equal(find(made.quotasBox(), "push-title").textContent, "AI quotas");
  suffix = "-es";
  made.cards.render();
  assert.equal(find(made.quotasBox(), "push-title").textContent, "AI quotas-es");
  assert.equal(find(made.loadBox(), "push-title").textContent, "Host load-es");
  /* the i18n KEY name is pinned: quotas.resets routes the reset stamp */
  assert.ok(/Resets /.test(find(made.quotasBox(), "quota-meta").textContent));
});

test("hostile server strings survive as text — innerHTML is never touched (C1)", async () => {
  const made = makeCards();
  const hostile = Object.assign({}, QUOTAS_OK, {
    providers: [Object.assign({}, QUOTAS_OK.providers[0], {
      display_name: "<img src=x onerror=alert(1)>",
      detail: "</div><script>steal()</script>",
    })],
  });
  queueOk(made.http, hostile);
  await made.cards.refresh();  /* fake nodes throw on ANY innerHTML use */
  const name = find(made.quotasBox(), "quota-name");
  assert.equal(name.textContent, "<img src=x onerror=alert(1)>");
  assert.ok(find(made.quotasBox(), "quota-meta").textContent.includes("</div><script>steal()</script>"));
});
