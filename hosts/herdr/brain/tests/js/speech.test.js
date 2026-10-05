/* Speech request identity + cancel (voice-stack VS1.7).
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

/* ---- app.js wiring (shape pins; behavior is E2E territory) --------------- */

test("wiring: ask() mints the identity before /ask and sends it in the body", () => {
  const start = appSrc.indexOf("function ask(text)");
  const end = appSrc.indexOf("speech recognition: browser engine");
  const region = appSrc.slice(start, end);
  assert.ok(region.includes("speechCtl.begin(sessionId)"));
  assert.ok(/Object\.assign\(\s*\{[\s\S]*?\},\s*speech\s*\)/.test(region),
    "the minted fields are merged into the /ask body");
  assert.ok(region.includes("enqueueAudio(data.audio_url, null, speech.speech_request_id)"),
    "queue items carry the request id so a cancel can purge by id");
  assert.ok(region.includes("speechCtl.release(speech.speech_request_id)"),
    "a job with no audio (or a failed ask) is released, never left active");
});

test("wiring: stopAudio cancels server-side, purges by id, lets foreign audio continue", () => {
  const start = appSrc.indexOf("function stopAudio()");
  const end = appSrc.indexOf("function onAudioError()");
  const region = appSrc.slice(start, end);
  assert.ok(region.includes("speechCtl.cancel()"));
  assert.ok(region.includes("Speech.purgeQueueById(audioQueue, jobId)"));
  assert.ok(region.includes("audioQueue.length = 0"), "no identified job: the legacy clear-all stays");
  assert.ok(region.includes("player.pause()"), "local audio stops immediately, always");
  assert.ok(/if \(audioQueue\.length\) \{\s*pumpAudio\(\);\s*return;\s*\}/.test(region),
    "other requests' queued audio keeps its turn after the cancel");
});

test("wiring: enqueueAudio tags items; ended/failure release the finished job", () => {
  assert.ok(appSrc.includes("speechRequestId: speechRequestId || null"));
  assert.ok(appSrc.includes("speechCtl.release(finished.speechRequestId)"));
  assert.ok(appSrc.includes("speechCtl.release(item.speechRequestId)"));
});

test("wiring: endCall reaches the same cancel path and index loads speech.js first", () => {
  const start = appSrc.indexOf("function endCall()");
  assert.ok(appSrc.slice(start, start + 600).includes("stopAudio();"));
  const speechAt = indexSrc.indexOf('<script src="/speech.js"></script>');
  const appAt = indexSrc.indexOf('<script src="/app.js"></script>');
  assert.ok(speechAt !== -1 && appAt !== -1 && speechAt < appAt);
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
