const test = require("node:test");
const assert = require("node:assert/strict");
const { createStatePoller } = require("../../src/herdr_brain/static/history.js");

/* The poller arms ONE recurring interval, like the reference engine's
 * applyCadence: a re-arm clears and re-creates it; a same-cadence tick
 * lets the armed interval keep its phase. */
function rig() {
  let now = 0;
  let id = 0;
  const timers = new Map();  // handle -> {fn, period, at}
  const reads = [];
  const poller = createStatePoller({
    now: () => now,
    setIntervalFn(fn, ms) { timers.set(++id, {fn, period: ms, at: now + ms}); return id; },
    clearIntervalFn: (handle) => timers.delete(handle),
    refresh: () => reads.push(now),
  });
  function advance(ms) {
    const end = now + ms;
    for (;;) {
      let due = null;
      for (const [handle, t] of timers) {
        if (t.at > end) continue;
        if (!due || t.at < due.t.at) due = {handle, t};
      }
      if (!due) break;
      now = due.t.at;
      const created = id;
      timers.delete(due.handle);
      due.t.fn();
      // A tick that did not re-arm (same cadence after the read) keeps
      // the recurring interval: it fires again one period later.
      if (id === created) timers.set(due.handle, {fn: due.t.fn, period: due.t.period, at: now + due.t.period});
    }
    now = end;
  }
  return { poller, reads, advance };
}

test("foreground normal, watched for 10 seconds, then background idle", () => {
  const { poller, advance, reads } = rig();
  poller.start();
  poller.setStreamHealth(true);
  advance(5000);
  assert.deepEqual(reads, [5000]);
  poller.noteAttention();
  advance(10000);
  assert.deepEqual(reads, [5000, 6500, 8000, 9500, 11000, 12500, 14000]);
  advance(500);
  assert.equal(reads.at(-1), 14000, "attention expires without a hot read; the tick re-arms");
  advance(5000);
  assert.equal(reads.at(-1), 20500, "normal cadence after attention expires");
  poller.setHidden(true);
  advance(9999);
  assert.equal(reads.at(-1), 20500);
  advance(1);
  assert.equal(reads.at(-1), 30500);
});

test("SSE loss tightens immediately; recovery reconciles once before relaxing", () => {
  const { poller, advance, reads } = rig();
  poller.setHidden(true);
  poller.start();
  poller.setStreamHealth(true);
  advance(1000);
  poller.setStreamHealth(false);
  advance(2000);
  poller.setStreamHealth(false); // repeated errors must not defer fallback
  advance(3000);
  assert.deepEqual(reads, [6000]);
  poller.setStreamHealth(true);
  poller.setStreamHealth(true);
  assert.deepEqual(reads, [6000, 6000]);
  advance(9999);
  assert.deepEqual(reads, [6000, 6000]);
  advance(1);
  assert.deepEqual(reads, [6000, 6000, 16000]);
});

test("the boot interval is a plain 5-second setInterval while unhealthy", () => {
  /* The PWA's poll rides setInterval — some harnesses stub
   * setInterval(ms === 5000) to drive a deterministic poll beat, so the
   * very first arm must be an ordinary 5 s interval. */
  const armed = [];
  const poller = createStatePoller({
    now: () => 0,
    setIntervalFn: (fn, ms) => armed.push(ms) || 7,
    clearIntervalFn: () => {},
    refresh: () => {},
  });
  poller.start();
  assert.deepEqual(armed, [5000]);
});
