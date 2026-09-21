/* Voice endpointing for the continuous call — PURE logic, no DOM.
 *
 * Chrome's continuous recognition often emits interim results without ever
 * finalizing, so the brain self-endpoints the utterance:
 *
 * - push(interimText): accumulates the latest interim transcript. Any text
 *   change marks "speech activity" and resets the silence timer.
 * - shouldFinalize(now): true when there is speech AND either
 *   (a) silence_ms elapsed since the last change, or
 *   (b) hard_cap_ms elapsed since speech began.
 * - finalize(): returns the accumulated text and resets the utterance.
 *
 * Dual environment: browser global (window.Endpointing) and CommonJS for
 * node --test.
 */
(function (global) {
  "use strict";

  var DEFAULT_SILENCE_MS = 1200;
  var DEFAULT_HARD_CAP_MS = 15000;

  function createEndpointer(options) {
    options = options || {};
    var silenceMs = typeof options.silenceMs === "number" ? options.silenceMs : DEFAULT_SILENCE_MS;
    var hardCapMs = typeof options.hardCapMs === "number" ? options.hardCapMs : DEFAULT_HARD_CAP_MS;
    var now = options.now || function () { return Date.now(); };

    var buffer = "";          // latest interim transcript
    var lastChangeAt = null;  // time of last buffer change (since speech began)
    var speechStartedAt = null; // time the first non-empty interim arrived

    function push(interimText) {
      var text = (interimText || "").trim();
      if (text === buffer) return false;  // no change: silence timer keeps running
      var t = now();
      if (!speechStartedAt && text) speechStartedAt = t;
      buffer = text;
      lastChangeAt = t;
      return true;
    }

    function hasSpeech() {
      return buffer.length > 0;
    }

    function shouldFinalize() {
      if (!hasSpeech()) return false;
      var t = now();
      if (lastChangeAt !== null && t - lastChangeAt >= silenceMs) return true;
      if (speechStartedAt !== null && t - speechStartedAt >= hardCapMs) return true;
      return false;
    }

    function silenceFor() {
      if (lastChangeAt === null) return 0;
      return Math.max(0, now() - lastChangeAt);
    }

    function finalize() {
      var text = buffer;
      reset();
      return text;
    }

    function reset() {
      buffer = "";
      lastChangeAt = null;
      speechStartedAt = null;
    }

    return {
      push: push,
      hasSpeech: hasSpeech,
      shouldFinalize: shouldFinalize,
      silenceFor: silenceFor,
      finalize: finalize,
      reset: reset
    };
  }

  var api = {
    createEndpointer: createEndpointer,
    DEFAULT_SILENCE_MS: DEFAULT_SILENCE_MS,
    DEFAULT_HARD_CAP_MS: DEFAULT_HARD_CAP_MS
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    global.Endpointing = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
