/* node --test suite for the pure VAD module (server voice engine v2). */

const test = require("node:test");
const assert = require("node:assert");
const Vad = require("../../src/herdr_brain/static/vad.js");
const { createVad } = Vad;

function fakeClock(start = 0) {
  let t = start;
  return {
    now: () => t,
    advance: ms => { t += ms; }
  };
}

const QUIET = 0.002;   // typical noise-room RMS
const SPEECH = 0.08;   // typical speech RMS

test("conversational VAD defaults to 800ms with configurable 700/900ms endpoints", () => {
  for (const silenceMs of [undefined, 700, 900]) {
    const clock = fakeClock();
    const vad = createVad({ now: clock.now, ...(silenceMs === undefined ? {} : { silenceMs }) });
    vad.push(SPEECH);
    clock.advance((silenceMs ?? Vad.DEFAULT_SILENCE_MS) - 1);
    vad.push(QUIET);
    assert.equal(vad.shouldFinalize(), false);
    clock.advance(1);
    assert.equal(vad.shouldFinalize(), true);
  }
});

test("explicit silenceMs alone keeps the baseline flat behavior (parent repro)", () => {
  const clock = fakeClock();
  const vad = createVad({ now: clock.now, silenceMs: 800 });
  vad.push(SPEECH);                 // t=0
  clock.advance(5000);
  vad.push(SPEECH);                 // still speech at t=5000 (5s span)
  clock.advance(799);
  vad.push(QUIET);
  assert.equal(vad.shouldFinalize(), false);  // 799ms < the configured 800ms
  clock.advance(1);                           // t=5800
  assert.equal(vad.shouldFinalize(), true);   // exact 800ms after the last loud frame
});

test("default configuration never grows the silence window", () => {
  const clock = fakeClock();
  const vad = createVad({ now: clock.now });
  clock.advance(100);
  vad.push(SPEECH);
  for (let ms = 0; ms < 5000; ms += 100) { clock.advance(100); vad.push(SPEECH); }
  clock.advance(799);
  assert.equal(vad.shouldFinalize(), false);
  clock.advance(1);
  assert.equal(vad.shouldFinalize(), true);   // flat 800ms despite the 5s span
});

test("opted-in silence budget grows with utterance length so pauses stop splitting turns", () => {
  const clock = fakeClock();
  const vad = createVad({ now: clock.now, silenceMs: 1200, silenceGrowthPerS: 200, maxSilenceMs: 2500 });
  clock.advance(100);
  vad.push(SPEECH);                          // gate opens at t=100
  for (let ms = 0; ms < 5000; ms += 100) {   // 5s of continuous speech
    clock.advance(100);
    vad.push(SPEECH);
  }
  // 5s of speech -> budget 1200 + 5*200 = 2200ms. A natural 1.6s thinking
  // pause mid-turn must NOT finalize the utterance.
  clock.advance(1600);
  vad.push(QUIET);
  assert.equal(vad.shouldFinalize(), false);
  clock.advance(600);                        // silence reaches 2200ms
  assert.equal(vad.shouldFinalize(), true);
});

test("opted-in silence budget is bounded by the 2500ms ceiling", () => {
  const clock = fakeClock();
  const vad = createVad({ now: clock.now, silenceMs: 1200, silenceGrowthPerS: 200, maxSilenceMs: 2500 });
  clock.advance(100);
  vad.push(SPEECH);
  for (let ms = 0; ms < 10000; ms += 100) {  // 10s of speech: growth saturates
    clock.advance(100);
    vad.push(SPEECH);
  }
  clock.advance(2400);
  vad.push(QUIET);
  assert.equal(vad.shouldFinalize(), false);  // 2400ms < 2500ms ceiling
  clock.advance(100);
  assert.equal(vad.shouldFinalize(), true);
});

test("silenceGrowthPerS 0 keeps a flat silence window (previous behavior)", () => {
  const clock = fakeClock();
  const vad = createVad({ now: clock.now, silenceMs: 1200, silenceGrowthPerS: 0 });
  clock.advance(100);
  vad.push(SPEECH);
  for (let ms = 0; ms < 5000; ms += 100) { clock.advance(100); vad.push(SPEECH); }
  clock.advance(1199);
  assert.equal(vad.shouldFinalize(), false);
  clock.advance(1);
  assert.equal(vad.shouldFinalize(), true);
});

test("maxSilenceMs never sits below the base silence window", () => {
  const clock = fakeClock();
  const vad = createVad({ now: clock.now, silenceMs: 2000, maxSilenceMs: 500 });
  clock.advance(100);
  vad.push(SPEECH);
  clock.advance(1999);
  assert.equal(vad.shouldFinalize(), false);
  clock.advance(1);
  assert.equal(vad.shouldFinalize(), true);
});

test("configured VAD cannot exceed the 60-second safety cap", () => {
  const clock = fakeClock();
  const vad = createVad({ now: clock.now, silenceMs: 120000, hardCapMs: 120000 });
  vad.push(SPEECH);
  clock.advance(59999);
  vad.push(SPEECH);
  assert.equal(vad.shouldFinalize(), false);
  clock.advance(1);
  vad.push(SPEECH);
  assert.equal(vad.shouldFinalize(), true);
});

test("quiet input never opens the gate", () => {
  const clock = fakeClock();
  const vad = createVad({ now: clock.now });
  for (let i = 0; i < 600; i++) {  // a full minute of quiet at 100ms frames
    clock.advance(100);
    assert.equal(vad.push(QUIET), false);
  }
  assert.equal(vad.hasSpeech(), false);
  assert.equal(vad.shouldFinalize(), false);
});

test("a loud frame opens the gate and marks speech", () => {
  const clock = fakeClock();
  const vad = createVad({ now: clock.now });
  clock.advance(100);
  assert.equal(vad.push(SPEECH), true);
  assert.equal(vad.hasSpeech(), true);
  assert.equal(vad.shouldFinalize(), false);  // silence timer just started
});

test("noise floor adapts: yesterday's loud becomes today's quiet", () => {
  const clock = fakeClock();
  const vad = createVad({ now: clock.now });
  // Room gets noisier: feed the new louder "quiet" while the gate is closed.
  for (let i = 0; i < 200; i++) { clock.advance(100); vad.push(0.01); }
  const floor = vad.floor();
  assert.ok(floor > 0.008, `floor should track the noisy room (got ${floor})`);
  // 0.02 opened the gate with the default floor (0.004*2.5 = 0.01); with the
  // adapted floor (>= 0.008) the open threshold is >= 0.02, so a level that
  // is only twice the background must NOT count as speech anymore.
  clock.advance(100);
  assert.equal(vad.push(0.019), false);
  assert.equal(vad.hasSpeech(), false);
});

test("floor never drops below the minimum", () => {
  const vad = createVad({ now: fakeClock().now });
  for (let i = 0; i < 500; i++) vad.push(0);  // pure digital silence
  assert.ok(vad.floor() >= 0.004);
});

test("hysteresis: a brief dip mid-utterance does not end it", () => {
  const clock = fakeClock();
  const vad = createVad({ now: clock.now });
  clock.advance(100);
  vad.push(SPEECH);                       // gate opens
  clock.advance(200);
  vad.push(SPEECH);                       // keep talking
  clock.advance(100);
  vad.push(0.006);                        // dip: above close threshold? 0.004*1.4=0.0056 -> quiet-ish
  clock.advance(500);                     // 500ms < 1200ms below threshold
  vad.push(SPEECH);                       // talking again before the silence elapsed
  clock.advance(100);
  assert.equal(vad.shouldFinalize(), false);
  assert.equal(vad.hasSpeech(), true);
});

test("utterance finalizes after silenceMs below the close threshold", () => {
  const clock = fakeClock();
  const vad = createVad({ now: clock.now, silenceMs: 1200 });
  clock.advance(100);
  vad.push(SPEECH);
  clock.advance(1199);
  assert.equal(vad.shouldFinalize(), false);  // not yet
  clock.advance(1);                           // 1200ms sharp (explicit override)
  assert.equal(vad.shouldFinalize(), true);
});

test("15s default hard cap finalizes even with continuous speech", () => {
  const clock = fakeClock();
  const vad = createVad({ now: clock.now });
  clock.advance(100);
  vad.push(SPEECH);
  for (let ms = 0; ms < 15000; ms += 100) {
    clock.advance(100);
    vad.push(SPEECH);  // never silent: the cap must end it
  }
  assert.equal(vad.shouldFinalize(), true);
  assert.equal(vad.lastFinalizeReason(), "cap");
});

test("utteranceCapMs replaces the legacy cap and reports its own reason", () => {
  const clock = fakeClock();
  const vad = createVad({ now: clock.now, utteranceCapMs: 30000 });
  clock.advance(100);
  vad.push(SPEECH);
  for (let ms = 0; ms < 29000; ms += 100) {
    clock.advance(100);
    vad.push(SPEECH);
  }
  assert.equal(vad.shouldFinalize(), false);  // the legacy 15s cap is NOT active
  for (let ms = 0; ms < 1000; ms += 100) {
    clock.advance(100);
    vad.push(SPEECH);
  }
  assert.equal(vad.shouldFinalize(), true);   // bounded at the configured 30s
  assert.equal(vad.lastFinalizeReason(), "utteranceCap");
});

test("shouldSegment marks recorder-rotation boundaries only while speech runs", () => {
  const clock = fakeClock();
  // The app's server-engine profile: segments at 45s, far utterance cap.
  const vad = createVad({
    now: clock.now, silenceMs: 1200, silenceGrowthPerS: 200, maxSilenceMs: 2500,
    segmentMs: 45000, utteranceCapMs: 600000,
  });
  assert.equal(vad.shouldSegment(), false);   // no speech yet
  clock.advance(100);
  vad.push(SPEECH);
  for (let ms = 0; ms < 44000; ms += 100) { clock.advance(100); vad.push(SPEECH); }
  assert.equal(vad.shouldSegment(), false);   // 44.1s < 45s since speech began
  clock.advance(1000);                        // speech is 45s old (started t=100)
  vad.push(SPEECH);
  assert.equal(vad.shouldSegment(), true);    // crossed the boundary mid-speech
  vad.markSegment();                          // caller rotated the recorder
  clock.advance(1000);
  vad.push(SPEECH);
  assert.equal(vad.shouldSegment(), false);   // re-armed for the next segment
  assert.equal(vad.shouldFinalize(), false);  // segmentation never ends the turn
});

test("without segmentMs shouldSegment never fires", () => {
  const clock = fakeClock();
  const vad = createVad({ now: clock.now });
  clock.advance(100);
  vad.push(SPEECH);
  clock.advance(60000);
  vad.push(SPEECH);
  assert.equal(vad.shouldSegment(), false);
});

test("silence finalize reports the silence reason", () => {
  const clock = fakeClock();
  const vad = createVad({ now: clock.now });
  clock.advance(100);
  vad.push(SPEECH);
  clock.advance(2000);
  assert.equal(vad.shouldFinalize(), true);
  assert.equal(vad.lastFinalizeReason(), "silence");
});

test("shouldFinalize latches: true exactly once until reset", () => {
  const clock = fakeClock();
  const vad = createVad({ now: clock.now });
  clock.advance(100);
  vad.push(SPEECH);
  clock.advance(2100);
  assert.equal(vad.shouldFinalize(), true);
  assert.equal(vad.shouldFinalize(), false);  // latched
  assert.equal(vad.hasSpeech(), false);
});

test("reset starts a fresh utterance and keeps the adapted floor", () => {
  const clock = fakeClock();
  const vad = createVad({ now: clock.now });
  for (let i = 0; i < 100; i++) { clock.advance(100); vad.push(0.01); }
  const learnedFloor = vad.floor();
  clock.advance(100);
  vad.push(SPEECH);
  clock.advance(2100);
  assert.equal(vad.shouldFinalize(), true);
  vad.reset();
  assert.equal(vad.hasSpeech(), false);
  assert.equal(vad.shouldFinalize(), false);
  assert.equal(vad.floor(), learnedFloor);  // kept: it learned the room
  // And the very next loud frame opens a new utterance.
  clock.advance(100);
  assert.equal(vad.push(SPEECH), true);
  assert.equal(vad.hasSpeech(), true);
});

test("speech must exceed the OPEN ratio, not just the close one", () => {
  const clock = fakeClock();
  const vad = createVad({ now: clock.now });
  clock.advance(100);
  // 0.004 * 1.4 = 0.0056 <= level < 0.004 * 2.5 = 0.01: hysteresis dead
  // band. It must NOT open the gate (that needs the open threshold) — it is
  // just a quiet frame that nudges the floor upward.
  assert.equal(vad.push(0.007), false);
  assert.equal(vad.hasSpeech(), false);
  assert.ok(vad.floor() > 0.004 && vad.floor() < 0.007, `floor drifted toward the quiet frame (${vad.floor()})`);
});
