"use strict";

/* herdr-brain PWA: continuous hands-free call.
 *
 * Call state machine: idle -> listening -> thinking -> speaking -> listening…
 * - ONE recognition lifecycle per listening session (continuous=true) with
 *   SELF-ENDPOINTING (static/endpointing.js): dispatch on isFinal, on 1200ms
 *   of interim silence, or on a 15s hard cap. No mic during /ask or TTS.
 * - Announcements arrive over SSE and queue behind any in-flight audio.
 * - Every poll render is guarded: one render error never kills the loop and
 *   every surface has a Spanish empty/error state (no dead ends).
 * User-facing strings are Spanish on purpose (single Spanish-speaking owner).
 */

(function () {
  var $ = function (id) { return document.getElementById(id); };
  var callBtn = $("call-btn");
  var pauseBtn = $("pause-btn");
  var stopBtn = $("stop-audio");
  var conv = $("conversation");
  var interimEl = $("interim");
  var bannerEl = $("banner");
  var micNote = $("mic-note");
  var player = $("player");
  var chip = $("status-chip");
  var paneTitle = $("pane-title");
  var fallbackForm = $("text-fallback");
  var textInput = $("text-input");
  var statePill = $("state-pill");
  var pillMain = $("pill-main");
  var pillSub = $("pill-sub");
  var agentView = $("agent-view");
  var viewEmpty = $("view-empty");
  var pendingBanner = $("pending-banner");
  var viewTranscript = $("view-transcript");
  var viewScreen = $("view-screen");
  var herdStrip = $("herd-strip");
  var herdNote = $("herd-note");
  var toastEl = $("toast");
  var sheet = $("sheet");
  var sheetTitle = $("sheet-title");
  var sheetNote = $("sheet-note");
  var sheetDiag = $("sheet-diag");
  var diagPanel = $("diag-panel");
  var diagText = $("diag-text");
  var sheetConv = $("sheet-conversation");
  var sheetScreen = $("sheet-screen");
  var tabConv = $("tab-conv");
  var tabScreen = $("tab-screen");
  var voiceEngineBtn = $("voice-engine-btn");

  var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  var recognition = null;
  var listening = false;        // a recognition object is running
  var manualStop = false;       // we stopped the mic on purpose
  var dispatching = false;      // an utterance is being processed
  var recRestartTimer = null;
  var recRestartDelay = 300;
  var recFailures = 0;
  var epTick = null;            // endpointing timer
  var endpointer = null;        // Endpointing.createEndpointer()
  var listeningStartedAt = null;
  var lastInterimRaw = "";
  var askCount = 0;
  var lastDispatch = "ninguna";
  var lastRecError = "ninguno";
  var textMode = false;         // mic unavailable: keyboard fallback

  /* Voice engine v2: "servidor" (getUserMedia + VAD + /transcribe) is the
   * default — Android Chrome's SpeechRecognition ignores BT headset mics
   * and returns zero results, while getUserMedia does honor them. The old
   * Web Speech path stays as "navegador" fallback; the choice persists. */
  var VOICE_KEY = "herdr-brain-voice-engine";
  var voiceEngine = localStorage.getItem(VOICE_KEY) === "navegador" ? "navegador" : "servidor";
  var mediaStream = null;       // getUserMedia stream, live for the whole call
  var audioContext = null;
  var analyser = null;
  var vadSampleBuf = null;      // reused Float32Array for RMS frames
  var vad = null;               // Vad.createVad()
  var vadTick = null;           // VAD sampling interval
  var recorder = null;          // MediaRecorder for the CURRENT utterance
  var recorderChunks = [];
  var serverMicBusy = false;    // a server-engine capture is starting up

  /* Persistent per-install ids. */
  var SESSION_KEY = "herdr-brain-session";
  var PANE_KEY = "herdr-brain-pane";
  var MUTE_KEY = "herdr-brain-mute";
  var sessionId = localStorage.getItem(SESSION_KEY);
  if (!sessionId) {
    sessionId = "s-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
    localStorage.setItem(SESSION_KEY, sessionId);
  }
  var selectedPane = localStorage.getItem(PANE_KEY);
  var lastViewJson = "";  // render cache — set only AFTER a successful render

  /* ---------------- call state machine ----------------
   * idle | listening | thinking | speaking | paused  */
  var callState = "idle";
  var inCall = false;

  var PILL_TEXT = {
    listening: "● Escuchando",
    recording: "● Grabando — habla",
    transcribing: "⏳ Entendiendo…",
    thinking: "⏳ Pensando…",
    speaking: "🔊 Hablando — toca para cortar",
    paused: "⏸ Micrófono en pausa"
  };

  function setCallState(state) {
    callState = state;
    if (state === "idle") {
      statePill.classList.add("hidden");
      pillSub.classList.add("hidden");
      callBtn.textContent = "📞 Llamar";
      callBtn.classList.remove("oncall");
      pauseBtn.classList.add("hidden");
      return;
    }
    pillMain.textContent = PILL_TEXT[state] || state;
    pillSub.classList.add("hidden");
    statePill.className = state;
    statePill.classList.remove("hidden");
    callBtn.textContent = "🔴 Colgar";
    callBtn.classList.add("oncall");
    var micControllable = state === "listening" || state === "recording" || state === "paused";
    pauseBtn.classList.toggle("hidden", !micControllable);
    pauseBtn.textContent = state === "paused" ? "▶ Reanudar" : "⏸ Pausa";
    // The interim strip carries the live transcript of Web Speech only.
    interimEl.classList.toggle("hidden", !(state === "listening" && voiceEngine === "navegador"));
  }

  /* ---------------- diagnostics ---------------- */

  var diag = { lastPoll: null, lastError: "ninguno" };

  function markPollOk() {
    diag.lastPoll = new Date();
    diag.lastError = "ninguno";
    renderDiag();
  }

  function markPollError(scope, err) {
    var msg = err && err.message ? err.message : String(err);
    diag.lastPoll = new Date();
    diag.lastError = scope + ": " + msg;
    renderDiag();
  }

  function renderDiag() {
    if (!sheetDiag) return;
    var when = diag.lastPoll ? diag.lastPoll.toLocaleTimeString() : "—";
    sheetDiag.textContent = "Última consulta: " + when + " · Último error: " + diag.lastError;
  }

  function safeRender(name, fn, arg) {
    try {
      fn(arg);
    } catch (err) {
      markPollError(name, err);
    }
  }

  /* ---------------- conversation view ---------------- */

  function addTurn(role, text) {
    var turn = document.createElement("div");
    turn.className = "turn " + role;
    var who = document.createElement("span");
    who.className = "who";
    who.textContent = role === "user" ? "tú" : "brain";
    var body = document.createElement("span");
    body.textContent = text;
    turn.appendChild(who);
    turn.appendChild(body);
    if (role === "brain") attachReplay(conv, turn, function () { return text; }, "brain");
    conv.appendChild(turn);
    conv.scrollTop = conv.scrollHeight;
  }

  /* Replay pill: within one container, only the newest kept turn has it.
   * getText resolves the text at tap time (the /view tail is truncated for
   * glancing, so the agent view resolves the full text from /conversation). */
  function attachReplay(container, turn, getText, label) {
    var prev = container.querySelector(".replay-btn");
    if (prev) prev.remove();
    var btn = document.createElement("button");
    btn.className = "replay-btn";
    btn.type = "button";
    btn.setAttribute("aria-label", "Leer esta respuesta en voz alta");
    btn.title = "Leer en voz alta";
    btn.textContent = "🔊 Escuchar";
    btn.addEventListener("click", function (event) {
      event.stopPropagation();  // never trigger the agent-view "open sheet" tap
      Promise.resolve(getText()).then(function (text) {
        speakText(text, label);
      });
    });
    turn.appendChild(btn);
  }

  /* The /view transcript tail caps each turn for glancing; replay reads the
   * full turn text from /conversation, falling back to the tail on failure. */
  function fetchFullAgentText(fallback) {
    var qs = selectedPane ? "?pane_id=" + encodeURIComponent(selectedPane) : "";
    return fetch("/conversation" + qs)
      .then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(function (data) {
        var turns = (data && data.turns) || [];
        for (var i = turns.length - 1; i >= 0; i--) {
          if (turns[i].role !== "user") return turns[i].text;
        }
        return fallback;
      })
      .catch(function () { return fallback; });
  }

  function speakText(text, label) {
    fetch("/tts", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text: text })
    })
      .then(function (resp) {
        if (!resp.ok) {
          showBanner("No pude sintetizar el audio (HTTP " + resp.status + ").");
          return;
        }
        return resp.json().then(function (data) {
          var toastText = text.length > 160 ? text.slice(0, 157) + "…" : text;
          enqueueAudio(data.audio_url, { label: label || "agente", text: toastText });
        });
      })
      .catch(function () {
        showBanner("No pude sintetizar el audio — revisa la conexión.");
      });
  }

  function showBanner(message) {
    bannerEl.textContent = message;
    bannerEl.classList.remove("hidden");
  }

  function hideBanner() {
    bannerEl.classList.add("hidden");
  }

  function showMicNote(message) {
    micNote.textContent = message;
    micNote.classList.remove("hidden");
  }

  function enterTextMode(reason) {
    if (textMode) return;
    textMode = true;
    if (inCall) endCall();
    fallbackForm.classList.remove("hidden");
    showMicNote(reason);
  }

  /* ---------------- herd + selected agent ---------------- */

  function effectiveSelected(herd) {
    if (selectedPane && herd.some(function (a) { return a.pane_id === selectedPane; })) {
      return selectedPane;
    }
    var focused = null;
    for (var i = 0; i < herd.length; i++) {
      if (herd[i].focused) { focused = herd[i]; break; }
    }
    var fallback = focused || herd[0];
    return fallback ? fallback.pane_id : null;
  }

  function renderHerd(herd) {
    if (!Array.isArray(herd)) herd = [];
    var effective = effectiveSelected(herd);
    var fellBack = !!(selectedPane && effective !== selectedPane);
    herdNote.classList.toggle("hidden", !fellBack);
    if (fellBack) {
      herdNote.textContent = "El agente seleccionado desapareció — volviendo al enfocado.";
      selectedPane = effective;
      if (effective) localStorage.setItem(PANE_KEY, effective);
    }

    herdStrip.textContent = "";
    for (var i = 0; i < herd.length; i++) {
      var agent = herd[i];
      var chipEl = document.createElement("button");
      chipEl.className = "herd-chip" + (agent.pane_id === effective ? " selected" : "");
      var base = (agent.cwd || "").split("/").filter(Boolean).pop() || agent.title || agent.agent;
      chipEl.title = (agent.title || "") + (agent.last_turn ? " — " + agent.last_turn.text : "");
      var st = document.createElement("span");
      st.className = "st " + (agent.agent_status || "");
      st.textContent = "●";
      chipEl.appendChild(st);
      chipEl.appendChild(document.createTextNode(base));
      chipEl.addEventListener("click", function (paneId) {
        return function () { selectAgent(paneId); };
      }(agent.pane_id));
      herdStrip.appendChild(chipEl);
    }
  }

  function selectAgent(paneId) {
    if (selectedPane === paneId) return;
    selectedPane = paneId;
    localStorage.setItem(PANE_KEY, paneId);
    lastViewJson = "";
    refreshView();
  }

  /* ---------------- status / view polling ---------------- */

  function renderStatus(state) {
    if (!state || !state.active) {
      chip.textContent = "nadie";
      chip.className = "chip";
      paneTitle.textContent = "sin agente activo";
      return;
    }
    chip.textContent = state.agent_status || "?";
    chip.className = "chip " + (state.agent_status || "");
    var title = state.title || state.agent || "";
    paneTitle.textContent = title + (state.cwd ? " — " + state.cwd : "");
  }

  function renderPending(pending) {
    if (!pending || !pending.detected) {
      pendingBanner.className = "pending hidden";
      return;
    }
    var kind = pending.kind || "question";
    var headline = {
      permission: "Pide permiso",
      error: "Error del agente",
      question: "El agente pregunta"
    }[pending.kind] || "Necesita tu atención";
    pendingBanner.textContent = headline + ": " + (pending.excerpt || "mira la pantalla");
    pendingBanner.className = "pending " + kind;
  }

  function renderViewTail(view) {
    viewTranscript.textContent = "";
    var turns = (view && view.transcript) || [];
    var lastAgentTurn = null;
    var lastAgentText = "";
    for (var i = 0; i < turns.length; i++) {
      var line = document.createElement("div");
      line.className = "view-turn " + turns[i].role;
      var role = document.createElement("span");
      role.className = "role";
      role.textContent = turns[i].role === "user" ? "tú" : "agente";
      line.appendChild(role);
      line.appendChild(document.createTextNode(turns[i].text));
      viewTranscript.appendChild(line);
      if (turns[i].role !== "user") {
        lastAgentTurn = line;
        lastAgentText = turns[i].text;
      }
    }
    if (lastAgentTurn) {
      attachReplay(viewTranscript, lastAgentTurn, function () {
        return fetchFullAgentText(lastAgentText);
      }, "agente");
    }
    if (view && view.screen) {
      viewScreen.textContent = view.screen;
      viewScreen.classList.remove("hidden");
    } else {
      viewScreen.textContent = "";
      viewScreen.classList.add("hidden");
    }
    /* NO dead ends: the panel is never blank; explicit empty state with retry. */
    var hasContent = turns.length > 0 || !!(view && view.screen);
    if (hasContent) {
      viewEmpty.classList.add("hidden");
    } else {
      viewEmpty.textContent = "Sin datos del agente — toca para reintentar";
      viewEmpty.classList.remove("hidden");
    }
    agentView.classList.remove("hidden");
  }

  function renderView(view) {
    if (!view) return;
    safeRender("status", renderStatus, view.status);
    safeRender("pending", renderPending, view.pending);
    safeRender("tail", renderViewTail, view);
    lastViewJson = JSON.stringify([view.transcript, view.screen, view.pending]);
    markPollOk();
  }

  function refreshView() {
    var qs = selectedPane ? "?pane_id=" + encodeURIComponent(selectedPane) : "";
    fetch("/view" + qs)
      .then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(renderView)
      .catch(function (err) {
        markPollError("view", err);
      });
  }

  function refreshState() {
    fetch("/herd")
      .then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(function (herd) { safeRender("herd", renderHerd, herd); })
      .catch(function (err) {
        markPollError("herd", err);
      });
    refreshView();
  }

  /* ---------------- audio playback (single queue) ---------------- */

  var audioQueue = [];
  var audioBusy = false;
  var audioFinished = null;

  function enqueueAudio(url, announcement) {
    audioQueue.push({ url: url, announcement: announcement || null });
    pumpAudio();
  }

  function pumpAudio() {
    if (audioBusy || !audioQueue.length) return;
    var item = audioQueue.shift();
    audioBusy = true;
    audioFinished = item;
    if (item.announcement) {
      showToast("🔊 " + item.announcement.label + ": " + item.announcement.text);
    }
    if (inCall) {
      if (callState === "listening") stopListening();  // never hear our own audio
      setCallState("speaking");
    }
    player.src = item.url;
    stopBtn.classList.remove("hidden");
    var pending = player.play();
    if (pending && pending.catch) {
      pending.catch(function () {
        audioBusy = false;
        if (item.announcement) hideToast();
        pumpAudio();
      });
    }
  }

  function onAudioEnded() {
    audioBusy = false;
    var finished = audioFinished;
    audioFinished = null;
    if (finished && finished.announcement) hideToast();
    if (!audioQueue.length) stopBtn.classList.add("hidden");
    if (!audioQueue.length && inCall && callState === "speaking") {
      setCallState("listening");
      startListening();
    } else {
      pumpAudio();
    }
  }

  function stopAudio() {
    audioQueue.length = 0;
    audioBusy = false;
    audioFinished = null;
    player.pause();
    player.removeAttribute("src");
    player.load();
    stopBtn.classList.add("hidden");
    hideToast();
    if (inCall && callState === "speaking") {
      setCallState("listening");
      startListening();
    }
  }

  player.addEventListener("ended", onAudioEnded);
  stopBtn.addEventListener("click", stopAudio);

  var pressTimer = null;
  var longPressFired = false;

  statePill.addEventListener("pointerdown", function () {
    longPressFired = false;
    pressTimer = setTimeout(function () {
      longPressFired = true;
      openDiag();
    }, 600);
  });
  ["pointerup", "pointercancel", "pointerleave"].forEach(function (ev) {
    statePill.addEventListener(ev, function () {
      if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; }
    });
  });

  statePill.addEventListener("click", function () {
    if (longPressFired) { longPressFired = false; return; }
    if (callState === "speaking") stopAudio();  // barge-in
  });

  /* ---------------- voice diagnostics overlay ---------------- */

  var diagTimer = null;

  function renderVoiceDiag() {
    var snap = endpointer ? endpointer.snapshot() : null;
    var versionEl = document.getElementById("app-version");
    var lines = [
      "build:            " + (versionEl ? versionEl.textContent : "?"),
      "motor de voz:     " + voiceEngine,
      "callState:        " + callState,
      "inCall:           " + inCall,
      "listening:        " + listening,
      "dispatching:      " + dispatching,
      "textMode:         " + textMode,
      "escuchando desde: " + (listeningStartedAt ? new Date(listeningStartedAt).toLocaleTimeString() : "—"),
      "ruido de sala:    " + (vad ? vad.floor().toFixed(4) : "—"),
      "errores recon.:   " + recFailures + " (último: " + lastRecError + ")",
      "preguntas /ask:   " + askCount,
      "último envío:     " + lastDispatch,
      "interim chars:    " + (snap ? snap.bufferChars : 0),
      "último interim:   " + (lastInterimRaw ? '"' + lastInterimRaw.slice(-80) + '"' : "(vacío)")
    ];
    if (snap) {
      lines.push(
        "silencio restante: " + (snap.silenceRemainingMs === null ? "—" : snap.silenceRemainingMs + " ms"),
        "cap restante:      " + (snap.capRemainingMs === null ? "—" : snap.capRemainingMs + " ms"),
        "hasSpeech:         " + snap.hasSpeech
      );
    } else {
      lines.push("endpointer:        (sin sesión activa)");
    }
    lines.push("diagnóstico:      " + diag.lastError);
    diagText.textContent = lines.join("\n");
  }

  function openDiag() {
    diagPanel.classList.remove("hidden");
    renderVoiceDiag();
    if (diagTimer) clearInterval(diagTimer);
    diagTimer = setInterval(renderVoiceDiag, 500);
  }

  $("diag-close").addEventListener("click", function () {
    diagPanel.classList.add("hidden");
    if (diagTimer) { clearInterval(diagTimer); diagTimer = null; }
  });

  /* ---------------- toast ---------------- */

  var toastTimer = null;

  function showToast(text, durationMs) {
    toastEl.textContent = text;
    toastEl.classList.remove("hidden");
    if (toastTimer) clearTimeout(toastTimer);
    if (durationMs) toastTimer = setTimeout(hideToast, durationMs);
  }

  function hideToast() {
    if (toastTimer) { clearTimeout(toastTimer); toastTimer = null; }
    toastEl.classList.add("hidden");
  }

  /* ---------------- announcements over SSE ---------------- */

  var eventsOpened = false;
  var muteBtn = $("mute-btn");

  function muted() {
    return localStorage.getItem(MUTE_KEY) === "1";
  }

  function renderMute() {
    muteBtn.textContent = muted() ? "🔇" : "🔊";
    muteBtn.classList.toggle("muted", muted());
  }

  muteBtn.addEventListener("click", function () {
    if (muted()) localStorage.removeItem(MUTE_KEY);
    else localStorage.setItem(MUTE_KEY, "1");
    renderMute();
  });
  renderMute();

  function openEvents() {
    if (eventsOpened || !window.EventSource) return;
    eventsOpened = true;
    var source = new EventSource("/events");
    source.onmessage = function (event) {
      var ann;
      try { ann = JSON.parse(event.data); } catch (err) { return; }
      if (!ann || ann.type !== "transition") return;
      if (muted()) {
        showToast("🔇 " + ann.label + ": " + ann.text, 6000);
        return;
      }
      if (ann.audio_url) enqueueAudio(ann.audio_url, ann);
      else showToast("🔊 " + ann.label + ": " + ann.text, 6000);
    };
  }

  /* ---------------- fetch with hard timeout ---------------- */

  function fetchWithTimeout(url, options, timeoutMs) {
    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, timeoutMs || 30000);
    return fetch(url, Object.assign({}, options || {}, { signal: controller.signal }))
      .then(
        function (resp) { clearTimeout(timer); return resp; },
        function (err) {
          clearTimeout(timer);
          if (err && err.name === "AbortError") {
            err = new Error("timeout: el brain tardó demasiado en responder");
          }
          throw err;
        }
      );
  }

  /* ---------------- ask pipeline ---------------- */

  function afterAnswer() {
    if (audioBusy) return;  // still speaking: onAudioEnded returns us to listening
    if (inCall && callState !== "paused") {
      setCallState("listening");
      startListening();
    } else if (!inCall) {
      setCallState("idle");
    }
  }

  function ask(text) {
    if (callState === "thinking") return Promise.resolve();
    askCount += 1;
    lastDispatch = text;
    addTurn("user", text);
    setCallState("thinking");
    stopListening();  // the mic must not hear the answer
    return fetchWithTimeout("/ask", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        text: text,
        session_id: sessionId,
        pane_id: selectedPane || null
      })
    }, 30000)
      .then(function (resp) {
        if (resp.status === 503) {
          showBanner("El brain no está configurado: falta GLM_API_KEY en el servidor. " +
            "Añádelo a ~/.dotfiles/shell/private-env.sh y reinicia el servicio.");
          return;
        }
        if (!resp.ok) {
          showBanner("La pregunta falló (HTTP " + resp.status + "). Prueba otra vez.");
          return;
        }
        return resp.json().then(function (data) {
          addTurn("brain", data.answer || "(respuesta vacía)");
          if (data.audio_url) enqueueAudio(data.audio_url, null);
          else afterAnswer();
        });
      })
      .catch(function (err) {
        if (err && /timeout/.test(String(err.message || err))) {
          showBanner("El brain tardó demasiado — vuelve a intentarlo.");
        } else {
          showBanner("Error de red hablando con el brain.");
        }
      })
      .then(function () {
        interimEl.textContent = "";
        interimEl.classList.add("hidden");
        if (callState === "thinking") afterAnswer();
      });
  }

  /* ---------------- speech recognition: browser engine (Web Speech) -----
   *
   * ONE recognition lifecycle per listening session, continuous=true.
   * Chrome often never finalizes interim results, so endpointing.js decides:
   * dispatch on isFinal, on 1200ms of interim silence, or at a 15s hard cap.
   * After dispatch the mic stays off through /ask + TTS; onAudioEnded
   * restarts it. onend restarts ONLY while listening, single-flight.
   */

  function teardownRecognition() {
    if (epTick) { clearInterval(epTick); epTick = null; }
    if (recognition) {
      try { recognition.stop(); } catch (err) { /* already stopped */ }
    }
    listening = false;
  }

  function dispatchUtterance() {
    if (dispatching || callState !== "listening" || !endpointer) return;
    var text = endpointer.finalize();
    if (!text) { endpointer.reset(); return; }
    dispatching = true;
    interimEl.textContent = "";
    teardownRecognition();
    setCallState("thinking");
    ask(text).then(function () { dispatching = false; });
  }

  function startBrowserListening() {
    if (textMode || !SR || callState !== "listening" || listening || dispatching) return;
    manualStop = false;
    hideBanner();
    interimEl.classList.remove("hidden");
    interimEl.textContent = "…";
    listeningStartedAt = Date.now();
    lastInterimRaw = "";
    endpointer = window.Endpointing.createEndpointer();

    recognition = new SR();
    recognition.lang = navigator.language || "es-ES";
    recognition.interimResults = true;
    recognition.continuous = true;

    recognition.onresult = function (event) {
      var interim = "";
      for (var i = event.resultIndex; i < event.results.length; i++) {
        interim += event.results[i][0].transcript;
        if (event.results[i].isFinal) {
          recRestartDelay = 300;
          recFailures = 0;
          lastInterimRaw = interim;
          dispatchUtterance();
          return;
        }
      }
      if (endpointer) endpointer.push(interim);
      lastInterimRaw = interim;
      pillSub.classList.add("hidden");
      // tail truncation: keep the newest words visible
      interimEl.textContent = interim.length > 120 ? "…" + interim.slice(-120) : (interim || "…");
    };

    recognition.onerror = function (event) {
      lastRecError = event.error || "desconocido";
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        showBanner("Micrófono BLOQUEADO: concede el permiso en el navegador " +
          "(menú ⋮ → Ajustes del sitio → Micrófono) y vuelve a llamar.");
        enterTextMode("Micrófono bloqueado — usa el teclado o concede el permiso en el navegador.");
        return;
      }
      if (event.error === "aborted") return;
      recFailures += 1;
      if (recFailures > 8) {
        enterTextMode("El reconocimiento de voz falla repetidamente — cambiado a teclado.");
      }
      /* no-speech and friends: silent restart with backoff (onend). */
    };

    recognition.onend = function () {
      listening = false;
      if (dispatching) return;  // deliberate stop: ask() owns the next transition
      if (epTick) { clearInterval(epTick); epTick = null; }
      if (callState === "listening" && !manualStop && !textMode) {
        recRestartTimer = setTimeout(startListening, recRestartDelay);
        recRestartDelay = Math.min(recRestartDelay * 2, 3000);
      } else if (interimEl.textContent === "…") {
        interimEl.textContent = "";
        interimEl.classList.add("hidden");
      }
    };

    try {
      listening = true;
      recognition.start();
      epTick = setInterval(function () {
        if (!endpointer) return;
        if (endpointer.shouldFinalize()) {
          dispatchUtterance();
          return;
        }
        // H2 feedback: if the mic has been up 5s with zero captured chars,
        // say it out loud in the pill.
        var elapsed = Date.now() - listeningStartedAt;
        if (!endpointer.hasSpeech() && elapsed > 5000) {
          pillSub.textContent = "No te oigo — comprueba el micro";
          pillSub.classList.remove("hidden");
        } else if (endpointer.hasSpeech()) {
          pillSub.classList.add("hidden");
        }
      }, 200);
    } catch (err) {
      listening = false;
      recRestartTimer = setTimeout(startListening, recRestartDelay);
      recRestartDelay = Math.min(recRestartDelay * 2, 3000);
    }
  }

  function stopBrowserListening() {
    manualStop = true;
    if (recRestartTimer) { clearTimeout(recRestartTimer); recRestartTimer = null; }
    if (epTick) { clearInterval(epTick); epTick = null; }
    if (recognition) {
      try { recognition.stop(); } catch (err) { /* already stopped */ }
    }
    listening = false;
  }

  /* ---------------- voice engine v2: servidor ----------------
   *
   * getUserMedia (echoCancellation + noiseSuppression) is kept open for the
   * whole call — unlike SpeechRecognition it honors BT headset mics. vad.js
   * bounds each utterance (adaptive noise floor + hysteresis; end after
   * 1.2s below threshold or 15s hard cap); one MediaRecorder per utterance
   * captures webm/opus, POSTs it to /transcribe and the text enters the
   * same dispatch pipeline as Web Speech (thinking → speak → resume).
   * Between utterances the recorder and the VAD loop are torn down so the
   * mic never hears the agent's own TTS; the stream itself stays live.
   */

  function serverEngineSupported() {
    return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia &&
      typeof window.MediaRecorder !== "undefined" && window.Vad);
  }

  function frameRms() {
    analyser.getFloatTimeDomainData(vadSampleBuf);
    var sum = 0;
    for (var i = 0; i < vadSampleBuf.length; i++) sum += vadSampleBuf[i] * vadSampleBuf[i];
    return Math.sqrt(sum / vadSampleBuf.length);
  }

  function pickRecorderMime() {
    var candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
    for (var i = 0; i < candidates.length; i++) {
      if (MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(candidates[i])) {
        return candidates[i];
      }
    }
    return "";  // let the browser choose
  }

  function releaseServerMic() {
    releaseVadLoopOnly();
    if (recorder && recorder.state !== "inactive") {
      try { recorder.onstop = null; recorder.stop(); } catch (err) { /* noop */ }
    }
    recorder = null;
    recorderChunks = [];
    analyser = null;
    vad = null;
  }

  function teardownServerCall() {
    releaseServerMic();
    if (audioContext) {
      try { audioContext.close(); } catch (err) { /* already closed */ }
      audioContext = null;
    }
    if (mediaStream) {
      mediaStream.getTracks().forEach(function (track) { track.stop(); });
      mediaStream = null;
    }
  }

  function dispatchServerUtterance(blob) {
    dispatching = true;
    setCallState("transcribing");  // ⏳ Entendiendo…
    var form = new FormData();
    form.append("audio", blob, "clip.webm");
    fetchWithTimeout("/transcribe", { method: "POST", body: form }, 60000)
      .then(function (resp) {
        if (!resp.ok) {
          return resp.json().catch(function () { return {}; }).then(function (data) {
            var detail = data && data.detail ? data.detail : ("HTTP " + resp.status);
            showBanner("No pude transcribir (HTTP " + resp.status + "): " + detail);
          });
        }
        return resp.json().then(function (data) {
          var text = (data && data.text ? data.text : "").trim();
          if (!text) {
            showBanner("No entendí el audio — inténtalo de nuevo.");
            return;
          }
          ask(text);  // sets thinking; its chain resumes listening afterwards
        });
      })
      .catch(function (err) {
        showBanner("No pude enviar el audio al servidor — " +
          (err && err.message ? err.message : "error de red") + ".");
      })
      .then(function () {
        dispatching = false;
        interimEl.textContent = "";
        interimEl.classList.add("hidden");
        if (callState === "transcribing") afterAnswer();
      });
  }

  function finishServerUtterance() {
    if (!recorder || recorder.state === "inactive") return;
    releaseVadLoopOnly();
    var capturedMime = recorder.mimeType || "";
    var onStop = function () {
      recorder = null;
      var blob = new Blob(recorderChunks, { type: capturedMime || "audio/webm" });
      recorderChunks = [];
      if (blob.size < 1000) {  // a sliver: no real audio captured
        if (inCall && callState === "recording") setCallState("listening");
        if (callState === "listening") startServerListening();
        return;
      }
      dispatchServerUtterance(blob);
    };
    recorder.onstop = onStop;
    try { recorder.stop(); } catch (err) { onStop(); }
  }

  function releaseVadLoopOnly() {
    if (vadTick) { clearInterval(vadTick); vadTick = null; }
  }

  function startServerListening() {
    if (textMode || callState !== "listening" || dispatching || serverMicBusy) return;
    if (!serverEngineSupported()) {
      showBanner("Este navegador no soporta el motor Servidor — usa el motor Navegador.");
      return;
    }
    serverMicBusy = true;
    listeningStartedAt = Date.now();

    var armUtterance = function () {
      vad = window.Vad.createVad();
      recorderChunks = [];
      var mime = pickRecorderMime();
      try {
        recorder = mime ? new MediaRecorder(mediaStream, { mimeType: mime })
                        : new MediaRecorder(mediaStream);
      } catch (err) {
        serverMicBusy = false;
        showBanner("No pude abrir la grabación — prueba el motor Navegador.");
        return;
      }
      recorder.ondataavailable = function (event) {
        // Real MediaRecorder emits Blobs (.size); tolerate typed arrays too.
        if (event.data && (event.data.size || event.data.length)) {
          recorderChunks.push(event.data);
        }
      };
      recorder.start(250);  // flush chunks regularly; we stop at the boundary
      releaseVadLoopOnly();
      vadTick = setInterval(function () {
        if (!vad) return;
        var speaking = vad.push(frameRms());
        if (callState === "listening") {
          setCallState(speaking || vad.hasSpeech() ? "recording" : "listening");
        }
        if (vad.shouldFinalize()) {
          finishServerUtterance();
          return;
        }
        // Parity with the browser engine: 5s of dead mic gets called out.
        var elapsed = Date.now() - listeningStartedAt;
        if (!vad.hasSpeech() && elapsed > 5000) {
          pillSub.textContent = "No te oigo — comprueba el micro";
          pillSub.classList.remove("hidden");
        } else if (vad.hasSpeech()) {
          pillSub.classList.add("hidden");
        }
      }, 100);
      serverMicBusy = false;
    };

    if (mediaStream && audioContext) {
      armUtterance();
      return;
    }
    var openStream = navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true }
    });
    openStream.then(function (stream) {
      mediaStream = stream;  // kept for the whole call
      var Ctx = window.AudioContext || window.webkitAudioContext;
      audioContext = new Ctx();
      var source = audioContext.createMediaStreamSource(stream);
      analyser = audioContext.createAnalyser();
      analyser.fftSize = 1024;
      source.connect(analyser);
      vadSampleBuf = new Float32Array(analyser.fftSize);
      armUtterance();
    }).catch(function (err) {
      serverMicBusy = false;
      var name = err && err.name ? err.name : "";
      if (name === "NotAllowedError" || name === "SecurityError") {
        showBanner("Micrófono BLOQUEADO: concede el permiso en el navegador " +
          "(menú ⋮ → Ajustes del sitio → Micrófono) y vuelve a llamar.");
        enterTextMode("Micrófono bloqueado — usa el teclado o concede el permiso en el navegador.");
      } else {
        showBanner("No pude abrir el micrófono (" + (name || "error") +
          ") — prueba el motor Navegador.");
        if (inCall && callState === "listening") setCallState("paused");
      }
    });
  }

  function stopServerListening() {
    manualStop = true;
    releaseVadLoopOnly();
    if (recorder && recorder.state !== "inactive") {
      try { recorder.onstop = null; recorder.stop(); } catch (err) { /* noop */ }
    }
    recorder = null;
    recorderChunks = [];
    vad = null;
  }

  /* Engine dispatch: one name pair, both engines. */
  function startListening() {
    if (voiceEngine === "servidor") startServerListening();
    else startBrowserListening();
  }

  function stopListening() {
    if (voiceEngine === "servidor") stopServerListening();
    else stopBrowserListening();
  }

  /* ---------------- call button / pause / voice engine toggle ------------- */

  function startCall() {
    inCall = true;
    openEvents();  // user gesture: unlocks autoplay for announcements
    setCallState("listening");
    startListening();
  }

  function endCall() {
    inCall = false;
    stopListening();
    teardownServerCall();  // release the getUserMedia stream + AudioContext
    stopAudio();
    hideToast();
    setCallState("idle");
  }

  callBtn.addEventListener("click", function () {
    if (inCall) endCall();
    else startCall();
  });

  pauseBtn.addEventListener("click", function () {
    if (callState === "listening" || callState === "recording") {
      stopListening();
      setCallState("paused");
    } else if (callState === "paused") {
      setCallState("listening");
      startListening();
    }
  });

  function renderVoiceEngine() {
    voiceEngineBtn.textContent = "Voz: " +
      (voiceEngine === "servidor" ? "Servidor" : "Navegador");
    voiceEngineBtn.title = voiceEngine === "servidor"
      ? "Motor Servidor: tu voz se transcribe en el servidor (funciona con auriculares BT). Toca para cambiar al Navegador."
      : "Motor Navegador: reconocimiento del navegador (Web Speech). Toca para cambiar al Servidor.";
  }

  voiceEngineBtn.addEventListener("click", function () {
    var next = voiceEngine === "servidor" ? "navegador" : "servidor";
    if (next === "navegador" && !SR) {
      showBanner("El motor Navegador no está disponible en este navegador.");
      return;
    }
    if (next === "servidor" && !serverEngineSupported()) {
      showBanner("El motor Servidor no está disponible en este navegador.");
      return;
    }
    voiceEngine = next;
    localStorage.setItem(VOICE_KEY, voiceEngine);
    renderVoiceEngine();
    hideBanner();
    if (inCall) {
      // Switch live: tear the running engine down and boot the new one.
      stopListening();
      teardownServerCall();
      teardownRecognition();
      if (callState === "recording" || callState === "transcribing") {
        dispatching = false;  // abandon the in-flight utterance cleanly
        setCallState("listening");
      }
      if (callState === "listening" || callState === "paused") {
        setCallState("listening");
        startListening();
      }
    }
  });

  /* ---------------- text fallback (also desktop testing) ---------------- */

  fallbackForm.addEventListener("submit", function (event) {
    event.preventDefault();
    var text = textInput.value.trim();
    if (!text) return;
    textInput.value = "";
    ask(text);
  });

  function initSpeech() {
    if (!SR && !serverEngineSupported()) {
      pauseBtn.classList.add("hidden");
      fallbackForm.classList.remove("hidden");
      enterTextMode("Ni reconocimiento de voz ni micrófono disponibles en este navegador — modo teclado.");
    } else if (!SR) {
      // Web Speech missing (e.g. desktop Firefox): servidor is the only engine.
      voiceEngine = "servidor";
      localStorage.setItem(VOICE_KEY, voiceEngine);
    }
    renderVoiceEngine();
  }

  /* ---------------- new conversation ---------------- */

  $("new-conversation").addEventListener("click", function () {
    stopAudio();
    stopListening();
    conv.textContent = "";
    interimEl.textContent = "";
    interimEl.classList.add("hidden");
    hideBanner();
    fetch("/reset", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ session_id: sessionId })
    }).catch(function () {
      showBanner("No se pudo reiniciar la conversación en el servidor.");
    });
    if (inCall && callState === "listening") startListening();
  });

  /* ---------------- full-text agent sheet ---------------- */

  var sheetTab = "conv";

  function renderSheetConversation(data) {
    sheetConv.textContent = "";
    var turns = (data && data.turns) || [];
    if (!turns.length) {
      var empty = document.createElement("div");
      empty.className = "c-turn";
      empty.textContent = "Sin conversación legible para este agente.";
      sheetConv.appendChild(empty);
      return;
    }
    var lastAgentBlock = null;
    var lastAgentText = "";
    for (var i = 0; i < turns.length; i++) {
      var block = document.createElement("div");
      block.className = "c-turn";
      var role = document.createElement("div");
      role.className = "c-role " + turns[i].role;
      role.textContent = turns[i].role === "user" ? "tú" : "agente";
      var text = document.createElement("div");
      text.className = "c-text";
      text.textContent = turns[i].text;
      block.appendChild(role);
      block.appendChild(text);
      sheetConv.appendChild(block);
      if (turns[i].role !== "user") {
        lastAgentBlock = block;
        lastAgentText = turns[i].text;
      }
    }
    if (lastAgentBlock) attachReplay(sheetConv, lastAgentBlock, function () { return lastAgentText; }, "agente");
    if (data && data.window && turns.length >= data.window) {
      sheetNote.textContent = "Últimas " + data.window + " intervenciones (ventana fija).";
      sheetNote.classList.remove("hidden");
    } else {
      sheetNote.classList.add("hidden");
    }
  }

  function loadSheetConversation() {
    sheetConv.textContent = "Cargando…";
    var qs = selectedPane ? "?pane_id=" + encodeURIComponent(selectedPane) : "";
    fetch("/conversation" + qs)
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        renderSheetConversation(data);
        markPollOk();
      })
      .catch(function (err) {
        sheetConv.textContent = "No se pudo cargar la conversación.";
        markPollError("conversación", err);
      });
  }

  function loadSheetScreen() {
    sheetScreen.textContent = "Cargando…";
    var qs = selectedPane ? "?pane_id=" + encodeURIComponent(selectedPane) : "";
    fetch("/screen" + qs)
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        sheetScreen.textContent = (data && data.screen) || "(pantalla no disponible)";
        markPollOk();
      })
      .catch(function (err) {
        sheetScreen.textContent = "(pantalla no disponible)";
        markPollError("pantalla", err);
      });
  }

  function renderSheetTab() {
    var isConv = sheetTab === "conv";
    tabConv.className = isConv ? "active" : "";
    tabScreen.className = isConv ? "" : "active";
    sheetConv.classList.toggle("hidden", !isConv);
    sheetScreen.classList.toggle("hidden", isConv);
    sheetNote.classList.toggle("hidden", !isConv || sheetNote.textContent.indexOf("Últimas") !== 0);
  }

  function openSheet() {
    sheetTitle.textContent = paneTitle.textContent || "Agente";
    sheet.classList.remove("hidden");
    sheetTab = "conv";
    renderSheetTab();
    loadSheetConversation();
  }

  function closeSheet() {
    sheet.classList.add("hidden");
  }

  agentView.addEventListener("click", function () {
    refreshView();  // "toca para reintentar": refresh glance, then show the sheet
    openSheet();
  });
  $("sheet-close").addEventListener("click", closeSheet);
  $("sheet-refresh").addEventListener("click", function () {
    if (sheet.classList.contains("hidden")) return;
    if (sheetTab === "conv") loadSheetConversation();
    else loadSheetScreen();
  });
  tabConv.addEventListener("click", function () { sheetTab = "conv"; renderSheetTab(); });
  tabScreen.addEventListener("click", function () { sheetTab = "screen"; renderSheetTab(); loadSheetScreen(); });

  /* ---------------- boot ---------------- */

  initSpeech();
  renderDiag();
  refreshState();
  setInterval(refreshState, 5000);

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(function () { /* best effort */ });
  }
})();
