/* Structural wiring tests for the call drawer's formatted reader surface
 * (call-chat-formatted).
 *
 * LIMITS, stated plainly: app.js is a single browser-only IIFE (DOM,
 * EventSource, live audio element) that node --test cannot load, so the
 * mount and the snapshot repaint cannot be EXECUTED here. These tests pin
 * the wiring SHAPE by reading the shipped sources as text — exactly the
 * discipline of pending-wiring.test.js and announce-wiring.test.js. The
 * mount mechanics themselves (innerHTML only on .reader-content, text
 * fallback, neutralize/harden) are already executable and covered by
 * reader.test.js against the real reader.js module.
 *
 * The regression this file guards: the call drawer's conversation and the
 * boot call-history repaint render plain textContent only, so Markdown the
 * main screen formats through /conversation/{pane}/rendered stays raw
 * inside the live call.
 */

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const appSrc = fs.readFileSync(
  path.join(__dirname, "../../src/herdr_brain/static/app.js"), "utf8");
const htmlSrc = fs.readFileSync(
  path.join(__dirname, "../../src/herdr_brain/static/index.html"), "utf8");

test("buildTurnEl records the raw turn and attempts the reader mount", () => {
  assert.ok(appSrc.includes("turn.rawText = text;"),
    "the turn element must hold its raw text as the snapshot lookup key");
  assert.ok(appSrc.includes("turn.rawRole = role;"),
    "the turn element must hold its drawer role for the repaint lookup");
  assert.ok(appSrc.includes("mountCallReader(turn, role, text);"),
    "the shared turn builder must attempt the formatted mount (live append, " +
    "boot repaint and history pages all route through buildTurnEl)");
});

test("the drawer lookup maps the drawer brain role to the conversation role", () => {
  assert.ok(appSrc.includes('role === "brain" ? "assistant" : role'),
    "reader.htmlFor keys on the conversation payload's user/assistant roles; " +
    "the drawer speaks user/brain");
});

test("an active karaoke turn keeps its raw teleprompter", () => {
  assert.ok(appSrc.includes("state.plan === turn.karaokePlan"),
    "mountCallReader must skip the formatted mount while THIS turn is the " +
    "active karaoke plan — the live teleprompter highlight rides the raw " +
    "chunk spans");
});

test("every reader snapshot repaint upgrades the drawer turns", () => {
  assert.ok(appSrc.includes('callReader.loadRendered("call-history", null)'),
    "the drawer mounts the CALL transcript snapshot (/call-history/rendered), " +
    "never the pane-transcript conversation snapshot — those content keys " +
    "never overlap");
  assert.ok(appSrc.includes("scheduleCallReaderSnapshot();") &&
    appSrc.includes("paintCallTurnsReader();"),
    "the burst-collapsed snapshot fetch repaints the whole drawer");
});

test("a dedicated call-transcript reader instance exists", () => {
  assert.ok(appSrc.includes('renderedUrl: function () { return "/call-history/rendered"; }'),
    "the second Reader instance must point at the call transcript endpoint");
  assert.ok(appSrc.includes("callReader.mountTurn(content,"),
    "the drawer mount delegates construction to its own reader instance");
});

test("the drawer mounts reader content only through reader.mountTurn", () => {
  const fn = appSrc.slice(
    appSrc.indexOf("function mountCallReader"),
    appSrc.indexOf("function paintCallTurnsReader"));
  assert.ok(fn.includes(".mountTurn(content,"), "delegated to reader.js");
  assert.ok(!fn.includes(".innerHTML"), "no direct innerHTML — reader.js owns it");
});

test("the reader CSS scope deliberately covers the call drawer", () => {
  assert.ok(htmlSrc.includes(":is(#glance-turns, #conversation) .reader-content"),
    "the formatted-surface rules must apply inside #conversation");
  assert.ok(
    htmlSrc.includes("#conversation .turn.has-rendered > span:not(.who) { display: none; }") &&
      !htmlSrc.includes("#conversation .turn.has-rendered span:not(.who)"),
    "when a formatted mount exists the drawer hides ONLY its direct raw body " +
      "span — a descendant selector would hide the reader content's own " +
      "tts-sent spans and collapse the bubble empty");
});
