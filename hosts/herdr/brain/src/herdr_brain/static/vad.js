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
 * - Utterance end: 1200ms continuously below the close threshold, or a
 *   15s hard cap measured from speech start.
 *
 * API mirrors endpointing.js:
 *   push(level) -> bool   feed one frame's RMS; true when speech opens.
 *   hasSpeech() -> bool   an utterance is in progress.
 *   shouldFinalize() -> bool  utterance complete (latches until reset).
 *   reset()               start a new utterance; KEEPS the adapted floor.
 *
 * Dual environment: browser global (window.Vad) and CommonJS for node --test.
 */
(function (global) {
  "use strict";

  var DEFAULT_SILENCE_MS = 1200;
  var DEFAULT_HARD_CAP_MS = 15000;
  var DEFAULT_OPEN_RATIO = 2.5;   // level > floor*ratio opens the gate
  var DEFAULT_CLOSE_RATIO = 1.4;  // level < floor*ratio closes the gate
  var DEFAULT_FLOOR = 0.004;      // initial/minimum noise floor (RMS)
  var DEFAULT_FLOOR_ALPHA = 0.1;  // EMA weight of each quiet frame

  function createVad(options) {
    options = options || {};
    var silenceMs = typeof options.silenceMs === "number" ? options.silenceMs : DEFAULT_SILENCE_MS;
    var hardCapMs = typeof options.hardCapMs === "number" ? options.hardCapMs : DEFAULT_HARD_CAP_MS;
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

    function push(level) {
      if (done) return false;
      var t = now();
      if (!gateOpen) {
        if (level > noiseFloor * openRatio) {
          gateOpen = true;
          hasSpeechFlag = true;
          speechStartedAt = t;
          lastLoudAt = t;
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
      if (t - speechStartedAt >= hardCapMs) {
        done = true;
        return true;
      }
      if (lastLoudAt !== null && t - lastLoudAt >= silenceMs) {
        done = true;
        return true;
      }
      return false;
    }

    function reset() {
      gateOpen = false;
      hasSpeechFlag = false;
      speechStartedAt = null;
      lastLoudAt = null;
      done = false;
      // noiseFloor is deliberately kept: it learned the room.
    }

    return {
      push: push,
      hasSpeech: hasSpeech,
      shouldFinalize: shouldFinalize,
      reset: reset,
      floor: function () { return noiseFloor; }
    };
  }

  var api = { createVad: createVad };
  global.Vad = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
