/* node --test suite for the pure VAD module (server voice engine v2). */

const test = require("node:test");
const assert = require("node:assert");
const { createVad } = require("../../src/herdr_brain/static/vad.js");

function fakeClock(start = 0) {
  let t = start;
  return {
    now: () => t,
    advance: ms => { t += ms; }
  };
}

const QUIET = 0.002;   // typical noise-room RMS
const SPEECH = 0.08;   // typical speech RMS

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

test("utterance finalizes after 1200ms below the close threshold", () => {
  const clock = fakeClock();
  const vad = createVad({ now: clock.now });
  clock.advance(100);
  vad.push(SPEECH);
  clock.advance(1199);
  assert.equal(vad.shouldFinalize(), false);  // not yet
  clock.advance(1);                           // 1200ms sharp
  assert.equal(vad.shouldFinalize(), true);
});

test("15s hard cap finalizes even with continuous speech", () => {
  const clock = fakeClock();
  const vad = createVad({ now: clock.now });
  clock.advance(100);
  vad.push(SPEECH);
  for (let ms = 0; ms < 15000; ms += 100) {
    clock.advance(100);
    vad.push(SPEECH);  // never silent: the cap must end it
  }
  assert.equal(vad.shouldFinalize(), true);
});

test("shouldFinalize latches: true exactly once until reset", () => {
  const clock = fakeClock();
  const vad = createVad({ now: clock.now });
  clock.advance(100);
  vad.push(SPEECH);
  clock.advance(1300);
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
  clock.advance(1300);
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
