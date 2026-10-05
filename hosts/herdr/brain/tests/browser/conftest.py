"""Isolated browser scope; never import the service or the parent conftest."""

import json
from pathlib import Path
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import BrowserContext, Page, Request, Route


RUNTIME = Path(__file__).resolve().parent / ".runtime"
STATIC = Path(__file__).resolve().parents[2] / "src/herdr_brain/static"
ORIGIN = "http://127.0.0.1:41731"


# The DOM, event listeners, application and module code remain real. Only
# device/service boundaries are doubled, before any application script runs.
INIT_SCRIPT = r"""
(() => {
    const harness = window.__karaokeHarness = {
        eventSources: [], recognitions: [], microphoneCalls: 0,
        ignoreTtsAbort: false, fetchCalls: []
    };
    class FixtureEvents extends EventTarget {
        emit(type, fields = {}) {
            const event = Object.assign(new Event(type), fields);
            const handler = this['on' + type];
            if (typeof handler === 'function') handler.call(this, event);
            this.dispatchEvent(event);
        }
    }
    class FixtureEventSource extends FixtureEvents {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSED = 2;
        constructor(url) {
            super();
            this.url = new URL(url, location.href).href;
            this.readyState = 1;
            harness.eventSources.push(this);
        }
        close() { this.readyState = 2; }
    }
    class FixtureRecognition extends FixtureEvents {
        constructor() { super(); harness.recognitions.push(this); }
        start() { this.emit('start'); }
        stop() { this.emit('end'); }
        abort() { this.stop(); }
    }
    window.EventSource = FixtureEventSource;
    window.SpeechRecognition = window.webkitSpeechRecognition = FixtureRecognition;
    if (navigator.serviceWorker) {
        Object.defineProperty(navigator.serviceWorker, 'register', {configurable: true, value: () =>
            Promise.reject(new DOMException('Service workers are blocked in this harness', 'SecurityError'))});
    }
    const devices = navigator.mediaDevices || {};
    Object.defineProperty(devices, 'getUserMedia', {configurable: true, value: () => {
        harness.microphoneCalls++;
        return Promise.reject(new DOMException('No physical microphone in this harness', 'NotAllowedError'));
    }});
    if (!navigator.mediaDevices) {
        Object.defineProperty(navigator, 'mediaDevices', {value: devices});
    }
    const nativeFetch = window.fetch.bind(window);
    window.fetch = (input, options) => {
        const path = new URL(typeof input === 'string' ? input : input.url, location.href).pathname;
        harness.fetchCalls.push(path);
        // Explicit race hook: deliver a held /tts result even if abort loses.
        if (path === '/tts' && harness.ignoreTtsAbort && options) {
            options = {...options};
            delete options.signal;
        }
        return nativeFetch(input, options);
    };

    const slots = new WeakMap();
    const pending = new Map();
    let nextGeneration = 0;
    let nextPlay = 0;
    function fresh(src) {
        return {src: src ? new URL(src, document.baseURI).href : '', rawSrc: src,
                generation: ++nextGeneration, duration: NaN, currentTime: 0,
                paused: true, ended: false, readyState: 0, error: null};
    }
    function slot(element) {
        if (!slots.has(element)) {
            const current = fresh('');
            slots.set(element, {current, sources: new Map([[current.generation, current]]),
                view: null, plays: [], pauseCalls: 0, loadCalls: 0,
                outcomes: {}, events: [], playQueue: []});
        }
        return slots.get(element);
    }
    function state(element) { const s = slot(element); return s.view || s.current; }
    function source(element, src) {
        const s = slot(element);
        s.current = fresh(String(src || ''));
        s.sources.set(s.current.generation, s.current);
    }
    function token(s) { return {src: s.src, generation: s.generation}; }
    function numeric(value) {
        if (value === 'unknown' || value === 'nan') return NaN;
        if (value === 'infinity') return Infinity;
        return Number(value);
    }
    function wireNumber(value) { return Number.isFinite(value) ? value : String(value); }
    function emit(element, type, options = {}) {
        const s = slot(element);
        const selected = options.source ? s.sources.get(options.source.generation) : s.current;
        if (!selected || (options.source && options.source.src !== selected.src)) {
            throw new Error('Unknown media source token');
        }
        if ('duration' in options) selected.duration = numeric(options.duration);
        if ('time' in options) selected.currentTime = numeric(options.time);
        if (type === 'loadedmetadata') selected.readyState = 1;
        if (type === 'ended') { selected.ended = true; selected.paused = true; }
        if (type === 'error') selected.error = {code: 4, message: 'Simulated media failure'};
        const previousView = s.view;
        s.view = selected;
        s.events.push({type, ...token(selected)});
        try {
            // A late source's event temporarily exposes that source's native
            // media properties; it never overwrites the replacement source.
            element.dispatchEvent(new Event(type));
        } finally { s.view = previousView; }
    }
    const proto = HTMLMediaElement.prototype;
    Object.defineProperty(proto, 'src', {configurable: true,
        get() { return state(this).src; }, set(value) { source(this, value); }});
    Object.defineProperty(proto, 'currentSrc', {configurable: true,
        get() { return state(this).src; }});
    Object.defineProperty(proto, 'currentTime', {configurable: true,
        get() { return state(this).currentTime; },
        set(value) { state(this).currentTime = numeric(value); }});
    for (const key of ['duration', 'paused', 'ended', 'readyState', 'error']) {
        Object.defineProperty(proto, key, {configurable: true, get() { return state(this)[key]; }});
    }
    const nativeGet = Element.prototype.getAttribute;
    const nativeSet = Element.prototype.setAttribute;
    const nativeRemove = Element.prototype.removeAttribute;
    proto.getAttribute = function(name) {
        return name.toLowerCase() === 'src' ? (state(this).rawSrc || null) : nativeGet.call(this, name);
    };
    proto.setAttribute = function(name, value) {
        if (name.toLowerCase() === 'src') source(this, value);
        else nativeSet.call(this, name, value);
    };
    proto.removeAttribute = function(name) {
        if (name.toLowerCase() === 'src') source(this, '');
        else nativeRemove.call(this, name);
    };
    proto.load = function() {
        slot(this).loadCalls++;
        source(this, state(this).rawSrc);
    };
    proto.pause = function() {
        const s = slot(this);
        s.pauseCalls++;
        if (!s.current.paused) { s.current.paused = true; emit(this, 'pause'); }
    };
    proto.play = function() {
        const element = this;
        const s = slot(element);
        const started = s.current;
        const id = ++nextPlay;
        const outcome = s.playQueue.shift() || {outcome: 'resolve'};
        s.plays.push({id, ...token(started)});
        started.paused = false;
        started.ended = false;
        emit(element, 'play');
        return new Promise((resolve, reject) => {
            function finish(error) {
                pending.delete(id);
                s.outcomes[id] = error ? 'rejected' : 'resolved';
                if (error) {
                    started.paused = true;
                    reject(new DOMException('Simulated play rejection', error));
                } else {
                    if (s.current === started) emit(element, 'playing');
                    resolve();
                }
            }
            if (outcome.outcome === 'defer') pending.set(id, {element, finish});
            else queueMicrotask(() => finish(outcome.outcome === 'reject' ? outcome.error || 'NotAllowedError' : null));
        });
    };
    // Audio() still returns a real HTMLAudioElement, without native source IO.
    window.Audio = function(src) {
        const audio = document.createElement('audio');
        if (src) audio.src = src;
        return audio;
    };
    window.Audio.prototype = HTMLAudioElement.prototype;
    const player = () => document.getElementById('player');
    harness.media = {
        source: ({src}) => { player().src = src; return token(state(player())); },
        capture: () => token(state(player())),
        metadata: (options) => { emit(player(), 'loadedmetadata', options); emit(player(), 'durationchange', options); },
        advance: (options) => emit(player(), 'timeupdate', options),
        emit: ({type, ...options}) => emit(player(), type, options),
        queuePlay: (options) => slot(player()).playQueue.push(options),
        settlePlay: ({id, error}) => {
            if (!pending.has(id)) throw new Error('Unknown pending play');
            pending.get(id).finish(error);
        },
        play: () => { player().play().catch(() => {}); return slot(player()).plays.at(-1).id; },
        pause: () => player().pause(),
        end: () => emit(player(), 'ended'),
        error: () => emit(player(), 'error'),
        snapshot: () => {
            const s = slot(player()), current = s.current;
            return {...token(current), duration: wireNumber(current.duration),
                currentTime: current.currentTime, paused: current.paused, ended: current.ended,
                playCalls: s.plays.length, plays: s.plays, pauseCalls: s.pauseCalls,
                loadCalls: s.loadCalls, events: s.events, outcomes: s.outcomes,
                pendingPlays: [...pending].filter(([, p]) => p.element === player()).map(([id]) => id)};
        }
    };
})();
"""


@dataclass
class TtsReply:
    """One queued /tts response, optionally held until release() is called."""

    payload: dict
    status: int = 200
    deferred: bool = False
    request: Request | None = None
    route: Route | None = None

    def release(self):
        assert self.route is not None, "Wait for this /tts request before releasing it"
        route, self.route = self.route, None
        route.fulfill(status=self.status, json=self.payload)


class KaraokeHarness:
    """Route-only application with explicit response and media race controls.

    All data is synthetic. respond() adds an exact same-origin path, not a
    wildcard; queued /tts responses have no provider or audio decoder behind
    them. media() dispatches native-shaped events on the actual #player.
    """

    origin = ORIGIN
    answer_text = "A synthetic Brain answer for the isolated browser harness."

    def __init__(self, context: BrowserContext, artifact_dir: Path, history=None, external=None):
        self.context = context
        self.artifact_dir = artifact_dir
        self.requests = []
        self.blocked_requests = []
        self.expected_blocks = []
        self.page_errors = []
        self.static_responses = {}
        self.tts_replies = []
        self.api = {}
        self.static = {"/": "index.html", "/index.html": "index.html"}
        for name in (
            "app.js", "endpointing.js", "vad.js", "approval.js", "reader.js",
            "toast.js", "announce.js", "speech.js", "consult.js", "sw.js",
            "manifest.webmanifest", "icon.svg", "karaoke.js",
        ):
            if (STATIC / name).is_file():
                self.static["/" + name] = name
        if history is None:
            history = [
                {"role": "user", "text": "A synthetic question.", "ts": "2026-01-01T00:00:00Z"},
                {"role": "brain", "text": self.answer_text, "ts": "2026-01-01T00:00:01Z"},
            ]
        self.respond("/call-history", {"turns": history, "has_more": False}, query=("before", "limit"))
        self.respond("/herd", [])
        self.respond("/view", {"status": {"active": False}, "screen": None,
                               "transcript": None, "pending": {"detected": False}}, query=("pane_id",))
        self.respond("/conversation", {"pane_id": None, "session_id": None, "agent": None,
                                       "turns": external or [], "window": 20}, query=("pane_id",))
        self.respond("/approval/current", {"approval": None}, query=("session_id",))
        self.respond("/reset", {"ok": True}, method="POST")
        context.route("**/*", self._route)
        context.route_web_socket("**/*", self._block_socket)
        context.add_init_script(INIT_SCRIPT)
        self.page = context.new_page()
        self.page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        self.page.on("response", self._response)

    def respond(self, path, payload, *, method="GET", status=200, query=()):
        assert path.startswith("/") and not path.startswith("//")
        assert urlsplit(path).path == path and "#" not in path
        self.api[(method, path)] = (payload, status, set(query))

    def queue_tts(self, *, audio_url=None, payload=None, status=200, defer=False):
        if payload is None:
            payload = {"audio_url": audio_url or f"/audio/fixture-{len(self.tts_replies)}.mp3"}
        reply = TtsReply(payload=payload, status=status, deferred=defer)
        self.tts_replies.append(reply)
        return reply

    def _block(self, route):
        self.blocked_requests.append({"method": route.request.method, "url": route.request.url})
        route.abort("blockedbyclient")

    def _block_socket(self, route):
        self.blocked_requests.append({"method": "WEBSOCKET", "url": route.url})
        route.close()

    def _route(self, route):
        request = route.request
        self.requests.append(request)
        parsed = urlsplit(request.url)
        if f"{parsed.scheme}://{parsed.netloc}" != self.origin:
            return self._block(route)
        path, method = parsed.path, request.method
        query = set(parse_qs(parsed.query, keep_blank_values=True))
        if method == "GET" and path in self.static and query <= {"v"}:
            name = self.static[path]
            content_type = {".html": "text/html", ".js": "application/javascript",
                            ".svg": "image/svg+xml", ".webmanifest": "application/manifest+json"}[Path(name).suffix]
            return route.fulfill(body=(STATIC / name).read_bytes(), content_type=content_type)
        if (method, path) in self.api:
            payload, status, allowed_query = self.api[(method, path)]
            if query <= allowed_query:
                return route.fulfill(status=status, json=payload)
        if method == "POST" and path == "/tts" and not query:
            reply = next((r for r in self.tts_replies if r.request is None), None)
            if reply is not None:
                reply.request, reply.route = request, route
                if not reply.deferred:
                    reply.release()
                return
        self._block(route)

    def _response(self, response):
        parsed = urlsplit(response.url)
        if f"{parsed.scheme}://{parsed.netloc}" == self.origin and parsed.path in self.static:
            self.static_responses[parsed.path] = response

    def load(self):
        self.page.goto(self.origin + "/", wait_until="load")
        self.page.wait_for_function("() => __karaokeHarness.fetchCalls.includes('/call-history')")
        return self

    def media(self, method, **options):
        return self.page.evaluate(
            "([method, options]) => __karaokeHarness.media[method](options)", [method, options]
        )

    def ignore_fetch_abort(self, enabled=True):
        self.page.evaluate("enabled => { __karaokeHarness.ignoreTtsAbort = enabled; }", enabled)

    def event(self, payload, source=0):
        self.page.evaluate(
            "([payload, index]) => __karaokeHarness.eventSources[index].emit('message', {data: JSON.stringify(payload)})",
            [payload, source],
        )

    def expect_blocked(self, url, *, method="GET"):
        """Declare an intentional negative probe; all other blocked traffic fails."""
        self.expected_blocks.append({"method": method, "url": url})

    def close(self):
        try:
            if not self.page.is_closed():
                self.page.evaluate("() => __karaokeHarness.eventSources.forEach(source => source.close())")
        finally:
            # Closing cancels held routes and destroys in-memory storage/media.
            self.context.close()


def pytest_configure(config):
    RUNTIME.mkdir(exist_ok=True)
    config._karaoke_output = Path(tempfile.mkdtemp(prefix="run-", dir=RUNTIME))
    config.option.output = str(config._karaoke_output)


@pytest.fixture(scope="session", autouse=True)
def delete_output_dir():
    """Override pytest-playwright's destructive autouse output cleanup."""


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args, pytestconfig):
    return {
        **browser_type_launch_args,
        "downloads_path": str(pytestconfig._karaoke_output / "downloads"),
        "traces_dir": str(pytestconfig._karaoke_output / "traces"),
    }


def pytest_terminal_summary(terminalreporter, config):
    versions = getattr(config, "_karaoke_versions", None)
    if versions:
        terminalreporter.write_line("Harness runtime versions: " + json.dumps(versions))


@pytest.fixture
def karaoke_app(browser, pytestconfig):
    """Factory for T-03's inline synthetic history/response scenarios."""
    harnesses = []

    with ExitStack() as cleanup:
        def create(*, history=None, external=None):
            context = browser.new_context(
                service_workers="block", permissions=[], accept_downloads=False
            )
            # Register immediately so partial fixture setup cannot leak a context.
            cleanup.callback(context.close)
            artifact_dir = Path(tempfile.mkdtemp(prefix="case-", dir=pytestconfig._karaoke_output))
            harness = KaraokeHarness(context, artifact_dir, history, external)
            cleanup.callback(harness.close)
            harnesses.append(harness)
            return harness

        yield create
    for harness in harnesses:
        assert harness.blocked_requests == harness.expected_blocks, "Undeclared traffic was blocked"
        assert not harness.page_errors, harness.page_errors


@pytest.fixture
def karaoke_harness(karaoke_app):
    return karaoke_app().load()
