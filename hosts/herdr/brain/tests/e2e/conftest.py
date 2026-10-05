"""Fixtures for the voice-stack E2E browser harness (task VS0.6).

This harness exercises the browser's REAL media pipeline end to end: a real
``<audio>`` element loads a committed MP3 fixture over plain HTTP, playback is
started with a real ``play()`` call, and completion is only ever accepted from
the platform-fired ``ended`` event, per contract D6/T12.4. No part of the media
stack is mocked.

Security posture: Chromium is launched with exactly one non-default flag,
``--autoplay-policy=no-user-gesture-required``, so programmatic ``play()`` is
allowed without a user gesture. The sandbox stays on, web security stays on,
and no blanket permissions are granted.
"""

import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterator

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args: Dict[str, Any]) -> Dict[str, Any]:
    """Launch Chromium with autoplay allowed without a user gesture.

    Follows pytest-playwright's documented launch-args override pattern: merge
    the plugin defaults and set ``args`` to exactly one flag. Note that in
    pytest-playwright 0.7.x the fixture the plugin actually consumes is named
    ``browser_type_launch_args``; the older ``browser_launch_args`` name no
    longer exists, so overriding that name would silently never reach the
    browser.
    """
    return {
        **browser_type_launch_args,
        "args": ["--autoplay-policy=no-user-gesture-required"],
    }


@pytest.fixture
def audio_server() -> Iterator[str]:
    """Serve the fixtures directory over real HTTP on an ephemeral local port.

    Yields the base URL (``http://127.0.0.1:<port>``) so tests load audio the
    same way a production page would: a real network fetch, not a file:// or
    data: URL and not an intercepted route. Stdlib only. The server runs in a
    daemon thread and is shut down and joined in teardown so no test can leak
    a listener across the suite.
    """
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(FIXTURES_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# -- voice-stack MQ-03: real-browser line/branch coverage of app.js glue --
#
# Opt-in via E2E_JS_COVERAGE_DIR: the context serves istanbul-instrumented
# copies of app.js/speech.js at their REAL URLs (route fulfill), everything
# else — server, SSE, media, the page itself — stays the real thing. After
# each test the collected window.__coverage__ (original-source coordinates)
# is written into the coverage dir. Without the env var the fixture is a
# transparent pass-through: identical suite, zero behavior change.

import json as json_mod
import os as os_mod
import re as re_mod
import subprocess as subprocess_mod

JS_COVERAGE_DIR = os_mod.environ.get("E2E_JS_COVERAGE_DIR")
_COVERAGE_TARGETS = ("app.js", "speech.js")


def _instrumented_bytes(static_dir: Path, name: str, out_dir: Path) -> bytes:
    src = static_dir / name
    out_file = out_dir / f"instrumented-{name}"
    maps_file = out_dir / f"instrumented-{name}.maps.json"
    if not out_file.exists():
        subprocess_mod.run(
            ["node", str(Path(__file__).resolve().parents[5] / "scripts/voice-stack/js-coverage/instrument-file.js"),
             str(src), str(out_file), str(maps_file)],
            check=True, capture_output=True, timeout=120,
        )
    return out_file.read_bytes()


@pytest.fixture
def context(context):
    if not JS_COVERAGE_DIR:
        yield context
        return
    import playwright.sync_api as _pw

    static_dir = Path(__file__).resolve().parents[2] / "src/herdr_brain/static"
    out_dir = Path(JS_COVERAGE_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    body_by_name = {
        name: _instrumented_bytes(static_dir, name, out_dir)
        for name in _COVERAGE_TARGETS
    }

    def _handler(name):
        def handle(route: "_pw.Route") -> None:
            route.fulfill(
                status=200,
                content_type="application/javascript",
                body=body_by_name[name],
            )
        return handle

    # The served index versions some assets as /app.js?v=<hash> (server.py
    # asset fingerprinting); the trailing * keeps both shapes intercepted.
    for target in _COVERAGE_TARGETS:
        context.route(f"**/{target}*", _handler(target))

    yield context

    # Collect before the plugin closes the context: async completions have
    # settled by test end, so the counters are final for this test.
    merged: dict = {}
    for page in context.pages:
        try:
            cov = page.evaluate("() => window.__coverage__")
        except Exception:
            continue
        if not cov:
            continue
        for fname, fc in cov.items():
            merged.setdefault(fname, []).append(fc)
    if merged:
        test_name = re_mod.sub(r"[^A-Za-z0-9_.-]", "_",
                                os_mod.environ.get("PYTEST_CURRENT_TEST", "unknown").split(" ")[0])
        (out_dir / f"browser-{test_name}-{os_mod.getpid()}.json").write_text(
            json_mod.dumps(merged), encoding="utf-8"
        )


# -- voice-stack M1: the REAL brain app over HTTP with deterministic doubles --

import dataclasses
import shutil
import socket as socket_mod
import time as time_mod

import uvicorn

from fastapi.testclient import TestClient  # noqa: F401  (import parity check)

from herdr_brain.config import Settings
from herdr_brain.server import create_app
from herdr_brain.tts import TTSError
from herdr_brain.watcher import AgentWatcher
from tests.conftest import SETTINGS_KWARGS, StubHerdr


class ScriptedTTS:
    """Renderer double: instant for normal turns, blocked-until-released for
    texts the test marks slow. Models cancel exactly like the production
    cancellable renderer (TTSError("cancelled"), no file written)."""

    takes_cancel_event = True

    def __init__(self, fixtures: Path, audio_dir: Path):
        self._fixtures = fixtures
        self._audio_dir = audio_dir
        self.slow_texts = set()
        self.slow_started = threading.Event()
        self.release_slow = threading.Event()
        self.calls = []
        self.aborted = []

    def __call__(self, settings, text, out_path: Path, cancel_event) -> Path:
        self.calls.append(text)
        if any(marker in text for marker in self.slow_texts):
            self.slow_started.set()
            while not (cancel_event.is_set() or self.release_slow.is_set()):
                cancel_event.wait(0.05)
            if cancel_event.is_set():
                self.aborted.append(text)
                raise TTSError("cancelled")
            shutil.copyfile(self._fixtures / "long.mp3", out_path)
            return out_path
        shutil.copyfile(self._fixtures / "long.mp3", out_path)
        return out_path


class FakeLLM:
    def __init__(self):
        self.calls = []

    def attach_store(self, store):
        pass

    def attach_approval_store(self, store):
        pass

    def ask(self, text, session_id=None, pane_id=None):
        self.calls.append(text)
        return {
            "answer": "Respuesta a: " + text,
            "pane_id": "w1:p9",
            "agent": "opencode",
            "session_id": session_id or "default",
            "approval": None,
        }


@pytest.fixture
def brain(tmp_path):
    """The real app served over HTTP: deterministic LLM/TTS, live SSE hub.

    Returns a namespace with the base URL, the scripted TTS, the watcher hub
    (publish announcements straight into the PWA's EventSource) and the LLM.
    """
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    cfg = Settings(**{**SETTINGS_KWARGS, "audio_dir": str(audio_dir)})
    tts = ScriptedTTS(FIXTURES_DIR, audio_dir)
    llm = FakeLLM()
    watcher = AgentWatcher(cfg, herdr=StubHerdr(), tts_renderer=None)  # not started
    app = create_app(
        settings=cfg,
        llm_factory=lambda _cfg, _tools: llm,
        tts_renderer=tts,
        watcher=watcher,
        daemon_probe=lambda: "up",
    )
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time_mod.monotonic() + 15
    while time_mod.monotonic() < deadline:
        if server.started:
            break
        time_mod.sleep(0.02)
    assert server.started, "brain server never came up"

    class Brain:
        pass

    handle = Brain()
    handle.url = f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    handle.tts = tts
    handle.llm = llm
    handle.hub = watcher.hub
    handle.audio_dir = audio_dir
    try:
        yield handle
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def ask_via_keyboard(page, text):
    """Drives the REAL text-fallback form (hidden or not): ask() runs."""
    page.evaluate(
        """([text]) => {
            const input = document.getElementById('text-input');
            input.value = text;
            document.getElementById('text-fallback').requestSubmit();
        }""",
        [text],
    )


def wait_for_sse_subscriber(brain, timeout=10.0):
    deadline = time_mod.monotonic() + timeout
    while time_mod.monotonic() < deadline:
        if brain.hub._subscribers:
            return
        time_mod.sleep(0.02)
    raise AssertionError("the PWA never subscribed to /events")
