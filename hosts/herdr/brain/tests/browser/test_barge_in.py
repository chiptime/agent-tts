"""Client barge-in over REAL fake capture and native playback (FR-04/05, T-05).

Chromium runs with all three fake-media flags and the canonical WAV from
``audio_capture_context``: getUserMedia, MediaRecorder, RMS analysis,
MP3 decoding and every media event are native. The init script only
OBSERVES (timestamps the real analyser frames, pause calls and /ask
abort signals) and stubs the pure service boundaries (EventSource,
SpeechRecognition) exactly like the real-media suite. No media event is
ever synthesized here.
"""

import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.browser.test_karaoke_real_media import ORIGIN, RealMediaApp


ROOT = Path(__file__).resolve().parents[2]
MAIN = "#conversation .turn.brain"

GATE = {
    "gate_id": "gate-barge-0001",
    "tool": "send_to_session",
    "agent": "opencode",
    "text": "run the full test suite",
    "pane_id": "w1:p9",
    "timeout_ms": 300000,
    "expires_in_s": 300,
}

BARGE_INIT = r"""
(() => {
    const probe = window.__barge = {events: [], frames: [], pauses: [],
        asks: [], abortedAsks: [], transcribes: 0, askResponses: 0, sources: []};
    class FixtureEventSource extends EventTarget {
        constructor(url) { super(); this.url = url; this.readyState = 1; probe.sources.push(this); }
        close() { this.readyState = 2; }
        emit(payload) { if (this.onmessage) this.onmessage({data: JSON.stringify(payload)}); }
    }
    window.EventSource = FixtureEventSource;
    window.SpeechRecognition = window.webkitSpeechRecognition = class {
        start() {} stop() {} abort() {}
    };
    localStorage.setItem('herdr-brain-voice-engine', 'servidor');
    if (navigator.serviceWorker) Object.defineProperty(navigator.serviceWorker, 'register', {
        configurable: true, value: () => Promise.reject(new DOMException('Blocked', 'SecurityError'))
    });
    __EXTRA__
    // Observation only: timestamp the REAL analyser frames the app reads.
    const nativeGetFloat = AnalyserNode.prototype.getFloatTimeDomainData;
    AnalyserNode.prototype.getFloatTimeDomainData = function (array) {
        nativeGetFloat.call(this, array);
        let sum = 0;
        for (let i = 0; i < array.length; i++) sum += array[i] * array[i];
        probe.frames.push({t: performance.now(), rms: Math.sqrt(sum / array.length)});
        if (probe.frames.length > 6000) probe.frames.splice(0, 3000);
    };
    // Observation only: timestamp the REAL pause calls on #player.
    const nativePause = HTMLMediaElement.prototype.pause;
    HTMLMediaElement.prototype.pause = function () {
        if (this.id === 'player') probe.pauses.push(performance.now());
        return nativePause.call(this);
    };
    document.addEventListener('DOMContentLoaded', () => {
        const player = document.getElementById('player');
        for (const type of ['playing', 'pause', 'ended']) {
            player.addEventListener(type, () => probe.events.push({type, t: performance.now()}));
        }
    });
    // Observation only: which /ask fetch signals really aborted.
    const nativeFetch = window.fetch.bind(window);
    window.fetch = (input, options) => {
        const path = new URL(typeof input === 'string' ? input : input.url, location.href).pathname;
        if (path === '/transcribe') probe.transcribes += 1;
        if (path === '/ask' && options && options.signal) {
            const index = probe.asks.length;
            probe.asks.push({index, aborted: false});
            options.signal.addEventListener('abort', () => {
                probe.asks[index].aborted = true;
                probe.abortedAsks.push({index, t: performance.now()});
            });
        }
        const pending = nativeFetch(input, options);
        if (path !== '/ask') return pending;
        return pending.then(response => {
            if (response.ok) probe.askResponses += 1;
            return response;
        });
    };
})();
"""


def barge_init(extra: str = "") -> str:
    return BARGE_INIT.replace("__EXTRA__", extra)


class BargeInApp(RealMediaApp):
    """RealMediaApp routes over real fake-capture, with barge-in probes.

    Static bytes, MP3 fixtures and media stay real; only the service
    endpoints are fulfilled. /ask, /transcribe and the speech/approval
    endpoints are recorded so the tests can prove which side effects a
    barge-in actually caused.
    """

    def __init__(self, context, extra_init=""):
        self.transcribes: list = []
        self.asks: list = []
        self.cancels: list = []
        self.approval_posts: list = []
        self.held_routes: list = []
        self.transcript_replies = [{"text": "Synthetic question"}]
        self.ask_responder = self._default_ask
        super().__init__(context, init=barge_init(extra_init))
        self.page.set_default_timeout(20000)

    @staticmethod
    def _default_ask(body):
        # Identified full-file turn: audio plays, speech id stays active.
        return {"answer": "A long spoken answer for the barge-in proof.",
                "audio_url": "/audio/long.mp3"}, False

    def load(self):
        self.page.goto(ORIGIN + "/")
        self.page.locator(MAIN + " .karaoke-chunk").first.wait_for(state="attached")
        self.page.locator("#call-btn").click()
        return self

    def emit_announcement(self, audio_url="/audio/long.mp3"):
        self.page.evaluate(
            """async ([audio]) => {
                const source = __barge.sources[0];
                source.emit({type: 'transition', pane_id: 'w1:p2', agent: 'claude',
                    status: 'blocked', label: 'Claude',
                    text: 'Claude is waiting for permission.', audio_url: audio});
            }""",
            [audio_url],
        )

    def route(self, route):
        request = route.request
        parsed = urlsplit(request.url)
        path, method = parsed.path, request.method
        query = set(parse_qs(parsed.query, keep_blank_values=True))
        if f"{parsed.scheme}://{parsed.netloc}" != ORIGIN:
            self.blocked.append(request.url)
            return route.abort("blockedbyclient")
        if method == "GET" and path in self.static and query <= {"v"}:
            file = self.static[path]
            mime = {".html": "text/html", ".js": "application/javascript",
                    ".svg": "image/svg+xml",
                    ".webmanifest": "application/manifest+json"}[file.suffix]
            return route.fulfill(body=file.read_bytes(), content_type=mime)
        if method == "GET" and path in self.api:
            payload, allowed_query = self.api[path]
            if query <= allowed_query:
                return route.fulfill(json=payload)
        if method == "GET" and not query and path in {"/audio/" + n for n in self.audio}:
            return self._serve_audio(route, request, path)
        if not query and method == "POST":
            if path == "/transcribe":
                # webm/opus bodies are binary: read the buffer, never text.
                self.transcribes.append(len(request.post_data_buffer or b""))
                reply = self.transcript_replies.pop(0) if self.transcript_replies else {"text": ""}
                return route.fulfill(json=reply)
            if path == "/ask":
                body = request.post_data_json
                # Responder FIRST: it distinguishes turns by ask count,
                # so the record must not include the request in flight.
                payload, hold = self.ask_responder(body)
                self.asks.append(body)
                if hold:
                    self.held_routes.append(route)
                    return
                return route.fulfill(json=payload)
            if path == "/tts":
                self.tts.append(request.post_data_json)
                return route.fulfill(json={"audio_url": "/audio/short.mp3"})
            if re.fullmatch(r"/speech/([^/]+)/cancel", path):
                self.cancels.append({"id": path.split("/")[2],
                                     "body": request.post_data_json})
                return route.fulfill(json={"status": "cancelled"})
            if re.fullmatch(r"/approval/[^/]+/(approve|reject|resolve|cancel)", path):
                self.approval_posts.append(path)
                return route.fulfill(json={"ok": True})
            if path == "/reset":
                return route.fulfill(json={"ok": True})
        self.blocked.append(request.url)
        route.abort("blockedbyclient")

    def _serve_audio(self, route, request, path):
        data = self.audio[path.removeprefix("/audio/")]
        headers = {"accept-ranges": "bytes", "content-type": "audio/mpeg"}
        byte_range = request.headers.get("range")
        if byte_range:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", byte_range)
            assert match and any(match.groups()), byte_range
            first, last = match.groups()
            start = int(first) if first else max(0, len(data) - int(last))
            end = min(int(last), len(data) - 1) if first and last else len(data) - 1
            if start > end:
                return route.fulfill(status=416, headers={"content-range": f"bytes */{len(data)}"})
            headers["content-range"] = f"bytes {start}-{end}/{len(data)}"
            return route.fulfill(status=206, body=data[start:end + 1], headers=headers)
        return route.fulfill(body=data, headers=headers)

    def wait_playing(self):
        self.page.wait_for_function(
            "() => __barge.events.some(e => e.type === 'playing') && "
            "!document.getElementById('player').paused"
        )

    def wait_pause(self):
        # The barge-in's src teardown (removeAttribute + load) swallows
        # the pending native 'pause' EVENT in Chromium, so the proof is
        # the REAL pause() call record plus the native paused state.
        self.page.wait_for_function(
            "() => __barge.pauses.length > 0 && document.getElementById('player').paused"
        )

    def probe(self):
        return self.page.evaluate(
            "() => ({events: __barge.events, pauses: __barge.pauses, frames: __barge.frames,"
            " asks: __barge.asks, abortedAsks: __barge.abortedAsks,"
            " pending: JSON.parse(localStorage.getItem('herdr.speech.pending.v1') || 'null')})"
        )


@pytest.fixture
def barge_app(audio_capture_context):
    app = BargeInApp(audio_capture_context)
    try:
        yield app
    finally:
        audio_capture_context.close()
        assert not app.blocked, "Unexpected traffic was blocked: " + repr(app.blocked)
        assert not app.errors, app.errors


def detection_frame(probe):
    """The barge-in detection frame: the LAST real analyser frame read
    before the player paused (the pause runs in that same tick)."""
    pause = probe["pauses"][0]
    frames = [f for f in probe["frames"] if f["t"] <= pause]
    assert frames, "no analyser frame preceded the pause"
    return frames[-1], pause


def test_fake_capture_track_reports_aec_enabled(audio_capture_context):
    """The premise of the AEC-gated monitor: this environment's REAL
    capture track reports echo cancellation enabled."""
    context = audio_capture_context
    # mediaDevices needs a secure context: serve a page at the loopback
    # origin (potentially trustworthy), never about:blank.
    context.route("**/*", lambda route: route.fulfill(
        content_type="text/html", body="<!doctype html><title>aec</title>"))
    page = context.new_page()
    try:
        page.goto(ORIGIN + "/")
        settings = page.evaluate("""async () => {
            const stream = await navigator.mediaDevices.getUserMedia({audio: {
                echoCancellation: true, noiseSuppression: true, autoGainControl: true
            }});
            const settings = stream.getAudioTracks()[0].getSettings();
            stream.getTracks().forEach(track => track.stop());
            return settings;
        }""")
        assert settings["echoCancellation"] is True
    finally:
        context.close()


def test_barge_in_pauses_playback_and_captures_new_utterance(barge_app):
    app = barge_app
    app.transcript_replies = [{"text": "Synthetic question"}, {"text": "Second question"}]

    def responder(body):
        if len(app.asks) == 0:
            return {"answer": "A long spoken answer for the barge-in proof.",
                    "audio_url": "/audio/long.mp3"}, False
        return {"answer": "ok"}, False  # the interrupting turn ends quietly

    app.ask_responder = responder
    app.load()
    app.wait_playing()

    # Barge-in: the interrupting speech pauses the REAL player...
    app.wait_pause()
    probe = app.probe()
    frame, pause = detection_frame(probe)
    assert frame["rms"] > 0.011  # the detection frame really carried speech
    assert pause - frame["t"] < 100  # pause within 100ms of detected speech
    playing = next(e for e in probe["events"] if e["type"] == "playing")
    assert frame["t"] > playing["t"]  # detection happened DURING playback

    # ...cancels the identified server speech job (token intact)...
    assert len(app.cancels) == 1
    assert app.cancels[0]["id"] == app.asks[0]["speech_request_id"]
    assert app.cancels[0]["body"]["speech_cancel_token"]
    # ...flushes the interrupted queue (no resume of the purged item)...
    app.page.wait_for_function(
        "() => document.getElementById('stop-audio').classList.contains('hidden')"
    )
    assert app.page.locator("#player").evaluate("p => p.paused")

    # ...and recovers the mic: the interrupting utterance is REALLY
    # captured (native recorder bytes) and dispatched as the next turn.
    app.page.wait_for_function("() => __barge.transcribes >= 2")
    # Fetch initiation can precede the Python route callback. A successful
    # second answer proves both service handlers recorded their requests.
    app.page.wait_for_function("() => __barge.askResponses >= 2")
    assert app.transcribes[1] > 1000  # real webm bytes, not a sliver
    app.page.wait_for_function("() => __barge.asks.length === 2")
    assert app.asks[1]["speech_request_id"] != app.asks[0]["speech_request_id"]


def test_barge_in_preserves_pending_approval_gate(barge_app):
    app = barge_app
    app.transcript_replies = [{"text": "Synthetic question"}, {"text": ""}]
    app.ask_responder = lambda body: (
        {"answer": "He preparado los cambios. ¿Se envía?",
         "audio_url": "/audio/long.mp3", "approval": GATE}, False
    )
    app.load()
    app.wait_playing()
    app.page.wait_for_function(
        "() => document.getElementById('approval-float').childElementCount > 0"
    )

    app.wait_pause()

    # S14: the interruption NEVER resolves or dismisses the gate — the
    # card stays live and not one approval endpoint was touched. (The
    # second transcribe answers empty, so no confirm-routing either.)
    assert app.approval_posts == []
    assert app.page.locator("#approval-float").evaluate("el => el.childElementCount") > 0
    # The interrupted speech job was still cancelled.
    assert len(app.cancels) == 1


def test_barge_in_aborts_inflight_ask_and_keeps_announcement_records(barge_app):
    app = barge_app
    app.transcript_replies = [{"text": "Synthetic question"}, {"text": "Second question"}]

    def responder(body):
        if len(app.asks) == 0:
            return {"answer": "First answer.", "audio_url": "/audio/long.mp3"}, False
        # A follow-up /ask stays in flight while the preceding answer is
        # still audible. Background announcements must NOT create playback.
        return {"answer": "held"}, True

    app.ask_responder = responder
    app.load()
    app.wait_playing()
    app.page.locator("#text-input").evaluate("input => { input.value = 'Follow-up question'; }")
    app.page.locator("#text-fallback").evaluate("form => form.requestSubmit()")
    app.page.wait_for_function("() => __barge.asks.length === 2")

    # S10: the announcement stays queued during the pending user turn.
    app.emit_announcement()
    assert len([event for event in app.probe()["events"] if event["type"] == "playing"]) == 1
    # User speech pauses the still-audible answer and aborts /ask #2
    # through its actual AbortController signal.
    app.wait_pause()
    probe = app.probe()
    assert probe["asks"][-1]["aborted"] is True
    assert len(app.cancels) == 1  # the active in-flight job is cancelled
    # The aborted turn degrades silently: no network-error banner.
    assert "Error de red" not in (app.page.locator("#banner").text_content() or "")
    # Ambient bookkeeping survives the interruption (records, not audio).
    records = (probe.get("pending") or {}).get("records") or []
    assert any(r.get("pane_id") == "w1:p2" for r in records)


NO_AEC_EXTRA = r"""
    const nativeSettings = MediaStreamTrack.prototype.getSettings;
    MediaStreamTrack.prototype.getSettings = function () {
        return Object.assign({}, nativeSettings.call(this), {echoCancellation: false});
    };
"""


def test_without_aec_report_the_call_stays_half_duplex(audio_capture_context):
    app = BargeInApp(audio_capture_context, extra_init=NO_AEC_EXTRA)
    try:
        app.load()
        app.wait_playing()
        # Voiced capture really crossed the speech floor during (or
        # right after) the playback window — and still nothing fired:
        # without an AEC report the monitor never arms, so the call
        # stays safe half-duplex.
        app.page.wait_for_function("""() => {
            const playingAt = __barge.events.find(e => e.type === 'playing');
            return playingAt && __barge.frames.some(f => f.t > playingAt.t && f.rms > 0.05);
        }""")
        app.page.wait_for_timeout(700)
        # No barge-in ever fired: no pause() call (the only way the app
        # pauses the player), no speech cancel, and the item was never
        # torn down — a barge-in clears the src, a natural end keeps it.
        assert app.page.evaluate("() => __barge.pauses.length") == 0
        assert app.cancels == []
        src = app.page.locator("#player").evaluate(
            "p => String(p.src || p.currentSrc)")
        assert "/audio/long.mp3" in src
    finally:
        audio_capture_context.close()
        assert not app.blocked, "Unexpected traffic was blocked: " + repr(app.blocked)
        assert not app.errors, app.errors
