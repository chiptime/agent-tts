/* node --test suite for the pure endpointing module. */

const test = require("node:test");
const assert = require("node:assert");
const { createEndpointer } = require("../../src/herdr_brain/static/endpointing.js");

function fakeClock(start = 0) {
  let t = start;
  return {
    now: () => t,
    advance: ms => { t += ms; }
  };
}

test("no speech: shouldFinalize stays false no matter the elapsed time", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now });
  clock.advance(60_000);
  assert.equal(ep.shouldFinalize(), false);
});

test("interim accumulation keeps the latest transcript", () => {
  const ep = createEndpointer();
  assert.equal(ep.push("hola"), true);
  assert.equal(ep.push("hola que"), true);
  assert.equal(ep.push("hola que"), false);  // unchanged: not new activity
  assert.equal(ep.hasSpeech(), true);
  assert.equal(ep.finalize(), "hola que");
});

test("silence threshold finalizes after no interim change", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now, silenceMs: 1200 });
  ep.push("dime el estado");
  clock.advance(1199);
  assert.equal(ep.shouldFinalize(), false);
  clock.advance(1);
  assert.equal(ep.shouldFinalize(), true);
});

test("new interim activity resets the silence timer", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now, silenceMs: 1200 });
  ep.push("primera parte");
  clock.advance(1100);
  ep.push("primera parte y segunda");  // activity: silence timer restarts
  clock.advance(1100);
  assert.equal(ep.shouldFinalize(), false);  // only 1100ms of silence
  clock.advance(100);
  assert.equal(ep.shouldFinalize(), true);
});

test("hard cap finalizes even with continuous speech", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now, hardCapMs: 15000, silenceMs: 1200 });
  ep.push("palabra");
  for (let t = 1000; t <= 14_000; t += 1000) {
    clock.advance(1000);
    ep.push("palabra " + t);  // constant activity keeps silencing
    assert.equal(ep.shouldFinalize(), false, `at +${t}ms should not finalize`);
  }
  clock.advance(15_000 - 14_000);
  ep.push("palabra extra");
  clock.advance(1000);  // 15s since speech began; silence only 1s
  assert.equal(ep.shouldFinalize(), true);
});

test("finalize returns the text and resets the utterance", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now });
  ep.push("una frase");
  clock.advance(5000);
  assert.equal(ep.finalize(), "una frase");
  assert.equal(ep.hasSpeech(), false);
  assert.equal(ep.shouldFinalize(), false);
  // After reset, silence must not trigger without new speech.
  clock.advance(60_000);
  assert.equal(ep.shouldFinalize(), false);
});

test("whitespace-only interims never count as speech", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now });
  assert.equal(ep.push("   "), false);
  assert.equal(ep.hasSpeech(), false);
  clock.advance(60_000);
  assert.equal(ep.shouldFinalize(), false);
});

test("leading silence before speech does not trip the hard cap", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now, hardCapMs: 15000 });
  clock.advance(60_000);  // user says nothing for a minute
  ep.push("ahora hablo");
  clock.advance(1000);
  assert.equal(ep.shouldFinalize(), false);  // cap counts from speech start
});
