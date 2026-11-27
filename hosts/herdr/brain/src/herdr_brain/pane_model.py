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
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from .stt_engine import EngineSttError, resolve_engine_python
from .secrets_mask import mask_text

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


# ── the full connected-model catalog (opencode) ───────────────────────

#: How long a successful ``opencode models`` listing is trusted. The
#: connected providers change rarely; a phone opening the model card
#: repeatedly must not spawn the CLI each time.
CATALOG_TTL_S = 300.0
#: A failed listing is retried soon (the operator may have just fixed
#: it) but not on every card open.
CATALOG_FAILURE_TTL_S = 15.0
CATALOG_CLI_TIMEOUT_S = 10.0
#: One ``provider/model-id`` line — the id may itself contain slashes.
_MODEL_LINE = re.compile(r"^[A-Za-z0-9][\w.\-]*/\S+$")


def _opencode_binary() -> Optional[str]:
    """The opencode CLI: PATH first, then the per-user install the
    brain's service PATH often lacks."""
    found = shutil.which("opencode")
    if found:
        return found
    fallback = os.path.expanduser("~/.local/bin/opencode")
    return fallback if os.access(fallback, os.X_OK) else None


class OpencodeModelCatalog:
    """Every model the operator's connected opencode providers offer.

    The picker on screen paints only a window of its list, so a card
    derived from the screen can never show them all. ``opencode models``
    prints ALL of them (``provider/model-id`` per line); opencode's own
    ``models.json`` cache names them for people — the display name the
    picker's Search filters by. Read-only: a subprocess with a timeout
    and a file read, cached for ``ttl_s``. Any failure is an empty list
    with a ``reason`` — never a guess, never a raise.
    """

    def __init__(
        self,
        runner: Optional[_Runner] = None,
        clock: Optional[Callable[[], float]] = None,
        ttl_s: float = CATALOG_TTL_S,
        failure_ttl_s: float = CATALOG_FAILURE_TTL_S,
        timeout_s: float = CATALOG_CLI_TIMEOUT_S,
        catalog_path: Optional[str] = None,
        binary: Optional[str] = None,
    ):
        self._run = runner or default_runner
        self._clock = clock or time.monotonic
        self._ttl_s = ttl_s
        self._failure_ttl_s = failure_ttl_s
        self._timeout_s = timeout_s
        self._catalog_path = catalog_path
        self._binary = binary
        self._lock = threading.Lock()
        self._cached: Optional[Tuple[float, dict]] = None

    def list_models(self) -> dict:
        """``{"models": [{id, name, provider, provider_name}], "reason"}``
        (``reason`` is empty on success). Never raises."""
        with self._lock:
            now = self._now()
            if self._cached is not None and self._cached[0] > now:
                return self._copy(self._cached[1])
            try:
                result = self._load()
            except Exception:  # noqa: BLE001 — a catalog read never breaks the card
                result = {"models": [], "reason": "error"}
            ttl = self._ttl_s if result["models"] else self._failure_ttl_s
            self._cached = (now + ttl, result)
            return self._copy(result)

    @staticmethod
    def _copy(result: dict) -> dict:
        return {
            "models": [dict(entry) for entry in result["models"]],
            "reason": result["reason"],
        }

    def _now(self) -> float:
        try:
            return self._clock()
        except Exception:  # noqa: BLE001 — a broken clock only costs the cache
            return 0.0

    def _load(self) -> dict:
        binary = self._binary or _opencode_binary()
        if not binary:
            return {"models": [], "reason": "opencode-not-found"}
        try:
            proc = self._run([binary, "models"], self._timeout_s)
        except subprocess.TimeoutExpired:
            return {"models": [], "reason": "timeout"}
        except OSError:
            return {"models": [], "reason": "unavailable"}
        if proc.returncode != 0:
            return {"models": [], "reason": "cli-failed"}
        ids: List[str] = []
        seen: Set[str] = set()
        for raw in (proc.stdout or "").splitlines():
            line = raw.strip()
            if _MODEL_LINE.match(line) and line not in seen:
                seen.add(line)
                ids.append(line)
        if not ids:
            return {"models": [], "reason": "empty"}
        path = self._catalog_path or _default_opencode_catalog()
        data = _catalog_data(path)
        names = data if isinstance(data, dict) else {}
        models = []
        for model_id in ids:
            provider, ident = _model_parts(model_id)
            block = _own(names, provider)
            entry = _own(_own(block, "models"), ident)
            name = _own(entry, "name")
            provider_name = _own(block, "name")
            models.append(
                {
                    "id": model_id,
                    "name": name if isinstance(name, str) and name.strip() else ident,
                    "provider": provider,
                    "provider_name": (
                        provider_name
                        if isinstance(provider_name, str) and provider_name.strip()
                        else provider
                    ),
                }
            )
        return {"models": models, "reason": ""}


# ── the antigravity (agy) connected-model catalog (POST-09) ───────────


#: One ``id<TAB>Display Name`` line of ``agy models`` (real output,
#: verified 2026-11-26 — e.g. ``gemini-4-neon-high<TAB>Gemini 4 Neon (High)``).
_AGY_MODEL_LINE = re.compile(r"^([A-Za-z0-9][\w.\-]*)\t(\S.*)$")

#: The trailing `` (Word)`` variant suffix of a display name — an effort
#: level (``Gemini 4 Neon (High)``) or an orchestrator backend
#: (``Orchestrator (Neon)``).
_AGY_NAME_VARIANT = re.compile(r"^(.*?)\s+\(([^()]+)\)$")


def _agy_binary() -> Optional[str]:
    """The agy CLI: PATH first, then the per-user install the brain's
    service PATH often lacks (same shape as ``_opencode_binary``)."""
    found = shutil.which("agy")
    if found:
        return found
    fallback = os.path.expanduser("~/.local/bin/agy")
    return fallback if os.access(fallback, os.X_OK) else None


def _default_agy_spawn(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="agy-model-catalog-refresh", daemon=True).start()


class AntigravityModelCatalog:
    """Every model the operator's `agy models` subcommand lists.

    ``id<TAB>Display Name`` per line; no ``--json``, no context windows
    (so the antigravity chip keeps ``limit=None`` — a window is never
    guessed). Read-only subprocess with a timeout, cached for ``ttl_s``;
    any failure is an empty list with a ``reason`` — never a guess,
    never a raise. The cache lock NEVER spans the subprocess (POST-09
    R2): one CLI run at a time, everyone else waits lock-free; the
    hot-poll path (:meth:`cached_name_for`) is stale-while-revalidate —
    a fresh or stale cache answers instantly, a stale or cold one
    schedules exactly one background refresh, and a cold cache answers
    ``None`` on the spot.
    """

    def __init__(
        self,
        runner: Optional[_Runner] = None,
        clock: Optional[Callable[[], float]] = None,
        ttl_s: float = CATALOG_TTL_S,
        failure_ttl_s: float = CATALOG_FAILURE_TTL_S,
        timeout_s: float = CATALOG_CLI_TIMEOUT_S,
        binary: Optional[str] = None,
        spawn: Optional[Callable[[Callable[[], None]], None]] = None,
    ):
        self._run = runner or default_runner
        self._clock = clock or time.monotonic
        self._ttl_s = ttl_s
        self._failure_ttl_s = failure_ttl_s
        self._timeout_s = timeout_s
        self._binary = binary
        self._spawn = spawn or _default_agy_spawn
        self._lock = threading.Lock()
        self._cached: Optional[Tuple[float, dict]] = None
        self._inflight = False

    @staticmethod
    def _copy(result: dict) -> dict:
        return {
            "models": [dict(entry) for entry in result["models"]],
            "reason": result["reason"],
        }

    def _now(self) -> float:
        try:
            return self._clock()
        except Exception:  # noqa: BLE001 — a broken clock only costs the cache
            return 0.0

    def list_models(self) -> dict:
        """``{"models": [{"id", "name"}], "reason"}`` (``reason`` empty on
        success). Never raises; may wait (lock-free) for an in-flight
        refresh — the on-demand card fetch, not a hot poll."""
        while True:
            with self._lock:
                cached = self._cached
                if cached is not None and cached[0] > self._now():
                    return self._copy(cached[1])
                mine = not self._inflight
                self._inflight = True
            if mine:
                return self._copy(self._refresh())
            # Someone else is loading: wait WITHOUT the lock. Every
            # refresh path finalizes (catch-all below), so this ends.
            time.sleep(0.005)

    def cached_name_for(self, model_id: str) -> Optional[str]:
        """The stale-while-revalidate name lookup for hot polls (POST-09
        R2). A fresh or stale cache answers instantly; a stale one also
        schedules exactly one background refresh; a cold one answers
        ``None`` on the spot (the caller falls back to the raw id).
        Never blocks on the CLI, never raises, never duplicates a run."""
        with self._lock:
            cached = self._cached
            cold = cached is None
            stale = cached is not None and cached[0] <= self._now()
            must_refresh = (cold or stale) and not self._inflight
            if must_refresh:
                self._inflight = True
        if must_refresh:
            self._spawn(self._refresh)
        if cached is None:
            return None
        for entry in cached[1]["models"]:
            if entry["id"] == model_id:
                return entry["name"]
        return None

    def name_for(self, model_id: str) -> Optional[str]:
        """Blocking name lookup (card-side uses): the catalog is
        fetched/cached first, then the id resolved."""
        for entry in self.list_models()["models"]:
            if entry["id"] == model_id:
                return entry["name"]
        return None

    def _refresh(self) -> dict:
        """One CLI run OUTSIDE the lock, then the guarded cache write.
        Every path finalizes, so waiters never wedge on ``_inflight``."""
        try:
            result = self._load()
        except Exception:  # noqa: BLE001 — a refresh never raises
            result = {"models": [], "reason": "error"}
        with self._lock:
            ttl = self._ttl_s if result["models"] else self._failure_ttl_s
            self._cached = (self._now() + ttl, result)
            self._inflight = False
        return result

    def _load(self) -> dict:
        binary = self._binary or _agy_binary()
        if not binary:
            return {"models": [], "reason": "agy-not-found"}
        try:
            proc = self._run([binary, "models"], self._timeout_s)
        except subprocess.TimeoutExpired:
            return {"models": [], "reason": "timeout"}
        except OSError:
            return {"models": [], "reason": "unavailable"}
        if proc.returncode != 0:
            return {"models": [], "reason": "cli-failed"}
        models: List[dict] = []
        seen: Set[str] = set()
        for raw in (proc.stdout or "").splitlines():
            match = _AGY_MODEL_LINE.match(raw.strip())
            if match is None or match.group(1) in seen:
                continue
            seen.add(match.group(1))
            # C1: display names are free-form CLI output — masked before
            # they ever reach a card or a refusal detail.
            models.append(
                {"id": match.group(1), "name": mask_text(match.group(2).strip())}
            )
        if not models:
            return {"models": [], "reason": "empty"}
        return {"models": models, "reason": ""}


def antigravity_picker_rows(models: List[dict]) -> List[dict]:
    """The rows agy's own ``/model`` picker paints, derived from the
    catalog (POST-09 S2/D2).

    The live capture (2026-11-26) pins the shape: a name's trailing
    `` (Word)`` is a VARIANT axis (effort levels, orchestrator backends);
    when several catalog entries share the base, the picker paints the
    bare base as ONE row (choosing it takes that family's default
    effort), while a lone entry keeps its full name — ``GPT-OSS 120B
    (Medium)`` is painted exactly so. Each row carries ``ids`` — every
    member id — so the pane's transcript id marks the current family.
    Malformed entries are skipped; rows keep the CLI's order.
    """
    groups: Dict[str, List[dict]] = {}
    order: List[str] = []
    for entry in models:
        if not isinstance(entry, dict):
            continue
        model_id = entry.get("id")
        name = entry.get("name")
        if not isinstance(model_id, str) or not model_id.strip():
            continue
        if not isinstance(name, str) or not name.strip():
            continue
        match = _AGY_NAME_VARIANT.match(name.strip())
        base = match.group(1) if match else name.strip()
        if base not in groups:
            groups[base] = []
            order.append(base)
        groups[base].append({"id": model_id, "name": name.strip()})
    rows: List[dict] = []
    for base in order:
        members = groups[base]
        row_name = base if len(members) > 1 else members[0]["name"]
        rows.append(
            {
                "id": members[0]["id"],
                "name": row_name,
                "ids": [member["id"] for member in members],
            }
        )
    return rows
