"use strict";

/* herdr-brain PWA: continuous hands-free call.
 *
 * Call state machine: idle -> listening -> thinking -> speaking -> listening…
 * - Speech recognition auto-restarts (with backoff) while listening.
 * - Recognition is stopped while thinking/speaking so the mic never hears
 *   the TTS. Tapping the pill while speaking is barge-in.
 * - The pause button silences the mic without ending the call.
 * - Announcements arrive over SSE and queue behind any in-flight audio.
 * - No SpeechRecognition (or persistent mic failure): text input fallback.
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
  var agentView = $("agent-view");
  var pendingBanner = $("pending-banner");
  var viewTranscript = $("view-transcript");
  var viewScreen = $("view-screen");
  var herdStrip = $("herd-strip");
  var herdNote = $("herd-note");
  var toastEl = $("toast");
  var sheet = $("sheet");
  var sheetTitle = $("sheet-title");
  var sheetNote = $("sheet-note");
  var sheetConv = $("sheet-conversation");
  var sheetScreen = $("sheet-screen");
  var tabConv = $("tab-conv");
  var tabScreen = $("tab-screen");

  var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  var recognition = null;
  var listening = false;       // a recognition object is actually running
  var manualStop = false;      // we stopped the mic on purpose this turn
  var recRestartTimer = null;
  var recRestartDelay = 300;
  var recFailures = 0;
  var textMode = false;        // mic unavailable: keyboard fallback

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

  /* ---------------- call state machine ----------------
   * idle | listening | thinking | speaking | paused  */
  var callState = "idle";
  var inCall = false;

  var PILL_TEXT = {
    listening: "● Escuchando",
    thinking: "⏳ Pensando…",
    speaking: "🔊 Hablando — toca para cortar",
    paused: "⏸ Micrófono en pausa"
  };

  function setCallState(state) {
    callState = state;
    if (state === "idle") {
      statePill.classList.add("hidden");
      callBtn.textContent = "📞 Llamar";
      callBtn.classList.remove("oncall");
      pauseBtn.classList.add("hidden");
      callBtn.disabled = false;
      return;
    }
    statePill.textContent = PILL_TEXT[state] || state;
    statePill.className = state;
    statePill.classList.remove("hidden");
    callBtn.textContent = "🔴 Colgar";
    callBtn.classList.add("oncall");
    callBtn.disabled = false;
    var micControllable = state === "listening" || state === "paused";
    pauseBtn.classList.toggle("hidden", !micControllable);
    pauseBtn.textContent = state === "paused" ? "▶ Reanudar" : "⏸ Pausa";
    interimEl.classList.toggle("hidden", state !== "listening");
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
    conv.appendChild(turn);
    conv.scrollTop = conv.scrollHeight;
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

  function hideMicNote() {
    micNote.classList.add("hidden");
  }

  function enterTextMode(reason) {
    if (textMode) return;
    textMode = true;
    if (inCall && callState !== "paused") endCall();
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
    for (var i = 0; i < turns.length; i++) {
      var line = document.createElement("div");
      line.className = "view-turn " + turns[i].role;
      var role = document.createElement("span");
      role.className = "role";
      role.textContent = turns[i].role === "user" ? "tú" : "agente";
      line.appendChild(role);
      line.appendChild(document.createTextNode(turns[i].text));
      viewTranscript.appendChild(line);
    }
    var interesting = view && (turns.length || view.screen || (view.pending && view.pending.detected));
    agentView.classList.toggle("hidden", !interesting);
    if (view && view.screen) {
      viewScreen.textContent = view.screen;
      viewScreen.classList.remove("hidden");
    } else {
      viewScreen.textContent = "";
      viewScreen.classList.add("hidden");
    }
  }

  function renderView(view) {
    if (!view) return;
    renderStatus(view.status);
    renderPending(view.pending);
    var tailJson = JSON.stringify([view.transcript, view.screen]);
    if (tailJson !== lastViewJson) {
      lastViewJson = tailJson;
      renderViewTail(view);
    }
  }

  function refreshView() {
    var qs = selectedPane ? "?pane_id=" + encodeURIComponent(selectedPane) : "";
    fetch("/view" + qs)
      .then(function (resp) { return resp.ok ? resp.json() : null; })
      .then(renderView)
      .catch(function () { /* keep last known state */ });
  }

  function refreshState() {
    fetch("/herd")
      .then(function (resp) { return resp.ok ? resp.json() : []; })
      .then(renderHerd)
      .catch(function () { /* keep last known strip */ });
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

  statePill.addEventListener("click", function () {
    if (callState === "speaking") stopAudio();  // barge-in
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
    addTurn("user", text);
    setCallState("thinking");
    stopListening();  // the mic must not hear the answer
    return fetch("/ask", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        text: text,
        session_id: sessionId,
        pane_id: selectedPane || null
      })
    })
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
      .catch(function () {
        showBanner("Error de red hablando con el brain.");
      })
      .then(function () {
        interimEl.textContent = "";
        interimEl.classList.add("hidden");
        if (callState === "thinking") afterAnswer();
      });
  }

  /* ---------------- speech recognition ---------------- */

  function startListening() {
    if (textMode || !SR || callState !== "listening" || listening) return;
    manualStop = false;
    hideBanner();
    interimEl.classList.remove("hidden");
    interimEl.textContent = "…";

    recognition = new SR();
    recognition.lang = navigator.language || "es-ES";
    recognition.interimResults = true;
    recognition.continuous = false;

    recognition.onresult = function (event) {
      var interim = "";
      var finalText = "";
      for (var i = event.resultIndex; i < event.results.length; i++) {
        var result = event.results[i];
        if (result.isFinal) finalText += result[0].transcript;
        else interim += result[0].transcript;
      }
      interimEl.textContent = interim || "…";
      if (finalText && callState === "listening") {
        recRestartDelay = 300;
        recFailures = 0;
        interimEl.textContent = "";
        interimEl.classList.add("hidden");
        stopListening();
        ask(finalText.trim());
      }
    };

    recognition.onerror = function (event) {
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        enterTextMode("Micrófono bloqueado — usa el teclado o concede el permiso en el navegador.");
        return;
      }
      if (event.error === "aborted") return;
      recFailures += 1;
      if (recFailures > 8) {
        enterTextMode("El reconocimiento de voz falla repetidamente — cambiado a teclado.");
      }
      /* no-speech and friends: silent quick restart with backoff (onend). */
    };

    recognition.onend = function () {
      listening = false;
      recognition = null;
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
    } catch (err) {
      listening = false;
      recRestartTimer = setTimeout(startListening, recRestartDelay);
      recRestartDelay = Math.min(recRestartDelay * 2, 3000);
    }
  }

  function stopListening() {
    manualStop = true;
    if (recRestartTimer) { clearTimeout(recRestartTimer); recRestartTimer = null; }
    if (recognition) {
      try { recognition.stop(); } catch (err) { /* already stopped */ }
    }
    listening = false;
  }

  /* ---------------- call button / pause ---------------- */

  function startCall() {
    inCall = true;
    openEvents();  // user gesture: unlocks autoplay for announcements
    setCallState("listening");
    startListening();
  }

  function endCall() {
    inCall = false;
    stopListening();
    stopAudio();
    hideToast();
    setCallState("idle");
  }

  callBtn.addEventListener("click", function () {
    if (inCall) endCall();
    else startCall();
  });

  pauseBtn.addEventListener("click", function () {
    if (callState === "listening") {
      stopListening();
      setCallState("paused");
    } else if (callState === "paused") {
      setCallState("listening");
      startListening();
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
    if (!SR) {
      pauseBtn.classList.add("hidden");
      fallbackForm.classList.remove("hidden");
      enterTextMode("Reconocimiento de voz no disponible en este navegador — modo teclado.");
    }
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

  var sheetOpen = false;
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
    }
    if (data && data.window && turns.length >= data.window) {
      sheetNote.textContent = "Últimas " + data.window + " intervenciones (ventana fija).";
      sheetNote.classList.remove("hidden");
    } else {
      sheetNote.classList.add("hidden");
    }
  }

  function loadSheetConversation() {
    var qs = selectedPane ? "?pane_id=" + encodeURIComponent(selectedPane) : "";
    fetch("/conversation" + qs)
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(renderSheetConversation)
      .catch(function () {
        sheetConv.textContent = "No se pudo cargar la conversación.";
      });
  }

  function loadSheetScreen() {
    var qs = selectedPane ? "?pane_id=" + encodeURIComponent(selectedPane) : "";
    fetch("/screen" + qs)
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        sheetScreen.textContent = (data && data.screen) || "(pantalla no disponible)";
      })
      .catch(function () {
        sheetScreen.textContent = "(pantalla no disponible)";
      });
  }

  function renderSheetTab() {
    var conv = sheetTab === "conv";
    tabConv.className = conv ? "active" : "";
    tabScreen.className = conv ? "" : "active";
    sheetConv.classList.toggle("hidden", !conv);
    sheetScreen.classList.toggle("hidden", conv);
    sheetNote.classList.toggle("hidden", !conv || sheetNote.textContent.indexOf("Últimas") !== 0);
  }

  function openSheet() {
    sheetOpen = true;
    sheetTitle.textContent = paneTitle.textContent || "Agente";
    sheet.classList.remove("hidden");
    sheetTab = "conv";
    renderSheetTab();
    loadSheetConversation();
  }

  function closeSheet() {
    sheetOpen = false;
    sheet.classList.add("hidden");
  }

  agentView.addEventListener("click", openSheet);
  $("sheet-close").addEventListener("click", closeSheet);
  $("sheet-refresh").addEventListener("click", function () {
    if (!sheetOpen) return;
    if (sheetTab === "conv") loadSheetConversation();
    else loadSheetScreen();
  });
  tabConv.addEventListener("click", function () { sheetTab = "conv"; renderSheetTab(); });
  tabScreen.addEventListener("click", function () { sheetTab = "screen"; renderSheetTab(); loadSheetScreen(); });

  /* ---------------- boot ---------------- */

  initSpeech();
  refreshState();
  setInterval(refreshState, 5000);

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(function () { /* best effort */ });
  }
})();
