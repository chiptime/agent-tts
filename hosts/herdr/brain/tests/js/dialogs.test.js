/* Pure-logic tests for the dialog button module (static/dialogs.js):
 * the model that turns a RECOGNIZED structured dialog (/view's
 * `structured` field) into tappable button descriptors, the exact
 * POST /pane/dialog/answer payload a tap sends, and the Spanish status
 * line for every outcome. No DOM here — the real Chromium behavior is
 * proven in tests/browser/test_dialog_buttons.py. */

const test = require("node:test");
const assert = require("node:assert");
const path = require("node:path");

const Dialogs = require(
  path.join(__dirname, "../../src/herdr_brain/static/dialogs.js"));

const STRUCTURED = {
  agent: "claude",
  kind: "select",
  question: "Which fruit do you want?",
  options: [
    { label: "Manzana", description: "la roja" },
    { label: "Pera", description: "" },
  ],
  fingerprint: "abc123",
  recipe: "keys",
};

test("buttonsFor: one button per option, labels and descriptions verbatim", () => {
  const buttons = Dialogs.buttonsFor(STRUCTURED);
  assert.deepEqual(buttons, [
    { index: 0, label: "Manzana", description: "la roja", checked: false },
    { index: 1, label: "Pera", description: "", checked: false },
  ]);
});

test("buttonsFor: null for no dialog, unread dialogs and empty options", () => {
  assert.equal(Dialogs.buttonsFor(null), null);
  assert.equal(Dialogs.buttonsFor(undefined), null);
  assert.equal(
    Dialogs.buttonsFor({ unread: true, agent: "claude" }), null);
  assert.equal(
    Dialogs.buttonsFor(Object.assign({}, STRUCTURED, { options: [] })), null);
});

test("unreadCard: explicit Spanish card that keeps the keypad", () => {
  const card = Dialogs.unreadCard("grok");
  assert.equal(card.title, "No puedo leer este diálogo");
  assert.ok(/grok/.test(card.body));
  assert.ok(/teclado/.test(card.body));
});

test("answerPayload: the exact body the endpoint expects", () => {
  assert.deepEqual(
    Dialogs.answerPayload("w1:p3", "abc123", 1),
    { pane_id: "w1:p3", fingerprint: "abc123", option: 1 });
});

test("statusFor: answered names the tapped option", () => {
  const status = Dialogs.statusFor({ ok: true, option: 1 },
    ["Manzana", "Pera"]);
  assert.equal(status, "Respondido: Pera");
});

test("statusFor: a changed dialog explains the 409", () => {
  const status = Dialogs.statusFor({ status: 409 }, ["Manzana"]);
  assert.ok(/cambió/i.test(status));
});

test("statusFor: a refused option and a failed request both point at the keypad", () => {
  assert.ok(/teclado/.test(Dialogs.statusFor({ status: 422 }, ["A"])));
  assert.ok(/teclado/.test(Dialogs.statusFor({ status: 0 }, ["A"])));
  assert.ok(/teclado/.test(Dialogs.statusFor(null, ["A"])));
});

/* Multiselect (kind: "multiselect"): every option row is a checkbox
 * toggle held in LOCAL state — no option tap ever posts — and one
 * explicit submit button posts the whole selection as {choices}. */

const MULTI = {
  agent: "claude",
  kind: "multiselect",
  question: "Which toppings do you want?",
  options: [
    { label: "Cheese", checked: false },
    { label: "Pepperoni", checked: true },
    { label: "Mushrooms", checked: false },
    { label: "Submit", checked: null },
  ],
  fingerprint: "m1234",
  recipe: "keys",
};

test("isMultiselect: true only for a claimed multiselect dialog", () => {
  assert.equal(Dialogs.isMultiselect(MULTI), true);
  assert.equal(Dialogs.isMultiselect(STRUCTURED), false);
  assert.equal(Dialogs.isMultiselect({ unread: true, agent: "claude" }), false);
  assert.equal(Dialogs.isMultiselect(null), false);
});

test("selectionModel: seeded from the terminal's own checked state", () => {
  const model = Dialogs.selectionModel(MULTI.options);
  assert.deepEqual(model.choices(), [1]);
  assert.equal(model.isSelected(0), false);
  assert.equal(model.isSelected(1), true);
});

test("selectionModel: toggle changes only local state and never posts", () => {
  const model = Dialogs.selectionModel(MULTI.options);
  model.toggle(0);
  model.toggle(2);
  assert.deepEqual(model.choices(), [0, 1, 2]);
  model.toggle(1);
  assert.deepEqual(model.choices(), [0, 2]);
  /* The submit row is not a checkbox: toggling it is a no-op. */
  model.toggle(3);
  assert.deepEqual(model.choices(), [0, 2]);
  /* The model owns no network or payload surface of its own. */
  assert.equal(model.post, undefined);
  assert.equal(model.answerPayload, undefined);
});

test("choicesPayload: the exact body the endpoint expects", () => {
  assert.deepEqual(
    Dialogs.choicesPayload("w1:p3", "m1234", [0, 2]),
    { pane_id: "w1:p3", fingerprint: "m1234", choices: [0, 2] });
});

test("submitLabel: explicit submit chrome, translated when injected", () => {
  assert.equal(Dialogs.submitLabel(), "Enviar selección");
});
