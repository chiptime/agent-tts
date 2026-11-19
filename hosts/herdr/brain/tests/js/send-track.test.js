/* Send tracking (parity-f7g S5): the pure model behind the PWA tracker
 * card — state labels, elapsed formatting, terminal detection, the
 * per-pane tracker map and the hot-poll cadence. No DOM here (the same
 * discipline as receipts.js); app.js owns the wiring. */

const test = require("node:test");
const assert = require("node:assert");

const mod = require("../../src/herdr_brain/static/send-track.js");
const SendTrack = mod.SendTrack;

/* ---- the closed state set ------------------------------------------- */

test("every tracker state is classified terminal or non-terminal", () => {
  for (const state of ["sent", "started", "working"]) {
    assert.equal(SendTrack.isTerminal(state), false, state);
  }
  for (const state of [
    "done", "blocked", "error", "no_start", "pane_gone", "superseded", "expired",
  ]) {
    assert.equal(SendTrack.isTerminal(state), true, state);
  }
  // An invented state is never terminal (a newer server's word converges
  // via polling instead of freezing the card).
  assert.equal(SendTrack.isTerminal("made-up"), false);
});

test("every state has its exact Spanish label", () => {
  const labels = {
    sent: "Enviado",
    started: "Empezó",
    working: "Trabajando",
    done: "Terminó",
    blocked: "Te espera en un diálogo",
    no_start: "No arrancó en 15 s — revisa el agente",
    pane_gone: "El pane se cerró",
    superseded: "Reemplazado por un envío nuevo",
    expired: "Sin noticias tras 30 min",
  };
  for (const [state, label] of Object.entries(labels)) {
    assert.equal(SendTrack.stateLabel(state), label, state);
  }
});

test("an unknown state falls back to honest copy, never a bare key", () => {
  const label = SendTrack.stateLabel("future_state");
  assert.ok(label && label.length >= 4);
  assert.ok(!/future_state/.test(label));
});

test("the stalled warning line is exact", () => {
  assert.equal(
    SendTrack.stalledLabel(),
    "Parece colgado (3 min sin actividad)"
  );
});

/* ---- elapsed formatting ---------------------------------------------- */

test("elapsed formats as m:ss", () => {
  assert.equal(SendTrack.formatElapsed(0), "0:00");
  assert.equal(SendTrack.formatElapsed(5), "0:05");
  assert.equal(SendTrack.formatElapsed(83), "1:23");
  assert.equal(SendTrack.formatElapsed(600), "10:00");
  assert.equal(SendTrack.formatElapsed(1800), "30:00");
});

test("elapsed never renders negatives or garbage", () => {
  assert.equal(SendTrack.formatElapsed(-4), "0:00");
  assert.equal(SendTrack.formatElapsed(NaN), "0:00");
  assert.equal(SendTrack.formatElapsed(undefined), "0:00");
  assert.equal(SendTrack.formatElapsed("12"), "0:12");
});

/* ---- the card model --------------------------------------------------- */

test("a working tracker shows the state plus ticking elapsed", () => {
  const model = SendTrack.cardModel(
    { state: "working", sent_at: 100, stalled: false }, 183);
  assert.equal(model.stateText, "Trabajando");
  assert.equal(model.elapsedText, "· 1:23");
  assert.equal(model.terminal, false);
  assert.equal(model.dismissible, false);
});

test("a sent tracker shows Enviado with elapsed while non-terminal", () => {
  const model = SendTrack.cardModel(
    { state: "sent", sent_at: 100, stalled: false }, 103);
  assert.equal(model.stateText, "Enviado");
  assert.equal(model.elapsedText, "· 0:03");
});

test("a stalled tracker shows the stalled line and keeps ticking off", () => {
  const model = SendTrack.cardModel(
    { state: "working", sent_at: 100, stalled: true }, 300);
  assert.equal(model.stateText, "Parece colgado (3 min sin actividad)");
  assert.equal(model.elapsedText, "");
  assert.equal(model.terminal, false);
});

test("an error tracker shows the masked message as text", () => {
  const model = SendTrack.cardModel({
    state: "error", sent_at: 100, stalled: false,
    error: { name: "APIError", message: "boom 42" },
  }, 140);
  assert.equal(model.stateText, "Error: boom 42");
  assert.equal(model.terminal, true);
  assert.equal(model.dismissible, true);
  assert.equal(model.elapsedText, "");
});

test("an error without message degrades to the error name, then to Error", () => {
  assert.equal(SendTrack.cardModel({
    state: "error", error: { name: "AbortError", message: "" },
  }, 0).stateText, "Error: AbortError");
  assert.equal(SendTrack.cardModel({
    state: "error", error: null,
  }, 0).stateText, "Error");
});

test("terminal states are dismissible and carry no elapsed", () => {
  for (const state of ["done", "blocked", "no_start", "pane_gone", "superseded", "expired"]) {
    const model = SendTrack.cardModel({ state, sent_at: 100 }, 5000);
    assert.equal(model.terminal, true, state);
    assert.equal(model.dismissible, true, state);
    assert.equal(model.elapsedText, "", state);
  }
  assert.equal(
    SendTrack.cardModel({ state: "no_start", sent_at: 1 }, 2).stateText,
    "No arrancó en 15 s — revisa el agente"
  );
});

test("cardModel tolerates a missing entry", () => {
  assert.equal(SendTrack.cardModel(null, 0), null);
});

/* ---- the initial snapshot from a receipt ------------------------------ */

test("only tracked outcomes start a tracker; effective is born done", () => {
  assert.equal(SendTrack.initialTrack("typed_not_submitted", 10), null);
  assert.equal(SendTrack.initialTrack("discarded", 10), null);
  assert.equal(SendTrack.initialTrack("refused", 10), null);
  assert.deepEqual(
    SendTrack.initialTrack("effective", 10),
    { state: "done", sent_at: 10, stalled: false }
  );
  assert.deepEqual(
    SendTrack.initialTrack("sent", 10),
    { state: "sent", sent_at: 10, stalled: false }
  );
  assert.deepEqual(
    SendTrack.initialTrack("unknown", 10),
    { state: "sent", sent_at: 10, stalled: false }
  );
});

/* ---- the per-pane map -------------------------------------------------- */

function storeWithSend() {
  let nowMs = 10000;
  const store = SendTrack.createPaneTracks(() => nowMs);
  store.start("w1:p2", { id: "rct-1", outcome: "sent" });
  return { store, tick: (ms) => { nowMs = ms; } };
}

test("start registers the pane's tracker keyed by receipt id", () => {
  const { store } = storeWithSend();
  const entry = store.get("w1:p2");
  assert.equal(entry.receipt_id, "rct-1");
  assert.equal(entry.state, "sent");
  assert.equal(entry.sent_at, 10);
  assert.equal(store.hasNonTerminal("w1:p2"), true);
});

test("a new send to the pane replaces the tracker", () => {
  const { store } = storeWithSend();
  store.start("w1:p2", { id: "rct-2", outcome: "sent" });
  assert.equal(store.get("w1:p2").receipt_id, "rct-2");
});

test("an untracked outcome never disturbs the existing tracker", () => {
  const { store } = storeWithSend();
  store.start("w1:p2", { id: "rct-9", outcome: "typed_not_submitted" });
  assert.equal(store.get("w1:p2").receipt_id, "rct-1");
});

test("SSE events for the same receipt merge into the entry", () => {
  const { store } = storeWithSend();
  store.applyEvent({
    receipt_id: "rct-1", pane_id: "w1:p2", state: "working", elapsed_s: 4,
  });
  const entry = store.get("w1:p2");
  assert.equal(entry.state, "working");
  assert.equal(entry.sent_at, 10);          // sent_at survives the merge
  assert.equal(entry.stalled, false);       // absent key reads as false

  store.applyEvent({
    receipt_id: "rct-1", pane_id: "w1:p2", state: "working", elapsed_s: 200,
    stalled: true,
  });
  assert.equal(store.get("w1:p2").stalled, true);
});

test("a stalled flag clears when a later event omits it", () => {
  const { store } = storeWithSend();
  store.applyEvent({
    receipt_id: "rct-1", pane_id: "w1:p2", state: "working", stalled: true,
  });
  store.applyEvent({
    receipt_id: "rct-1", pane_id: "w1:p2", state: "working",
  });
  assert.equal(store.get("w1:p2").stalled, false);
});

test("an adopted event derives sent_at from elapsed_s", () => {
  const { store, tick } = storeWithSend();
  tick(20500);
  store.applyEvent({
    receipt_id: "rct-x", pane_id: "w9:p1", state: "working", elapsed_s: 5,
  });
  const entry = store.get("w9:p1");
  assert.equal(entry.state, "working");
  assert.equal(entry.sent_at, 20.5 - 5);    // now - elapsed
});

test("a superseded event for an older receipt is ignored", () => {
  const { store } = storeWithSend();
  store.start("w1:p2", { id: "rct-2", outcome: "sent" });
  store.applyEvent({
    receipt_id: "rct-1", pane_id: "w1:p2", state: "superseded", elapsed_s: 9,
  });
  assert.equal(store.get("w1:p2").receipt_id, "rct-2");
  assert.equal(store.get("w1:p2").state, "sent");
});

test("a newer receipt's event replaces the entry", () => {
  const { store } = storeWithSend();
  store.applyEvent({
    receipt_id: "rct-2", pane_id: "w1:p2", state: "sent", elapsed_s: 0,
  });
  assert.equal(store.get("w1:p2").receipt_id, "rct-2");
});

test("a terminal entry never regresses to a non-terminal state", () => {
  const { store } = storeWithSend();
  store.applyEvent({
    receipt_id: "rct-1", pane_id: "w1:p2", state: "done", elapsed_s: 9,
  });
  store.applyEvent({
    receipt_id: "rct-1", pane_id: "w1:p2", state: "working", elapsed_s: 12,
  });
  assert.equal(store.get("w1:p2").state, "done");
});

test("an event with an unknown state is dropped whole", () => {
  const { store } = storeWithSend();
  store.applyEvent({
    receipt_id: "rct-1", pane_id: "w1:p2", state: "quantum", elapsed_s: 1,
  });
  assert.equal(store.get("w1:p2").state, "sent");
});

test("an error event carries its masked error", () => {
  const { store } = storeWithSend();
  store.applyEvent({
    receipt_id: "rct-1", pane_id: "w1:p2", state: "error", elapsed_s: 3,
    error: { name: "APIError", message: "quota" },
  });
  const entry = store.get("w1:p2");
  assert.equal(entry.state, "error");
  assert.deepEqual(entry.error, { name: "APIError", message: "quota" });
});

/* ---- receipts polling convergence -------------------------------------- */

test("the newest tracked receipt from the listing wins", () => {
  const { store } = storeWithSend();
  /* The listing is newest first: rct-2 (the newer send, working) rides
   * above rct-1, whose retained tracker is already superseded. */
  store.applyReceipts("w1:p2", [
    { id: "rct-2", outcome: "sent", track: { state: "working", sent_at: 21, elapsed_s: 1, stalled: false } },
    { id: "rct-1", outcome: "sent", track: { state: "superseded", sent_at: 10, elapsed_s: 5, stalled: false } },
  ]);
  const entry = store.get("w1:p2");
  assert.equal(entry.receipt_id, "rct-2");
  assert.equal(entry.state, "working");
  assert.equal(entry.sent_at, 21);
});

test("a listing without trackers leaves the stored entry alone", () => {
  const { store } = storeWithSend();
  store.applyReceipts("w1:p2", [
    { id: "rct-1", outcome: "sent" },
    { id: "rct-0", outcome: "discarded" },
  ]);
  assert.equal(store.get("w1:p2").state, "sent");
});

test("the same receipt's snapshot merges like an event", () => {
  const { store } = storeWithSend();
  store.applyReceipts("w1:p2", [
    { id: "rct-1", outcome: "sent", track: { state: "no_start", sent_at: 10, elapsed_s: 15, stalled: false } },
  ]);
  assert.equal(store.get("w1:p2").state, "no_start");
  assert.equal(store.hasNonTerminal("w1:p2"), false);
});

/* ---- dismiss ----------------------------------------------------------- */

test("dismiss removes the pane's card state", () => {
  const { store } = storeWithSend();
  assert.equal(store.dismiss("w1:p2"), true);
  assert.equal(store.get("w1:p2"), null);
  assert.equal(store.dismiss("w1:p2"), false);
});

/* ---- the hot-poll cadence ---------------------------------------------- */

test("the first fetch is always due", () => {
  assert.equal(SendTrack.fetchDue(1000, null, 0), true);
});

test("inside the hot window the cadence is 1.5 s", () => {
  const hotUntil = 12000;
  assert.equal(SendTrack.fetchDue(10000, 9000, hotUntil), false); // 1.0 s
  assert.equal(SendTrack.fetchDue(10600, 9000, hotUntil), true);  // 1.6 s
});

test("after the hot window the cadence returns to 5 s", () => {
  const hotUntil = 12000;
  assert.equal(SendTrack.fetchDue(13000, 9000, hotUntil), false); // 4 s, window over
  assert.equal(SendTrack.fetchDue(14100, 9000, hotUntil), true);  // 5.1 s
});
