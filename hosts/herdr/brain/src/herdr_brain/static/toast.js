/* Formatted agent-status toast (toast-formatted-avisos).
 *
 * Pure UMD module following the reader.js house pattern: every DOM
 * touch goes through the injected surface ({ doc, el,
 * mountFormatted }) — this file references no browser global at all,
 * so the Node suite drives it against a plain fake DOM.
 *
 * Safety contract (inherited from the reader):
 *  - A formatted payload mounts ONLY through the injected mount
 *    function (the host wires reader.mountTurn): innerHTML stays on
 *    .reader-content containers, script tags and inline handlers are
 *    neutralized, links are hardened (http/https only, target=_blank,
 *    rel=noopener noreferrer) — the same scoped-insertion discipline
 *    as the reading surface.
 *  - The html string is mounted VERBATIM. The excerpt is CSS clipping
 *    on the .toast-content body (max-height + overflow hidden in
 *    index.html); the payload is never string-cut or regex-stripped.
 *  - Every failure path (null html, missing mount function, mount
 *    refusal, insertion throw) degrades to the exact textContent
 *    toast this app always had — watcher template avisos and system
 *    warnings are indistinguishable from before this module.
 *
 * chunkifyText (replay teleprompter, 2026-09-25): pure string helper
 * that splits a turn into sequential ~approxChars windows (default 80
 * ≈ 2 lines at the toast width). Cut preference inside a window:
 * sentence end (. ! ? …) > clause (, ; newline) > word space — and ":"
 * is NEVER a boundary: the space right after a colon is skipped, so a
 * window can not end hanging on the colon it opened. Every window is
 * normalized (edges trimmed, inner whitespace runs collapsed to single
 * spaces) so the teleprompter never shows a stray line jump. A window
 * with no boundary at all is an unbroken token longer than the limit:
 * it is cut at the first boundary AFTER the limit (the one allowed
 * mid-word case) or kept whole when no boundary ever follows. The
 * replay toast paints one window at a time and the player timeupdate
 * advances the window — the full text stays in the turn (drawer), the
 * toast is a reading aid.
 *
 * splitForTts (multi-piece replay, 2026-09-25): POST /tts validates
 * max_length=8000 per request, so a longer turn used to die as HTTP
 * 422 with no audio (product decision: no client-side reading limit —
 * the text we have is the text we read). The turn is split with the
 * same boundary preference into pieces within maxChars (default
 * 8000). A short text returns VERBATIM as a single piece (yesterday's
 * exact request body); an unbroken token longer than the limit is
 * HARD-CUT at the limit — for the request guard "within limit" is
 * absolute, unlike the display windows. Piece interiors stay verbatim.
 */

(function (global) {
  function createToast(deps) {
    var doc = deps && deps.doc;
    var el = deps && deps.el;
    var mountFormatted = deps && deps.mountFormatted;
    var timer = null;

    function hide() {
      if (timer) { clearTimeout(timer); timer = null; }
      el.classList.add("hidden");
    }

    function show(text, opts) {
      opts = opts || {};
      var body = null;
      if (opts.html && typeof mountFormatted === "function") {
        var candidate = doc.createElement("div");
        candidate.classList.add("toast-content");
        candidate.classList.add("reader-content");
        var mounted = false;
        try {
          mounted = mountFormatted(candidate, { text: text, html: opts.html });
        } catch (err) {
          mounted = false;   /* fail-soft: plain text below */
        }
        if (mounted) {
          el.textContent = "";   /* one live body per toast, never mixed */
          el.appendChild(candidate);
          body = candidate;
        }
      }
      if (!body) el.textContent = text;   /* the historical plain path */
      el.classList.toggle("warning", opts.kind === "warning");
      el.classList.remove("hidden");
      if (timer) { clearTimeout(timer); timer = null; }
      if (opts.durationMs) timer = setTimeout(hide, opts.durationMs);
    }

    return { show: show, hide: hide };
  }

  /* ---- replay helpers: boundary tiers shared by both splitters ---- */

  var SENTENCE_ENDS = [".", "!", "?", "…"];
  var CLAUSE_ENDS = [",", ";", "\n"];

  function normalizeSpace(s) {
    return s.replace(/\s+/g, " ").trim();
  }

  /* Latest cut (exclusive end index) that keeps the punctuation char
   * inside the chunk, at or before the ceiling. */
  function latestPunctCut(rest, chars, ceiling) {
    var end = -1;
    for (var i = 0; i < chars.length; i++) {
      var at = rest.lastIndexOf(chars[i], ceiling);
      if (at !== -1 && at + 1 > end) end = at + 1;
    }
    return end;
  }

  /* Latest word-space cut (chunk ends BEFORE the space). A space whose
   * preceding non-space char is ":" is not a boundary. */
  function latestSpaceCut(rest, ceiling) {
    for (var idx = ceiling; idx > 0; idx--) {
      if (rest.charAt(idx) !== " ") continue;
      var before = idx - 1;
      while (before >= 0 && rest.charAt(before) === " ") before--;
      if (before >= 0 && rest.charAt(before) === ":") continue;
      return idx;
    }
    return -1;
  }

  /* Best in-window cut, tier by tier: sentence > clause > word space.
   * Within the winning tier the LATEST cut wins (~even windows). */
  function bestWindowCut(rest, limit) {
    var end = latestPunctCut(rest, SENTENCE_ENDS, limit - 1);
    if (end > 0) return end;
    end = latestPunctCut(rest, CLAUSE_ENDS, limit - 1);
    if (end > 0) return end;
    return latestSpaceCut(rest, limit);
  }

  /* Hard-cut fallback: earliest boundary at/after the limit (the token
   * overruns the window, so its END is the cut), -1 when none exist. */
  function firstCutAfter(rest, limit) {
    var all = SENTENCE_ENDS.concat(CLAUSE_ENDS, [" "]);
    var next = -1;
    for (var i = 0; i < all.length; i++) {
      var ahead = rest.indexOf(all[i], limit);
      if (ahead !== -1 && (next === -1 || ahead < next)) next = ahead;
    }
    return next === -1 ? -1 : next + 1;
  }

  function chunkifyText(text, approxChars) {
    if (typeof text !== "string") return [];
    var rest = text.trim();
    if (!rest) return [];
    var limit = typeof approxChars === "number" && approxChars > 0 ? approxChars : 80;
    var chunks = [];
    /* Each pass emits one <=limit window (when a boundary exists) and
     * consumes it, so the loop always advances. Windows are normalized:
     * clean edges, inner whitespace runs collapsed to single spaces. */
    while (rest.length > limit) {
      var end = bestWindowCut(rest, limit);
      if (end <= 0) {
        end = firstCutAfter(rest, limit);
        if (end === -1) break;   /* over-long word runs to the end */
      }
      var chunk = normalizeSpace(rest.slice(0, end));
      if (chunk) chunks.push(chunk);
      rest = rest.slice(end).trim();
    }
    if (rest) chunks.push(normalizeSpace(rest));
    return chunks;
  }

  function splitForTts(text, maxChars) {
    if (typeof text !== "string" || !text) return [];
    var limit = typeof maxChars === "number" && maxChars > 0 ? maxChars : 8000;
    if (text.length <= limit) return [text];   /* verbatim: one request, as always */
    var rest = text.trim();
    var pieces = [];
    while (rest.length > limit) {
      var end = bestWindowCut(rest, limit);
      if (end <= 0) end = limit;   /* unbroken token: hard cut — never exceed the request guard */
      var piece = rest.slice(0, end).trim();
      if (piece) pieces.push(piece);
      rest = rest.slice(end).trim();
    }
    if (rest) pieces.push(rest);
    return pieces;
  }

  var api = { createToast: createToast, chunkifyText: chunkifyText, splitForTts: splitForTts };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    global.Toast = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
