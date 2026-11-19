/* Structural wiring pins for the model picker search + scroll (the
 * operator's 2026-11-19 report). app.js is a browser-only IIFE node
 * cannot load, so the wiring is pinned by SHAPE on the shipped
 * sources (the pending-wiring discipline); the real rendering is
 * proven in tests/browser/test_model_switch_flow.py. */

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const read = (rel) => fs.readFileSync(
  path.join(__dirname, "../../src/herdr_brain/static", rel), "utf8");
const appSrc = read("app.js");
const htmlSrc = read("index.html");
const es = require("../../src/herdr_brain/static/i18n/es.js");
const en = require("../../src/herdr_brain/static/i18n/en.js");

/* The search card is built by ONE shared helper (screen options AND the
 * full catalog), so the slice spans the helper, the catalog swap and
 * the structured painter. */
const start = appSrc.indexOf("function mountModelSearch");
assert.ok(start !== -1, "the shared search-card builder must exist");
const slice = appSrc.slice(start, appSrc.indexOf("function paintWizardStepper"));

test("the structured card builds the picker rows through the pure model", () => {
  assert.ok(slice.includes("Dialogs.modelPickerRows("),
    "the picker card must derive its rows from dialogs.js");
  assert.ok(slice.includes("Dialogs.filterModelRows("),
    "typing must filter through the pure, tested function");
});

test("the card has a search input and a bounded scrolling list", () => {
  assert.ok(slice.includes("dlg-model-search"));
  assert.ok(slice.includes("dlg-model-list"));
  assert.match(htmlSrc, /\.dlg-model-list\s*\{[^}]*overflow-y:\s*auto/);
  assert.match(htmlSrc, /\.dlg-model-list\s*\{[^}]*max-height:/);
});

test("a filtered row keeps its ORIGINAL index on the button", () => {
  assert.ok(/setAttribute\("data-index",\s*String\(option\.index\)\)/.test(slice),
    "the tap must keep sending the option's original index");
});

test("the filter never rebuilds the card (typing must not lose focus)", () => {
  const handler = slice.slice(slice.indexOf('addEventListener("input"'));
  assert.ok(handler.length > 0, "the input handler must exist");
  assert.ok(!/lastDialogIdentity\s*=/.test(handler.slice(0, 600)),
    "filtering must not reset the paint identity");
});

test("the current model is highlighted", () => {
  assert.ok(slice.includes("dlg-current"));
  assert.match(htmlSrc, /\.dlg-current/);
});

test("both dictionaries carry the search chrome", () => {
  for (const dict of [es, en]) {
    assert.ok(dict["dialogs.model_search_placeholder"]);
    assert.ok(dict["dialogs.model_search_none"]);
  }
});

/* ── the full catalog (GET /pane/models) ─────────────────────────── */

const catalogStart = appSrc.indexOf("var MODEL_CATALOG_TTL_MS");
assert.ok(catalogStart !== -1, "the catalog loader must exist");
const catalogSlice = appSrc.slice(catalogStart, appSrc.indexOf("function paintWizardStepper"));
assert.ok(catalogSlice.includes("function requestModelCatalog"));

test("an opencode picker card asks for the full catalog, on demand", () => {
  assert.ok(slice.includes("Dialogs.wantsModelCatalog("),
    "only an opencode model picker may ask for the catalog");
  assert.ok(catalogSlice.includes("Dialogs.modelCatalogUrl("),
    "the read route comes from the pure module");
  assert.ok(catalogSlice.includes("readFetch("),
    "the catalog read carries the device credential and the pairing recovery");
  assert.ok(!/\bfetch\(\s*["']\/pane\/models/.test(appSrc),
    "no boot-time fetch: the catalog loads only when the card paints");
});

test("the catalog REPLACES the screen options and keeps them as the fallback", () => {
  assert.ok(catalogSlice.includes("Dialogs.modelCatalogRows("));
  assert.match(catalogSlice, /rows\.length/,
    "an empty catalog must leave the picker's own options painted");
  assert.ok(catalogSlice.includes("mountModelSearch("),
    "the catalog list reuses the search + scrolling card");
});

test("a catalog tap sends the model name, never an option index", () => {
  assert.ok(appSrc.includes("Dialogs.catalogAnswerPayload("));
  assert.ok(appSrc.includes('button[data-catalog]'),
    "catalog rows are their own tap target (no data-index to misread)");
  const catalogButtons = catalogSlice.slice(catalogSlice.indexOf("data-catalog"));
  assert.ok(!/data-index/.test(catalogButtons.slice(0, 500)),
    "a catalog button must not carry an option index");
});

test("typed filter text survives the catalog swap", () => {
  assert.match(catalogSlice, /\.value/,
    "what the operator typed before the list arrived is kept");
});
