/* node --test suite for the model-switch modal poll (F7d fix, the
 * PWA half): after the "Cambiar modelo" tap sends the harness's picker
 * command, the PWA polls /view?pane_id=…&modal=1 until a structured
 * dialog appears — a USER-opened opencode picker leaves the pane idle,
 * so the plain status-gated /view never carries it.
 *
 * The machine is pure: timers and the fetch are injected, so the three
 * exits are driven here exactly — renders on the first structured
 * payload, stops (no further fetches), the none-toast after the ~10 s
 * budget, and a clean stop when the sheet closes. The DOM wiring is
 * pinned by shape in app.js (the card renders through the same
 * paintStructuredDialog path the blocked card uses). */

const test = require("node:test");
const assert = require("node:assert");
const path = require("node:path");

const Dialogs = require(
  path.join(__dirname, "../../src/herdr_brain/static/dialogs.js"));

/* A manual clock: queued timers fire only when time is advanced past
 * them, in schedule order. */
function fakeClock() {
  const queue = [];
  let now = 0;
  let nextId = 1;
  return {
    setTimeoutFn(fn, ms) {
      const entry = { at: now + ms, fn, id: nextId++ };
      queue.push(entry);
      return entry.id;
    },
    clearTimeoutFn(id) {
      const at = queue.findIndex((entry) => entry.id === id);
      if (at !== -1) queue.splice(at, 1);
    },
    advance(ms) {
      now += ms;
      const due = queue.filter((entry) => entry.at <= now);
      for (let i = queue.length - 1; i >= 0; i--) {
        if (queue[i].at <= now) queue.splice(i, 1);
      }
      due.sort((a, b) => a.at - b.at).forEach((entry) => entry.fn());
    },
    pending() { return queue.length; },
  };
}

/* Microtask flush: the machine's fetch rides promises, so a tick's
 * callbacks land one await after the timer fires. */
async function flush() {
  await Promise.resolve();
  await Promise.resolve();
}

const PICKER = {
  status: { pane_id: "w1:p3", agent: "opencode" },
  structured: { kind: "model_picker", agent: "opencode", fingerprint: "f1",
                options: [{ label: "GLM-5.3" }] },
};

function machine(overrides) {
  const clock = fakeClock();
  const log = { fetches: 0, structured: [], none: 0, panes: [] };
  const opts = Object.assign({
    fetchView(paneId) {
      log.fetches += 1;
      log.panes.push(paneId);
      return Promise.resolve({});
    },
    onStructured(payload, paneId) { log.structured.push([payload, paneId]); },
    onNone() { log.none += 1; },
    setTimeoutFn: clock.setTimeoutFn,
    clearTimeoutFn: clock.clearTimeoutFn,
  }, overrides || {});
  const poller = Dialogs.createModelSwitchPoller(opts);
  return { poller, clock, log };
}

test("modalViewUrl: pane_id escaped, modal=1 rides the query", () => {
  assert.equal(Dialogs.modalViewUrl("w1:p3"), "/view?pane_id=w1%3Ap3&modal=1");
  assert.equal(Dialogs.modalViewUrl("a b"), "/view?pane_id=a%20b&modal=1");
});

test("renders on the first structured payload, then stops", async () => {
  const rig = machine({
    fetchView(paneId) {
      rig.log.fetches += 1;
      rig.log.panes.push(paneId);
      return Promise.resolve(PICKER);
    },
  });
  rig.poller.start("w1:p3");
  assert.ok(rig.poller.active(), "polling after start");
  rig.clock.advance(700);
  await flush();
  assert.equal(rig.log.structured.length, 1, "onStructured fired once");
  assert.equal(rig.log.structured[0][1], "w1:p3");
  assert.equal(rig.log.structured[0][0].structured.kind, "model_picker");
  assert.ok(!rig.poller.active(), "stopped on success");
  /* Stopped means stopped: no timer survives and no further fetch
   * leaves, however much time passes. */
  assert.equal(rig.clock.pending(), 0);
  rig.clock.advance(60000);
  await flush();
  assert.equal(rig.log.fetches, 1);
  assert.equal(rig.log.none, 0);
});

test("keeps polling empty payloads, then fires the none-toast at ~10 s and stops", async () => {
  const rig = machine();
  rig.poller.start("w1:p3");
  let ticks = 0;
  while (rig.poller.active() && ticks < 40) {
    rig.clock.advance(700);
    await flush();
    ticks += 1;
  }
  assert.ok(!rig.poller.active(), "the budget ends the poll");
  assert.ok(ticks <= 15, "at most ceil(10000/700) fetches: " + ticks);
  assert.equal(rig.log.none, 1, "the none exit fired exactly once");
  assert.equal(rig.log.structured.length, 0);
  assert.equal(rig.clock.pending(), 0);
  rig.clock.advance(60000);
  await flush();
  assert.equal(rig.log.none, 1, "no second toast after the stop");
});

test("stops when the sheet closes: cancel kills timers and callbacks", async () => {
  const rig = machine();
  rig.poller.start("w1:p3");
  rig.clock.advance(700);
  await flush();
  const fetchesSoFar = rig.log.fetches;
  assert.ok(fetchesSoFar >= 1);
  rig.poller.cancel();
  assert.ok(!rig.poller.active());
  assert.equal(rig.clock.pending(), 0, "no timer survives the cancel");
  rig.clock.advance(60000);
  await flush();
  assert.equal(rig.log.fetches, fetchesSoFar, "no further fetch");
  assert.equal(rig.log.none, 0, "no toast on cancel");
  assert.equal(rig.log.structured.length, 0);
});

test("a failed fetch counts as no-dialog: the poll survives to the next tick", async () => {
  const responses = [Promise.reject(new Error("offline")), Promise.resolve(PICKER)];
  const rig = machine({
    fetchView(paneId) {
      rig.log.fetches += 1;
      rig.log.panes.push(paneId);
      return responses[rig.log.fetches - 1];
    },
  });
  rig.poller.start("w1:p3");
  rig.clock.advance(700);
  await flush();
  assert.equal(rig.log.structured.length, 0, "the error did not render");
  assert.ok(rig.poller.active(), "the poll survives a transport error");
  rig.clock.advance(700);
  await flush();
  assert.equal(rig.log.structured.length, 1, "the next tick's payload renders");
  assert.ok(!rig.poller.active());
});

test("restart on a new pane drops the old budget", async () => {
  const rig = machine();
  rig.poller.start("w1:p3");
  rig.clock.advance(700);
  await flush();
  const firstFetches = rig.log.fetches;
  rig.poller.start("w2:p1");
  assert.ok(rig.poller.active());
  assert.equal(rig.clock.pending(), 1, "exactly one rescheduled timer");
  rig.clock.advance(700);
  await flush();
  assert.ok(rig.log.fetches > firstFetches);
  assert.equal(rig.log.panes[rig.log.panes.length - 1], "w2:p1");
});
