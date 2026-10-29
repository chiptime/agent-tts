/* Destructive-command guard — PURE logic, no DOM.
 *
 * A pane reply types straight into a real terminal, so an accidental send
 * of a destructive command (a fat-fingered `rm -rf`, a stray force-push) is
 * remote-shell dangerous. `isDestructiveInput` flags the small, reviewed
 * set of commands worth a second tap before send — deliberately
 * conservative: a false positive costs one extra tap, a false negative
 * costs a terminal.
 *
 * The guard is a tiny command analyzer, not a regex pile. It works the
 * way the shell it protects does, in three steps:
 *
 *   1. split the input into SEGMENTS — one candidate command each —
 *      breaking on `;`, `&`, `|` and newlines, but never inside quotes;
 *   2. cut each segment into WORDS on whitespace;
 *   3. run every named RULE over the words of every segment, in
 *      priority order, and return the first firing rule's reason.
 *
 * Segmenting first is what makes the scoping honest: a flag belongs to
 * the command it follows (`rm x; grep -r` is not `rm -r`), and quotes
 * protect separators without hiding words — a quoted command line
 * (`bash -c "sudo rm -rf /"`) is still words the rules can see. Words
 * are compared whole and stripped of shell punctuation at the edges, so
 * look-alikes ("assume", "sudoku", "not--force") never fire. Reasons
 * are user-facing copy, shown on the "Really send?" confirm.
 */
(function (global) {
  "use strict";

  /* ── step 1: segments ───────────────────────────────────────────────
   *
   * Quotes guard their content: a `;` inside quotes is text to echo,
   * not a separator. `&&` and `||` fall out of breaking on each `&`
   * and `|` on its own. */
  function splitSegments(text) {
    var segments = [];
    var current = "";
    var quote = null;
    for (var i = 0; i < text.length; i++) {
      var ch = text.charAt(i);
      if (quote) {
        current += ch;
        if (ch === quote) quote = null;
      } else if (ch === "'" || ch === '"') {
        quote = ch;
        current += ch;
      } else if (ch === ";" || ch === "&" || ch === "|" || ch === "\n" || ch === "\r") {
        segments.push(current);
        current = "";
      } else {
        current += ch;
      }
    }
    segments.push(current);
    return segments;
  }

  /* ── step 2: words ──────────────────────────────────────────────────
   *
   * Deliberately NOT quote-aware: a quoted argument still splits into
   * words (the guard must peek inside `bash -c "sudo rm -rf /"`), and
   * for harmless quoted prose the worst case is one extra tap. */
  function splitWords(segment) {
    var words = [];
    var parts = segment.split(/\s+/);
    for (var i = 0; i < parts.length; i++) {
      if (parts[i]) words.push(parts[i]);
    }
    return words;
  }

  /* A word as the shell would name a command: shell punctuation
   * (`$(…)`, `{…}`, quotes, a trailing `)`) peeled off both edges, so
   * `$(rm` and `reboot)` still read as the words they carry. The
   * middle is untouched — this strips wrappers, never letters. */
  function bare(word) {
    return word.replace(/^[$({`'"]+/, "").replace(/[)`}'"]+$/, "");
  }

  /* Does this word name the program `name`? Case-insensitive: an
   * uppercase command is still a command. */
  function isProgram(word, name) {
    return bare(word).toLowerCase() === name;
  }

  /* A recursive-delete flag of rm: `-r`, `-R`, `-rf`, `-fr`, `-rv`… or
   * the long form. One dash, letters only, containing the r. */
  function isRecursiveFlag(word) {
    var flag = bare(word).toLowerCase();
    return flag === "--recursive" || /^-[a-z]*r[a-z]*$/.test(flag);
  }

  /* The blunt force rule: the long `--force` (alone or as
   * `--force=…`) anywhere. The short `-f` is NOT this rule's to
   * flag — half the CLI world has a harmless `-f`, and only the
   * rules that know their program (git push) may read it. */
  function isLongForceFlag(word) {
    var flag = bare(word).toLowerCase();
    return flag === "--force" || flag.indexOf("--force=") === 0;
  }

  /* A force flag on a `git push`: `-f` exactly, `--force`,
   * `--force=…`, or any `--force-*` long form (`--force-with-lease`
   * rewrites history too, it just asks the remote first). */
  function isPushForceFlag(word) {
    var flag = bare(word).toLowerCase();
    return flag === "-f" || isLongForceFlag(word) || flag.indexOf("--force-") === 0;
  }

  /* A redirection operator word: `>`, `>>`, `2>`, `2>>`, `>|`, `&>`,
   * `&>>`, or the truncate idiom `:>`. */
  var REDIRECT_OP = /^(?:\d*&?)?>{1,2}\|?$|^:>$|^&>{1,2}$/;

  /* A glued `word>/absolute/path` — the operator and its target inside
   * one word (`echo x>/etc/passwd`, `2>/dev/null`). Returns the target
   * (starting at the `/`) or null. */
  var GLUED_REDIRECT = /(?:^|[^>])(>{1,2})\|?(\/.*)$/;

  /* An absolute path is the system's, not the composer's working
   * directory — every absolute target is worth a confirm except
   * `/dev/null`, the one routine, harmless silencer. */
  function isSystemTarget(path) {
    return path.charAt(0) === "/" && path !== "/dev/null";
  }

  /* ── step 3: the rules ──────────────────────────────────────────────
   *
   * Ordered most-specific first: the first rule to fire on any segment
   * is the one whose reason is shown (`sudo rm -rf /` is answered as
   * the rm, not the sudo). Each rule sees one segment's words. */
  var DESTRUCTIVE_RULES = [
    {
      name: "rm-recursive",
      reason: "rm -r (borrado recursivo)",
      test: function (words) {
        for (var i = 0; i < words.length; i++) {
          if (!isProgram(words[i], "rm")) continue;
          for (var j = i + 1; j < words.length; j++) {
            if (isRecursiveFlag(words[j])) return true;
          }
        }
        return false;
      },
    },
    {
      name: "git-force-push",
      reason: "git push --force (reescribe el historial)",
      test: function (words) {
        for (var i = 0; i + 1 < words.length; i++) {
          if (!isProgram(words[i], "git")) continue;
          if (!isProgram(words[i + 1], "push")) continue;
          for (var j = i + 2; j < words.length; j++) {
            if (isPushForceFlag(words[j])) return true;
          }
        }
        return false;
      },
    },
    {
      name: "sudo",
      reason: "sudo (se ejecuta como root)",
      test: function (words) {
        for (var i = 0; i < words.length; i++) {
          if (isProgram(words[i], "sudo")) return true;
        }
        return false;
      },
    },
    {
      name: "force-flag",
      reason: "flag --force",
      test: function (words) {
        for (var i = 0; i < words.length; i++) {
          if (isLongForceFlag(words[i])) return true;
        }
        return false;
      },
    },
    {
      name: "dd-raw-write",
      reason: "dd if= (escritura cruda en disco)",
      test: function (words) {
        for (var i = 0; i < words.length; i++) {
          if (!isProgram(words[i], "dd")) continue;
          for (var j = i + 1; j < words.length; j++) {
            if (bare(words[j]).toLowerCase().indexOf("if=") === 0) return true;
          }
        }
        return false;
      },
    },
    {
      name: "mkfs",
      reason: "mkfs (formatea un sistema de ficheros)",
      test: function (words) {
        for (var i = 0; i < words.length; i++) {
          if (/^mkfs($|[^a-z0-9_])/i.test(bare(words[i]))) return true;
        }
        return false;
      },
    },
    {
      name: "redirect-system-path",
      reason: "redirect a una ruta del sistema",
      test: function (words) {
        for (var i = 0; i < words.length; i++) {
          var word = bare(words[i]);
          if (REDIRECT_OP.test(word)) {
            // Operator on its own: the target is the next word.
            if (i + 1 < words.length && isSystemTarget(bare(words[i + 1]))) {
              return true;
            }
          }
          // Operator glued to its target inside one word.
          var glued = word.match(GLUED_REDIRECT);
          if (glued && isSystemTarget(glued[2])) return true;
        }
        return false;
      },
    },
  ];

  /* Returns the reason of the first rule that fires on any segment of
   * the text, or null when none does (non-strings are never
   * destructive). */
  function isDestructiveInput(text) {
    if (typeof text !== "string" || !text) return null;
    var segments = splitSegments(text);
    for (var r = 0; r < DESTRUCTIVE_RULES.length; r++) {
      var rule = DESTRUCTIVE_RULES[r];
      for (var s = 0; s < segments.length; s++) {
        var words = splitWords(segments[s]);
        if (words.length && rule.test(words)) return rule.reason;
      }
    }
    return null;
  }

  var api = {
    DESTRUCTIVE_RULES: DESTRUCTIVE_RULES,
    isDestructiveInput: isDestructiveInput,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    global.DestructiveGuard = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
