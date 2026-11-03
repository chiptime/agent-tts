/* Voice activity detection for the server voice engine — PURE logic, no DOM.
 *
 * MediaRecorder captures webm/opus continuously while the call is up; this
 * module decides UTTERANCE BOUNDARIES from frame RMS energy:
 *
 * - Adaptive noise floor: a rolling quiet average (EMA). Frames below the
 *   open threshold while the gate is closed keep adapting the floor, so a
 *   noisy room raises the bar instead of producing endless false speech.
 * - Hysteresis: the gate OPENS above floor*openRatio (2.5) but only CLOSES
 *   below floor*closeRatio (1.4) — brief dips mid-word do not split an
 *   utterance.
 * - Utterance end: the configured silence window below the close
 *   threshold (800ms default), or the hard cap (15s default, 60s max).
 *   Both are EXACTLY the pre-growth behavior unless the caller opts in:
 *   silenceGrowthPerS/maxSilenceMs grow the silence window with the
 *   utterance (a natural thinking pause no longer splits a long turn),
 *   and utteranceCapMs REPLACES the legacy hard cap with a far bound for
 *   pathological never-quiet input (segments, not dispatches, bound the
 *   resources of an ordinary long turn — see shouldSegment).
 * - shouldSegment()/markSegment(): recorder-rotation boundaries for
 *   long-running speech. A segment boundary NEVER ends the utterance;
 *   the caller rotates its capture chunk so memory and each upload stay
 *   bounded while the words keep accumulating client-side.
 * - shouldFinalize latches; the reason ("silence" | "cap" |
 *   "utteranceCap") is exposed via lastFinalizeReason() so callers can
 *   surface a bound-driven close honestly (the captured audio is still
 *   dispatched — a bound never drops speech).
 *
 * API mirrors endpointing.js:
 *   push(level) -> bool   feed one frame's RMS; true when speech opens.
 *   hasSpeech() -> bool   an utterance is in progress.
 *   shouldFinalize() -> bool  utterance complete (latches until reset).
 *   shouldSegment() -> bool   a recorder-rotation boundary was crossed.
 *   markSegment()             caller rotated the capture chunk.
 *   lastFinalizeReason() -> "silence" | "cap" | "utteranceCap" | null
 *   reset()               start a new utterance; KEEPS the adapted floor.
 *
 * Dual environment: browser global (window.Vad) and CommonJS for node --test.
 */
(function (global) {
  "use strict";

  var DEFAULT_SILENCE_MS = 800;              // silence window at utterance start
  var DEFAULT_SILENCE_GROWTH_PER_S_MS = 0;   // OPT-IN: growth per second of speech
  var DEFAULT_HARD_CAP_MS = 15000;
  var MAX_HARD_CAP_MS = 60000;
  var MIN_UTTERANCE_CAP_MS = 1000;           // opt-in utteranceCapMs sanity range
  var MAX_UTTERANCE_CAP_MS = 3600000;
  var MIN_BOUNDARY_MS = 1000;                // minimum opt-in boundary interval
  var DEFAULT_OPEN_RATIO = 2.5;   // level > floor*ratio opens the gate
  var DEFAULT_CLOSE_RATIO = 1.25; // level < floor*ratio closes the gate
  var DEFAULT_FLOOR = 0.004;      // initial/minimum noise floor (RMS)
  var DEFAULT_FLOOR_ALPHA = 0.1;  // EMA weight of each quiet frame

  function createVad(options) {
    options = options || {};
    var silenceMs = typeof options.silenceMs === "number" ? options.silenceMs : DEFAULT_SILENCE_MS;
    var silenceGrowthPerS = typeof options.silenceGrowthPerS === "number" &&
      options.silenceGrowthPerS >= 0 ? options.silenceGrowthPerS : DEFAULT_SILENCE_GROWTH_PER_S_MS;
    var maxSilenceMs = Math.max(silenceMs,
      typeof options.maxSilenceMs === "number" ? options.maxSilenceMs : silenceMs);
    var hardCapMs = Math.min(MAX_HARD_CAP_MS,
      typeof options.hardCapMs === "number" ? options.hardCapMs : DEFAULT_HARD_CAP_MS);
    /* utteranceCapMs OPTS IN to the far absolute bound and REPLACES the
     * legacy hard cap: callers that bound resources with segments do not
     * want a mid-speech legacy dispatch at 15-60s. */
    var utteranceCapMs = typeof options.utteranceCapMs === "number" &&
      options.utteranceCapMs >= MIN_UTTERANCE_CAP_MS
      ? Math.min(MAX_UTTERANCE_CAP_MS, options.utteranceCapMs) : null;
    var activeCapMs = utteranceCapMs !== null ? utteranceCapMs : hardCapMs;
    var segmentMs = typeof options.segmentMs === "number" &&
      options.segmentMs >= MIN_BOUNDARY_MS ? options.segmentMs : null;
    var openRatio = typeof options.openRatio === "number" ? options.openRatio : DEFAULT_OPEN_RATIO;
    var closeRatio = typeof options.closeRatio === "number" ? options.closeRatio : DEFAULT_CLOSE_RATIO;
    var minFloor = typeof options.minFloor === "number" ? options.minFloor : DEFAULT_FLOOR;
    var floorAlpha = typeof options.floorAlpha === "number" ? options.floorAlpha : DEFAULT_FLOOR_ALPHA;
    var now = options.now || function () { return Date.now(); };

    var noiseFloor = Math.max(minFloor, 0);
    var gateOpen = false;          // level is above the close threshold
    var hasSpeechFlag = false;     // an utterance started (and not finalized)
    var speechStartedAt = null;
    var lastLoudAt = null;         // last frame above the close threshold
    var done = false;              // shouldFinalize latch
    var finalizeReason = null;     // why shouldFinalize flipped true (survives reset)
    var segmentStartAt = null;     // utterance start or last markSegment()

    /* The silence window for the CURRENT pause. With growth 0 (the
     * default) it is the caller's flat silenceMs — the exact legacy
     * behavior. Opted in, the base grows with the utterance's speech span
     * (last loud frame minus speech start — fixed while the user is
     * quiet, so the window does not run away while we wait), clamped to
     * [silenceMs, maxSilenceMs]. Long turns get longer tolerated pauses;
     * the ceiling keeps turn-taking bounded. */
    function silenceWindowMs() {
      if (lastLoudAt === null || speechStartedAt === null) return silenceMs;
      var speechMs = Math.max(0, lastLoudAt - speechStartedAt);
      var window = silenceMs + (speechMs / 1000) * silenceGrowthPerS;
      return Math.min(maxSilenceMs, Math.max(silenceMs, window));
    }

    function push(level) {
      if (done) return false;
      var t = now();
      if (!gateOpen) {
        if (level > noiseFloor * openRatio) {
          gateOpen = true;
          hasSpeechFlag = true;
          speechStartedAt = t;
          lastLoudAt = t;
          segmentStartAt = t;
          return true;
        }
        // Quiet frame while closed: adapt the floor (rolling quiet average).
        noiseFloor = Math.max(minFloor, noiseFloor + floorAlpha * (level - noiseFloor));
        return false;
      }
      if (level >= noiseFloor * closeRatio) {
        lastLoudAt = t;  // still speech (hysteresis): silence clock restarts
        return true;
      }
      return false;  // quiet while open: silence clock runs in shouldFinalize
    }

    function hasSpeech() {
      return hasSpeechFlag && !done;
    }

    function shouldFinalize() {
      if (!hasSpeechFlag || done) return false;
      var t = now();
      if (t - speechStartedAt >= activeCapMs) {
        done = true;
        finalizeReason = utteranceCapMs !== null ? "utteranceCap" : "cap";
        return true;
      }
      if (lastLoudAt !== null && t - lastLoudAt >= silenceWindowMs()) {
        done = true;
        finalizeReason = "silence";
        return true;
      }
      return false;
    }

    /* Recorder-rotation boundary: crossed once per segmentMs of ongoing
     * speech. Never latches done — a segment is transport chunking, not
     * an utterance end; markSegment() re-arms the boundary when the
     * caller has rotated its capture chunk. */
    function shouldSegment() {
      if (segmentMs === null || !hasSpeechFlag || done) return false;
      return now() - segmentStartAt >= segmentMs;
    }

    function markSegment() {
      segmentStartAt = now();
    }

    function reset() {
      gateOpen = false;
      hasSpeechFlag = false;
      speechStartedAt = null;
      lastLoudAt = null;
      segmentStartAt = null;
      done = false;
      // noiseFloor is deliberately kept: it learned the room.
      // finalizeReason is deliberately kept too: the caller reads it after
      // shouldFinalize() latched, before the next utterance overwrites it.
    }

    return {
      push: push,
      hasSpeech: hasSpeech,
      shouldFinalize: shouldFinalize,
      shouldSegment: shouldSegment,
      markSegment: markSegment,
      lastFinalizeReason: function () { return finalizeReason; },
      reset: reset,
      floor: function () { return noiseFloor; }
    };
  }

  var api = {
    createVad: createVad,
    DEFAULT_SILENCE_MS: DEFAULT_SILENCE_MS,
    DEFAULT_SILENCE_GROWTH_PER_S_MS: DEFAULT_SILENCE_GROWTH_PER_S_MS,
    DEFAULT_HARD_CAP_MS: DEFAULT_HARD_CAP_MS,
    MAX_HARD_CAP_MS: MAX_HARD_CAP_MS
  };
  global.Vad = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
