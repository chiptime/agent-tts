/* Send receipts (S12) — PURE logic, no DOM.
 *
 * The composer's send is VERIFIED: the brain reads the pane screen
 * before and after the reply and answers with a receipt whose outcome
 * is a closed set. This module holds everything decidable without the
 * DOM: the Spanish label and the SPOKEN phrase of every outcome (the
 * voice path may only say «enviado» for effective/sent, and must say
 * the uncertainty out loud when unknown), the exact bodies the
 * submit/discard follow-ups POST, the receipt card model, and the
 * Stop button's double-tap arming state machine. app.js owns the
 * fetching (withDeviceAuth) and the DOM painting (text nodes only).
 *
 * Steer is a deliberate secondary action: replyPayload only gains
 * `steer: true` when the caller passes it — the module offers no way
 * to make it the default. */
(function (global) {
  "use strict";

  /* The closed outcome set, in the server's vocabulary. */
  var OUTCOMES = [
    "effective", "sent", "typed_not_submitted", "discarded", "refused", "unknown",
  ];

  /* The Spanish inline label of each outcome. "Enviado…" is reserved
   * for effective/sent: anything else must not read as delivered. */
  var OUTCOME_LABELS = {
    effective: "Enviado — el agente tomó el turno",
    sent: "Enviado — sin confirmación aún",
    typed_not_submitted: "Quedó escrito sin enviar",
    discarded: "Descartado",
    refused: "El agente no aceptó el texto",
    unknown: "No puedo confirmar si se envió",
  };

  /* The spoken confirmation. Voice is the only channel that cannot
   * show a receipt card, so the phrase carries the outcome honestly:
   * «enviado» only for effective/sent; unknown says so explicitly. */
  var SPEECH_PHRASES = {
    effective: "Enviado. El agente tomó el turno.",
    sent: "Enviado. Sin confirmación todavía.",
    typed_not_submitted: "Quedó escrito sin enviar. Puedes enviarlo o descartarlo.",
    discarded: "Descartado.",
    refused: "El agente no aceptó el texto.",
    unknown: "No puedo confirmar si se envió.",
  };

  function outcomeLabel(outcome) {
    return OUTCOME_LABELS[outcome] || OUTCOME_LABELS.unknown;
  }

  function speakPhrase(outcome) {
    return SPEECH_PHRASES[outcome] || null;
  }

  /* Says the outcome through the local speech synthesizer when the
   * browser offers one; never throws, never blocks the card. */
  function speakOutcome(outcome, win) {
    var phrase = speakPhrase(outcome);
    if (!phrase) return;
    var scope = win || (typeof window !== "undefined" ? window : null);
    var synth = scope && scope.speechSynthesis;
    if (!synth || !scope.SpeechSynthesisUtterance) return;
    try {
      var utterance = new scope.SpeechSynthesisUtterance(phrase);
      utterance.lang = "es-ES";
      synth.speak(utterance);
    } catch (error) { /* speech is a courtesy, never a dependency */ }
  }

  /* The exact bodies the follow-up endpoints expect — nothing else
   * travels (the draft's text NEVER re-sends; only the receipt id). */
  function submitPayload(receiptId) {
    return { receipt_id: String(receiptId || "") };
  }

  function discardPayload(receiptId) {
    return { receipt_id: String(receiptId || "") };
  }

  /* The composer body: `steer` rides along ONLY when explicitly asked
   * (the long-press path) — the default body is byte-identical to the
   * pre-S12 composer body. */
  function replyPayload(paneId, text, steer) {
    var body = { pane_id: paneId, text: text };
    if (steer) body.steer = true;
    return body;
  }

  /* The receipt card: a "typed" variant offers Submit/Discard (the
   * only outcome that may act), every other variant is information. */
  function cardModel(receipt) {
    if (!receipt || !receipt.id) return null;
    var outcome = receipt.outcome;
    var typed = outcome === "typed_not_submitted";
    return {
      variant: typed ? "typed" : "info",
      outcome: outcome,
      label: outcomeLabel(outcome),
      detail: String(receipt.detail == null ? "" : receipt.detail),
      submitUrl: typed ? "/pane/receipt/" + receipt.id + "/submit" : null,
      discardUrl: typed ? "/pane/receipt/" + receipt.id + "/discard" : null,
    };
  }

  /* The Stop button's double-tap arming: the first tap arms for a
   * short window, only the second tap INSIDE the window sends the
   * interrupt. A tap outside the window re-arms — a stale arm can
   * never fire. `now` is injectable for tests. */
  function createStopArming(now, armMs) {
    var clock = now || function () { return Date.now(); };
    var window_ = armMs || 3000;
    var armedAt = null;

    function isArmed() {
      return armedAt !== null && clock() - armedAt <= window_;
    }

    function tap() {
      if (isArmed()) {
        armedAt = null;
        return "fire";
      }
      armedAt = clock();
      return "armed";
    }

    function disarm() {
      armedAt = null;
    }

    return { tap: tap, disarm: disarm, isArmed: isArmed };
  }

  var api = {
    OUTCOMES: OUTCOMES,
    OUTCOME_LABELS: OUTCOME_LABELS,
    SPEECH_PHRASES: SPEECH_PHRASES,
    outcomeLabel: outcomeLabel,
    speakPhrase: speakPhrase,
    speakOutcome: speakOutcome,
    submitPayload: submitPayload,
    discardPayload: discardPayload,
    replyPayload: replyPayload,
    cardModel: cardModel,
    createStopArming: createStopArming,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { Receipts: api };
  }
  global.Receipts = api;
})(typeof window !== "undefined" ? window : globalThis);
