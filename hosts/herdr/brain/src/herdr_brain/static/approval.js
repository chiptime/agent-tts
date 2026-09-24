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
 *
 * Button entry points (the drawer card, T6): approve()/reject() POST the
 * dedicated endpoints with the same handling as their voice twins,
 * patchText(text) is the manual-edit path (PATCH + client-side /tts
 * re-echo) and redictate() enters the dictation round directly — the
 * next utterance still resolves first (command precedence, PRD §5).
 * All of them refuse without a live gate, and the HTTP ones also refuse
 * while a round is in flight (the caller falls back to no-op).
 *
 * Boot reload recovery (T7): recover(sessionId) GETs
 * /approval/current?session_id=… and re-arms a live gate through the
 * same open() path — card, pill and mic routing follow from the
 * existing emissions. Every failure is silent and the promise never
 * rejects, so the caller can fire it during init without guarding.
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

    /* Silent expiry (PRD §6): no spoken line — the server lazy-expires
     * the gate on its next touch. The payload stays so the UI can gray
     * the card out. */
    function expireIfDue() {
      if (!gate || expired) return;
      /* A round in flight owns the state: the approve replay blocks
       * server-side for minutes and its request timeout is the real
       * deadline. When it settles, resolving clears and the next tick
       * can expire normally. */
      if (resolving) return;
      if (expiresAt - now() <= 0) {
        expired = true;
        dictating = false;
        onExpired();
        setState("listening");
      }
    }

    function post(url, body, timeoutMs) {
      return request(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body)
      }, timeoutMs);
    }

    /* The approve replay blocks server-side until the agent completes
     * the frozen send (up to timeout_ms) plus the report round-trip —
     * the request needs that budget plus margin, not the caller's short
     * default: a mid-replay client timeout would strand an approved
     * gate with no report. */
    function replayTimeoutMs() {
      return (gate && gate.timeout_ms ? gate.timeout_ms : 120000) + 30000;
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
        return false;
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
      return true;
    }

    /* Button path ([✓ Enviar]): POST /approve replays the frozen call
     * and answers with the exact /ask shape — identical handling to a
     * spoken approval, just without the lexicon hop. */
    function approve() {
      if (!gate || expired || resolving) return false;
      resolving = true;
      setState("thinking");
      post("/approval/" + encodeURIComponent(gate.gate_id) + "/approve", {}, replayTimeoutMs())
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
          resolving = false;
          finish();
          renderAnswer({ answer: data.answer, audio_url: data.audio_url });
          if (!data.audio_url) setState("listening");
        })
        .catch(function () {
          resolving = false;
          banner("No pude confirmar el envío — inténtalo otra vez.");
          if (gate && !expired) setState("confirming");
          else setState("listening");
        });
      return true;
    }

    /* Button path ([✕ Cancelar]): POST /reject is silent server-side
     * (PRD §5) — straight back to listening, nothing rendered. */
    function reject() {
      if (!gate || expired || resolving) return false;
      resolving = true;
      setState("thinking");
      post("/approval/" + encodeURIComponent(gate.gate_id) + "/reject", {})
        .then(function (resp) {
          if (resp.status === 404) return null;
          if (!resp.ok) throw new Error("HTTP " + resp.status);
          return resp.json();
        })
        .then(function (data) {
          if (data === null) {
            gone();
            return;
          }
          resolving = false;
          finish();
          setState("listening");
        })
        .catch(function () {
          resolving = false;
          banner("No pude cancelar el envío — inténtalo otra vez.");
          if (gate && !expired) setState("confirming");
          else setState("listening");
        });
      return true;
    }

    /* Button path ([🎙 Re-dictar]): enter the dictation round directly.
     * The next utterance still goes to /resolve FIRST (approve/reject
     * commands keep working; only a non-command PATCHes as new text). */
    function redictate() {
      if (!gate || expired || resolving) return false;
      dictating = true;
      setState("confirming");
      return true;
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
      }, replayTimeoutMs())
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

    /* Boot reload recovery (T7, PRD §5): GET /approval/current with the
     * client's session id; a live gate re-enters confirming via arm()
     * (the same path as open()). Every failure — network, non-ok, or a
     * malformed body — is a silent no-op and the promise never rejects:
     * no gate equals normal boot.
     *
     * Resolves to the armed payload when a LIVE gate was recovered (the
     * caller re-opens the drawer with the restored card) and null
     * otherwise — including a payload whose window already closed
     * (expires_in_s 0, the lazy-expiry razor edge): it is expired on
     * the spot through the normal silent path, never a zombie
     * confirming state. */
    function recover(sessionId) {
      var qs = sessionId ? "?session_id=" + encodeURIComponent(sessionId) : "";
      return request("/approval/current" + qs)
        .then(function (resp) {
          if (!resp.ok) return null;
          return resp.json();
        })
        .then(function (data) {
          var approval = data && data.approval;
          if (!approval || !approval.gate_id) return null;
          arm(approval);
          expireIfDue();
          return gate && !expired ? approval : null;
        })
        .catch(function () {
          return null;
        });
    }

    return {
      active: function () { return !!gate && !expired; },
      gate: function () { return gate; },
      isDictating: function () { return dictating; },
      isBusy: function () { return resolving; },
      isExpired: function () { return expired; },
      remainingSeconds: function () {
        if (!gate) return 0;
        return Math.max(0, Math.ceil((expiresAt - now()) / 1000));
      },
      open: function (approval) {
        if (!approval || !approval.gate_id) return;
        arm(approval);
      },
      recover: recover,
      tick: function () {
        expireIfDue();
      },
      routeUtterance: routeUtterance,
      patchText: patchText,
      approve: approve,
      reject: reject,
      redictate: redictate,
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
