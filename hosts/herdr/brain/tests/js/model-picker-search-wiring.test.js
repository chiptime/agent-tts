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

const start = appSrc.indexOf("function paintStructuredDialog");
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
