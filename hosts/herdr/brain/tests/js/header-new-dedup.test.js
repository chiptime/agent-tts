/* Structural wiring tests for the header ✚ dedup (2026-11-19): the
 * user read the header ✚ (#new-open, opened the launch sheet) and the
 * conversation sheet's "＋ Nueva conversación" (#conv-new, POSTed
 * /reset of the call conversation) as DUPLICATES. ONE entry stays:
 * #conv-new, now opening the LAUNCH SHEET (the new-session sheet).
 *
 * LIMITS, stated plainly: app.js is a single browser-only IIFE that
 * node --test cannot load, so the open wiring is pinned by SHAPE on
 * the shipped sources — the pending-wiring discipline. The mid-call
 * reset/fresh-call flow must SURVIVE somewhere reachable: it lives on
 * #call-new ("Nueva llamada", the drawer-head ✚), which does the
 * same POST /reset and dials a fresh call. */

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const appSrc = fs.readFileSync(
  path.join(__dirname, "../../src/herdr_brain/static/app.js"), "utf8");
const htmlSrc = fs.readFileSync(
  path.join(__dirname, "../../src/herdr_brain/static/index.html"), "utf8");

/* The conversation sheet slice isolates #conv-new so the entry point is
 * pinned where it belongs, not merely somewhere in the document. */
const convStart = htmlSrc.indexOf('id="conv-sheet"');
const convSlice = htmlSrc.slice(
  convStart, htmlSrc.indexOf("</section>", convStart));

test("the header ✚ is gone: one new-session entry, not two", () => {
  assert.ok(!htmlSrc.includes('id="new-open"'),
    "the duplicate header entry must not exist — #conv-new is THE entry");
  assert.ok(!appSrc.includes('newOpenBtn'),
    "the removed button's wiring must not survive either");
});

test("the conversation sheet still carries the Nueva conversación row", () => {
  assert.notEqual(convStart, -1, "the conversation sheet must exist");
  assert.ok(convSlice.includes('id="conv-new"'),
    "the sheet's action row must stay: it is the one surviving entry");
});

test("#conv-new opens the launch sheet, never a POST /reset", () => {
  assert.ok(
    /convNewBtn\.addEventListener\("click", openNewSheet\)/.test(appSrc),
    "#conv-new must open the launch sheet (openNewSheet), like the old ✚ did",
  );
  const wiringAt = appSrc.indexOf('convNewBtn.addEventListener');
  const wiringSlice = appSrc.slice(wiringAt, wiringAt + 400);
  assert.ok(!wiringSlice.includes('"/reset"'),
    "#conv-new must no longer reset the call conversation");
  assert.ok(!appSrc.includes('t("herd.new_started"'),
    "the reset-only toast key must not be wired to anything");
});

test("the mid-call reset/fresh-call flow survives on #call-new", () => {
  assert.ok(htmlSrc.includes('id="call-new"'),
    "the drawer-head Nueva llamada button must stay");
  const callAt = appSrc.indexOf("callNewBtn.addEventListener");
  /* +30 skips this very `callNewBtn.addEventListener` occurrence. */
  const callSlice = appSrc.slice(callAt, appSrc.indexOf("addEventListener", callAt + 30));
  assert.ok(callSlice.includes('"/reset"'),
    "the fresh-call path must still POST /reset (it is now the ONLY reset trigger)");
  assert.ok(callSlice.includes("startCall()"),
    "the fresh-call path must still dial a new call after the reset");
});
