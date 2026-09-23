"use strict";

/* herdr-brain PWA: continuous hands-free call over a cockpit-first layout.
 *
 * Layout (PRD call-drawer-redesign):
 * - BASE SCREEN = agent cockpit: herd strip, glance turn list (fed by
 *   GET /conversation: 20 turns, 4000-char clip) with real scroll +
 *   "ver más" inline expansion, and the terminal preview (fed by /view)
 *   with real scroll. The old full-screen sheet is gone.
 * - THE CALL = bottom drawer anchored above the footer: auto-opens on
 *   call start, closes without hanging up, reopens from the state pill.
 *   Engine-dependent interim strip (navegador: live text; servidor:
 *   recording dot + VAD level meter + utterance timer — never fake text).
 *
 * Call state machine: idle -> listening -> thinking -> speaking -> listening…
 * - ONE recognition lifecycle per listening session (continuous=true) with
 *   SELF-ENDPOINTING (static/endpointing.js): dispatch on isFinal, on 1200ms
 *   of interim silence, or on a 15s hard cap. No mic during /ask or TTS.
 * - Announcements arrive over SSE and queue behind any in-flight audio.
 * - Every poll render is KEYED (FR10): herd chips and glance turns are
 *   updated in place per id/content, so the 5s poll never swallows taps,
 *   resets scroll. One render error never
 *   kills the loop and every surface has a Spanish empty/error state.
 * User-facing strings are Spanish on purpose (single Spanish-speaking owner).
 */

(function () {
  var $ = function (id) { return document.getElementById(id); };
  var callBtn = $("call-btn");
  var pauseBtn = $("pause-btn");
  var stopBtn = $("stop-audio");
  var conv = $("conversation");
  var interimTextEl = $("interim-text");
  var recMeterEl = $("rec-meter");
  var recElapsedEl = $("rec-elapsed");
  var stripStatusEl = $("strip-status");
  var meterCellsEl = $("meter-cells");
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
  var pendingBanner = $("pending-banner");
  var viewScreen = $("view-screen");
  var herdStrip = $("herd-strip");
  var herdNote = $("herd-note");
  var toastEl = $("toast");
  var drawer = $("call-drawer");
  var drawerCloseBtn = $("drawer-close");
  var settingsSheet = $("settings-sheet");
  var settingsBtn = $("settings-btn");
  var settingsCloseBtn = $("settings-close");
  var callTimerEl = $("call-timer");
  var dPendingEl = $("d-pending");
  var glanceTurns = $("glance-turns");
  var glanceEmpty = $("glance-empty");
  var glanceLabel = $("glance-label");
  var diagPanel = $("diag-panel");
  var diagText = $("diag-text");
  var diagStatus = $("diag-status");
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

  /* Mid-call reload marker (FR15): boot shows "Llamada cortada" and NEVER
   * auto-resumes the mic; a tap starts a fresh call. */
  var CALL_INTENT_KEY = "herdr-brain-call-intent";
  var interruptedFromReload = false;
  function markCallIntent() {
    try { sessionStorage.setItem(CALL_INTENT_KEY, "1"); } catch (err) { /* private mode */ }
  }
  function clearCallIntent() {
    try { sessionStorage.removeItem(CALL_INTENT_KEY); } catch (err) { /* noop */ }
  }

  /* ---------------- call state machine ----------------
   * idle | listening | thinking | speaking | paused
   * (+ recording/transcribing: servidor engine sub-states) */
  var callState = "idle";
  var inCall = false;
  var drawerOpen = false;
  var settingsOpen = false;
  var callStartedAt = null;
  var timerTick = null;
  var utteranceStartedAt = null;  // servidor: current utterance (for the strip timer)
  var lastElapsedShown = -1;
  var wakeLock = null;
  var METER_CELLS = 12;
  var meterLit = -1;

  var PILL_TEXT = {
    listening: "● Escuchando",
    recording: "● Grabando — habla",
    transcribing: "⏳ Entendiendo…",
    thinking: "⏳ Pensando…",
    speaking: "🔊 Hablando",
    paused: "⏸ Micrófono en pausa"
  };

  function setCallState(state) {
    callState = state;
    if (state !== "recording" && utteranceStartedAt) {
      utteranceStartedAt = null;
      lastElapsedShown = -1;
      recElapsedEl.textContent = "0:00";
    }
    pillSub.classList.add("hidden");
    if (state === "idle") {
      callBtn.textContent = "📞 Llamar";
      callBtn.classList.remove("oncall");
      pauseBtn.classList.add("hidden");
    } else {
      callBtn.textContent = "🔴 Colgar";
      callBtn.classList.add("oncall");
      var micControllable = state === "listening" || state === "recording" || state === "paused";
      pauseBtn.classList.toggle("hidden", !micControllable);
      pauseBtn.textContent = state === "paused" ? "▶ Reanudar" : "⏸ Pausa";
    }
    renderPill();
    renderInterimStrip();
  }

  /* Pill exists ONLY while calling with the drawer closed (state machine
   * rows 8–13), or in the reload-interrupted boot state (row 17). Idle
   * and drawer-open are pill-free (FR12). */
  function renderPill() {
    if (interruptedFromReload && !inCall) {
      pillMain.textContent = "Llamada cortada — ¿Volver a llamar?";
      statePill.className = "interrupted";
      statePill.classList.remove("hidden");
      return;
    }
    if (inCall && !drawerOpen && !textMode && callState !== "idle") {
      pillMain.textContent = (PILL_TEXT[callState] || callState) + " ▲";
      statePill.className = callState;
      statePill.classList.remove("hidden");
      return;
    }
    statePill.classList.add("hidden");
  }

  /* Interim strip (FR8) — engine-dependent, lives at the drawer top:
   * navegador -> live interim text; servidor -> recording indicator +
   * level meter + elapsed utterance time (never simulated text). */
  function renderInterimStrip() {
    var active = inCall && !textMode;
    var st = callState;
    var browser = voiceEngine === "navegador";
    interimTextEl.classList.toggle("hidden", !(active && browser && st === "listening"));
    var meterOn = active && !browser && (st === "listening" || st === "recording");
    recMeterEl.classList.toggle("hidden", !meterOn);
    var status = "";
    if (active && !browser) {
      if (st === "listening") status = "○ escuchando…";
      else if (st === "transcribing") status = "⏳ transcribiendo…";
      else if (st === "paused") status = "⏸ micrófono en pausa";
    }
    if (status) {
      if (stripStatusEl.textContent !== status) stripStatusEl.textContent = status;
      stripStatusEl.classList.remove("hidden");
    } else {
      stripStatusEl.classList.add("hidden");
    }
  }

  function resetInterimContent() {
    interimTextEl.textContent = "";
    meterLit = -1;          // force the meter to repaint on next push
    lastElapsedShown = -1;
    recElapsedEl.textContent = "0:00";
    renderInterimStrip();
  }

  /* ---------------- call drawer ----------------
   * Close NEVER hangs up (AC2); the pill reopens (AC3); Android back
   * closes instead of exiting the PWA (FR16/AC14). */

  function focusAfterDrawerClose() {
    var target = !statePill.classList.contains("hidden") ? statePill : callBtn;
    try { target.focus({ preventScroll: true }); } catch (err) { /* no focus API */ }
  }

  function openDrawer() {
    if (drawerOpen) return;
    drawerOpen = true;
    drawer.classList.add("open");
    drawer.removeAttribute("inert");
    try { history.pushState({ herdrDrawer: true }, ""); } catch (err) { /* non-http origins */ }
    conv.scrollTop = conv.scrollHeight;  // newest turn (AC1/AC3)
    renderPill();
    try { drawerCloseBtn.focus({ preventScroll: true }); } catch (err) { /* no focus API */ }
  }

  function closeDrawer() {
    if (!drawerOpen) return;
    drawerOpen = false;
    drawer.classList.remove("open");
    drawer.setAttribute("inert", "");
    if (history.state && history.state.herdrDrawer) history.back();  // pop our entry; popstate no-ops
    renderPill();
    focusAfterDrawerClose();
  }

  window.addEventListener("popstate", function () {
    /* Android back with an overlay open: close the topmost one, never exit
     * the PWA. Settings sits above the drawer, so it closes first. */
    if (settingsOpen) {
      settingsOpen = false;
      settingsSheet.classList.remove("open");
      settingsSheet.setAttribute("inert", "");
      try { settingsBtn.focus({ preventScroll: true }); } catch (err) { /* noop */ }
      return;
    }
    if (!drawerOpen) return;
    drawerOpen = false;
    drawer.classList.remove("open");
    drawer.setAttribute("inert", "");
    renderPill();
    focusAfterDrawerClose();
  });

  function openSettings() {
    if (settingsOpen) return;
    settingsOpen = true;
    settingsSheet.classList.add("open");
    settingsSheet.removeAttribute("inert");
    try { history.pushState({ herdrSettings: true }, ""); } catch (err) { /* non-http */ }
    try { settingsCloseBtn.focus({ preventScroll: true }); } catch (err) { /* no focus API */ }
  }

  function closeSettings() {
    if (!settingsOpen) return;
    settingsOpen = false;
    settingsSheet.classList.remove("open");
    settingsSheet.setAttribute("inert", "");
    if (history.state && history.state.herdrSettings) history.back();  // pop our entry
    try { settingsBtn.focus({ preventScroll: true }); } catch (err) { /* noop */ }
  }

  settingsBtn.addEventListener("click", function () {
    if (settingsOpen) closeSettings(); else openSettings();
  });
  settingsCloseBtn.addEventListener("click", closeSettings);

  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", function () {
      /* FR11: the drawer and the soft keyboard never coexist. */
      if (drawerOpen && window.visualViewport.height < window.innerHeight * 0.75) closeDrawer();
    });
  }

  function startCallTimer() {
    stopCallTimer();
    callTimerEl.textContent = "00:00";
    timerTick = setInterval(function () {
      var s = Math.max(0, Math.floor((Date.now() - callStartedAt) / 1000));
      var m = Math.floor(s / 60);
      s = s % 60;
      callTimerEl.textContent = (m < 10 ? "0" + m : m) + ":" + (s < 10 ? "0" + s : s);
    }, 1000);
  }

  function stopCallTimer() {
    if (timerTick) { clearInterval(timerTick); timerTick = null; }
  }

  /* ---------------- wake lock (FR17) ---------------- */

  function requestWakeLock() {
    try {
      if (navigator.wakeLock && navigator.wakeLock.request) {
        navigator.wakeLock.request("screen").then(function (lock) {
          wakeLock = lock;
        }, function () { /* unsupported/denied: silent fail */ });
      }
    } catch (err) { /* unsupported: silent fail */ }
  }

  function releaseWakeLock() {
    if (wakeLock) {
      try { wakeLock.release(); } catch (err) { /* already released */ }
      wakeLock = null;
    }
  }

  document.addEventListener("visibilitychange", function () {
    /* Chrome silently drops wake locks on tab hide: re-acquire mid-call. */
    if (document.visibilityState === "visible" && inCall) requestWakeLock();
  });

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
    if (!diagStatus) return;
    var when = diag.lastPoll ? diag.lastPoll.toLocaleTimeString() : "—";
    diagStatus.textContent = "Última consulta: " + when + " · Último error: " + diag.lastError;
  }

  function safeRender(name, fn, arg) {
    try {
      fn(arg);
    } catch (err) {
      markPollError(name, err);
    }
  }

  /* ---------------- conversation (drawer body) ---------------- */

  function updateGhost() {
    /* FR9: ghost bubble until the first turn of the call. */
    var ghost = document.getElementById("ghost-bubble");
    var hasTurns = !!conv.querySelector(".turn");
    if (!hasTurns && !ghost) {
      ghost = document.createElement("div");
      ghost.id = "ghost-bubble";
      ghost.textContent = "Di algo — te escucho";
      conv.appendChild(ghost);
    } else if (hasTurns && ghost) {
      ghost.remove();
    }
  }

  function addTurn(role, text) {
    var ghost = document.getElementById("ghost-bubble");
    if (ghost) ghost.remove();
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

  /* FR13: SSE announcements append a VISUALLY DISTINCT bubble to the call. */
  function addAnnouncementTurn(label, text) {
    var ghost = document.getElementById("ghost-bubble");
    if (ghost) ghost.remove();
    var turn = document.createElement("div");
    turn.className = "turn announce";
    var who = document.createElement("span");
    who.className = "who";
    who.textContent = "🔊 anuncio · " + (label || "agente");
    var body = document.createElement("span");
    body.textContent = text || "";
    turn.appendChild(who);
    turn.appendChild(body);
    conv.appendChild(turn);
    conv.scrollTop = conv.scrollHeight;
  }

  /* Replay pill: within one container, only the newest kept turn has it.
   * getText resolves the text at tap time (the glance turns already carry
   * the full /conversation text, so no extra fetch is needed). */
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
      event.stopPropagation();  // never bubble into container-level taps
      Promise.resolve(getText()).then(function (text) {
        speakText(text, label);
      });
    });
    turn.appendChild(btn);
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
    if (inCall) endCall();  // endCall closes the drawer (AC4/AC11)
    else closeDrawer();     // FR11: the drawer and the soft keyboard never coexist
    fallbackForm.classList.remove("hidden");
    showMicNote(reason);
    renderPill();
    renderInterimStrip();
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

  var lastHerd = [];
  var lastPending = { detected: false };
  var pendingBannerTapPane = null;
  var lastEffectivePane = null;

  /* Keyed in-place update (FR10/AC8): chips are reused per pane_id and
   * touched only when their content changed, so the 5s poll can never
   * swallow a tap on the strip. */
  function renderHerd(herd) {
    if (!Array.isArray(herd)) herd = [];
    lastHerd = herd;
    var effective = effectiveSelected(herd);
    lastEffectivePane = effective;
    var fellBack = !!(selectedPane && effective !== selectedPane);
    herdNote.classList.toggle("hidden", !fellBack);
    if (fellBack) {
      herdNote.textContent = "El agente seleccionado desapareció — volviendo al enfocado.";
      selectedPane = effective;
      if (effective) localStorage.setItem(PANE_KEY, effective);
    }

    var existing = {};
    var i, el;
    for (i = 0; i < herdStrip.children.length; i++) {
      el = herdStrip.children[i];
      existing[el.getAttribute("data-pane")] = el;
    }
    var wanted = [];
    var seen = {};
    for (i = 0; i < herd.length; i++) {
      var agent = herd[i];
      var id = agent.pane_id;
      seen[id] = true;
      var base = (agent.cwd || "").split("/").filter(Boolean).pop() || agent.title || agent.agent;
      var title = (agent.title || "") + (agent.last_turn ? " — " + agent.last_turn.text : "");
      var cls = "herd-chip" + (id === effective ? " selected" : "");
      el = existing[id];
      if (!el) {
        el = document.createElement("button");
        el.type = "button";
        el.className = "herd-chip";
        el.setAttribute("data-pane", id);
        var stEl = document.createElement("span");
        el.appendChild(stEl);
        var labelEl = document.createElement("span");
        el.appendChild(labelEl);
        el.addEventListener("click", (function (paneId) {
          return function () { selectAgent(paneId); };
        })(id));
      }
      var stCls = "st " + (agent.agent_status || "");
      if (el.firstChild.className !== stCls) el.firstChild.className = stCls;
      if (el.firstChild.nextSibling.textContent !== base) el.firstChild.nextSibling.textContent = base;
      if (el.className !== cls) el.className = cls;
      if (el.title !== title) el.title = title;
      wanted.push(el);
    }
    var stale = [];
    for (i = 0; i < herdStrip.children.length; i++) {
      if (!seen[herdStrip.children[i].getAttribute("data-pane")]) stale.push(herdStrip.children[i]);
    }
    for (i = 0; i < stale.length; i++) stale[i].remove();
    var sameOrder = wanted.length === herdStrip.children.length;
    for (i = 0; sameOrder && i < wanted.length; i++) {
      if (herdStrip.children[i] !== wanted[i]) sameOrder = false;
    }
    if (!sameOrder) {
      for (i = 0; i < wanted.length; i++) herdStrip.appendChild(wanted[i]);
    }
    renderGlanceLabel();       // lastHerd just refreshed: recompute name
    renderPending(lastPending);  // blocked-agent counts may have changed
  }

  function selectAgent(paneId) {
    if (selectedPane === paneId) return;
    selectedPane = paneId;
    localStorage.setItem(PANE_KEY, paneId);
    /* Restyle chips NOW (no fetch): selection feedback must not wait for
     * the next 5s /herd poll. */
    safeRender("herd-restyle", renderHerd, lastHerd);
    glanceNeedsWipe = true;  // another agent's turns: full keyed-cache reset
    lastScreenText = null;
    refreshView();
    refreshConversation();
  }

  /* ---------------- status / pending / terminal (from /view) ---------------- */

  function renderStatus(state) {
    var chipText, chipClass, titleText;
    if (!state || !state.active) {
      chipText = "nadie";
      chipClass = "chip";
      titleText = "sin agente activo";
    } else {
      chipText = state.agent_status || "?";
      chipClass = "chip " + (state.agent_status || "");
      var title = state.title || state.agent || "";
      titleText = title + (state.cwd ? " — " + state.cwd : "");
    }
    if (chip.textContent !== chipText) chip.textContent = chipText;
    if (chip.className !== chipClass) chip.className = chipClass;
    if (paneTitle.textContent !== titleText) paneTitle.textContent = titleText;
  }

  function blockedAgents() {
    var out = [];
    for (var i = 0; i < lastHerd.length; i++) {
      if ((lastHerd[i].agent_status || "").toLowerCase() === "blocked") out.push(lastHerd[i]);
    }
    return out;
  }

  /* Pending banner (FR4): the three kinds keep their colors; multiple
   * pending agents aggregate and tapping selects (cycles to) the next
   * blocked agent. The compact #d-pending strip mirrors the banner inside
   * the drawer on short screens (AC12, CSS media query). */
  function renderPending(pending) {
    lastPending = pending && pending.detected ? pending : { detected: false };
    var detected = lastPending.detected;
    var blocked = blockedAgents();
    var selBlocked = false;
    var otherBlocked = null;
    var i;
    for (i = 0; i < blocked.length; i++) {
      if (blocked[i].pane_id === selectedPane) selBlocked = true;
      else if (!otherBlocked) otherBlocked = blocked[i];
    }
    var count = blocked.length + (detected && !selBlocked ? 1 : 0);
    var actionable = false;
    var tapPane = null;
    if (count >= 2) {
      pendingBanner.className = "pending permission actionable";
      pendingBanner.textContent = count + " agentes esperan tu OK — toca para atender";
      actionable = true;
      if (otherBlocked) {
        tapPane = otherBlocked.pane_id;
      } else {
        for (i = 0; i < blocked.length; i++) {
          if (blocked[i].pane_id !== selectedPane) { tapPane = blocked[i].pane_id; break; }
        }
      }
    } else if (detected) {
      var kind = lastPending.kind || "question";
      var headline = {
        permission: "Pide permiso",
        error: "Error del agente",
        question: "El agente pregunta"
      }[lastPending.kind] || "Necesita tu atención";
      pendingBanner.className = "pending " + kind;
      pendingBanner.textContent = headline + ": " + (lastPending.excerpt || "mira la pantalla");
    } else if (blocked.length === 1) {
      pendingBanner.className = "pending permission actionable";
      pendingBanner.textContent = (blocked[0].title || blocked[0].agent || "Un agente") +
        " espera tu OK — toca para verlo";
      actionable = true;
      tapPane = blocked[0].pane_id;
    } else {
      pendingBanner.className = "pending hidden";
    }
    var hidden = pendingBanner.classList.contains("hidden");
    if (!hidden) {
      if (actionable) {
        pendingBanner.setAttribute("role", "button");
        pendingBanner.setAttribute("tabindex", "0");
      } else {
        pendingBanner.setAttribute("role", "presentation");
        pendingBanner.removeAttribute("tabindex");
      }
      dPendingEl.textContent = pendingBanner.textContent;
    } else {
      dPendingEl.textContent = "";
    }
    pendingBannerTapPane = tapPane;
  }

  pendingBanner.addEventListener("click", function () {
    if (pendingBannerTapPane && pendingBannerTapPane !== selectedPane) {
      selectAgent(pendingBannerTapPane);
    }
  });

  /* Terminal preview (FR3): set textContent ONLY on change so the user's
   * scroll position inside the preview survives the poll. */
  var lastScreenText = null;

  function renderScreen(view) {
    var wrap = viewScreen.parentElement;  // .gscreen-wrap
    var text = (view && view.screen) || "";
    if (text) {
      if (text !== lastScreenText) {
        lastScreenText = text;
        viewScreen.textContent = text;
      }
      wrap.classList.remove("hidden");
    } else {
      lastScreenText = null;
      viewScreen.textContent = "";
      wrap.classList.add("hidden");
    }
    agentView.classList.remove("hidden");  // reveal even if /conversation fails
  }

  /* ---------------- glance turn list (from /conversation) ---------------- */

  var glanceNeedsWipe = true;

  function buildGlanceTurn(role, text) {
    var el = document.createElement("div");
    el.className = "gturn " + role;
    el.setAttribute("data-role", role);
    var roleEl = document.createElement("span");
    roleEl.className = "gt-role";
    roleEl.textContent = role === "user" ? "tú" : "agente";
    var textEl = document.createElement("span");
    textEl.className = "gt-text";
    textEl.textContent = text;
    /* Full-width <button aria-expanded> row — never an inline span. */
    var moreBtn = document.createElement("button");
    moreBtn.type = "button";
    moreBtn.className = "ver-mas";
    moreBtn.setAttribute("aria-expanded", "false");
    moreBtn.textContent = "ver más";
    moreBtn.addEventListener("click", function (event) {
      event.stopPropagation();
      var expanded = el.classList.toggle("expanded");
      moreBtn.setAttribute("aria-expanded", expanded ? "true" : "false");
      moreBtn.textContent = expanded ? "ver menos" : "ver más";
    });
    el.appendChild(roleEl);
    el.appendChild(textEl);
    el.appendChild(moreBtn);
    return el;
  }

  function measureVerMas(el, text) {
    if (el.classList.contains("expanded")) {
      el.classList.add("has-more");  // it was expandable before: keep it
      return;
    }
    var textEl = el.querySelector(".gt-text");
    var overflow = textEl.scrollHeight > textEl.clientHeight + 2 ||
      text.length > 260 || text.split("\n").length >= 5;
    el.classList.toggle("has-more", overflow);
  }

  function renderGlanceLabel() {
    var paneId = lastEffectivePane || selectedPane;
    var name = "";
    for (var i = 0; i < lastHerd.length; i++) {
      if (lastHerd[i].pane_id === paneId) {
        var a = lastHerd[i];
        name = (a.cwd || "").split("/").filter(Boolean).pop() || a.title || a.agent || "";
        break;
      }
    }
    var label = "Vista del agente" + (name ? " · " + name : "");
    if (glanceLabel.textContent !== label) glanceLabel.textContent = label;
  }

  /* Keyed in-place turn list (FR10/AC7/AC8): turns are reused by content
   * key, expansion state and scroll survive the 5s poll, and elements are
   * deleted only when their content really disappeared. */
  function renderGlance(data) {
    var turns = (data && data.turns) || [];
    var scroll = glanceTurns;
    var beforeTop = scroll.scrollTop;
    var beforeHeight = scroll.scrollHeight;
    var atBottom = beforeTop + scroll.clientHeight >= beforeHeight - 4;

    var existing = [];
    var i, el;
    for (i = 0; i < scroll.children.length; i++) {
      el = scroll.children[i];
      if (el.classList.contains("gturn")) existing.push(el);
    }
    if (glanceNeedsWipe) {
      for (i = 0; i < existing.length; i++) existing[i].remove();
      existing = [];
      glanceNeedsWipe = false;
    }

    var free = {};
    for (i = 0; i < existing.length; i++) {
      var k = existing[i].getAttribute("data-key");
      (free[k] || (free[k] = [])).push(existing[i]);
    }
    var wanted = [];
    for (i = 0; i < turns.length; i++) {
      var t = turns[i];
      var key = t.role + "\u0000" + t.text;
      var pool = free[key];
      if (pool && pool.length) {
        el = pool.shift();
      } else {
        el = buildGlanceTurn(t.role, t.text);
        /* Expansion carry-over (AC7): if the turn that used to sit at
         * this position was expanded, the rebuilt one stays expanded. */
        var oldHere = existing[i];
        if (oldHere && oldHere.classList.contains("expanded") &&
            oldHere.getAttribute("data-role") === t.role) {
          el.classList.add("expanded");
          var btn = el.querySelector(".ver-mas");
          btn.setAttribute("aria-expanded", "true");
          btn.textContent = "ver menos";
        }
        measureVerMas(el, t.text);
      }
      if (el.getAttribute("data-key") !== key) el.setAttribute("data-key", key);
      wanted.push(el);
    }
    var kept = {};
    for (i = 0; i < wanted.length; i++) kept[wanted[i]] = true;
    for (i = 0; i < existing.length; i++) {
      if (!kept[existing[i]]) existing[i].remove();
    }
    var sameOrder = wanted.length === scroll.children.length;
    for (i = 0; sameOrder && i < wanted.length; i++) {
      if (scroll.children[i] !== wanted[i]) sameOrder = false;
    }
    if (!sameOrder) {
      for (i = 0; i < wanted.length; i++) scroll.appendChild(wanted[i]);
    }

    /* Replay on the newest agent turn: the glance already carries the
     * full /conversation text, so no fetch is needed. */
    var lastAgent = null;
    for (i = wanted.length - 1; i >= 0; i--) {
      if (wanted[i].getAttribute("data-role") !== "user") { lastAgent = wanted[i]; break; }
    }
    var replay = scroll.querySelector(".replay-btn");
    if (lastAgent && (!replay || replay.parentNode !== lastAgent)) {
      attachReplay(scroll, lastAgent, (function (node) {
        return function () { return node.querySelector(".gt-text").textContent; };
      })(lastAgent), "agente");
    }

    if (!turns.length && !lastScreenText) glanceEmpty.classList.remove("hidden");
    else glanceEmpty.classList.add("hidden");
    agentView.classList.remove("hidden");

    /* Scroll preservation (AC7): pinned-at-bottom stays pinned; content
     * removed above the viewport shifts scrollTop by the height delta. */
    var afterHeight = scroll.scrollHeight;
    if (atBottom) scroll.scrollTop = scroll.scrollHeight;
    else if (afterHeight !== beforeHeight) {
      scroll.scrollTop = Math.max(0, beforeTop + (afterHeight - beforeHeight));
    }
  }

  /* ---------------- status / view polling ---------------- */

  function renderView(view) {
    if (!view) return;
    safeRender("status", renderStatus, view.status);
    safeRender("pending", renderPending, view.pending);
    safeRender("screen", renderScreen, view);
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

  function refreshConversation() {
    var qs = selectedPane ? "?pane_id=" + encodeURIComponent(selectedPane) : "";
    fetch("/conversation" + qs)
      .then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(function (data) {
        renderGlanceLabel();
        safeRender("glance", renderGlance, data);
        markPollOk();
      })
      .catch(function (err) {
        markPollError("conversación", err);
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
    refreshConversation();
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
      /* FR13: distinct anuncio bubble in the call + toast above drawer. */
      if (inCall) addAnnouncementTurn(item.announcement.label, item.announcement.text);
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

  /* ---------------- state pill (rows 8–13, 17) ----------------
   * Tap = REOPEN the drawer, always — never state-dependent (FR12).
   * Barge-in stays on the footer ⏹. The old pill long-press/parse is
   * gone: diagnostics moved to an idle-only long-press on 📞. */

  statePill.addEventListener("click", function () {
    if (interruptedFromReload && !inCall) {
      /* Reload boot pill (FR15/AC13): tap = NEW call, never auto-resume. */
      interruptedFromReload = false;
      startCall();
      return;
    }
    if (inCall && !drawerOpen) openDrawer();
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
      "drawer abierto:   " + drawerOpen,
      "listening:        " + listening,
      "dispatching:      " + dispatching,
      "textMode:         " + textMode,
      "escuchando desde: " + (listeningStartedAt ? new Date(listeningStartedAt).toLocaleTimeString() : "—"),
      "ruido de sala:    " + (vad ? vad.floor().toFixed(4) : "—"),
      "errores recon.:   " + recFailures + " (último: " + lastRecError + ")",
      "preguntas /ask:   " + askCount,
      "último envío:     " + lastDispatch,
      "committed chars:  " + (snap ? snap.committedChars : 0),
      "interim chars:    " + (snap ? snap.interimChars : 0),
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

  /* Diagnostics entry point (FR12): idle-only long-press (>= 800ms) on
   * the call button; NEVER fires during a call. */
  var pressTimer = null;
  var longPressFired = false;

  callBtn.addEventListener("pointerdown", function () {
    if (inCall) return;  // idle only: a call in progress disables it
    longPressFired = false;
    pressTimer = setTimeout(function () {
      longPressFired = true;
      openDiag();
    }, 800);
  });
  ["pointerup", "pointercancel", "pointerleave"].forEach(function (ev) {
    callBtn.addEventListener(ev, function () {
      if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; }
    });
  });

  /* ---------------- toast ---------------- */

  var toastTimer = null;

  function showToast(text, durationMs, kind) {
    toastEl.textContent = text;
    toastEl.classList.toggle("warning", kind === "warning");
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
      if (ann && ann.type === "system") {
        // System warnings (e.g. herdr-tts daemon down): the toast always
        // shows — even muted, this is the surviving channel speaking.
        var isWarning = ann.kind === "warning";
        showToast((isWarning ? "⚠️ " : "✅ ") + ann.text, isWarning ? 8000 : 5000,
          isWarning ? "warning" : null);
        if (!muted() && ann.audio_url) enqueueAudio(ann.audio_url, ann);
        return;
      }
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
        interimTextEl.textContent = "";
        renderInterimStrip();
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
    interimTextEl.textContent = "";
    teardownRecognition();
    // ask() owns the transition to "thinking" — setting it here would trip
    // ask()'s re-entrancy guard (callState === "thinking") and silently
    // swallow the dispatch (the 'never dispatched' half of the voice bug).
    // afterAnswer() inside ask's chain cannot restart the mic either (it
    // runs while dispatching is still true, so startBrowserListening's
    // guard blocks it): re-evaluate the resume once the ask is fully done
    // and dispatching is cleared.
    ask(text).then(function () {
      dispatching = false;
      afterAnswer();
    });
  }

  function startBrowserListening() {
    if (textMode || !SR || callState !== "listening" || listening || dispatching) return;
    manualStop = false;
    hideBanner();
    listeningStartedAt = Date.now();
    // The endpointer SURVIVES recognition session restarts (Chrome ends
    // sessions every few seconds; committed text must carry over). Only a
    // dispatch (finalize), hang-up or new conversation resets it.
    if (!endpointer) endpointer = window.Endpointing.createEndpointer();
    lastInterimRaw = endpointer.text();
    interimTextEl.textContent = lastInterimRaw.length > 120
      ? "…" + lastInterimRaw.slice(-120)
      : (lastInterimRaw || "…");

    recognition = new SR();
    recognition.lang = navigator.language || "es-ES";
    recognition.interimResults = true;
    recognition.continuous = true;

    recognition.onresult = function (event) {
      if (!endpointer) return;
      var interim = "";
      var finals = "";
      for (var i = event.resultIndex; i < event.results.length; i++) {
        var transcript = event.results[i][0].transcript || "";
        if (event.results[i].isFinal) finals += (finals ? " " : "") + transcript;
        else interim += transcript;
      }
      if (finals) {
        // Finalized chunk (typically flushed when a session ends): COMMIT,
        // never dispatch — Chrome finalizes per phrase, not per utterance;
        // the silence/cap endpointing decides when the utterance is done.
        endpointer.commit(finals);
        recRestartDelay = 300;
        recFailures = 0;
      }
      if (interim) endpointer.push(interim);
      lastInterimRaw = (finals ? finals + (interim ? " " : "") : "") + interim;
      pillSub.classList.add("hidden");
      // tail truncation: keep the newest words visible
      var text = endpointer.text();
      interimTextEl.textContent = text.length > 120 ? "…" + text.slice(-120) : (text || "…");
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
      } else if (interimTextEl.textContent === "…") {
        interimTextEl.textContent = "";
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
    * The drawer strip shows a live level meter from the SAME RMS frames
    * the VAD consumes (FR8) — never simulated text.
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

  function updateRecMeter(rms) {
    if (!vad) return;
    var floor = vad.floor();
    var ratio = Math.min(1, Math.max(0, (rms - floor) / 0.06));
    var lit = Math.round(ratio * METER_CELLS);
    if (lit === meterLit) return;  // paint only on change
    meterLit = lit;
    var cells = meterCellsEl.children;
    for (var i = 0; i < cells.length; i++) {
      cells[i].className = i < lit ? "lit" : "";
    }
  }

  function updateRecElapsed() {
    if (!utteranceStartedAt) return;
    var s = Math.floor((Date.now() - utteranceStartedAt) / 1000);
    if (s === lastElapsedShown) return;
    lastElapsedShown = s;
    recElapsedEl.textContent = Math.floor(s / 60) + ":" + (s % 60 < 10 ? "0" : "") + (s % 60);
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
            showBanner("No entendí el audio — inténtalo otra vez.");
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
        interimTextEl.textContent = "";
        renderInterimStrip();
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
        var rms = frameRms();
        var speaking = vad.push(rms);
        updateRecMeter(rms);  // FR8: level meter from the live VAD RMS
        if (callState === "listening") {
          if (speaking || vad.hasSpeech()) {
            if (!utteranceStartedAt) utteranceStartedAt = Date.now();
            setCallState("recording");
          }
        } else if (callState === "recording") {
          updateRecElapsed();  // FR8: elapsed utterance time
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
    // The "No te oigo" 5s hint must judge the CURRENT listening window:
    // refresh the timestamp on every start attempt (a failed dispatch can
    // resume through here while the old window's timestamp lingers).
    listeningStartedAt = Date.now();
    if (voiceEngine === "servidor") startServerListening();
    else startBrowserListening();
  }

  function stopListening() {
    if (voiceEngine === "servidor") stopServerListening();
    else stopBrowserListening();
  }

  /* ---------------- call button / pause / voice engine toggle ------------- */

  function startCall() {
    interruptedFromReload = false;
    clearCallIntent();
    inCall = true;
    callStartedAt = Date.now();
    markCallIntent();
    startCallTimer();
    requestWakeLock();
    openEvents();  // user gesture: unlocks autoplay for announcements
    setCallState("listening");
    startListening();
    openDrawer();  // AC1: the drawer auto-opens on call start
  }

  function endCall() {
    inCall = false;
    closeDrawer();  // AC4: drawer closes on hang up
    stopListening();
    teardownServerCall();  // release the getUserMedia stream + AudioContext
    if (endpointer) endpointer.reset();  // no stale partial in the next call
    stopAudio();
    hideToast();
    stopCallTimer();
    releaseWakeLock();
    clearCallIntent();
    resetInterimContent();
    setCallState("idle");  // pill disappears; idle is pill-free (FR12)
  }

  callBtn.addEventListener("click", function () {
    if (longPressFired) { longPressFired = false; return; }  // diag opened: swallow
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
    renderInterimStrip();
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
    resetInterimContent();
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
    if (endpointer) endpointer.reset();  // fresh conversation, fresh utterance
    conv.textContent = "";
    resetInterimContent();
    updateGhost();
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

  /* ---------------- boot ---------------- */

  function buildMeterCells() {
    for (var i = 0; i < METER_CELLS; i++) {
      meterCellsEl.appendChild(document.createElement("i"));
    }
  }

  initSpeech();
  renderDiag();
  buildMeterCells();
  updateGhost();
  refreshState();
  setInterval(refreshState, 5000);

  /* FR15/AC13: after a mid-call reload the app boots IDLE with a
   * "call was cut" pill; the mic never resumes without a tap. */
  try {
    if (sessionStorage.getItem(CALL_INTENT_KEY) === "1") {
      interruptedFromReload = true;
      renderPill();
    }
  } catch (err) { /* private mode: normal idle boot */ }

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(function () { /* best effort */ });
  }
})();
