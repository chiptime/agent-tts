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

/* ---- formatted payload parity ---- */

test("announcement html rides every toast the module shows", () => {
  const { deps, calls } = fakes({ isMuted: () => true });
  const a = createAnnouncer(deps);
  const withHtml = ann();
  withHtml.html = "<p>ha terminado</p>";
  a.handle(withHtml);
  assert.equal(calls.toast[0].html, "<p>ha terminado</p>");  // formatted avisos parity
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
