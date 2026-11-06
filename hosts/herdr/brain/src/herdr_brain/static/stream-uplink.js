/* Streaming audio uplink helpers: downsampling and bounded send buffer (PRD-03).
 *
 * Runs in the main thread (and unit tested under node --test).
 * Conforms to ES5/ES6 browser compatibility patterns of herdr-brain static scripts.
 */
(function (global) {
  "use strict";

  var TARGET_SAMPLE_RATE = 16000;
  var DEFAULT_MAX_FRAMES = 20; // ~2 seconds of 100ms frames (FR-03)
  // ~2 s of PCM16 16 kHz mono already handed to the socket (32 KiB/s):
  // beyond this the socket is a slow consumer and frames must queue in
  // the bounded buffer instead of growing ws.bufferedAmount (FR-03/A1).
  var DEFAULT_BUFFERED_LIMIT_BYTES = 65536;

  /* Backpressure decision for an OPEN socket (FR-03/A1): true means the
   * frame must route through the bounded send buffer (drop oldest, keep
   * the last ~2 s) rather than another ws.send. A missing reading (0 or
   * undefined) never blocks sends. */
  function shouldBuffer(bufferedAmount, limitBytes) {
    var limit = typeof limitBytes === "number" ? limitBytes : DEFAULT_BUFFERED_LIMIT_BYTES;
    return (bufferedAmount || 0) > limit;
  }

  function downsampleTo16kPCM16(inputFloat32, sourceSampleRate) {
    if (!inputFloat32 || !inputFloat32.length) return new Int16Array(0);
    var srcRate = sourceSampleRate || 48000;
    if (srcRate === TARGET_SAMPLE_RATE) {
      var direct = new Int16Array(inputFloat32.length);
      for (var d = 0; d < inputFloat32.length; d++) {
        var s = Math.max(-1.0, Math.min(1.0, inputFloat32[d]));
        direct[d] = s < 0 ? Math.floor(s * 32768) : Math.floor(s * 32767);
      }
      return direct;
    }

    var ratio = srcRate / TARGET_SAMPLE_RATE;
    var outLength = Math.floor(inputFloat32.length / ratio);
    var output = new Int16Array(outLength);

    for (var i = 0; i < outLength; i++) {
      var srcPos = i * ratio;
      var idx = Math.floor(srcPos);
      var frac = srcPos - idx;
      var s0 = inputFloat32[idx];
      var s1 = (idx + 1 < inputFloat32.length) ? inputFloat32[idx + 1] : s0;
      var val = s0 + frac * (s1 - s0);
      var clamped = Math.max(-1.0, Math.min(1.0, val));
      output[i] = clamped < 0 ? Math.floor(clamped * 32768) : Math.floor(clamped * 32767);
    }
    return output;
  }

  function BoundedSendBuffer(options) {
    var opts = options || {};
    this.maxFrames = opts.maxFrames || DEFAULT_MAX_FRAMES;
    this._queue = [];
  }

  BoundedSendBuffer.prototype.push = function (frame) {
    this._queue.push(frame);
    while (this._queue.length > this.maxFrames) {
      this._queue.shift(); // Drop oldest frame (FR-03: retains last ~2s)
    }
  };

  BoundedSendBuffer.prototype.flush = function () {
    var items = this._queue;
    this._queue = [];
    return items;
  };

  BoundedSendBuffer.prototype.length = function () {
    return this._queue.length;
  };

  BoundedSendBuffer.prototype.clear = function () {
    this._queue = [];
  };

  var api = {
    TARGET_SAMPLE_RATE: TARGET_SAMPLE_RATE,
    DEFAULT_MAX_FRAMES: DEFAULT_MAX_FRAMES,
    DEFAULT_BUFFERED_LIMIT_BYTES: DEFAULT_BUFFERED_LIMIT_BYTES,
    shouldBuffer: shouldBuffer,
    downsampleTo16kPCM16: downsampleTo16kPCM16,
    BoundedSendBuffer: BoundedSendBuffer
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    global.StreamUplink = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
