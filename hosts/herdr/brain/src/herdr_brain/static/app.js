"use strict";

/* herdr-brain PWA: one-tap call UI.
 * Speech recognition via the Web Speech API (browser-side, no server STT);
 * answers arrive as text + audio_url from POST /ask and play in the device. */

(function () {
  var $ = function (id) { return document.getElementById(id); };
  var callBtn = $("call-btn");
  var stopBtn = $("stop-audio");
  var conv = $("conversation");
  var interimEl = $("interim");
  var bannerEl = $("banner");
  var player = $("player");
  var chip = $("status-chip");
  var paneTitle = $("pane-title");
  var fallbackForm = $("text-fallback");
  var textInput = $("text-input");
  var agentView = $("agent-view");
  var pendingBanner = $("pending-banner");
  var viewTranscript = $("view-transcript");
  var viewScreen = $("view-screen");
  var herdStrip = $("herd-strip");
  var herdNote = $("herd-note");
  var lastViewJson = "";

  /* Persisted per-install selection; falls back to the focused pane when
   * the selected one disappears from the herd. */
  var PANE_KEY = "herdr-brain-pane";
  var selectedPane = localStorage.getItem(PANE_KEY);

  var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  var recognition = null;
  var listening = false;
  var thinking = false;

  /* Stable per-install client session so the brain keeps conversation
   * memory across page loads; a fresh id is minted only if none exists. */
  var SESSION_KEY = "herdr-brain-session";
  var sessionId = localStorage.getItem(SESSION_KEY);
  if (!sessionId) {
    sessionId = "s-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
    localStorage.setItem(SESSION_KEY, sessionId);
  }

  /* ---------- conversation view ---------- */

  function addTurn(role, text) {
    var turn = document.createElement("div");
    turn.className = "turn " + role;
    var who = document.createElement("span");
    who.className = "who";
    who.textContent = role === "user" ? "you" : "brain";
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

  /* ---------- herd strip (all agents) + selection ---------- */

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
      herdNote.textContent = "Selected agent is gone — back to the focused one.";
      selectedPane = effective;
      if (effective) localStorage.setItem(PANE_KEY, effective);
    }

    herdStrip.textContent = "";
    for (var i = 0; i < herd.length; i++) {
      var agent = herd[i];
      var chipEl = document.createElement("button");
      chipEl.className = "herd-chip" + (agent.pane_id === effective ? " selected" : "");
      chipEl.title = (agent.cwd || "") + (agent.last_turn ? " — " + agent.last_turn.text : "");
      var st = document.createElement("span");
      st.className = "st " + (agent.agent_status || "");
      st.textContent = "● " + (agent.agent_status || "?");
      chipEl.appendChild(st);
      chipEl.appendChild(document.createTextNode(agent.title || agent.agent || agent.pane_id));
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
    lastViewJson = "";  // force a panel refresh for the new target
    refreshView();
  }

  /* ---------- state polling ---------- */

  function renderStatus(state) {
    if (!state || !state.active) {
      chip.textContent = "none";
      chip.className = "chip";
      paneTitle.textContent = "no active agent pane";
      return;
    }
    chip.textContent = state.agent_status || "unknown";
    chip.className = "chip " + (state.agent_status || "");
    var title = state.title || state.agent || "";
    paneTitle.textContent = title + (state.cwd ? " — " + state.cwd : "");
  }

  function renderPending(pending) {
    if (!pending || !pending.detected) {
      pendingBanner.classList.add("hidden");
      pendingBanner.className = "pending hidden";
      return;
    }
    var kind = pending.kind || "question";
    var headline = {
      permission: "Permission requested",
      error: "Agent error",
      question: "Agent is asking"
    }[pending.kind] || "Agent needs input";
    pendingBanner.textContent = headline + ": " + (pending.excerpt || "check the terminal");
    pendingBanner.className = "pending " + kind;
  }

  function renderViewTail(view) {
    /* transcript turns (secondary, compact) */
    viewTranscript.textContent = "";
    var turns = (view && view.transcript) || [];
    for (var i = 0; i < turns.length; i++) {
      var line = document.createElement("div");
      line.className = "view-turn " + turns[i].role;
      var role = document.createElement("span");
      role.className = "role";
      role.textContent = turns[i].role === "user" ? "you" : "agent";
      line.appendChild(role);
      line.appendChild(document.createTextNode(turns[i].text));
      viewTranscript.appendChild(line);
    }
    agentView.classList.toggle("hidden", !view || (!turns.length && !view.screen && !(view.pending && view.pending.detected)));
    /* dimmed screen tail */
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

  /* ---------- audio playback ---------- */

  function play(url) {
    player.src = url;
    stopBtn.classList.remove("hidden");
    var p = player.play();
    if (p && p.catch) p.catch(function () { /* autoplay blocked; text is shown */ });
  }

  function stopAudio() {
    player.pause();
    player.removeAttribute("src");
    player.load();
    stopBtn.classList.add("hidden");
  }

  player.addEventListener("ended", function () {
    stopBtn.classList.add("hidden");
  });
  stopBtn.addEventListener("click", stopAudio);

  /* ---------- ask pipeline ---------- */

  function setThinking(value) {
    thinking = value;
    if (value) {
      callBtn.disabled = true;
      callBtn.textContent = "… thinking";
    } else {
      callBtn.disabled = false;
      callBtn.textContent = listening ? "■ Listening… tap to stop" : "● Call";
    }
  }

  function ask(text) {
    if (thinking) return Promise.resolve();
    addTurn("user", text);
    setThinking(true);
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
          showBanner("Brain is not configured: GLM_API_KEY is missing on the server. " +
            "Add it to ~/.dotfiles/shell/private-env.sh and restart the service.");
          return;
        }
        if (!resp.ok) {
          showBanner("Ask failed (HTTP " + resp.status + "). Try again.");
          return;
        }
        return resp.json().then(function (data) {
          addTurn("brain", data.answer || "(empty answer)");
          if (data.audio_url) play(data.audio_url);
        });
      })
      .catch(function () {
        showBanner("Network error talking to the brain.");
      })
      .then(function () {
        setThinking(false);
        interimEl.textContent = "";
        interimEl.classList.add("hidden");
      });
  }

  /* ---------- speech recognition ---------- */

  function startListening() {
    hideBanner();
    interimEl.classList.remove("hidden");
    interimEl.textContent = "…";
    listening = true;
    callBtn.classList.add("listening");
    callBtn.textContent = "■ Listening… tap to stop";

    recognition = new SR();
    recognition.lang = navigator.language || "es-ES";
    recognition.interimResults = true;
    recognition.continuous = false;

    recognition.onresult = function (event) {
      var interim = "";
      var finalText = "";
      for (var i = event.resultIndex; i < event.results.length; i++) {
        var result = event.results[i];
        if (result.isFinal) {
          finalText += result[0].transcript;
        } else {
          interim += result[0].transcript;
        }
      }
      interimEl.textContent = interim || "…";
      if (finalText && !thinking) {
        interimEl.textContent = "";
        interimEl.classList.add("hidden");
        stopListening();
        ask(finalText.trim());
      }
    };

    recognition.onerror = function (event) {
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        showBanner("Microphone access denied. Allow mic permission for this site " +
          "(browser settings) and try again.");
      } else if (event.error === "no-speech") {
        interimEl.textContent = "heard nothing — tap Call and speak again";
      } else if (event.error !== "aborted") {
        showBanner("Speech recognition error: " + event.error);
      }
    };

    recognition.onend = function () {
      listening = false;
      recognition = null;
      callBtn.classList.remove("listening");
      if (!thinking) {
        callBtn.textContent = "● Call";
        if (interimEl.textContent === "…") {
          interimEl.textContent = "";
          interimEl.classList.add("hidden");
        }
      }
    };

    try {
      recognition.start();
    } catch (err) {
      showBanner("Could not start speech recognition: " + err.message);
      listening = false;
    }
  }

  function stopListening() {
    if (recognition) {
      try { recognition.stop(); } catch (err) { /* already stopped */ }
    }
  }

  callBtn.addEventListener("click", function () {
    if (thinking) return;
    if (listening) {
      stopListening();
      return;
    }
    if (!SR) return; /* fallback form is shown instead */
    startListening();
  });

  /* ---------- text fallback (also for desktop testing) ---------- */

  fallbackForm.addEventListener("submit", function (event) {
    event.preventDefault();
    var text = textInput.value.trim();
    if (!text) return;
    textInput.value = "";
    ask(text);
  });

  /* ---------- new conversation ---------- */

  $("new-conversation").addEventListener("click", function () {
    if (thinking) return;
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
      showBanner("Could not reset the conversation on the server.");
    });
  });

  function initSpeech() {
    if (!SR) {
      callBtn.classList.add("hidden");
      fallbackForm.classList.remove("hidden");
      paneTitle.textContent = document.title + " — text mode";
    }
  }

  initSpeech();
  refreshState();
  setInterval(refreshState, 5000);

  /* ---------- installability ---------- */

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(function () { /* best effort */ });
  }
})();
