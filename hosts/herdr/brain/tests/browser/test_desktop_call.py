"""Desktop voice-call entry and phone/laptop shared-context handover.

FR-12/S12: the desktop is ANOTHER interface to the same call. A
desktop-class Chromium with the existing fake-capture flags loads the
real PWA, adopts the server's shared call identity, and runs the real
mic -> VAD -> /transcribe -> /ask -> native playback loop.

FR-13/S13: two independent browser contexts (phone and laptop) talk to
ONE shared service state — the same call id, the same pane selection,
the same pending gate. Nothing is proven by giving the two pages
coincidentally equal fixtures: every shared value observed on B was
written by A's real traffic. The S14 re-explain decision itself is
proved against the real server in tests/test_call_context.py; here the
handover proves the CLIENT side — B recovers A's gate and presents NO
playback receipt («adelante» leaves with speech_request_id null).
"""

from __future__ import annotations

import json
import math
import struct
import wave
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, Route

STATIC = Path(__file__).resolve().parents[2] / "src/herdr_brain/static"
FIXTURES = Path(__file__).resolve().parents[1] / "e2e/fixtures"
ORIGIN = "http://127.0.0.1:41731"

STATIC_FILES = {"/": "index.html", "/index.html": "index.html"}
for _name in (
    "app.js", "endpointing.js", "vad.js", "approval.js", "reader.js",
    "toast.js", "announce.js", "speech.js", "consult.js",
    "herd-group.js", "sw.js", "manifest.webmanifest", "icon.svg",
    "karaoke.js", "pairing.js", "destructive.js", "history.js", "dialogs.js",
    "receipts.js", "push.js",
):
    if (STATIC / _name).is_file():
        STATIC_FILES["/" + _name] = _name
CONTENT_TYPES = {
    ".html": "text/html", ".js": "application/javascript",
    ".svg": "image/svg+xml", ".webmanifest": "application/manifest+json",
}

HERD = [
    dict(pane_id="w1:p9", agent="opencode", agent_status="idle", title="OpenCode",
         cwd="/repo/open", session_id="ses-a", focused=False, last_turn=None),
    dict(pane_id="w2:p1", agent="claude", agent_status="idle", title="Refactor",
         cwd="/repo/ref", session_id="ses-b", focused=True, last_turn=None),
]

GATE_PAYLOAD = {
    "gate_id": "g-1", "tool": "send_to_session", "pane_id": "w1:p9",
    "agent": "opencode", "text": "envía el gated resumen",
    "timeout_ms": 300000, "expires_in_s": 300,
}


class SharedCallService:
    """One call's server-side state, shared by every browser context."""

    def __init__(self, *, gate_on_ask: bool = False):
        self.call_id = "default"
        self.pane_id = None
        self.revision = 0
        self.gate_on_ask = gate_on_ask
        self.gate: dict | None = None
        self.turns: list[dict] = []
        self.asks: list[dict] = []
        self.resolves: list[dict] = []
        self.selects: list[dict] = []
        self.views: list[str] = []
        self.approval_reads: list[str] = []

    def snapshot(self) -> dict:
        return {"call_id": self.call_id, "pane_id": self.pane_id, "revision": self.revision}

    def select(self, pane_id) -> dict:
        self.selects.append({"pane_id": pane_id})
        self.pane_id = pane_id or None
        self.revision += 1
        return self.snapshot()

    def handle_ask(self, body: dict) -> dict:
        self.asks.append(body)
        self.turns.append({"role": "user", "text": body.get("text", ""), "ts": "2026-10-22T10:00:00Z"})
        speech_id = (body.get("speech") or {}).get("speech_request_id") or "srv-1"
        response = {
            "answer": "Resumen listo. Te lo leo.",
            "pane_id": body.get("pane_id"),
            "agent": "opencode",
            "session_id": self.call_id,
            "audio_url": "/audio/answer.mp3",
            "speech": {"id": speech_id, "status": "complete"},
            "approval": None,
        }
        self.turns.append({"role": "assistant", "text": response["answer"], "ts": "2026-10-22T10:00:01Z"})
        if self.gate_on_ask and self.gate is None:
            self.gate = dict(GATE_PAYLOAD)
            response["approval"] = self.gate
        return response


def make_route(
    service: SharedCallService, utterances: list[str], blocked: list[dict]
):
    """Exact-path routes for one client; everything else is blocked."""

    def pane_or_focused(query: dict) -> str | None:
        requested = (query.get("pane_id") or [None])[0]
        if requested in {a["pane_id"] for a in HERD}:
            return requested
        return service.pane_id or "w2:p1"

    def handler(route: Route):
        request = route.request
        parsed = urlsplit(request.url)
        if f"{parsed.scheme}://{parsed.netloc}" != ORIGIN:
            blocked.append({"method": request.method, "url": request.url})
            route.abort("blockedbyclient")
            return
        path, method = parsed.path, request.method
        query = parse_qs(parsed.query, keep_blank_values=True)
        if method == "GET" and path in STATIC_FILES and set(query) <= {"v"}:
            name = STATIC_FILES[path]
            return route.fulfill(
                body=(STATIC / name).read_bytes(),
                content_type=CONTENT_TYPES[Path(name).suffix],
            )
        if method == "GET" and path == "/events":
            return route.fulfill(
                status=200, headers={"content-type": "text/event-stream"},
                body=": connected\n\n",
            )
        if method == "GET" and path == "/devices":
            # Device pairing boot poll (F1): unpaired default keeps the
            # pre-F1 stance.
            return route.fulfill(
                status=200,
                json={"enforced": False, "current": None, "devices": []},
            )
        if method == "GET" and path == "/call-history" and set(query) <= {"before", "limit"}:
            return route.fulfill(json={"turns": service.turns, "has_more": False})
        if method == "GET" and path == "/herd":
            return route.fulfill(json=HERD)
        if method == "GET" and path == "/view" and set(query) <= {"pane_id"}:
            pane = pane_or_focused(query)
            service.views.append(pane)
            return route.fulfill(json={
                "status": {"active": True, "pane_id": pane, "agent": "opencode",
                           "agent_status": "idle"},
                "screen": None, "transcript": None, "screen_runs": None,
                "dialog": None, "pending": {"detected": False, "kind": None, "excerpt": None},
            })
        if method == "GET" and path == "/conversation" and set(query) <= {"pane_id"}:
            pane = pane_or_focused(query)
            return route.fulfill(json={
                "pane_id": pane, "session_id": "ses-" + (pane or "none"),
                "agent": "opencode", "turns": [], "window": 20,
            })
        if method == "GET" and path.startswith("/conversation/") and path.endswith("/rendered"):
            from urllib.parse import unquote

            pane = unquote(path[len("/conversation/"):-len("/rendered")])
            if pane in {a["pane_id"] for a in HERD}:
                return route.fulfill(json={
                    "pane_id": pane, "session_id": "ses-" + pane, "turns": [],
                })
        if method == "GET" and path == "/call/context":
            return route.fulfill(json=service.snapshot())
        if method == "POST" and path == "/call/context":
            body = request.post_data_json
            return route.fulfill(json=service.select(body.get("pane_id") if body else None))
        if method == "GET" and path == "/approval/current" and set(query) <= {"session_id"}:
            session = (query.get("session_id") or [None])[0]
            service.approval_reads.append(session)
            gate = service.gate if session in (None, service.call_id) else None
            return route.fulfill(json={"approval": gate})
        if method == "POST" and path == "/transcribe":
            uploaded = request.post_data_buffer or b""
            text = utterances.pop(0) if utterances else ""
            assert len(uploaded) > 1000, "no real capture bytes reached /transcribe"
            return route.fulfill(json={"text": text})
        if method == "POST" and path == "/ask":
            return route.fulfill(json=service.handle_ask(request.post_data_json))
        if method == "POST" and path == "/approval/g-1/resolve":
            body = request.post_data_json or {}
            service.resolves.append({
                "utterance": body.get("utterance"),
                "speech_request_id": body.get("speech_request_id"),
            })
            # The real server's clarify/approve decision is pinned in
            # tests/test_call_context.py; here the gate is simply gone.
            service.gate = None
            return route.fulfill(status=404, json={"detail": "gone"})
        if method == "GET" and path.startswith("/audio/"):
            return route.fulfill(
                body=(FIXTURES / "sample.mp3").read_bytes(),
                content_type="audio/mpeg",
            )
        blocked.append({"method": method, "url": request.url})
        route.abort("blockedbyclient")

    return handler


# A page-side probe recording every fetch path (and only that — media,
# EventSource and the DOM stay native in these desktop contexts).
FETCH_PROBE = """
(() => {
    const nativeFetch = window.fetch.bind(window);
    const paths = window.__fetchPaths = [];
    window.fetchCalls = (needle) => paths.some(p => p.includes(needle));
    window.fetch = (input, options) => {
        const url = new URL(typeof input === 'string' ? input : input.url, location.href);
        paths.push(url.pathname + url.search);
        return nativeFetch(input, options);
    };
})();
"""


def open_client(browser: Browser, service: SharedCallService, utterances: list[str]):
    """One independent device: fresh context, routes into the SHARED state."""
    context = browser.new_context(
        permissions=["microphone"], service_workers="block", accept_downloads=False
    )
    blocked: list[dict] = []
    errors: list[str] = []
    context.add_init_script(FETCH_PROBE)
    context.route("**/*", make_route(service, utterances, blocked))
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(ORIGIN + "/", wait_until="load")
    return context, page, blocked, errors


def fake_capture_browser(browser_type, tmp_path: Path) -> Browser:
    """The existing fake-media launch, owned here so two device contexts
    can share ONE browser (and one fake microphone signal).

    The wav is ONE short voiced burst (t=1.0s..2.0s) inside 20 s of
    silence: exactly one utterance reaches /transcribe, and the mic is
    QUIET while the answer plays — a continuously-talking fixture would
    be a legitimate barge-in (FR-04) and interrupt the very playback
    under test."""
    capture = tmp_path / "speech.wav"
    rate = 48000
    duration_s = 20
    frames = bytearray()
    for index in range(rate * duration_s):
        voiced = 1.0 <= index / rate < 2.0
        value = int(12000 * math.sin(2 * math.pi * 440 * index / rate)) if voiced else 0
        frames.extend(struct.pack("<h", value))
    with wave.open(str(capture), "wb") as wav:
        wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        wav.writeframes(frames)
    return browser_type.launch(args=[
        "--use-fake-ui-for-media-stream",
        "--use-fake-device-for-media-stream",
        "--use-file-for-fake-audio-capture=" + str(capture),
    ])


def wait_for_fetch(page: Page, needle: str, timeout: int = 30000) -> None:
    """wait_for_function plus a state dump when it fails, so a stalled
    voice loop reports WHAT it did instead of a bare timeout."""
    import pytest as _pytest

    try:
        page.wait_for_function(
            f"() => fetchCalls({needle!r})", timeout=timeout
        )
    except Exception:
        state = page.evaluate("""() => ({
            paths: window.__fetchPaths,
            callBtn: document.getElementById('call-btn').textContent,
            pillMain: (document.getElementById('pill-main') || {}).textContent,
            pillSub: (document.getElementById('pill-sub') || {}).textContent,
            banner: (document.getElementById('banner') || {}).textContent,
            playerSrc: document.getElementById('player').getAttribute('src'),
            playerPaused: document.getElementById('player').paused,
        })""")
        raise _pytest.fail(
            f"fetch {needle!r} never happened; page state: {state}"
        ) from None


def wait_player_ended(page: Page, timeout: int = 30000) -> None:
    """Native playback of the answer, with a player dump on failure."""
    import pytest as _pytest

    try:
        page.wait_for_function(
            "() => { const p = document.getElementById('player');"
            " return p && p.ended; }", timeout=timeout,
        )
    except Exception:
        state = page.evaluate("""() => {
            const p = document.getElementById('player');
            return {src: p.getAttribute('src'), currentSrc: p.currentSrc,
                    paused: p.paused, ended: p.ended, duration: p.duration,
                    currentTime: p.currentTime, readyState: p.readyState,
                    error: p.error ? p.error.code : null,
                    paths: window.__fetchPaths};
        }""")
        raise _pytest.fail(
            f"the answer never finished playing; player state: {state}"
        ) from None


def select_agent(page: Page, pane_id: str) -> None:
    page.locator("#pane-picker").click()
    row = page.locator(f"#conv-list [data-pane='{pane_id}']")
    row.wait_for(state="visible")
    row.click()


class TestDesktopCallEntry:
    def test_desktop_browser_runs_the_real_call_loop(self, browser_type, tmp_path):
        service = SharedCallService()
        with fake_capture_browser(browser_type, tmp_path) as browser:
            context, page, blocked, errors = open_client(
                browser, service, ["¿cómo va todo?"]
            )
            try:
                # The desktop user picks the agent, then places the call:
                # both taps are the browser-gesture consent the entry
                # command honestly requires.
                select_agent(page, "w1:p9")
                page.locator("#call-btn").click()
                wait_for_fetch(page, "/ask")
                ask = service.asks[0]
                # Same shared call identity the mobile client uses — not a
                # per-device minted id — and the selected agent travels too.
                assert ask["session_id"] == "default"
                assert ask["pane_id"] == "w1:p9"
                assert ask["speech_request_id"]
                assert ask["conversation"] is True
                assert service.selects == [{"pane_id": "w1:p9"}]
                # The answer plays through the REAL media element.
                wait_player_ended(page)
                assert page.locator("#call-btn").inner_text().strip() == "🔴 Colgar"
            finally:
                context.close()
            assert blocked == [], blocked
            assert not errors, errors


class TestPhoneLaptopHandover:
    def test_second_device_shares_call_selection_and_pending_gate(
        self, browser_type, tmp_path
    ):
        service = SharedCallService(gate_on_ask=True)
        with fake_capture_browser(browser_type, tmp_path) as browser:
            # --- phone (A): selects the agent, asks, opens the gate.
            ctx_a, page_a, blocked_a, errors_a = open_client(
                browser, service, ["envía el gated resumen"]
            )
            try:
                select_agent(page_a, "w1:p9")
                page_a.locator("#call-btn").click()
                wait_for_fetch(page_a, "/ask")
                assert service.asks[0]["session_id"] == "default"
                assert service.asks[0]["pane_id"] == "w1:p9"
                wait_player_ended(page_a)
                # Hang up the phone: the gate stays pending on the call.
                page_a.locator("#call-btn").click()
            finally:
                ctx_a.close()

            # --- laptop (B): fresh device, SAME service state.
            views_before = len(service.views)
            ctx_b, page_b, blocked_b, errors_b = open_client(
                browser, service, ["adelante"]
            )
            try:
                # B adopted A's pane selection — NOT the focused pane
                # (w2:p1) it would have fallen back to on its own.
                from playwright.sync_api import expect

                expect(page_b.locator("#pane-picker-label")).to_have_text(
                    "OpenCode", timeout=15000
                )
                assert set(service.views[views_before:]) == {"w1:p9"}
                # B recovered A's pending gate under the shared session.
                page_b.wait_for_function(
                    "() => !document.getElementById('approval-float').classList.contains('hidden')",
                    timeout=15000,
                )
                assert service.approval_reads[-1] == "default"
                # The shared transcript repaints on B: it sees A's turn
                # (the drawer is closed on boot, so the turn only needs
                # to EXIST with the shared text, not be visible).
                page_b.wait_for_selector(".turn.user", state="attached", timeout=15000)
                assert "gated resumen" in page_b.locator(".turn.user").first.text_content()
                # B joins the call; the recovered gate owns the mic, so B's
                # spoken «adelante» goes to the gate — carrying NO playback
                # receipt, because B never drained A's explanation.
                page_b.locator("#call-btn").click()
                wait_for_fetch(page_b, "/approval/g-1/resolve")
                assert service.resolves == [
                    {"utterance": "adelante", "speech_request_id": None}
                ]
                # «Adelante» never leaked into a second /ask turn.
                assert len(service.asks) == 1
            finally:
                ctx_b.close()
            assert blocked_a == [], blocked_a
            assert blocked_b == [], blocked_b
            assert not errors_a, errors_a
            assert not errors_b, errors_b


class TestDesktopPermissionDenied:
    def test_blocked_mic_is_reported_and_the_keyboard_fallback_opens(self, browser):
        service = SharedCallService()
        context = browser.new_context(
            permissions=[], service_workers="block", accept_downloads=False
        )
        blocked: list[dict] = []
        errors: list[str] = []
        # Deterministic native-consent refusal: the browser says no to
        # the microphone. Nothing else is doubled.
        context.add_init_script("""
            Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {
                configurable: true,
                value: () => Promise.reject(new DOMException('denied', 'NotAllowedError'))
            });
        """)
        context.route("**/*", make_route(service, [], blocked))
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(ORIGIN + "/", wait_until="load")
            page.locator("#call-btn").click()
            page.wait_for_selector("#text-fallback:not(.hidden)", timeout=15000)
            assert "bloqueado" in page.locator("#mic-note").inner_text().lower()
            assert "Micrófono BLOQUEADO" in page.locator("#banner").inner_text()
            # The failed call hung up honestly instead of pretending.
            assert page.locator("#call-btn").inner_text().strip() == "📞 Llamar"
        finally:
            context.close()
        assert blocked == [], blocked
        assert not errors, errors
