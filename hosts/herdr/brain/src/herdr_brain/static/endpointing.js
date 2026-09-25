/* Voice endpointing for the continuous call — PURE logic, no DOM.
 *
 * Chrome's continuous recognition ends sessions every few seconds and
 * scopes interim results to the CURRENT session, so one user utterance
 * typically spans several recognition sessions (app.js restarts them on
 * onend). The utterance model accumulates across those boundaries:
 *
 * - committed: text finalized (isFinal) in PREVIOUS recognition sessions.
 *   It persists across session restarts and can only grow via commit().
 * - commit(finalText): merges a finalized chunk into the committed text.
 *   Chrome Android restarts sessions mid-utterance and RE-EMITS the whole
 *   utterance as a growing final ("a" -> "a b" -> "a b c"), so a blind
 *   append turned one utterance into O(n²) word soup. The merge strips
 *   the overlap (suffix of committed == prefix of the final, compared
 *   case/punctuation/accent-insensitively) and keeps only the new tail.
 *   A genuine repetition inside ONE final ("hola hola") survives; only
 *   the cross-final restating is collapsed. It also clears the current
 *   interim — the final transcript already contains that hypothesis.
 * - push(interimText): sets the CURRENT session's interim only; it can
 *   never shorten committed. An EMPTY interim is ignored outright: a
 *   fresh session must never wipe what earlier sessions captured. Any
 *   change of the COMBINED text (committed + interim) marks speech
 *   activity and resets the silence timer.
 * - shouldFinalize(): true when there is speech AND either
 *   (a) silence_ms elapsed since the last combined-text change, or
 *   (b) hard_cap_ms elapsed since speech began.
 * - finalize(): returns the combined text and resets everything.
 * - text(): the combined text, for live display.
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

    var committed = "";       // finalized in PREVIOUS recognition sessions
    var interim = "";         // latest interim of the CURRENT session
    var lastChangeAt = null;  // time of last combined-text change
    var speechStartedAt = null; // time the first captured text arrived

  function combined() {
    if (!committed) return interim;
    if (!interim) return committed;
    return committed + " " + interim;
  }

  /* Word-level normalization for overlap matching: Chrome finals drift in
   * case, punctuation, and accents between sessions ("qué" -> "que"), and
   * none of that drift may defeat the match. Matching folds diacritics:
   * NFD-normalize, then strip combining marks (U+0300-U+036F), so "que"
   * and "qué" compare equal ("ñ" folds to "n" — accepted). Folding is a
   * comparison-only view; raw committed/display text is never rewritten.
   * Per-word so raw and normalized stay aligned. */
  function plainWord(word) {
    return word.toLowerCase().replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, "");
  }

  function normWord(word) {
    return plainWord(word).normalize("NFD").replace(/[\u0300-\u036f]/g, "");
  }

  function splitWords(text) {
    return text.split(/\s+/).filter(Boolean);
  }

  /* Merges `next` into `base` stripping the longest overlap where a
   * suffix of base equals a prefix of next (the Chrome growing-final
   * shape). Returns base when next is empty and next when base is.
   * Overlap rendering: a pair that differs only by accent drift (equal
   * after folding, different before it) keeps the committed word —
   * accents are meaning-bearing in Spanish and a re-emission is not a
   * correction of them. Case/punctuation-only drift keeps the final's
   * rendering, as it always has. */
  function mergeOverlap(base, next) {
    var baseWords = splitWords(base);
    var nextWords = splitWords(next);
    if (!baseWords.length) return next;
    if (!nextWords.length) return base;
    var maxK = Math.min(baseWords.length, nextWords.length);
    var k = 0;
    for (var candidate = maxK; candidate >= 1; candidate--) {
      var matches = true;
      for (var i = 0; i < candidate; i++) {
        if (normWord(baseWords[baseWords.length - candidate + i]) !==
            normWord(nextWords[i])) {
          matches = false;
          break;
        }
      }
      if (matches) { k = candidate; break; }
    }
    var merged = baseWords.slice(0, baseWords.length - k);
    for (var j = 0; j < k; j++) {
      merged.push(overlapWord(baseWords[baseWords.length - k + j], nextWords[j]));
    }
    return merged.concat(nextWords.slice(k)).join(" ");
  }

  /* Picks the surviving rendering for one overlapped word pair: the
   * final's word unless the pair drifted ONLY in accents, in which case
   * the committed word stays. */
  function overlapWord(baseWord, nextWord) {
    return plainWord(baseWord) === plainWord(nextWord) ? nextWord : baseWord;
  }

    function push(interimText) {
      var text = (interimText || "").trim();
      if (!text) return false;             // empty interim never wipes anything
      if (text === interim) return false;  // no change: silence timer keeps running
      var t = now();
      if (speechStartedAt === null) speechStartedAt = t;
      interim = text;
      lastChangeAt = t;
      return true;
    }

    function commit(finalText) {
      var text = (finalText || "").trim();
      if (!text) return false;
      var t = now();
      if (speechStartedAt === null) speechStartedAt = t;
      committed = mergeOverlap(committed, text);
      interim = "";  // the final transcript already contains it
      lastChangeAt = t;
      return true;
    }

    function hasSpeech() {
      return combined().length > 0;
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
      var text = combined();
      reset();
      return text;
    }

    function reset() {
      committed = "";
      interim = "";
      lastChangeAt = null;
      speechStartedAt = null;
    }

    function snapshot() {
      /* Pure read for diagnostics overlays: shape is stable, relative
       * remaining times are null when the utterance has not started. */
      var t = now();
      var speaking = hasSpeech();
      return {
        buffer: combined(),
        bufferChars: combined().length,
        committedChars: committed.length,
        interimChars: interim.length,
        hasSpeech: speaking,
        speechStartedAt: speechStartedAt,
        lastChangeAt: lastChangeAt,
        silenceMs: silenceMs,
        hardCapMs: hardCapMs,
        silenceRemainingMs: speaking && lastChangeAt !== null
          ? Math.max(0, silenceMs - (t - lastChangeAt))
          : null,
        capRemainingMs: speaking && speechStartedAt !== null
          ? Math.max(0, hardCapMs - (t - speechStartedAt))
          : null,
        shouldFinalize: shouldFinalize()
      };
    }

    return {
      push: push,
      commit: commit,
      hasSpeech: hasSpeech,
      shouldFinalize: shouldFinalize,
      silenceFor: silenceFor,
      finalize: finalize,
      reset: reset,
      text: combined,
      snapshot: snapshot
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
