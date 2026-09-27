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

/* ---- reopened T3 edge (parent review, fixed in the T4 unit) ----
 * After the FIRST out-of-call rejection, pumpAudio must not blindly
 * attempt announcements that were already queued: each would burn a
 * play() attempt under the blocked policy. They must be dropped with
 * their text shown (the newest stays as the visible persistent toast)
 * and never attempted later; non-announcement items keep today's path.
 * The module semantics each dropped text relies on (persistent re-show,
 * affordance fires once) are executable-tested in announce.test.js. */

test("rejection drops already-queued announcements with text preserved", () => {
  assert.ok(region.includes("queued.announcement) dropped.push(queued.announcement)"),
    "queued announcements are collected for text-preserving drop");
  assert.ok(region.includes("kept.push(queued)"),
    "non-announcement items keep their queue order and path");
  assert.ok(region.includes("announcer.onPlayRejected(dropped[dj])"),
    "each dropped announcement's text is shown; newest ends visible");
});

/* ---- T4: voice unlock affordance and gesture wiring ----
 * The affordance block lives in the announcements section of app.js
 * (between the section marker and the fetch-timeout section). Same
 * bounds as above: shape-pinning only, no execution. */

const sectionStart = appSrc.indexOf("announcements over SSE");
const sectionEnd = appSrc.indexOf("fetch with hard timeout");
assert.ok(sectionStart !== -1 && sectionEnd !== -1 && sectionStart < sectionEnd,
  "announcements section found");
const section = appSrc.slice(sectionStart, sectionEnd);

test("affordance visibility: blocked AND not muted, re-evaluated on mute toggle", () => {
  assert.ok(section.includes("announcer.isBlocked() && !muted()"),
    "mute wins the priority: muted means toast only, no affordance");
  const renders = section.match(/renderVoiceUnlock\(\)/g) || [];
  // definition + blockedChange call + mute-toggle call = at least 3 uses
  assert.ok(renders.length >= 3,
    "renderVoiceUnlock is wired from both the blocked-change callback and the mute toggle");
});

test("button click unlocks once; document gestures attach only while blocked", () => {
  assert.ok(section.includes('voiceUnlockBtn.addEventListener("click", unlockVoice)'),
    "the button routes through unlockVoice");
  assert.ok(section.includes('document.addEventListener("pointerdown", onDocumentGesture, true)'),
    "pointerdown gesture listener registered (capture)");
  assert.ok(section.includes('document.addEventListener("keydown", onDocumentGesture, true)'),
    "keydown gesture listener registered (capture)");
  assert.ok(section.includes('document.removeEventListener("pointerdown", onDocumentGesture, true)') &&
            section.includes('document.removeEventListener("keydown", onDocumentGesture, true)'),
    "both listeners detach on unblock — no double handlers");
  assert.ok(/if \(blocked\) \{[\s\S]{0,200}addEventListener\("pointerdown"/.test(section),
    "attachment is gated on the blocked transition");
});

test("unlock routes through requestUnlock: state flips only on prime success", () => {
  assert.ok(section.includes("announcer.requestUnlock(primeAudio)"),
    "the button and gestures ask for an unlock-on-proof");
  assert.ok(!section.includes("announcer.unlock()"),
    "no unconditional unlock — a refused prime must keep blocked state, " +
    "the persistent text and the affordance (FR-04/FR-05)");
});

test("prime is an isolated silent Audio element; the shared player is untouched", () => {
  const pStart = section.indexOf("function primeAudio");
  const pEnd = section.indexOf("function unlockVoice");
  assert.ok(pStart !== -1 && pEnd !== -1 && pStart < pEnd, "primeAudio found");
  const prime = section.slice(pStart, pEnd);
  assert.ok(prime.includes("new Audio(SILENT_WAV)"),
    "a fresh isolated element primes — never the shared player");
  assert.ok(prime.includes("SILENT_WAV"),
    "the prime source is the silent wav, never a queued announcement src");
  assert.ok(!prime.includes("player"),
    "queued non-announcement audio on the shared player must not be interrupted");
  assert.ok(!prime.includes("pumpAudio") && !prime.includes("audioQueue"),
    "priming must not replay or advance anything");
});

/* ---- reopened T4/T3 defects (parent review #2) ----
 * Defect A: unlock-before-prime-resolution removed the affordance on a
 * refused prime, and priming hijacked the shared player. Fixed by
 * unlock-on-proof (requestUnlock, executable-tested in announce.test.js)
 * plus an ISOLATED Audio element for the prime.
 * Defect B: the media 'error' event ran onAudioEnded BEFORE the play()
 * rejection, hiding the announcement text and letting the stale guard
 * swallow the rejection — FR-04/AC3 lost the text on media errors. The
 * error event now routes out-of-call announcements through the SAME
 * failure handler as the rejection; whichever arrival comes first
 * claims the failure (audioFinished = null) and the late twin no-ops. */

test("media error routes out-of-call announcements through the shared failure handler", () => {
  assert.ok(appSrc.includes('player.addEventListener("error", onAudioError)'),
    "the error event must not run onAudioEnded for announcements (it hid the text first)");
  assert.ok(!appSrc.includes('player.addEventListener("error", onAudioEnded'),
    "the old error wiring must be gone");
  const eStart = appSrc.indexOf("function onAudioError");
  const eEnd = appSrc.indexOf('player.addEventListener("ended"');
  assert.ok(eStart !== -1 && eEnd !== -1 && eStart < eEnd, "onAudioError found");
  const errFn = appSrc.slice(eStart, eEnd);
  assert.ok(errFn.includes("handlePlayFailure(item)"),
    "out-of-call announcement errors share the rejection handler's policy");
  assert.ok(errFn.includes("onAudioEnded()"),
    "in-call and non-announcement errors keep today's path (FR-08)");
});

test("handlePlayFailure serves both failure arrivals; the second is a no-op", () => {
  const fStart = appSrc.indexOf("function handlePlayFailure(item)");
  const fEnd = appSrc.indexOf("function onAudioEnded");
  assert.ok(fStart !== -1 && fEnd !== -1 && fStart < fEnd, "handlePlayFailure found");
  const fn = appSrc.slice(fStart, fEnd);
  assert.ok(fn.includes("if (audioFinished !== item) return;"),
    "stale guard first: superseded items are ignored");
  assert.ok(fn.includes("audioFinished = null;"),
    "claims the failure so the LATE promise rejection after the error event no-ops");
  assert.ok(fn.includes("announcer.onPlayRejected(item.announcement)"),
    "text preserved: enters blocked and re-shows persistently");
  assert.ok(fn.includes("dropped.push(queued.announcement)"),
    "previously queued announcements drop with text preserved");
  assert.ok(fn.includes('stopBtn.classList.add("hidden")'),
    "the stop button hides when the queue drains");
  assert.ok(fn.includes('inCall && callState === "speaking"'),
    "in-call mic resume preserved inside the shared handler");
  assert.ok(/pending\.catch\(function \(\) \{[\s\S]{0,80}handlePlayFailure\(item\);/.test(appSrc),
    "the promise catch delegates to the same handler");
});
