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
 *   A genuine repetition inside ONE final ("hola hola") survives; only the
 *   cross-final restating is collapsed. It also clears the current
 *   interim — the final transcript already contains that hypothesis.
 *   A final whose folded words equal the just-dispatched utterance
 *   within duplicateWindowMs is swallowed ENTIRELY: after our dispatch
 *   Chrome Android opens a fresh session that re-emits the whole
 *   utterance, and committing that echo fired a second identical
 *   bubble. Tradeoff: a genuine whole-utterance repeat inside the
 *   window is collapsed too (accepted, mirroring the mergeOverlap
 *   over-merge note above). Rejecting an echo also drops an interim
 *   buffered earlier as a GROWING prefix of that same utterance (see
 *   dropEchoInterim).
 * - combined(): committed and interim joined through the SAME overlap
 *   merge the finals path uses — a fresh session's interim often
 *   re-states the committed text before growing, and a blind append
 *   put a duplicated prefix inside ONE bubble. Rendering follows the
 *   merge rules: accent drift keeps the committed word, case and
 *   punctuation drift keep the interim's, genuine repetition inside
 *   one transcript survives. While an echo is still growing the merged
 *   view can transiently show its short tail; it converges as soon as
 *   the echo reaches the committed text.
 * - push(interimText): sets the CURRENT session's interim only; it can
 *   never shorten committed. An EMPTY interim is ignored outright: a
 *   fresh session must never wipe what earlier sessions captured. An
 *   interim whose folded words EQUAL the just-dispatched utterance
 *   inside duplicateWindowMs is swallowed exactly like commit()
 *   swallows a re-emitted final — same guard, same pre-existing
 *   tradeoff that a genuine whole-utterance repeat inside the window
 *   is collapsed. Shorter speech that merely SHARES a prefix is never
 *   suppressed: after "hola mundo" a genuine "hola" still buffers,
 *   because a short real utterance is indistinguishable from Chrome's
 *   growing echo and suppressing prefixes would eat legitimate
 *   commands. The cost is an honest limitation: a partial echo that
 *   never reaches the full signature is accepted as speech. When a
 *   full echo IS identified, a buffered earlier prefix of that same
 *   echo is dropped with it (dropEchoInterim) so silence cannot
 *   dispatch the partial ghost; unrelated buffered text and timing
 *   are untouched, and if the echo was the only buffered speech its
 *   ghost utterance timing is cleared so later speech gets its own
 *   silence/hard-cap clocks. Any change of the interim text marks
 *   speech activity and resets the silence timer.
 * - shouldFinalize(): true when there is speech AND either
 *   (a) silence_ms elapsed since the last combined-text change, or
 *   (b) hard_cap_ms elapsed since speech began.
 * - finalize(): returns the combined text and resets everything, but
 *   remembers the dispatched text (folded words + time) for the
 *   re-emission guard above; reset() deliberately keeps that memory —
 *   window expiry is what makes stale entries harmless.
 * - text(): the combined text, for live display.
 *
 * Dual environment: browser global (window.Endpointing) and CommonJS for
 * node --test.
 */
(function (global) {
  "use strict";

  var DEFAULT_SILENCE_MS = 1200;
  var DEFAULT_HARD_CAP_MS = 15000;
  var DEFAULT_DUPLICATE_WINDOW_MS = 5000;

  function createEndpointer(options) {
    options = options || {};
    var silenceMs = typeof options.silenceMs === "number" ? options.silenceMs : DEFAULT_SILENCE_MS;
    var hardCapMs = typeof options.hardCapMs === "number" ? options.hardCapMs : DEFAULT_HARD_CAP_MS;
    var duplicateWindowMs = typeof options.duplicateWindowMs === "number" ? options.duplicateWindowMs : DEFAULT_DUPLICATE_WINDOW_MS;
    var now = options.now || function () { return Date.now(); };

    var committed = "";       // finalized in PREVIOUS recognition sessions
    var interim = "";         // latest interim of the CURRENT session
    var lastChangeAt = null;  // time of last combined-text change
    var speechStartedAt = null; // time the first captured text arrived
    var lastFinalSignature = null; // folded words of the last DISPATCHED utterance
    var lastFinalAt = null;   // when finalize() dispatched it

  function combined() {
    /* Merged, not concatenated: the fresh session's interim usually
     * re-states the committed text before growing past it. mergeOverlap
     * returns `interim` when committed is empty and vice versa, so the
     * no-speech and single-source cases keep their exact old shape. */
    return mergeOverlap(committed, interim);
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
      if (isReemittedFinal(text)) {        // FULL echo: same guard as commit()
        dropEchoInterim(text);             // drop a buffered growing prefix
        return false;                      // an echo is not new activity
      }
      if (text === interim) return false;  // no change: silence timer keeps running
      var t = now();
      if (speechStartedAt === null) speechStartedAt = t;
      interim = text;
      lastChangeAt = t;
      return true;
    }

    /* Folded word sequence of a text — the comparison view shared by the
     * overlap match and the post-dispatch re-emission guard. */
    function foldedSignature(text) {
      return splitWords(text).map(normWord).join(" ");
    }

    /* True when `text` is Chrome re-emitting the utterance finalize()
     * just dispatched: same folded words, inside the window. */
    function isReemittedFinal(text) {
      if (lastFinalSignature === null || lastFinalAt === null) return false;
      if (now() - lastFinalAt >= duplicateWindowMs) return false;
      return foldedSignature(text) === lastFinalSignature;
    }

    /* True when `candidate`'s folded words are a prefix of `whole`'s
     * (equality included). Comparison-only view, like every fold here.
     * Used ONLY to clean a buffered echo after a FULL match identified
     * it — never to decide whether new speech is accepted. */
    function isFoldedPrefix(candidate, whole) {
      var words = splitWords(candidate).map(normWord);
      var wholeWords = splitWords(whole).map(normWord);
      if (words.length > wholeWords.length) return false;
      for (var i = 0; i < words.length; i++) {
        if (words[i] !== wholeWords[i]) return false;
      }
      return true;
    }

    /* A FULL echo match identifies the utterance Chrome is re-emitting.
     * Its earlier GROWING prefixes may already sit in the interim
     * buffer — they were accepted as ordinary speech because a shorter
     * real utterance is indistinguishable from echo growth and must
     * NOT be suppressed. Once the full echo identifies the utterance,
     * such a stale prefix is dropped so silence cannot dispatch the
     * partial ghost. Only a folded prefix of the identified echo is
     * cleared; unrelated interim text and committed text are
     * untouched. When the echo was the ONLY buffered speech its
     * utterance timing is ghost state and is cleared too — otherwise a
     * later genuine phrase would inherit the echo's hard-cap age and
     * finalize early. Committed speech keeps its own timing, and the
     * dispatch memory (lastFinalSignature/lastFinalAt) always
     * survives: the window, not cleanup, expires it. */
    function dropEchoInterim(echoText) {
      if (!interim || !isFoldedPrefix(interim, echoText)) return;
      interim = "";
      if (!committed) {
        lastChangeAt = null;
        speechStartedAt = null;
      }
    }

    function commit(finalText) {
      var text = (finalText || "").trim();
      if (!text) return false;
      if (isReemittedFinal(text)) {  // echo: no new content, but a buffered
        dropEchoInterim(text);       // growing prefix of it is stale
        return false;
      }
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
      if (text) {
        /* Remember the dispatch BEFORE resetting — this memory must
         * survive reset() so commit() can recognize Chrome's echo. Only
         * finalize() writes it; a kept commit never overwrites it. */
        lastFinalSignature = foldedSignature(text);
        lastFinalAt = now();
      }
      reset();
      return text;
    }

    function reset() {
      committed = "";
      interim = "";
      lastChangeAt = null;
      speechStartedAt = null;
      /* lastFinalSignature/lastFinalAt survive on purpose: the dispatch
       * memory outlives the utterance; the window expires it. */
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
    DEFAULT_HARD_CAP_MS: DEFAULT_HARD_CAP_MS,
    DEFAULT_DUPLICATE_WINDOW_MS: DEFAULT_DUPLICATE_WINDOW_MS
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    global.Endpointing = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
