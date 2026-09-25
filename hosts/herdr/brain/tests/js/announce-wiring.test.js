/* Structural wiring tests for the pumpAudio play()-rejection path (T3,
 * PRD announcements-without-call FR-04/AC3).
 *
 * LIMITS, stated plainly: app.js is a single browser-only IIFE (DOM,
 * EventSource, live audio element) that node --test cannot load, so the
 * rejection handler cannot be EXECUTED here. These tests pin the wiring
 * SHAPE of the catch block — stale guard, out-of-call announcer
 * notification, in-call hideToast preservation, stop-button cleanup —
 * while tests/js/announce.test.js exercises the pure policy the wiring
 * delegates to. Real autoplay-rejection behavior is device territory
 * (T5, AC3). */

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const appSrc = fs.readFileSync(
  path.join(__dirname, "../../src/herdr_brain/static/app.js"), "utf8");

/* The rejection handler lives between the play() promise wiring and
 * onAudioEnded; slicing between those markers isolates exactly the
 * catch body pumpAudio owns. */
const start = appSrc.indexOf("var pending = player.play();");
const end = appSrc.indexOf("function onAudioEnded");
assert.ok(start !== -1 && end !== -1 && start < end,
  "pumpAudio rejection region found");
const region = appSrc.slice(start, end);

test("stale rejection guard runs first: superseded items are ignored", () => {
  assert.ok(region.includes("if (audioFinished !== item) return;"),
    "the catch must bail before ANY side effect when the rejecting item " +
    "was superseded (stop button, error event, or a newer queue item)");
});

test("out-of-call announcement rejection notifies the announcer (FR-04)", () => {
  assert.ok(region.includes("announcer.onPlayRejected(item.announcement)"),
    "the announcer enters blocked and re-shows the persistent toast");
  assert.ok(region.includes("item.announcement && !inCall"),
    "the notification branch is out-of-call only");
});

test("the old unconditional announcement hideToast is gone; in-call keeps it", () => {
  assert.ok(!region.includes("if (item.announcement) hideToast();"),
    "the defect (app.js:1294-1296 @ ab20f8d — text lost) must not survive");
  assert.ok(/else if \(item\.announcement\)[\s\S]{0,40}hideToast\(\)/.test(region),
    "in-call rejections keep the existing hideToast (FR-08)");
});

test("drained queue hides the stop button out of call", () => {
  assert.ok(region.includes("!audioBusy && !audioQueue.length && !inCall"),
    "the cleanup fires only out of call (in-call stays as today)");
  assert.ok(region.includes('stopBtn.classList.add("hidden")'),
    "an empty queue after rejection must not leave Parar audio over the " +
    "persistent toast (a tap would stopAudio() and hide the saved text)");
});

test("in-call mic resume logic is preserved (pin)", () => {
  assert.ok(region.includes('inCall && callState === "speaking"'),
    "the dead-mic resume contract stays");
});
