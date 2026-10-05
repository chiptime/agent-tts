/* Speech request identity + server-side cancel (voice-stack VS1.7,
 * TECHNICAL-PLAN T1/T2).
 *
 * Pure UMD module following the announce.js/toast.js house pattern: every
 * browser touch (fetch, timers, crypto) is injected, so the Node suite
 * drives it without a DOM and app.js stays thin wiring.
 *
 * Contract:
 *  - The PWA mints the request id (crypto.randomUUID) AND a capability
 *    token (>= 256 bits from crypto.getRandomValues) BEFORE /ask, because
 *    /ask is synchronous: an id minted by the server would arrive too late
 *    to cancel mid-render. Both ride the /ask body; the token never goes in
 *    a URL, a log line, or any status object.
 *  - cancel() POSTs /speech/{id}/cancel with {session_id, speech_cancel_token}.
 *    Local audio stops IMMEDIATELY (the caller's job); the POST retries a
 *    finite number of times on network/5xx failure (SPEECH_CANCEL_NET_RETRY,
 *    fixed backoff) and then reports the VISIBLE state "cancel-unconfirmed" —
 *    never a silent catch and never a false "cancelled by the server".
 *  - purgeQueueById removes ONLY the queue items of one request, so other
 *    requests' audio and ambient announcements keep their turn.
 */
(function (global) {
  "use strict";

  var ID_PATTERN = /^[A-Za-z0-9._-]{8,64}$/;
  var CANCEL_NET_RETRY = 2;      // T11 SPEECH_CANCEL_NET_RETRY
  var CANCEL_BACKOFF_MS = 1000;  // fixed, deterministic backoff

  function validId(value) {
    return typeof value === "string" && ID_PATTERN.test(value);
  }

  function toHex(bytes) {
    var out = "";
    for (var i = 0; i < bytes.length; i++) {
      out += (bytes[i] < 16 ? "0" : "") + bytes[i].toString(16);
    }
    return out;
  }

  /* crypto.randomUUID exists only in secure contexts, and this PWA is also
   * served over plain HTTP on the LAN: derive a v4 UUID from
   * getRandomValues (available everywhere) when it is missing. */
  function randomId(cryptoObj) {
    if (typeof cryptoObj.randomUUID === "function") return cryptoObj.randomUUID();
    var b = new Uint8Array(16);
    cryptoObj.getRandomValues(b);
    b[6] = (b[6] & 0x0f) | 0x40;  // version 4
    b[8] = (b[8] & 0x3f) | 0x80;  // RFC 4122 variant
    var h = toHex(b);
    return h.slice(0, 8) + "-" + h.slice(8, 12) + "-" + h.slice(12, 16) + "-" +
      h.slice(16, 20) + "-" + h.slice(20);
  }

  /* One fresh identity: { id, token }. token = 32 random bytes (256 bits). */
  function mintSpeechRequest(cryptoObj) {
    var bytes = new Uint8Array(32);
    cryptoObj.getRandomValues(bytes);
    return { id: randomId(cryptoObj), token: toHex(bytes) };
  }

  /* Removes the queue items tagged with `id`, in place; returns how many. */
  function purgeQueueById(queue, id) {
    var kept = [];
    var removed = 0;
    for (var i = 0; i < queue.length; i++) {
      if (queue[i] && queue[i].speechRequestId === id) removed += 1;
      else kept.push(queue[i]);
    }
    queue.length = 0;
    for (var j = 0; j < kept.length; j++) queue.push(kept[j]);
    return removed;
  }

  /* deps: { fetch(url, opts) -> Promise<Response>, setTimeout, crypto,
   *         onStatus(state) } — onStatus receives plain { state, ... }
   * objects and NEVER the token. */
  function createSpeechController(deps) {
    var active = null;  // { id, token, sessionId }

    function notify(status) {
      if (deps.onStatus) deps.onStatus(status);
    }

    /* Starts a new request identity; the body fields go into /ask. Without
     * a usable crypto the request degrades to the legacy (uncancellable)
     * path exactly as before: null, nothing tracked. */
    function begin(sessionId) {
      var minted;
      try {
        minted = mintSpeechRequest(deps.crypto);
      } catch (err) {
        active = null;
        return null;
      }
      active = { id: minted.id, token: minted.token, sessionId: sessionId };
      return { speech_request_id: minted.id, speech_cancel_token: minted.token };
    }

    function activeId() {
      return active ? active.id : null;
    }

    /* The job is over (answer without audio, audio finished, failure). */
    function release(id) {
      if (active && active.id === id) active = null;
    }

    function postCancel(job) {
      return deps.fetch("/speech/" + encodeURIComponent(job.id) + "/cancel", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          session_id: job.sessionId,
          speech_cancel_token: job.token
        })
      });
    }

    /* Cancels the active request on the server. Resolves with the final
     * visible state; never rejects. */
    function cancel() {
      var job = active;
      if (!job) return Promise.resolve({ state: "no-active-job" });
      active = null;  // a second stop while retrying must not double-post
      notify({ state: "cancelling", id: job.id });

      function attempt(retriesLeft) {
        return postCancel(job).then(
          function (resp) {
            if (resp.status === 403) return finish({ state: "cancel-forbidden", id: job.id });
            if (resp.status === 422) return finish({ state: "cancel-rejected", id: job.id });
            if (resp.ok) {
              return resp.json().then(
                function (data) {
                  return finish({ state: (data && data.status) || "cancelled", id: job.id });
                },
                function () { return finish({ state: "cancelled", id: job.id }); }
              );
            }
            return retryOrGiveUp(retriesLeft);  // 5xx and other transient replies
          },
          function () { return retryOrGiveUp(retriesLeft); }  // network failure
        );
      }

      function retryOrGiveUp(retriesLeft) {
        if (retriesLeft <= 0) return finish({ state: "cancel-unconfirmed", id: job.id });
        return new Promise(function (resolve) {
          deps.setTimeout(function () { resolve(attempt(retriesLeft - 1)); }, CANCEL_BACKOFF_MS);
        });
      }

      function finish(status) {
        notify(status);
        return status;
      }

      return attempt(CANCEL_NET_RETRY);
    }

    return { begin: begin, activeId: activeId, release: release, cancel: cancel };
  }

  var api = {
    ID_PATTERN: ID_PATTERN,
    CANCEL_NET_RETRY: CANCEL_NET_RETRY,
    CANCEL_BACKOFF_MS: CANCEL_BACKOFF_MS,
    validId: validId,
    mintSpeechRequest: mintSpeechRequest,
    purgeQueueById: purgeQueueById,
    createSpeechController: createSpeechController
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    global.Speech = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
