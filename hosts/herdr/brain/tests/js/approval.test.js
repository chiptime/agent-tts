/* node --test suite for the pure approval-gate client flow. */

const test = require("node:test");
const assert = require("node:assert");
const { createApprovalFlow } = require("../../src/herdr_brain/static/approval.js");

function fakeClock(start = 0) {
  let t = start;
  return {
    now: () => t,
    advance: ms => { t += ms; }
  };
}

function makeResponse(spec) {
  const status = spec.status || 200;
  return {
    status,
    ok: status >= 200 && status < 300,
    json: () => Promise.resolve(spec.json || {})
  };
}

/* Scripted HTTP: every call shifts the next planned response spec. */
function scriptedHttp(plan) {
  const calls = [];
  const request = (url, options) => {
    calls.push({ url, options });
    const spec = plan.length ? plan.shift() : { status: 500 };
    return Promise.resolve(makeResponse(spec));
  };
  return { request, calls };
}

/* Deferred HTTP: the test resolves each call by hand (in-flight checks). */
function deferredHttp() {
  const calls = [];
  const pending = [];
  const request = (url, options) => {
    calls.push({ url, options });
    return new Promise(resolve => pending.push(resolve));
  };
  return { request, calls, respond: spec => pending.shift()(makeResponse(spec)) };
}

function bodyOf(call) {
  return JSON.parse(call.options.body);
}

const flush = () => new Promise(resolve => setImmediate(resolve));

/* Full harness: the flow plus recorded callbacks, a fake clock and a
 * fetch mock — per the tests/js convention of injected time and IO. */
function harness(overrides = {}) {
  const clock = fakeClock();
  const states = [];
  const answers = [];
  const played = [];
  const banners = [];
  const expired = [];
  const http = overrides.http || scriptedHttp(overrides.plan || []);
  const flow = createApprovalFlow({
    now: clock.now,
    request: http.request,
    setState: s => states.push(s),
    renderAnswer: a => answers.push(a),
    playAudio: url => played.push(url),
    onExpired: () => expired.push(true),
    banner: m => banners.push(m)
  });
  return { flow, states, answers, played, banners, expired, clock, http };
}

const GATE = {
  gate_id: "a1b2c3d4e5f6",
  tool: "send_to_session",
  pane_id: "3",
  agent: "opencode",
  text: "arregla el bug del login",
  timeout_ms: 300000,
  expires_in_s: 60
};

const RESOLVE_URL = "/approval/a1b2c3d4e5f6/resolve";
const PATCH_URL = "/approval/a1b2c3d4e5f6";

/* ---- /ask with approval{} -> confirming ---- */

test("open() enters confirming and runs the countdown from expires_in_s", () => {
  const h = harness();
  h.flow.open({ ...GATE });
  assert.equal(h.flow.active(), true);
  assert.deepEqual(h.states, ["confirming"]);
  assert.equal(h.flow.remainingSeconds(), 60);
  h.clock.advance(30_000);
  assert.equal(h.flow.remainingSeconds(), 30);
});

test("tick before the window closes is a no-op", () => {
  const h = harness();
  h.flow.open({ ...GATE });
  h.clock.advance(59_999);
  h.flow.tick();
  assert.equal(h.flow.active(), true);
  assert.deepEqual(h.states, ["confirming"]);
});

/* ---- STT routing: resolve, never /ask ---- */

test("a confirming utterance resolves against the gate, never /ask", async () => {
  const h = harness({ plan: [{ json: { decision: "reject" } }] });
  h.flow.open({ ...GATE });
  assert.equal(h.flow.routeUtterance("no"), true);
  await flush();
  assert.equal(h.http.calls.length, 1);
  assert.equal(h.http.calls[0].url, RESOLVE_URL);
  assert.equal(h.http.calls[0].options.method, "POST");
  assert.deepEqual(bodyOf(h.http.calls[0]), { utterance: "no" });
});

test("without a live gate routing declines and the caller falls back to /ask", () => {
  const h = harness();
  assert.equal(h.flow.routeUtterance("hola"), false);
  assert.equal(h.http.calls.length, 0);
});

test("a second utterance during an in-flight round is swallowed", async () => {
  const http = deferredHttp();
  const h = harness({ http });
  h.flow.open({ ...GATE });
  assert.equal(h.flow.routeUtterance("sí"), true);
  assert.equal(h.flow.routeUtterance("no"), true);  // swallowed: no new request
  assert.equal(h.http.calls.length, 1);
  assert.deepEqual(h.states, ["confirming", "thinking"]);
  http.respond({ json: { decision: "reject" } });
  await flush();
  assert.equal(h.flow.active(), false);
  assert.equal(h.flow.routeUtterance("hola"), false);
});

/* ---- decision handling ---- */

test("approve: thinking during the replay, answer rendered, then listening", async () => {
  const h = harness({
    plan: [{ json: { decision: "approve", answer: "Enviado y listo", audio_url: null, approval: null } }]
  });
  h.flow.open({ ...GATE });
  h.flow.routeUtterance("sí");
  await flush();
  assert.deepEqual(h.states, ["confirming", "thinking", "listening"]);
  assert.deepEqual(h.answers, [{ answer: "Enviado y listo", audio_url: null }]);
  assert.deepEqual(h.played, []);
  assert.equal(h.flow.active(), false);
});

test("approve with audio hands the return to listening to the audio queue", async () => {
  const h = harness({
    plan: [{ json: { decision: "approve", answer: "Enviado", audio_url: "/audio/x.mp3", approval: null } }]
  });
  h.flow.open({ ...GATE });
  h.flow.routeUtterance("dale");
  await flush();
  assert.deepEqual(h.states, ["confirming", "thinking"]);  // queue resumes at listening
  assert.deepEqual(h.answers, [{ answer: "Enviado", audio_url: "/audio/x.mp3" }]);
  assert.equal(h.flow.active(), false);
});

test("reject is silent: straight back to listening", async () => {
  const h = harness({ plan: [{ json: { decision: "reject" } }] });
  h.flow.open({ ...GATE });
  h.flow.routeUtterance("no lo envíes");
  await flush();
  assert.deepEqual(h.states, ["confirming", "thinking", "listening"]);
  assert.deepEqual(h.answers, []);
  assert.deepEqual(h.played, []);
  assert.deepEqual(h.banners, []);
  assert.equal(h.flow.active(), false);
});

test("reprompt re-echoes ¿Sí o no? and stays confirming for the next round", async () => {
  const h = harness({
    plan: [
      { json: { decision: "reprompt", answer: "¿Sí o no?", audio_url: "/audio/r.mp3", approval: null } },
      { json: { decision: "reject" } }
    ]
  });
  h.flow.open({ ...GATE });
  h.flow.routeUtterance("hola");
  await flush();
  assert.deepEqual(h.played, ["/audio/r.mp3"]);
  assert.equal(h.flow.active(), true);
  assert.deepEqual(h.states, ["confirming", "thinking"]);  // audio owns the confirming return
  // The next STT round resolves again against the SAME gate.
  h.flow.routeUtterance("no");
  await flush();
  assert.equal(h.http.calls.length, 2);
  assert.equal(h.http.calls[1].url, RESOLVE_URL);
  assert.deepEqual(h.states, ["confirming", "thinking", "thinking", "listening"]);
});

test("reprompt without audio re-arms confirming directly", async () => {
  const h = harness({
    plan: [{ json: { decision: "reprompt", answer: "¿Sí o no?", audio_url: null, approval: null } }]
  });
  h.flow.open({ ...GATE });
  h.flow.routeUtterance("hola");
  await flush();
  assert.equal(h.flow.active(), true);
  assert.equal(h.states[h.states.length - 1], "confirming");
});

/* ---- listen_replace: one dictation round -> PATCH -> re-confirm ---- */

test("listen_replace: dictation -> PATCH -> re-confirm with restarted timer", async () => {
  const h = harness({
    plan: [
      { json: { decision: "listen_replace", answer: null, audio_url: null, approval: null } },
      { json: { decision: "reprompt", answer: "¿Sí o no?", audio_url: null, approval: null } },
      { status: 200, json: { ok: true, approval: { ...GATE, text: "nuevo texto dictado", expires_in_s: 60 } } },
      { json: { audio_url: "/audio/echo.mp3" } }
    ]
  });
  h.flow.open({ ...GATE });
  h.clock.advance(45_000);  // 15 s left on the old window
  h.flow.routeUtterance("cambia el texto");
  await flush();
  assert.equal(h.flow.isDictating(), true);
  assert.equal(h.states[h.states.length - 1], "confirming");
  // The dictation itself still routes to /resolve first (command precedence).
  h.flow.routeUtterance("nuevo texto dictado");
  await flush();
  assert.equal(h.http.calls.length, 4);
  assert.equal(h.http.calls[1].url, RESOLVE_URL);
  assert.equal(h.http.calls[2].url, PATCH_URL);
  assert.equal(h.http.calls[2].options.method, "PATCH");
  assert.deepEqual(bodyOf(h.http.calls[2]), { text: "nuevo texto dictado" });
  assert.equal(h.http.calls[3].url, "/tts");
  assert.deepEqual(bodyOf(h.http.calls[3]), { text: "nuevo texto dictado" });
  assert.deepEqual(h.played, ["/audio/echo.mp3"]);
  assert.equal(h.flow.active(), true);
  assert.equal(h.flow.gate().text, "nuevo texto dictado");
  assert.equal(h.flow.remainingSeconds(), 60);  // restarted, not the old 15
  assert.equal(h.flow.isDictating(), false);
});

test("an approval command during the dictation round resolves as usual (no PATCH)", async () => {
  const h = harness({
    plan: [
      { json: { decision: "listen_replace" } },
      { json: { decision: "approve", answer: "Enviado", audio_url: "/audio/x.mp3", approval: null } }
    ]
  });
  h.flow.open({ ...GATE });
  h.flow.routeUtterance("cambia el texto");
  await flush();
  h.flow.routeUtterance("sí");
  await flush();
  assert.equal(h.http.calls.length, 2);
  for (const call of h.http.calls) {
    assert.equal(call.url, RESOLVE_URL);  // never a PATCH, never /ask
  }
  assert.deepEqual(h.answers, [{ answer: "Enviado", audio_url: "/audio/x.mp3" }]);
  assert.equal(h.flow.active(), false);
  assert.equal(h.flow.isDictating(), false);
});

test("a repeated replace-intent during dictation keeps waiting for the text", async () => {
  const h = harness({
    plan: [
      { json: { decision: "listen_replace" } },
      { json: { decision: "listen_replace" } }
    ]
  });
  h.flow.open({ ...GATE });
  h.flow.routeUtterance("cambia el texto");
  await flush();
  h.flow.routeUtterance("redicta");
  await flush();
  assert.equal(h.http.calls.length, 2);
  assert.equal(h.http.calls[1].url, RESOLVE_URL);
  assert.equal(h.flow.isDictating(), true);
  assert.equal(h.states[h.states.length - 1], "confirming");
});

test("a failed /tts re-echo re-confirms text-only", async () => {
  const h = harness({
    plan: [
      { json: { decision: "listen_replace" } },
      { json: { decision: "reprompt", answer: "¿Sí o no?", audio_url: null, approval: null } },
      { status: 200, json: { ok: true, approval: { ...GATE, text: "texto revisado", expires_in_s: 60 } } },
      { status: 502, json: {} }
    ]
  });
  h.flow.open({ ...GATE });
  h.flow.routeUtterance("cambia el texto");
  await flush();
  h.flow.routeUtterance("texto revisado");
  await flush();
  assert.equal(h.flow.active(), true);
  assert.equal(h.flow.gate().text, "texto revisado");
  assert.equal(h.flow.remainingSeconds(), 60);
  assert.equal(h.states[h.states.length - 1], "confirming");
  assert.equal(h.banners.length, 1);
});

/* ---- expiry (countdown + 404) ---- */

test("countdown hitting 0 expires silently back to listening", () => {
  const h = harness();
  h.flow.open({ ...GATE });
  h.clock.advance(60_000);
  h.flow.tick();
  assert.deepEqual(h.expired, [true]);
  assert.equal(h.states[h.states.length - 1], "listening");
  assert.equal(h.flow.active(), false);
  assert.deepEqual(h.banners, []);
  assert.deepEqual(h.played, []);
  // The server owns the truth; the expired payload stays for the gray card.
  assert.equal(h.flow.isExpired(), true);
  assert.equal(h.flow.gate().gate_id, GATE.gate_id);
  // Post-expiry utterances are plain questions again.
  assert.equal(h.flow.routeUtterance("hola"), false);
});

test("404 from resolve (expired at touch) is a silent terminal", async () => {
  const h = harness({
    plan: [{ status: 404, json: { detail: "approval gate not found or no longer active" } }]
  });
  h.flow.open({ ...GATE });
  h.flow.routeUtterance("sí");
  await flush();
  assert.deepEqual(h.states, ["confirming", "thinking", "listening"]);
  assert.equal(h.flow.active(), false);
  assert.deepEqual(h.banners, []);
  assert.deepEqual(h.answers, []);
});

test("404 from PATCH is terminal too", async () => {
  const h = harness({
    plan: [
      { json: { decision: "listen_replace" } },
      { json: { decision: "reprompt", answer: "¿Sí o no?", audio_url: null, approval: null } },
      { status: 404, json: { detail: "approval gate not found or no longer active" } }
    ]
  });
  h.flow.open({ ...GATE });
  h.flow.routeUtterance("cambia el texto");
  await flush();
  h.flow.routeUtterance("texto que ya no importa");
  await flush();
  assert.equal(h.states[h.states.length - 1], "listening");
  assert.equal(h.flow.active(), false);
  assert.deepEqual(h.played, []);
  assert.deepEqual(h.banners, []);
});

/* ---- lifecycle ---- */

test("cancel drops the gate silently", () => {
  const h = harness();
  h.flow.open({ ...GATE });
  h.flow.cancel();
  assert.equal(h.flow.active(), false);
  assert.deepEqual(h.states, ["confirming"]);  // no extra emission
  assert.equal(h.flow.routeUtterance("hola"), false);
});

test("a failed resolve keeps the gate live and surfaces a banner", async () => {
  const calls = [];
  const h = harness({ http: { request: () => Promise.reject(new Error("boom")), calls } });
  h.flow.open({ ...GATE });
  h.flow.routeUtterance("sí");
  await flush();
  assert.equal(h.flow.active(), true);
  assert.equal(h.states[h.states.length - 1], "confirming");
  assert.equal(h.banners.length, 1);
});

test("a failed PATCH keeps or drops the gate with a banner, never silently stuck", async () => {
  const h = harness({
    plan: [
      { json: { decision: "listen_replace" } },
      { json: { decision: "reprompt", answer: "¿Sí o no?", audio_url: null, approval: null } },
      { status: 500, json: {} }
    ]
  });
  h.flow.open({ ...GATE });
  h.flow.routeUtterance("cambia el texto");
  await flush();
  h.flow.routeUtterance("nuevo texto");
  await flush();
  assert.equal(h.flow.active(), true);      // gate untouched server-side: still live
  assert.equal(h.states[h.states.length - 1], "confirming");
  assert.equal(h.banners.length, 1);
});
