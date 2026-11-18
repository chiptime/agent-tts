"""The model serving each pane's agent (parity F7a S3).

The engine owns the transcript knowledge: ``agent-tts last-model`` prints
the model of the LAST assistant request of a session (the model holding the
freshest context). brain never imports ``agent_tts``; it runs that CLI with
the engine python, the same seam as the STT engine (``stt_engine``).

Every failure is an honest ``None``: a harness the engine cannot read, a
pane without a session id, a missing engine python, a non-zero exit, a
timeout or an empty answer. Results are cached per (harness, session) for
``ttl_s`` seconds. In background mode a lookup never blocks a polling
endpoint: it answers from the cache and schedules one refresh per key.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Set, Tuple

from .stt_engine import EngineSttError, resolve_engine_python

#: Engine CLI contract (mirrored; brain must not import agent_tts).
LAST_MODEL_MODULE = "agent_tts.cli"
LAST_MODEL_COMMAND = "last-model"

#: brain harness name -> engine agent name. Only harnesses whose model the
#: engine extracts today; every other harness answers None without a run.
ENGINE_AGENTS: Dict[str, str] = {
    "claude": "claude",
    "opencode": "opencode",
    "oa": "opencode",
    "codex": "codex",
    "antigravity": "antigravity",
    "agy": "antigravity",
}

ENABLE_ENV = "HERDR_BRAIN_PANE_MODEL"
DEFAULT_TTL_S = 60.0
DEFAULT_TIMEOUT_S = 5.0

_Runner = Callable[[list, float], subprocess.CompletedProcess]


def default_runner(argv: list, timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout)


def _default_python() -> Optional[str]:
    try:
        return resolve_engine_python()
    except EngineSttError:
        return None


def _default_spawn(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="pane-model-refresh", daemon=True).start()


@dataclass
class _Entry:
    model: Optional[str]
    expires_at: float


class PaneModelResolver:
    """Cached ``agent-tts last-model`` lookups keyed by (harness, session)."""

    def __init__(
        self,
        python_resolver: Optional[Callable[[], Optional[str]]] = None,
        runner: Optional[_Runner] = None,
        clock: Optional[Callable[[], float]] = None,
        ttl_s: float = DEFAULT_TTL_S,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        background: bool = True,
        spawn: Optional[Callable[[Callable[[], None]], None]] = None,
        enabled: Optional[bool] = None,
    ):
        self._python = python_resolver or _default_python
        self._run = runner or default_runner
        self._clock = clock or time.monotonic
        self.ttl_s = ttl_s
        self._timeout_s = timeout_s
        self._background = background
        self._spawn = spawn or _default_spawn
        if enabled is None:
            enabled = os.environ.get(ENABLE_ENV, "").strip().lower() != "off"
        self.enabled = enabled
        self._cache: Dict[Tuple[str, str], _Entry] = {}
        self._inflight: Set[Tuple[str, str]] = set()
        self._lock = threading.Lock()

    def model(self, agent: Optional[str], session_id: Optional[str]) -> Optional[str]:
        """The pane's model, or None when unknown. Never raises."""
        if not self.enabled or not session_id:
            return None
        engine_agent = ENGINE_AGENTS.get((agent or "").strip().lower())
        if engine_agent is None:
            return None
        key = (engine_agent, session_id)
        with self._lock:
            entry = self._cache.get(key)
            if entry is not None and entry.expires_at > self._clock():
                return entry.model
            known = entry.model if entry is not None else None
            if self._background:
                if key not in self._inflight:
                    self._inflight.add(key)
                    self._spawn(lambda: self._refresh(key))
                return known
        return self._refresh(key)

    def _refresh(self, key: Tuple[str, str]) -> Optional[str]:
        try:
            model = self._lookup(*key)
        except Exception:  # noqa: BLE001 — a lookup must never break a poll
            model = None
        with self._lock:
            self._cache[key] = _Entry(model, self._clock() + self.ttl_s)
            self._inflight.discard(key)
        return model

    def _lookup(self, engine_agent: str, session_id: str) -> Optional[str]:
        python = self._python()
        if not python:
            return None
        argv = [
            python, "-m", LAST_MODEL_MODULE, LAST_MODEL_COMMAND,
            "--agent", engine_agent, "--session-id", session_id,
        ]
        try:
            proc = self._run(argv, self._timeout_s)
        except (subprocess.TimeoutExpired, OSError):
            return None
        if proc.returncode != 0:
            return None
        first = (proc.stdout or "").strip().splitlines()
        return first[0].strip() if first and first[0].strip() else None
