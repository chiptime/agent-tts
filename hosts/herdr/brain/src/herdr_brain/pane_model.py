"""The model serving each pane's agent (parity F7a S3), and its
context window (parity F7d S1).

The engine owns the transcript knowledge: ``agent-tts last-model`` prints
the model of the LAST assistant request of a session (the model holding the
freshest context), and ``agent-tts cache-report --json`` prints the same
probe's measured tokens. brain never imports ``agent_tts``; it runs those
read-only CLI commands with the engine python, the same seam as the STT
engine (``stt_engine``).

The context LIMIT comes from the harness's OWN local catalog when one
exists — opencode's ``models.json`` cache, pi's ``models-store.json``,
omp's ``models.db`` (the shapes the reference product reads; see
``bridge/journal/model-window.ts`` in its checkout). No network, no
bundled table, no guess: a catalog that is missing, too big,
unreadable or silent about the model leaves the window unknown, and
``context`` is then ``null``.

Every failure is an honest ``None``: a harness the engine cannot read, a
pane without a session id, a missing engine python, a non-zero exit, a
timeout or an empty answer. Results are cached per (harness, session) for
``ttl_s`` seconds. In background mode a lookup never blocks a polling
endpoint: it answers from the cache and schedules one refresh per key.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from .stt_engine import EngineSttError, resolve_engine_python

#: Engine CLI contract (mirrored; brain must not import agent_tts).
LAST_MODEL_MODULE = "agent_tts.cli"
LAST_MODEL_COMMAND = "last-model"
CACHE_REPORT_COMMAND = "cache-report"

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

#: A catalog bigger than this is not read (opencode's is ~6 MB).
CATALOG_MAX_BYTES = 16 * 1024 * 1024

#: The largest window a catalog may name: a bound on a strange file,
#: not on a model's window.
_WINDOW_MAX = 100_000_000

_Runner = Callable[[list, float], subprocess.CompletedProcess]


def as_window(value) -> Optional[int]:
    """A window as a catalog may state it: a whole, positive number of
    tokens under the bound — anything else is not a window."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 < value <= _WINDOW_MAX else None


def _catalog_data(path: str) -> Optional[object]:
    """A catalog file's parsed JSON, or None when it is absent, not a
    file, too big or unparsable. Nothing is ever written."""
    try:
        info = os.stat(path)
        if info.st_size > CATALOG_MAX_BYTES:
            return None
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _own(value, key: str) -> Optional[object]:
    """An object's OWN member, so a name from a log never reaches a
    prototype."""
    if isinstance(value, dict) and key in value:
        return value[key]
    return None


def _model_parts(model: str) -> Tuple[str, Optional[str]]:
    """``provider/id`` → ``(provider, id)``; a bare id keeps its whole
    self as the id with no provider."""
    if "/" in model:
        provider, ident = model.split("/", 1)
        return provider, ident
    return "", model


def _unique_or_none(windows: List[int]) -> Optional[int]:
    """The one distinct window the providers named, or None when they
    disagreed or knew nothing: a window is never guessed."""
    distinct = set(windows)
    return windows[0] if len(distinct) == 1 else None


def opencode_models_window(path: str, model: str) -> Optional[int]:
    """OpenCode's copy of the models.dev catalog:
    ``<provider>.models.<id>.limit.context``."""
    data = _catalog_data(path)
    if not isinstance(data, dict):
        return None
    provider, ident = _model_parts(model)

    def named(block) -> Optional[int]:
        entry = _own(_own(block, "models"), ident)
        limit = _own(entry, "limit")
        return as_window(_own(limit, "context"))

    if provider:
        window = named(data.get(provider))
        if window is not None:
            return window
    # A bare id (or one the named provider does not carry) is asked of
    # every provider; providers that DISAGREE leave it unknown.
    windows = [window for block in data.values() if (window := named(block))]
    return _unique_or_none(windows)


def _window_in_list(entry, model: str, provider_block: str = "") -> Optional[int]:
    """The window a ``[{id, contextWindow}]`` list names for ``model``.

    An entry matches by its exact id, or — inside a provider's own
    block — by the id the provider namespaces the model under (the
    block's key must be the model's provider prefix)."""
    if not isinstance(entry, list):
        return None
    provider, ident = _model_parts(model)
    for item in entry:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str):
            continue
        if item_id == model or (
            provider and provider_block == provider and item_id == ident
        ):
            return as_window(item.get("contextWindow"))
    return None


def pi_models_store_window(path: str, model: str) -> Optional[int]:
    """pi's ``models-store.json``: per provider, the models it fetched,
    each with its ``contextWindow``."""
    data = _catalog_data(path)
    if not isinstance(data, dict):
        return None
    for provider_block, block in data.items():
        window = _window_in_list(
            _own(block, "models"), model, provider_block=str(provider_block)
        )
        if window is not None:
            return window
    return None


def omp_models_db_window(path: str, model: str) -> Optional[int]:
    """omp's ``models.db``: the ``model_cache(provider_id, models)``
    rows the provider names (its own, or a ``provider:`` variant),
    exact provider first — each row's ``models`` a JSON array of
    ``{id, contextWindow}``. Read-only; never a write."""
    try:
        if not os.path.isfile(path):
            return None
        uri = f"{Path(path).resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    except (sqlite3.Error, OSError):
        return None
    provider, _ident = _model_parts(model)
    try:
        if provider:
            rows = conn.execute(
                "select provider_id, models from model_cache "
                "where (provider_id = ? or substr(provider_id, 1, ?) = ?) "
                "and length(models) <= ? "
                "order by provider_id = ? desc, provider_id",
                (
                    provider, len(provider) + 1, f"{provider}:",
                    CATALOG_MAX_BYTES, provider,
                ),
            ).fetchall()
        else:
            # A bare id names no provider row: scan them in store
            # order and keep the FIRST window one names.
            rows = conn.execute(
                "select provider_id, models from model_cache "
                "where length(models) <= ? order by rowid",
                (CATALOG_MAX_BYTES,),
            ).fetchall()
        for _pid, blob in rows:
            try:
                entry = json.loads(blob)
            except (json.JSONDecodeError, TypeError):
                continue
            window = _window_in_list(entry, model)
            if window is not None:
                return window
        return None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _default_opencode_catalog() -> str:
    cache_home = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser(
        "~/.cache"
    )
    return os.path.join(cache_home, "opencode", "models.json")


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
    #: The last request's measured tokens (parity F7d S1), when the
    #: engine probe reported them.
    used: Optional[int] = None
    #: The local catalog's window for the model, when one named it.
    limit: Optional[int] = None
    #: True once used/limit were probed for this entry's model. The
    #: model lookup stays the F7a contract (ONE last-model call); the
    #: context facts ride their own lazily-run probe.
    context_done: bool = False


class PaneModelResolver:
    """Cached ``agent-tts last-model`` lookups keyed by (harness, session).

    The same entry carries the context facts (parity F7d S1): the last
    request's measured tokens (``used``) from the read-only
    ``cache-report`` probe and the local catalog's ``limit`` for the
    model, when the harness keeps one.
    """

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
        opencode_catalog: Optional[str] = None,
    ):
        self._python = python_resolver or _default_python
        self._run = runner or default_runner
        self._clock = clock or time.monotonic
        self.ttl_s = ttl_s
        self._timeout_s = timeout_s
        self._background = background
        self._spawn = spawn or _default_spawn
        self._opencode_catalog = opencode_catalog
        if enabled is None:
            enabled = os.environ.get(ENABLE_ENV, "").strip().lower() != "off"
        self.enabled = enabled
        self._cache: Dict[Tuple[str, str], _Entry] = {}
        self._inflight: Set[Tuple[str, str]] = set()
        self._lock = threading.Lock()

    def _now(self) -> float:
        """A clock read that keeps the never-raise promise (0.0 on failure)."""
        try:
            return self._clock()
        except Exception:  # noqa: BLE001 — a broken clock must not break a poll
            return 0.0

    def model(self, agent: Optional[str], session_id: Optional[str]) -> Optional[str]:
        """The pane's model, or None when unknown. Never raises."""
        entry = self._entry(agent, session_id)
        return entry.model if entry is not None else None

    def context(
        self, agent: Optional[str], session_id: Optional[str]
    ) -> Optional[dict]:
        """The pane's context window (parity F7d S1), or None.

        ``{"used", "limit"}`` when the local catalog named the model's
        window (``used`` from the last request's measured tokens, None
        when the probe reported none); ``None`` without a catalog —
        a window is never guessed. Never raises, and never costs a
        ``cache-report`` run while the model itself is unknown.
        """
        entry = self._entry(agent, session_id, want_context=True)
        if entry is None or entry.limit is None:
            return None
        return {"used": entry.used, "limit": entry.limit}

    def note_switched(
        self, agent: Optional[str], session_id: Optional[str]
    ) -> None:
        """Drops the cached entry so the next poll re-probes (the
        transcript's NEXT request records the new model)."""
        engine_agent = ENGINE_AGENTS.get((agent or "").strip().lower())
        if engine_agent is None or not session_id:
            return
        with self._lock:
            self._cache.pop((engine_agent, session_id), None)

    def _entry(
        self,
        agent: Optional[str],
        session_id: Optional[str],
        want_context: bool = False,
    ) -> Optional[_Entry]:
        if not self.enabled or not session_id:
            return None
        engine_agent = ENGINE_AGENTS.get((agent or "").strip().lower())
        if engine_agent is None:
            return None
        key = (engine_agent, session_id)
        with self._lock:
            entry = self._cache.get(key)
            fresh = entry is not None and entry.expires_at > self._now()
            if fresh and (
                not want_context or entry.context_done or entry.model is None
            ):
                return entry
            known = entry
            if self._background:
                if key not in self._inflight:
                    self._inflight.add(key)
                    try:
                        self._spawn(
                            lambda: self._refresh(key, want_context=want_context)
                        )
                    except Exception:  # noqa: BLE001 — spawn failure must not break a poll
                        self._inflight.discard(key)  # so a later call retries
                return known
        return self._refresh(key, want_context=want_context)

    def _refresh(
        self, key: Tuple[str, str], want_context: bool = False
    ) -> Optional[_Entry]:
        try:
            entry = self._probe(*key, want_context=want_context)
        except Exception:  # noqa: BLE001 — a lookup must never break a poll
            entry = _Entry(None, self._now() + self.ttl_s)
        with self._lock:
            # A concurrent model() refresh may already hold a model:
            # keep it, never clobber it with a context-only miss.
            current = self._cache.get(key)
            if want_context and current is not None and current.model:
                entry = _Entry(
                    current.model,
                    current.expires_at,
                    used=entry.used,
                    limit=entry.limit,
                    context_done=True,
                )
            self._cache[key] = entry
            self._inflight.discard(key)
        return entry

    def _probe(
        self, engine_agent: str, session_id: str, want_context: bool = False
    ) -> _Entry:
        """One probe pass: the model, then (asked for) the measured
        tokens and the local catalog's window for it."""
        expires = self._now() + self.ttl_s
        model = self._lookup(engine_agent, session_id)
        if not model:
            return _Entry(None, expires, context_done=True)
        if not want_context:
            return _Entry(model, expires)
        used = self._probe_used(engine_agent, session_id)
        limit = self._limit_for(engine_agent, model)
        return _Entry(model, expires, used=used, limit=limit, context_done=True)

    def _probe_used(self, engine_agent: str, session_id: str) -> Optional[int]:
        """The last request's measured tokens: input + cached + cache
        write of the newest turn the probe reported."""
        python = self._python()
        if not python:
            return None
        argv = [
            python, "-m", LAST_MODEL_MODULE, CACHE_REPORT_COMMAND,
            "--agent", engine_agent, "--session-id", session_id,
            "--json", "--limit", "1",
        ]
        try:
            proc = self._run(argv, self._timeout_s)
        except (subprocess.TimeoutExpired, OSError):
            return None
        if proc.returncode != 0:
            return None
        try:
            body = json.loads(proc.stdout or "")
        except (json.JSONDecodeError, ValueError):
            return None
        turns = body.get("turns") if isinstance(body, dict) else None
        if not isinstance(turns, list) or not turns:
            return None
        newest = turns[-1]
        if not isinstance(newest, dict):
            return None
        parts = [
            newest.get("input_tokens"),
            newest.get("cached_tokens"),
            newest.get("cache_write_tokens"),
        ]
        if not all(isinstance(part, int) and not isinstance(part, bool) for part in parts):
            return None
        used = sum(parts)
        return used if used > 0 else None

    def _limit_for(self, engine_agent: str, model: str) -> Optional[int]:
        """The local catalog's window for the model, by harness. Only
        harnesses with BOTH an engine extractor and a known catalog can
        answer; everyone else keeps ``None`` (never a guess)."""
        catalog = self._opencode_catalog
        if engine_agent == "opencode":
            path = catalog if catalog is not None else _default_opencode_catalog()
            return opencode_models_window(path, model)
        return None

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
