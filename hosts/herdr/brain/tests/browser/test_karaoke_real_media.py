"""Real Chromium/MP3 regression proof, with no service or simulated media.

Route fulfillment uses only committed fixtures. The play probe delegates to
native media except for explicitly requested failure cases; duration, seeking,
decoding and media events are never synthesized. This is not physical UAT.
"""

from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, expect


ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "src/herdr_brain/static"
FIXTURES = ROOT / "tests/e2e/fixtures"
ORIGIN = "http://127.0.0.1:41731"
MAIN = "#conversation .turn.brain"
ANSWER = (
    "The first sentence contains enough distinct words for several reading windows. "
    "The second sentence continues the answer with more useful details to read aloud. "
    "The third sentence finishes this synthetic answer without any private chat data."
)

INIT = r"""
(() => {
    const probe = window.__realMedia = {events: [], sources: [], nextPlayFailure: null};
    class FixtureEventSource extends EventTarget {
        constructor() { super(); this.readyState = 1; probe.sources.push(this); }
        close() { this.readyState = 2; }
        emit(payload) {
            if (this.onmessage) this.onmessage({data: JSON.stringify(payload)});
        }
    }
    window.EventSource = FixtureEventSource;
    window.SpeechRecognition = window.webkitSpeechRecognition = class {
        start() {} stop() {} abort() {}
    };
    localStorage.setItem('herdr-brain-voice-engine', 'navegador');
    const devices = navigator.mediaDevices || {};
    Object.defineProperty(devices, 'getUserMedia', {configurable: true, value: () =>
        Promise.reject(new DOMException('No physical microphone', 'NotAllowedError'))});
    if (!navigator.mediaDevices) Object.defineProperty(navigator, 'mediaDevices', {value: devices});
    if (navigator.serviceWorker) Object.defineProperty(navigator.serviceWorker, 'register', {
        configurable: true, value: () => Promise.reject(new DOMException('Blocked', 'SecurityError'))
    });
    function record(player, type) {
        probe.events.push({type, src: player.currentSrc || player.src,
            time: player.currentTime, muted: player.muted, paused: player.paused,
            duration: Number.isFinite(player.duration) ? player.duration : String(player.duration),
            readyState: player.readyState});
    }
    const nativePlay = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function() {
        if (this.id === 'player') {
            record(this, 'play-call');
            const failure = probe.nextPlayFailure;
            probe.nextPlayFailure = null;
            if (failure === 'throw') throw new DOMException('Probe failure', 'NotAllowedError');
            if (failure === 'reject') return Promise.reject(new DOMException('Probe failure', 'NotAllowedError'));
        }
        return nativePlay.call(this);
    };
    document.addEventListener('DOMContentLoaded', () => {
        const player = document.getElementById('player');
        for (const type of ['loadedmetadata', 'durationchange', 'playing', 'timeupdate',
                            'seeking', 'seeked', 'pause', 'ended', 'error']) {
            player.addEventListener(type, () => record(player, type));
        }
    });
})();
"""


class RealMediaApp:
    def __init__(self, context):
        self.context = context
        self.blocked = []
        self.errors = []
        self.tts = []
        self.jobs = set()
        self.audio_name = "long.mp3"
        self.hold_audio = False
        self.held_audio = []
        self.audio = {name: (FIXTURES / name).read_bytes()
                      for name in ("short.mp3", "sample.mp3", "long.mp3")}
        names = ("index.html", "app.js", "karaoke.js", "toast.js", "reader.js", "speech.js",
                 "announce.js", "approval.js", "consult.js", "endpointing.js", "vad.js",
                 "manifest.webmanifest", "icon.svg")
        self.static = {"/" + name: STATIC / name for name in names}
        self.static["/"] = STATIC / "index.html"
        self.api = {
            "/call-history": ({"turns": [
                {"role": "user", "text": "Synthetic question", "ts": "2026-01-01T00:00:00Z"},
                {"role": "brain", "text": ANSWER, "ts": "2026-01-01T00:00:01Z"},
            ], "has_more": False}, {"before", "limit"}),
            "/herd": ([], set()),
            "/view": ({"status": {"active": False}, "screen": None, "transcript": None,
                       "pending": {"detected": False}}, {"pane_id"}),
            "/conversation": ({"pane_id": None, "session_id": None, "agent": None,
                               "turns": [{"role": "assistant", "text": "External replay fixture"}],
                               "window": 20}, {"pane_id"}),
            "/approval/current": ({"approval": None}, {"session_id"}),
        }
        context.route("**/*", self.route)
        context.route_web_socket("**/*", self.block_socket)
        context.add_init_script(INIT)
        self.page = context.new_page()
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.set_default_timeout(5000)
        self.page.set_viewport_size({"width": 1000, "height": 1000})

    def block_socket(self, route):
        self.blocked.append(route.url)
        route.close()

    def route(self, route):
        request = route.request
        parsed = urlsplit(request.url)
        path, method = parsed.path, request.method
        query = set(parse_qs(parsed.query, keep_blank_values=True))
        if f"{parsed.scheme}://{parsed.netloc}" == ORIGIN:
            if method == "GET" and path in self.static and query <= {"v"}:
                file = self.static[path]
                mime = {".html": "text/html", ".js": "application/javascript", ".svg": "image/svg+xml",
                        ".webmanifest": "application/manifest+json"}[file.suffix]
                return route.fulfill(body=file.read_bytes(), content_type=mime)
            if method == "GET" and path in self.api:
                payload, allowed_query = self.api[path]
                if query <= allowed_query:
                    return route.fulfill(json=payload)
            if not query and method == "POST":
                if path == "/tts":
                    self.tts.append(request.post_data_json)
                    return route.fulfill(json={"audio_url": "/audio/" + self.audio_name})
                if path == "/ask":
                    self.jobs.add(request.post_data_json["speech_request_id"])
                    return route.fulfill(json={"answer": ANSWER, "audio_url": "/audio/sample.mp3"})
                if path == "/reset" or path in {"/speech/" + job + "/cancel" for job in self.jobs}:
                    return route.fulfill(json={"status": "cancelled", "ok": True})
            if method == "GET" and not query and path in {"/audio/" + name for name in self.audio}:
                if self.hold_audio:
                    self.held_audio.append(route)
                    return
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
        self.blocked.append(request.url)
        route.abort("blockedbyclient")

    def load(self, preload=None):
        self.page.goto(ORIGIN + "/")
        self.page.locator(MAIN + " .karaoke-chunk").first.wait_for(state="attached")
        if preload is not None:
            self.page.locator("#player").evaluate("(player, value) => { player.preload = value; }", preload)
        self.page.locator("#call-btn").click()
        return self

    def snapshot(self):
        return self.page.locator("#player").evaluate("""p => ({paused: p.paused, time: p.currentTime,
            duration: Number.isFinite(p.duration) ? p.duration : String(p.duration),
            readyState: p.readyState, muted: p.muted, error: p.error && p.error.code,
            src: p.currentSrc || p.src, events: __realMedia.events})""")

    def click(self, index):
        self.page.locator(MAIN + " .karaoke-chunk").first.wait_for()
        self.page.locator(MAIN).first.locator(".karaoke-chunk").nth(index).click()


@pytest.fixture(scope="module")
def real_media_browser(browser_type, pytestconfig):
    assert browser_type.name == "chromium", "This suite requires real Chromium MP3 decoding"
    browser = browser_type.launch(
        args=["--autoplay-policy=document-user-activation-required"],
        downloads_path=str(pytestconfig._karaoke_output / "real-downloads"),
        traces_dir=str(pytestconfig._karaoke_output / "real-traces"),
    )
    yield browser
    browser.close()


@pytest.fixture
def real_media_app(real_media_browser):
    context = real_media_browser.new_context(service_workers="block", accept_downloads=False, permissions=[])
    app = RealMediaApp(context)
    try:
        yield app
    finally:
        context.close()
        assert not app.blocked, "Unexpected traffic was blocked: " + repr(app.blocked)
        assert not app.errors, app.errors


def playing(app, index):
    try:
        app.page.wait_for_function("""() => {
            const p = document.getElementById('player');
            return !p.paused && !p.muted && p.readyState >= 2 && Number.isFinite(p.duration) && p.duration > 0;
        }""", timeout=5000)
    except PlaywrightTimeoutError:
        pytest.fail("Replay did not start: " + repr(app.snapshot()))
    snapshot = app.snapshot()
    count = app.page.locator(MAIN).first.locator(".karaoke-chunk").count()
    calls = [event for event in snapshot["events"] if event["type"] == "play-call" and not event["muted"]]
    assert calls[-1]["time"] == pytest.approx(index / count * snapshot["duration"], abs=0.03)
    expect(app.page.locator(".karaoke-active")).to_have_count(1)
    expect(app.page.locator(MAIN).first.locator(".karaoke-chunk").nth(index)).to_have_class(
        "karaoke-chunk karaoke-active"
    )
    app.page.wait_for_function("""time => {
        const p = document.getElementById('player'); return !p.paused && p.currentTime > time + 0.08;
    }""", arg=snapshot["time"], timeout=3000)
    return snapshot


def automatic(app):
    app.page.locator("#text-input").evaluate("input => { input.value = 'Synthetic automatic question'; }")
    app.page.locator("#text-fallback").evaluate("form => form.requestSubmit()")
    app.page.wait_for_function("""() => {
        const p = document.getElementById('player');
        return p.currentSrc.includes('/sample.mp3') && !p.paused && !p.muted && p.currentTime > 0.08;
    }""")


@pytest.mark.parametrize("preload", [None, "none"], ids=["default", "deferred"])
def test_click_seeks_and_advances_real_audio(real_media_app, preload):
    app = real_media_app.load(preload)
    app.click(2)
    playing(app, 2)
    assert len(app.tts) == 1


def test_no_audible_playback_before_proportional_seek(real_media_app):
    app = real_media_app.load("none")
    app.click(2)
    snapshot = playing(app, 2)
    calls = [event for event in snapshot["events"] if event["type"] == "play-call"]
    assert len(calls) == 2
    assert calls[0]["muted"] is True and calls[0]["time"] == 0
    assert calls[1]["muted"] is False and calls[1]["time"] > 0
    app.page.wait_for_function("() => __realMedia.events.some(e => e.type === 'timeupdate' && !e.muted)")
    audible = [event for event in app.snapshot()["events"]
               if event["type"] in ("playing", "timeupdate") and not event["muted"]]
    assert audible and all(event["time"] >= calls[1]["time"] - 0.03 for event in audible)


@pytest.mark.parametrize("state", ["playing", "ended", "paused"])
def test_click_again_while_playing_after_end_or_pause(real_media_app, state):
    app = real_media_app.load()
    app.click(2)
    playing(app, 2)
    initial_calls = len([event for event in app.snapshot()["events"] if event["type"] == "play-call"])
    if state == "ended":
        app.page.locator("#player").evaluate("p => { p.currentTime = p.duration - 0.12; }")
        expect(app.page.locator(".karaoke-active")).to_have_count(0)
        app.page.wait_for_function("() => __realMedia.events.some(e => e.type === 'ended')")
    elif state == "paused":
        app.page.locator("#player").evaluate("p => p.pause()")
        app.page.wait_for_function("() => document.getElementById('player').paused")
    app.click(1)
    playing(app, 1)
    if state != "ended":
        calls = [event for event in app.snapshot()["events"] if event["type"] == "play-call"]
        assert len(calls) == initial_calls + 1, "Reusable metadata needs only post-seek playback"
        assert len(app.tts) == 1


@pytest.mark.parametrize("action", ["stop", "end", "detach"])
def test_cleanup_unmutes_legacy_replay_and_automatic_audio(real_media_app, action):
    app = real_media_app.load()
    if action == "end":
        app.audio_name = "short.mp3"
        app.click(0)
        app.page.wait_for_function("() => __realMedia.events.some(e => e.type === 'ended')")
    else:
        app.click(2)
        playing(app, 2)
        if action == "stop":
            app.page.locator("#stop-audio").click()
        else:
            app.page.locator(MAIN).first.evaluate("turn => turn.remove()")
    expect(app.page.locator(".karaoke-active")).to_have_count(0)
    app.page.wait_for_function("() => document.getElementById('player').paused && !document.getElementById('player').muted")
    app.audio_name = "sample.mp3"
    app.page.locator("#glance-turns .replay-btn").evaluate("button => button.click()")
    app.page.wait_for_function("""() => {
        const p = document.getElementById('player');
        return p.currentSrc.includes('/sample.mp3') && !p.paused && !p.muted && p.currentTime > 0.08;
    }""")
    app.page.locator("#stop-audio").click()
    automatic(app)
    assert app.snapshot()["muted"] is False


@pytest.mark.parametrize("action", ["stop", "detach"])
def test_retirement_during_muted_loading_restores_shared_player(real_media_app, action):
    app = real_media_app.load("none")
    app.hold_audio = True
    app.click(2)
    app.page.wait_for_function("() => __realMedia.events.some(e => e.type === 'play-call' && e.muted)")
    assert app.snapshot()["muted"] is True
    assert app.snapshot()["duration"] == "NaN"
    if action == "stop":
        app.page.locator("#stop-audio").click()
    else:
        app.page.locator(MAIN).first.evaluate("turn => turn.remove()")
    expect(app.page.locator(".karaoke-active")).to_have_count(0)
    app.page.wait_for_function("() => !document.getElementById('player').muted")
    app.hold_audio = False
    automatic(app)
    assert "No pude" not in app.page.locator("#banner").text_content()


@pytest.mark.parametrize("failure", ["reject", "throw"])
def test_loading_play_failure_is_visible_and_unmutes(real_media_app, failure):
    app = real_media_app.load("none")
    app.page.evaluate("failure => { __realMedia.nextPlayFailure = failure; }", failure)
    app.click(2)
    expect(app.page.locator("#banner")).to_contain_text("parte 1")
    expect(app.page.locator(".karaoke-active")).to_have_count(0)
    app.page.wait_for_function("() => document.getElementById('player').paused && !document.getElementById('player').muted")
    automatic(app)
