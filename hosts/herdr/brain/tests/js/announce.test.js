/* node --test suite for the pure announcement playback policy module.
 *
 * The announcer owns the PRD §C decision table for out-of-call
 * announcements (muted / blocked / ok) and the ok ⇄ blocked state
 * machine; every side effect goes through the injected dependencies,
 * exactly like endpointing.js and toast.js.
 */

const test = require("node:test");
const assert = require("node:assert");
const { createAnnouncer } = require("../../src/herdr_brain/static/announce.js");

/* Recording harness mirroring how app.js wires the deps: showToast
 * keeps the historical (text, durationMs, kind, html) signature. */
function fakes(overrides) {
  const calls = { play: [], toast: [], blocked: [] };
  const deps = {
    play: ann => { calls.play.push(ann); },
    showToast: (text, durationMs, kind, html) => {
      calls.toast.push({ text, durationMs, kind, html });
    },
    isMuted: () => false,
    isInCall: () => false,
    onBlockedChange: blocked => { calls.blocked.push(blocked); }
  };
  Object.assign(deps, overrides || {});
  return { deps, calls };
}

const ann = () => ({
  type: "transition",
  label: "opencode dotfiles",
  text: "ha terminado",
  audio_url: "/audio/ann-1.mp3"
});

/* ---- initial state ---- */

test("starts unblocked and emits no blocked-change callback", () => {
  const { deps, calls } = fakes();
  const a = createAnnouncer(deps);
  assert.equal(a.isBlocked(), false);
  assert.deepEqual(calls.blocked, []);
});

/* ---- FR-03: play on arrival ---- */

test("unmuted and unblocked: announcement with audio plays on arrival", () => {
  const { deps, calls } = fakes();
  const a = createAnnouncer(deps);
  a.handle(ann());
  assert.equal(calls.play.length, 1);            // played, not just shown
  assert.deepEqual(calls.toast, []);             // playback pipeline owns its toast
  assert.equal(a.isBlocked(), false);
});

test("announcement without audio_url shows a timed text toast and never plays", () => {
  const { deps, calls } = fakes();
  const a = createAnnouncer(deps);
  a.handle({ label: "opencode dotfiles", text: "necesita tu atención" });
  assert.deepEqual(calls.play, []);
  assert.equal(calls.toast.length, 1);
  assert.equal(calls.toast[0].text, "🔊 opencode dotfiles: necesita tu atención");
  assert.equal(calls.toast[0].durationMs, 6000);
});

test("falsy announcement is a complete no-op", () => {
  const { deps, calls } = fakes();
  const a = createAnnouncer(deps);
  a.handle(null);
  a.handle(undefined);
  assert.deepEqual(calls.play, []);
  assert.deepEqual(calls.toast, []);
});

/* ---- FR-06: mute has priority ---- */

test("muted: toast only, never plays (no regression)", () => {
  const { deps, calls } = fakes({ isMuted: () => true });
  const a = createAnnouncer(deps);
  a.handle(ann());
  assert.deepEqual(calls.play, []);
  assert.equal(calls.toast.length, 1);
  assert.equal(calls.toast[0].text, "🔇 opencode dotfiles: ha terminado");
  assert.equal(calls.toast[0].durationMs, 6000);
});

test("muted dominates blocked state", () => {
  const { deps, calls } = fakes({ isMuted: () => true });
  const a = createAnnouncer(deps);
  a.onPlayRejected(ann());                        // enter blocked
  calls.toast.length = 0;
  a.handle(ann());
  assert.deepEqual(calls.play, []);
  assert.equal(calls.toast.length, 1);
  assert.equal(calls.toast[0].durationMs, 6000);  // the plain muted toast, not persistent
});

/* ---- FR-04 / FR-05: blocked state machine ---- */

test("blocked arrival: persistent toast, no playback", () => {
  const { deps, calls } = fakes();
  const a = createAnnouncer(deps);
  a.onPlayRejected(ann());
  calls.toast.length = 0;
  a.handle(ann());
  assert.deepEqual(calls.play, []);               // dropped, not queued
  assert.equal(calls.toast.length, 1);
  assert.equal(calls.toast[0].text, "🔊 opencode dotfiles: ha terminado");
  assert.equal(calls.toast[0].durationMs, undefined);  // persistent: no auto-hide
});

test("blocked announcements are dropped for good: unlock never replays them", () => {
  const { deps, calls } = fakes();
  const a = createAnnouncer(deps);
  a.onPlayRejected(ann());
  a.handle(ann());                                // dropped while blocked
  a.unlock();
  assert.deepEqual(calls.play, []);               // still zero: no retro playback
  a.handle(ann());                                // the NEXT one plays normally
  assert.equal(calls.play.length, 1);
});

test("unlock returns to ok and notifies the affordance", () => {
  const { deps, calls } = fakes();
  const a = createAnnouncer(deps);
  a.onPlayRejected(ann());
  assert.deepEqual(calls.blocked, [true]);
  a.unlock();
  assert.equal(a.isBlocked(), false);
  assert.deepEqual(calls.blocked, [true, false]);
});

test("unlock while already ok is a no-op: no spurious callback", () => {
  const { deps, calls } = fakes();
  const a = createAnnouncer(deps);
  a.unlock();
  assert.deepEqual(calls.blocked, []);
  assert.equal(a.isBlocked(), false);
});

test("re-blocking after an unlock notifies again", () => {
  const { deps, calls } = fakes();
  const a = createAnnouncer(deps);
  a.onPlayRejected(ann());
  a.unlock();
  a.onPlayRejected(ann());
  assert.deepEqual(calls.blocked, [true, false, true]);
  assert.equal(a.isBlocked(), true);
});

test("blocked state only intercepts playable announcements", () => {
  const { deps, calls } = fakes();
  const a = createAnnouncer(deps);
  a.onPlayRejected(ann());
  calls.toast.length = 0;
  a.handle({ label: "opencode dotfiles", text: "necesita tu atención" });  // no audio_url
  assert.deepEqual(calls.play, []);
  assert.equal(calls.toast.length, 1);
  assert.equal(calls.toast[0].durationMs, 6000);  // text-only keeps the timed toast
});

/* ---- FR-04: play rejection paths ---- */

test("onPlayRejected: enters blocked, keeps the text visible, shows affordance", () => {
  const { deps, calls } = fakes();
  const a = createAnnouncer(deps);
  a.onPlayRejected(ann());
  assert.equal(a.isBlocked(), true);
  assert.deepEqual(calls.blocked, [true]);
  assert.equal(calls.toast.length, 1);
  assert.equal(calls.toast[0].text, "🔊 opencode dotfiles: ha terminado");
  assert.equal(calls.toast[0].durationMs, undefined);  // persistent (FR-04)
});

test("onPlayRejected without an announcement flips state but shows no toast", () => {
  const { deps, calls } = fakes();
  const a = createAnnouncer(deps);
  a.onPlayRejected(null);
  assert.equal(a.isBlocked(), true);
  assert.deepEqual(calls.blocked, [true]);
  assert.deepEqual(calls.toast, []);              // nothing to keep visible
});

test("asynchronous play rejection routes to blocked with no unhandled promise", async () => {
  const { deps, calls } = fakes({
    play: () => Promise.reject(new Error("NotAllowedError"))
  });
  const a = createAnnouncer(deps);
  a.handle(ann());
  assert.equal(a.isBlocked(), false);             // the rejection is still in flight
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(a.isBlocked(), true);              // settled into blocked
  assert.deepEqual(calls.blocked, [true]);
  assert.equal(calls.toast.length, 1);
  assert.equal(calls.toast[0].durationMs, undefined);
  assert.equal(calls.toast[0].text, "🔊 opencode dotfiles: ha terminado");
});

test("a fulfilled play promise leaves the state ok", async () => {
  const { deps, calls } = fakes({ play: a => Promise.resolve() });
  const a2 = createAnnouncer(deps);
  a2.handle(ann());
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(a2.isBlocked(), false);
  assert.deepEqual(calls.toast, []);
});

test("a synchronous play throw is treated as a rejection", () => {
  const { deps, calls } = fakes({
    play: () => { throw new Error("media error"); }
  });
  const a = createAnnouncer(deps);
  a.handle(ann());                                // must not escape handle()
  assert.equal(a.isBlocked(), true);
  assert.equal(calls.toast.length, 1);
  assert.equal(calls.toast[0].durationMs, undefined);
});

/* ---- FR-08: in-call legacy delegation ---- */

test("in call: audio announcement delegates to the existing pipeline even when blocked", () => {
  const { deps, calls } = fakes({ isInCall: () => true });
  const a = createAnnouncer(deps);
  a.onPlayRejected(ann());                        // blocked out-of-call earlier
  a.handle(ann());
  assert.equal(calls.play.length, 1);             // in-call ignores blocked state
});

test("in call and muted: toast only, exactly like today", () => {
  const { deps, calls } = fakes({ isInCall: () => true, isMuted: () => true });
  const a = createAnnouncer(deps);
  a.handle(ann());
  assert.deepEqual(calls.play, []);
  assert.equal(calls.toast.length, 1);
  assert.equal(calls.toast[0].text, "🔇 opencode dotfiles: ha terminado");
  assert.equal(calls.toast[0].durationMs, 6000);
});

test("in call without audio_url: timed text toast as today", () => {
  const { deps, calls } = fakes({ isInCall: () => true });
  const a = createAnnouncer(deps);
  a.handle({ label: "opencode dotfiles", text: "necesita tu atención" });
  assert.deepEqual(calls.play, []);
  assert.equal(calls.toast[0].durationMs, 6000);
});

/* ---- toast html parity with today's app.js (T2 preservation) ----
 * openEvents today shows the muted and text-only toasts as PLAIN
 * (text, ms) calls — no kind, no html mount. Only the playback toast
 * (pumpAudio) and the new persistent blocked toast carry announcement
 * html. The module must match those shapes exactly so wiring the
 * transition branch through it changes nothing observable. */

test("muted and text-only toasts stay plain: no kind, no html mount", () => {
  let mutedNow = true;                          // module captures the fn: flip via closure
  const { deps, calls } = fakes({ isMuted: () => mutedNow });
  const a = createAnnouncer(deps);
  const mutedAnn = ann();
  mutedAnn.html = "<p>ha terminado</p>";
  a.handle(mutedAnn);
  assert.equal(calls.toast[0].text, "🔇 opencode dotfiles: ha terminado");
  assert.equal(calls.toast[0].durationMs, 6000);
  assert.equal(calls.toast[0].kind, undefined);   // exactly today's 2-arg call
  assert.equal(calls.toast[0].html, undefined);
  mutedNow = false;                               // text-only path: unmuted
  const textOnly = { label: "opencode dotfiles", text: "necesita tu atención",
                     html: "<p>atención</p>" };
  a.handle(textOnly);
  assert.equal(calls.toast[1].text, "🔊 opencode dotfiles: necesita tu atención");
  assert.equal(calls.toast[1].durationMs, 6000);
  assert.equal(calls.toast[1].kind, undefined);
  assert.equal(calls.toast[1].html, undefined);
});

test("the persistent blocked toast carries announcement html like pumpAudio", () => {
  const { deps, calls } = fakes();
  const a = createAnnouncer(deps);
  const withHtml = ann();
  withHtml.html = "<p>ha terminado</p>";
  a.onPlayRejected(withHtml);
  assert.equal(calls.toast[0].text, "🔊 opencode dotfiles: ha terminado");
  assert.equal(calls.toast[0].durationMs, undefined);
  assert.equal(calls.toast[0].kind, null);              // pumpAudio toast shape
  assert.equal(calls.toast[0].html, "<p>ha terminado</p>");
});

test("announcer works without an onBlockedChange callback", () => {
  const { deps, calls } = fakes();
  delete deps.onBlockedChange;
  const a = createAnnouncer(deps);
  a.onPlayRejected(ann());
  assert.equal(a.isBlocked(), true);              // no throw, state still tracked
  a.unlock();
  assert.equal(a.isBlocked(), false);
});

/* ---- T3: queue-drain and post-unlock rejection semantics ----
 * pumpAudio drains queued announcements when a play() rejects: each
 * item takes its turn, its turn fails, its rejection calls
 * onPlayRejected. The module contract that makes the drain safe: the
 * affordance fires ONCE (state already blocked) while every drained
 * text still gets its persistent toast, and a rejection arriving after
 * an unlock re-enters blocked. These are pins of existing T1 behavior
 * the T3 wiring relies on, not new module logic. */

test("consecutive rejections while blocked: each text shows, affordance fires once", () => {
  const { deps, calls } = fakes();
  const a = createAnnouncer(deps);
  a.onPlayRejected(ann());
  const second = ann();
  second.label = "claude servidor";
  a.onPlayRejected(second);
  assert.deepEqual(calls.blocked, [true]);        // no duplicate affordance fire
  assert.equal(calls.toast.length, 2);            // both texts were shown
  assert.equal(calls.toast[1].text, "🔊 claude servidor: ha terminado");
  assert.equal(calls.toast[1].durationMs, undefined);  // newest text persists
});

test("rejection after unlock re-enters blocked and shows the text", () => {
  const { deps, calls } = fakes();
  const a = createAnnouncer(deps);
  a.onPlayRejected(ann());
  a.unlock();
  a.onPlayRejected(ann());
  assert.deepEqual(calls.blocked, [true, false, true]);
  assert.equal(calls.toast[calls.toast.length - 1].durationMs, undefined);
});

/* ---- T4 correction: unlock-on-proof (requestUnlock) ----
 * The unlock must follow a SUCCESSFUL silent prime inside the gesture:
 * state flips to ok only when the prime's promise fulfills. A refused
 * or throwing prime leaves blocked state, the persistent text and the
 * affordance exactly as they were (FR-04/FR-05). One prime in flight
 * at a time; the promise is always handled here, never unhandled. */

test("requestUnlock while unblocked is a no-op: the prime never runs", () => {
  const { deps, calls } = fakes();
  let primed = 0;
  const a = createAnnouncer(deps);
  a.requestUnlock(() => { primed += 1; return Promise.resolve(); });
  assert.equal(primed, 0);
  assert.equal(a.isBlocked(), false);
  assert.deepEqual(calls.blocked, []);
});

test("successful prime unlocks (state ok, affordance notified)", async () => {
  const { deps, calls } = fakes();
  const a = createAnnouncer(deps);
  a.onPlayRejected(ann());
  a.requestUnlock(() => new Promise(r => { setTimeout(r, 0); }));
  assert.equal(a.isBlocked(), true);           // unlock WAITS for the prime
  await new Promise(r => setTimeout(r, 5));
  assert.equal(a.isBlocked(), false);
  assert.deepEqual(calls.blocked, [true, false]);
});

test("refused prime stays blocked: no unblock callback, text untouched", async () => {
  const { deps, calls } = fakes();
  const a = createAnnouncer(deps);
  a.onPlayRejected(ann());
  calls.toast.length = 0;
  a.requestUnlock(() => Promise.reject(new Error("NotAllowedError")));
  await new Promise(r => setImmediate(r));
  assert.equal(a.isBlocked(), true);           // the browser still blocks
  assert.deepEqual(calls.blocked, [true]);     // affordance never hidden
  assert.deepEqual(calls.toast, []);           // persistent text untouched
});

test("synchronously throwing prime stays blocked and throws nothing out", () => {
  const { deps, calls } = fakes();
  const a = createAnnouncer(deps);
  a.onPlayRejected(ann());
  a.requestUnlock(() => { throw new Error("no Audio support"); });
  assert.equal(a.isBlocked(), true);
  assert.deepEqual(calls.blocked, [true]);
});

test("one prime in flight: a second gesture during the flight is ignored", async () => {
  const { deps } = fakes();
  let primed = 0;
  let settle;
  const a = createAnnouncer(deps);
  a.onPlayRejected(ann());
  const slow = () => new Promise(r => { primed += 1; settle = r; });
  a.requestUnlock(slow);
  a.requestUnlock(slow);                       // rapid second gesture
  assert.equal(primed, 1);
  settle();
  await new Promise(r => setImmediate(r));
  assert.equal(a.isBlocked(), false);
});

test("a failed prime can be retried by a later gesture", async () => {
  const { deps } = fakes();
  let primed = 0;
  const a = createAnnouncer(deps);
  a.onPlayRejected(ann());
  a.requestUnlock(() => { primed += 1; return Promise.reject(new Error("nope")); });
  await new Promise(r => setImmediate(r));
  assert.equal(a.isBlocked(), true);
  a.requestUnlock(() => { primed += 1; return Promise.resolve(); });
  await new Promise(r => setImmediate(r));
  assert.equal(primed, 2);
  assert.equal(a.isBlocked(), false);
});

test("legacy prime without a promise unlocks optimistically", () => {
  const { deps, calls } = fakes();
  const a = createAnnouncer(deps);
  a.onPlayRejected(ann());
  a.requestUnlock(() => { /* no promise returned */ });
  assert.equal(a.isBlocked(), false);
  assert.deepEqual(calls.blocked, [true, false]);
});
