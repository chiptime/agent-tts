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

test("snapshot exposes the stable diagnostic shape", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now, silenceMs: 1200, hardCapMs: 15000 });
  const idle = ep.snapshot();
  assert.deepEqual(Object.keys(idle).sort(), [
    "buffer", "bufferChars", "capRemainingMs", "committedChars", "hardCapMs",
    "hasSpeech", "interimChars", "lastChangeAt", "shouldFinalize",
    "silenceMs", "silenceRemainingMs", "speechStartedAt"
  ]);
  assert.equal(idle.bufferChars, 0);
  assert.equal(idle.committedChars, 0);
  assert.equal(idle.interimChars, 0);
  assert.equal(idle.hasSpeech, false);
  assert.equal(idle.silenceRemainingMs, null);  // no speech: nothing to count
  assert.equal(idle.capRemainingMs, null);

  ep.push("hola");
  clock.advance(400);
  const active = ep.snapshot();
  assert.equal(active.buffer, "hola");
  assert.equal(active.bufferChars, 4);
  assert.equal(active.committedChars, 0);   // nothing finalized yet
  assert.equal(active.interimChars, 4);     // it is all live interim
  assert.equal(active.hasSpeech, true);
  assert.equal(active.silenceRemainingMs, 800);
  assert.equal(active.capRemainingMs, 14600);
  assert.equal(active.shouldFinalize, false);
});

test("snapshot: unchanged interim does not reset the silence countdown", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now, silenceMs: 1200 });
  ep.push("hola");
  clock.advance(500);
  ep.push("hola");  // same text: NOT new activity
  clock.advance(500);
  let snap = ep.snapshot();
  assert.equal(snap.silenceRemainingMs, 200);  // 1000ms of real silence elapsed
  clock.advance(200);
  snap = ep.snapshot();
  assert.equal(snap.silenceRemainingMs, 0);
  assert.equal(snap.shouldFinalize, true);
});

test("snapshot: cap remaining never goes negative", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now, hardCapMs: 15000 });
  ep.push("hablando sin parar");
  clock.advance(99_000);
  const snap = ep.snapshot();
  assert.equal(snap.capRemainingMs, 0);
  assert.equal(snap.shouldFinalize, true);
});

/* ---- session-boundary accumulation (the Fase 2g fix) ----
 * Chrome ends recognition sessions every few seconds; the app restarts
 * them. Finals flushed at session end COMMIT text that must survive the
 * restart; a fresh session's short/empty interim must never wipe it. */

test("utterance accumulates across recognition session boundaries", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now });
  // Session 1: interims, then Chrome flushes a final and ends the session.
  ep.push("el rebaño");
  ep.commit("el rebaño");
  // Session 2 starts: short/empty interims arrive while the mic restarts.
  assert.equal(ep.push(""), false);       // empty new-session interim: ignored
  assert.equal(ep.push("está"), true);    // fresh session interim
  assert.equal(ep.text(), "el rebaño está");
  assert.equal(ep.hasSpeech(), true);
  // And the combined utterance survives to the silence dispatch.
  clock.advance(1200);
  assert.equal(ep.shouldFinalize(), true);
  assert.equal(ep.finalize(), "el rebaño está");
});

test("commit appends with a single space and clears the interim", () => {
  const ep = createEndpointer();
  ep.push("ho");
  ep.commit("hola");
  ep.push("qué");
  ep.commit("qué tal");
  assert.equal(ep.text(), "hola qué tal");  // interim never duplicated
  assert.equal(ep.finalize(), "hola qué tal");
});

test("empty interim never wipes captured text", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now });
  ep.push("hola que tal");
  assert.equal(ep.push(""), false);
  assert.equal(ep.push("   "), false);
  assert.equal(ep.hasSpeech(), true);
  assert.equal(ep.finalize(), "hola que tal");
});

test("push after commit never shortens committed", () => {
  const ep = createEndpointer();
  ep.commit("primera parte larga");
  ep.push("segunda");         // shorter than committed: fine, appended
  assert.equal(ep.text(), "primera parte larga segunda");
  ep.push("s");               // revision of the interim only
  assert.equal(ep.text(), "primera parte larga s");
});

test("commit counts as combined-text change and resets the silence timer", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now, silenceMs: 1200 });
  ep.push("hola");
  clock.advance(1100);
  ep.commit("hola que");      // change at t=1100
  clock.advance(1100);
  assert.equal(ep.shouldFinalize(), false);  // only 1100ms since the commit
  clock.advance(100);
  assert.equal(ep.shouldFinalize(), true);
});

test("ignored empty push does not extend the silence clock", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now, silenceMs: 1200 });
  ep.push("hola");
  clock.advance(600);
  ep.push("");                // session restart hiccup: ignored, NOT activity
  clock.advance(600);
  assert.equal(ep.shouldFinalize(), true);  // 1200ms since the real change
});

test("hard cap spans sessions: measured from first captured text", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now, hardCapMs: 15000, silenceMs: 1200 });
  ep.commit("parte uno");     // session 1 final at t=0
  for (let t = 3000; t <= 12000; t += 3000) {
    clock.advance(3000);
    ep.commit("parte " + t);  // a final per session, no silence gap
  }
  clock.advance(2500);
  assert.equal(ep.shouldFinalize(), true);  // 14.5s+ since speech began
});

test("finalize returns the combined text and resets everything", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now });
  ep.commit("ya está");
  ep.push("listo");
  assert.equal(ep.finalize(), "ya está listo");
  assert.equal(ep.hasSpeech(), false);
  assert.equal(ep.finalize(), "");           // nothing left
  const snap = ep.snapshot();
  assert.equal(snap.committedChars, 0);
  assert.equal(snap.interimChars, 0);
  // Fresh utterance on the same instance works immediately.
  ep.push("otra cosa");
  assert.equal(ep.hasSpeech(), true);
});

test("snapshot splits committed vs interim after a session boundary", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now });
  ep.commit("el rebaño");
  ep.push("está");
  const snap = ep.snapshot();
  assert.equal(snap.committedChars, "el rebaño".length);
  assert.equal(snap.interimChars, "está".length);
  assert.equal(snap.bufferChars, "el rebaño está".length);
  assert.equal(snap.hasSpeech, true);
});

/* ---- growing-final dedup (the Chrome restart soup fix) ----
 * Chrome Android re-emits the whole utterance as a growing final after
 * each mid-utterance session restart. Blind appends turned one spoken
 * sentence into "cuantas cuantas sesiones cuantas sesiones tengo…". */

test("growing finals across session restarts merge instead of duplicating", () => {
  const ep = createEndpointer();
  ep.commit("cuantas");
  ep.commit("cuantas sesiones");
  ep.commit("cuantas sesiones tengo");
  ep.commit("cuantas sesiones tengo levantadas");
  assert.equal(ep.finalize(), "cuantas sesiones tengo levantadas");
});

test("partial overlap appends only the new tail", () => {
  const ep = createEndpointer();
  ep.commit("el rebaño está");
  ep.commit("está funcionando muy mal");
  assert.equal(ep.finalize(), "el rebaño está funcionando muy mal");
});

test("growing finals merge regardless of a carried interim", () => {
  const ep = createEndpointer();
  ep.commit("cuantas");
  ep.push("cuantas sesiones");  // interim snapshot carried across restart
  ep.commit("cuantas sesiones tengo");
  assert.equal(ep.finalize(), "cuantas sesiones tengo");
});

test("overlap match ignores case and punctuation drift", () => {
  const ep = createEndpointer();
  ep.commit("La escucha,");
  ep.commit("la escucha está mal!");
  // The final's own rendering wins for the overlapping region — the point
  // is that the drift never defeats the match and nothing duplicates.
  assert.equal(ep.finalize(), "la escucha está mal!");
});

test("disjoint finals still append without merging", () => {
  const ep = createEndpointer();
  ep.commit("primera parte");
  ep.commit("segunda parte");
  assert.equal(ep.finalize(), "primera parte segunda parte");
});

test("genuine repetition inside one final survives the merge", () => {
  const ep = createEndpointer();
  ep.commit("hola hola");
  ep.commit("hola hola cómo estás");
  assert.equal(ep.finalize(), "hola hola cómo estás");
});

/* ---- accent-drift tolerance (the diacritic folding fix) ----
 * Chrome re-emissions also drift in accents ("qué" -> "que"); without
 * folding the overlap match missed and the text DOUBLED inside one
 * bubble. Folding is comparison-only: the committed rendering survives
 * when the pair drifted only in accents. */

test("accent-drift re-emission without a tail changes nothing", () => {
  const ep = createEndpointer();
  ep.commit("qué hora es");
  ep.commit("que hora es");
  assert.equal(ep.finalize(), "qué hora es");  // raw base wins in the overlap
});

test("accent-drift re-emission with a tail appends only the tail", () => {
  const ep = createEndpointer();
  ep.commit("dime qué");
  ep.commit("dime que hora es");
  assert.equal(ep.finalize(), "dime qué hora es");  // committed "qué" stays
});

test("accent folding does not merge genuinely different words", () => {
  const ep = createEndpointer();
  ep.commit("qué difícil");
  ep.commit("que fácil");
  assert.equal(ep.finalize(), "qué difícil que fácil");  // no shared prefix
});

test("ñ drift folds like any other accent", () => {
  const ep = createEndpointer();
  ep.commit("feliz año");
  ep.commit("feliz ano nuevo");  // NFD decomposes ñ -> n + combining tilde
  assert.equal(ep.finalize(), "feliz año nuevo");
});

/* ---- post-dispatch re-emission dedupe (the duplicate bubble fix) ----
 * After finalize() dispatches, the endpointer is empty — but Chrome
 * Android's fresh session re-emits the whole utterance as a final, and
 * committing it again fired a second identical dispatch. commit()
 * swallows a final whose folded words match the dispatched signature
 * inside duplicateWindowMs (default 5s). */

test("re-emitted final inside the window is swallowed whole", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now });
  ep.commit("abre spotify");
  assert.equal(ep.finalize(), "abre spotify");  // dispatched; buffer empty
  clock.advance(1000);
  assert.equal(ep.commit("abre spotify"), false);  // echo: no mutation
  assert.equal(ep.text(), "");
  assert.equal(ep.hasSpeech(), false);
});

test("same final past the window is kept as new speech", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now });
  ep.commit("abre spotify");
  ep.finalize();
  clock.advance(6000);  // beyond the 5s window: stale memory is harmless
  assert.equal(ep.commit("abre spotify"), true);
  assert.equal(ep.text(), "abre spotify");
});

test("longer final inside the window is new speech, not an echo", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now });
  ep.commit("abre spotify");
  ep.finalize();
  clock.advance(1000);
  assert.equal(ep.commit("abre spotify otra vez"), true);  // signature differs
  assert.equal(ep.text(), "abre spotify otra vez");
});

test("re-emission dedupe folds accents like the overlap match", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now });
  ep.push("qué tal");
  assert.equal(ep.finalize(), "qué tal");
  clock.advance(1000);
  assert.equal(ep.commit("que tal"), false);  // folded signature still matches
  assert.equal(ep.text(), "");
});

/* ---- T3: overlapping interim merged into committed (one-turn dedupe) ----
 * Chrome restarts sessions mid-utterance and the fresh session's interim
 * re-states the committed text before growing past it; a blind append of
 * committed + interim put a duplicated prefix inside ONE bubble. The
 * combined view now goes through the same overlap merge the finals path
 * uses. */

test("committed prefix plus overlapping interim yields a single phrase", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now, silenceMs: 1200 });
  ep.commit("cuántas sesiones");
  ep.push("cuántas sesiones tengo");
  assert.equal(ep.text(), "cuántas sesiones tengo");  // no duplicated prefix
  const snap = ep.snapshot();
  assert.equal(snap.buffer, "cuántas sesiones tengo");
  assert.equal(snap.hasSpeech, true);
  assert.equal(snap.interimChars, "cuántas sesiones tengo".length);  // live raw interim
  clock.advance(1200);
  assert.equal(ep.shouldFinalize(), true);
  assert.equal(ep.finalize(), "cuántas sesiones tengo");
});

test("partial overlap with committed appends only the new interim tail", () => {
  const ep = createEndpointer();
  ep.commit("el rebaño está");
  ep.push("está funcionando mal");
  assert.equal(ep.text(), "el rebaño está funcionando mal");
  assert.equal(ep.finalize(), "el rebaño está funcionando mal");
});

test("interim re-stating the whole committed text does not duplicate it", () => {
  const ep = createEndpointer();
  ep.commit("qué tal");
  ep.push("qué tal");
  assert.equal(ep.text(), "qué tal");
  assert.equal(ep.hasSpeech(), true);
  assert.equal(ep.finalize(), "qué tal");
});

test("interim overlap match ignores accent drift and keeps the committed word", () => {
  const ep = createEndpointer();
  ep.commit("dime qué");
  ep.push("que hora es");          // accent-drifted re-statement plus tail
  assert.equal(ep.text(), "dime qué hora es");
});

test("interim overlap match ignores case and punctuation drift", () => {
  const ep = createEndpointer();
  ep.commit("La escucha");
  ep.push("la escucha está mal!");  // interim rendering wins case/punct drift
  assert.equal(ep.text(), "la escucha está mal!");
});

test("genuine repetition inside one transcript survives the interim merge", () => {
  const ep = createEndpointer();
  ep.commit("hola hola");
  ep.push("hola hola cómo estás");
  assert.equal(ep.text(), "hola hola cómo estás");
});

test("disjoint interim still appends without merging", () => {
  const ep = createEndpointer();
  ep.commit("primera parte");
  ep.push("segunda parte");
  assert.equal(ep.text(), "primera parte segunda parte");
  assert.equal(ep.finalize(), "primera parte segunda parte");
});

/* ---- T3: post-dispatch interim echo does not revive the turn ----
 * After finalize() dispatches, Chrome's fresh recognition session can
 * re-emit the utterance, growing ("hola" -> "hola mundo"). Only the
 * FULL folded signature is rejected (same guard as commit); shorter
 * speech that shares a prefix is legitimate and must stay accepted.
 * When a full echo identifies the utterance, a buffered earlier
 * growing prefix of it is dropped so silence cannot dispatch the
 * partial ghost. */

test("matching interim echo inside the window is rejected without reviving speech", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now, silenceMs: 1200 });
  ep.push("hola mundo");
  assert.equal(ep.finalize(), "hola mundo");  // dispatched; buffer empty
  clock.advance(1000);
  assert.equal(ep.push("hola mundo"), false);  // full echo: no new activity
  assert.equal(ep.text(), "");
  assert.equal(ep.hasSpeech(), false);
  assert.equal(ep.shouldFinalize(), false);    // no second turn can fire
  assert.equal(ep.finalize(), "");
});

test("shorter command sharing a prefix of the dispatched utterance is accepted", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now, silenceMs: 1200 });
  ep.push("hola mundo");
  ep.finalize();
  clock.advance(1000);
  assert.equal(ep.push("hola"), true);      // genuine shorter speech, NOT echo
  assert.equal(ep.text(), "hola");
  assert.equal(ep.hasSpeech(), true);
  clock.advance(1200);
  assert.equal(ep.shouldFinalize(), true);
  assert.equal(ep.finalize(), "hola");
});

test("shorter command sharing a longer dispatched prefix is accepted", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now });
  ep.push("abre spotify y pon rock");
  ep.finalize();
  clock.advance(1000);
  assert.equal(ep.push("abre spotify"), true);  // real command, shared prefix
  assert.equal(ep.text(), "abre spotify");
  assert.equal(ep.finalize(), "abre spotify");
});

test("growing echo prefixes are accepted until the full echo identifies them", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now, silenceMs: 1200 });
  ep.commit("cuántas sesiones tengo");
  assert.equal(ep.finalize(), "cuántas sesiones tengo");
  clock.advance(1000);
  assert.equal(ep.push("cuántas"), true);               // shorter speech: accepted
  assert.equal(ep.push("cuántas sesiones"), true);      // still shorter: accepted
  assert.equal(ep.text(), "cuántas sesiones");          // buffered while growing
  assert.equal(ep.push("cuántas sesiones tengo"), false);  // FULL echo: rejected
  assert.equal(ep.text(), "");                          // stale prefix cleaned
  assert.equal(ep.hasSpeech(), false);
  clock.advance(1200);
  assert.equal(ep.shouldFinalize(), false);             // no ghost dispatch
  const snap = ep.snapshot();
  assert.equal(snap.buffer, "");
  assert.equal(snap.silenceRemainingMs, null);  // utterance effectively empty
  assert.equal(ep.finalize(), "");
});

test("growing echo prefixes are cleaned when the full echo arrives as a final", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now });
  ep.push("abre spotify y pon rock");
  assert.equal(ep.finalize(), "abre spotify y pon rock");
  clock.advance(1000);
  assert.equal(ep.push("abre"), true);                      // growing echo prefix
  assert.equal(ep.commit("abre spotify y pon rock"), false);  // FULL echo final
  assert.equal(ep.text(), "");                              // no ghost interim left
  assert.equal(ep.hasSpeech(), false);
});

test("echo cleanup with no committed speech clears ghost utterance timing", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now, silenceMs: 1200, hardCapMs: 5000 });
  ep.push("hola mundo");
  ep.finalize();                                  // dispatched at t=0
  clock.advance(4000);
  assert.equal(ep.push("hola"), true);            // growing echo prefix t=4000
  clock.advance(500);
  assert.equal(ep.push("hola mundo"), false);     // full echo t=4500: cleanup
  const snap = ep.snapshot();
  assert.equal(snap.hasSpeech, false);
  assert.equal(snap.speechStartedAt, null);       // ghost timing reset, not 4000
  assert.equal(snap.lastChangeAt, null);
  assert.equal(snap.silenceRemainingMs, null);    // no-speech shape, fully idle
  assert.equal(snap.capRemainingMs, null);
  assert.equal(ep.push("hola mundo"), false);     // dispatch memory still alive
});

test("new speech after echo cleanup gets its own hard cap, not the ghost's", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now, silenceMs: 1200, hardCapMs: 5000 });
  ep.push("hola mundo");
  ep.finalize();                                  // t=0
  clock.advance(4000);
  ep.push("hola");                                // echo prefix t=4000
  clock.advance(500);
  ep.push("hola mundo");                          // full echo t=4500: cleanup
  clock.advance(100);
  assert.equal(ep.push("una"), true);             // genuine new speech t=4600
  const active = ep.snapshot();
  assert.equal(active.speechStartedAt, 4600);     // its OWN clock, not 4000
  assert.equal(active.capRemainingMs, 5000);
  // Keep the phrase alive (<1200ms gaps) so only the hard cap can fire.
  clock.advance(1000); ep.push("una dos");
  clock.advance(1000); ep.push("una dos tres");
  clock.advance(1000); ep.push("una dos tres cuatro");
  clock.advance(1000); ep.push("una dos tres cuatro cinco");  // t=8600
  clock.advance(399);
  assert.equal(ep.shouldFinalize(), false);       // t=8999: speech 4399ms old
  clock.advance(1);
  assert.equal(ep.shouldFinalize(), false);       // t=9000: ghost cap must NOT fire
  clock.advance(600);
  assert.equal(ep.shouldFinalize(), true);        // t=9600: its own 5000ms cap
  assert.equal(ep.finalize(), "una dos tres cuatro cinco");
});

test("echo cleanup with committed speech preserves its utterance timing", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now, silenceMs: 1200 });
  ep.push("hola mundo");
  ep.finalize();                                  // t=0
  clock.advance(1000);
  assert.equal(ep.commit("dime el estado"), true);  // committed speech t=1000
  clock.advance(600);
  assert.equal(ep.push("hola"), true);            // echo growth interim t=1600
  clock.advance(100);
  assert.equal(ep.push("hola mundo"), false);     // full echo t=1700: interim
  assert.equal(ep.text(), "dime el estado");      // cleared, committed kept
  const snap = ep.snapshot();
  assert.equal(snap.hasSpeech, true);
  assert.equal(snap.speechStartedAt, 1000);       // committed's own start kept
  assert.equal(snap.lastChangeAt, 1600);          // timing not reset by the echo
  clock.advance(1100);                            // 1200ms since t=1600
  assert.equal(ep.shouldFinalize(), true);
  assert.equal(ep.finalize(), "dime el estado");
});

test("interim echo guard folds accents like the final guard", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now });
  ep.push("qué tal");
  assert.equal(ep.finalize(), "qué tal");
  clock.advance(1000);
  assert.equal(ep.push("que tal"), false);  // folded signature still matches
  assert.equal(ep.hasSpeech(), false);
});

test("unrelated interim inside the window is accepted as new speech", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now, silenceMs: 1200 });
  ep.push("hola mundo");
  ep.finalize();
  clock.advance(1000);
  assert.equal(ep.push("otra cosa"), true);  // different words: not an echo
  assert.equal(ep.text(), "otra cosa");
  assert.equal(ep.hasSpeech(), true);
  clock.advance(1200);
  assert.equal(ep.shouldFinalize(), true);
  assert.equal(ep.finalize(), "otra cosa");
});

test("full interim echo exactly at window expiry is accepted as new speech", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now, duplicateWindowMs: 5000 });
  ep.push("hola mundo");
  ep.finalize();                              // dispatched at t=0
  clock.advance(4999);
  assert.equal(ep.push("hola mundo"), false);  // still inside: full echo
  clock.advance(1);                            // exactly 5000ms: window expired
  assert.equal(ep.push("hola mundo"), true);
  assert.equal(ep.text(), "hola mundo");
  assert.equal(ep.hasSpeech(), true);
});

test("longer interim inside the window is new speech, not an echo", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now });
  ep.push("abre spotify");
  ep.finalize();
  clock.advance(1000);
  assert.equal(ep.push("abre spotify otra vez"), true);  // extends the signature
  assert.equal(ep.text(), "abre spotify otra vez");
});

test("full echo preserves unrelated buffered speech and its timing", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now, silenceMs: 1200 });
  ep.push("hola mundo");
  ep.finalize();
  clock.advance(1000);
  assert.equal(ep.commit("dime el estado"), true);  // new speech at t=1000
  clock.advance(600);
  assert.equal(ep.push("otra cosa"), true);         // unrelated interim t=1600
  clock.advance(100);
  assert.equal(ep.push("hola mundo"), false);       // full echo at t=1700
  assert.equal(ep.text(), "dime el estado otra cosa");  // nothing erased
  clock.advance(1100);                              // 1200ms since t=1600
  assert.equal(ep.shouldFinalize(), true);          // timing was not reset
  assert.equal(ep.finalize(), "dime el estado otra cosa");
});

test("speech that starts like the echo but diverges replaces the prefix", () => {
  const clock = fakeClock();
  const ep = createEndpointer({ now: clock.now });
  ep.push("hola mundo");
  ep.finalize();
  clock.advance(1000);
  assert.equal(ep.push("hola"), true);          // shorter speech: accepted
  assert.equal(ep.push("hola planeta"), true);  // diverges: buffered whole
  assert.equal(ep.text(), "hola planeta");
  assert.equal(ep.finalize(), "hola planeta");
});
