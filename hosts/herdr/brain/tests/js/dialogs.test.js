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

/* Free-text options (S3): a row with `free_text: true` does not post
 * on tap — the tap OPENS an editor in the card, the typed text passes
 * the composer's destructive guard (second tap on a match), and only
 * the editor's send posts {option, text}. */

const FREE_TEXT = {
  agent: "claude",
  kind: "select",
  question: "Which fruit do you want?",
  options: [
    { label: "Manzana", description: "" },
    { label: "Pera", description: "" },
    { label: "Type something.", description: "", free_text: true },
  ],
  fingerprint: "ft123",
  recipe: "keys",
};

test("isFreeTextOption: true only for the row the server marked", () => {
  assert.equal(Dialogs.isFreeTextOption(FREE_TEXT, 2), true);
  assert.equal(Dialogs.isFreeTextOption(FREE_TEXT, 0), false);
  assert.equal(Dialogs.isFreeTextOption(FREE_TEXT, 9), false);
  assert.equal(
    Dialogs.isFreeTextOption({ unread: true, agent: "claude" }, 0), false);
  assert.equal(Dialogs.isFreeTextOption(null, 0), false);
});

test("freeTextPayload: the exact body the editor's send posts", () => {
  assert.deepEqual(
    Dialogs.freeTextPayload("w1:p3", "ft123", 2, "usa la rama main"),
    { pane_id: "w1:p3", fingerprint: "ft123", option: 2, text: "usa la rama main" });
});

test("editor chrome: placeholder and send label, translated when injected", () => {
  assert.equal(Dialogs.editorPlaceholder(), "Escribe tu respuesta…");
  assert.equal(Dialogs.editorSendLabel(), "Enviar respuesta");
  Dialogs.useTranslator(function (key) {
    return key === "dialogs.free_text_placeholder" ? "Type your answer…"
      : key === "dialogs.free_text_send" ? "Send answer" : key;
  });
  assert.equal(Dialogs.editorPlaceholder(), "Type your answer…");
  assert.equal(Dialogs.editorSendLabel(), "Send answer");
  Dialogs.useTranslator(null);
});

test("confirmFreeText: null when safe, the composer's confirm wording when not", () => {
  assert.equal(Dialogs.confirmFreeText("usa la rama main", null), null);
  const question = Dialogs.confirmFreeText(
    "sudo rm -rf /", "sudo (se ejecuta como root)");
  assert.ok(/destructive/i.test(question) || /parece destructivo/.test(question));
  assert.ok(question.indexOf("sudo (se ejecuta como root)") !== -1);
  assert.ok(question.indexOf("sudo rm -rf /") !== -1);
  assert.ok(/¿Enviar igualmente\?/.test(question));
});

test("confirmFreeText: reuses the injected reply.destructive_confirm wording", () => {
  Dialogs.useTranslator(function (key) {
    return key === "reply.destructive_confirm"
      ? "This send looks destructive: {match}.\n\n{text}\n\nSend anyway?"
      : key;
  });
  const question = Dialogs.confirmFreeText("git push --force", "git push --force");
  assert.equal(
    question,
    "This send looks destructive: git push --force.\n\ngit push --force\n\nSend anyway?");
  Dialogs.useTranslator(null);
});

/* Wizard (S4): a wizard step whose payload carries `wizard: {step,
 * steps}` gets a stepper. Each step's answer is captured LOCALLY with
 * the same option/choices/text model; Next posts one step at a time
 * through the existing endpoint payloads; the LAST step stops at a
 * review (Confirm sends it, Edit returns to a capture without sending);
 * any drift — a 409 changed response, or a next dialog that is not the
 * expected step — aborts with the changed card and never retries. */

const WIZARD_STEP1 = {
  agent: "claude",
  kind: "wizard_step",
  wizard: { step: 1, steps: 2 },
  question: "Which fruit do you want?",
  options: [
    { label: "Manzana", description: "" },
    { label: "Pera", description: "" },
  ],
  fingerprint: "fp1",
  recipe: "keys",
};

const WIZARD_STEP2 = {
  agent: "claude",
  kind: "multiselect",
  wizard: { step: 2, steps: 2 },
  question: "Which toppings?",
  options: [
    { label: "Cheese", checked: false },
    { label: "Pepperoni", checked: true },
    { label: "Submit", checked: null },
  ],
  fingerprint: "fp2",
  recipe: "keys",
};

function recordingSend(result) {
  const calls = [];
  const fn = function (payload) {
    calls.push(payload);
    return Promise.resolve(result || { ok: true, status: 200, body: {} });
  };
  fn.calls = calls;
  return fn;
}

/* The canonical two-step run: step 1 captured and posted, step 2 (the
 * last) captured and held at the review. */
async function wizardAtReview(send) {
  const wizard = Dialogs.wizardSession(send);
  wizard.feed(WIZARD_STEP1, "w1:p3");
  wizard.pick(1);
  await wizard.next();
  wizard.feed(WIZARD_STEP2, "w1:p3");
  wizard.toggle(0);
  await wizard.next();
  return wizard;
}

test("wizardOf: only a well-formed wizard metadata object counts", () => {
  assert.deepEqual(Dialogs.wizardOf(WIZARD_STEP1), { step: 1, steps: 2 });
  assert.deepEqual(Dialogs.wizardOf(WIZARD_STEP2), { step: 2, steps: 2 });
  assert.equal(Dialogs.wizardOf(STRUCTURED), null);
  assert.equal(Dialogs.wizardOf({ unread: true, agent: "claude" }), null);
  assert.equal(Dialogs.wizardOf(null), null);
  assert.equal(
    Dialogs.wizardOf({ kind: "wizard_step", wizard: { step: 2 } }), null);
  assert.equal(
    Dialogs.wizardOf({ kind: "wizard_step", wizard: { step: 0, steps: 2 } }),
    null);
  assert.equal(
    Dialogs.wizardOf({ kind: "wizard_step", wizard: { step: 3, steps: 2 } }),
    null);
});

test("wizard: Next posts the captured step-1 option exactly once", async () => {
  const send = recordingSend();
  const wizard = Dialogs.wizardSession(send);
  wizard.feed(WIZARD_STEP1, "w1:p3");
  assert.equal(wizard.view().mode, "capturing");

  wizard.pick(1);
  const result = await wizard.next();

  assert.ok(result.ok);
  assert.equal(send.calls.length, 1);
  assert.deepEqual(send.calls[0], {
    pane_id: "w1:p3", fingerprint: "fp1", option: 1,
  });
  assert.equal(wizard.view().mode, "waiting");
  /* Waiting for the next step's dialog: no path posts again. */
  await wizard.next();
  await wizard.confirm();
  assert.equal(send.calls.length, 1);
});

test("wizard: an empty capture never posts", async () => {
  const send = recordingSend();
  const wizard = Dialogs.wizardSession(send);
  wizard.feed(WIZARD_STEP1, "w1:p3");

  const result = await wizard.next();

  assert.equal(result.ok, false);
  assert.equal(result.reason, "empty");
  assert.equal(send.calls.length, 0);
  assert.equal(wizard.view().mode, "capturing");
});

test("wizard: the last step stops at a review listing every answer", async () => {
  const send = recordingSend();
  const wizard = await wizardAtReview(send);

  assert.equal(send.calls.length, 1);  /* only step 1 left */
  const view = wizard.view();
  assert.equal(view.mode, "reviewing");
  assert.equal(view.steps, 2);
  assert.equal(view.answers.length, 2);
  assert.equal(view.answers[0].question, "Which fruit do you want?");
  assert.deepEqual(view.answers[0].labels, ["Pera"]);
  assert.equal(view.answers[0].submitted, true);
  assert.deepEqual(view.answers[1].labels, ["Cheese", "Pepperoni"]);
  assert.equal(view.answers[1].submitted, false);
});

test("wizard: Edit returns to a step's capture without posting", async () => {
  const send = recordingSend();
  const wizard = await wizardAtReview(send);

  wizard.edit(1);

  const view = wizard.view();
  assert.equal(view.mode, "capturing");
  assert.equal(view.current, 1);
  assert.equal(send.calls.length, 1);  /* nothing new posted */
  /* The capture is restored to what that step chose. */
  assert.equal(wizard.pickedIndex(), 1);
});

test("wizard: Confirm sends the pending last step and finishes", async () => {
  const send = recordingSend();
  const wizard = await wizardAtReview(send);

  const result = await wizard.confirm();

  assert.ok(result.ok);
  assert.equal(send.calls.length, 2);
  assert.deepEqual(send.calls[1], {
    pane_id: "w1:p3", fingerprint: "fp2", choices: [0, 1],
  });
  const view = wizard.view();
  assert.equal(view.mode, "waiting");
  assert.equal(view.answers[1].submitted, true);
  /* The dialog leaving the pane completes the wizard (nothing pending). */
  wizard.feed(null, "w1:p3");
  assert.equal(wizard.view().mode, "idle");
});

test("wizard: a 409 changed response aborts with the card and no retries", async () => {
  const send = recordingSend({ ok: false, status: 409, body: {} });
  const wizard = Dialogs.wizardSession(send);
  wizard.feed(WIZARD_STEP1, "w1:p3");
  wizard.pick(0);

  const result = await wizard.next();

  assert.equal(result.ok, false);
  const view = wizard.view();
  assert.equal(view.mode, "aborted");
  assert.equal(view.error, "changed");
  assert.ok(/cambió/i.test(Dialogs.wizardChangedMessage()));
  /* No path posts again, and later dialogs do not restart the machine. */
  await wizard.next();
  await wizard.confirm();
  wizard.feed(WIZARD_STEP2, "w1:p3");
  assert.equal(send.calls.length, 1);
  assert.equal(wizard.view().mode, "aborted");
});

test("wizard: a next dialog that is not the expected step aborts", async () => {
  const send = recordingSend();
  const wizard = Dialogs.wizardSession(send);
  wizard.feed(WIZARD_STEP1, "w1:p3");
  wizard.pick(0);
  await wizard.next();

  const wrongStep = Object.assign({}, WIZARD_STEP2, {
    wizard: { step: 1, steps: 2 },
    fingerprint: "fpX",
  });
  wizard.feed(wrongStep, "w1:p3");

  assert.equal(wizard.view().mode, "aborted");
  await wizard.confirm();
  assert.equal(send.calls.length, 1);
});

test("wizard: while waiting, the un-advanced same screen is tolerated", async () => {
  const send = recordingSend();
  const wizard = Dialogs.wizardSession(send);
  wizard.feed(WIZARD_STEP1, "w1:p3");
  wizard.pick(1);
  await wizard.next();

  /* The pane's redraw lag: the SAME step, same fingerprint, again. */
  wizard.feed(WIZARD_STEP1, "w1:p3");
  assert.equal(wizard.view().mode, "waiting");

  wizard.feed(WIZARD_STEP2, "w1:p3");
  const view = wizard.view();
  assert.equal(view.mode, "capturing");
  assert.equal(view.current, 2);
});

test("wizard: Back rewinds only local selections, never a sent step", async () => {
  const send = recordingSend();
  const wizard = Dialogs.wizardSession(send);
  wizard.feed(WIZARD_STEP1, "w1:p3");

  /* Nothing marked yet on the first step: nothing to rewind. */
  let back = wizard.back();
  assert.deepEqual(back, { ok: false, reason: "first" });

  wizard.pick(1);
  back = wizard.back();
  assert.deepEqual(back, { ok: true });
  assert.equal(wizard.pickedIndex(), null);
  assert.equal(send.calls.length, 0);

  /* After a step was submitted, Back has nothing local to rewind and
   * says WHY: an already-sent step cannot be un-sent. */
  wizard.pick(0);
  await wizard.next();
  wizard.feed(WIZARD_STEP2, "w1:p3");
  wizard.toggle(2);
  back = wizard.back();
  assert.deepEqual(back, { ok: false, reason: "sent" });
});

test("wizard chrome: translated labels with Spanish fallbacks", () => {
  assert.equal(Dialogs.wizardProgressLabel(1, 2), "Paso 1 de 2");
  assert.equal(Dialogs.wizardNextLabel(), "Siguiente");
  assert.equal(Dialogs.wizardBackLabel(), "Atrás");
  assert.ok(/enviad/.test(Dialogs.wizardBackDisabledReason("sent")));
  assert.ok(/nada/i.test(Dialogs.wizardBackDisabledReason("first")));
  assert.ok(/revis/i.test(Dialogs.wizardReviewTitle()));
  assert.ok(/confirmar/i.test(Dialogs.wizardConfirmLabel()));
  assert.equal(Dialogs.wizardEditLabel(), "Editar");
  assert.ok(/cambió/i.test(Dialogs.wizardChangedMessage()));

  Dialogs.useTranslator(function (key) {
    return ({
      "dialogs.wizard_progress": "Step {step} of {steps}",
      "dialogs.wizard_next": "Next",
      "dialogs.wizard_back": "Back",
      "dialogs.wizard_back_sent": "Sent steps cannot be undone.",
      "dialogs.wizard_review_title": "Review your answers",
      "dialogs.wizard_confirm": "Confirm and send",
      "dialogs.wizard_edit": "Edit",
      "dialogs.wizard_changed": "The wizard changed; nothing further was sent.",
    })[key] || key;
  });
  assert.equal(Dialogs.wizardProgressLabel(2, 3), "Step 2 of 3");
  assert.equal(Dialogs.wizardNextLabel(), "Next");
  assert.equal(Dialogs.wizardBackLabel(), "Back");
  assert.equal(Dialogs.wizardBackDisabledReason("sent"),
    "Sent steps cannot be undone.");
  assert.equal(Dialogs.wizardReviewTitle(), "Review your answers");
  assert.equal(Dialogs.wizardConfirmLabel(), "Confirm and send");
  assert.equal(Dialogs.wizardEditLabel(), "Edit");
  assert.ok(/changed/.test(Dialogs.wizardChangedMessage()));
  Dialogs.useTranslator(null);
});

/* ── parity-f7d S2: the model picker card + the "Cambiar modelo"
 * affordance. The card is the SAME generic option surface (recipes
 * stay server-side); the affordance decides whether the sheet offers
 * the switch and which command the reply path sends. */

const MODEL_PICKER = {
  agent: "claude",
  kind: "model_picker",
  question: "Select model",
  options: [
    { label: "Default (recommended) ✔", description: "Opus 5 with 1M context" },
    { label: "Opus (1M context)", description: "Opus 5 with 1M context" },
    { label: "Fable", description: "Fable 5" },
    { label: "Sonnet", description: "Sonnet 5" },
    { label: "Haiku", description: "Haiku 4.5" },
  ],
  fingerprint: "mp1",
  recipe: "walk",
};

test("isModelPicker: only a claimed, non-unread model_picker payload", () => {
  assert.equal(Dialogs.isModelPicker(MODEL_PICKER), true);
  assert.equal(Dialogs.isModelPicker(STRUCTURED), false);
  assert.equal(Dialogs.isModelPicker(null), false);
  assert.equal(Dialogs.isModelPicker({ unread: true, kind: "model_picker" }), false);
});

test("model picker card: the generic buttons render every option", () => {
  const buttons = Dialogs.buttonsFor(MODEL_PICKER);
  assert.equal(buttons.length, 5);
  assert.deepEqual(
    buttons.map((b) => b.label),
    ["Default (recommended) ✔", "Opus (1M context)", "Fable", "Sonnet", "Haiku"]);
  // No executable material rides the payload — a tap names an index.
  assert.ok(buttons.every((b) => !("keys" in b)));
});

test("modelPickerTitle: the card's own chrome", () => {
  assert.equal(Dialogs.modelPickerTitle(), "Cambiar modelo");
});

test("modelSwitchOffer: only harnesses with a verified picker command", () => {
  assert.deepEqual(Dialogs.modelSwitchOffer("claude"), {
    command: "/model", label: "Cambiar modelo",
  });
  assert.deepEqual(Dialogs.modelSwitchOffer("grok"), {
    command: "/model", label: "Cambiar modelo",
  });
  assert.deepEqual(Dialogs.modelSwitchOffer("omp"), {
    command: "/switch", label: "Cambiar modelo",
  });
  // pi draws omp's Switch Model box (same TUI): /switch opens it.
  assert.deepEqual(Dialogs.modelSwitchOffer("pi"), {
    command: "/switch", label: "Cambiar modelo",
  });
  // opencode 1.19.5, live-verified: the composer command is /models
  // (/model only filters the command palette).
  assert.deepEqual(Dialogs.modelSwitchOffer("opencode"), {
    command: "/models", label: "Cambiar modelo",
  });
  assert.deepEqual(Dialogs.modelSwitchOffer("opencode2"), {
    command: "/models", label: "Cambiar modelo",
  });
  assert.equal(Dialogs.modelSwitchOffer("codex"), null);
  assert.equal(Dialogs.modelSwitchOffer(""), null);
  assert.equal(Dialogs.modelSwitchOffer(null), null);
});

test("modelSwitchPayload: the plain reply body, command verbatim", () => {
  assert.deepEqual(Dialogs.modelSwitchPayload("w1:p3", "/model"), {
    pane_id: "w1:p3",
    text: "/model",
  });
});

test("model switch toasts", () => {
  assert.match(Dialogs.modelSwitchOpenedToast("Claude"), /selector/);
  assert.match(Dialogs.modelSwitchNoneToast(), /no puede cambiar/);
});

/* ── model picker search + scroll (2026-11-19 operator report: "no me
 * deja hacer scroll… incluiría un buscador… un autocomplete… si clico
 * tiene que seleccionarse"). The filter is PURE and client-side; a
 * filtered row always answers with its ORIGINAL option index. */

const OPENCODE_PICKER = {
  agent: "opencode",
  kind: "model_picker",
  question: "Select model",
  options: [
    { label: "Qwen3.8 Max OpenCode Go", model: "Qwen3.8 Max", provider: "OpenCode Go" },
    { label: "GPT-5.6 Terra OpenAI", model: "GPT-5.6 Terra", provider: "OpenAI" },
    { label: "Claude Opus 5.5 Anthropic", model: "Claude Opus 5.5", provider: "Anthropic", current: true },
    { label: "Ñandú Pro DeepSeek", model: "Ñandú Pro", provider: "DeepSeek" },
    { label: "GLM-5.3 Z.AI Coding Plan", model: "GLM-5.3", provider: "Z.AI Coding Plan" },
  ],
  fingerprint: "oc1",
  recipe: "walk",
};

test("modelPickerRows: clean model, provider, current — original indexes", () => {
  const rows = Dialogs.modelPickerRows(OPENCODE_PICKER);
  assert.deepEqual(
    rows.map((r) => [r.index, r.model, r.provider, r.current]),
    [
      [0, "Qwen3.8 Max", "OpenCode Go", false],
      [1, "GPT-5.6 Terra", "OpenAI", false],
      [2, "Claude Opus 5.5", "Anthropic", true],
      [3, "Ñandú Pro", "DeepSeek", false],
      [4, "GLM-5.3", "Z.AI Coding Plan", false],
    ]);
});

test("modelPickerRows: a harness without model/provider falls back to label + description", () => {
  const rows = Dialogs.modelPickerRows(MODEL_PICKER);
  assert.equal(rows[2].model, "Fable");
  assert.equal(rows[2].provider, "Fable 5");
  assert.equal(Dialogs.modelPickerRows(STRUCTURED), null);
  assert.equal(Dialogs.modelPickerRows(null), null);
});

test("filterModelRows: case-insensitive substring over the model name", () => {
  const rows = Dialogs.modelPickerRows(OPENCODE_PICKER);
  assert.deepEqual(
    Dialogs.filterModelRows(rows, "GPT").map((r) => r.index), [1]);
  assert.deepEqual(
    Dialogs.filterModelRows(rows, "opus").map((r) => r.index), [2]);
});

test("filterModelRows: accent-insensitive both ways", () => {
  const rows = Dialogs.modelPickerRows(OPENCODE_PICKER);
  assert.deepEqual(
    Dialogs.filterModelRows(rows, "nandu").map((r) => r.index), [3]);
  assert.deepEqual(
    Dialogs.filterModelRows(rows, "ÑANDÚ").map((r) => r.index), [3]);
});

test("filterModelRows: provider is secondary searchable text", () => {
  const rows = Dialogs.modelPickerRows(OPENCODE_PICKER);
  assert.deepEqual(
    Dialogs.filterModelRows(rows, "anthropic").map((r) => r.index), [2]);
  // Every token must match somewhere (model or provider).
  assert.deepEqual(
    Dialogs.filterModelRows(rows, "terra openai").map((r) => r.index), [1]);
  assert.deepEqual(
    Dialogs.filterModelRows(rows, "terra anthropic").map((r) => r.index), []);
});

test("filterModelRows: empty / blank query keeps every row in order", () => {
  const rows = Dialogs.modelPickerRows(OPENCODE_PICKER);
  assert.deepEqual(
    Dialogs.filterModelRows(rows, "").map((r) => r.index), [0, 1, 2, 3, 4]);
  assert.deepEqual(
    Dialogs.filterModelRows(rows, "   ").map((r) => r.index), [0, 1, 2, 3, 4]);
  assert.deepEqual(
    Dialogs.filterModelRows(rows, null).map((r) => r.index), [0, 1, 2, 3, 4]);
});

test("filterModelRows: a filtered row keeps the ORIGINAL index a tap sends", () => {
  const rows = Dialogs.modelPickerRows(OPENCODE_PICKER);
  const hit = Dialogs.filterModelRows(rows, "glm")[0];
  assert.equal(hit.index, 4);
  assert.deepEqual(
    Dialogs.answerPayload("w1:p4", "oc1", hit.index),
    { pane_id: "w1:p4", fingerprint: "oc1", option: 4 });
});

test("model picker search chrome is translatable text", () => {
  assert.equal(Dialogs.modelSearchPlaceholder(), "Buscar modelo…");
  assert.equal(Dialogs.modelSearchNone(), "Ningún modelo coincide.");
});
