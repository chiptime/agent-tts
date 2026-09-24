/* Approval gate client flow — PURE logic, no DOM, no timers.
 *
 * The client half of the action approval gate (PRD-action-approval-gate
 * §4–§6). While a gate is live the call sits in "confirming" and STT
 * utterances route to POST /approval/{gate_id}/resolve — never /ask.
 * Server contracts are locked by T4 and consumed as-is:
 *
 * - POST /approval/{id}/resolve {utterance} -> {decision, answer,
 *   audio_url, approval}, decision in approve | reject | listen_replace
 *   | reprompt. approve already contains the replayed report (exact /ask
 *   shape); reject (incl. the auto-reject after a second ambiguous
 *   utterance) is silent; reprompt carries the spoken "¿Sí o no?";
 *   listen_replace leaves the gate untouched.
 * - PATCH /approval/{id} {text} -> {ok, approval} — the timer restarts
 *   server-side (created_at reset) and NO audio comes back: the client
 *   re-echoes the revised text through POST /tts itself.
 * - 404 "approval gate not found or no longer active" = expired or
 *   terminal: silent return toward cancel; the server lazy-expires on
 *   every touch, so the client countdown is advisory only.
 *
 * Precedence during the ONE dictation round after listen_replace: the
 * dictated utterance still goes to /resolve first, so a change of mind
 * ("sí") resolves server-side as usual. Only a non-command utterance
 * (reprompt outcome — it matched no keyword) becomes the NEW text via
 * PATCH (PRD §5).
 *
 * The clock (`now`) and HTTP (`request`, fetch-shaped: {status, ok,
 * json()}) are injected the same way endpointing.js takes `now`; app.js
 * owns the 1s tick interval, the callState pill and the audio queue.
 *
 * setState emissions: "confirming" while a gate is live (also during the
 * dictation round), "thinking" while a resolve/PATCH round is in flight,
 * "listening" on silent/terminal outcomes. Approve with audio renders
 * through renderAnswer and hands the return to listening to the audio
 * queue (app.js re-arms the mic when playback ends), so that path emits
 * no "listening" itself.
 *
 * Dual environment: browser global (window.ApprovalFlow) and CommonJS
 * for node --test.
 */
(function (global) {
  "use strict";

  function createApprovalFlow(options) {
    options = options || {};
    var now = options.now || function () { return Date.now(); };
    var request = options.request || function () {
      return Promise.resolve({ status: 0, ok: false, json: function () {} });
    };
    var setState = options.setState || function () {};
    var renderAnswer = options.renderAnswer || function () {};
    var playAudio = options.playAudio || function () {};
    var onExpired = options.onExpired || function () {};
    var banner = options.banner || function () {};

    var gate = null;        // live approval payload {gate_id, text, expires_in_s, ...}
    var expiresAt = null;   // epoch ms when the countdown reaches 0
    var expired = false;    // countdown reached 0 (payload kept for the gray card)
    var dictating = false;  // listen_replace: next utterance is the NEW text
    var resolving = false;  // a resolve/PATCH/echo round is in flight

    function arm(approval) {
      gate = approval;
      expiresAt = now() + (approval.expires_in_s || 0) * 1000;
      expired = false;
      dictating = false;
      setState("confirming");
    }

    function finish() {
      gate = null;
      expiresAt = null;
      expired = false;
      dictating = false;
    }

    function gone() {
      // 404 / vanished mid-flight: silent terminal, toward cancel.
      resolving = false;
      finish();
      setState("listening");
    }

    function post(url, body) {
      return request(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body)
      });
    }

    /* Re-echo after PATCH: the server sends no audio on revision (locked
     * T4 contract), so the client synthesizes the revised text via /tts
     * and plays it through the normal queue; the queue returns the call
     * to confirming when playback ends. */
    function reEcho(text) {
      post("/tts", { text: text })
        .then(function (resp) {
          if (!resp.ok) throw new Error("HTTP " + resp.status);
          return resp.json();
        })
        .then(function (data) {
          resolving = false;
          if (data && data.audio_url) {
            playAudio(data.audio_url);
          } else {
            setState("confirming");  // nothing to play: re-arm the mic
          }
        })
        .catch(function () {
          resolving = false;
          banner("No pude sintetizar el audio — revisa la conexión.");
          setState("confirming");    // text-only re-confirmation
        });
    }

    function patchText(text) {
      if (!gate || expired) {
        gone();
        return;
      }
      setState("thinking");
      request("/approval/" + encodeURIComponent(gate.gate_id), {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ text: text })
      })
        .then(function (resp) {
          if (resp.status === 404) return null;
          if (!resp.ok) throw new Error("HTTP " + resp.status);
          return resp.json();
        })
        .then(function (data) {
          if (!data || !data.approval || !gate || expired) {
            gone();
            return;
          }
          gate = data.approval;  // created_at reset server-side: restart the ring
          expiresAt = now() + (data.approval.expires_in_s || 0) * 1000;
          dictating = false;
          reEcho(data.approval.text);
        })
        .catch(function () {
          resolving = false;
          banner("No pude actualizar el texto — inténtalo otra vez.");
          if (gate && !expired) setState("confirming");
          else setState("listening");
        });
    }

    function handleResolve(data, utterance) {
      var decision = data && data.decision;
      if (dictating) {
        if (decision === "reprompt") {
          // The dictation matched no command: during the dictation round
          // the utterance IS the new text (commands were matched
          // server-side first — PRD §5 precedence).
          patchText(utterance);
          return;
        }
        if (decision === "listen_replace") {
          // Replace-intent repeated: keep waiting for the actual text.
          resolving = false;
          setState("confirming");
          return;
        }
        dictating = false;  // approve/reject during dictation: as usual
      }
      if (decision === "approve") {
        // The replay already ran server-side inside /resolve: render the
        // report through the same path as a normal /ask answer. With
        // audio the queue owns the return to listening; without it the
        // explicit "listening" emission re-arms the mic.
        resolving = false;
        finish();
        renderAnswer({ answer: data.answer, audio_url: data.audio_url });
        if (!data.audio_url) setState("listening");
        return;
      }
      if (decision === "reject") {
        // Silent (PRD §5): reject speaks nothing, client or server.
        resolving = false;
        finish();
        setState("listening");
        return;
      }
      if (decision === "reprompt") {
        resolving = false;
        if (!gate || expired) {
          gone();
          return;
        }
        if (data.audio_url) playAudio(data.audio_url);  // re-echo "¿Sí o no?"
        else setState("confirming");                    // TTS failed server-side
        return;
      }
      if (decision === "listen_replace") {
        resolving = false;
        if (!gate || expired) {
          gone();
          return;
        }
        dictating = true;  // ONE dictation round starts now
        setState("confirming");
        return;
      }
      // Unknown decision (outside the contract): inert and retryable.
      resolving = false;
      setState("confirming");
    }

    function routeUtterance(text) {
      if (!gate || expired) return false;  // caller dispatches a normal /ask
      if (resolving) return true;          // a round is in flight: swallow it
      resolving = true;
      setState("thinking");
      post("/approval/" + encodeURIComponent(gate.gate_id) + "/resolve", {
        utterance: text
      })
        .then(function (resp) {
          if (resp.status === 404) return null;
          if (!resp.ok) throw new Error("HTTP " + resp.status);
          return resp.json();
        })
        .then(function (data) {
          if (data === null) {
            gone();  // expired/terminal at touch: silent
            return;
          }
          handleResolve(data, text);
        })
        .catch(function () {
          resolving = false;
          banner("No pude consultar la confirmación — inténtalo otra vez.");
          if (gate && !expired) setState("confirming");
          else setState("listening");
        });
      return true;
    }

    return {
      active: function () { return !!gate && !expired; },
      gate: function () { return gate; },
      isDictating: function () { return dictating; },
      isExpired: function () { return expired; },
      remainingSeconds: function () {
        if (!gate) return 0;
        return Math.max(0, Math.ceil((expiresAt - now()) / 1000));
      },
      open: function (approval) {
        if (!approval || !approval.gate_id) return;
        arm(approval);
      },
      tick: function () {
        if (!gate || expired) return;
        if (expiresAt - now() <= 0) {
          // Silent expiry (PRD §6): no spoken line — the server
          // lazy-expires the gate on its next touch. The payload stays
          // so the UI can gray the card out.
          expired = true;
          dictating = false;
          onExpired();
          setState("listening");
        }
      },
      routeUtterance: routeUtterance,
      cancel: function () {
        // Hang-up / new conversation: silent drop, no emission.
        gate = null;
        expiresAt = null;
        expired = false;
        dictating = false;
        resolving = false;
      }
    };
  }

  var api = { createApprovalFlow: createApprovalFlow };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    global.ApprovalFlow = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
