/* Structural wiring tests for the pending panel's move off the document
 * flow into the Ajustes launcher + a dedicated bottom sheet
 * (pending-notices-in-settings-sheet).
 *
 * LIMITS, stated plainly: app.js is a single browser-only IIFE (DOM,
 * EventSource, live audio element) that node --test cannot load, so the
 * mount and the open/close wiring cannot be EXECUTED here. These tests
 * pin the wiring SHAPE by reading the shipped sources as text — exactly
 * the discipline of announce-wiring.test.js. Real open/close and the
 * geometry of the list inside the sheet are device territory and live
 * in tests/e2e/test_m3_pending.py.
 *
 * The regression this file guards: the panel used to be inserted into
 * the document flow (consultPanel.parentNode.insertBefore), so its
 * appear/disappear pushed the whole cockpit. Moving it inside an inert,
 * transform-hidden sheet is what removes that layout shift.
 */

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const appSrc = fs.readFileSync(
  path.join(__dirname, "../../src/herdr_brain/static/app.js"), "utf8");
const htmlSrc = fs.readFileSync(
  path.join(__dirname, "../../src/herdr_brain/static/index.html"), "utf8");

/* The settings sheet slice isolates the Ajustes body so the launcher is
 * pinned where it belongs, not merely somewhere in the document. */
const settingsStart = htmlSrc.indexOf('id="settings-sheet"');
const settingsSlice = htmlSrc.slice(
  settingsStart, htmlSrc.indexOf("</section>", settingsStart));

test("the pending panel mounts inside the sheet, off the document flow", () => {
  assert.notEqual(settingsStart, -1, "the settings sheet must exist");
  assert.ok(appSrc.includes("pendingSheetBody.appendChild(pendingSection)"),
    "the panel must be mounted into #pending-sheet-body");
  assert.ok(!appSrc.includes("insertBefore(pendingSection"),
    "the panel must no longer be inserted into the document flow — that " +
    "in-flow insertion is what shifted the layout");
});

test("the Ajustes sheet carries the Pendientes launcher and its count", () => {
  assert.ok(settingsSlice.includes('id="pending-open"'),
    "the Pendientes launcher must live inside the Ajustes sheet");
  assert.ok(settingsSlice.includes('id="pending-menu-count"'),
    "the launcher must carry the pending count badge");
});

test("a dedicated pending sheet and backdrop exist", () => {
  assert.ok(htmlSrc.includes('id="pending-sheet"'), "missing #pending-sheet");
  assert.ok(htmlSrc.includes('id="pending-sheet-body"'),
    "missing #pending-sheet-body mount");
  assert.ok(htmlSrc.includes('id="pending-backdrop"'),
    "missing #pending-backdrop");
});

test("app.js wires the launcher open and the sheet close", () => {
  assert.ok(appSrc.includes('pendingOpenBtn.addEventListener("click"'),
    "the Ajustes launcher must open the pending sheet");
  assert.ok(appSrc.includes("function openPendingSheet"),
    "missing openPendingSheet");
  assert.ok(appSrc.includes("function closePendingSheet"),
    "missing closePendingSheet");
  assert.ok(appSrc.includes("function setPendingSheet"),
    "missing setPendingSheet");
});
