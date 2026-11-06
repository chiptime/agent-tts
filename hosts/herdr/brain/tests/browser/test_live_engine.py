"""Browser tests for Dictado: En vivo engine and fallback (PRD-03 Task T1)."""

import pytest
from playwright.sync_api import expect


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
    page.evaluate("() => window.__stopTone && window.__stopTone()")
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
