/* Receipts module (S12): outcome labels, spoken phrases, stop arming.
 * Pure logic only — no DOM — the same discipline as dialogs.js. */

const test = require("node:test");
const assert = require("node:assert");

const mod = require("../../src/herdr_brain/static/receipts.js");
const Receipts = mod.Receipts;

test("every closed-set outcome has a Spanish label", () => {
  const outcomes = [
    "effective", "sent", "typed_not_submitted", "discarded", "refused", "unknown",
  ];
  for (const outcome of outcomes) {
    const label = Receipts.outcomeLabel(outcome);
    assert.ok(label && label.length >= 4, outcome);
  }
  // An invented outcome never renders as a bare key.
  assert.equal(Receipts.outcomeLabel("made-up"), "No puedo confirmar si se envió");
});

test("the label says enviado only for effective and sent", () => {
  assert.ok(Receipts.outcomeLabel("effective").startsWith("Enviado"));
  assert.ok(Receipts.outcomeLabel("sent").startsWith("Enviado"));
  for (const outcome of ["typed_not_submitted", "discarded", "refused", "unknown"]) {
    const label = Receipts.outcomeLabel(outcome);
    assert.ok(!label.startsWith("Enviado"), outcome + " must not say Enviado");
  }
});

test("the spoken phrase says enviado only for effective and sent, and is explicit when unknown", () => {
  assert.ok(Receipts.speakPhrase("effective").startsWith("Enviado"));
  assert.ok(Receipts.speakPhrase("sent").startsWith("Enviado"));
  const unknown = Receipts.speakPhrase("unknown");
  assert.ok(/no puedo confirmar/i.test(unknown));
  assert.ok(Receipts.speakPhrase("typed_not_submitted").length >= 10);
  assert.equal(Receipts.speakPhrase("made-up"), null);
});

test("submit and discard payloads name the receipt and nothing else", () => {
  assert.deepEqual(Receipts.submitPayload("rct-1"), { receipt_id: "rct-1" });
  assert.deepEqual(Receipts.discardPayload("rct-1"), { receipt_id: "rct-1" });
});

test("the receipt card model shows submit and discard only while typed", () => {
  const typed = Receipts.cardModel({
    id: "rct-1", outcome: "typed_not_submitted", detail: "aún escrito",
  });
  assert.equal(typed.variant, "typed");
  assert.equal(typed.submitUrl, "/pane/receipt/rct-1/submit");
  assert.equal(typed.discardUrl, "/pane/receipt/rct-1/discard");

  const sent = Receipts.cardModel({ id: "rct-2", outcome: "sent", detail: "" });
  assert.equal(sent.variant, "info");
  assert.equal(sent.submitUrl, null);
  assert.equal(sent.discardUrl, null);
});

test("stop arming: the first tap arms, the second inside the window fires", () => {
  let now = 0;
  const arming = Receipts.createStopArming(() => now, 3000);
  assert.ok(!arming.isArmed());                 // fresh: nothing armed
  assert.equal(arming.tap(), "armed");          // first tap arms
  assert.ok(arming.isArmed());
  now = 2000;
  assert.equal(arming.tap(), "fire");           // second tap inside 3s
  assert.ok(!arming.isArmed());                 // fired disarms
});

test("stop arming expires after the window", () => {
  let now = 0;
  const arming = Receipts.createStopArming(() => now, 3000);
  arming.tap();
  now = 3500;                                   // past the window
  assert.equal(arming.tap(), "armed");          // re-arms, never fires stale
  assert.ok(arming.isArmed());
});

test("stop arming disarms explicitly", () => {
  const arming = Receipts.createStopArming(() => 0, 3000);
  arming.tap();
  arming.disarm();
  assert.ok(!arming.isArmed());
  assert.equal(arming.tap(), "armed");
});

test("steer is never the default: the composer body gains steer only on demand", () => {
  assert.deepEqual(Receipts.replyPayload("w1:p2", "hola", false), {
    pane_id: "w1:p2", text: "hola",
  });
  assert.deepEqual(Receipts.replyPayload("w1:p2", "hola", true), {
    pane_id: "w1:p2", text: "hola", steer: true,
  });
});
