"""Content-addressed render cache and orchestration for the conversation
reader (design: reader-html-integration, Decisions 3-7 and 11).

The rendered-conversation endpoint asks this module for (html, sidecar)
pairs keyed by turn content. Cache layers, both bounded:

- memory: lock-guarded OrderedDict LRU (``READER_LRU_MAX`` entries),
- disk: one JSON envelope per entry (html + map together) published with
  ``os.replace`` so a pair is never torn, swept oldest-first at
  ``READER_DISK_MAX`` entries under ``reader_cache/<profile>/``.

Execution is bounded three ways (Decision 7): a semaphore caps
concurrently executing renderer subprocesses, a per-request budget caps
cold renders per response, and a bounded failure memo keeps a failing
turn from respawning a renderer on every poll. Every failure degrades
fail-soft to a ``None`` pair — never an exception past this module — and
logged warnings name the failure class plus the first 8 hex chars of the
cache key only: transcript text, HTML and sidecar payloads never reach
the log.

Per-request budget null-cause (Decision 7 open question, CONFIRMED at
apply): ``READER_MAX_RENDERS_PER_REQUEST = 8`` yields ``html: null`` /
``map: null`` for over-budget cold turns — a fail-soft superset of the
spec's enumerated null causes. ``text`` is preserved, the request stays
HTTP 200, and subsequent requests warm the remaining turns
progressively (each already-rendered turn is a cache hit), so a 20-turn
conversation converges in a few expansions without ever spawning more
than 8 subprocesses per request. Do not change the value or the
deferral-as-null behavior without escalating to the orchestrator.

RELEASE STEP — renderer upgrade (Decision 11 open question, CONFIRMED
at apply): upgrading the herdr-tts renderer (or changing the reader
profile/contract) REQUIRES bumping ``READER_PROFILE_VERSION`` here.
Binary mtime/size is a FALSE signal because ``bin/herdr-tts`` is a thin
entry script whose mtime does not move when the engine underneath
changes; the bump is deliberate and is the only invalidation trigger.
Bumping orphans the old ``reader_cache/<old-version>/`` tree, which the
oldest-first disk sweep then reclaims. See README.md (deployment).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Optional

from .config import Settings
from .tts import ReaderError, render_html

logger = logging.getLogger("herdr_brain.reader")

READER_NAMESPACE = "herdr-brain/reader"
READER_PROFILE_VERSION = "1"  # bump to invalidate the whole cache namespace
READER_LANG = "es"
READER_MAX_CHARS = "0"
READER_SUMMARIZE = "false"
CONTRACT_ID = "reader-pipeline/anchors@1"

READER_CACHE_DIRNAME = "reader_cache"
READER_LRU_MAX = 128
READER_DISK_MAX = 512
READER_MAX_RENDERS_PER_REQUEST = 8
READER_FAILURE_MEMO_MAX = 256
# Short wait, not a queue: under pressure an over-budget render fails soft
# (null pair) instead of piling requests behind the semaphore.
READER_SEMAPHORE_WAIT_S = 2.0

Renderer = Callable[..., "tuple[str, dict]"]


def _framed(parts: tuple[str, ...]) -> bytes:
    """8-byte big-endian length prefix per field: injective by construction."""
    return b"".join(
        len(raw := p.encode("utf-8")).to_bytes(8, "big") + raw for p in parts
    )


def cache_key(text: str, *, lang: str = READER_LANG) -> str:
    """SHA-256 over the framed (namespace, profile, contract, lang,
    max_chars, summarize, text) tuple — no text can impersonate another
    tuple's key material."""
    parts = (
        READER_NAMESPACE, READER_PROFILE_VERSION, CONTRACT_ID,
        lang, READER_MAX_CHARS, READER_SUMMARIZE, text,
    )
    return hashlib.sha256(_framed(parts)).hexdigest()


def turn_id(
    session_id: Optional[str], index: int, role: str, text: str
) -> str:
    """Snapshot-scoped turn identity: same framing over (session, index,
    role, text) truncated to 16 hex. The index makes duplicate role+text
    collide-free; purity makes repeated requests identical."""
    blob = _framed((session_id or "", str(index), role, text))
    return hashlib.sha256(blob).hexdigest()[:16]


def cache_dir(settings: Settings) -> Path:
    return settings.audio_dir.parent / READER_CACHE_DIRNAME / READER_PROFILE_VERSION


def validate_sidecar(sidecar: object, *, lang: str = READER_LANG) -> bool:
    """Data Contracts predicate: accept only reader-pipeline/anchors@1,
    version 1, exact|coverage alignment, and this reader profile's engine
    metadata (lang es, max_chars 0, summarize false). Anything else is a
    miss, never a served entry."""
    if not isinstance(sidecar, dict):
        return False
    if sidecar.get("contract") != CONTRACT_ID:
        return False
    if sidecar.get("version") != 1:
        return False
    if sidecar.get("alignment") not in ("exact", "coverage"):
        return False
    engine = sidecar.get("engine")
    if not isinstance(engine, dict):
        return False
    return (
        engine.get("lang") == lang
        and engine.get("max_chars") == 0
        and engine.get("summarize") is False
    )


class ReaderCache:
    """Per-app cache + bounded renderer orchestration."""

    def __init__(self, settings: Settings, renderer: Renderer = render_html):
        self._settings = settings
        self._render = renderer
        self._memory: "OrderedDict[str, tuple[str, dict]]" = OrderedDict()
        self._failures: "OrderedDict[str, float]" = OrderedDict()
        self._lock = threading.Lock()
        self._semaphore = threading.BoundedSemaphore(settings.reader_concurrency)
        self._renders = 0

    @property
    def render_calls(self) -> int:
        """Renderer invocations — test observability for cache-hit proofs."""
        return self._renders

    def get(self, key: str) -> Optional["tuple[str, dict]"]:
        """Memory -> disk -> None. Every hit revalidates the sidecar."""
        with self._lock:
            pair = self._memory.get(key)
            if pair is not None:
                self._memory.move_to_end(key)
        if pair is not None:
            if validate_sidecar(pair[1]):
                return pair
            with self._lock:
                self._memory.pop(key, None)
            return None
        path = cache_dir(self._settings) / f"{key}.json"
        try:
            if not path.is_file():
                return None
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None  # corrupt artifact is a miss, not a failure
        if not isinstance(envelope, dict) or envelope.get("key") != key:
            return None
        if not validate_sidecar(envelope.get("map")):
            return None
        pair = (envelope.get("html"), envelope.get("map"))
        self._remember(key, pair)
        return pair

    def publish(self, key: str, html: str, sidecar: dict) -> None:
        """Writes one JSON envelope via a same-directory tmp file +
        ``os.replace`` (POSIX-atomic); unwritable storage degrades silently
        to memory-only."""
        try:
            cdir = cache_dir(self._settings)
            cdir.mkdir(parents=True, exist_ok=True)
            envelope = {
                "v": 1,
                "key": key,
                "contract": CONTRACT_ID,
                "created_at": time.time(),
                "html": html,
                "map": sidecar,
            }
            tmp = cdir / f"{key}.json.tmp-{os.getpid()}-{uuid.uuid4().hex}"
            tmp.write_text(
                json.dumps(envelope, ensure_ascii=False), encoding="utf-8"
            )
            os.replace(tmp, cdir / f"{key}.json")
            self._sweep(cdir)
        except OSError:
            pass  # fail-soft: memory-only serving

    def render_turn(self, text: str) -> Optional["tuple[str, dict]"]:
        """Cache-first single-turn render. ``None`` is the fail-soft null
        pair (failure memo, busy semaphore, renderer error) — this method
        never raises."""
        key = cache_key(text)
        hit = self.get(key)
        if hit is not None:
            return hit
        with self._lock:
            if key in self._failures:
                return None  # memoized: a failing turn never respawns
        try:
            acquired = self._semaphore.acquire(timeout=READER_SEMAPHORE_WAIT_S)
        except Exception:  # noqa: BLE001 — degenerate semaphore state
            acquired = False
        if not acquired:
            logger.warning("reader render skipped [%s]: semaphore busy", key[:8])
            return None
        try:
            self._renders += 1
            html, sidecar = self._render(self._settings, text, None)
            if not validate_sidecar(sidecar):
                raise ReaderError("sidecar failed contract validation")
            self.publish(key, html, sidecar)
            self._remember(key, (html, sidecar))
            return html, sidecar
        except Exception as exc:  # noqa: BLE001 — strictly fail-soft
            with self._lock:
                self._failures[key] = time.time()
                self._failures.move_to_end(key)
                while len(self._failures) > READER_FAILURE_MEMO_MAX:
                    self._failures.popitem(last=False)
            logger.warning("reader render failed [%s]: %s", key[:8], exc)
            return None
        finally:
            self._semaphore.release()

    def render_snapshot(self, turns: "list[dict]") -> "list[dict]":
        """Renders [{role, text}, ...] in source order, applying the
        per-request cold-render budget; every turn is answered."""
        out: "list[dict]" = []
        budget = READER_MAX_RENDERS_PER_REQUEST
        for turn in turns:
            hit = self.get(cache_key(turn.get("text", "")))
            if hit is None and budget <= 0:
                out.append({**turn, "html": None, "map": None})
                continue
            if hit is None:
                budget -= 1
                hit = self.render_turn(turn.get("text", ""))
            out.append({**turn, "html": hit[0], "map": hit[1]} if hit
                       else {**turn, "html": None, "map": None})
        return out

    def _remember(self, key: str, pair: "tuple[str, dict]") -> None:
        with self._lock:
            self._memory[key] = pair
            self._memory.move_to_end(key)
            while len(self._memory) > READER_LRU_MAX:
                self._memory.popitem(last=False)

    def _sweep(self, cdir: Path) -> None:
        """Oldest-first disk retention, lazy (on publish), no background
        thread — matching the approval-gate lazy-expiry convention."""
        try:
            entries = [p for p in cdir.iterdir() if p.name.endswith(".json")]
            if len(entries) <= READER_DISK_MAX:
                return
            entries.sort(key=lambda p: p.stat().st_mtime)
            for stale in entries[: len(entries) - READER_DISK_MAX]:
                stale.unlink()
        except OSError:
            pass
