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
 * ≈ 2 lines at the toast width), cutting preferentially at sentence
 * ends (" ", ".", "\n") and never mid-word unless a single word alone
 * exceeds the limit. The replay toast paints one window at a time and
 * the player timeupdate advances the window — the full text stays in
 * the turn (drawer), the toast is a reading aid.
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

  function chunkifyText(text, approxChars) {
    if (typeof text !== "string") return [];
    var rest = text.trim();
    if (!rest) return [];
    var limit = typeof approxChars === "number" && approxChars > 0 ? approxChars : 80;
    var bounds = [" ", ".", "\n"];
    var chunks = [];
    /* Each pass emits one <=limit window (when a boundary exists) and
     * consumes it, so the loop always advances. A window with NO
     * boundary is an unbroken token longer than the limit: it is cut
     * at the first boundary AFTER the limit (the one allowed mid-word
     * case) or kept whole when no boundary ever follows. */
    while (rest.length > limit) {
      var window = rest.slice(0, limit);
      var best = -1;
      var i;
      for (i = 0; i < bounds.length; i++) {
        var at = window.lastIndexOf(bounds[i]);
        if (at > best) best = at;   /* latest boundary wins: ~even windows */
      }
      if (best <= 0) {
        var next = -1;
        for (i = 0; i < bounds.length; i++) {
          var ahead = rest.indexOf(bounds[i], limit);
          if (ahead !== -1 && (next === -1 || ahead < next)) next = ahead;
        }
        if (next === -1) break;     /* over-long word runs to the end */
        best = next;
      }
      var chunk = rest.slice(0, best + 1).trim();
      if (chunk) chunks.push(chunk);
      rest = rest.slice(best + 1).trim();
    }
    if (rest) chunks.push(rest);
    return chunks;
  }

  var api = { createToast: createToast, chunkifyText: chunkifyText };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    global.Toast = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
