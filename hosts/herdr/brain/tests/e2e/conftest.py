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
