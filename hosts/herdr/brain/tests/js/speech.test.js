/* Speech request identity + cancel (voice-stack VS1.7) and the segmented
 * delivery client / segment player (voice-stack VS2.6, T6).
 *
 * speech.js is a pure UMD module (fetch/timers/crypto injected), so its
 * behavior is EXECUTED here. app.js is a browser-only IIFE that node --test
 * cannot load; the wiring tests below pin its SHAPE (same limitation and
 * same discipline as announce-wiring.test.js) and the real-browser
 * behavior is covered by the E2E harness (tests/e2e). */

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const Speech = require("../../src/herdr_brain/static/speech.js");

const STATIC = path.join(__dirname, "../../src/herdr_brain/static");
const appSrc = fs.readFileSync(path.join(STATIC, "app.js"), "utf8");
const indexSrc = fs.readFileSync(path.join(STATIC, "index.html"), "utf8");

/* ---- doubles ---------------------------------------------------------- */

function fakeCrypto(seedBytes) {
  let n = 0;
  return {
    randomUUID: () => "00000000-0000-4000-8000-00000000000" + (n++ % 10),
    getRandomValues: (bytes) => {
      for (let i = 0; i < bytes.length; i++) bytes[i] = seedBytes ? seedBytes[i % seedBytes.length] : i;
      return bytes;
    },
  };
}

function response(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => (body === undefined ? Promise.reject(new Error("no json")) : Promise.resolve(body)),
  };
}

/* fetch double scripted with a queue of outcomes: a response or an Error. */
function scriptedFetch(outcomes) {
  const calls = [];
  const fn = (url, opts) => {
    calls.push({ url, opts });
    const next = outcomes.shift();
    return next instanceof Error ? Promise.reject(next) : Promise.resolve(next);
  };
  fn.calls = calls;
  return fn;
}

/* setTimeout double: records delays and fires callbacks immediately. */
function instantTimers() {
  const delays = [];
  const fn = (cb, ms) => { delays.push(ms); cb(); };
  fn.delays = delays;
  return fn;
}

function controller(outcomes, extra) {
  const statuses = [];
  const fetch = scriptedFetch(outcomes);
  const timers = instantTimers();
  const ctl = Speech.createSpeechController(Object.assign({
    fetch, setTimeout: timers, crypto: fakeCrypto(),
    onStatus: (s) => statuses.push(s),
  }, extra || {}));
  return { ctl, fetch, timers, statuses };
}

/* fetch double for the segment player: every call is parked until the
 * test resolves/rejects it explicitly — long-poll stepping must be
 * deterministic, so responses never auto-arrive. */
function manualFetch() {
  const calls = [];
  const fn = (url, opts) => {
    const rec = { url, opts, resolve: null, reject: null };
    rec.promise = new Promise((res, rej) => { rec.resolve = res; rec.reject = rej; });
    calls.push(rec);
    return rec.promise;
  };
  fn.calls = calls;
  return fn;
}

/* Timer double for the segment player. Every delay is recorded. The 15s
 * fetch-race timers PARK (fired only via flush()) so scripted fetches
 * settle first; flush(ms) fires only the parked callbacks scheduled with
 * that delay (the 1000ms retry backoffs), and flush() fires everything.
 * mode "instant" fires EVERY callback immediately — including the race —
 * which makes an unanswered fetch count as timed out.
 *
 * The flush-spacing delay (SEGMENT_FLUSH_POLL_MS) fires immediately in
 * this double: the behavior tests below assert fetch order/dedup/terminal
 * semantics, which pacing must not observably change — the spacing BOUND
 * itself is proven by test_flush_poll_pacing_bounds_requests with its own
 * deferred-timer double. */
function segTimers(mode) {
  const delays = [];
  const parked = [];
  const fn = (cb, ms) => {
    delays.push(ms);
    if (mode === "instant" || ms === Speech.SEGMENT_FLUSH_POLL_MS) { cb(); return; }
    parked.push({ cb, ms });
  };
  fn.delays = delays;
  fn.flush = (ms) => {
    const fire = [];
    for (let i = parked.length - 1; i >= 0; i--) {
      if (ms === undefined || parked[i].ms === ms) fire.push(parked.splice(i, 1)[0]);
    }
    for (let i = fire.length - 1; i >= 0; i--) fire[i].cb();
  };
  return fn;
}

/* Deferred timer double for the pacing test: records every delay and
 * fires NOTHING on its own — the test advances the flush spacing on
 * demand (fire(ms)), so the next() request count over a scripted time
 * window IS the pacing evidence. pending(ms) counts what is scheduled. */
function deferredTimers() {
  const delays = [];
  const scheduled = [];
  const fn = (cb, ms) => {
    delays.push(ms);
    scheduled.push({ cb, ms });
  };
  fn.delays = delays;
  fn.fire = (ms) => {
    const fire = [];
    for (let i = scheduled.length - 1; i >= 0; i--) {
      if (ms === undefined || scheduled[i].ms === ms) fire.push(scheduled.splice(i, 1)[0]);
    }
    for (let i = fire.length - 1; i >= 0; i--) fire[i].cb();
  };
  fn.pending = (ms) => scheduled.filter((t) => ms === undefined || t.ms === ms).length;
  return fn;
}

/* Macrotask drain: all queued promise continuations run before resuming. */
async function drain(rounds) {
  for (let i = 0; i < (rounds || 4); i++) await new Promise((r) => setImmediate(r));
}

function segmentHarness(timerMode, withoutOnStatus) {
  const enqueued = [];
  const statuses = [];
  const fetch = manualFetch();
  const timers = segTimers(timerMode);
  const deps = {
    fetch, setTimeout: timers, crypto: fakeCrypto(),
    enqueue: (item) => enqueued.push(item),
  };
  if (!withoutOnStatus) deps.onStatus = (s) => statuses.push(s);
  const player = Speech.createSegmentPlayer(deps);
  return { player, fetch, timers, enqueued, statuses };
}

/* ---- identity ----------------------------------------------------------- */

test("mintSpeechRequest: valid id + 256-bit hex token, zero-padded bytes", () => {
  const minted = Speech.mintSpeechRequest(fakeCrypto([0, 15, 16, 255]));
  assert.ok(Speech.validId(minted.id));
  assert.match(minted.token, /^[0-9a-f]{64}$/);
  assert.strictEqual(minted.token.slice(0, 8), "000f10ff");  // 0 and 15 padded to 2 digits
});

test("validId: charset and 8-64 length", () => {
  for (const ok of ["a".repeat(8), "a".repeat(64), "A.b_c-d.1234", "ann-0123456789"]) {
    assert.ok(Speech.validId(ok), ok);
  }
  for (const bad of ["short", "a".repeat(65), "has space 12345", "slash/not-ok12", "", null, 12345678]) {
    assert.ok(!Speech.validId(bad), String(bad));
  }
});

test("begin returns the /ask body fields and tracks the active job; release is id-scoped", () => {
  const { ctl } = controller([]);
  assert.strictEqual(ctl.activeId(), null);
  const fields = ctl.begin("session-1");
  assert.ok(Speech.validId(fields.speech_request_id));
  assert.match(fields.speech_cancel_token, /^[0-9a-f]{64}$/);
  assert.strictEqual(ctl.activeId(), fields.speech_request_id);
  ctl.release("some-other-id");               // a foreign id never clears the job
  assert.strictEqual(ctl.activeId(), fields.speech_request_id);
  ctl.release(fields.speech_request_id);
  assert.strictEqual(ctl.activeId(), null);
});

/* ---- cancel ------------------------------------------------------------- */

test("test_stopaudio_posts_cancel", async () => {
  const { ctl, fetch, statuses } = controller([response(200, { status: "cancelled" })]);
  const fields = ctl.begin("session-1");

  const result = await ctl.cancel();

  assert.strictEqual(fetch.calls.length, 1);
  const { url, opts } = fetch.calls[0];
  assert.strictEqual(url, "/speech/" + fields.speech_request_id + "/cancel");
  assert.strictEqual(opts.method, "POST");
  assert.deepStrictEqual(JSON.parse(opts.body), {
    session_id: "session-1",
    speech_cancel_token: fields.speech_cancel_token,
  });
  assert.ok(!url.includes(fields.speech_cancel_token), "token never rides the URL");
  assert.deepStrictEqual(result, { state: "cancelled", id: fields.speech_request_id });
  assert.deepStrictEqual(statuses.map((s) => s.state), ["cancelling", "cancelled"]);
  assert.strictEqual(ctl.activeId(), null);
});

test("server verdicts pass through: already-complete and unknown-or-expired", async () => {
  for (const verdict of ["already-complete", "unknown-or-expired"]) {
    const { ctl } = controller([response(200, { status: verdict })]);
    ctl.begin("s");
    assert.strictEqual((await ctl.cancel()).state, verdict);
  }
});

test("a 200 without a readable status still reports cancelled", async () => {
  for (const reply of [response(200), response(200, {})]) {
    const { ctl } = controller([reply]);
    ctl.begin("s");
    assert.strictEqual((await ctl.cancel()).state, "cancelled");
  }
});

test("cancel without an active job does nothing and never posts", async () => {
  const { ctl, fetch } = controller([]);
  assert.deepStrictEqual(await ctl.cancel(), { state: "no-active-job" });
  assert.strictEqual(fetch.calls.length, 0);
});

test("a second cancel while the first retries never double-posts", async () => {
  const { ctl, fetch } = controller([response(200, { status: "cancelled" })]);
  ctl.begin("s");
  const first = ctl.cancel();
  const second = await ctl.cancel();
  await first;
  assert.strictEqual(second.state, "no-active-job");
  assert.strictEqual(fetch.calls.length, 1);
});

test("test_cancel_net_retry_then_visible_unconfirmed", async () => {
  const boom = () => new Error("network down");
  const { ctl, fetch, timers, statuses } = controller([boom(), boom(), boom(), boom()]);
  const fields = ctl.begin("session-1");

  const result = await ctl.cancel();

  assert.strictEqual(fetch.calls.length, 1 + Speech.CANCEL_NET_RETRY);  // 1 try + 2 retries, then stop
  assert.deepStrictEqual(timers.delays, [Speech.CANCEL_BACKOFF_MS, Speech.CANCEL_BACKOFF_MS]);
  assert.deepStrictEqual(result, { state: "cancel-unconfirmed", id: fields.speech_request_id });
  assert.strictEqual(statuses[statuses.length - 1].state, "cancel-unconfirmed");
  assert.ok(!statuses.some((s) => s.state === "cancelled"), "never a false 'server cancelled'");
});

test("transient 5xx retries and then succeeds", async () => {
  const { ctl, fetch } = controller([response(503), response(500), response(200, { status: "cancelled" })]);
  ctl.begin("s");
  assert.strictEqual((await ctl.cancel()).state, "cancelled");
  assert.strictEqual(fetch.calls.length, 3);
});

test("403 and 422 are final and visible: no retry", async () => {
  for (const [status, state] of [[403, "cancel-forbidden"], [422, "cancel-rejected"]]) {
    const { ctl, fetch, timers } = controller([response(status)]);
    ctl.begin("s");
    assert.strictEqual((await ctl.cancel()).state, state);
    assert.strictEqual(fetch.calls.length, 1);
    assert.deepStrictEqual(timers.delays, []);
  }
});

test("the token never appears in any status object", async () => {
  const { ctl, statuses } = controller([new Error("x"), new Error("x"), new Error("x")]);
  const fields = ctl.begin("s");
  await ctl.cancel();
  assert.ok(statuses.length >= 2);
  assert.ok(!JSON.stringify(statuses).includes(fields.speech_cancel_token));
});

test("onStatus is optional", async () => {
  const fetch = scriptedFetch([response(200, { status: "cancelled" })]);
  const ctl = Speech.createSpeechController({ fetch, setTimeout: instantTimers(), crypto: fakeCrypto() });
  ctl.begin("s");
  assert.strictEqual((await ctl.cancel()).state, "cancelled");
});

test("the id is URL-encoded in the cancel path", async () => {
  const { ctl, fetch } = controller([response(200, { status: "cancelled" })], {
    crypto: { randomUUID: () => "id/with spaces?", getRandomValues: (b) => b },
  });
  ctl.begin("s");
  await ctl.cancel();
  assert.strictEqual(fetch.calls[0].url, "/speech/id%2Fwith%20spaces%3F/cancel");
});

/* ---- purge -------------------------------------------------------------- */

test("test_purge_scoped_to_id", () => {
  const queue = [
    { url: "a1", speechRequestId: "job-A000001" },
    { url: "ann", announcement: { label: "x" }, speechRequestId: null },
    { url: "b1", speechRequestId: "job-B000001" },
    { url: "a2", speechRequestId: "job-A000001" },
    { url: "legacy" },
  ];

  const removed = Speech.purgeQueueById(queue, "job-A000001");

  assert.strictEqual(removed, 2);
  assert.deepStrictEqual(queue.map((i) => i.url), ["ann", "b1", "legacy"]);  // order kept, foreign audio survives
  assert.strictEqual(Speech.purgeQueueById(queue, "job-A000001"), 0);          // idempotent
  assert.strictEqual(Speech.purgeQueueById([], "job-A000001"), 0);
});

/* ---- segment player (VS2.6, T6) — behavior EXECUTED --------------------- */

test("test_seq_order_and_once", async () => {
  const h = segmentHarness();
  h.player.start({ id: "job-00000001", sessionId: "session-1" });
  await drain();

  assert.strictEqual(h.fetch.calls.length, 1);
  assert.strictEqual(h.fetch.calls[0].url,
    "/speech/job-00000001/next?after=-1&ack=-1&session_id=session-1");
  assert.deepStrictEqual(h.fetch.calls[0].opts, { method: "GET" });
  h.fetch.calls[0].resolve(response(200, { seq: 0, audio_url: "/s0.mp3", is_final: false }));
  await drain();

  // one unplayed segment (prefetch=1): keep polling
  assert.strictEqual(h.fetch.calls.length, 2);
  assert.ok(h.fetch.calls[1].url.includes("after=0&ack=-1"));
  h.fetch.calls[1].resolve(response(200, { seq: 1, audio_url: "/s1.mp3", is_final: false }));
  await drain();

  // two unplayed (seq0 playing + seq1 buffered): the prefetch gate closes
  assert.strictEqual(h.fetch.calls.length, 2);
  assert.deepStrictEqual(h.enqueued, [
    { speechRequestId: "job-00000001", seq: 0, url: "/s0.mp3", announcement: null },
    { speechRequestId: "job-00000001", seq: 1, url: "/s1.mp3", announcement: null },
  ]);
  assert.strictEqual(h.player.state().after, 1);

  // seq0 finishes: ack=0 reopens the gate
  h.player.onEnded(0);
  assert.strictEqual(h.fetch.calls.length, 3);
  assert.ok(h.fetch.calls[2].url.includes("after=1&ack=0"));

  // duplicate HTTP delivery of seq1 (re-poll race): discarded, enqueued once
  h.fetch.calls[2].resolve(response(200, { seq: 1, audio_url: "/s1.mp3", is_final: false }));
  await drain();
  assert.strictEqual(h.enqueued.length, 2, "the duplicate is NOT enqueued again");
  assert.strictEqual(h.player.state().after, 1, "after does not regress");

  // the re-poll delivers the final segment
  assert.strictEqual(h.fetch.calls.length, 4);
  assert.ok(h.fetch.calls[3].url.includes("after=1&ack=0"));
  h.fetch.calls[3].resolve(response(200, { seq: 2, audio_url: "/s2.mp3", is_final: true }));
  await drain();
  assert.strictEqual(h.player.state().finalSeq, 2);
  assert.strictEqual(h.fetch.calls.length, 4);  // gate closed: 2 unplayed

  // seq1 finishes: intermediate acks ride every poll while flushing
  h.player.onEnded(1);
  assert.strictEqual(h.fetch.calls.length, 5);
  assert.ok(h.fetch.calls[4].url.includes("after=2&ack=1"));
  h.fetch.calls[4].resolve(response(200, { wait: true }));
  await drain();

  // final received, ack<final: keep flushing (one poll in flight)
  assert.strictEqual(h.fetch.calls.length, 6);
  assert.ok(h.fetch.calls[5].url.includes("after=2&ack=1"));
  h.player.onEnded(2);  // in flight: no extra poll
  assert.strictEqual(h.fetch.calls.length, 6);
  h.fetch.calls[5].resolve(response(200, { wait: true }));
  await drain();

  // the FINAL flush: exactly one request carrying ack === finalSeq
  assert.strictEqual(h.fetch.calls.length, 7);
  assert.strictEqual(h.fetch.calls[6].url,
    "/speech/job-00000001/next?after=2&ack=2&session_id=session-1");
  h.fetch.calls[6].resolve(response(200, { wait: true }));
  await drain();

  assert.deepStrictEqual(h.statuses, [{ state: "complete", id: "job-00000001" }]);
  const st = h.player.state();
  assert.strictEqual(st.after, 2);
  assert.strictEqual(st.ack, 2);
  assert.strictEqual(st.finalSeq, 2);
  assert.ok(st.finalAckDelivered, "the last ack was delivered in one request");
  assert.ok(st.stopped);
  assert.ok(!st.degraded);

  // polling stopped after the final ack flush; a late duplicate ended no-ops
  h.player.onEnded(2);
  await drain();
  h.timers.flush();
  assert.strictEqual(h.fetch.calls.length, 7);
  assert.strictEqual(h.player.state().ack, 2);
});

test("test_fetch_retry_then_degraded", async () => {
  const h = segmentHarness();
  h.player.start({ id: "job-00000002", sessionId: "session-2" });
  await drain();
  assert.strictEqual(h.fetch.calls.length, 1);

  h.fetch.calls[0].reject(new Error("network down"));
  await drain();
  h.timers.flush(Speech.SEGMENT_RETRY_BACKOFF_MS);
  await drain();
  assert.strictEqual(h.fetch.calls.length, 2);
  assert.ok(h.fetch.calls[1].url.includes("after=-1&ack=-1"), "same watermarks retried");

  h.fetch.calls[1].reject(new Error("network down"));
  await drain();
  h.timers.flush(Speech.SEGMENT_RETRY_BACKOFF_MS);
  await drain();
  assert.strictEqual(h.fetch.calls.length, 3);

  h.fetch.calls[2].reject(new Error("network down"));
  await drain();

  assert.strictEqual(h.fetch.calls.length, 1 + Speech.SPEECH_SEGMENT_RETRY);
  assert.deepStrictEqual(h.timers.delays,
    [15000, 1000, 15000, 1000, 15000]);  // race timer per attempt + fixed backoffs
  assert.deepStrictEqual(h.statuses,
    [{ state: "degraded", id: "job-00000002", reason: "segment-fetch-failed" }]);
  const st = h.player.state();
  assert.ok(st.stopped);
  assert.ok(st.degraded);
  assert.strictEqual(h.enqueued.length, 0, "nothing was skipped or enqueued");

  h.timers.flush();  // no stray callback restarts the loop
  await drain();
  assert.strictEqual(h.fetch.calls.length, 3);
});

test("test_delayed_ended_no_double_free", async () => {
  const h = segmentHarness();
  h.player.start({ id: "job-00000003", sessionId: "session-3" });
  await drain();
  h.fetch.calls[0].resolve(response(200, { seq: 0, audio_url: "/a0.mp3", is_final: false }));
  await drain();
  h.fetch.calls[1].resolve(response(200, { seq: 1, audio_url: "/a1.mp3", is_final: true }));
  await drain();
  assert.strictEqual(h.player.state().ack, -1);

  h.player.onEnded(0);   // first ended for seq 0
  assert.strictEqual(h.player.state().ack, 0);
  h.player.onEnded(0);   // DUPLICATE late ended for seq 0
  h.player.onEnded(0);
  assert.strictEqual(h.player.state().ack, 0, "the watermark never double-advances");
  assert.strictEqual(h.player.state().after, 1);

  h.player.onEnded(1);   // contiguous: advances exactly once
  assert.strictEqual(h.player.state().ack, 1);

  // the flushed request carries the monotone ack, then completes the job
  h.fetch.calls[2].resolve(response(200, { wait: true }));
  await drain();
  const flushCall = h.fetch.calls[h.fetch.calls.length - 1];
  assert.ok(flushCall.url.includes("after=1&ack=1"), "the final ack flush is monotone");
  flushCall.resolve(response(200, { wait: true }));
  await drain();
  assert.deepStrictEqual(h.statuses, [{ state: "complete", id: "job-00000003" }]);
  assert.strictEqual(h.player.state().ack, 1);
});

test("test_duplicated_http_delivery_deduped", async () => {
  const h = segmentHarness();
  h.player.start({ id: "job-00000004", sessionId: "session-4" });
  await drain();
  h.fetch.calls[0].resolve(response(200, { seq: 0, audio_url: "/d0.mp3", is_final: false }));
  await drain();
  h.fetch.calls[1].resolve(response(200, { seq: 1, audio_url: "/d1.mp3", is_final: true }));
  await drain();

  h.player.onEnded(0);   // opens the gate for the next poll
  assert.strictEqual(h.fetch.calls.length, 3);
  assert.ok(h.fetch.calls[2].url.includes("after=1&ack=0"));

  // the server (scripted) delivers seq 1 AGAIN across two polls
  h.fetch.calls[2].resolve(response(200, { seq: 1, audio_url: "/d1.mp3", is_final: true }));
  await drain();
  assert.strictEqual(h.enqueued.filter((i) => i.seq === 1).length, 1, "seq 1 enqueued once");
  assert.strictEqual(h.player.state().after, 1, "the after watermark stays intact");

  // the discard re-polls with unchanged watermarks; the flush loop keeps
  // carrying ack=0 until seq1 finishes playing
  assert.strictEqual(h.fetch.calls.length, 4);
  assert.ok(h.fetch.calls[3].url.includes("after=1&ack=0"));
  h.fetch.calls[3].resolve(response(200, { wait: true }));
  await drain();
  h.player.onEnded(1);  // ack=1 while the next flush poll is in flight
  h.fetch.calls[4].resolve(response(200, { wait: true }));
  await drain();
  assert.strictEqual(h.fetch.calls[5].url,
    "/speech/job-00000004/next?after=1&ack=1&session_id=session-4");
  h.fetch.calls[5].resolve(response(200, { wait: true }));
  await drain();
  assert.deepStrictEqual(h.statuses, [{ state: "complete", id: "job-00000004" }]);
  assert.strictEqual(h.enqueued.length, 2);
});

test("test_flush_poll_pacing_bounds_requests", async () => {
  /* VS2 remediation (hot re-poll): once is_final is received, only ack
   * flushing remains and terminal server replies are immediate — an
   * unpaced player re-polled /next at ~2ms (213 requests for a 3-segment
   * stream). With SEGMENT_FLUSH_POLL_MS spacing, a ~1s window with the
   * last 'ended' withheld issues at most 4 next() requests, and the
   * final ack still lands exactly once when playback catches up. */
  const FLUSH = Speech.SEGMENT_FLUSH_POLL_MS;
  assert.strictEqual(FLUSH, 250);
  const timers = deferredTimers();
  const enqueued = [];
  const statuses = [];
  const fetch = manualFetch();
  const player = Speech.createSegmentPlayer({
    fetch, setTimeout: timers, crypto: fakeCrypto(),
    enqueue: (item) => enqueued.push(item),
    onStatus: (s) => statuses.push(s),
  });

  player.start({ id: "job-00000020", sessionId: "session-20" });
  await drain();
  fetch.calls[0].resolve(response(200, { seq: 0, audio_url: "/f0.mp3", is_final: false }));
  await drain();
  fetch.calls[1].resolve(response(200, { seq: 1, audio_url: "/f1.mp3", is_final: false }));
  await drain();                     // two unplayed: the prefetch gate closes
  player.onEnded(0);                 // ack=0 reopens it: the poll is immediate
  assert.strictEqual(fetch.calls.length, 3,
    "waiting for segments stays unpaced (the server hold paces that path)");
  fetch.calls[2].resolve(response(200, { wait: true }));   // hold: nothing new yet
  await drain();
  assert.strictEqual(fetch.calls.length, 4);               // one unplayed: keep polling
  player.onEnded(1);                 // ack=1 while the next poll is in flight
  // is_final lands in the exact hot-loop state (after=2, ack=1): an
  // unpaced player re-polled here at ~2ms; the paced one waits out the
  // flush spacing instead.
  fetch.calls[3].resolve(response(200, { seq: 2, audio_url: "/f2.mp3", is_final: true }));
  await drain();
  assert.strictEqual(fetch.calls.length, 4,
    "is_final known: the next poll waits out the flush spacing, not loopback speed");

  // ~1s scripted window (t=250/500/750/1000), the last 'ended' withheld.
  for (let tick = 0; tick < 4; tick++) {
    timers.fire(FLUSH);
    const call = fetch.calls[fetch.calls.length - 1];
    assert.ok(call.url.includes("after=2&ack=1"),
      "flush polls keep both watermarks until playback catches up");
    call.resolve(response(200, { wait: true }));
    await drain();
  }
  assert.strictEqual(fetch.calls.length, 4 + 4,
    "~1s of ack flushing issued at most 4 next() requests (was: a ~2ms loop)");
  assert.strictEqual(timers.pending(FLUSH), 1,
    "exactly one spacing timer is ever pending at a time");

  // The delayed last ended arrives: the final-ack flush is ONE-SHOT and
  // immediate — it rides the ended, never the spacing timer (the player
  // stops on its reply, so there is no loop to pace).
  player.onEnded(2);
  assert.strictEqual(fetch.calls.length, 9,
    "the final-ack poll goes out with the ended, not behind the timer");
  assert.strictEqual(fetch.calls[8].url,
    "/speech/job-00000020/next?after=2&ack=2&session_id=session-20");
  fetch.calls[8].resolve(response(200, { wait: true }));
  await drain();

  // …and the job completes EXACTLY once; nothing fires afterwards.
  assert.deepStrictEqual(statuses, [{ state: "complete", id: "job-00000020" }]);
  timers.fire(FLUSH);
  timers.fire();  // leftover race timers hit settled promises: no-ops
  await drain();
  assert.strictEqual(fetch.calls.length, 9);
  assert.ok(player.state().finalAckDelivered, "the final ack landed");
  assert.strictEqual(enqueued.length, 3);
});

test("terminal job statuses stop polling visibly; unknown statuses do not", async () => {
  for (const status of ["cancelled", "degraded", "failed", "expired-unconsumed"]) {
    const h = segmentHarness();
    h.player.start({ id: "job-00000005", sessionId: "session-5" });
    await drain();
    h.fetch.calls[0].resolve(response(200, { wait: true, status }));
    await drain();
    const st = h.player.state();
    assert.ok(st.stopped, status);
    assert.strictEqual(st.terminalStatus, status);
    assert.deepStrictEqual(h.statuses, [{ state: "server-" + status, id: "job-00000005" }]);
    assert.strictEqual(h.fetch.calls.length, 1, "nothing further is polled or enqueued");
  }
  const h = segmentHarness();
  h.player.start({ id: "job-00000005", sessionId: "session-5" });
  await drain();
  h.fetch.calls[0].resolve(response(200, { wait: true, status: "rendering" }));
  await drain();
  assert.ok(!h.player.state().stopped, "an unknown (non-terminal) phase keeps the long-poll alive");
  assert.strictEqual(h.fetch.calls.length, 2);
});

test("a local stop halts the loop and never purges the queue", async () => {
  const h = segmentHarness();
  h.player.start({ id: "job-00000006", sessionId: "session-6" });
  await drain();
  h.player.stop("user-stop");
  h.fetch.calls[0].resolve(response(200, { seq: 0, audio_url: "/x0.mp3", is_final: false }));
  await drain();
  const st = h.player.state();
  assert.ok(st.stopped);
  assert.strictEqual(st.stopReason, "user-stop");
  assert.strictEqual(st.inFlight, false);
  assert.strictEqual(h.enqueued.length, 0, "a response landing after stop enqueues nothing");
  assert.strictEqual(h.fetch.calls.length, 1);
  h.player.stop();  // idempotent
  assert.strictEqual(h.player.state().stopReason, "user-stop");

  const h2 = segmentHarness();
  h2.player.start({ id: "job-00000006", sessionId: "session-6" });
  h2.player.stop();
  assert.strictEqual(h2.player.state().stopReason, "local-stop");
});

test("a fetch that only times out counts as a failure and degrades", async () => {
  const h = segmentHarness("instant");  // the 15s race fires before any fetch settles
  h.player.start({ id: "job-00000007", sessionId: "session-7" });
  await drain();
  assert.strictEqual(h.fetch.calls.length, 1 + Speech.SPEECH_SEGMENT_RETRY);
  assert.deepStrictEqual(h.statuses,
    [{ state: "degraded", id: "job-00000007", reason: "segment-fetch-failed" }]);
  assert.ok(h.player.state().degraded);

  // late fetch settlements after the race are no-ops, never double-handled
  for (const call of h.fetch.calls) call.resolve(response(200, { seq: 0, audio_url: "/t.mp3" }));
  await drain();
  assert.strictEqual(h.enqueued.length, 0);
  assert.strictEqual(h.fetch.calls.length, 3);
});

test("garbage replies never skip silently: malformed keeps polling, failures degrade", async () => {
  // a null body: not terminal, not a segment — keep the long-poll alive
  const a = segmentHarness();
  a.player.start({ id: "job-00000008", sessionId: "session-8" });
  await drain();
  a.fetch.calls[0].resolve(response(200, null));
  await drain();
  assert.strictEqual(a.fetch.calls.length, 2);
  assert.strictEqual(a.enqueued.length, 0);

  // without onStatus the degrade is still terminal, just unreported
  const b = segmentHarness(null, true);
  b.player.start({ id: "job-00000008", sessionId: "session-8" });
  await drain();
  for (let i = 0; i < 3; i++) {
    b.fetch.calls[i].resolve(response(500));
    await drain();
    b.timers.flush(Speech.SEGMENT_RETRY_BACKOFF_MS);
    await drain();
  }
  assert.ok(b.player.state().degraded, "a non-ok reply retries then degrades visibly-late");

  // a fetch resolving undefined (broken transport) degrades, never throws
  const c = segmentHarness();
  c.player.start({ id: "job-00000008", sessionId: "session-8" });
  await drain();
  for (let i = 0; i < 3; i++) {
    c.fetch.calls[i].resolve(undefined);
    await drain();
    c.timers.flush(Speech.SEGMENT_RETRY_BACKOFF_MS);
    await drain();
  }
  assert.ok(c.player.state().degraded);

  // a 200 with an unreadable body degrades after the retries
  const d = segmentHarness();
  d.player.start({ id: "job-00000008", sessionId: "session-8" });
  await drain();
  for (let i = 0; i < 3; i++) {
    d.fetch.calls[i].resolve(response(200));
    await drain();
    d.timers.flush(Speech.SEGMENT_RETRY_BACKOFF_MS);
    await drain();
  }
  assert.ok(d.player.state().degraded);
});

test("start() supersedes an in-flight job: its late responses are dropped", async () => {
  // resolve mismatch: a late segment of job A must not enqueue under job B
  const h1 = segmentHarness();
  h1.player.start({ id: "jobA-000001", sessionId: "session-9" });
  await drain();
  h1.player.start({ id: "jobB-000002", sessionId: "session-9" });
  await drain();
  assert.strictEqual(h1.fetch.calls.length, 2);
  h1.fetch.calls[0].resolve(response(200, { seq: 0, audio_url: "/a0.mp3", is_final: false }));
  await drain();
  assert.deepStrictEqual(h1.enqueued, [], "the superseded job's segment is dropped");
  assert.strictEqual(h1.player.state().after, -1);
  h1.fetch.calls[1].resolve(response(200, { seq: 0, audio_url: "/b0.mp3", is_final: false }));
  await drain();
  assert.deepStrictEqual(h1.enqueued,
    [{ speechRequestId: "jobB-000002", seq: 0, url: "/b0.mp3", announcement: null }]);
  // a stale ended beyond the new job's received range never touches ack
  h1.player.onEnded(5);
  assert.strictEqual(h1.player.state().ack, -1);

  // reject mismatch: a late failure of job A must not kill job B
  const h2 = segmentHarness();
  h2.player.start({ id: "jobA-000001", sessionId: "session-9" });
  await drain();
  h2.player.start({ id: "jobB-000002", sessionId: "session-9" });
  await drain();
  h2.fetch.calls[0].reject(new Error("old job network down"));
  await drain();
  assert.ok(!h2.player.state().degraded);
  assert.strictEqual(h2.fetch.calls.length, 2);

  // backoff mismatch: job A's parked retry never fires once job B owns the player
  const h3 = segmentHarness();
  h3.player.start({ id: "jobA-000001", sessionId: "session-9" });
  await drain();
  h3.fetch.calls[0].reject(new Error("old job network down"));
  await drain();
  h3.player.start({ id: "jobB-000002", sessionId: "session-9" });
  await drain();
  h3.timers.flush(Speech.SEGMENT_RETRY_BACKOFF_MS);
  await drain();
  assert.strictEqual(h3.fetch.calls.length, 2, "no third fetch: the old backoff was dropped");
});

test("late continuations after a stop or restart never touch the new state", async () => {
  // fetch rejected while stopped: no retry is even scheduled
  const a = segmentHarness();
  a.player.start({ id: "job-00000010", sessionId: "session-11" });
  await drain();
  a.player.stop();
  a.fetch.calls[0].reject(new Error("down"));
  await drain();
  a.timers.flush(Speech.SEGMENT_RETRY_BACKOFF_MS);
  await drain();
  assert.strictEqual(a.fetch.calls.length, 1);
  assert.strictEqual(a.player.state().inFlight, false);

  // a parked retry backoff dies with the stop
  const b = segmentHarness();
  b.player.start({ id: "job-00000010", sessionId: "session-11" });
  await drain();
  b.fetch.calls[0].reject(new Error("down"));
  await drain();                 // the backoff is parked now
  b.player.stop();
  b.timers.flush(Speech.SEGMENT_RETRY_BACKOFF_MS);
  await drain();
  assert.strictEqual(b.fetch.calls.length, 1);
  assert.strictEqual(b.player.state().inFlight, false);

  // a 200 whose body is unreadable, with a restart landing between the
  // response handler and the json microtask (two hops: the race adapter,
  // then the handler that calls json): the OLD generation's failure drops
  const c = segmentHarness();
  c.player.start({ id: "job-00000011", sessionId: "session-11" });
  await drain();
  c.fetch.calls[0].resolve(response(200));   // json() will reject
  await Promise.resolve();                   // hop 1: the race adapter settles
  await Promise.resolve();                   // hop 2: the response handler queues json
  c.player.start({ id: "job-00000012", sessionId: "session-11" });
  await drain();
  assert.ok(!c.player.state().degraded, "the old body failure never degrades the new job");
  assert.strictEqual(c.fetch.calls.length, 2);

  // same choreography with a stop: the json continuation parks inFlight
  const e = segmentHarness();
  e.player.start({ id: "job-00000011", sessionId: "session-11" });
  await drain();
  e.fetch.calls[0].resolve(response(200));
  await Promise.resolve();
  await Promise.resolve();
  e.player.stop();
  await drain();
  assert.ok(!e.player.state().degraded);
  assert.strictEqual(e.player.state().inFlight, false);

  // same choreography with a readable body: the old generation's segment
  // is dropped before it can enqueue under the new job
  const d = segmentHarness();
  d.player.start({ id: "job-00000011", sessionId: "session-11" });
  await drain();
  d.fetch.calls[0].resolve(response(200, { seq: 0, audio_url: "/old.mp3", is_final: false }));
  await Promise.resolve();                   // hop 1: the race adapter settles
  await Promise.resolve();                   // hop 2: the response handler queues json
  d.player.start({ id: "job-00000012", sessionId: "session-11" });
  await drain();
  assert.deepStrictEqual(d.enqueued, [], "the superseded segment is never enqueued");
});

/* ---- app.js wiring (shape pins; behavior is E2E territory) --------------- */

test("wiring: ask() mints the identity before /ask and sends it in the body", () => {
  const start = appSrc.indexOf("function ask(text)");
  const end = appSrc.indexOf("speech recognition: browser engine");
  const region = appSrc.slice(start, end);
  assert.ok(region.includes("speechCtl.begin(sessionId)"));
  assert.ok(/requestAsk\([\s\S]*?speech:\s*speech[\s\S]*?\)/.test(region),
    "the minted fields ride Consult.requestAsk into the /ask body");
  assert.ok(region.includes('data.speech && data.speech.status === "delivering" && !data.audio_url'),
    "a delivering protocol-2 turn without audio_url switches to the segment player");
  assert.ok(region.includes("segmentPlayer.start({ id: speech.speech_request_id, sessionId: sessionId })"),
    "the segment player streams that job into the same audio queue");
  assert.ok(region.includes("enqueueAudio(data.audio_url, null, speech.speech_request_id)"),
    "full-file enqueue stays for legacy AND v1-degraded identified turns");
  assert.ok(region.includes("speechCtl.release(speech.speech_request_id)"),
    "a job with no audio (or a failed ask) is released, never left active");
});

test("wiring: stopAudio cancels server-side, purges by id, lets foreign audio continue", () => {
  const start = appSrc.indexOf("function stopAudio()");
  const end = appSrc.indexOf("function onAudioError()");
  const region = appSrc.slice(start, end);
  assert.ok(region.includes("segmentPlayer.stop()"),
    "a streamed job stops polling when the user stops the audio");
  assert.ok(region.includes("speechCtl.cancel()"));
  assert.ok(region.includes("Speech.purgeQueueById(audioQueue, jobId)"));
  assert.ok(region.includes("audioQueue.length = 0"), "no identified job: the legacy clear-all stays");
  assert.ok(region.includes("player.pause()"), "local audio stops immediately, always");
  assert.ok(/if \(audioQueue\.length\) \{\s*pumpAudio\(\);\s*return;\s*\}/.test(region),
    "other requests' queued audio keeps its turn after the cancel");
  /* VS3.6 (PRD03 FR-08): the stop purges SPEECH only — the pending
   * ledger of ambient announcements is a separate domain and must
   * survive every stop untouched (behavior proven in pending.test.js). */
  assert.ok(!region.includes("pendingStore"),
    "stopAudio never touches the pending records (stop purges speech, keeps records)");
});

test("wiring: enqueueAudio tags items; ended/failure release ONLY legacy items", () => {
  assert.ok(appSrc.includes("speechRequestId: speechRequestId || null"));
  assert.ok(appSrc.includes('seq: typeof seq === "number" ? seq : null'),
    "segment items keep their seq through the backwards-compatible signature");
  assert.ok(appSrc.includes("enqueueAudio(item.url, null, item.speechRequestId, item.seq)"),
    "the player's enqueue hook feeds the same sequential queue");
  assert.ok(appSrc.includes("segmentPlayer.onEnded(finished.seq)"),
    "a finished segment advances the ack watermark");

  /* Identity lifetime (VS2 remediation, PRD01 FR-02/T2/T6): onAudioEnded
   * releases ONLY no-seq (legacy full-file) items — M1 byte-identical —
   * and the seq branch NEVER releases: the segment player owns the
   * identity until a terminal status, or a mid-stream stop could neither
   * POST the cancel nor purge by id. */
  const endedStart = appSrc.indexOf("function onAudioEnded()");
  const endedEnd = appSrc.indexOf("function stopAudio()");
  assert.ok(endedStart !== -1 && endedEnd !== -1, "onAudioEnded region not found");
  const endedRegion = appSrc.slice(endedStart, endedEnd);
  assert.ok(
    /if \(finished && finished\.seq === null\) \{\s*speechCtl\.release\(finished\.speechRequestId\);\s*\}/.test(endedRegion),
    "the ended release is gated to legacy (no-seq) items only");
  assert.strictEqual(endedRegion.split("speechCtl.release(").length - 1, 1,
    "onAudioEnded has exactly one release site — the gated legacy one");
  assert.ok(endedRegion.includes("segmentPlayer.onEnded(finished.seq)"),
    "the seq branch advances the ack watermark instead of releasing");

  /* Same split on the shared play-failure handler (onAudioError routes
   * announcement failures here; everything else runs onAudioEnded). */
  const failStart = appSrc.indexOf("function handlePlayFailure(item)");
  const failEnd = appSrc.indexOf("function onAudioEnded()");
  assert.ok(failStart !== -1, "handlePlayFailure region not found");
  const failRegion = appSrc.slice(failStart, failEnd);
  assert.ok(
    /if \(item\.seq === null\) \{\s*speechCtl\.release\(item\.speechRequestId\);\s*\}/.test(failRegion),
    "handlePlayFailure releases only legacy items too");
  assert.strictEqual(failRegion.split("speechCtl.release(").length - 1, 1);
});

test("wiring: the segment player's terminal statuses release the controller identity", () => {
  const start = appSrc.indexOf("var segmentPlayer = Speech.createSegmentPlayer({");
  const end = appSrc.indexOf("function enqueueAudio(");
  assert.ok(start !== -1 && end !== -1, "segmentPlayer wiring region not found");
  const region = appSrc.slice(start, end);
  /* The player's terminal set — complete / server-* (cancelled, degraded,
   * failed, expired-unconsumed) / local degraded — is the segment path's
   * ONLY release: the job is terminal, nothing is left to cancel. */
  assert.ok(region.includes('status.state === "complete"'));
  assert.ok(region.includes('status.state === "degraded"'));
  assert.ok(region.includes("status.state.indexOf(\"server-\") === 0"),
    "every server-terminal status is covered by the release hook");
  assert.ok(region.includes("speechCtl.release(status.id)"),
    "a terminal player status releases the controller identity (the job id)");
  assert.strictEqual(region.split("speechCtl.release(").length - 1, 1,
    "the segment player wiring has exactly one release site");
  assert.ok(!region.includes("speechCtl.release(item.speechRequestId)"));
});

test("wiring: endCall reaches the same cancel path and index loads speech.js first", () => {
  const start = appSrc.indexOf("function endCall()");
  assert.ok(appSrc.slice(start, start + 600).includes("stopAudio();"));
  const speechAt = indexSrc.indexOf('<script src="/speech.js"></script>');
  const appAt = indexSrc.indexOf('<script src="/app.js"></script>');
  assert.ok(speechAt !== -1 && appAt !== -1 && speechAt < appAt);
});

/* VS3.6 (PRD03/T8): every SSE transition is ALSO recorded in the
 * pending ledger — bookkeeping + recovery only, never a second queue.
 * Shape pins only; the store's behavior is EXECUTED in
 * pending.test.js. */
test("wiring: SSE transitions append to the pending store; playback path unchanged", () => {
  const start = appSrc.indexOf("function openEvents()");
  const end = appSrc.indexOf("fetch with hard timeout");
  assert.ok(start !== -1 && end !== -1 && start < end, "openEvents region found");
  const region = appSrc.slice(start, end);
  assert.ok(region.includes("pendingStore.observe(ann)"),
    "every transition SSE event is recorded in the pending ledger");
  assert.ok(region.includes("announcer.handle(ann)"),
    "the announcement still flows through the announcer — the store is bookkeeping, not a second queue");

  const storeStart = appSrc.indexOf("var pendingStore = Speech.createPendingStore({");
  assert.ok(storeStart !== -1, "pendingStore wiring found");
  const storeEnd = appSrc.indexOf("function openEvents()");
  assert.ok(storeEnd !== -1 && storeStart < storeEnd, "pendingStore wiring region found");
  const storeRegion = appSrc.slice(storeStart, storeEnd);
  assert.ok(storeRegion.includes("storage: window.localStorage"),
    "the ledger persists in localStorage under the locked key");
  assert.ok(/onListen[\s\S]{0,160}speakText\(/.test(storeRegion),
    "listen regenerates speech from TEXT through the NORMAL TTS pipeline (legacy path, no speech_request_id)");
  assert.ok(!storeRegion.includes("enqueueAudio"),
    "the store never enqueues audio itself");
  assert.ok(/onChange[\s\S]{0,80}renderPendingSection/.test(storeRegion),
    "every mutation re-renders the Pendientes section");
  assert.ok(appSrc.includes('pendingSection.id = "pending-panel"'),
    "the narrow Pendientes section is built from JS (index.html is frozen)");
});

/* ---- insecure-context robustness (the PWA is also served over LAN HTTP) ---- */

test("mint falls back to a v4 UUID from getRandomValues when randomUUID is missing", () => {
  const noUuid = {
    getRandomValues: (b) => { for (let i = 0; i < b.length; i++) b[i] = 0xff; return b; },
  };
  const minted = Speech.mintSpeechRequest(noUuid);
  assert.match(minted.id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.ok(Speech.validId(minted.id));
  assert.match(minted.token, /^[0-9a-f]{64}$/);
});

test("begin degrades to the legacy path when crypto is unusable", () => {
  const ctl = Speech.createSpeechController({
    fetch: scriptedFetch([]), setTimeout: instantTimers(), crypto: undefined,
  });
  assert.strictEqual(ctl.begin("s"), null);
  assert.strictEqual(ctl.activeId(), null);
});
