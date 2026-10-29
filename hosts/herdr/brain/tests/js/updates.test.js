/* node --test suite for the build-stamp update notice (S4, doc 09).
 *
 * The page polls GET /build and compares against the footer stamp;
 * a different served build means the SW is holding a stale shell and
 * the operator gets ONE toast per build with a Recargar action.
 * Multiple tabs arbitrate over a BroadcastChannel: the tab with the
 * smallest id keeps the toast, and the reload tap reloads every tab. */

const test = require("node:test");
const assert = require("node:assert");
const { createUpdater, newerBuild, keepToast } = require("../../src/herdr_brain/static/updates.js");

/* ---- pure decisions ---- */

test("newerBuild is true only for a different, real served build", () => {
  assert.equal(newerBuild("ab69618", "ab69618"), false);
  assert.equal(newerBuild("0df6929", "ab69618"), true);
  assert.equal(newerBuild("", "ab69618"), false);
  assert.equal(newerBuild(null, "ab69618"), false);
  assert.equal(newerBuild("x", ""), false);
  assert.equal(newerBuild(undefined, undefined), false);
});

test("keepToast: a smaller peer id claiming the same build wins", () => {
  assert.equal(keepToast("tab-b", ["tab-a"]), false);
  assert.equal(keepToast("tab-a", ["tab-b"]), true);
  assert.equal(keepToast("tab-a", []), true);
  assert.equal(keepToast("tab-a", ["tab-a"]), true);
});

/* ---- a fake BroadcastChannel bus shared by two updaters ---- */

function makeBus() {
  const members = [];
  return {
    post(message) { for (const m of members) if (m !== message._from) m.onMessage(message); },
    join(member) { members.push(member); },
  };
}

function makeUpdater(bus, id, opts) {
  const state = {
    shown: [], hidden: [], reloaded: 0, actions: [], builds: (opts && opts.builds) || ["new"],
  };
  let buildIndex = 0;
  const member = { onMessage() {} };
  bus.join(member);
  const updater = createUpdater({
    id: id,
    fetchBuild: () => Promise.resolve(state.builds[Math.min(buildIndex++, state.builds.length - 1)]),
    footerBuild: () => "old",
    show: (text, action) => { state.shown.push(text); state.actions.push(action); },
    hide: () => { state.hidden.push(true); },
    reload: () => { state.reloaded += 1; },
    post: (message) => { message._from = member; bus.post(message); },
    onMessage: (handler) => { member.onMessage = handler; },
    intervalMs: 0,
  });
  return { updater, state };
}

test("one tab: a newer build shows exactly one toast with a reload action", async () => {
  const bus = makeBus();
  const { updater, state } = makeUpdater(bus, "solo");
  await updater.check();
  assert.equal(state.shown.length, 1);
  assert.equal(typeof state.actions[0].onClick, "function");
  assert.equal(state.reloaded, 0);
  state.actions[0].onClick();
  assert.equal(state.reloaded, 1);
});

test("same build never toasts", async () => {
  const bus = makeBus();
  const { updater, state } = makeUpdater(bus, "solo", { builds: ["old"] });
  await updater.check();
  assert.equal(state.shown.length, 0);
});

test("a missing /build endpoint (null) degrades silently", async () => {
  const bus = makeBus();
  const { updater, state } = makeUpdater(bus, "solo", { builds: [null] });
  await updater.check();
  assert.equal(state.shown.length, 0);
});

test("two tabs: only the smallest id shows; the reload reloads both", async () => {
  const bus = makeBus();
  const a = makeUpdater(bus, "tab-a");
  const b = makeUpdater(bus, "tab-b");
  await a.updater.check();
  await b.updater.check();
  assert.equal(a.state.shown.length, 1, "smaller id keeps the toast");
  assert.equal(b.state.shown.length, 0, "larger id suppressed by the peer claim");
  a.state.actions[0].onClick();
  assert.equal(a.state.reloaded, 1);
  assert.equal(b.state.reloaded, 1);
});

test("two tabs detecting in reverse order: still exactly one live toast", async () => {
  const bus = makeBus();
  const a = makeUpdater(bus, "tab-a");
  const b = makeUpdater(bus, "tab-b");
  await b.updater.check();
  await a.updater.check();
  /* b detected first and showed; a's later claim carries a smaller id,
   * so b re-arbitrates and hides its toast while a shows its own. */
  assert.equal(a.state.shown.length, 1);
  assert.equal(b.state.shown.length, 1);
  assert.equal(b.state.hidden.length, 1, "b hid its toast when the smaller id claimed");
});

test("a second build change toasts again (one toast per build)", async () => {
  const bus = makeBus();
  const { updater, state } = makeUpdater(bus, "solo", { builds: ["new1", "new2"] });
  await updater.check();
  await updater.check();
  assert.equal(state.shown.length, 2);
});

test("peer reload message reloads without a local toast", async () => {
  const bus = makeBus();
  const a = makeUpdater(bus, "tab-a");
  const b = makeUpdater(bus, "tab-b");
  await a.updater.check();
  b.updater.reload();  /* b's UI never showed anything; the tap happened on a */
  assert.equal(a.state.reloaded, 1);
  assert.equal(b.state.reloaded, 1);
});

test("the 404 sentinel (\"absent\") stands the poller down for good", async () => {
  const bus = makeBus();
  const { updater, state } = makeUpdater(bus, "solo", { builds: ["absent", "new"] });
  await updater.check();
  assert.equal(state.shown.length, 0);
  assert.equal(updater.isDisabled(), true);
  await updater.check();   /* a later build change must NOT revive it */
  assert.equal(state.shown.length, 0);
});
