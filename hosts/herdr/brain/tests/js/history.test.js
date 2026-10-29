/* Pure-logic tests for the reading-view module (static/history.js):
 * cursor pagination state, page merging without duplicates and URL
 * building for /history and /pane/{id}/mirror. No DOM here — the module
 * is consumed by app.js, whose real Chromium behavior is proven in
 * tests/browser/test_history_view.py and test_mirror_view.py.
 *
 * The contract mirrored from the server (history_view.py):
 * - every page arrives in CHRONOLOGICAL order; older pages must be
 *   PREPENDED under what is already held;
 * - `cursor` is present only while `has_more` (the next page's cursor),
 *   and the first page is requested without one;
 * - an agent without a readable history answers an ordinary empty page
 *   with reason "no-session", never an error. */

const test = require("node:test");
const assert = require("node:assert");
const path = require("node:path");

const HistoryView = require(
  path.join(__dirname, "../../src/herdr_brain/static/history.js"));

const PAGE_LIMIT = 60;

function turn(role, text, created_at) {
  return { role, text, created_at: created_at || null };
}

function page(turns, extra) {
  return Object.assign(
    { pane_id: "w1:p1", agent: "opencode", session_id: "s1", title: "T",
      turns, reason: null, cursor: null, has_more: false, limit: PAGE_LIMIT },
    extra || {});
}

test("first page: issues a cursor-less request and stores the page", () => {
  const model = HistoryView.createHistoryModel();
  const first = model.firstPage("w1:p1");
  assert.equal(first.url, "/history?pane_id=w1%3Ap1&limit=60");

  const result = model.applyFirstPage(page(
    [turn("user", "hola"), turn("assistant", "qué tal")],
    { cursor: "b2", has_more: true }));
  assert.deepEqual(result.turns.map(t => t.text), ["hola", "qué tal"]);

  const st = model.state();
  assert.equal(st.paneId, "w1:p1");
  assert.equal(st.cursor, "b2");
  assert.equal(st.hasMore, true);
  assert.equal(st.loading, false);
  assert.equal(st.count, 2);
});

test("no second request while one is in flight (first or older)", () => {
  const model = HistoryView.createHistoryModel();
  assert.ok(model.firstPage("w1:p1"));
  assert.equal(model.firstPage("w1:p1"), null);
  assert.equal(model.olderPage(), null, "loading blocks older pages too");
});

test("a loaded first page is not re-requested for the same pane", () => {
  const model = HistoryView.createHistoryModel();
  model.firstPage("w1:p1");
  model.applyFirstPage(page([turn("user", "x")]));
  assert.equal(model.firstPage("w1:p1"), null);
  assert.ok(model.firstPage("w2:p1"), "another pane starts fresh");
});

test("fail clears the in-flight flag so the view can retry", () => {
  const model = HistoryView.createHistoryModel();
  model.firstPage("w1:p1");
  model.fail();
  assert.equal(model.state().loading, false);
  assert.ok(model.firstPage("w1:p1"));
});

test("older page: requests the stored cursor and prepends without overlap", () => {
  const model = HistoryView.createHistoryModel();
  model.firstPage("w1:p1");
  model.applyFirstPage(page(
    [turn("user", "c", "t3"), turn("assistant", "d", "t4")],
    { cursor: "b2", has_more: true }));

  const older = model.olderPage();
  assert.equal(older.url, "/history?pane_id=w1%3Ap1&cursor=b2&limit=60");

  const result = model.applyOlderPage(page(
    [turn("user", "a", "t1"), turn("assistant", "b", "t2")],
    { cursor: null, has_more: false }));
  assert.deepEqual(result.prepend.map(t => t.text), ["a", "b"]);
  assert.deepEqual(
    model.state().count, 4, "held turns grow by the prepended ones only");
  assert.equal(model.state().hasMore, false);
  assert.equal(model.olderPage(), null, "exhausted cursor issues nothing");
});

test("older page overlapping a grown transcript never duplicates turns", () => {
  /* The transcript GREW between the first page and the older-page fetch:
   * the cursor counts from the newest, so the older page re-serves the
   * boundary turns already held. The overlap sits at the incoming page's
   * tail (its newest end) against the held list's head (its oldest end). */
  const model = HistoryView.createHistoryModel();
  model.firstPage("w1:p1");
  model.applyFirstPage(page(
    [turn("user", "b", "t2"), turn("assistant", "c", "t3")],
    { cursor: "b2", has_more: true }));
  const result = model.applyOlderPage(page(
    [turn("user", "a0", "t0"), turn("user", "b", "t2"), turn("assistant", "c", "t3")],
    { cursor: null, has_more: false }));
  assert.deepEqual(result.prepend.map(t => t.text), ["a0"]);
  assert.equal(model.state().count, 3);
});

test("boundary trim keeps multiplicity: only the true overlap goes", () => {
  const held = [turn("user", "a"), turn("user", "a")];
  const incoming = [turn("user", "z"), turn("user", "a"), turn("user", "a"), turn("user", "a")];
  const trimmed = HistoryView.trimBoundaryOverlap(held, incoming);
  assert.deepEqual(trimmed.map(t => t.text), ["z", "a"],
    "two boundary matches trim exactly two copies");
});

test("no-session and empty pages are ordinary empty states", () => {
  const model = HistoryView.createHistoryModel();
  model.firstPage("w1:p1");
  const result = model.applyFirstPage(page([], { reason: "no-session" }));
  assert.deepEqual(result.turns, []);
  assert.equal(model.state().reason, "no-session");
  assert.equal(model.state().hasMore, false);
  assert.equal(model.olderPage(), null);
});

test("historyUrl encodes its parts and omits absent ones", () => {
  assert.equal(HistoryView.historyUrl("w1:p1", null, 60),
    "/history?pane_id=w1%3Ap1&limit=60");
  assert.equal(HistoryView.historyUrl("w1:p1", "b2", 60),
    "/history?pane_id=w1%3Ap1&cursor=b2&limit=60");
  assert.equal(HistoryView.historyUrl(null, null, 60), "/history?limit=60");
});

test("mirrorUrl addresses one pane with the server-default window", () => {
  assert.equal(HistoryView.mirrorUrl("w1:p1"), "/pane/w1%3Ap1/mirror");
  assert.equal(HistoryView.mirrorUrl(null), null);
});
