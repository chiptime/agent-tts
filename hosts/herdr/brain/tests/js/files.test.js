/* Workspace file explorer (W3-P2 S3/S5): the pure model behind the
 * «Ficheros» tab — size/mtime formatting, git status labels, the tree
 * render model and the per-file diff split. No DOM here (the same
 * discipline as send-track.test.js); app.js owns the wiring. */

const test = require("node:test");
const assert = require("node:assert");

const mod = require("../../src/herdr_brain/static/files.js");
const Files = mod.FilesExplorer;

/* ---- status labels --------------------------------------------------- */

test("every porcelain status code maps to its label", () => {
  assert.equal(Files.statusLabel("M"), "modificado");
  assert.equal(Files.statusLabel("A"), "nuevo");
  assert.equal(Files.statusLabel("D"), "borrado");
  assert.equal(Files.statusLabel("R"), "renombrado");
  assert.equal(Files.statusLabel("??"), "sin seguimiento");
});

test("the index column wins for staged+dirty entries", () => {
  // "MM" = staged AND worktree modification; the first column decides.
  assert.equal(Files.statusLabel("MM"), "modificado");
  assert.equal(Files.statusLabel("AM"), "nuevo");
});

test("an unknown status code falls back to honest copy", () => {
  const label = Files.statusLabel("X");
  assert.ok(label && label.length >= 4);
  assert.ok(!/^[A-Z?]$/.test(label));
});

/* ---- formatting ------------------------------------------------------- */

test("sizes render in human units", () => {
  assert.equal(Files.formatSize(0), "0 B");
  assert.equal(Files.formatSize(512), "512 B");
  assert.equal(Files.formatSize(1024), "1.0 KB");
  assert.equal(Files.formatSize(1536), "1.5 KB");
  assert.equal(Files.formatSize(1024 * 1024), "1.0 MB");
});

test("size garbage renders empty, never NaN", () => {
  assert.equal(Files.formatSize(undefined), "");
  assert.equal(Files.formatSize(-5), "");
  assert.equal(Files.formatSize("abc"), "");
});

test("mtimes render as local YYYY-MM-DD HH:MM", () => {
  // 2026-11-26 00:30 UTC; the label follows the runtime timezone, so
  // pin shape, not a wall-clock string.
  const label = Files.formatMtime(Date.UTC(2026, 10, 26, 0, 30) / 1000);
  assert.match(label, /^2026-11-2\d \d\d:\d\d$/);
});

test("mtime garbage renders empty", () => {
  assert.equal(Files.formatMtime(undefined), "");
  assert.equal(Files.formatMtime(0), "");
});

/* ---- tree render model ------------------------------------------------ */

test("treeRows flatten entries with depth, name and labels", () => {
  const rows = Files.treeRows([
    { path: "src", type: "dir", size: 0, mtime: 0 },
    { path: "src/main.py", type: "file", size: 2048, mtime: 1764100000 },
    { path: "readme.md", type: "file", size: 10, mtime: 1764100000 },
  ]);
  assert.equal(rows.length, 3);
  const src = rows[0];
  assert.deepEqual(
    { name: src.name, type: src.type, depth: src.depth, size: src.size },
    { name: "src", type: "dir", depth: 0, size: "" },
  );
  const main = rows[1];
  assert.equal(main.name, "main.py");
  assert.equal(main.depth, 1);
  assert.equal(main.size, "2.0 KB");
});

test("treeRows tolerates a missing or empty payload", () => {
  assert.deepEqual(Files.treeRows(null), []);
  assert.deepEqual(Files.treeRows([]), []);
});

/* ---- per-file diff split ---------------------------------------------- */

const TWO_FILE_DIFF = [
  "diff --git a/app.py b/app.py",
  "index 111..222 100644",
  "--- a/app.py",
  "+++ b/app.py",
  "@@ -1 +1 @@",
  "-print('app')",
  "+print('changed')",
  "diff --git a/new.txt b/new.txt",
  "new file mode 100644",
  "index 000..333",
  "--- /dev/null",
  "+++ b/new.txt",
  "@@ -0,0 +1 @@",
  "+hello",
].join("\n");

test("diffFiles splits a multi-file diff into named chunks", () => {
  const chunks = Files.diffFiles(TWO_FILE_DIFF);
  assert.equal(chunks.length, 2);
  assert.equal(chunks[0].file, "app.py");
  assert.equal(chunks[1].file, "new.txt");
  assert.ok(chunks[0].body.startsWith("diff --git a/app.py"));
  assert.ok(chunks[1].body.includes("+hello"));
});

test("diffFiles keeps the full body so context lines survive", () => {
  const chunks = Files.diffFiles(TWO_FILE_DIFF);
  assert.ok(chunks[0].body.includes("-print('app')"));
  assert.ok(chunks[0].body.includes("@@ -1 +1 @@"));
  assert.equal(chunks[0].additions, 1);
  assert.equal(chunks[0].deletions, 1);
  assert.ok(chunks[0].parsedLines.some(l => l.kind === "add" && l.text === "+print('changed')"));
  assert.ok(chunks[0].parsedLines.some(l => l.kind === "del" && l.text === "-print('app')"));
  assert.ok(chunks[0].parsedLines.some(l => l.kind === "hunk" && l.text === "@@ -1 +1 @@"));
});

test("statusBadge returns letter and class for porcelain statuses", () => {
  assert.deepEqual(Files.statusBadge("M"), { code: "M", cls: "diff-badge-m" });
  assert.deepEqual(Files.statusBadge("A"), { code: "A", cls: "diff-badge-a" });
  assert.deepEqual(Files.statusBadge("D"), { code: "D", cls: "diff-badge-d" });
  assert.deepEqual(Files.statusBadge("??"), { code: "?", cls: "diff-badge-u" });
});

test("a rename is named by its new path", () => {
  const diff = [
    "diff --git a/old.txt b/new.txt",
    "similarity index 90%",
    "rename from old.txt",
    "rename to new.txt",
    "--- a/old.txt",
    "+++ b/new.txt",
  ].join("\n");
  const chunks = Files.diffFiles(diff);
  assert.equal(chunks.length, 1);
  assert.equal(chunks[0].file, "new.txt");
});

test("empty and garbage diffs yield no chunks", () => {
  assert.deepEqual(Files.diffFiles(""), []);
  assert.deepEqual(Files.diffFiles(null), []);
  assert.deepEqual(Files.diffFiles("not a diff"), []);
});

/* ---- status summary ---------------------------------------------------- */

test("statusSummary counts by label", () => {
  const line = Files.statusSummary([
    { status: "M", path: "a" },
    { status: "M", path: "b" },
    { status: "??", path: "c" },
  ]);
  assert.equal(line, "2 modificados, 1 sin seguimiento");
});

test("statusSummary of a clean repo is empty", () => {
  assert.equal(Files.statusSummary([]), "");
  assert.equal(Files.statusSummary(null), "");
});

/* ---- syntax highlighting & language detection ------------------------- */

test("detectLanguage identifies extensions and special files", () => {
  assert.equal(Files.detectLanguage("main.py"), "python");
  assert.equal(Files.detectLanguage("src/app.js"), "javascript");
  assert.equal(Files.detectLanguage("types.ts"), "typescript");
  assert.equal(Files.detectLanguage("Dockerfile"), "dockerfile");
  assert.equal(Files.detectLanguage("unknown.xyz"), "plain");
  assert.equal(Files.detectLanguage(null), "plain");
});

test("highlightLine highlights keywords, strings, comments and numbers", () => {
  const py = Files.highlightLine("def hello(self, count=42): # greeting", "python");
  assert.match(py, /class="tok-kwd">def</);
  assert.match(py, /class="tok-fn">hello</);
  assert.match(py, /class="tok-val">self</);
  assert.match(py, /class="tok-num">42</);
  assert.match(py, /class="tok-cmt"># greeting</);

  const js = Files.highlightLine("const msg = 'hi'; // note", "javascript");
  assert.match(js, /class="tok-kwd">const</);
  assert.match(js, /class="tok-str">&#39;hi&#39;</);
  assert.match(js, /class="tok-cmt">\/\/ note</);
});
