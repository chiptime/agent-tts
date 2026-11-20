/* Structural wiring tests for the crew /herd contract in the PWA
 * (parity T1, S1).
 *
 * LIMITS, stated plainly: app.js is a single browser-only IIFE (DOM,
 * EventSource, live audio element) that node --test cannot load, so the
 * unwrap itself cannot be EXECUTED here. These tests pin the wiring
 * SHAPE by reading the shipped sources as text — exactly the
 * discipline of agent-tabs-wiring.test.js and pending-wiring.test.js.
 * The real rendering (local + remote rows, unreachable peer chips, the
 * remote tap's selection flow) is Chromium territory and lives in
 * tests/browser/test_crew_herd_pwa.py.
 *
 * The regression this file guards: the crew lead's /herd answers
 * {"panes", "peers"} while refreshHerd passed that OBJECT straight into
 * renderHerd, which accepts only arrays — every crewed install then
 * rendered zero panes on the phone (the audit's blocker).
 */

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const appSrc = fs.readFileSync(
  path.join(__dirname, "../../src/herdr_brain/static/app.js"), "utf8");

function fnBody(source, name) {
  const start = source.indexOf("function " + name + "(");
  assert.notEqual(start, -1, name + " must exist in app.js");
  const end = start < 0 ? start : findBraceEnd(source, start);
  return source.slice(start, end);
}

function findBraceEnd(source, openIdx) {
  const first = source.indexOf("{", openIdx);
  let depth = 0;
  for (let i = first; i < source.length; i++) {
    const ch = source[i];
    if (ch === "{") depth++;
    else if (ch === "}") {
      depth--;
      if (depth === 0) return i + 1;
    }
  }
  return source.length;
}

test("refreshHerd unwraps the crew {panes, peers} payload before renderHerd", () => {
  const body = fnBody(appSrc, "refreshHerd");
  assert.ok(
    body.includes("Array.isArray(herd.panes)"),
    "refreshHerd must detect the crew object shape"
  );
  assert.ok(
    body.includes("herd.peers"),
    "refreshHerd must carry the peers list to the renderer"
  );
});

test("remote panes are keyed by the qualified <host>:<pane> address", () => {
  const helper = fnBody(appSrc, "paneRowId");
  assert.ok(
    helper.includes('agent.host + ":" + agent.pane_id'),
    "the row id helper must qualify remote panes by host"
  );
  const body = fnBody(appSrc, "renderHerd");
  assert.ok(
    body.includes("paneRowId(agent)"),
    "rows must key on the qualified address (two hosts may share a bare id)"
  );
  assert.ok(
    body.includes("cr-host"),
    "remote rows must carry a host chip so local/remote are told apart"
  );
  assert.ok(
    body.includes("peer:") && body.includes("herd.peer_unreachable"),
    "unreachable peers must render as explicit rows, never vanish"
  );
});

test("a remote row keeps the ordinary selection flow — nothing is stripped", () => {
  const body = fnBody(appSrc, "renderHerd");
  // The rows' click handler routes through selectAgent for remote ids too:
  // the server's REST crew seam already proxies /view, /conversation and
  // writes for <host>:<pane> ids, so selection keeps working end to end.
  assert.ok(
    body.includes("selectAgent(paneId)"),
    "row taps must select through the ordinary flow"
  );
  assert.ok(
    !body.includes("herd.remote_readonly"),
    "no refusal path may block remote selection (REST proxies it)"
  );
  // A remote selection must SURVIVE the 5s poll: the effective-pane
  // matcher understands qualified ids, or the tap would fall back.
  const effBody = fnBody(appSrc, "effectiveSelected");
  assert.ok(
    effBody.includes("herdRowMatches"),
    "effective selection must match qualified remote ids"
  );
  const lookup = fnBody(appSrc, "herdEntry");
  assert.ok(
    lookup.includes("herdRowMatches"),
    "herd lookups (stop model, glance labels) must resolve qualified ids"
  );
});

test("the i18n dictionaries ship the unreachable-peer key in both locales", () => {
  for (const locale of ["es", "en"]) {
    const src = fs.readFileSync(
      path.join(__dirname, "../../src/herdr_brain/static/i18n/" + locale + ".js"),
      "utf8");
    assert.ok(src.includes('"herd.peer_unreachable"'),
      locale + " locale must define herd.peer_unreachable");
  }
});
