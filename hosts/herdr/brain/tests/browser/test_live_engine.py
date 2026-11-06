"""Browser tests for Dictado: En vivo engine and fallback (PRD-03 Task T1)."""

import json
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from conftest import KaraokeHarness


def test_live_engine_cycles_and_persists(karaoke_harness):
    """S2/D15: Third engine option 'Dictado: En vivo' cycles and persists in localStorage."""
    page = karaoke_harness.page
    btn = page.locator("#voice-engine-btn")

    # Initial default is servidor
    assert "Servidor" in (btn.text_content() or "")

    # Click 1: servidor -> navegador
    page.evaluate("() => document.getElementById('voice-engine-btn').click()")
    assert "Navegador" in (btn.text_content() or "")
    assert page.evaluate("() => localStorage.getItem('herdr-brain-voice-engine')") == "navegador"

    # Click 2: navegador -> live
    page.evaluate("() => document.getElementById('voice-engine-btn').click()")
    assert "En vivo" in (btn.text_content() or "")
    assert page.evaluate("() => localStorage.getItem('herdr-brain-voice-engine')") == "live"

    # Reload page: live engine selection persists
    page.reload()
    btn = page.locator("#voice-engine-btn")
    assert "En vivo" in (btn.text_content() or "")
    assert page.evaluate("() => localStorage.getItem('herdr-brain-voice-engine')") == "live"

    # Click 3: live -> servidor
    page.evaluate("() => document.getElementById('voice-engine-btn').click()")
    assert "Servidor" in (btn.text_content() or "")
    assert page.evaluate("() => localStorage.getItem('herdr-brain-voice-engine')") == "servidor"


def test_socket_failure_falls_back_to_servidor_with_banner(karaoke_app):
    """S3/D1: Socket failure or drop falls back automatically to Servidor with visible banner."""
    harness = karaoke_app()
    harness.expect_blocked("ws://127.0.0.1:41731/call/stream", method="WEBSOCKET")
    harness.context.add_init_script("""
        localStorage.setItem('herdr-brain-voice-engine', 'live');
        const _origDevices = navigator.mediaDevices || {};
        Object.defineProperty(_origDevices, 'getUserMedia', {
            configurable: true,
            value: () => {
                const ctx = new (window.AudioContext || window.webkitAudioContext)();
                const dst = ctx.createMediaStreamDestination();
                return Promise.resolve(dst.stream);
            }
        });
        if (!navigator.mediaDevices) {
            Object.defineProperty(navigator, 'mediaDevices', { value: _origDevices });
        }
    """)
    page = harness.load().page

    btn = page.locator("#voice-engine-btn")
    assert "En vivo" in (btn.text_content() or "")

    # Start call: socket connection will fail on synthetic port 41731
    call_btn = page.locator("#call-btn")
    call_btn.click()

    # Banner must become visible showing fallback notice
    banner = page.locator("#banner")
    expect(banner).to_be_visible(timeout=5000)
    banner_text = banner.text_content() or ""
    assert "servidor" in banner_text.lower() or "vivo" in banner_text.lower()

    # Call is NOT dropped: call button remains on call
    assert page.locator("#call-btn").evaluate("btn => btn.classList.contains('oncall')")


# ---- correction batch (verifier blockers B1/B2/B4, advisories A1/A2) ----
#
# The fakes mirror the verifier's scratch probes: a controllable WebSocket
# double and an oscillator-backed getUserMedia so the live VAD hears real
# frames and the Servidor fallback can arm a real MediaRecorder.

LIVE_FAKE = r"""
(() => { window.__gumCount = 0; window.__mr = 0;
  try { const R = window.MediaRecorder; class MR extends R { constructor(...a){ super(...a); window.__mr++; } } window.MediaRecorder = MR; } catch(e) {}
  window.__sent = []; window.__wsList = [];
  const md = navigator.mediaDevices;
  Object.defineProperty(md, "getUserMedia", {configurable: true, value: async function () {
    window.__gumCount++;
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator(); const gain = ctx.createGain(); gain.gain.value = 0.7;
    const dst = ctx.createMediaStreamDestination(); osc.connect(gain); gain.connect(dst); osc.start();
    window.__stopTone = function () { gain.gain.value = 0.0; };
    return dst.stream;
  }});
  class FakeWS {
    constructor(url){this.url=url;this.readyState=0;this.sent=[];this.bufferedAmount=0;
      window.__fakeWs=this;window.__wsList.push(this);
      setTimeout(()=>{if(this.readyState===0){this.readyState=1;this.onopen&&this.onopen();}},30);}
    send(d){this.sent.push(d);window.__sent.push(typeof d==="string"?d:"binary");}
    close(){this.readyState=3;}
    drop(){this.readyState=3;if(this.onclose)this.onclose({});}
    emit(data){if(this.onmessage)this.onmessage({data: JSON.stringify(data)});}
  }
  FakeWS.OPEN=1; window.WebSocket=FakeWS;
})();
"""

GUM_FAKE = r"""
(() => {
  const md = navigator.mediaDevices;
  Object.defineProperty(md, "getUserMedia", {configurable: true, value: async function () {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const dst = ctx.createMediaStreamDestination();
    return dst.stream;
  }});
})();
"""

# WebSocket double only. The real emulate-media fake-device stream stays in
# place (audio_capture_context launches Chromium with fake-capture flags).
WS_ONLY_FAKE = r"""
(() => { window.__sent = []; window.__wsList = [];
  class FakeWS {
    constructor(url){this.url=url;this.readyState=0;this.sent=[];this.bufferedAmount=0;
      window.__fakeWs=this;window.__wsList.push(this);
      setTimeout(()=>{if(this.readyState===0){this.readyState=1;this.onopen&&this.onopen();}},30);}
    send(d){this.sent.push(d);window.__sent.push(typeof d==="string"?d:"binary");}
    close(){this.readyState=3;}
    drop(){this.readyState=3;if(this.onclose)this.onclose({});}
    emit(data){if(this.onmessage)this.onmessage({data: JSON.stringify(data)});}
  }
  FakeWS.OPEN=1; window.WebSocket=FakeWS;
})();
"""

# A worklet whose port never posts a frame: it loads and connects, but the
# capture pipeline stays silent — the exact class of defect that produced
# 3,200 bytes over a 10 s Android call. The health check must re-arm the
# ScriptProcessor path and record why.
WORKLET_STUCK_FAKE = r"""
(() => {
  const origConnect = AudioNode.prototype.connect;
  const origDisconnect = AudioNode.prototype.disconnect;
  class StuckWorklet {
    constructor() { this.port = { onmessage: null }; }
    connect(dest) { return dest; }
    disconnect() {}
  }
  AudioNode.prototype.connect = function (dest) {
    if (dest instanceof StuckWorklet) return dest;
    return origConnect.apply(this, arguments);
  };
  AudioNode.prototype.disconnect = function () {
    if (this instanceof StuckWorklet) return;
    return origDisconnect.apply(this, arguments);
  };
  window.AudioWorkletNode = StuckWorklet;
  const origAdd = AudioWorklet.prototype.addModule;
  AudioWorklet.prototype.addModule = async function () { return; };
})();
"""

# Force every AudioContext to report a suspended state even after resume()
# resolves: Android's "the graph never actually runs" case.
SUSPENDED_CONTEXT_FAKE = r"""
(() => {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  Object.defineProperty(Ctx.prototype, "state", {
    configurable: true,
    get() { return "suspended"; }
  });
  Ctx.prototype.resume = function () { return Promise.resolve(); };
})();
"""


def _live_boot(karaoke_app, fake=LIVE_FAKE):
    """Fresh harness with the live engine persisted and the fakes installed."""
    harness = karaoke_app()
    harness.context.add_init_script(fake)
    page = harness.load().page
    page.evaluate("() => localStorage.setItem('herdr-brain-voice-engine','live')")
    page.reload()
    page.wait_for_timeout(800)
    return harness, page


def _drive_to_transcribing(page):
    """Speak (oscillator), go silent, and wait until the transcribe request left."""
    page.wait_for_function("() => window.__fakeWs && window.__fakeWs.readyState === 1", timeout=5000)
    page.wait_for_timeout(1500)
    _tone_off(page)
    page.wait_for_function(
        "() => window.__sent.some(s => typeof s === 'string' && s.indexOf('transcribe') >= 0)",
        timeout=25000,
    )


def test_drop_while_transcribing_arms_servidor_and_notifies(karaoke_app):
    """B1: a mid-transcription socket drop must arm the Servidor recorder and
    surface the lost phrase — never leave a live call that hears nothing."""
    harness, page = _live_boot(karaoke_app)
    page.locator("#call-btn").click()
    _drive_to_transcribing(page)

    mr_before = page.evaluate("() => window.__mr")
    page.evaluate("() => window.__fakeWs.drop()")
    page.wait_for_timeout(1000)

    # Engine falls back...
    assert "Servidor" in (page.locator("#voice-engine-btn").text_content() or "")
    # ...the Servidor recorder is actually armed despite the in-flight dispatch
    assert page.evaluate("() => window.__mr") >= mr_before + 1, \
        "Servidor recorder was not armed after the mid-transcription drop"
    # The in-flight utterance is surfaced, never silently lost
    banner = page.locator("#banner")
    expect(banner).to_be_visible()
    assert "frase" in (banner.text_content() or "")
    # The call itself survives
    assert page.locator("#call-btn").evaluate("b => b.classList.contains('oncall')")


def test_second_call_socket_drop_still_falls_back(karaoke_app):
    """B2: manualStop from a previous call's teardown must not suppress the
    fallback on later calls — call-scoped live state resets at call start."""
    harness, page = _live_boot(karaoke_app)
    # Call 1: connect, then hang up (stopLiveListening sets manualStop).
    page.locator("#call-btn").click()
    page.wait_for_function("() => window.__fakeWs && window.__fakeWs.readyState === 1", timeout=5000)
    page.wait_for_timeout(500)
    page.locator("#call-btn").click()  # hang up
    page.wait_for_timeout(500)

    # Call 2: a socket drop must STILL fall back to Servidor.
    page.locator("#call-btn").click()
    page.wait_for_function(
        "() => window.__wsList.length >= 2 && window.__wsList[1].readyState === 1", timeout=5000
    )
    page.evaluate("() => window.__wsList[1].drop()")
    page.wait_for_timeout(1500)

    assert "Servidor" in (page.locator("#voice-engine-btn").text_content() or ""), \
        "Second call's socket drop did not fall back to Servidor"
    expect(page.locator("#banner")).to_be_visible()
    assert page.locator("#call-btn").evaluate("b => b.classList.contains('oncall')")


@pytest.mark.parametrize("mode", ["http503", "network"], ids=["non-2xx", "network-error"])
def test_live_engine_refuses_start_when_brain_unreachable(karaoke_app, mode):
    """B4/S3/D1: with the live engine, an unreachable brain (network error or
    non-2xx on POST /call/start) refuses to start the call with a notice."""
    harness = karaoke_app()
    harness.context.add_init_script(GUM_FAKE)
    if mode == "http503":
        harness.api[("POST", "/call/start")] = ({"detail": "brain down"}, 503, set())
    else:
        harness.context.route(
            harness.origin + "/call/start",
            lambda route: route.abort("connectionrefused"),
        )
    page = harness.load().page
    page.evaluate("() => localStorage.setItem('herdr-brain-voice-engine','live')")
    page.reload()
    page.wait_for_timeout(800)

    page.locator("#call-btn").click()
    page.wait_for_timeout(1000)

    # The call NEVER starts when the brain cannot even open it.
    assert not page.locator("#call-btn").evaluate("b => b.classList.contains('oncall')"), \
        "Live call started although POST /call/start failed"
    banner = page.locator("#banner")
    expect(banner).to_be_visible()
    assert "brain" in (banner.text_content() or "").lower()
    # The engine selection itself is untouched.
    assert "En vivo" in (page.locator("#voice-engine-btn").text_content() or "")


def test_error_message_unsticks_transcribing_and_falls_back(karaoke_app):
    """A2: a server {"type":"error"} while transcribing must not leave the call
    stuck: notice + Servidor fallback exactly like a socket failure."""
    harness, page = _live_boot(karaoke_app)
    page.locator("#call-btn").click()
    _drive_to_transcribing(page)

    mr_before = page.evaluate("() => window.__mr")
    page.evaluate("() => window.__fakeWs.emit({type: 'error', message: 'engine failed closed'})")
    page.wait_for_timeout(1000)

    assert "Servidor" in (page.locator("#voice-engine-btn").text_content() or ""), \
        "Server error message did not trigger the Servidor fallback"
    assert page.evaluate("() => window.__mr") >= mr_before + 1, \
        "Call stayed stuck in transcribing after the server error"
    expect(page.locator("#banner")).to_be_visible()
    assert page.locator("#call-btn").evaluate("b => b.classList.contains('oncall')")


def test_error_message_while_listening_shows_error_notice(karaoke_app):
    """A2: a server error outside a dispatch surfaces the engine-failure notice
    and still falls back (the socket itself may be perfectly healthy)."""
    harness, page = _live_boot(karaoke_app)
    page.locator("#call-btn").click()
    page.wait_for_function("() => window.__fakeWs && window.__fakeWs.readyState === 1", timeout=5000)
    page.wait_for_timeout(800)

    page.evaluate("() => window.__fakeWs.emit({type: 'error', message: 'engine failed closed'})")
    page.wait_for_timeout(1000)

    assert "Servidor" in (page.locator("#voice-engine-btn").text_content() or "")
    banner = page.locator("#banner")
    expect(banner).to_be_visible()
    assert "falló" in (banner.text_content() or "")
    assert page.locator("#call-btn").evaluate("b => b.classList.contains('oncall')")


def test_open_socket_backpressure_routes_through_bounded_buffer(karaoke_app):
    """A1/FR-03: while the socket is OPEN, a bufferedAmount above the bound
    stops unbounded ws.send; when the socket drains, sending resumes."""
    harness, page = _live_boot(karaoke_app)
    page.locator("#call-btn").click()
    page.wait_for_function("() => window.__fakeWs && window.__fakeWs.readyState === 1", timeout=5000)
    page.wait_for_function(
        "() => window.__sent.filter(s => s === 'binary').length >= 5", timeout=10000
    )
    binary_count = "() => window.__sent.filter(s => s === 'binary').length"

    # Saturate the socket: frames above the bound route through the bounded
    # buffer (drop oldest, keep ~2 s), never an unbounded ws.send.
    page.evaluate("() => { window.__fakeWs.bufferedAmount = 16 * 1024 * 1024; }")
    page.wait_for_timeout(400)  # let any in-pipeline frame land
    s1 = page.evaluate(binary_count)
    page.wait_for_timeout(900)
    s2 = page.evaluate(binary_count)
    assert s2 == s1, f"sends continued under backpressure: {s1} -> {s2}"

    # The socket drains: queued (bounded) frames flush and direct sends resume.
    page.evaluate("() => { window.__fakeWs.bufferedAmount = 0; }")
    page.wait_for_timeout(900)
    s3 = page.evaluate(binary_count)
    assert s3 > s2, "sending did not resume after the socket drained"


@pytest.mark.parametrize("engine", ["servidor", "navegador"], ids=["servidor", "navegador"])
def test_non_live_engines_start_best_effort_when_call_start_fails(karaoke_app, engine):
    """Preservation (S2): Servidor and Navegador keep today's best-effort call
    start EXACTLY — a failing /call/start never blocks their calls."""
    harness = karaoke_app()
    harness.context.add_init_script(GUM_FAKE)
    harness.api[("POST", "/call/start")] = ({"detail": "down"}, 503, set())
    page = harness.load().page
    page.evaluate("(engine) => localStorage.setItem('herdr-brain-voice-engine', engine)", engine)
    page.reload()
    page.wait_for_timeout(800)

    page.locator("#call-btn").click()
    page.wait_for_timeout(1000)

    assert page.locator("#call-btn").evaluate("b => b.classList.contains('oncall')"), \
        f"{engine} call did not start best-effort despite /call/start failing"
    banner_text = page.locator("#banner").text_content() or ""
    assert "brain" not in banner_text.lower(), "live-refusal notice leaked to a non-live engine"


# ---- L11 correction: duplicate live-call start ----
#
# The live start probe already opens (or resumes) the per-call record;
# beginCall must never POST it a second time. Every extra POST leaves an
# orphan record the next brain restart closes as "interrupted" (L10).

RESUME_CALL = {
    "call_id": "call-live-1",
    "started_at": "2026-11-03T10:00:00Z",
    "ended_at": "2026-11-03T10:02:30Z",
    "duration_s": 150.0,
    "turn_count": 2,
    "status": "ended",
}


def _posts(harness, path):
    """Exact-path POST count: /call/start must never match /calls/... paths."""
    return [
        r for r in harness.requests
        if r.method == "POST" and urlsplit(r.url).path == path
    ]


def _live_resume_boot(karaoke_app, resume_status=200):
    """Live harness whose boot-mounted Llamadas UI shows one resumable call."""
    harness = karaoke_app()
    harness.context.add_init_script(LIVE_FAKE)
    harness.respond(
        "/calls", {"calls": [RESUME_CALL], "has_more": False}, query=("before", "limit")
    )
    harness.respond(
        "/calls/call-live-1",
        {"call": RESUME_CALL, "turns": [], "has_more": False},
        query=("before", "limit"),
    )
    harness.respond(
        "/calls/call-live-1/resume",
        {"call_id": "call-live-1"}, method="POST", status=resume_status,
    )
    page = harness.load().page
    page.evaluate("() => localStorage.setItem('herdr-brain-voice-engine','live')")
    page.reload()
    page.wait_for_timeout(800)
    # Reveal the boot-mounted Llamadas UI: the section is hidden and the
    # drawer closed until a call starts — the calls module is already live.
    page.evaluate("""
        () => {
            const drawer = document.getElementById("call-drawer");
            if (drawer) { drawer.classList.add("open"); drawer.removeAttribute("inert"); }
            const section = document.getElementById("calls-section");
            if (section) section.classList.remove("hidden");
        }
    """)
    return harness, page


def _resume_via_calls_ui(page):
    """Drive the real UI path: list item -> detail -> resume button."""
    page.locator("#calls-container .call-item").first.click()
    resume_btn = page.locator("#calls-container .calls-resume-btn")
    expect(resume_btn).to_be_visible(timeout=5000)
    resume_btn.click()
    page.wait_for_function("() => window.__fakeWs && window.__fakeWs.readyState === 1", timeout=5000)
    page.wait_for_timeout(500)  # let any stray trailing POST land before counting


def test_live_fresh_call_opens_exactly_one_call_record(karaoke_app):
    """L11: for a fresh live call the probe is the ONLY /call/start — the
    call starts exactly once, with no second record left behind."""
    harness, page = _live_boot(karaoke_app)
    page.locator("#call-btn").click()
    page.wait_for_function("() => window.__fakeWs && window.__fakeWs.readyState === 1", timeout=5000)
    page.wait_for_timeout(500)  # let any stray trailing POST land before counting

    assert len(_posts(harness, "/call/start")) == 1, \
        "fresh live call POSTed /call/start more than once"
    assert page.locator("#call-btn").evaluate("b => b.classList.contains('oncall')"), \
        "the live call did not start"
    assert page.evaluate("() => window.__wsList.length") == 1, \
        "fresh live call opened more than one stream socket"


# The oscillator is SILENT: with no speech the live call stays in
# "listening", which is exactly the state the 1s mic watchdog scans —
# the deterministic twin of the loud-mic race the L11 spot check hit.
SILENT_LIVE_FAKE = r"""
(() => { window.__gumCount = 0; window.__mr = 0;
  window.__sent = []; window.__wsList = [];
  const md = navigator.mediaDevices;
  Object.defineProperty(md, "getUserMedia", {configurable: true, value: async function () {
    window.__gumCount++;
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator(); const gain = ctx.createGain(); gain.gain.value = 0.0;
    const dst = ctx.createMediaStreamDestination(); osc.connect(gain); gain.connect(dst); osc.start();
    return dst.stream;
  }});
  class FakeWS {
    constructor(url){this.url=url;this.readyState=0;this.sent=[];this.bufferedAmount=0;
      window.__fakeWs=this;window.__wsList.push(this);
      setTimeout(()=>{if(this.readyState===0){this.readyState=1;this.onopen&&this.onopen();}},30);}
    send(d){this.sent.push(d);window.__sent.push(typeof d==="string"?d:"binary");}
    close(){this.readyState=3;}
    drop(){this.readyState=3;if(this.onclose)this.onclose({});}
    emit(data){if(this.onmessage)this.onmessage({data: JSON.stringify(data)});}
  }
  FakeWS.OPEN=1; window.WebSocket=FakeWS;
})();
"""


def test_live_idle_listening_never_stacks_a_second_socket(karaoke_app):
    """Watchdog (parent spot check): the 1s mic re-arm is navegador-only.
    An idle silent live call stays in "listening" across watchdog ticks —
    it must keep exactly ONE stream socket. The live engine never sets
    the v1 `listening` flag, so an unrestricted watchdog re-arms it
    mid-call, stacking a second /call/stream socket and leaking the
    first (D9: one streaming connection per call)."""
    harness = karaoke_app()
    harness.context.add_init_script(SILENT_LIVE_FAKE)
    page = harness.load().page
    page.evaluate("() => localStorage.setItem('herdr-brain-voice-engine','live')")
    page.reload()
    page.wait_for_timeout(800)

    page.locator("#call-btn").click()
    page.wait_for_function("() => window.__fakeWs && window.__fakeWs.readyState === 1", timeout=5000)
    # Cross at least two 1s watchdog ticks while idle-listening.
    page.wait_for_timeout(2500)

    assert page.evaluate("() => window.__wsList.length") == 1, \
        "watchdog re-armed the live engine and stacked a second stream socket"
    assert page.locator("#call-btn").evaluate("b => b.classList.contains('oncall')"), \
        "the idle live call did not survive"
    assert len(_posts(harness, "/call/start")) == 1, \
        "idle live call POSTed /call/start more than once"


def test_live_resume_reopens_record_exactly_once(karaoke_app):
    """L11: a successful live resume POSTs /calls/{id}/resume exactly once
    and never opens a fresh record on top of it."""
    harness, page = _live_resume_boot(karaoke_app, resume_status=200)
    _resume_via_calls_ui(page)

    assert len(_posts(harness, "/calls/call-live-1/resume")) == 1, \
        "resumed live call POSTed /calls/{id}/resume more than once"
    assert len(_posts(harness, "/call/start")) == 0, \
        "successful resume must not open a fresh call record"
    assert page.locator("#call-btn").evaluate("b => b.classList.contains('oncall')"), \
        "the resumed live call did not start"


def test_live_failed_resume_falls_back_to_one_fresh_start(karaoke_app):
    """L11/S6: a refused resume (non-2xx) falls back to a fresh record —
    exactly one failed resume POST plus exactly one /call/start, and the
    call still starts."""
    harness, page = _live_resume_boot(karaoke_app, resume_status=409)
    _resume_via_calls_ui(page)

    assert len(_posts(harness, "/calls/call-live-1/resume")) == 1, \
        "failed resume was retried more than once"
    assert len(_posts(harness, "/call/start")) == 1, \
        "resume -> fresh fallback must open exactly one fresh record"
    assert page.locator("#call-btn").evaluate("b => b.classList.contains('oncall')"), \
        "the live call did not start after the resume fallback"


@pytest.mark.parametrize("engine", ["servidor", "navegador"], ids=["servidor", "navegador"])
def test_non_live_engines_keep_their_own_single_start_post(karaoke_app, engine):
    """Preservation (S2): Servidor and Navegador never run the live probe —
    beginCall itself still POSTs their fire-and-forget start exactly once."""
    harness = karaoke_app()
    harness.context.add_init_script(GUM_FAKE)
    page = harness.load().page
    page.evaluate("(engine) => localStorage.setItem('herdr-brain-voice-engine', engine)", engine)
    page.reload()
    page.wait_for_timeout(800)

    page.locator("#call-btn").click()
    page.wait_for_timeout(800)

    assert page.locator("#call-btn").evaluate("b => b.classList.contains('oncall')"), \
        f"{engine} call did not start"
    assert len(_posts(harness, "/call/start")) == 1, \
        f"{engine} call must keep its own single fire-and-forget /call/start"


# ---- L13 correction batch: model-degradation notices reach the user ----
#
# Blocker 2 (L13): the socket emits {"type":"notice"} when the live model
# degrades (D13) but the client ignored it. The contract now: a visible,
# human-readable localized banner; the engine stays "En vivo", the socket
# stays open and the call is never interrupted. The reason token is
# untrusted server input — only the two known tokens map to their own
# wording; anything else renders the generic safe notice.


@pytest.mark.parametrize("reason,needle", [
    ("live_stt_fallback_oom", "memoria"),
    ("live_stt_fallback_model_absent", "modelo principal"),
], ids=["oom", "model-absent"])
def test_degradation_notice_is_visible_and_call_continues(karaoke_app, reason, needle):
    """L13/D13: both known degradation notices show a localized banner while
    the live engine, the socket and the call all continue untouched."""
    harness, page = _live_boot(karaoke_app)
    page.locator("#call-btn").click()
    page.wait_for_function("() => window.__fakeWs && window.__fakeWs.readyState === 1", timeout=5000)
    page.wait_for_timeout(300)

    page.evaluate("(r) => window.__fakeWs.emit({type: 'notice', reason: r})", reason)

    banner = page.locator("#banner")
    expect(banner).to_be_visible(timeout=5000)
    text = banner.text_content() or ""
    assert needle in text, f"notice wording missing ({needle!r}): {text}"
    assert "reserva" in text, "human-readable notice wording missing"
    assert reason not in text, "raw reason token rendered verbatim"

    # No engine switch, no call interruption, socket untouched, no
    # Servidor recorder armed (no unintended fallback anywhere).
    assert "En vivo" in (page.locator("#voice-engine-btn").text_content() or "")
    assert page.locator("#call-btn").evaluate("b => b.classList.contains('oncall')"), \
        "the notice interrupted the call"
    assert page.evaluate("() => window.__fakeWs.readyState") == 1, \
        "the notice closed the stream socket"
    assert page.evaluate("() => window.__wsList.length") == 1, \
        "the notice opened another socket"
    assert page.evaluate("() => window.__mr") == 0, \
        "the notice armed a Servidor recorder"


@pytest.mark.parametrize("reason", [
    "<img src=x onerror='window.__pwned=1'>totally-unknown-reason",
    "live_stt_fallback_nonsense",
    None,
], ids=["markup", "unknown-token", "absent"])
def test_unknown_notice_reason_renders_safe_generic_text(karaoke_app, reason):
    """L13: an unknown or absent reason token never renders raw server
    input — the generic localized notice shows instead, and the call
    continues untouched."""
    harness, page = _live_boot(karaoke_app)
    page.locator("#call-btn").click()
    page.wait_for_function("() => window.__fakeWs && window.__fakeWs.readyState === 1", timeout=5000)
    page.wait_for_timeout(300)

    page.evaluate("(r) => window.__fakeWs.emit({type: 'notice', reason: r})", reason)

    banner = page.locator("#banner")
    expect(banner).to_be_visible(timeout=5000)
    text = banner.text_content() or ""
    assert "reserva" in text, "generic localized notice wording missing"
    assert "totally-unknown-reason" not in text, "raw server string rendered"
    assert "live_stt_fallback" not in text, "raw reason token rendered verbatim"
    assert not page.evaluate("() => window.__pwned"), "markup executed"

    assert "En vivo" in (page.locator("#voice-engine-btn").text_content() or "")
    assert page.locator("#call-btn").evaluate("b => b.classList.contains('oncall')"), \
        "the notice interrupted the call"
    assert page.evaluate("() => window.__fakeWs.readyState") == 1, \
        "the notice closed the stream socket"


# ---- T3 (PRD-04/S5): live partials while speaking ----
#
# The FakeWS records every sent string in window.__sent and can emit
# server messages back through emit(); the oscillator drives the live
# VAD onset exactly like a spoken phrase.


def _sent_strings(page):
    return page.evaluate("() => window.__sent.filter(s => typeof s === 'string')")


def test_auth_message_carries_the_app_locale(karaoke_app):
    """D8: the first socket message publishes the app locale so the
    server forces the transcription language to it."""
    harness, page = _live_boot(karaoke_app)
    page.locator("#call-btn").click()
    page.wait_for_function("() => window.__fakeWs && window.__fakeWs.readyState === 1", timeout=5000)
    page.wait_for_function(
        "() => window.__sent.some(s => typeof s === 'string' && s.indexOf('auth') >= 0)",
        timeout=5000,
    )
    auths = [s for s in _sent_strings(page) if '"auth"' in s.replace(" ", "")]
    assert auths, "auth message never sent"
    assert '"locale":"es"' in auths[0].replace(" ", ""), auths[0]


def test_stream_control_rides_onset_and_turn_end_in_order(karaoke_app):
    """PRD-04 protocol: speech onset sends stream start after binary
    frames begin; the silence close sends stream stop IMMEDIATELY BEFORE
    the transcribe command — never after it."""
    harness, page = _live_boot(karaoke_app)
    page.locator("#call-btn").click()
    _drive_to_transcribing(page)

    sent = _sent_strings(page)
    starts = [i for i, s in enumerate(sent) if '"start"' in s.replace(" ", "")]
    stops = [i for i, s in enumerate(sent) if '"stop"' in s.replace(" ", "")]
    transcribes = [i for i, s in enumerate(sent) if '"transcribe"' in s.replace(" ", "")]
    assert starts, "stream start never sent at onset"
    assert stops and transcribes, "turn end did not send stop + transcribe"
    assert stops[0] < transcribes[0], "transcribe left before stream stop"
    assert transcribes[0] - stops[0] == 1, "messages between stop and transcribe"
    # Stream start came after audio frames were already flowing.
    binaries = page.evaluate("() => window.__sent.filter(s => s === 'binary').length")
    assert binaries > 0


def test_partial_text_shows_in_strip_and_never_dispatches(karaoke_app):
    """S5/AC4: a server partial renders in the interim strip while
    speaking; it NEVER dispatches /ask (the strip is display-only)."""
    harness, page = _live_boot(karaoke_app)
    page.locator("#call-btn").click()
    page.wait_for_function(
        "() => window.__sent.some(s => typeof s === 'string' && s.indexOf('start') >= 0)",
        timeout=25000,
    )

    page.evaluate(
        "() => window.__fakeWs.emit({type: 'partial', text: 'hola que'})"
    )
    strip = page.locator("#interim-text")
    expect(strip).to_have_text("hola que", timeout=5000)
    assert not strip.evaluate("el => el.classList.contains('hidden')")

    asks = [r for r in harness.requests if r.method == "POST" and urlsplit(r.url).path == "/ask"]
    assert asks == [], "a partial dispatched /ask"


def test_finals_accumulate_in_strip_and_dispatch_once_via_transcript(karaoke_app):
    """FR-02/FR-04 display contract: finals accumulate in the strip while
    speaking; the turn-end transcript routes the ONE /ask with the whole
    utterance; afterwards the strip clears and a late partial stays
    invisible."""
    harness, page = _live_boot(karaoke_app)
    harness.respond("/ask", {"answer": "Listo.", "audio_url": None}, method="POST")
    page.locator("#call-btn").click()
    # Speak: onset arrives (stream start), the stable prefix renders.
    page.wait_for_function(
        "() => window.__sent.some(s => typeof s === 'string' && s.indexOf('start') >= 0)",
        timeout=25000,
    )
    page.evaluate("() => window.__fakeWs.emit({type: 'final', text: 'hola que'})")
    expect(page.locator("#interim-text")).to_have_text("hola que", timeout=5000)

    # Silence closes the turn; the transcript carries the whole utterance.
    _tone_off(page)
    page.wait_for_function(
        "() => window.__sent.some(s => typeof s === 'string' && s.indexOf('transcribe') >= 0)",
        timeout=25000,
    )
    page.evaluate(
        "() => window.__fakeWs.emit({type: 'transcript', text: 'hola que tal'})"
    )
    # Exactly ONE /ask carries the whole utterance text.
    page.wait_for_function(
        "() => window.__karaokeHarness.fetchCalls.includes('/ask')", timeout=10000
    )
    page.wait_for_timeout(300)
    asks = [r for r in harness.requests if r.method == "POST" and urlsplit(r.url).path == "/ask"]
    assert len(asks) == 1, f"expected exactly one /ask, got {len(asks)}"

    # The strip cleared for the finished turn; a LATE partial on the
    # finished utterance's OWN socket (its handlers are detached once
    # the dispatch tears the mic down) never repaints the strip. The
    # re-armed mic owns a NEW socket — partials there are legitimate.
    assert (page.locator("#interim-text").text_content() or "") == ""
    page.evaluate(
        "() => window.__wsList[0].emit({type: 'partial', text: 'fantasma'})"
    )
    page.wait_for_timeout(400)
    assert (page.locator("#interim-text").text_content() or "") == "", \
        "a late partial repainted the strip after the transcript"


def test_final_message_alone_never_dispatches(karaoke_app):
    """AC4 pin: a final (stable prefix) message is display-only — by
    itself it must never reach routeUtterance or /ask."""
    harness, page = _live_boot(karaoke_app)
    page.locator("#call-btn").click()
    page.wait_for_function(
        "() => window.__sent.some(s => typeof s === 'string' && s.indexOf('start') >= 0)",
        timeout=25000,
    )

    page.evaluate("() => window.__fakeWs.emit({type: 'final', text: 'solo texto estable'})")
    page.wait_for_timeout(500)
    asks = [r for r in harness.requests if r.method == "POST" and urlsplit(r.url).path == "/ask"]
    assert asks == [], "a final message dispatched /ask"
    assert "En vivo" in (page.locator("#voice-engine-btn").text_content() or "")
    assert page.locator("#call-btn").evaluate("b => b.classList.contains('oncall')")


def test_audio_gap_notice_is_visible_and_call_continues(karaoke_app):
    """L16 honesty: a live_audio_gap notice discloses that part of the
    turn could not be transcribed — reusing the existing turn-gap
    wording — while the engine, the socket and the call continue
    untouched (informational only, never a fallback trigger)."""
    harness, page = _live_boot(karaoke_app)
    page.locator("#call-btn").click()
    page.wait_for_function("() => window.__fakeWs && window.__fakeWs.readyState === 1", timeout=5000)
    page.wait_for_timeout(300)

    page.evaluate(
        "() => window.__fakeWs.emit({type: 'notice', reason: 'live_audio_gap'})"
    )

    banner = page.locator("#banner")
    expect(banner).to_be_visible(timeout=5000)
    text = banner.text_content() or ""
    assert "transcribir todo el turno" in text, f"turn-gap wording missing: {text}"
    assert "live_audio_gap" not in text, "raw reason token rendered verbatim"

    # Informational only: no engine switch, no socket churn, no fallback.
    assert "En vivo" in (page.locator("#voice-engine-btn").text_content() or "")
    assert page.locator("#call-btn").evaluate("b => b.classList.contains('oncall')")
    assert page.evaluate("() => window.__fakeWs.readyState") == 1
    assert page.evaluate("() => window.__wsList.length") == 1
    assert page.evaluate("() => window.__mr") == 0


# ---- S7 (PRD-05/D10, T4): speech after the close rejoins the turn ----
#
# Between the turn close (transcribe requested / /ask in flight) and the
# answer starting, resumed speech rejoins the SAME turn: the in-flight
# ask is cancelled and the WHOLE utterance re-dispatches exactly once.
# Speech once the answer has started is barge-in — PRD-07, out of scope —
# so the window closes the moment the ask resolves.
#
# The fake mirrors LIVE_FAKE plus a controllable tone (__toneOn) for the
# resumed speech, and an /ask hook that can HOLD a request in flight,
# observe aborts, and serve synthetic replies (the stale-response race).

REJOIN_FAKE = r"""
(() => { window.__gumCount = 0; window.__mr = 0;
  try { const R = window.MediaRecorder; class MR extends R { constructor(...a){ super(...a); window.__mr++; } } window.MediaRecorder = MR; } catch(e) {}
  window.__sent = []; window.__wsList = [];
  const md = navigator.mediaDevices;
  Object.defineProperty(md, "getUserMedia", {configurable: true, value: async function () {
    window.__gumCount++;
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator(); const gain = ctx.createGain(); gain.gain.value = 0.0;
    const dst = ctx.createMediaStreamDestination(); osc.connect(gain); gain.connect(dst); osc.start();
    window.__toneOn = function () { gain.gain.value = 0.7; };
    window.__stopTone = function () { gain.gain.value = 0.0; };
    return dst.stream;
  }});
  class FakeWS {
    constructor(url){this.url=url;this.readyState=0;this.sent=[];this.bufferedAmount=0;
      window.__wsList.push(this);
      setTimeout(()=>{if(this.readyState===0){this.readyState=1;this.onopen&&this.onopen();}},30);}
    send(d){this.sent.push(d);window.__sent.push(typeof d==="string"?d:"binary");}
    close(){this.readyState=3;}
    drop(){this.readyState=3;if(this.onclose)this.onclose({});}
    emit(data){if(this.onmessage)this.onmessage({data: JSON.stringify(data)});}
  }
  FakeWS.OPEN=1; window.WebSocket=FakeWS;
})();
"""

ASK_HOOK = r"""
(() => {
  window.__askLog = [];
  window.__askHolds = [];
  window.__askHoldEnabled = false;
  window.__askReplies = [];
  const native = window.fetch.bind(window);
  window.fetch = (input, options) => {
    const path = new URL(typeof input === 'string' ? input : input.url, location.href).pathname;
    if (path !== '/ask') return native(input, options);
    const entry = { text: null, outcome: null };
    window.__askLog.push(entry);
    try {
      const body = JSON.parse((options && options.body) || '{}');
      entry.text = body.text || null;
    } catch (e) {}
    const plan = window.__askReplies.length ? window.__askReplies.shift() : null;
    const wantHold = window.__askHoldEnabled || !!(plan && plan.hold);
    window.__askHoldEnabled = false;
    const hold = () => new Promise((resolve) => { window.__askHolds.push(resolve); });
    if (plan && plan.body) {
      const respond = () => {
        entry.outcome = 'resolved';
        return new Response(JSON.stringify(plan.body), {
          status: plan.status || 200,
          headers: { 'content-type': 'application/json' }
        });
      };
      return wantHold ? hold().then(respond) : Promise.resolve().then(respond);
    }
    return (async () => {
      if (wantHold) await hold();
      try {
        const resp = await native(input, options);
        if (entry.outcome === null) entry.outcome = 'resolved';
        return resp;
      } catch (err) {
        entry.outcome = err && err.name === 'AbortError' ? 'aborted' : 'error';
        throw err;
      }
    })();
  };
  window.__releaseAsk = () => { window.__askHolds.splice(0).forEach((r) => r()); };
})();
"""


def _rejoin_boot(karaoke_app):
    """Live harness with a controllable tone and the /ask hold/abort hook."""
    harness = karaoke_app()
    harness.respond("/ask", {"answer": "respuesta del harness", "audio_url": None}, method="POST")
    # Hang-up cancels the in-flight speech job server-side (the shared
    # stopAudio path) — declare that traffic so it is not "undeclared".
    harness.context.route(
        "**/speech/*/cancel",
        lambda route: route.fulfill(json={"status": "cancelled"}, status=200),
    )
    harness.context.add_init_script(REJOIN_FAKE)
    harness.context.add_init_script(ASK_HOOK)
    page = harness.load().page
    page.evaluate("() => localStorage.setItem('herdr-brain-voice-engine','live')")
    page.reload()
    page.wait_for_timeout(800)
    return harness, page


def _tone_on(page):
    """Tone on — the fake defines __toneOn only after getUserMedia runs."""
    page.wait_for_function("() => typeof window.__toneOn === 'function'", timeout=15000)
    page.evaluate("() => window.__toneOn()")


def _tone_off(page):
    page.wait_for_function("() => typeof window.__stopTone === 'function'", timeout=15000)
    page.evaluate("() => window.__stopTone()")


def _wait_sent_count(page, needle, count, timeout=30000):
    page.wait_for_function(
        "([n, c]) => window.__sent.filter(s => typeof s === 'string' && s.indexOf(n) >= 0).length >= c",
        arg=[needle, count], timeout=timeout,
    )


def _speak_close(page):
    """One closed utterance: tone on until the VAD onset (stream start),
    tone off until the silence close (transcribe command sent)."""
    _tone_on(page)
    _wait_sent_count(page, '"start"', 1)
    _tone_off(page)
    _wait_sent_count(page, '"transcribe"', 1)


def test_live_capture_stays_armed_through_thinking(karaoke_app):
    """S7 precondition: while the ask for a closed turn is in flight the
    live socket stays open and frames keep streaming — exactly one
    connection per call (D9) — so a resume in that window is heard."""
    harness, page = _rejoin_boot(karaoke_app)
    page.evaluate("""() => {
        window.__askReplies.push({status: 200, body: {answer: 'primera respuesta', audio_url: null}});
        window.__askHoldEnabled = true;
    }""")
    page.locator("#call-btn").click()
    _speak_close(page)
    page.evaluate("() => window.__wsList[0].emit({type: 'transcript', text: 'hola buenos'})")
    page.wait_for_function("() => window.__askLog.length === 1", timeout=10000)
    page.wait_for_timeout(300)  # the held ask keeps the call in thinking

    assert page.evaluate("() => window.__wsList[0].readyState") == 1, \
        "the live socket closed while the ask was still in flight"
    assert page.evaluate("() => window.__wsList.length") == 1, \
        "a second stream socket opened while thinking"
    b1 = page.evaluate("() => window.__sent.filter(s => s === 'binary').length")
    page.wait_for_timeout(600)
    b2 = page.evaluate("() => window.__sent.filter(s => s === 'binary').length")
    assert b2 > b1, "capture stopped streaming while thinking"

    # Releasing the ask resolves the turn normally.
    page.evaluate("() => window.__releaseAsk()")
    page.wait_for_function(
        "() => document.body.textContent.indexOf('primera respuesta') >= 0", timeout=10000
    )


def test_resumed_speech_rejoins_the_turn_and_cancels_the_inflight_ask(karaoke_app):
    """S7/D10 core: speech resuming after the close but before the answer
    cancels the in-flight ask and re-dispatches the WHOLE utterance as
    one turn — no independent second assistant turn, no lost words."""
    harness, page = _rejoin_boot(karaoke_app)
    page.evaluate("""() => {
        window.__askReplies.push({hold: true});  // ask 1: held NATIVE — its abort is observable
        window.__askReplies.push({status: 200, body: {answer: 'respuesta combinada', audio_url: null}});
    }""")
    page.locator("#call-btn").click()
    _speak_close(page)
    page.evaluate("() => window.__wsList[0].emit({type: 'transcript', text: 'hola buenos'})")
    page.wait_for_function("() => window.__askLog.length === 1", timeout=10000)

    # Resume while the ask is held in flight: same socket, new utterance.
    _tone_on(page)
    _wait_sent_count(page, '"start"', 2)
    assert page.evaluate("() => window.__wsList.length") == 1, \
        "the resume opened another socket instead of rejoining on the same one"
    # Release the cancelled ask mid-recording: its stale completion must
    # not clobber the resumed recording.
    page.evaluate("() => window.__releaseAsk()")
    _tone_off(page)
    _wait_sent_count(page, '"transcribe"', 2)
    page.evaluate("() => window.__wsList[0].emit({type: 'transcript', text: 'días a todos'})")

    page.wait_for_function("() => window.__askLog.length === 2", timeout=10000)
    assert page.evaluate("() => window.__askLog[1].text") == "hola buenos días a todos", \
        "the rejoined utterance did not dispatch as the whole text"
    assert page.evaluate("() => window.__askLog[0].outcome") == "aborted", \
        "the in-flight ask was not cancelled by the resume"
    page.wait_for_function(
        "() => document.body.textContent.indexOf('respuesta combinada') >= 0", timeout=10000
    )
    page.wait_for_timeout(400)
    user_turns = page.locator("#conversation .turn.user:not([data-ts])")
    assert user_turns.count() == 1, "the rejoined turn rendered a duplicate user bubble"
    assert "hola buenos días a todos" in (user_turns.first.text_content() or "")


def test_stale_ask_response_after_rejoin_never_renders(karaoke_app):
    """S7 race: the cancelled ask's response can still land after the
    abort (the fetch lost the race) — a stale answer must never render,
    own playback or overwrite the rejoined turn's state."""
    harness, page = _rejoin_boot(karaoke_app)
    page.evaluate("""() => {
        window.__askReplies.push({status: 200, body: {answer: 'RESPUESTA VIEJA', audio_url: null}});
        window.__askReplies.push({status: 200, body: {answer: 'RESPUESTA NUEVA', audio_url: null}});
        window.__askHoldEnabled = true;  // hold the FIRST ask (synthetic, survives the abort)
    }""")
    page.locator("#call-btn").click()
    _speak_close(page)
    page.evaluate("() => window.__wsList[0].emit({type: 'transcript', text: 'hola buenos'})")
    page.wait_for_function("() => window.__askLog.length === 1", timeout=10000)

    _tone_on(page)
    _wait_sent_count(page, '"start"', 2)
    # The abort fired at onset; NOW the held response lands after it.
    page.evaluate("() => window.__releaseAsk()")
    page.wait_for_function("() => window.__askLog[0].outcome === 'resolved'", timeout=10000)
    page.wait_for_timeout(400)
    assert "RESPUESTA VIEJA" not in (page.locator("#conversation").text_content() or ""), \
        "a stale response rendered after the rejoin cancelled its ask"

    # The rejoined turn still completes normally.
    _tone_off(page)
    _wait_sent_count(page, '"transcribe"', 2)
    page.evaluate("() => window.__wsList[0].emit({type: 'transcript', text: 'días'})")
    page.wait_for_function("() => window.__askLog.length === 2", timeout=10000)
    assert page.evaluate("() => window.__askLog[1].text") == "hola buenos días"
    page.wait_for_function(
        "() => document.body.textContent.indexOf('RESPUESTA NUEVA') >= 0", timeout=10000
    )
    assert "RESPUESTA VIEJA" not in (page.locator("#conversation").text_content() or "")


def test_hangup_while_ask_pending_never_dispatches_afterwards(karaoke_app):
    """S7 safety: hanging up while the ask is pending (and even if its
    response lands afterwards) dispatches nothing on the dead call and
    opens no fallback socket — the rejoin window dies with the call."""
    harness, page = _rejoin_boot(karaoke_app)
    page.evaluate("""() => {
        window.__askReplies.push({status: 200, body: {answer: 'respuesta tardía', audio_url: null}});
        window.__askHoldEnabled = true;
    }""")
    page.locator("#call-btn").click()
    _speak_close(page)
    page.evaluate("() => window.__wsList[0].emit({type: 'transcript', text: 'hola buenos'})")
    page.wait_for_function("() => window.__askLog.length === 1", timeout=10000)
    starts_before = page.evaluate(
        "() => window.__sent.filter(s => typeof s === 'string' && s.indexOf('start') >= 0).length"
    )

    page.locator("#call-btn").click()  # hang up while thinking
    page.wait_for_timeout(500)
    assert not page.locator("#call-btn").evaluate("b => b.classList.contains('oncall')"), \
        "the call did not end"
    assert page.evaluate("() => window.__wsList[0].readyState") == 3, \
        "the stream socket survived the hangup"
    assert page.evaluate("() => window.__wsList.length") == 1, \
        "the hangup opened another socket"

    # The late response lands on the dead call: no rejoin, no re-dispatch.
    page.evaluate("() => window.__releaseAsk()")
    page.wait_for_function("() => window.__askLog[0].outcome === 'resolved'", timeout=10000)
    page.wait_for_timeout(700)
    assert page.evaluate("() => window.__askLog.length") == 1, \
        "a dispatched ask appeared after the hangup"
    starts_after = page.evaluate(
        "() => window.__sent.filter(s => typeof s === 'string' && s.indexOf('start') >= 0).length"
    )
    assert starts_after == starts_before, "the dead socket kept routing utterances"
    assert not page.locator("#call-btn").evaluate("b => b.classList.contains('oncall')"), \
        "the call came back from the dead"


def test_speech_after_the_answer_is_a_new_turn_not_a_rejoin(karaoke_app):
    """S7 boundary (PRESERVE): once the answer arrived, the window is
    closed — speech afterwards is a NEW utterance on a NEW cycle, never
    merged into the answered turn (merging there would be barge-in,
    PRD-07)."""
    harness, page = _rejoin_boot(karaoke_app)
    page.evaluate(
        "() => window.__askReplies.push({status: 200, body: {answer: 'primera respuesta', audio_url: null}})"
    )
    page.locator("#call-btn").click()
    _speak_close(page)
    page.evaluate("() => window.__wsList[0].emit({type: 'transcript', text: 'hola buenos'})")
    page.wait_for_function(
        "() => document.body.textContent.indexOf('primera respuesta') >= 0", timeout=10000
    )
    # The post-answer re-arm owns a NEW socket cycle (per-turn lifecycle).
    page.wait_for_function(
        "() => window.__wsList.length >= 2 && window.__wsList[1].readyState === 1",
        timeout=10000,
    )

    _tone_on(page)
    _wait_sent_count(page, '"start"', 2)
    _tone_off(page)
    _wait_sent_count(page, '"transcribe"', 2)
    page.evaluate("() => window.__wsList[1].emit({type: 'transcript', text: 'segunda idea'})")
    page.wait_for_function("() => window.__askLog.length === 2", timeout=10000)
    assert page.evaluate("() => window.__askLog[1].text") == "segunda idea", \
        "speech after the answer merged into the answered turn"


def test_resume_during_transcribing_holds_the_first_transcript(karaoke_app):
    """S7 across the whole window: a resume BEFORE the transcript even
    arrives holds segment one as text and waits for the resumed close —
    the whole utterance dispatches exactly once, never per segment."""
    harness, page = _rejoin_boot(karaoke_app)
    page.evaluate(
        "() => window.__askReplies.push({status: 200, body: {answer: 'combinado', audio_url: null}})"
    )
    page.locator("#call-btn").click()
    # Speak and go silent, but do NOT answer the transcribe yet.
    _tone_on(page)
    _wait_sent_count(page, '"start"', 1)
    _tone_off(page)
    _wait_sent_count(page, '"transcribe"', 1)

    # Resume while still transcribing: the utterance re-opens.
    _tone_on(page)
    _wait_sent_count(page, '"start"', 2)

    # NOW the first transcript lands: held, never dispatched alone.
    page.evaluate("() => window.__wsList[0].emit({type: 'transcript', text: 'hola buenos'})")
    page.wait_for_timeout(700)
    assert page.evaluate("() => window.__askLog.length") == 0, \
        "the held first segment dispatched its own /ask"

    _tone_off(page)
    _wait_sent_count(page, '"transcribe"', 2)
    page.evaluate("() => window.__wsList[0].emit({type: 'transcript', text: 'días a todos'})")
    page.wait_for_function("() => window.__askLog.length === 1", timeout=10000)
    assert page.evaluate("() => window.__askLog[0].text") == "hola buenos días a todos"
    page.wait_for_function(
        "() => document.body.textContent.indexOf('combinado') >= 0", timeout=10000
    )


def test_empty_resumed_transcript_still_owes_the_original_words(karaoke_app):
    """S7 honesty: a resume that transcribes to nothing (a noise-triggered
    onset) still re-dispatches the cancelled turn's words — the abort
    never loses what the user already said."""
    harness, page = _rejoin_boot(karaoke_app)
    page.evaluate("""() => {
        window.__askReplies.push({status: 200, body: {answer: 'vieja', audio_url: null}});
        window.__askReplies.push({status: 200, body: {answer: 'deuda saldada', audio_url: null}});
        window.__askHoldEnabled = true;
    }""")
    page.locator("#call-btn").click()
    _speak_close(page)
    page.evaluate("() => window.__wsList[0].emit({type: 'transcript', text: 'hola buenos'})")
    page.wait_for_function("() => window.__askLog.length === 1", timeout=10000)

    _tone_on(page)
    _wait_sent_count(page, '"start"', 2)
    page.evaluate("() => window.__releaseAsk()")
    _tone_off(page)
    _wait_sent_count(page, '"transcribe"', 2)
    page.evaluate("() => window.__wsList[0].emit({type: 'transcript', text: ''})")

    page.wait_for_function("() => window.__askLog.length === 2", timeout=10000)
    assert page.evaluate("() => window.__askLog[1].text") == "hola buenos", \
        "the cancelled turn's words were dropped instead of re-dispatched"
    page.wait_for_function(
        "() => document.body.textContent.indexOf('deuda saldada') >= 0", timeout=10000
    )


def test_socket_drop_during_resumed_recording_surfaces_the_lost_phrase(karaoke_app):
    """S7 + B1: a socket drop while the resumed speech is recording must
    surface the lost rejoined utterance and arm the Servidor fallback —
    never drop the user's words silently."""
    harness, page = _rejoin_boot(karaoke_app)
    page.evaluate("""() => {
        window.__askReplies.push({status: 200, body: {answer: 'nunca llega', audio_url: null}});
        window.__askHoldEnabled = true;
    }""")
    page.locator("#call-btn").click()
    _speak_close(page)
    page.evaluate("() => window.__wsList[0].emit({type: 'transcript', text: 'hola buenos'})")
    page.wait_for_function("() => window.__askLog.length === 1", timeout=10000)

    _tone_on(page)
    _wait_sent_count(page, '"start"', 2)
    mr_before = page.evaluate("() => window.__mr")
    page.evaluate("() => window.__wsList[0].drop()")
    page.wait_for_timeout(1500)

    assert "Servidor" in (page.locator("#voice-engine-btn").text_content() or ""), \
        "the drop during resumed recording did not fall back to Servidor"
    assert page.evaluate("() => window.__mr") >= mr_before + 1, \
        "the Servidor recorder was not armed after the drop"
    banner = page.locator("#banner")
    expect(banner).to_be_visible()
    assert "frase" in (banner.text_content() or ""), \
        "the lost rejoined utterance was not surfaced"
    assert page.locator("#call-btn").evaluate("b => b.classList.contains('oncall')")


def test_approval_answer_closes_the_rejoin_window_and_capture(karaoke_app):
    """S7 approval safety: an /ask that answers with an approval gate
    closes the rejoin window and tears the capture down BEFORE the gate
    takes the call — the spoken echo must never reach an armed mic, and
    the gate owns the next utterance's routing."""
    harness, page = _rejoin_boot(karaoke_app)
    page.evaluate("""() => {
        window.__askReplies.push({status: 200, body: {
            answer: 'explicación de la puerta',
            audio_url: null,
            approval: {
                gate_id: 'gate-live-s7',
                tool: 'send_to_session',
                agent: 'opencode',
                text: 'run the tests',
                pane_id: 'w1:p9',
                timeout_ms: 300000,
                expires_in_s: 300,
            }
        }});
        window.__askHoldEnabled = true;
    }""")
    page.locator("#call-btn").click()
    _speak_close(page)
    page.evaluate("() => window.__wsList[0].emit({type: 'transcript', text: 'envía el informe'})")
    page.wait_for_function("() => window.__askLog.length === 1", timeout=10000)

    page.evaluate("() => window.__releaseAsk()")
    page.wait_for_function(
        "() => !document.getElementById('approval-float').classList.contains('hidden')",
        timeout=10000,
    )
    page.wait_for_timeout(500)
    # The capture died with the window: the rejoin socket must not stay
    # armed under the gate. (The gate itself may arm the mic again under
    # "confirming" — that is its own NEW socket, existing behavior.)
    assert page.evaluate("() => window.__wsList[0].readyState") == 3, \
        "the live socket stayed armed under the approval gate"
    starts_before = page.evaluate(
        "() => window.__wsList[0].sent.filter(s => typeof s === 'string' && s.indexOf('start') >= 0).length"
    )
    # Speech under the gate never opens a live utterance on the dead socket.
    _tone_on(page)
    page.wait_for_timeout(1200)
    starts_after = page.evaluate(
        "() => window.__wsList[0].sent.filter(s => typeof s === 'string' && s.indexOf('start') >= 0).length"
    )
    assert starts_after == starts_before, "an utterance opened on the dead gate socket"
    _tone_off(page)


# ---- S6 bounded increment (PRD-05 FR-01/FR-06): Silero framing gate ----
#
# The SERVER frames speech/silence with Silero and streams {"type":"vad"}
# events; the client keeps its energy endpointer as the decision owner and
# grants Silero ONE power only: while the negotiated VAD is silero AND its
# opinion is fresh AND it still frames speech, an energy SILENCE close is
# deferred (D3: never cut the speaker). Silero can never close a turn, and
# it can never shorten the energy windows — cap closes (FR-07) pass
# through untouched. No vad_status → exactly today's behavior.


def _emit_vad(page, msg):
    page.evaluate("(m) => window.__wsList[0].emit(m)", msg)


def _sent_has(page, needle):
    return (
        f"() => window.__sent.some(s => typeof s === 'string' && s.indexOf({needle!r}) >= 0)"
    )


def test_silero_framing_holds_the_energy_close_until_silence_agrees(karaoke_app):
    """FR-01/D3: with silero negotiated and speech heartbeats flowing, the
    energy silence close is DEFERRED while Silero still frames speech;
    the transcribe only leaves once Silero reports the speech ended."""
    harness, page = _rejoin_boot(karaoke_app)
    page.locator("#call-btn").click()
    _tone_on(page)
    _wait_sent_count(page, '"start"', 1)

    # Negotiate silero and open a speech segment; heartbeats keep the
    # opinion fresh exactly like the real server does (~1/s).
    _emit_vad(page, {"type": "vad_status", "engine": "silero", "reason": None})
    _emit_vad(page, {"type": "vad", "event": "speech_start", "at_ms": 1})
    page.evaluate("""() => {
        window.__vadPulse = setInterval(() => {
            window.__wsList[0].emit({type: 'vad', event: 'heartbeat', speech: true});
        }, 800);
    }""")

    _tone_off(page)
    # The energy window (3.2-4.5 s) elapses while Silero holds speech:
    # 5.8 s of silence and the transcribe must NOT have left.
    page.wait_for_timeout(5800)
    assert not page.evaluate(_sent_has(page, "transcribe")), \
        "the energy close dispatched while Silero still framed speech"
    assert page.locator("#call-btn").evaluate("b => b.classList.contains('oncall')"), \
        "the held turn ended the call"

    # Silero reports the speech ended: the deferred close releases and the
    # turn transcribes exactly once.
    page.evaluate("() => clearInterval(window.__vadPulse)")
    _emit_vad(page, {"type": "vad", "event": "speech_end", "at_ms": 2, "silence_ms": 512})
    _wait_sent_count(page, '"transcribe"', 1, timeout=8000)
    assert page.evaluate(
        "() => window.__sent.filter(s => typeof s === 'string' && s.indexOf('transcribe') >= 0).length"
    ) == 1, "the held turn dispatched more than once"


def test_silero_opinion_that_agrees_with_silence_never_delays_the_close(karaoke_app):
    """The gate only DEFERS while speech is active: a speech_end that
    already arrived leaves the ordinary energy close untouched (Silero
    never slows a close it agrees with)."""
    harness, page = _rejoin_boot(karaoke_app)
    page.locator("#call-btn").click()
    _tone_on(page)
    _wait_sent_count(page, '"start"', 1)
    _emit_vad(page, {"type": "vad_status", "engine": "silero", "reason": None})
    _emit_vad(page, {"type": "vad", "event": "speech_start", "at_ms": 1})
    _emit_vad(
        page, {"type": "vad", "event": "speech_end", "at_ms": 2, "silence_ms": 512}
    )

    _tone_off(page)
    # Ordinary energy close timing (~3.2-4.5 s + margin): no deferral.
    page.wait_for_function(_sent_has(page, "transcribe"), timeout=9000)


def test_stale_silero_opinion_never_blocks_the_close(karaoke_app):
    """FR-06 safety: a Silero opinion that stopped refreshing (dead
    framing, stalled socket) is UNCERTAIN — the gate disengages and the
    energy endpointer closes the turn exactly as today."""
    harness, page = _rejoin_boot(karaoke_app)
    page.locator("#call-btn").click()
    _tone_on(page)
    _wait_sent_count(page, '"start"', 1)
    _emit_vad(page, {"type": "vad_status", "engine": "silero", "reason": None})
    _emit_vad(page, {"type": "vad", "event": "speech_start", "at_ms": 1})
    # NO heartbeats: the opinion goes stale while the user is silent.

    _tone_off(page)
    page.wait_for_function(_sent_has(page, "transcribe"), timeout=12000)
    assert page.locator("#call-btn").evaluate("b => b.classList.contains('oncall')")


def test_energy_close_without_any_vad_status_is_unchanged(karaoke_app):
    """PRESERVE: a socket that never negotiates framing (old server, or a
    client that does not opt in) behaves exactly as today — the energy
    silence close dispatches the transcribe with today's timing."""
    harness, page = _rejoin_boot(karaoke_app)
    page.locator("#call-btn").click()
    _tone_on(page)
    _wait_sent_count(page, '"start"', 1)
    _tone_off(page)
    page.wait_for_function(_sent_has(page, "transcribe"), timeout=9000)


def _open_diag_after_call(page):
    """Hang up and open the idle diagnostics overlay via the long-press."""
    page.locator("#call-btn").click()  # hang up
    page.wait_for_timeout(400)
    page.locator("#call-btn").dispatch_event("pointerdown")
    page.wait_for_timeout(950)  # >= the 800 ms long-press
    return page.locator("#diag-text").text_content() or ""


def test_diag_panel_shows_silero_when_negotiated(karaoke_app):
    """FR-06: the debug panel shows the VAD actually in use — silero when
    the live socket negotiated it."""
    harness, page = _rejoin_boot(karaoke_app)
    page.locator("#call-btn").click()
    page.wait_for_function(
        "() => window.__wsList.length >= 1 && window.__wsList[0].readyState === 1",
        timeout=5000,
    )
    _emit_vad(page, {"type": "vad_status", "engine": "silero", "reason": None})
    page.wait_for_timeout(200)

    diag = _open_diag_after_call(page)
    assert "silero" in diag, f"active VAD missing from diagnostics: {diag!r}"


def test_diag_panel_shows_energy_when_silero_unavailable(karaoke_app):
    """FR-06 fallback truth: when the server answers energy (Silero
    unavailable), the debug panel must say energy — never claim silero."""
    harness, page = _rejoin_boot(karaoke_app)
    page.locator("#call-btn").click()
    page.wait_for_function(
        "() => window.__wsList.length >= 1 && window.__wsList[0].readyState === 1",
        timeout=5000,
    )
    _emit_vad(
        page,
        {"type": "vad_status", "engine": "energy", "reason": "silero_vad_model_missing"},
    )
    page.wait_for_timeout(200)

    diag = _open_diag_after_call(page)
    assert "energía" in diag, f"energy fallback missing from diagnostics: {diag!r}"
    assert "silero" not in diag, f"diagnostics claim silero on the energy path: {diag!r}"





# ---- Live uplink source wiring (Android silent-stream defect) ----
#
# A phone call heard speech through the analyser while the uplink streamed
# SILENCE ("PR rama" / "esta" transcripts). Working hypothesis (not
# reproducible off-device): the SECOND createMediaStreamSource() made for
# the same MediaStream — one belongs to the analyser, armLiveUtterance made
# another per arm — receives silence on Android, and/or a worklet with no
# destination connection is never pulled. The probe below emulates the
# hypothesis in Chromium: every source after the FIRST one is muted. The
# contract: ONE source per call feeds the analyser AND every arm, and the
# worklet is pulled through a zero-gain GainNode into the destination.

SOURCE_PROBE = r"""
(() => {
  window.__srcCount = 0; window.__connects = []; window.__disconnects = [];
  window.__frames = [];  // one {ws, max} per binary frame sent
  const Ctx = window.AudioContext || window.webkitAudioContext;
  const origSrc = Ctx.prototype.createMediaStreamSource;
  Ctx.prototype.createMediaStreamSource = function (stream) {
    window.__srcCount++;
    const real = origSrc.call(this, stream);
    if (window.__srcCount === 1) return real;
    // Android hypothesis: any later source on the same stream hears silence.
    const mute = this.createGain(); mute.gain.value = 0; real.connect(mute);
    return mute;
  };
  // The harness cannot fetch worklet modules (the worklet global scope
  // bypasses page routing), so addModule would always fail and silently
  // route every test through the ScriptProcessor fallback. Make BOTH paths
  // deterministic: "worklet" loads the real stream-worklet.js through a
  // blob URL, "reject" forces the ScriptProcessor fallback.
  const origAdd = AudioWorklet.prototype.addModule;
  AudioWorklet.prototype.addModule = async function (url, opts) {
    if (window.__workletMode === 'reject') throw new Error('worklet unavailable');
    const text = await (await fetch(url)).text();
    return origAdd.call(this, URL.createObjectURL(new Blob([text], {type: 'text/javascript'})), opts);
  };
  const origConnect = AudioNode.prototype.connect;
  AudioNode.prototype.connect = function (dest, ...rest) {
    window.__connects.push({
      from: this.constructor.name, to: dest && dest.constructor && dest.constructor.name,
      gain: this.gain ? this.gain.value : null
    });
    return origConnect.call(this, dest, ...rest);
  };
  const origDisc = AudioNode.prototype.disconnect;
  AudioNode.prototype.disconnect = function (...args) {
    window.__disconnects.push(this.constructor.name);
    return origDisc.apply(this, args);
  };
  const origSend = window.WebSocket.prototype.send;
  window.WebSocket.prototype.send = function (d) {
    if (typeof d !== 'string') {
      const view = new Int16Array(d.buffer ? d.buffer : d);
      let max = 0;
      for (let i = 0; i < view.length; i++) max = Math.max(max, Math.abs(view[i]));
      window.__frames.push({ ws: window.__wsList.indexOf(this), max: max, bytes: view.length * 2 });
    }
    return origSend.call(this, d);
  };
})();
"""


def _source_probe_boot(karaoke_app, worklet_mode="worklet"):
    harness = karaoke_app()
    harness.respond("/ask", {"answer": "ok", "audio_url": None}, method="POST")
    harness.context.route(
        "**/speech/*/cancel",
        lambda route: route.fulfill(json={"status": "cancelled"}, status=200),
    )
    harness.context.add_init_script(f"window.__workletMode = {worklet_mode!r};")
    harness.context.add_init_script(REJOIN_FAKE)
    harness.context.add_init_script(SOURCE_PROBE)
    harness.context.add_init_script(ASK_HOOK)
    page = harness.load().page
    page.evaluate("() => localStorage.setItem('herdr-brain-voice-engine','live')")
    page.reload()
    page.wait_for_timeout(800)
    return harness, page


def _audible_frames(page, ws_index):
    return page.evaluate(
        "(i) => window.__frames.filter(f => f.ws === i && f.max > 500).length", ws_index
    )


@pytest.mark.parametrize("worklet_mode", ["worklet", "reject"], ids=["audioworklet", "scriptprocessor"])
def test_live_call_streams_audible_frames_on_first_and_rearmed_turn(karaoke_app, worklet_mode):
    """The uplink must carry the AUDIBLE signal — not just frames — on the
    first turn and on a re-armed second turn, through ONE media source
    (AudioWorklet path AND ScriptProcessor fallback), and each turn
    dispatches /ask exactly once."""
    harness, page = _source_probe_boot(karaoke_app, worklet_mode)
    page.locator("#call-btn").click()
    page.wait_for_function(
        "() => window.__wsList.length >= 1 && window.__wsList[0].readyState === 1", timeout=5000
    )

    # Turn 1: speak, close, the transcript dispatches.
    _tone_on(page)
    _wait_sent_count(page, '"start"', 1)
    page.wait_for_function("() => window.__frames.some(f => f.ws === 0 && f.max > 500)", timeout=10000)
    _tone_off(page)
    _wait_sent_count(page, '"transcribe"', 1)
    page.evaluate("() => window.__wsList[0].emit({type: 'transcript', text: 'primera frase'})")
    page.wait_for_function("() => window.__askLog.length === 1", timeout=10000)

    # The answer ends the capture; the re-armed mic owns a NEW socket.
    page.wait_for_function(
        "() => window.__wsList.length >= 2 && window.__wsList[1].readyState === 1", timeout=15000
    )

    # Turn 2 on the re-armed capture.
    _tone_on(page)
    _wait_sent_count(page, '"start"', 2)
    page.wait_for_function("() => window.__frames.some(f => f.ws === 1 && f.max > 500)", timeout=10000)
    _tone_off(page)
    _wait_sent_count(page, '"transcribe"', 2)
    page.evaluate("() => window.__wsList[1].emit({type: 'transcript', text: 'segunda frase'})")
    page.wait_for_function("() => window.__askLog.length === 2", timeout=10000)
    page.wait_for_timeout(400)

    assert page.evaluate("() => window.__askLog.length") == 2, "a turn dispatched /ask more than once"
    assert _audible_frames(page, 0) > 0, "turn 1 streamed only silence"
    assert _audible_frames(page, 1) > 0, "the re-armed turn 2 streamed only silence"
    assert page.evaluate("() => window.__srcCount") == 1, \
        "the call created more than one MediaStreamSource (analyser + uplink must share one)"


def test_live_worklet_is_pulled_through_a_zero_gain_node_and_cleaned_up(karaoke_app):
    """The worklet must not rely on being pulled implicitly: its output
    routes through a zero-gain GainNode to the destination (standard pull
    guarantee, silent), and re-arm/stop disconnect that path."""
    harness, page = _source_probe_boot(karaoke_app)
    page.locator("#call-btn").click()
    page.wait_for_function(
        "() => window.__wsList.length >= 1 && window.__wsList[0].readyState === 1", timeout=5000
    )
    page.wait_for_function(
        "() => window.__connects.some(c => c.from === 'AudioWorkletNode')", timeout=10000
    )
    connects = page.evaluate("() => window.__connects")
    worklet_targets = [c["to"] for c in connects if c["from"] == "AudioWorkletNode"]
    assert worklet_targets == ["GainNode"], worklet_targets
    assert {"from": "GainNode", "to": "AudioDestinationNode", "gain": 0} in connects, \
        "no zero-gain GainNode feeds the destination"
    assert "AudioWorkletNode" not in [c["from"] for c in connects if c["to"] == "AudioDestinationNode"]

    # Hang up: the worklet and its gain are disconnected, not leaked.
    before = page.evaluate("() => window.__disconnects.filter(n => n === 'AudioWorkletNode').length")
    page.locator("#call-btn").click()
    page.wait_for_timeout(500)
    after = page.evaluate("""() => ({
        worklet: window.__disconnects.filter(n => n === 'AudioWorkletNode').length,
        gain: window.__disconnects.filter(n => n === 'GainNode').length })""")
    assert after["worklet"] > before, "the worklet node was not disconnected on hang-up"
    assert after["gain"] >= 1, "the pull-guarantee gain node was not disconnected on hang-up"


# ---- L53 device defect: real mic, AudioContext lifecycle, self-report ----
#
# The Pixel call produced ~3,200 bytes in 10 s (~two 100 ms frames) with
# peak near digital silence, and no `live client diag:` ever reached the
# server. The tests below use REAL Chromium fake-device capture (not an
# oscillator) for the byte/level proof, and explicit doubles for the
# Android AudioContext-suspended and worklet-never-posts cases.


def _real_audio_live_boot(audio_capture_context, tmp_path):
    """Real fake-device mic + fake WebSocket, real-time capture frames."""
    artifact = tmp_path / "live-real-audio"
    artifact.mkdir(parents=True, exist_ok=True)
    harness = KaraokeHarness(audio_capture_context, artifact, real_mic=True)
    harness.context.add_init_script(WS_ONLY_FAKE)
    harness.context.add_init_script(SOURCE_PROBE)
    page = harness.load().page
    page.evaluate("() => localStorage.setItem('herdr-brain-voice-engine','live')")
    page.reload()
    page.wait_for_timeout(800)
    return harness, page


def _diag_messages(page, ws_index=0):
    return page.evaluate(
        """(i) => window.__wsList[i].sent
            .filter(s => typeof s === 'string' && s.indexOf('"type":"diag"') >= 0)
            .map(s => JSON.parse(s))""",
        ws_index,
    )


def test_live_real_mic_arms_running_context_and_streams_audio_bytes(
    audio_capture_context, tmp_path
):
    """L53(a): the live pipeline must post CONTINUOUS 16 kHz PCM to the
    socket for real mic input (not two frames in 10 s) and the 2 s diag
    must report a RUNNING AudioContext plus the served build."""
    harness, page = _real_audio_live_boot(audio_capture_context, tmp_path)
    page.locator("#call-btn").click()
    page.wait_for_function(
        "() => window.__wsList.length >= 1 && window.__wsList[0].readyState === 1",
        timeout=5000,
    )

    # ~1 s of audible fake speech must be far above the 3,200-byte defect.
    page.wait_for_function(
        """() => {
            const frames = window.__frames.filter(f => f.ws === 0);
            const bytes = frames.reduce((n, f) => n + (f.bytes || 0), 0);
            return bytes > 20000 && frames.some(f => f.max > 500);
        }""",
        timeout=12000,
    )

    page.wait_for_function(
        """() => window.__wsList[0].sent.some(s =>
            typeof s === 'string' && s.indexOf('"type":"diag"') >= 0 &&
            JSON.parse(s).posted > 0 && JSON.parse(s).ctx === 'running')""",
        timeout=8000,
    )
    diag = _diag_messages(page)[-1]
    assert diag["ctx"] == "running", diag
    assert diag["mode"] in ("worklet", "script"), diag
    assert diag["posted"] > 0 and diag["sent"] > 0, diag
    assert diag["build"], f"diag carries no served build: {diag}"
    assert diag["maxrms"] >= 0, diag

    bytes_sent = page.evaluate(
        "() => window.__frames.filter(f => f.ws === 0).reduce((n,f) => n + (f.bytes||0), 0)"
    )
    assert bytes_sent > 20000, f"uplink streamed only {bytes_sent} bytes"
    assert not harness.page_errors, harness.page_errors


def test_suspended_audio_context_banner_and_servidor_fallback(karaoke_app):
    """L53(b): a context that never actually runs must surface the new
    banner and fall back to Servidor listening — never a deaf live call
    stuck in transcribing."""
    harness = karaoke_app()
    harness.context.add_init_script(LIVE_FAKE)
    harness.context.add_init_script(SUSPENDED_CONTEXT_FAKE)
    page = harness.load().page
    page.evaluate("() => localStorage.setItem('herdr-brain-voice-engine','live')")
    page.reload()
    page.wait_for_timeout(600)

    page.locator("#call-btn").click()
    banner = page.locator("#banner")
    expect(banner).to_be_visible(timeout=5000)
    text = banner.text_content() or ""
    assert "suspendido" in text, f"suspended-context banner missing: {text}"

    assert "Servidor" in (page.locator("#voice-engine-btn").text_content() or "")
    assert page.locator("#call-btn").evaluate("b => b.classList.contains('oncall')")
    strip = page.locator("#strip-status").text_content() or ""
    assert "transcrib" not in strip, f"call stuck transcribing: {strip!r}"
    assert page.evaluate("() => window.__mr") >= 1, "Servidor never armed the recorder"


@pytest.mark.parametrize(
    "error_name,needle",
    [
        ("NotAllowedError", "BLOQUEADO"),
        ("SecurityError", "BLOQUEADO"),
        ("NotFoundError", "No encuentro el micrófono"),
        ("NotReadableError", "No encuentro el micrófono"),
    ],
)
def test_live_get_user_media_failure_surfaces_distinct_banner(
    karaoke_app, error_name, needle
):
    """L53(c): permission denied keeps the Micrófono BLOQUEADO semantics;
    a missing/unreadable device gets its own live-banner wording instead
    of one generic fallback."""
    harness = karaoke_app()
    harness.context.add_init_script(LIVE_FAKE)
    harness.context.add_init_script(f"""
        (() => {{
          const name = {error_name!r};
          const md = navigator.mediaDevices;
          Object.defineProperty(md, 'getUserMedia', {{
            configurable: true,
            value: () => Promise.reject(new DOMException('fake', name))
          }});
        }})();
    """)
    page = harness.load().page
    page.evaluate("() => localStorage.setItem('herdr-brain-voice-engine','live')")
    page.reload()
    page.wait_for_timeout(600)

    page.locator("#call-btn").click()
    banner = page.locator("#banner")
    expect(banner).to_be_visible(timeout=5000)
    text = banner.text_content() or ""
    assert needle in text, f"{error_name}: wrong banner {text!r}"
    assert "Servidor" in (page.locator("#voice-engine-btn").text_content() or "")


def test_live_missing_media_devices_surfaces_distinct_banner(karaoke_app):
    """L53(c): a browser with no navigator.mediaDevices gets its own
    wording (and keyboard mode), not the generic socket fallback."""
    harness = karaoke_app()
    harness.context.add_init_script(LIVE_FAKE)
    harness.context.add_init_script(
        "Object.defineProperty(navigator, 'mediaDevices', "
        "{configurable: true, value: undefined});"
    )
    page = harness.load().page
    page.evaluate("() => localStorage.setItem('herdr-brain-voice-engine','live')")
    page.reload()
    page.wait_for_timeout(600)

    page.locator("#call-btn").click()
    banner = page.locator("#banner")
    expect(banner).to_be_visible(timeout=5000)
    text = banner.text_content() or ""
    assert "no expone el micrófono" in text, f"missing-api banner wrong: {text!r}"


def test_worklet_without_frames_rearms_script_processor_and_reports_reason(karaoke_app):
    """L53(d)/health: a worklet that loads but posts ZERO frames must be
    replaced by the ScriptProcessor path within ~1 s, and the diag must
    name the reason so the next device attempt is conclusive."""
    harness, page = _live_boot(
        karaoke_app, fake=LIVE_FAKE + "\n" + WORKLET_STUCK_FAKE + "\n" + SOURCE_PROBE
    )
    page.locator("#call-btn").click()
    page.wait_for_function(
        "() => window.__wsList.length >= 1 && window.__wsList[0].readyState === 1",
        timeout=5000,
    )
    page.wait_for_function(
        """() => window.__wsList[0].sent.some(s => {
            if (typeof s !== 'string' || s.indexOf('"type":"diag"') < 0) return false;
            const d = JSON.parse(s);
            return d.mode === 'script' && d.reason === 'worklet_no_frames';
        })""",
        timeout=8000,
    )
    page.wait_for_function(
        "() => window.__frames.some(f => f.ws === 0 && f.max > 500)", timeout=8000
    )
    diag = [
        d for d in _diag_messages(page)
        if d["mode"] == "script" and d["reason"] == "worklet_no_frames"
    ][-1]
    assert diag["posted"] > 0, diag
