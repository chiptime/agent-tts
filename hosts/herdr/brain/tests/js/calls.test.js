/* Unit tests for the Calls UI module (static/calls.js).
 * Pure-logic / fake-DOM tests verifying createCallsUI contract,
 * state machine (list vs detail), dependency injection, and no direct globals.
 */

const test = require("node:test");
const assert = require("node:assert");
const path = require("node:path");

const CallsModule = require(path.join(__dirname, "../../src/herdr_brain/static/calls.js"));

function makeElement(tagName) {
  const children = [];
  const classes = new Set();
  const attributes = {};
  const listeners = {};
  let text = "";

  return {
    tagName: tagName.toUpperCase(),
    get textContent() {
      if (children.length === 0) return text;
      return children.map(c => c.textContent).join(" ");
    },
    set textContent(v) {
      text = String(v);
    },
    children,
    classList: {
      add(...cls) { cls.forEach(c => classes.add(c)); },
      remove(...cls) { cls.forEach(c => classes.delete(c)); },
      contains(c) { return classes.has(c); },
      get length() { return classes.size; },
    },
    setAttribute(name, value) { attributes[name] = String(value); },
    getAttribute(name) { return attributes[name] || null; },
    removeAttribute(name) { delete attributes[name]; },
    appendChild(child) {
      children.push(child);
      child.parentNode = this;
      return child;
    },
    removeChild(child) {
      const idx = children.indexOf(child);
      if (idx >= 0) children.splice(idx, 1);
      return child;
    },
    addEventListener(event, fn) {
      if (!listeners[event]) listeners[event] = [];
      listeners[event].push(fn);
    },
    click() {
      if (listeners["click"]) {
        listeners["click"].forEach(fn => fn({ currentTarget: this }));
      }
    },
    querySelector(selector) {
      // Simple selector matching class or tag
      function match(node) {
        if (!node) return null;
        if (selector.startsWith(".") && node.classList && node.classList.contains(selector.slice(1))) {
          return node;
        }
        if (node.tagName === selector.toUpperCase()) return node;
        for (const child of node.children || []) {
          const found = match(child);
          if (found) return found;
        }
        return null;
      }
      return match(this);
    },
    querySelectorAll(selector) {
      const results = [];
      function collect(node) {
        if (!node) return;
        if (selector.startsWith(".") && node.classList && node.classList.contains(selector.slice(1))) {
          results.push(node);
        } else if (node.tagName === selector.toUpperCase()) {
          results.push(node);
        }
        for (const child of node.children || []) {
          collect(child);
        }
      }
      collect(this);
      return results;
    },
  };
}

function makeDoc() {
  return {
    createElement(tagName) {
      return makeElement(tagName);
    },
  };
}

function makeTranslator() {
  return function t(key, vars) {
    if (!vars) return `[${key}]`;
    let res = `[${key}]`;
    for (const [k, v] of Object.entries(vars)) {
      res += ` ${k}=${v}`;
    }
    return res;
  };
}

test("createCallsUI requires doc, http and t dependencies", () => {
  const doc = makeDoc();
  const t = makeTranslator();
  const http = () => Promise.resolve({});

  assert.throws(() => CallsModule.createCallsUI({}), /deps\.doc/);
  assert.throws(() => CallsModule.createCallsUI({ doc }), /deps\.http/);
  assert.throws(() => CallsModule.createCallsUI({ doc, http }), /deps\.t/);

  const ui = CallsModule.createCallsUI({ doc, http, t });
  assert.equal(typeof ui.mount, "function");
  assert.equal(typeof ui.refresh, "function");
});

test("mount and render calls list with status, turns, and duration", async () => {
  const doc = makeDoc();
  const t = makeTranslator();
  const callsData = {
    calls: [
      {
        call_id: "call-1",
        started_at: "2026-11-03T10:00:00Z",
        ended_at: "2026-11-03T10:02:00Z",
        duration_s: 120.0,
        turn_count: 4,
        status: "ended",
      },
      {
        call_id: "call-2",
        started_at: "2026-11-03T10:05:00Z",
        ended_at: "2026-11-03T10:06:00Z",
        duration_s: 60.0,
        turn_count: 1,
        status: "interrupted",
      },
    ],
    has_more: false,
  };

  const http = (url) => {
    if (url.startsWith("/calls")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(callsData) });
    }
    return Promise.reject(new Error("unexpected url: " + url));
  };

  const container = doc.createElement("div");
  const ui = CallsModule.createCallsUI({ doc, http, t });
  ui.mount(container);

  // Wait a microtask tick for async fetch to resolve
  await new Promise(resolve => setTimeout(resolve, 10));

  const items = container.querySelectorAll(".call-item");
  assert.equal(items.length, 2);

  const first = items[0];
  assert.ok(first.textContent.includes("[calls.status_ended]"));
  assert.ok(first.textContent.includes("count=4"));

  const second = items[1];
  assert.ok(second.textContent.includes("[calls.status_interrupted]"));
  assert.ok(second.textContent.includes("count=1"));
});

test("empty state renders when calls list is empty", async () => {
  const doc = makeDoc();
  const t = makeTranslator();
  const http = () => Promise.resolve({
    ok: true,
    json: () => Promise.resolve({ calls: [], has_more: false }),
  });

  const container = doc.createElement("div");
  const ui = CallsModule.createCallsUI({ doc, http, t });
  ui.mount(container);

  await new Promise(resolve => setTimeout(resolve, 10));

  const emptyEl = container.querySelector(".calls-empty");
  assert.ok(emptyEl);
  assert.equal(emptyEl.classList.contains("hidden"), false);
  assert.equal(emptyEl.textContent, "[calls.empty]");
});

test("error state renders when http rejects", async () => {
  const doc = makeDoc();
  const t = makeTranslator();
  const http = () => Promise.reject(new Error("network error"));

  const container = doc.createElement("div");
  const ui = CallsModule.createCallsUI({ doc, http, t });
  ui.mount(container);

  await new Promise(resolve => setTimeout(resolve, 10));

  const errorEl = container.querySelector(".calls-error");
  assert.ok(errorEl);
  assert.equal(errorEl.classList.contains("hidden"), false);
  assert.equal(errorEl.textContent, "[calls.error]");
});

test("detail view shows a resume button that calls onResume with the call", async () => {
  const doc = makeDoc();
  const t = makeTranslator();
  const callsData = {
    calls: [
      {
        call_id: "call-7",
        started_at: "2026-11-03T10:00:00Z",
        ended_at: "2026-11-03T10:01:00Z",
        duration_s: 60.0,
        turn_count: 2,
        status: "ended",
      },
    ],
    has_more: false,
  };
  const detailData = { call: callsData.calls[0], turns: [], has_more: false };

  const http = (url) => {
    if (url === "/calls" || url.startsWith("/calls?")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(callsData) });
    }
    if (url === "/calls/call-7" || url.startsWith("/calls/call-7?")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(detailData) });
    }
    return Promise.reject(new Error("unexpected: " + url));
  };

  const resumed = [];
  const container = doc.createElement("div");
  const ui = CallsModule.createCallsUI({
    doc, http, t,
    onResume: (call) => resumed.push(call),
  });
  ui.mount(container);

  await new Promise(resolve => setTimeout(resolve, 10));

  const items = container.querySelectorAll(".call-item");
  items[0].click();
  await new Promise(resolve => setTimeout(resolve, 10));

  const resumeBtn = container.querySelector(".calls-resume-btn");
  assert.ok(resumeBtn, "resume button renders in detail view");
  assert.ok(resumeBtn.textContent.includes("[calls.resume]"));
  assert.equal(resumed.length, 0);
  resumeBtn.click();
  assert.equal(resumed.length, 1);
  assert.equal(resumed[0].call_id, "call-7");
});

test("no resume button when onResume is not provided", async () => {
  const doc = makeDoc();
  const t = makeTranslator();
  const callsData = {
    calls: [
      {
        call_id: "call-8",
        started_at: "2026-11-03T10:00:00Z",
        ended_at: null,
        duration_s: null,
        turn_count: 0,
        status: "open",
      },
    ],
    has_more: false,
  };
  const detailData = { call: callsData.calls[0], turns: [], has_more: false };

  const http = (url) => {
    if (url === "/calls" || url.startsWith("/calls?")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(callsData) });
    }
    if (url === "/calls/call-8" || url.startsWith("/calls/call-8?")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(detailData) });
    }
    return Promise.reject(new Error("unexpected: " + url));
  };

  const container = doc.createElement("div");
  const ui = CallsModule.createCallsUI({ doc, http, t });
  ui.mount(container);

  await new Promise(resolve => setTimeout(resolve, 10));
  container.querySelectorAll(".call-item")[0].click();
  await new Promise(resolve => setTimeout(resolve, 10));

  assert.equal(container.querySelector(".calls-resume-btn"), null);
});

test("detail shows the retake time when the call was resumed", async () => {
  const doc = makeDoc();
  const t = makeTranslator();
  const callsData = {
    calls: [
      {
        call_id: "call-9",
        started_at: "2026-11-03T10:00:00Z",
        ended_at: null,
        duration_s: null,
        turn_count: 3,
        status: "open",
        resumed_at: "2026-11-04T13:00:00Z",
      },
    ],
    has_more: false,
  };
  const detailData = { call: callsData.calls[0], turns: [], has_more: false };

  const http = (url) => {
    if (url === "/calls" || url.startsWith("/calls?")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(callsData) });
    }
    if (url === "/calls/call-9" || url.startsWith("/calls/call-9?")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(detailData) });
    }
    return Promise.reject(new Error("unexpected: " + url));
  };

  const container = doc.createElement("div");
  const ui = CallsModule.createCallsUI({ doc, http, t: t });
  ui.mount(container);

  await new Promise(resolve => setTimeout(resolve, 10));
  container.querySelectorAll(".call-item")[0].click();
  await new Promise(resolve => setTimeout(resolve, 10));

  const resumedEl = container.querySelector(".call-meta-resumed");
  assert.ok(resumedEl, "resumed marker renders in detail header");
  assert.ok(resumedEl.textContent.includes("[calls.resumed_at]"));
  assert.ok(resumedEl.textContent.includes("time="));
  // The ORIGINAL start time stays as the call's time.
  const header = container.querySelector(".calls-detail-header");
  assert.ok(header.textContent.includes("[calls.resumed_at]"));
});

test("tapping a call opens detail view with transcript turns and back button", async () => {
  const doc = makeDoc();
  const t = makeTranslator();
  const callsData = {
    calls: [
      {
        call_id: "call-42",
        started_at: "2026-11-03T10:00:00Z",
        ended_at: "2026-11-03T10:01:00Z",
        duration_s: 60.0,
        turn_count: 2,
        status: "ended",
      },
    ],
    has_more: false,
  };
  const detailData = {
    call: callsData.calls[0],
    turns: [
      { ts: "2026-11-03T10:00:05Z", role: "user", text: "¿hola cómo estás?" },
      { ts: "2026-11-03T10:00:10Z", role: "assistant", text: "¡muy bien!" },
    ],
    has_more: false,
  };

  const http = (url) => {
    if (url === "/calls" || url.startsWith("/calls?")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(callsData) });
    }
    if (url === "/calls/call-42" || url.startsWith("/calls/call-42?")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(detailData) });
    }
    return Promise.reject(new Error("unexpected: " + url));
  };

  const container = doc.createElement("div");
  const ui = CallsModule.createCallsUI({ doc, http, t });
  ui.mount(container);

  await new Promise(resolve => setTimeout(resolve, 10));

  const items = container.querySelectorAll(".call-item");
  assert.equal(items.length, 1);

  // Click on the first call item
  items[0].click();

  await new Promise(resolve => setTimeout(resolve, 10));

  // Now detail view should be visible
  const turns = container.querySelectorAll(".call-turn");
  assert.equal(turns.length, 2);
  assert.ok(turns[0].textContent.includes("¿hola cómo estás?"));
  assert.ok(turns[1].textContent.includes("¡muy bien!"));

  // Click back button
  const backBtn = container.querySelector(".calls-back-btn");
  assert.ok(backBtn);
  backBtn.click();

  // Returned to list view
  const listView = container.querySelector(".calls-list-view");
  assert.ok(listView);
  assert.equal(listView.classList.contains("hidden"), false);
});
