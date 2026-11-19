/* node --test suite for the "New" session sheet (parity-f7c S1, doc 03
 * §1–§2 — the PWA half).
 *
 * app.js is a single browser-only IIFE that node --test cannot load, so
 * the sheet's PURE model (folder list building, form validation gating
 * the Launch button, the destructive-task guard wiring) lives in one
 * explicitly marked block that this file extracts and evaluates — the
 * same discipline as the wiring suites, extended with real execution of
 * the pure parts. The DOM wiring itself is pinned by shape: the sheet
 * must post /session/new through withDeviceAuth, reuse the composer's
 * destructive second tap (reply.destructive_confirm) and ride the dock
 * launcher functions for the shortcuts, never a duplicate flow. */

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const staticDir = path.join(__dirname, "../../src/herdr_brain/static");
const appSrc = fs.readFileSync(path.join(staticDir, "app.js"), "utf8");
const htmlSrc = fs.readFileSync(path.join(staticDir, "index.html"), "utf8");
const esDict = require(path.join(staticDir, "i18n/es.js"));
const enDict = require(path.join(staticDir, "i18n/en.js"));

/* ---- extract and evaluate the pure block ---- */

function loadModel() {
  const START = "/* @new-session-pure-start */";
  const END = "/* @new-session-pure-end */";
  const start = appSrc.indexOf(START);
  const end = appSrc.indexOf(END);
  assert.notEqual(start, -1, "app.js must mark the pure model block");
  assert.notEqual(end, -1, "app.js must close the pure model block");
  const block = appSrc.slice(start + START.length, end);
  /* The block declares the factory; evaluate it and build the model. */
  const factory = new Function(block + "\nreturn newSessionModel();");
  return factory();
}

const model = loadModel();

/* ---- folderChoices: roots + recents, deduped, labeled ---- */

test("roots render with explicit labels, basename fallback", () => {
  const choices = model.folderChoices(
    [
      { path: "/srv/work", label: "Trabajo" },
      { path: "/srv/code/personal", label: "" },
    ],
    [],
  );
  assert.deepEqual(choices.roots, [
    { path: "/srv/work", label: "Trabajo", kind: "root" },
    { path: "/srv/code/personal", label: "personal", kind: "root" },
  ]);
  assert.deepEqual(choices.recents, []);
});

test("recents are deduped and a recent equal to a root is not repeated", () => {
  const choices = model.folderChoices(
    [{ path: "/srv/work", label: "" }],
    [
      { cwd: "/srv/work" },            // already a root: dropped
      { cwd: "/srv/work/proj" },
      { cwd: "/srv/work/proj" },       // duplicate: kept once
      { cwd: "/srv/other" },           // server already filtered, but a
                                       // proxy could lie: kept as-is (the
                                       // server re-validates on launch)
    ],
  );
  assert.deepEqual(choices.recents, [
    { path: "/srv/work/proj", label: "proj", kind: "recent" },
    { path: "/srv/other", label: "other", kind: "recent" },
  ]);
});

test("malformed rows never become buttons", () => {
  const choices = model.folderChoices(
    [{ path: "/srv/ok", label: "" }, { label: "no path" }, "junk"],
    [{ cwd: "" }, 7, null, { cwd: "/srv/ok/deep" }],
  );
  assert.deepEqual(choices.roots, [{ path: "/srv/ok", label: "ok", kind: "root" }]);
  assert.deepEqual(choices.recents, [
    { path: "/srv/ok/deep", label: "deep", kind: "recent" },
  ]);
});

/* ---- formState: the Launch button gate ---- */

test("launch requires a harness and a cwd", () => {
  assert.equal(model.formState({ harness: "", cwd: "" }).launchable, false);
  assert.equal(model.formState({ harness: "claude", cwd: "" }).launchable, false);
  assert.equal(model.formState({ harness: "", cwd: "/srv/work" }).launchable, false);
  assert.equal(
    model.formState({ harness: "claude", cwd: "/srv/work" }).launchable,
    true,
  );
  /* No roots configured: nothing can be launched from the sheet, no
   * matter what the client thinks it selected. */
  assert.equal(
    model.formState({ harness: "claude", cwd: "/srv/work", rootsConfigured: false })
      .launchable,
    false,
  );
});

/* ---- taskGuard: the destructive second tap, same as the composer ---- */

test("a clean task needs no confirm", () => {
  const guard = model.taskGuard("corre los tests", (t) => (/rm -rf/.test(t) ? "rm -rf" : null));
  assert.deepEqual(guard, { confirm: false });
});

test("a destructive task demands the second tap with the match", () => {
  const guard = model.taskGuard("rm -rf /srv/work", (t) => (/rm -rf/.test(t) ? "rm -rf" : null));
  assert.deepEqual(guard, { confirm: true, match: "rm -rf" });
});

test("an empty task and a missing analyzer never block the launch", () => {
  assert.deepEqual(model.taskGuard("", (t) => "always"), { confirm: false });
  assert.deepEqual(model.taskGuard("   ", (t) => "always"), { confirm: false });
  assert.deepEqual(model.taskGuard("rm -rf /", null), { confirm: false });
});

/* ---- mintRequestId: one id per launch tap (S2, doc 03 §4) ---- */

test("the request id rides crypto's uuid when the page offers one", () => {
  assert.equal(
    model.mintRequestId(() => "11111111-2222-4333-8444-555555555555", 1000, 0.5),
    "11111111-2222-4333-8444-555555555555",
  );
});

test("a missing or throwing randomUUID falls back to time+random", () => {
  /* 1000 → "rs" and 0.5 → "0.i" in base 36: the fallback is a plain
   * ns-<time>-<rand> shape — client-uniqueness is all idempotency
   * needs, not cryptographic identity. */
  assert.equal(model.mintRequestId(null, 1000, 0.5), "ns-rs-i");
  assert.equal(
    model.mintRequestId(() => { throw new Error("insecure context"); }, 1000, 0.5),
    "ns-rs-i",
  );
});

/* ---- resumeRows: the "Retomar" list (parity-f7c S3) ---- */

test("resume rows keep well-formed sessions and drop malformed ones", () => {
  const rows = model.resumeRows([
    { session_id: "abc-123", title: "Fix login", cwd: "/srv/work/api" },
    { session_id: "def-456", title: "", cwd: "/srv/other" },  // label falls back
    { session_id: "", title: "x", cwd: "/srv" },              // no id: dropped
    { title: "no id", cwd: "/srv" },                          // dropped
    null, "junk", 7,                                           // dropped
    { session_id: "ghi-789" },                                 // no cwd: kept, empty cwd
  ]);
  assert.deepEqual(rows, [
    { session_id: "abc-123", label: "Fix login", cwd: "/srv/work/api" },
    { session_id: "def-456", label: "other · def-456", cwd: "/srv/other" },
    { session_id: "ghi-789", label: "ghi-789", cwd: "" },
  ]);
});

test("the fallback label uses the folder name and a short id", () => {
  const rows = model.resumeRows([
    { session_id: "0123456789abcdef", cwd: "/srv/work/deep/proj" },
  ]);
  assert.equal(rows[0].label, "proj · 01234567");
});

test("a title is trimmed before it wins over the fallback", () => {
  const rows = model.resumeRows([
    { session_id: "abc", title: "   ", cwd: "/srv/x" },
    { session_id: "def", title: "  Kept  ", cwd: "/srv/x" },
  ]);
  assert.equal(rows[0].label, "x · abc");
  assert.equal(rows[1].label, "Kept");
});

/* ---- wiring pins (shape, the pending-wiring discipline) ---- */

test("the sheet and its entry exist in the shell", () => {
  /* Header ✚ dedup (2026-11-19): #new-open left the header — the
   * conversation sheet's "Nueva conversación" row is the sheet's ONE
   * entry (see header-new-dedup.test.js). */
  for (const id of [
    "conv-new", "new-sheet", "new-backdrop", "new-close",
    "new-harness", "new-folders", "new-title", "new-task", "new-launch",
    "new-launchers", "new-roots-empty",
    "new-resume-harness", "new-resume-list",
  ]) {
    assert.ok(htmlSrc.includes(`id="${id}"`), `index.html must carry #${id}`);
  }
  assert.ok(!htmlSrc.includes('id="new-open"'), "the header ✚ is gone");
});

test("the launch posts /session/new through withDeviceAuth", () => {
  assert.ok(appSrc.includes('"/session/new"'));
  assert.ok(/fetch\("\/session\/new",\s*withDeviceAuth\(/.test(appSrc),
    "the launch must ride withDeviceAuth like every write");
});

test("the launch body mints and carries a request_id per tap", () => {
  assert.ok(/request_id:\s*nsModel\.mintRequestId\(/.test(appSrc),
    "the /session/new body must mint a fresh request_id on every Launch tap");
});

test("the destructive guard reuses the composer's confirm copy", () => {
  assert.ok(appSrc.includes('t("reply.destructive_confirm"'),
    "the sheet must reuse the composer's destructive confirm key");
});

test("the Launch button is gated by the pure form state", () => {
  assert.ok(appSrc.includes("newLaunchBtn.disabled"),
    "the wiring must disable the Launch button from the form state");
});

test("launcher shortcuts ride the dock functions, never a duplicate flow", () => {
  assert.ok(/OperatorDock\.launcherTap/.test(appSrc),
    "the arm/send decision must be OperatorDock.launcherTap");
  assert.ok(/OperatorDock\.launchOutcome/.test(appSrc),
    "the outcome handling must be OperatorDock.launchOutcome");
  assert.ok(appSrc.includes('"/operator/launch"'));
});

/* ---- resume wiring pins (parity-f7c S3) ---- */

test("the resume list fetches /sessions/recent through the read path", () => {
  assert.ok(/readFetch\("\/sessions\/recent\?harness="\s*\+/.test(appSrc),
    "the recent list must ride readFetch like every read");
});

test("a resume tap posts /session/resume through withDeviceAuth", () => {
  assert.ok(/fetch\("\/session\/resume",\s*withDeviceAuth\(/.test(appSrc),
    "the resume must ride withDeviceAuth like every write");
});

test("each resume tap mints its own request id", () => {
  assert.ok(/request_id:\s*nsModel\.mintRequestId\(/.test(appSrc) ||
    /resumeRequestId|mintRequestId/.test(appSrc),
    "every resume tap must mint a fresh request_id (S2 receipts)");
});

test("only server-offered resume harnesses become options", () => {
  /* The harness picker renders exactly what /sessions/recent's
   * `harnesses` array said — a disabled harness has no code path that
   * could add it locally. */
  assert.ok(/renderNewResumeHarnesses\(body\.harnesses \|\| \[\]\)/.test(appSrc),
    "the picker must render the server's enabled list verbatim");
});

/* ---- spaceRows: every workspace is offered (parity-f7c S1b, L11) ---- */

test("space rows list EVERY workspace, panes or not", () => {
  const rows = model.spaceRows([
    { workspace_id: "w1", label: "main", cwd: "/srv/work", inside_roots: true },
    { workspace_id: "w9", label: "vacía", cwd: null, inside_roots: false },
  ]);
  assert.deepEqual(rows, [
    {
      workspace_id: "w1", label: "main", cwd: "/srv/work",
      pickable: true, reason: "", kind: "space",
    },
    {
      workspace_id: "w9", label: "vacía", cwd: null,
      pickable: false, reason: "cwd_unknown", kind: "space",
    },
  ]);
});

test("a known cwd outside the roots is listed but unpickable", () => {
  const rows = model.spaceRows([
    { workspace_id: "w2", label: "lab", cwd: "/srv/elsewhere", inside_roots: false },
  ]);
  assert.equal(rows[0].pickable, false);
  assert.equal(rows[0].reason, "outside_roots");
  assert.equal(rows[0].cwd, "/srv/elsewhere");
});

test("malformed space rows never become buttons", () => {
  const rows = model.spaceRows([
    null, "junk", 7,
    { label: "no id" },
    { workspace_id: "" , label: "empty id" },
    { workspace_id: "w3", label: "", cwd: "/srv/work", inside_roots: true },
  ]);
  assert.deepEqual(rows, [{
    workspace_id: "w3", label: "w3", cwd: "/srv/work",
    pickable: true, reason: "", kind: "space",
  }]);
});

test("an absent spaces array degrades to no rows", () => {
  assert.deepEqual(model.spaceRows(undefined), []);
  assert.deepEqual(model.spaceRows(null), []);
});

/* ---- space wiring pins (S1b) ---- */

test("the sheet renders the server's spaces alongside roots and recents", () => {
  assert.ok(
    /renderNewFolders\(body\.roots \|\| \[\], body\.recents \|\| \[\], body\.spaces \|\| \[\]\)/.test(appSrc),
    "renderNewFolders must receive the /session/roots spaces",
  );
  assert.ok(/t\("newsession\.folder_spaces"\)/.test(appSrc),
    "the Espacios group must carry its own i18n key");
});

test("a space selection sets both cwd and workspace_id", () => {
  assert.ok(/newSessionState\.workspaceId = /.test(appSrc),
    "selecting a space must set newSessionState.workspaceId");
  assert.ok(/newSessionState\.workspaceId = ""/.test(appSrc),
    "selecting a root/recent must clear the workspace targeting");
});

test("an unpickable space renders disabled with its reason badge", () => {
  assert.ok(/btn\.disabled = true/.test(appSrc),
    "spaces without a pickable cwd must render disabled");
  assert.ok(/newsession\.space_cwd_unknown|newsession\.space_outside_roots/.test(appSrc),
    "the reason badge must ride the i18n keys");
});

test("the launch body carries the picked workspace_id", () => {
  assert.ok(/workspace_id:\s*newSessionState\.workspaceId \|\| null/.test(appSrc),
    "POST /session/new must send the picked space's workspace_id (null when none)");
});

/* ---- i18n: every new key exists in both locales ---- */

const NEW_KEYS = [
  "newsession.aria", "newsession.title", "newsession.harness",
  "newsession.harness_pick", "newsession.folder", "newsession.folder_roots",
  "newsession.folder_recents", "newsession.title_label", "newsession.task",
  "newsession.task_placeholder", "newsession.launch", "newsession.launched",
  "newsession.launch_failed", "newsession.err_harness", "newsession.err_cwd",
  "newsession.roots_empty",
  "newsession.resume", "newsession.resume_pick", "newsession.resume_empty",
  "newsession.resumed", "newsession.resume_failed",
  "newsession.err_resume_harness", "newsession.err_resume_cwd",
  "newsession.folder_spaces", "newsession.space_cwd_unknown",
  "newsession.space_outside_roots",
];

test("es and en carry the full newsession key set", () => {
  for (const key of NEW_KEYS) {
    assert.ok(key in esDict, `es.js must define ${key}`);
    assert.ok(key in enDict, `en.js must define ${key}`);
  }
});
