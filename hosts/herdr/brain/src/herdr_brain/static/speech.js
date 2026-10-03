/* Speech request identity + server-side cancel (voice-stack VS1.7,
 * TECHNICAL-PLAN T1/T2) and the segmented delivery client (VS2.6, T6).
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

  /* ==== segmented delivery client (VS2.6, T6) ===========================
   *
   * One instance streams ONE job: it long-polls GET /speech/{id}/next and
   * feeds segments into the caller's global sequential audio queue as
   * plain items {speechRequestId, seq, url} — announcements and foreign
   * turns keep interleaving exactly as before (the queue stays sequential).
   *
   * Watermarks (T6 locked):
   *  - after: highest seq received+enqueued. The server only ever serves
   *    after+1, but duplicated HTTP delivery can still happen on client
   *    re-poll races — anything <= after is DISCARDED, never re-enqueued.
   *  - ack: highest CONTIGUOUS seq consumed. Advanced ONLY by onEnded from
   *    the <audio> element's ended event (or an explicit local stop, whose
   *    server story is the cancel POST); late/duplicate ended events never
   *    re-ack and the watermark never regresses. Both watermarks ride
   *    EVERY poll, so a reconnect is simply the next poll from memory.
   *
   * Acks are playback evidence (T5): after the final segment is consumed,
   * one last poll delivers ack === finalSeq, then the job completes.
   */

  var SPEECH_CLIENT_PREFETCH = 1;              // max received-not-consumed segments
  var SPEECH_CLIENT_FETCH_TIMEOUT_MS = 15000;  // > the server's 10s long-poll hold
  var SPEECH_SEGMENT_RETRY = 2;                // retries per poll on fetch failure
  var SEGMENT_RETRY_BACKOFF_MS = 1000;         // fixed, deterministic backoff
  var SEGMENT_FLUSH_POLL_MS = 250;             // min spacing between flush-phase polls

  var TERMINAL_JOB_STATUSES = {
    "cancelled": true,
    "degraded": true,
    "failed": true,
    "expired-unconsumed": true
  };

  /* deps: { fetch(url, opts) -> Promise<Response>, setTimeout, crypto
   *         (unused; kept for symmetry with the controller), onStatus
   *         (optional), enqueue(item) -> void, now (optional, unused) }.
   * The player owns its fetch-timeout race (SPEECH_CLIENT_FETCH_TIMEOUT_MS),
   * so callers inject a BARE fetch; a late timer reject on an already
   * settled promise is a no-op, which is exactly the desired race. */
  function createSegmentPlayer(deps) {
    var id = null;
    var sessionId = null;
    var generation = 0;  // start() bumps it: late responses of a superseded job are dropped
    var after = -1;      // highest seq received + enqueued
    var ack = -1;        // highest contiguous seq consumed (ended)
    var finalSeq = null; // set when is_final is received
    var finalAckDelivered = false;
    var stopped = false;
    var degraded = false;
    var terminalStatus = null;
    var stopReason = null;
    var inFlight = false;
    var flushPollPending = false;  // a flush-phase poll is spaced via the timer

    function notify(status) {
      if (deps.onStatus) deps.onStatus(status);
    }

    function state() {
      return {
        id: id, sessionId: sessionId, after: after, ack: ack,
        finalSeq: finalSeq, finalAckDelivered: finalAckDelivered,
        stopped: stopped, degraded: degraded, terminalStatus: terminalStatus,
        stopReason: stopReason, inFlight: inFlight
      };
    }

    function start(jobInfo) {
      id = jobInfo.id;
      sessionId = jobInfo.sessionId;
      generation += 1;
      after = -1;
      ack = -1;
      finalSeq = null;
      finalAckDelivered = false;
      stopped = false;
      degraded = false;
      terminalStatus = null;
      stopReason = null;
      inFlight = false;
      flushPollPending = false;
      maybePoll();
    }

    /* Terminal local stop (stop button / hang-up): polling and enqueueing
     * die here; the queue purge belongs to the caller (stopAudio). */
    function stop(reason) {
      if (stopped) return;
      stopped = true;
      stopReason = reason || "local-stop";
    }

    /* Consumption evidence: the watermark advances ONLY on the contiguous
     * next seq belonging to THIS job's received range. Late/duplicate
     * ended events (seq <= ack) and stale segments of a superseded job
     * (seq > after) are ignored — no double-ack, no regression, no
     * invariant-violating ack > after on the wire. */
    function onEnded(seq) {
      if (seq !== ack + 1 || seq > after) return;
      ack = seq;
      maybePoll();  // a slot opened — or the final ack can now flush
    }

    /* Poll gate: exactly one next() in flight at any time; prefetch caps
     * the received-not-consumed backlog; once the final seq is consumed the
     * ONLY remaining poll is the one that delivers ack === finalSeq.
     *
     * Flush pacing (VS2 remediation): once finalSeq is known, no new
     * segment can ever arrive — every later poll only flushes the ack
     * watermark or learns a terminal server status, and terminal replies
     * are IMMEDIATE, so an unpaced re-poll spins at loopback speed (213
     * requests measured for a 3-segment stream). While the final ack is
     * still PENDING (ack < finalSeq) those wait polls are spaced by
     * SEGMENT_FLUSH_POLL_MS through the injected timer; waiting for NEW
     * segments stays unpaced (the server's 10s long-poll hold already
     * paces that path). The final-ack flush itself (ack >= finalSeq) is
     * exempt: it is a ONE-SHOT request fired the moment the last segment
     * ends — the player stops on its reply, so there is no loop to pace,
     * and the job's completion evidence must never sit behind a timer. */
    function maybePoll() {
      if (stopped || inFlight) return;
      if (finalSeq !== null && ack < finalSeq) {
        if (flushPollPending) return;
        flushPollPending = true;
        var gen = generation;
        deps.setTimeout(function () {
          flushPollPending = false;
          if (gen !== generation) return;  // superseded job: hands off the new state
          pollNow();
        }, SEGMENT_FLUSH_POLL_MS);
        return;
      }
      pollNow();
    }

    function pollNow() {
      if (stopped || inFlight) return;
      if (finalSeq !== null && ack >= finalSeq) {
        poll();
        return;
      }
      if (after - ack > SPEECH_CLIENT_PREFETCH) return;
      poll();
    }

    function poll() {
      inFlight = true;
      var gen = generation;
      var url = "/speech/" + encodeURIComponent(id) + "/next?after=" + after +
        "&ack=" + ack + "&session_id=" + encodeURIComponent(sessionId);
      attempt(url, after, ack, SPEECH_SEGMENT_RETRY, gen);
    }

    /* Race the fetch against the client timeout (> the server hold): a
     * hung connection counts as a fetch failure, not a stuck player. */
    function fetchWithTimeout(url) {
      return new Promise(function (resolve, reject) {
        deps.setTimeout(function () {
          reject(new Error("segment fetch timeout"));
        }, SPEECH_CLIENT_FETCH_TIMEOUT_MS);
        deps.fetch(url, { method: "GET" }).then(resolve, reject);
      });
    }

    function attempt(url, sentAfter, sentAck, retriesLeft, gen) {
      fetchWithTimeout(url).then(
        function (resp) {
          if (gen !== generation) return;  // superseded job: hands off the new state
          if (stopped) { inFlight = false; return; }
          if (!resp || !resp.ok) {
            retryOrDegrade(url, sentAfter, sentAck, retriesLeft, gen);
            return;
          }
          resp.json().then(
            function (data) {
              if (gen !== generation) return;
              inFlight = false;
              handleData(data, sentAfter, sentAck);
            },
            function () {
              if (gen !== generation) return;
              if (stopped) { inFlight = false; return; }
              retryOrDegrade(url, sentAfter, sentAck, retriesLeft, gen);
            }
          );
        },
        function () {
          if (gen !== generation) return;
          if (stopped) { inFlight = false; return; }
          retryOrDegrade(url, sentAfter, sentAck, retriesLeft, gen);
        }
      );
    }

    function retryOrDegrade(url, sentAfter, sentAck, retriesLeft, gen) {
      if (retriesLeft <= 0) {
        /* Exhausted: VISIBLE degraded, never a silent skip, never a loop. */
        inFlight = false;
        stopped = true;
        degraded = true;
        notify({ state: "degraded", id: id, reason: "segment-fetch-failed" });
        return;
      }
      deps.setTimeout(function () {
        if (gen !== generation) return;
        if (stopped) { inFlight = false; return; }
        attempt(url, sentAfter, sentAck, retriesLeft - 1, gen);
      }, SEGMENT_RETRY_BACKOFF_MS);
    }

    function handleData(data, sentAfter, sentAck) {
      /* The request that just completed carried the full contiguous
       * watermark — the server now holds its final playback evidence. */
      if (finalSeq !== null && sentAfter === finalSeq && sentAck === finalSeq) {
        finalAckDelivered = true;
        stopped = true;
        notify({ state: "complete", id: id });
        return;
      }
      if (data && data.wait && TERMINAL_JOB_STATUSES[data.status]) {
        /* cancelled / degraded / failed / expired-unconsumed: the job is
         * terminal server-side — nothing further is served or enqueued. */
        terminalStatus = data.status;
        stopped = true;
        notify({ state: "server-" + data.status, id: id });
        return;
      }
      if (data && typeof data.seq === "number" &&
          typeof data.audio_url === "string" && data.seq > after) {
        after = data.seq;
        deps.enqueue({
          speechRequestId: id,
          seq: data.seq,
          url: data.audio_url,
          announcement: null
        });
        if (data.is_final) finalSeq = data.seq;
        maybePoll();
        return;
      }
      /* Duplicate HTTP delivery (seq <= after), {wait:true} holds (with or
       * without an unknown status), or a malformed body: watermarks intact
       * — keep the long-poll alive. */
      maybePoll();
    }

    return { start: start, onEnded: onEnded, stop: stop, state: state };
  }

  /* ==== pending announcements store (VS3.6, T8) ===========================
   *
   * The PHONE's own ledger of the ambient announcements it received over
   * SSE — bookkeeping + recovery ONLY, never a second playback queue:
   * the announcement itself keeps flowing through the announcer and the
   * sequential audio queue, and stopping a response job NEVER touches
   * these records (PRD03 FR-08: stop purges speech, keeps records — the
   * response job and the ambient records are separate domains).
   *
   * Persistence: localStorage[PENDING_STORAGE_KEY] holds
   * {records, overflow_count} as JSON. Honesty rules (locked T8):
   *  - PENDING_MAX_RECORDS caps the list; the OLDEST records beyond the
   *    cap are dropped from the list but COUNTED in overflow_count — a
   *    visible aggregate, never a fake-preserved list, never silent.
   *  - A save failure (QuotaExceededError, private mode, storage denied)
   *    is a visible data-integrity condition: the in-memory records are
   *    flagged persist_uncertain and pending ones marked state
   *    "uncertain" for the live session; deps.onStatus surfaces the
   *    condition so the UI can show the honest banner ("not persistent
   *    until reload"). A later successful save clears the flag — the
   *    list is durable again.
   *
   * Consolidation mirrors T7 (metadata-only): within the live session,
   * re-observing the same pane_id+status bumps repeat_count/last_seen_ts
   * and refreshes label/agent/text — no new record.
   */

  var PENDING_STORAGE_KEY = "herdr.speech.pending.v1";
  var PENDING_MAX_RECORDS = 100;  // PWA_PENDING_MAX_RECORDS (locked T8)
  var PENDING_TEXT_MAX = 1000;    // stored text bound, in characters

  var PENDING_STATES = {
    "pending": true, "announced": true, "uncertain": true, "discarded": true
  };

  /* deps: { storage: {getItem, setItem} (localStorage-shaped; every
   *         access is guarded — a missing/throwing storage degrades to
   *         the visible uncertain condition, never a crash),
   *         now() -> ms (optional), makeId() -> string (optional),
   *         onStatus(status) (optional; "persist-uncertain",
   *         "persist-restored", "overflow"), onChange(snapshot)
   *         (optional; fires after every visible mutation),
   *         onListen(text, record) (optional; the listen action hands
   *         the stored text to the caller, who regenerates the speech
   *         through the NORMAL pipeline) }. */
  function createPendingStore(deps) {
    deps = deps || {};
    var records = [];
    var overflowCount = 0;
    var persistFailed = false;
    var sessionKeys = {};  // pane_id + "\u0000" + status -> record id (THIS session only)
    var idSeq = 0;

    var now = typeof deps.now === "function"
      ? deps.now : function () { return Date.now(); };
    var makeId = typeof deps.makeId === "function"
      ? deps.makeId
      : function () {
          idSeq += 1;
          return "p-" + idSeq + "-" + Math.random().toString(36).slice(2, 8);
        };

    function notify(status) {
      if (deps.onStatus) deps.onStatus(status);
    }

    function emitChange() {
      if (deps.onChange) deps.onChange(snapshot());
    }

    function recordKey(paneId, status) {
      return paneId + "\u0000" + status;
    }

    /* Stored text is bounded so one pathological announcement cannot
     * blow the localStorage quota for every record after it. */
    function boundText(text) {
      if (typeof text !== "string") return "";
      if (text.length <= PENDING_TEXT_MAX) return text;
      return text.slice(0, PENDING_TEXT_MAX - 1) + "…";
    }

    function findById(id) {
      for (var i = 0; i < records.length; i++) {
        if (records[i].id === id) return records[i];
      }
      return null;
    }

    function cloneRecord(record) {
      var out = {};
      for (var key in record) {
        if (Object.prototype.hasOwnProperty.call(record, key)) out[key] = record[key];
      }
      return out;
    }

    function snapshot() {
      var uncertain = false;
      var copies = [];
      for (var i = 0; i < records.length; i++) {
        if (records[i].persist_uncertain) uncertain = true;
        copies.push(cloneRecord(records[i]));
      }
      return {
        records: copies,
        count: copies.length,
        overflow_count: overflowCount,
        persist_uncertain: uncertain
      };
    }

    function markDurable(record) {
      record.persist_uncertain = false;
      if (record.state === "uncertain") record.state = "pending";
    }

    function markUncertain(record) {
      record.persist_uncertain = true;
      if (record.state === "pending") record.state = "uncertain";
    }

    function enforceCap() {
      while (records.length > PENDING_MAX_RECORDS) {
        var dropped = records.shift();  // oldest beyond the cap: dropped…
        delete sessionKeys[recordKey(dropped.pane_id, dropped.status)];
        overflowCount += 1;             // …but COUNTED — never silent
        notify({ state: "overflow", overflow_count: overflowCount, dropped_id: dropped.id });
      }
    }

    function sanitizeRow(raw, ts) {
      if (!raw || typeof raw !== "object") return;
      var state = typeof raw.state === "string" && PENDING_STATES[raw.state]
        ? raw.state : "pending";
      /* A record read back from storage IS durable by construction: the
       * uncertainty flag never survives a reload, and stale "uncertain"
       * / "discarded" states degrade to "pending" (we hold the record;
       * resolution is a deliberate action, never an assumed outcome). */
      if (state === "uncertain" || state === "discarded") state = "pending";
      records.push({
        id: typeof raw.id === "string" && raw.id ? raw.id : makeId(),
        pane_id: typeof raw.pane_id === "string" ? raw.pane_id : "",
        agent: typeof raw.agent === "string" ? raw.agent : "",
        status: typeof raw.status === "string" ? raw.status : "",
        label: typeof raw.label === "string" ? raw.label : "",
        text: boundText(typeof raw.text === "string" ? raw.text : ""),
        first_seen_ts: typeof raw.first_seen_ts === "number" && isFinite(raw.first_seen_ts)
          ? raw.first_seen_ts : ts,
        last_seen_ts: typeof raw.last_seen_ts === "number" && isFinite(raw.last_seen_ts)
          ? raw.last_seen_ts : ts,
        repeat_count: typeof raw.repeat_count === "number" && raw.repeat_count >= 1
          ? Math.floor(raw.repeat_count) : 1,
        state: state,
        persist_uncertain: false  // read back from storage => durable
      });
    }

    function load() {
      var raw = null;
      if (deps.storage) {
        try {
          raw = deps.storage.getItem(PENDING_STORAGE_KEY);
        } catch (err) {
          raw = null;  // unreadable storage (private mode): start empty
        }
      }
      if (!raw) return;
      var data;
      try {
        data = JSON.parse(raw);
      } catch (err) {
        return;  // corrupt payload: start empty, never throw
      }
      if (!data || !Array.isArray(data.records)) return;
      var ts = now();
      for (var i = 0; i < data.records.length; i++) sanitizeRow(data.records[i], ts);
      if (typeof data.overflow_count === "number" && data.overflow_count > 0) {
        overflowCount = Math.floor(data.overflow_count);
      }
      enforceCap();  // a tampered/oversized payload is still honestly accounted
    }

    /* Best-effort save. The DURABLE form is computed first — the payload
     * never carries the uncertainty flag (a snapshot written to storage
     * is durable by definition) — then a failure re-marks every record
     * uncertain. Returns true when the save landed. */
    function save() {
      var ok = false;
      var i;
      if (deps.storage) {
        for (i = 0; i < records.length; i++) markDurable(records[i]);
        try {
          deps.storage.setItem(PENDING_STORAGE_KEY,
            JSON.stringify({ records: records, overflow_count: overflowCount }));
          ok = true;
        } catch (err) {
          ok = false;  // QuotaExceededError / private mode / storage denied
        }
      }
      if (ok) {
        if (persistFailed) {
          persistFailed = false;
          notify({ state: "persist-restored" });  // the banner can hide
        }
      } else {
        persistFailed = true;
        for (i = 0; i < records.length; i++) markUncertain(records[i]);
        notify({ state: "persist-uncertain", count: records.length });
      }
      return ok;
    }

    /* SSE intake: append-or-consolidate. The announcement itself still
     * plays through the normal announcer path — this is bookkeeping. */
    function observe(announcement) {
      if (!announcement || typeof announcement !== "object") return null;
      var paneId = typeof announcement.pane_id === "string" ? announcement.pane_id : "";
      var status = typeof announcement.status === "string" ? announcement.status : "";
      var ts = now();
      var existing = findById(sessionKeys[recordKey(paneId, status)]);
      if (existing) {
        /* T7 mirror: metadata-only consolidation — no new record. */
        existing.repeat_count += 1;
        existing.last_seen_ts = ts;
        if (typeof announcement.agent === "string") existing.agent = announcement.agent;
        if (typeof announcement.label === "string") existing.label = announcement.label;
        if (typeof announcement.text === "string") existing.text = boundText(announcement.text);
        save();
        emitChange();
        return cloneRecord(existing);
      }
      var record = {
        id: makeId(),
        pane_id: paneId,
        agent: typeof announcement.agent === "string" ? announcement.agent : "",
        status: status,
        label: typeof announcement.label === "string" ? announcement.label : "",
        text: boundText(announcement.text),
        first_seen_ts: ts,
        last_seen_ts: ts,
        repeat_count: 1,
        state: "pending"
      };
      records.push(record);
      sessionKeys[recordKey(paneId, status)] = record.id;
      enforceCap();
      save();
      emitChange();
      return cloneRecord(record);
    }

    /* Deliberate actions (FR-08/FR-10): resolution is never automatic. */

    function mark_announced(id) {
      var record = findById(id);
      if (!record) return false;
      if (record.state !== "announced") {
        record.state = "announced";
        save();
        emitChange();
      }
      return true;
    }

    function discard(id) {
      var record = findById(id);
      if (!record) return false;
      records.splice(records.indexOf(record), 1);
      delete sessionKeys[recordKey(record.pane_id, record.status)];
      save();  // the record leaves persistence on this very save
      emitChange();
      return true;
    }

    function listen(id) {
      var record = findById(id);
      if (!record) return false;
      /* Regeneration is the caller's business: app.js re-synthesizes
       * the stored text through the NORMAL speech pipeline (speakText
       * -> /tts -> enqueueAudio), never through this store. */
      if (deps.onListen) deps.onListen(record.text, cloneRecord(record));
      return true;
    }

    load();

    return {
      observe: observe,
      mark_announced: mark_announced,
      discard: discard,
      listen: listen,
      snapshot: snapshot
    };
  }

  var api = {
    ID_PATTERN: ID_PATTERN,
    CANCEL_NET_RETRY: CANCEL_NET_RETRY,
    CANCEL_BACKOFF_MS: CANCEL_BACKOFF_MS,
    SPEECH_CLIENT_PREFETCH: SPEECH_CLIENT_PREFETCH,
    SPEECH_CLIENT_FETCH_TIMEOUT_MS: SPEECH_CLIENT_FETCH_TIMEOUT_MS,
    SPEECH_SEGMENT_RETRY: SPEECH_SEGMENT_RETRY,
    SEGMENT_RETRY_BACKOFF_MS: SEGMENT_RETRY_BACKOFF_MS,
    SEGMENT_FLUSH_POLL_MS: SEGMENT_FLUSH_POLL_MS,
    PENDING_STORAGE_KEY: PENDING_STORAGE_KEY,
    PENDING_MAX_RECORDS: PENDING_MAX_RECORDS,
    PENDING_TEXT_MAX: PENDING_TEXT_MAX,
    validId: validId,
    mintSpeechRequest: mintSpeechRequest,
    purgeQueueById: purgeQueueById,
    createSpeechController: createSpeechController,
    createSegmentPlayer: createSegmentPlayer,
    createPendingStore: createPendingStore
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    global.Speech = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
