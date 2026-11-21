/* node --test suite for the conversation sheet's Espacios section
 * (parity-f7f S4, audit 03 §5 — the PWA half).
 *
 * app.js is a single browser-only IIFE that node --test cannot load, so
 * the section's PURE model (row shaping from the /session/roots spaces,
 * the capability explanation branch) lives in one explicitly marked
 * block that this file extracts and evaluates — the same discipline as
 * new-session.test.js. The DOM wiring is pinned by shape: the section
 * must fetch the SAME /session/roots payload the New sheet fetches,
 * post /workspace/focus through withDeviceAuth, toast, close the sheet
 * and re-run the chip-tap refresh; a mux with no herdr child renders
 * the honest explanation from the declared capabilities instead of
 * rows. */

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const staticDir = path.join(__dirname, "../../src/herdr_brain/static");
const appSrc = fs.readFileSync(path.join(staticDir, "app.js"), "utf8");
const esDict = require(path.join(staticDir, "i18n/es.js"));
const enDict = require(path.join(staticDir, "i18n/en.js"));

/* ---- extract and evaluate the pure block ---- */

function loadModel() {
  const START = "/* @spaces-pure-start */";
  const END = "/* @spaces-pure-end */";
  const start = appSrc.indexOf(START);
  const end = appSrc.indexOf(END);
  assert.notEqual(start, -1, "app.js must mark the spaces pure model block");
  assert.notEqual(end, -1, "app.js must close the spaces pure model block");
  const block = appSrc.slice(start + START.length, end);
  const factory = new Function(block + "\nreturn spacesSwitchModel();");
  return factory();
}

const model = loadModel();

/* ---- section(): rows vs explanation vs hidden ---- */

test("herdr-backed mux renders one row per space with the focused flag", () => {
  const section = model.section(
    [
      { workspace_id: "w1", label: "Main", cwd: "/srv/work", focused: true },
      { workspace_id: "w2", label: "", cwd: null, focused: false },
    ],
    { name: "herdr", children: ["herdr"] },
  );
  assert.equal(section.mode, "rows");
  assert.deepEqual(section.rows, [
    { workspace_id: "w1", label: "Main", cwd: "/srv/work", focused: true },
    { workspace_id: "w2", label: "w2", cwd: "", focused: false },
  ]);
});

test("a composite with a herdr child still renders rows", () => {
  const section = model.section([], { name: "composite", children: ["herdr", "tmux"] });
  assert.equal(section.mode, "rows");
  assert.deepEqual(section.rows, []);
});

test("a mux with no herdr child explains from the declared children", () => {
  const section = model.section(
    [{ workspace_id: "w1", label: "Main" }],
    { name: "composite", children: ["tmux", "zellij"] },
  );
  assert.equal(section.mode, "unsupported");
  assert.equal(section.names, "tmux, zellij");
});

test("a bare alternate names itself in the explanation", () => {
  const section = model.section([], { name: "tmux", children: ["tmux"] });
  assert.equal(section.mode, "unsupported");
  assert.equal(section.names, "tmux");
});

test("an absent mux summary hides the section entirely", () => {
  /* Older payloads carry no mux field: nothing is claimed either way. */
  assert.equal(model.section([], undefined).mode, "hidden");
  assert.equal(model.section([], null).mode, "hidden");
  assert.equal(model.section([{ workspace_id: "w1" }], {}).mode, "hidden");
});

test("malformed spaces never become rows", () => {
  const section = model.section(
    [null, "junk", 7, { label: "no id" }, { workspace_id: "", label: "empty" }],
    { name: "herdr", children: ["herdr"] },
  );
  assert.equal(section.mode, "rows");
  assert.deepEqual(section.rows, []);
});

/* ---- wiring pins (shape, the new-session discipline) ---- */

test("the section fetches the SAME /session/roots payload on sheet open", () => {
  assert.ok(/readFetch\("\/session\/roots"\)/.test(appSrc),
    "the spaces section must reuse /session/roots — never a second endpoint");
});

test("a space tap posts /workspace/focus through withDeviceAuth", () => {
  assert.ok(/fetch\("\/workspace\/focus",\s*withDeviceAuth\(/.test(appSrc),
    "the focus must ride withDeviceAuth like every write");
});

test("a successful focus toasts, closes the sheet and refreshes like a chip tap", () => {
  assert.ok(/spaces\.focused/.test(appSrc),
    "the toast must ride the spaces.focused i18n key");
  assert.ok(/closeConvSheet\(\)/.test(appSrc), "the sheet must close");
  assert.ok(/refreshState\(\)/.test(appSrc),
    "the chip-tap refresh set must re-run after a focus");
});

test("the explanation and the current marker ride their i18n keys", () => {
  assert.ok(/spaces\.unsupported/.test(appSrc),
    "the capability explanation must ride spaces.unsupported");
  assert.ok(/spaces\.current/.test(appSrc),
    "the current space badge must ride spaces.current");
});

/* ---- i18n: every new key exists in both locales ---- */

const NEW_KEYS = [
  "spaces.title", "spaces.current", "spaces.empty", "spaces.focused",
  "spaces.focus_failed", "spaces.unsupported",
];

test("es and en carry the full spaces key set", () => {
  for (const key of NEW_KEYS) {
    assert.ok(key in esDict, `es.js must define ${key}`);
    assert.ok(key in enDict, `en.js must define ${key}`);
  }
});

test("the Spanish copy matches the sheet's tone", () => {
  assert.equal(esDict["spaces.title"], "Espacios");
  assert.equal(esDict["spaces.focused"], "Espacio activo: {name}");
  assert.ok(esDict["spaces.unsupported"].includes("{names}"));
  assert.ok(esDict["spaces.unsupported"].includes("espacios de herdr"));
});
