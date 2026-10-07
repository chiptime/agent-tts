/* Shared, DOM-independent replay plan and controller. */
(function (global) {
  "use strict";

  function mappingError() {
    var error = new Error("A-01: karaoke windows cannot partition the raw turn text");
    error.code = "KARAOKE_RAW_MAPPING";
    return error;
  }

  /* turnId is host-issued, DOM-local identity, never a text hash. Empty audio
   * windows leave rawText intact for the host's whitespace-only rendering.
   * The optional helpers argument instruments the unchanged Toast splitters. */
  function createPlan(turnId, rawText, helpers) {
    if (typeof turnId !== "string" || !turnId || typeof rawText !== "string") {
      throw new TypeError("A turn needs a nonempty local ID and string text");
    }
    helpers = helpers || (typeof module !== "undefined" && module.exports
      ? require("./toast.js") : global.Toast);
    var pieces = [];
    var chunks = [];
    var cursor = 0;

    function consumeWhitespace() {
      while (cursor < rawText.length && /\s/.test(rawText.charAt(cursor))) cursor++;
    }

    /* Match forward, never search by text identity. This preserves repeated
     * substrings and UTF-16 offsets, even when Toast hard-cuts a surrogate pair.
     * Normalization belongs only to popup text, not to the displayed slices. */
    helpers.splitForTts(rawText, 8000).forEach(function (text, pieceIndex) {
      var windows = helpers.chunkifyText(text);
      var chunkOffset = chunks.length;
      windows.forEach(function (popupText, localIndex) {
        var start = cursor;
        consumeWhitespace();
        for (var i = 0; i < popupText.length; i++) {
          if (popupText.charAt(i) === " ") {
            if (cursor >= rawText.length || !/\s/.test(rawText.charAt(cursor))) {
              throw mappingError();
            }
            consumeWhitespace();
          } else {
            if (rawText.charAt(cursor) !== popupText.charAt(i)) throw mappingError();
            cursor++;
          }
        }
        consumeWhitespace();
        chunks.push(Object.freeze({
          globalIndex: chunks.length, ordinal: chunks.length + 1,
          pieceIndex: pieceIndex, localIndex: localIndex,
          raw: Object.freeze([start, cursor]), popupText: popupText
        }));
      });
      pieces.push(Object.freeze({ pieceIndex: pieceIndex, text: text,
        chunks: Object.freeze(windows), chunkOffset: chunkOffset }));
    });
    if (chunks.length ? cursor !== rawText.length : rawText.trim() !== "") {
      throw mappingError();
    }
    return Object.freeze({ turnId: turnId, rawText: rawText,
      pieces: Object.freeze(pieces), chunks: Object.freeze(chunks) });
  }

  /* Required deps: fetch and player; optional: createAbortController,
   * onState(snapshot), onProgress(snapshot + chunk), onFailure(failure).
   * select(plan, globalIndex) / replay(plan) return whether a target exists.
   * stop/reset retire everything owned; detach(turnId) retires only that owner.
   *
   * player.load(item, events) returns an audio-like handle with numeric
   * duration/currentTime, writable currentTime, play(), pause(), dispose().
   * It may queue the item and initially expose unknown duration. The adapter
   * must bind events to this exact item/source and dispose ONLY that item;
   * foreign speech/announcements and their cancellation remain host-owned.
   * No clock/timer is needed: metadata waiting is entirely event-driven. */
  function createController(deps) {
    if (!deps || typeof deps.fetch !== "function" || !deps.player ||
        typeof deps.player.load !== "function") {
      throw new TypeError("Replay needs injected fetch and player.load");
    }
    var generation = 0;
    var session = null;
    var attempt = null;
    var phase = "idle";
    var activeGlobalIndex = null;
    var pendingTarget = null;

    function state() {
      return Object.freeze({ ownerTurnId: session ? session.plan.turnId : null,
        generation: generation, phase: phase, activeGlobalIndex: activeGlobalIndex,
        pendingTarget: pendingTarget, plan: session ? session.plan : null });
    }

    function notifyState() {
      if (deps.onState) deps.onState(state());
    }

    /* One event carries the already-derived index AND popup text. The host
     * updates both surfaces from it, regardless of popup visibility. */
    function notifyProgress() {
      if (!deps.onProgress) return;
      var snapshot = state();
      deps.onProgress(Object.freeze(Object.assign({}, snapshot, {
        chunk: snapshot.plan && snapshot.activeGlobalIndex !== null
          ? snapshot.plan.chunks[snapshot.activeGlobalIndex] : null
      })));
    }

    function current(token) {
      return attempt === token && !token.finished && session === token.session &&
        generation === token.generation;
    }

    function dispose(token) {
      if (!token) return;
      token.finished = true;
      token.playVersion++;
      if (token.abort) token.abort.abort();
      if (token.handle) token.handle.dispose();
    }

    function stop() {
      var old = attempt;
      attempt = null;
      session = null;
      generation++;
      phase = "idle";
      activeGlobalIndex = null;
      pendingTarget = null;
      dispose(old);
      notifyState();
      notifyProgress();
    }

    function advance(token) {
      if (!current(token)) return;
      var next = token.piece.pieceIndex + 1;
      while (next < session.plan.pieces.length && !session.plan.pieces[next].chunks.length) next++;
      if (next === session.plan.pieces.length) stop();
      else beginPiece(session, next, 0);
    }

    function fail(token, error, stage) {
      if (!current(token) || token.failed) return;
      token.failed = true; // error events and rejected play share this claim.
      token.session.ready.delete(token.piece.pieceIndex);
      phase = "preparing";
      activeGlobalIndex = null;
      pendingTarget = null;
      notifyState();
      notifyProgress();
      if (!current(token)) return;
      if (deps.onFailure) deps.onFailure(Object.freeze({
        ownerTurnId: token.session.plan.turnId, generation: token.generation,
        plan: token.session.plan, pieceIndex: token.piece.pieceIndex,
        item: token.item, stage: stage, error: error
      }));
      advance(token);
    }

    function validDuration(duration) {
      return Number.isFinite(duration) && duration > 0;
    }

    function metadata(token) {
      if (!current(token) || token.failed || !token.handle || token.playRequested || phase === "paused") return;
      try {
        var duration = token.handle.duration;
        // Only an explicitly zero-start compatibility handle may play before
        // metadata. A nonzero proportional seek still requires valid duration.
        var startAtZero = token.handle.startAtZero === true && token.localIndex === 0;
        if (!startAtZero && !validDuration(duration)) return;
        token.playRequested = true;
        var version = ++token.playVersion;
        token.handle.currentTime = startAtZero ? 0 : token.localIndex / token.piece.chunks.length * duration;
        var playing = token.handle.play();
        Promise.resolve(playing).then(function () {
          if (!current(token) || token.failed || token.playVersion !== version) return;
          phase = "playing";
          pendingTarget = null;
          notifyState();
        }, function (error) {
          if (token.playVersion === version) fail(token, error, "play");
        });
      } catch (error) {
        fail(token, error, "play");
      }
    }

    function progress(token) {
      if (!current(token) || token.failed || phase !== "playing" || !token.handle) return;
      var duration = token.handle.duration;
      var time = token.handle.currentTime;
      if (!validDuration(duration) || !Number.isFinite(time)) return;
      var count = token.piece.chunks.length;
      activeGlobalIndex = Math.max(0, Math.min(count - 1, Math.floor(time / duration * count)))
        + token.piece.chunkOffset;
      notifyState();
      if (current(token)) notifyProgress();
    }

    function paused(token) {
      if (!current(token) || token.failed || !token.playRequested || phase === "paused") return;
      token.playVersion++;
      phase = "paused";
      pendingTarget = null;
      notifyState();
    }

    /* Capture one attempt, not the mutable "current player". A bridge must
     * retain these callbacks alongside the queue item that created them. */
    function eventsFor(token) {
      return {
        loadedmetadata: function () { metadata(token); },
        durationchange: function () { metadata(token); },
        timeupdate: function () { progress(token); },
        pause: function () {
          // A disposed source's queued native pause can reach replacement
          // listeners. Native state, not the event alone, admits the pause.
          if (token.handle && token.handle.paused === false) return;
          paused(token);
        },
        ended: function () { if (!token.failed) advance(token); },
        error: function (error) { fail(token, error, "media"); }
      };
    }

    function load(token, url) {
      if (!current(token)) return;
      token.item = Object.freeze({ ownerTurnId: token.session.plan.turnId,
        generation: token.generation, pieceIndex: token.piece.pieceIndex,
        plan: token.session.plan, url: url });
      phase = "awaiting_metadata";
      notifyState();
      if (!current(token)) return;
      try {
        var handle = deps.player.load(token.item, eventsFor(token));
        if (!current(token)) {
          handle.dispose();
          return;
        }
        token.handle = handle;
        metadata(token);
      } catch (error) {
        fail(token, error, "media");
      }
    }

    function sameOriginPath(url) {
      return typeof url === "string" && /^\/(?!\/)/.test(url) && !/[\u0000-\u0020\\]/.test(url);
    }

    async function synthesize(token) {
      var url;
      try {
        var init = { method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ text: token.piece.text }) };
        if (deps.createAbortController) {
          token.abort = deps.createAbortController();
          init.signal = token.abort.signal;
        }
        var response = await deps.fetch("/tts", init);
        if (!current(token)) return;
        if (!response.ok) throw new Error("TTS failed (HTTP " + response.status + ")");
        var data = await response.json();
        if (!current(token)) return;
        if (!data || !sameOriginPath(data.audio_url)) throw new Error("TTS needs a same-origin audio path");
        url = data.audio_url;
        token.session.ready.set(token.piece.pieceIndex, url);
      } catch (error) {
        fail(token, error, "synthesis");
        return;
      }
      load(token, url);
    }

    function beginPiece(owner, pieceIndex, localIndex) {
      if (session !== owner || generation !== owner.generation) return;
      var old = attempt;
      attempt = null;
      dispose(old);
      if (session !== owner || generation !== owner.generation) return;
      var piece = owner.plan.pieces[pieceIndex];
      var token = { session: owner, generation: generation, piece: piece,
        localIndex: localIndex, handle: null, item: null, abort: null,
        finished: false, failed: false, playRequested: false, playVersion: 0 };
      attempt = token;
      phase = "preparing";
      activeGlobalIndex = piece.chunkOffset + localIndex;
      pendingTarget = activeGlobalIndex;
      notifyState();
      if (!current(token)) return;
      notifyProgress();
      if (!current(token)) return;
      if (owner.ready.has(pieceIndex)) load(token, owner.ready.get(pieceIndex));
      else synthesize(token);
    }

    function select(plan, globalIndex) {
      if (globalIndex === undefined) globalIndex = 0;
      if (!plan || !Number.isInteger(globalIndex) || !plan.chunks[globalIndex]) return false;
      var ready = session && session.plan === plan ? session.ready : new Map();
      var old = attempt;
      attempt = null;
      generation++;
      var owner = { plan: plan, generation: generation, ready: ready };
      session = owner;
      dispose(old);
      var chunk = plan.chunks[globalIndex];
      beginPiece(owner, chunk.pieceIndex, chunk.localIndex);
      return true;
    }

    function pause() {
      if (!attempt || !attempt.handle || phase !== "playing") return false;
      var token = attempt;
      paused(token);
      if (current(token)) {
        try { token.handle.pause(); }
        catch (error) { fail(token, error, "media"); }
      }
      return true;
    }

    return { select: select, replay: function (plan) { return select(plan, 0); },
      pause: pause, stop: stop, reset: stop,
      detach: function (turnId) { if (session && session.plan.turnId === turnId) stop(); },
      state: state };
  }

  var api = { createPlan: createPlan, createController: createController };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    global.Karaoke = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
