"""Run one coroutine on a fresh event loop, isolated from a foreign loop.

``asyncio.run`` refuses to start when another event loop is already running
in the calling thread. pytest-playwright's session-scoped synchronous
fixtures keep their loop running in the main thread for the whole session,
so any test that runs AFTER a browser test (``tests/e2e``) would fail with
``RuntimeError: asyncio.run() cannot be called from a running event loop``.

Running the coroutine in a short-lived worker thread gives it a clean thread
with no running loop, while the exception still reaches the caller.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Coroutine, TypeVar

T = TypeVar("T")


def run_async(coro: Coroutine[Any, Any, T]) -> T:
    """Run ``coro`` to completion on a new loop in a dedicated thread."""
    box: dict[str, Any] = {}

    def _target() -> None:
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 — re-raised in the caller
            box["error"] = exc

    thread = threading.Thread(target=_target, name="test-async-runner")
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box["value"]
