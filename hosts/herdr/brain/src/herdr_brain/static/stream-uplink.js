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

  /* Live transcript display tracker (PRD-04/S5, T3): the pure client
   * state behind the interim strip. Finals accumulate with single
   * spaces (FR-04 display contract); the latest partial — which the
   * server sends as the FULL current best guess — supersedes them for
   * rendering; reset clears the utterance at every turn boundary so a
   * new utterance never shows the finished turn's text (prefix-overlap
   * protection). Server text is UNTRUSTED: never a number, bounded in
   * length, stripped of control characters before it reaches the DOM. */
  var LIVE_TEXT_LIMIT = 2000;

  function sanitizeLiveText(value) {
    if (typeof value !== "string") return "";
    var flat = "";
    for (var i = 0; i < value.length; i++) {
      var ch = value[i];
      flat += ch === "\n" || ch === "\r" || ch === "\t" ? " " : (ch < " " ? "" : ch);
    }
    return flat.replace(/\s+/g, " ").trim().slice(0, LIVE_TEXT_LIMIT);
  }

  function LiveTranscriptTracker() {
    this.finals = "";
    this.partial = "";
  }

  LiveTranscriptTracker.prototype.onFinal = function (text) {
    var clean = sanitizeLiveText(text);
    if (clean) {
      this.finals = this.finals ? this.finals + " " + clean : clean;
      this.partial = ""; // superseded: the stable text moved forward
    }
    return this.text();
  };

  LiveTranscriptTracker.prototype.onPartial = function (text) {
    this.partial = sanitizeLiveText(text);
    return this.text();
  };

  LiveTranscriptTracker.prototype.text = function () {
    return this.partial || this.finals;
  };

  LiveTranscriptTracker.prototype.reset = function () {
    this.finals = "";
    this.partial = "";
    return "";
  };

  /* S7 (PRD-05/D10, T4): rejoin text accumulation — the pure client
   * state behind "speech after the close but before the answer starts
   * rejoins the SAME turn". The closed segment's dispatched transcript
   * becomes the base; a transcript arriving while the resumed speech is
   * still recording is HELD as segment one (never dispatched alone);
   * the resumed close merges everything into the WHOLE utterance,
   * dispatched exactly once. An empty resumed transcript still owes the
   * base — the cancelled ask must never lose the user's words. */
  function LiveRejoinText() {
    this.segmentOne = null;  // held transcript of the first closed segment
    this.whole = "";         // accumulated whole utterance (dispatch base)
  }

  LiveRejoinText.prototype.base = function (text) {
    this.whole = text || "";
    return this.whole;
  };

  LiveRejoinText.prototype.holdSegmentOne = function (text) {
    this.segmentOne = text || "";
    return null;  // display/dispatch stay with the recording utterance
  };

  LiveRejoinText.prototype.rejoin = function (text) {
    var parts = [];
    if (this.segmentOne !== null) parts.push(this.segmentOne);
    else if (this.whole) parts.push(this.whole);
    if (text) parts.push(text);
    this.segmentOne = null;
    this.whole = parts.join(" ").trim();
    return this.whole;
  };

  /* L53: bounded client self-report. The live uplink is the only place a
   * phone can tell the journal why it is deaf: AudioContext state,
   * capture mode, frame counters, analyser peak, fallback reason and the
   * served build. Values are untrusted-ish (a page could be hostile) and
   * capped, so every field is a short opaque token or a finite number —
   * numbers only, never audio or transcript text. */
  var DIAG_TOKENS = {
    mode: 12,
    ctx: 12,
    build: 24,
    reason: 24,
    state: 14
  };

  function diagToken(value, limit) {
    if (typeof value !== "string") return "";
    var flat = "";
    for (var i = 0; i < value.length && flat.length < limit; i++) {
      var ch = value[i];
      if (ch < " " || ch === "\u007f") continue;
      flat += ch;
    }
    return flat.slice(0, limit);
  }

  function diagNumber(value) {
    return typeof value === "number" && isFinite(value) ? value : -1;
  }

  function buildDiag(input) {
    var src = input || {};
    return {
      type: "diag",
      mode: diagToken(src.mode, DIAG_TOKENS.mode),
      posted: diagNumber(src.posted),
      sent: diagNumber(src.sent),
      queued: diagNumber(src.queued),
      buffered: diagNumber(src.buffered),
      fails: diagNumber(src.fails),
      maxrms: diagNumber(src.maxrms),
      ctx: diagToken(src.ctx, DIAG_TOKENS.ctx),
      build: diagToken(src.build, DIAG_TOKENS.build),
      reason: diagToken(src.reason, DIAG_TOKENS.reason),
      state: diagToken(src.state, DIAG_TOKENS.state)
    };
  }

  var api = {
    TARGET_SAMPLE_RATE: TARGET_SAMPLE_RATE,
    DEFAULT_MAX_FRAMES: DEFAULT_MAX_FRAMES,
    DEFAULT_BUFFERED_LIMIT_BYTES: DEFAULT_BUFFERED_LIMIT_BYTES,
    LIVE_TEXT_LIMIT: LIVE_TEXT_LIMIT,
    shouldBuffer: shouldBuffer,
    downsampleTo16kPCM16: downsampleTo16kPCM16,
    BoundedSendBuffer: BoundedSendBuffer,
    LiveTranscriptTracker: LiveTranscriptTracker,
    LiveRejoinText: LiveRejoinText,
    buildDiag: buildDiag
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    global.StreamUplink = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
