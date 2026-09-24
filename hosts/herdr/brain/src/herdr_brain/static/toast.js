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

  var api = { createToast: createToast };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    global.Toast = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
