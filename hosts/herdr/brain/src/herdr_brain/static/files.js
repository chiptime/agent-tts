/* Workspace file explorer (W3-P2 S3, doc 13 §1) — PURE logic, no DOM.
 *
 * The server module (workspace.py) already refuses any escape from the
 * pane cwd and bounds every read; this module holds everything
 * decidable without the DOM: byte-size and mtime formatting, git
 * porcelain status labels, the render model for the bounded tree
 * (directories first, indentation depth), and the per-file split of a
 * unified git diff for the «Ficheros» tab's diff viewer. app.js owns
 * the fetching (readFetch) and the DOM painting (text nodes only).
 *
 * The same discipline as send-track.js: every function here is pure,
 * so tests/js can pin the behaviour without a browser. */
(function (global) {
  "use strict";

  /* Git porcelain status codes → the short Spanish label the status
   * chips show. Index ("staged") codes win over worktree codes: an
   * entry "MM" is displayed by its FIRST column, the stronger claim. */
  var STATUS_LABELS = {
    A: "nuevo",
    M: "modificado",
    D: "borrado",
    R: "renombrado",
    C: "copiado",
    T: "tipo cambiado",
    U: "sin fusionar",
    "?": "sin seguimiento",
    "!": "ignorado",
  };

  var UNKNOWN_STATUS_LABEL = "cambiado";

  function statusLabel(code) {
    var first = (code || "?").trim().charAt(0);
    return STATUS_LABELS[first] || UNKNOWN_STATUS_LABEL;
  }

  function formatSize(bytes) {
    var value = Number(bytes);
    if (!isFinite(value) || value < 0) return "";
    if (value < 1024) return value + " B";
    var units = ["KB", "MB", "GB"];
    var unit = -1;
    do {
      value /= 1024;
      unit += 1;
    } while (value >= 1024 && unit < units.length - 1);
    return (value >= 100 ? Math.round(value) : value.toFixed(1)) + " " + units[unit];
  }

  function formatMtime(epochSeconds) {
    var value = Number(epochSeconds);
    if (!isFinite(value) || value <= 0) return "";
    var date = new Date(value * 1000);
    function pad(n) { return (n < 10 ? "0" : "") + n; }
    return (
      date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate()) +
      " " + pad(date.getHours()) + ":" + pad(date.getMinutes())
    );
  }

  /* The render model for the bounded tree payload: one row per entry,
   * directories first (the server walk already orders them), with the
   * indentation depth derived from the path — no DOM here. */
  function treeRows(entries) {
    var rows = [];
    var list = Array.isArray(entries) ? entries : [];
    for (var i = 0; i < list.length; i++) {
      var entry = list[i];
      if (!entry || !entry.path) continue;
      var path = String(entry.path);
      var name = path.slice(path.lastIndexOf("/") + 1);
      rows.push({
        path: path,
        name: name,
        type: entry.type === "dir" ? "dir" : "file",
        depth: path.split("/").length - 1,
        size: entry.type === "dir" ? "" : formatSize(entry.size),
        mtime: formatMtime(entry.mtime),
      });
    }
    return rows;
  }

  /* Split a unified git diff into per-file chunks (the «Ficheros» tab
   * previews ONE file at a time). Each chunk keeps its raw body and a
   * display name parsed from the +++ line (falling back to the diff
   * --git header); a part without any resolvable name is not a file
   * chunk (garbage text) and is skipped.
   *
   * Lazygit-style additions/deletions counts and per-line classification
   * (add, del, hunk, header, ctx) are computed here so the UI can color
   * lines deterministically without raw HTML injection. */
  function diffFiles(diffText) {
    var text = typeof diffText === "string" ? diffText : "";
    if (!text) return [];
    var chunks = [];
    var parts = text.split(/^diff --(?:git|cc|combined) /m);
    for (var i = 0; i < parts.length; i++) {
      var body = parts[i];
      if (!body || !body.trim()) continue;
      var name = null;
      var plus = body.match(/^\+\+\+ (?:b\/)?(.+)$/m);
      if (plus && plus[1] && plus[1] !== "/dev/null") {
        name = plus[1].trim();
      } else {
        var header = body.match(/^a\/(.+?) b\//m);
        if (header) name = header[1].trim();
        else {
          var minus = body.match(/^--- (?:a\/)?(.+)$/m);
          if (minus && minus[1] && minus[1] !== "/dev/null") {
            name = minus[1].trim();
          }
        }
      }
      if (!name) continue;

      var lines = body.split("\n");
      var additions = 0;
      var deletions = 0;
      var parsedLines = [];
      for (var j = 0; j < lines.length; j++) {
        var line = lines[j];
        var kind = "ctx";
        if (line.startsWith("@@")) {
          kind = "hunk";
        } else if (line.startsWith("+") && !line.startsWith("+++")) {
          kind = "add";
          additions++;
        } else if (line.startsWith("-") && !line.startsWith("---")) {
          kind = "del";
          deletions++;
        } else if (
          line.startsWith("diff --") ||
          line.startsWith("index ") ||
          line.startsWith("--- ") ||
          line.startsWith("+++ ") ||
          line.startsWith("new file ") ||
          line.startsWith("deleted file ") ||
          line.startsWith("similarity index ") ||
          line.startsWith("rename ") ||
          line.startsWith("commit ") ||
          line.startsWith("Author: ") ||
          line.startsWith("Date: ") ||
          line.startsWith("Merge: ")
        ) {
          kind = "header";
        }
        parsedLines.push({ kind: kind, text: line });
      }

      chunks.push({
        file: name,
        body: "diff --git " + body,
        additions: additions,
        deletions: deletions,
        parsedLines: parsedLines,
      });
    }
    return chunks;
  }

  var STATUS_BADGES = {
    M: { code: "M", cls: "diff-badge-m" },
    A: { code: "A", cls: "diff-badge-a" },
    D: { code: "D", cls: "diff-badge-d" },
    R: { code: "R", cls: "diff-badge-m" },
    "?": { code: "?", cls: "diff-badge-u" },
    U: { code: "!", cls: "diff-badge-d" },
  };

  function statusBadge(status) {
    var first = (status || "?").trim().charAt(0);
    return STATUS_BADGES[first] || { code: first || "?", cls: "diff-badge-m" };
  }

  /* One status count line: "2 modificados, 1 sin seguimiento". */
  function statusSummary(entries) {
    var list = Array.isArray(entries) ? entries : [];
    if (!list.length) return "";
    var counts = {};
    var order = [];
    for (var i = 0; i < list.length; i++) {
      var label = statusLabel((list[i] || {}).status);
      if (!(label in counts)) {
        counts[label] = 0;
        order.push(label);
      }
      counts[label] += 1;
    }
    return order
      .map(function (label) {
        var n = counts[label];
        return n + " " + (n === 1 ? singular(label) : plural(label));
      })
      .join(", ");
  }

  var SINGULARS = { nuevo: "nuevo", borrado: "borrado", "sin seguimiento": "sin seguimiento" };
  var PLURALS = { nuevo: "nuevos", modificado: "modificados", borrado: "borrados", renombrado: "renombrados" };

  function singular(label) { return SINGULARS[label] || label; }
  function plural(label) { return PLURALS[label] || label + (label.charAt(label.length - 1) === "s" ? "" : "s"); }

  var EXT_LANG = {
    py: "python",
    js: "javascript",
    mjs: "javascript",
    cjs: "javascript",
    ts: "typescript",
    tsx: "typescript",
    jsx: "javascript",
    json: "json",
    html: "html",
    htm: "html",
    xml: "xml",
    svg: "xml",
    css: "css",
    scss: "css",
    md: "markdown",
    markdown: "markdown",
    sh: "bash",
    bash: "bash",
    zsh: "bash",
    yml: "yaml",
    yaml: "yaml",
    toml: "toml",
    ini: "ini",
    sql: "sql",
    go: "go",
    rs: "rust",
    c: "c",
    cpp: "cpp",
    h: "c",
  };

  function detectLanguage(filepath) {
    if (!filepath) return "plain";
    var dot = filepath.lastIndexOf(".");
    if (dot === -1) {
      var base = filepath.slice(filepath.lastIndexOf("/") + 1).toLowerCase();
      if (base === "dockerfile") return "dockerfile";
      if (base === "makefile") return "makefile";
      return "plain";
    }
    var ext = filepath.slice(dot + 1).toLowerCase();
    return EXT_LANG[ext] || "plain";
  }

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function highlightLine(line, lang) {
    if (!line) return "";
    var commentPattern = (lang === "python" || lang === "bash" || lang === "yaml" || lang === "toml")
      ? "(#.*$)"
      : (lang === "sql" ? "(--.*$)" : "(\\/\\/.*$)");

    var keyPattern = (lang === "yaml")
      ? "(\"[^\"\\\\]*(?:\\\\.[^\"\\\\]*)*\"|'[^'\\\\]*(?:\\\\.[^'\\\\]*)*'|[a-zA-Z0-9_$-]+)(?=\\s*:)"
      : "(\"[^\"\\\\]*(?:\\\\.[^\"\\\\]*)*\"|'[^'\\\\]*(?:\\\\.[^'\\\\]*)*')(?=\\s*:)";

    var stringPattern = "(\"[^\"\\\\]*(?:\\\\.[^\"\\\\]*)*\"|'[^'\\\\]*(?:\\\\.[^'\\\\]*)*'|`[^`\\\\]*(?:\\\\.[^`\\\\]*)*`)";
    var kwdPattern = "\\b(def|class|function|return|if|elif|else|for|while|import|from|as|in|is|not|and|or|try|except|finally|with|raise|yield|async|await|const|let|var|export|default|switch|case|break|continue|new|typeof|interface|type|enum)\\b";
    var valPattern = "\\b(True|False|None|true|false|null|undefined|self|this)\\b";
    var numPattern = "\\b(\\d+(?:\\.\\d+)?)\\b";
    var fnPattern = "\\b([a-zA-Z_]\\w*)(?=\\s*\\()";

    var tokenRe = new RegExp([commentPattern, keyPattern, stringPattern, kwdPattern, valPattern, numPattern, fnPattern].join("|"), "g");

    var lastIndex = 0;
    var html = "";
    var match;

    while ((match = tokenRe.exec(line)) !== null) {
      if (match.index > lastIndex) {
        html += escapeHtml(line.slice(lastIndex, match.index));
      }
      var tok = match[0];
      if (match[1]) {
        html += '<span class="tok-cmt">' + escapeHtml(tok) + '</span>';
      } else if (match[2]) {
        html += '<span class="tok-key">' + escapeHtml(tok) + '</span>';
      } else if (match[3]) {
        html += '<span class="tok-str">' + escapeHtml(tok) + '</span>';
      } else if (match[4]) {
        html += '<span class="tok-kwd">' + escapeHtml(tok) + '</span>';
      } else if (match[5]) {
        html += '<span class="tok-val">' + escapeHtml(tok) + '</span>';
      } else if (match[6]) {
        html += '<span class="tok-num">' + escapeHtml(tok) + '</span>';
      } else if (match[7]) {
        html += '<span class="tok-fn">' + escapeHtml(tok) + '</span>';
      } else {
        html += escapeHtml(tok);
      }
      lastIndex = tokenRe.lastIndex;
    }
    if (lastIndex < line.length) {
      html += escapeHtml(line.slice(lastIndex));
    }
    return html;
  }

  var api = {
    STATUS_LABELS: STATUS_LABELS,
    UNKNOWN_STATUS_LABEL: UNKNOWN_STATUS_LABEL,
    statusLabel: statusLabel,
    formatSize: formatSize,
    formatMtime: formatMtime,
    treeRows: treeRows,
    diffFiles: diffFiles,
    statusSummary: statusSummary,
    statusBadge: statusBadge,
    detectLanguage: detectLanguage,
    highlightLine: highlightLine,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { FilesExplorer: api };
  }
  global.FilesExplorer = api;
})(typeof window !== "undefined" ? window : globalThis);
