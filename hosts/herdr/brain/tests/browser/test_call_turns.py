"""Hands-free turn-taking through the real app (T1, brain-call-reliability).

Deterministic proofs for the premature-endpointing repair:
- a natural pause inside one spoken turn no longer splits it into several
  /ask dispatches ("turnos nuevos innecesarios", S1);
- continuous speech ACROSS the old 45s hard cap — words on BOTH sides of
  it — produces NO premature /ask and exactly ONE complete turn after the
  natural end (the server engine bounds memory/transport by rotating
  recorder segments whose transcripts accumulate in order);
- pathological nonstop input is still bounded (10-minute utterance cap)
  and that bound is VISIBLE when it fires; captured words still dispatch.

Two engines, two clock strategies, zero real microphones:
- navegador (Web Speech fixture): the endpointer consumes Date.now(), so an
  init script freezes the clock and the test advances it explicitly.
- servidor (Chromium fake capture): energy frames are REAL fake-device
  frames and the WAV envelope decides speech/silence; a self-advancing
  fast clock reaches the 45s segment boundary / 10-minute cap in seconds
  of wall time. The tone is deterministic VAD input, never an
  acoustic-quality claim.
"""

import math
import struct
import wave

import pytest

from tests.browser.test_barge_in import BargeInApp
from tests.browser.test_karaoke_real_media import ORIGIN
from tests.browser.test_voice_dialog_browser import post_body, wait_post


CAP_NOTICE_MARK = "límite"  # the localized cap-cut banner contains this word

VOICE_CLOCK_INIT = """
localStorage.setItem('herdr-brain-voice-engine', 'navegador');
(() => {
    // Frozen clock: the endpointer judges silence in Date.now() units, so
    // only an explicit advance() can let time pass. Fully deterministic.
    let t = 1000000;
    window.__voiceClock = {advance: ms => { t += ms; }, now: () => t};
    Date.now = () => t;
    // Record every banner text the app really showed (it can hide again
    // before a poll would see it).
    window.__banners = [];
    document.addEventListener('DOMContentLoaded', () => {
        const banner = document.getElementById('banner');
        if (banner) new MutationObserver(() => {
            const text = banner.textContent;
            if (text && !banner.classList.contains('hidden') &&
                !window.__banners.includes(text)) {
                window.__banners.push(text);
            }
        }).observe(banner, {childList: true, characterData: true, subtree: true,
            attributes: true, attributeFilter: ['class']});
    });
})();
"""


def fast_clock_init(speed):
    """BargeInApp __EXTRA__ block: a self-advancing clock (fake ms per wall
    ms) plus the banner recorder. Real analyser frames keep flowing in wall
    time, so the WAV envelope still decides speech/silence."""
    return r"""
    const epoch = performance.now();
    const speed = %d;
    Date.now = () => 1000000 + Math.round((performance.now() - epoch) * speed);
    window.__banners = [];
    document.addEventListener('DOMContentLoaded', () => {
        const banner = document.getElementById('banner');
        if (banner) new MutationObserver(() => {
            const text = banner.textContent;
            if (text && !banner.classList.contains('hidden') &&
                !window.__banners.includes(text)) {
                window.__banners.push(text);
            }
        }).observe(banner, {childList: true, characterData: true, subtree: true,
            attributes: true, attributeFilter: ['class']});
    });
""" % (speed,)


# ---------------- navegador (browser engine) ----------------


def voice_turn_app(karaoke_app):
    """Browser-engine harness with a frozen clock and one /ask responder."""
    harness = karaoke_app()
    harness.context.add_init_script(VOICE_CLOCK_INIT)
    harness.respond("/ask", {"answer": "Hecho.", "audio_url": None}, method="POST")
    harness.load()
    harness.page.set_default_timeout(10000)
    harness.page.locator("#call-btn").click()
    harness.page.wait_for_function("() => __karaokeHarness.recognitions.length > 0")
    return harness


def feed(harness, index, text, final=False):
    """One interim (or final) result chunk through the real recognition."""
    harness.page.evaluate(
        """([index, text, final]) => {
            const rec = __karaokeHarness.recognitions[index];
            rec.emit('result', {resultIndex: 0,
                results: [{isFinal: final, 0: {transcript: text}}]});
        }""",
        [index, text, final],
    )


def advance(harness, ms):
    harness.page.evaluate("ms => __voiceClock.advance(ms)", ms)


def ask_count(harness):
    return harness.page.evaluate(
        "() => __karaokeHarness.fetchCalls.filter(p => p === '/ask').length")


def test_natural_pause_keeps_one_turn(karaoke_app):
    harness = voice_turn_app(karaoke_app)
    # Phrase 1 grows while the (frozen) clock advances: ~2.4s of speech.
    feed(harness, 0, "oye", final=False)
    advance(harness, 1200)
    feed(harness, 0, "oye brain", final=False)
    advance(harness, 1200)
    feed(harness, 0, "oye brain", final=True)
    # Natural thinking pause between phrases: 1300ms. The grown budget
    # (1200 + 2.4*200 = 1680ms) must hold both phrases in ONE turn.
    advance(harness, 1300)
    harness.page.wait_for_timeout(400)  # real epTicks evaluate the frozen clock
    assert ask_count(harness) == 0
    # Phrase 2 continues the SAME turn...
    feed(harness, 0, "necesito", final=False)
    advance(harness, 600)
    feed(harness, 0, "necesito que revises el plan", final=True)
    # ...and only the closing silence dispatches it, whole.
    advance(harness, 2200)
    wait_post(harness, "/ask")
    assert ask_count(harness) == 1
    assert post_body(harness, "/ask")["text"] == "oye brain necesito que revises el plan"


def test_short_command_still_ends_the_turn(karaoke_app):
    """PRESERVE: turn-taking still works — a short command dispatches once."""
    harness = voice_turn_app(karaoke_app)
    feed(harness, 0, "qué tal", final=True)
    advance(harness, 1300)
    wait_post(harness, "/ask")
    assert ask_count(harness) == 1
    assert post_body(harness, "/ask")["text"] == "qué tal"


def test_speech_across_the_old_cap_stays_one_complete_turn(karaoke_app):
    """BLOCKER 1 (browser engine): continuous speech spanning the old 45s
    hard cap — words on BOTH sides of it — must NOT dispatch mid-speech.
    Only the natural end (silence) closes the turn, exactly once, whole."""
    harness = voice_turn_app(karaoke_app)
    harness.page.evaluate("""() => {
        const rec = __karaokeHarness.recognitions[0];
        window.__speech = '';
        const emit = text => rec.emit('result', {resultIndex: 0,
            results: [{isFinal: false, 0: {transcript: text}}]});
        for (let t = 1; t <= 50; t++) {
            __voiceClock.advance(1000);
            window.__speech += (t === 1 ? '' : ' ') + 'palabra' + t;
            emit(window.__speech);
        }
    }""")
    harness.page.wait_for_timeout(400)
    # Mid-speech at fake t=50s, beyond the old 45s cap: nothing dispatched.
    assert ask_count(harness) == 0
    # Natural end: silence closes the turn exactly once, with both sides.
    advance(harness, 2600)
    wait_post(harness, "/ask")
    assert ask_count(harness) == 1
    body = post_body(harness, "/ask")["text"]
    assert "palabra1" in body and "palabra50" in body


def test_nonstop_speech_bounds_at_the_visible_utterance_cap(karaoke_app):
    """Pathological never-quiet input is still bounded (10 minutes) and the
    bound is VISIBLE; the captured words ride the single dispatch."""
    harness = voice_turn_app(karaoke_app)

    def speak(frm, to):
        harness.page.evaluate("""([frm, to]) => {
            const rec = __karaokeHarness.recognitions[0];
            window.__speech = window.__speech || '';
            const emit = text => rec.emit('result', {resultIndex: 0,
                results: [{isFinal: false, 0: {transcript: text}}]});
            for (let t = frm; t <= to; t++) {
                __voiceClock.advance(1000);
                window.__speech += (t === 1 ? '' : ' ') + 'palabra' + t;
                emit(window.__speech);
            }
        }""", [frm, to])

    speak(1, 300)  # five minutes of nonstop speech
    harness.page.wait_for_timeout(400)
    assert ask_count(harness) == 0  # nothing fires before the 10-minute bound
    speak(301, 600)
    advance(harness, 2000)  # cross the utterance cap since speech began
    wait_post(harness, "/ask")
    assert ask_count(harness) == 1  # one bounded turn, not one per stretch
    body = post_body(harness, "/ask")["text"]
    assert "palabra1" in body and "palabra600" in body
    banners = harness.page.evaluate("() => __banners")
    assert any(CAP_NOTICE_MARK in text for text in banners)


# ---------------- servidor (server engine, real fake capture) ----------------


def write_wav(path, *, total_s, period_s, voiced_from, voiced_to):
    """Deterministic 440 Hz envelope: voiced windows per looping period."""
    rate = 48000
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        frames = bytearray()
        for index in range(int(rate * total_s)):
            phase = (index / rate) % period_s
            voiced = voiced_from <= phase < voiced_to
            value = int(12000 * math.sin(2 * math.pi * 440 * index / rate)) if voiced else 0
            frames.extend(struct.pack("<h", value))
        wav.writeframes(frames)


def write_wav_windows(path, *, total_s, windows):
    """Deterministic 440 Hz tone inside each (start_s, end_s) window."""
    rate = 48000
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        frames = bytearray()
        for index in range(int(rate * total_s)):
            at = index / rate
            voiced = any(start <= at < end for start, end in windows)
            value = int(12000 * math.sin(2 * math.pi * 440 * index / rate)) if voiced else 0
            frames.extend(struct.pack("<h", value))
        wav.writeframes(frames)


def capture_context(browser_type, tmp_path, *, total_s, period_s=None, voiced_from=None,
                    voiced_to=None, windows=None, user_agent=None):
    """Chromium fake capture over a custom deterministic WAV envelope."""
    assert browser_type.name == "chromium", "Fake capture requires Chromium"
    capture = tmp_path / "speech.wav"
    if windows is not None:
        write_wav_windows(capture, total_s=total_s, windows=windows)
    else:
        write_wav(capture, total_s=total_s, period_s=period_s,
                  voiced_from=voiced_from, voiced_to=voiced_to)
    browser = browser_type.launch(args=[
        "--use-fake-ui-for-media-stream",
        "--use-fake-device-for-media-stream",
        "--use-file-for-fake-audio-capture=" + str(capture),
    ])
    extra = {"user_agent": user_agent} if user_agent else {}
    context = browser.new_context(
        permissions=["microphone"], service_workers="block", accept_downloads=False,
        **extra,
    )
    try:
        yield context
    finally:
        context.close()
        browser.close()


@pytest.fixture
def bursty_capture_context(browser_type, tmp_path):
    # 2.0s voiced + 1.2s silent per 3.2s loop, two periods in the file.
    yield from capture_context(browser_type, tmp_path,
                               total_s=6.4, period_s=3.2, voiced_from=0.2, voiced_to=2.2)


@pytest.fixture
def continuous_capture_context(browser_type, tmp_path):
    yield from capture_context(browser_type, tmp_path,
                               total_s=4.0, period_s=1.0, voiced_from=0.0, voiced_to=1.0)


@pytest.fixture
def tolling_capture_context(browser_type, tmp_path):
    # 6.0s voiced then 2.0s silent per 8.0s loop: continuous speech across
    # the 45s (fast-clock) segment boundary, then a NATURAL end.
    yield from capture_context(browser_type, tmp_path,
                               total_s=8.0, period_s=8.0, voiced_from=0.0, voiced_to=6.0)


@pytest.fixture
def resumed_capture_context(browser_type, tmp_path):
    # 1s phrase, a 1.8s pause (longer than its 1.4s silence window), then
    # the speaker resumes for 1s: one thought with a long thinking pause.
    yield from capture_context(browser_type, tmp_path, total_s=12.0,
                               windows=[(0.2, 1.2), (3.0, 4.0)])


@pytest.fixture
def separate_turns_capture_context(browser_type, tmp_path):
    # Same first phrase, but the next one starts well after the turn ended.
    yield from capture_context(browser_type, tmp_path, total_s=12.0,
                               windows=[(0.2, 1.2), (6.0, 7.0)])


PIXEL_7_PRO_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 7 Pro) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36"
)


@pytest.fixture
def android_capture_context(browser_type, tmp_path):
    # One phrase, then a pause far longer than the continuation window.
    yield from capture_context(browser_type, tmp_path, total_s=8.0, period_s=8.0,
                               voiced_from=0.2, voiced_to=1.2, user_agent=PIXEL_7_PRO_UA)


ANDROID_SERVER_DEFAULT = r"""
window.__webSpeechStarts = 0;
class CountingRecognition {
  start() { window.__webSpeechStarts += 1; }
  stop() {}
  abort() {}
}
window.SpeechRecognition = CountingRecognition;
window.webkitSpeechRecognition = CountingRecognition;
"""


COUNT_WEB_SPEECH = r"""
localStorage.setItem('herdr-brain-voice-engine', 'navegador');
window.__webSpeechStarts = 0;
// A counting Web Speech stand-in: on Android every real start() plays the
// recognizer earcon, so the call must never reach it there.
class CountingRecognition {
  start() { window.__webSpeechStarts += 1; }
  stop() {}
  abort() {}
}
window.SpeechRecognition = CountingRecognition;
window.webkitSpeechRecognition = CountingRecognition;
"""


def close_app(context, app):
    try:
        assert not app.blocked, "Unexpected traffic was blocked: " + repr(app.blocked)
        assert not app.errors, app.errors
    finally:
        context.close()


def test_server_natural_gaps_keep_one_utterance(bursty_capture_context):
    """Bursts of speech with 1.2s gaps: the old 800ms silence finalized
    INSIDE every gap and dispatched a fragment per burst (S1: "corta
    mientras hablas"). The grown budget must hold the whole loop in ONE
    open utterance: no /transcribe at all, recording still running."""
    context = bursty_capture_context
    app = BargeInApp(context)
    try:
        app.load()
        app.page.wait_for_function(
            "() => document.getElementById('rec-elapsed').textContent !== '0:00'")
        app.page.wait_for_timeout(6000)
        assert app.page.evaluate("() => __barge.transcribes") == 0
        # The utterance is still open: the elapsed timer never reset.
        assert app.page.evaluate(
            "() => document.getElementById('rec-elapsed').textContent") != "0:00"
    finally:
        close_app(context, app)


def test_server_speech_across_the_segment_boundary_ends_naturally(tolling_capture_context):
    """BLOCKER 1 (server engine): continuous speech across the old 45s cap
    rotates a recorder SEGMENT (a /transcribe upload — bounded transport,
    no dispatch), and the turn only ends at the NATURAL silence: exactly
    one /ask carrying the ordered transcripts of BOTH sides."""
    context = tolling_capture_context
    app = BargeInApp(context, extra_init=fast_clock_init(10))
    app.transcript_replies = [{"text": "primera parte"}, {"text": "segunda parte"}]
    app.ask_responder = lambda body: ({"answer": "ok"}, False)
    try:
        app.load()
        start = app.page.evaluate("() => Date.now()")
        # Past fake t=50s (the old cap boundary is 45s): one segment has
        # uploaded, nothing has DISPATCHED.
        app.page.wait_for_function(
            "start => __barge.transcribes >= 1 && __barge.asks.length === 0 && Date.now() - start >= 50000",
            arg=start, timeout=8000)
        # The WAV goes quiet at fake ~60s: the turn ends naturally, once.
        app.page.wait_for_function("() => __barge.asks.length === 1", timeout=8000)
        assert app.asks[0]["text"] == "primera parte segunda parte"  # ordered
        app.page.wait_for_function("() => __barge.transcribes === 2", timeout=8000)
        banners = app.page.evaluate("() => __banners")
        assert not any(CAP_NOTICE_MARK in text for text in banners)  # natural end
    finally:
        close_app(context, app)


def test_server_nonstop_input_bounds_at_the_visible_utterance_cap(continuous_capture_context):
    """Never-quiet input is bounded by the 10-minute utterance cap: segments
    keep uploading (bounded memory), then ONE dispatch carries the captured
    text and the bound is announced visibly."""
    context = continuous_capture_context
    app = BargeInApp(context, extra_init=fast_clock_init(100))
    app.transcript_replies = [{"text": f"part {i}"} for i in range(1, 30)]
    app.ask_responder = lambda body: ({"answer": "ok"}, False)
    try:
        app.load()
        start = app.page.evaluate("() => Date.now()")
        app.page.wait_for_function(
            "start => __barge.transcribes >= 1 && __barge.asks.length === 0 && Date.now() - start >= 50000",
            arg=start, timeout=8000)
        app.page.wait_for_function("() => __barge.asks.length === 1", timeout=15000)
        assert app.asks[0]["text"] == " ".join(
            f"part {i}" for i in range(1, len(app.transcribes) + 1)
        )
        assert app.transcribes[0] > 1000  # real webm bytes, not a sliver
        assert len(app.transcribes) >= 2  # segments rotated, memory bounded
        banners = app.page.evaluate("() => __banners")
        assert any(CAP_NOTICE_MARK in text for text in banners)
    finally:
        close_app(context, app)


DEFER_FIRST_TRANSCRIPTION = r"""
const originalFetch = window.fetch.bind(window);
window.resolveFirstTranscription = null;
window.fetch = (input, options) => {
  const path = new URL(typeof input === "string" ? input : input.url, location.href).pathname;
  if (path === "/transcribe" && !window.firstTranscriptionSeen) {
    window.firstTranscriptionSeen = true;
    return new Promise(resolve => { window.resolveFirstTranscription = resolve; });
  }
  return originalFetch(input, options);
};
"""

DELAY_FIRST_RECORDER_STOP = r"""
const originalStop = MediaRecorder.prototype.stop;
MediaRecorder.prototype.stop = function () {
  if (!window.delayedRecorderStop) {
    window.delayedRecorderStop = true;
    const listener = this.onstop;
    this.onstop = event => setTimeout(() => listener.call(this, event), 4000);
  }
  return originalStop.call(this);
};
"""


def test_server_delayed_segment_stop_preserves_order(tolling_capture_context):
    """A late recorder event cannot send a later segment as a complete turn."""
    context = tolling_capture_context
    app = BargeInApp(context, extra_init=fast_clock_init(10) + DELAY_FIRST_RECORDER_STOP)
    app.transcript_replies = [{"text": "first part"}, {"text": "second part"}]
    app.ask_responder = lambda body: ({"answer": "ok"}, False)
    try:
        app.load()
        app.page.wait_for_function("() => window.delayedRecorderStop === true", timeout=12000)
        app.page.wait_for_timeout(2500)
        assert app.asks == []
        app.page.wait_for_function("() => __barge.asks.length === 1", timeout=8000)
        assert app.asks[0]["text"] == "first part second part"
    finally:
        close_app(context, app)


def test_server_failed_segment_does_not_dispatch_partial_turn(tolling_capture_context):
    """A missing segment must not turn the surviving words into a new request."""
    context = tolling_capture_context
    app = BargeInApp(context, extra_init=fast_clock_init(10) + DEFER_FIRST_TRANSCRIPTION)
    app.transcript_replies = [{"text": "delete everything"}]
    app.ask_responder = lambda body: ({"answer": "ok"}, False)
    try:
        app.load()
        app.page.wait_for_function("() => window.resolveFirstTranscription !== null", timeout=12000)
        app.page.evaluate("""() => window.resolveFirstTranscription(new Response(
          JSON.stringify({detail: 'failed'}), {status: 500, headers: {'Content-Type': 'application/json'}}))""")
        app.page.wait_for_timeout(5000)
        assert app.asks == []
        assert any("transcrib" in text.lower() for text in app.page.evaluate("() => __banners"))
    finally:
        close_app(context, app)


def test_server_empty_segment_does_not_dispatch_partial_turn(tolling_capture_context):
    """An unrecognized segment cannot leave the rest as an actionable request."""
    context = tolling_capture_context
    app = BargeInApp(context, extra_init=fast_clock_init(10))
    app.transcript_replies = [{"text": "first part"}, {"text": ""}]
    app.ask_responder = lambda body: ({"answer": "ok"}, False)
    try:
        app.load()
        app.page.wait_for_function("() => __banners.some(text => /transcrib/i.test(text))", timeout=12000)
        assert app.asks == []
        assert len(app.transcribes) == 2
    finally:
        close_app(context, app)


def test_server_pending_transcription_cannot_dispatch_after_hangup(tolling_capture_context):
    """A response arriving after hangup cannot revive the previous turn."""
    context = tolling_capture_context
    app = BargeInApp(context, extra_init=fast_clock_init(10) + DEFER_FIRST_TRANSCRIPTION)
    app.transcript_replies = [{"text": "second part"}]
    app.ask_responder = lambda body: ({"answer": "ok"}, False)
    try:
        app.load()
        app.page.wait_for_function("() => window.resolveFirstTranscription !== null", timeout=12000)
        # Let the utterance end while its first segment is still pending.
        app.page.wait_for_timeout(3500)
        app.page.locator("#call-btn").click()
        app.page.evaluate("""() => window.resolveFirstTranscription(new Response(
          JSON.stringify({text: 'stale command'}), {status: 200, headers: {'Content-Type': 'application/json'}}))""")
        app.page.wait_for_timeout(1500)
        assert app.asks == []
    finally:
        close_app(context, app)


def test_server_old_transcription_cannot_dispatch_into_a_new_call(tolling_capture_context):
    """A reopened but paused call must not consume the previous call's response."""
    context = tolling_capture_context
    app = BargeInApp(context, extra_init=fast_clock_init(10) + DEFER_FIRST_TRANSCRIPTION)
    app.transcript_replies = [{"text": "second part"}]
    app.ask_responder = lambda body: ({"answer": "ok"}, False)
    try:
        app.load()
        app.page.wait_for_function("() => window.resolveFirstTranscription !== null", timeout=12000)
        app.page.wait_for_timeout(3500)
        app.page.locator("#call-btn").click()
        app.page.locator("#call-btn").click()
        app.page.locator("#pause-btn").click()
        app.page.evaluate("""() => window.resolveFirstTranscription(new Response(
          JSON.stringify({text: 'stale command'}), {status: 200, headers: {'Content-Type': 'application/json'}}))""")
        app.page.wait_for_timeout(1500)
        assert app.asks == []
    finally:
        close_app(context, app)


def wait_python_asks(app, count, timeout_ms=15000):
    """Polls the Python-side /ask record (route handlers run while waiting)."""
    waited = 0
    while len(app.asks) < count and waited < timeout_ms:
        app.page.wait_for_timeout(100)
        waited += 100
    assert len(app.asks) >= count, f"expected {count} /ask, saw {len(app.asks)}"
    return waited


def test_server_resumed_speech_after_a_long_pause_stays_one_turn(resumed_capture_context):
    """S1 (Pixel report "me corta mientras hablo"): a thinking pause longer
    than the silence window used to dispatch the first phrase alone and
    lose the rest to a second turn. Speech that resumes inside the
    continuation window rejoins the SAME turn: one /ask, both phrases."""
    context = resumed_capture_context
    app = BargeInApp(context)
    app.transcript_replies = [{"text": "uno"}, {"text": "dos"}]
    app.ask_responder = lambda body: ({"answer": "ok"}, False)
    try:
        app.load()
        wait_python_asks(app, 1)
        assert app.asks[0]["text"] == "uno dos"
    finally:
        close_app(context, app)


def test_server_turn_still_ends_when_the_speaker_stays_quiet(separate_turns_capture_context):
    """PRESERVE turn-taking: the continuation window is bounded, so a
    phrase followed by real silence dispatches on its own, before the
    next phrase begins."""
    context = separate_turns_capture_context
    app = BargeInApp(context)
    app.transcript_replies = [{"text": "uno"}, {"text": "dos"}]
    app.ask_responder = lambda body: ({"answer": "ok"}, False)
    try:
        app.load()
        waited = wait_python_asks(app, 1)
        assert app.asks[0]["text"] == "uno"
        assert waited < 5500  # dispatched before the second phrase (6s) began
    finally:
        close_app(context, app)


def test_android_server_call_never_uses_web_speech_and_toggle_is_allowed(android_capture_context):
    """S2: on Android the default Server engine never starts Web Speech
    (whose every start() plays the system beep), and the engine choice
    stays the user's: the toggle switches to Navegador."""
    context = android_capture_context
    app = BargeInApp(context, extra_init=ANDROID_SERVER_DEFAULT)
    app.transcript_replies = [{"text": "hola"}]
    app.ask_responder = lambda body: ({"answer": "ok"}, False)
    try:
        app.load()
        wait_python_asks(app, 1)
        assert app.asks[0]["text"] == "hola"
        assert app.page.evaluate("() => window.__webSpeechStarts") == 0
        app.page.evaluate("() => document.getElementById('voice-engine-btn').click()")
        assert app.page.evaluate(
            "() => localStorage.getItem('herdr-brain-voice-engine')") == "navegador"
    finally:
        close_app(context, app)


@pytest.fixture
def android_late_speech_context(browser_type, tmp_path):
    # 3s of silence, then speech: the idle stretch must stay beep-free.
    yield from capture_context(browser_type, tmp_path, total_s=8.0,
                               windows=[(3.0, 4.5)], user_agent=PIXEL_7_PRO_UA)


def test_android_browser_engine_starts_recognition_only_on_speech(android_late_speech_context):
    """S3 ("no le veo a que tenga que estar pitando todo el rato"): with the
    Browser engine on Android, an idle mic is watched silently and Web
    Speech (one beep per start) starts only when speech begins."""
    context = android_late_speech_context
    app = BargeInApp(context, extra_init=COUNT_WEB_SPEECH)
    try:
        app.load()
        app.page.wait_for_timeout(2000)  # inside the silent stretch
        assert app.page.evaluate("() => window.__webSpeechStarts") == 0
        assert app.page.evaluate(
            "() => localStorage.getItem('herdr-brain-voice-engine')") == "navegador"
        app.page.wait_for_function("() => window.__webSpeechStarts === 1", timeout=6000)
        app.page.wait_for_timeout(1500)
        assert app.page.evaluate("() => window.__webSpeechStarts") == 1
    finally:
        close_app(context, app)
