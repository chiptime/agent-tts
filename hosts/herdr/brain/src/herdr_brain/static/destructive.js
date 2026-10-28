/* Destructive-command guard — PURE logic, no DOM.
 *
 * A pane reply types straight into a real terminal, so an accidental send
 * of a destructive command (a fat-fingered `rm -rf`, a stray force-push) is
 * remote-shell dangerous. `isDestructiveInput` flags the small, reviewed set
 * of patterns worth a second tap before send. Port of Panel's
 * web/src/lib/destructive.ts (v1.10.0): deliberately conservative — a false
 * positive costs one extra tap, but it must NOT fire on innocent look-alikes
 * ("assume", "sudoku", "forced"), so every pattern is word-boundary anchored.
 *
 * Ordered most-specific first; the first match wins, so its reason is the
 * one shown. None use the `g` flag: `.test()` with `g` is stateful
 * (lastIndex) and would flake across calls. Reasons are user-facing copy.
 */
(function (global) {
  "use strict";

  var DESTRUCTIVE_PATTERNS = [
    // `rm` with a recursive flag (-r, -R, -rf, -fr, -rfv, --recursive). The
    // gap can't cross a command separator (; & |), so a later unrelated -r
    // flag won't trip it.
    { reason: "rm -r (borrado recursivo)", pattern: /\brm\b[^\n;&|]*\s(?:-[a-z]*r[a-z]*|--recursive)\b/i },
    // A force-push can rewrite shared history.
    { reason: "git push --force (reescribe el historial)", pattern: /\bgit\s+push\b[^\n;&|]*\s(?:--force|-f)\b/i },
    { reason: "sudo (se ejecuta como root)", pattern: /\bsudo\b/i },
    { reason: "flag --force", pattern: /--force\b/i },
    { reason: "dd if= (escritura cruda en disco)", pattern: /\bdd\b[^\n]*\bif=/i },
    { reason: "mkfs (formatea un sistema de ficheros)", pattern: /\bmkfs\b/i },
    // Truncate/redirect onto an absolute system path (`:> /…`, `> /dev/sda`, `> /`).
    {
      reason: "redirect a una ruta del sistema",
      pattern: /:>\s*\/|>\s*\/(?:\s|$|(?:dev|etc|boot|proc|sys|usr|bin|sbin|lib|var|root)\b)/i
    }
  ];

  /* Returns the reason of the first destructive pattern the text matches,
   * or null when none does (non-strings are never destructive). */
  function isDestructiveInput(text) {
    if (typeof text !== "string" || !text) return null;
    for (var i = 0; i < DESTRUCTIVE_PATTERNS.length; i++) {
      if (DESTRUCTIVE_PATTERNS[i].pattern.test(text)) return DESTRUCTIVE_PATTERNS[i].reason;
    }
    return null;
  }

  var api = {
    DESTRUCTIVE_PATTERNS: DESTRUCTIVE_PATTERNS,
    isDestructiveInput: isDestructiveInput
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    global.DestructiveGuard = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
