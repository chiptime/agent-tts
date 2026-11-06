/* node --test suite for the streaming uplink client module (PRD-03 Task T1). */

const test = require("node:test");
const assert = require("node:assert");
const StreamUplink = require("../../src/herdr_brain/static/stream-uplink.js");

test("downsampleTo16kPCM16 converts Float32 audio to 16 kHz PCM16 mono bytes", () => {
  // 48 kHz sine-like pattern downsampled to 16 kHz (3:1 ratio)
  const input48k = new Float32Array(4800); // 100 ms at 48 kHz
  for (let i = 0; i < input48k.length; i++) {
    input48k[i] = Math.sin((2 * Math.PI * 440 * i) / 48000);
  }

  const pcm16 = StreamUplink.downsampleTo16kPCM16(input48k, 48000);
  assert(pcm16 instanceof Int16Array);
  // 100 ms at 16 kHz = 1600 samples
  assert.equal(pcm16.length, 1600);

  // Peak amplitude should be close to 32767
  let maxAbs = 0;
  for (let i = 0; i < pcm16.length; i++) {
    if (Math.abs(pcm16[i]) > maxAbs) maxAbs = Math.abs(pcm16[i]);
  }
  assert(maxAbs > 30000, `Expected amplitude > 30000, got ${maxAbs}`);
});

test("shouldBuffer reports backpressure only above the bufferedAmount bound (FR-03/A1)", () => {
  // While the socket is OPEN, ws.bufferedAmount is the backpressure signal.
  // At or below the default bound (~2s of PCM16 16kHz mono in flight): send.
  assert.equal(StreamUplink.shouldBuffer(0), false);
  assert.equal(StreamUplink.shouldBuffer(StreamUplink.DEFAULT_BUFFERED_LIMIT_BYTES), false);
  // Above the bound: route the frame through the bounded buffer instead.
  assert.equal(StreamUplink.shouldBuffer(StreamUplink.DEFAULT_BUFFERED_LIMIT_BYTES + 1), true);
  assert.equal(StreamUplink.shouldBuffer(16 * 1024 * 1024), true);
  // A custom bound moves the threshold; a missing reading never blocks sends.
  assert.equal(StreamUplink.shouldBuffer(2048, 1024), true);
  assert.equal(StreamUplink.shouldBuffer(1024, 1024), false);
  assert.equal(StreamUplink.shouldBuffer(undefined), false);
});

test("BoundedSendBuffer drops oldest frames when full and keeps the last ~2s", () => {
  // 20 frames of 100ms = 2s
  const buffer = new StreamUplink.BoundedSendBuffer({ maxFrames: 20 });
  assert.equal(buffer.length(), 0);

  // Push 25 frames
  for (let i = 1; i <= 25; i++) {
    buffer.push(new Uint8Array([i]));
  }

  assert.equal(buffer.length(), 20);
  // Oldest 5 frames (1..5) should have been dropped; remaining are 6..25
  const frames = buffer.flush();
  assert.equal(frames.length, 20);
  assert.equal(frames[0][0], 6);
  assert.equal(frames[19][0], 25);
  assert.equal(buffer.length(), 0);
});

// ---- T3 (PRD-04/S5): live transcript display tracker ----
//
// The pure client state behind the interim strip: finals accumulate with
// single spaces (FR-04 display contract), the latest partial supersedes
// them for rendering, reset clears the utterance, and hostile server text
// is bounded before it ever reaches the DOM.

test("LiveTranscriptTracker accumulates finals with single spaces", () => {
  const tracker = new StreamUplink.LiveTranscriptTracker();
  assert.equal(tracker.onFinal("hola"), "hola");
  assert.equal(tracker.onFinal("que tal"), "hola que tal");
  // Whitespace-only finals never append.
  assert.equal(tracker.onFinal("   "), "hola que tal");
});

test("LiveTranscriptTracker renders the latest partial over accumulated finals", () => {
  const tracker = new StreamUplink.LiveTranscriptTracker();
  tracker.onFinal("hola");
  assert.equal(tracker.onPartial("hola que"), "hola que");
  // A later final clears the superseded partial and appends.
  assert.equal(tracker.onFinal("que"), "hola que");
  assert.equal(tracker.onPartial("hola que tal"), "hola que tal");
});

test("LiveTranscriptTracker reset clears the utterance for the next one", () => {
  const tracker = new StreamUplink.LiveTranscriptTracker();
  tracker.onPartial("turno uno");
  tracker.onFinal("turno");
  assert.equal(tracker.reset(), "");
  assert.equal(tracker.text(), "");
  // The next utterance starts from scratch — no prefix overlap.
  assert.equal(tracker.onPartial("turno dos"), "turno dos");
});

test("LiveTranscriptTracker bounds hostile server text", () => {
  const tracker = new StreamUplink.LiveTranscriptTracker();
  // A multi-megabyte partial renders truncated, never unbounded DOM text.
  const huge = "x".repeat(100000);
  const shown = tracker.onPartial(huge);
  assert.ok(shown.length <= StreamUplink.LIVE_TEXT_LIMIT + 1);
  // Non-string payloads degrade to safe empties instead of throwing.
  assert.equal(tracker.onPartial(null), "");
  assert.equal(tracker.onFinal(undefined), "");
  // Control characters never reach the rendered text.
  const dirty = tracker.onPartial("hola\x00\nmundo");
  assert.ok(dirty.indexOf("\x00") === -1 && dirty.indexOf("\n") === -1);
});

// ---- S7 (PRD-05/D10, T4): rejoin text accumulation ----------------------
//
// Speech resuming after the turn closed but BEFORE the answer starts
// rejoins the SAME turn: the closed segment's dispatched text is the
// base, and the resumed transcript merges into the WHOLE utterance,
// dispatched exactly once. A transcript arriving while the resumed
// speech is still recording is held as segment one (never dispatched
// alone), and an empty resumed transcript still owes the base — the
// cancelled ask must never lose the user's words.

test("LiveRejoinText base records the dispatched segment for a later resume", () => {
  const rejoin = new StreamUplink.LiveRejoinText();
  // No resume yet: the transcript dispatches now; a later resume joins onto it.
  assert.equal(rejoin.base("hola buenos"), "hola buenos");
  assert.equal(rejoin.whole, "hola buenos");
  assert.equal(rejoin.segmentOne, null);
});

test("LiveRejoinText holds a transcript that lands mid-recording (segment one)", () => {
  const rejoin = new StreamUplink.LiveRejoinText();
  // The first segment's transcript arrives while the resumed speech is
  // still being recorded: it is held, never dispatched by itself.
  assert.equal(rejoin.holdSegmentOne("hola buenos"), null);
  assert.equal(rejoin.segmentOne, "hola buenos");
  // The resumed transcript closes the turn: the WHOLE utterance, once.
  assert.equal(rejoin.rejoin("días a todos"), "hola buenos días a todos");
  assert.equal(rejoin.segmentOne, null);
  assert.equal(rejoin.whole, "hola buenos días a todos");
});

test("LiveRejoinText rejoin merges onto an already-dispatched base", () => {
  const rejoin = new StreamUplink.LiveRejoinText();
  rejoin.base("hola buenos");
  // Resume detected while the ask for the base is still in flight.
  assert.equal(rejoin.rejoin("días"), "hola buenos días");
  // A second resume chains onto the accumulated whole, not just the tail.
  assert.equal(rejoin.rejoin("a todos"), "hola buenos días a todos");
});

test("LiveRejoinText empty resumed transcript still owes the base", () => {
  const rejoin = new StreamUplink.LiveRejoinText();
  rejoin.base("hola buenos");
  // The resume was noise (empty transcript): the cancelled ask's words
  // are re-dispatched alone — never silently dropped.
  assert.equal(rejoin.rejoin(""), "hola buenos");
  const held = new StreamUplink.LiveRejoinText();
  held.holdSegmentOne("hola buenos");
  assert.equal(held.rejoin(""), "hola buenos");
  // Nothing ever said anything: the empty turn fails honestly downstream.
  const empty = new StreamUplink.LiveRejoinText();
  assert.equal(empty.rejoin(""), "");
});

test("LiveRejoinText tolerates non-string and padded inputs", () => {
  const rejoin = new StreamUplink.LiveRejoinText();
  rejoin.base(null);
  assert.equal(rejoin.whole, "");
  rejoin.base("  hola  ");
  assert.equal(rejoin.rejoin(undefined), "hola");
  const hostile = new StreamUplink.LiveRejoinText();
  assert.doesNotThrow(() => hostile.holdSegmentOne(null));
  assert.equal(hostile.rejoin(null), "");
});

// ---- L53: bounded live client diagnostics (numbers/opaque tokens only) ----
//
// The 2 s diag is the only device-side evidence the journal gets. It must
// carry the AudioContext state, analyser peak, frame counters, capture
// mode, fallback reason and served build — bounded and sanitized so a
// hostile page cannot inject lines or grow the log.

test("buildDiag carries ctx state, build, reason and frame counters", () => {
  const diag = StreamUplink.buildDiag({
    mode: "worklet",
    posted: 42,
    sent: 40,
    queued: 2,
    maxrms: 0.125,
    ctx: "running",
    build: "2efa6dc-s2bd2523f",
    reason: "worklet_no_frames",
    state: "listening",
  });
  assert.equal(diag.type, "diag");
  assert.equal(diag.mode, "worklet");
  assert.equal(diag.posted, 42);
  assert.equal(diag.sent, 40);
  assert.equal(diag.queued, 2);
  assert.equal(diag.maxrms, 0.125);
  assert.equal(diag.ctx, "running");
  assert.equal(diag.build, "2efa6dc-s2bd2523f");
  assert.equal(diag.reason, "worklet_no_frames");
  assert.equal(diag.state, "listening");
});

test("buildDiag bounds hostile and missing values", () => {
  const diag = StreamUplink.buildDiag({
    mode: "m".repeat(200),
    build: "b".repeat(200),
    ctx: "c".repeat(200),
    reason: "r".repeat(200),
    state: "s".repeat(200),
    posted: "not-a-number",
    sent: null,
    queued: true,
    maxrms: "loud",
  });
  assert.equal(diag.mode.length, 12);
  assert.equal(diag.build.length, 24);
  assert.equal(diag.ctx.length, 12);
  assert.equal(diag.reason.length, 24);
  assert.equal(diag.state.length, 14);
  assert.equal(diag.posted, -1);
  assert.equal(diag.sent, -1);
  assert.equal(diag.queued, -1);
  assert.equal(diag.maxrms, -1);
  // Control characters and newlines never reach the journal line.
  const dirty = StreamUplink.buildDiag({ mode: "work\nlet\x00", ctx: "run\rning" });
  assert.ok(dirty.mode.indexOf("\n") === -1 && dirty.mode.indexOf("\x00") === -1);
  assert.ok(dirty.ctx.indexOf("\r") === -1);
});
