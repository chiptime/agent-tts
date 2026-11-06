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
