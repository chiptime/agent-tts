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
