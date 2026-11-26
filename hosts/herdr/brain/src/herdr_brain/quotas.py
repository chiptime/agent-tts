"""Provider quota snapshots for the brain (POST-02).

The data source is the host's external ``ai-quotas`` CLI
(``check --json``: one JSON array on stdout, human progress on
stderr). Brain owns only the adapter, the cache and the serialization:
no provider credentials are read, stored or logged here, and nothing
from the binary's output is ever persisted to disk (POST-02 S5).

The shape mirrors ``OpencodeModelCatalog`` in pane_model.py: an
injectable runner/clock/binary, a thread-safe TTL cache, fail-soft
``reason`` values and no raises. Unlike the catalog, a cold miss never
blocks: it kicks one background refresh and reports ``not-ready``
(POST-02 S2, decision 2a — a voice ask must never stall on the
~5-15s cold subprocess).
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

#: Fresh success lives for five minutes (the CLI keeps its own ~5m
#: cache upstream; brain's TTL just avoids respawning it per call).
QUOTAS_TTL_S = 300.0
#: A failure is retried much sooner than a success.
QUOTAS_FAILURE_TTL_S = 15.0
#: A cold run was observed at ~8s (pricing refresh); leave headroom.
QUOTAS_CLI_TIMEOUT_S = 30.0

_Runner = Callable[[List[str], float], "subprocess.CompletedProcess"]
_Spawner = Callable[[Callable[[], None]], None]


def _default_runner(args: List[str], timeout_s: float) -> "subprocess.CompletedProcess":
    return subprocess.run(args, timeout=timeout_s, capture_output=True, text=True)


def _default_spawner(job: Callable[[], None]) -> None:
    threading.Thread(target=job, daemon=True, name="quota-refresh").start()


def ai_quotas_binary() -> Optional[str]:
    """The host ``ai-quotas`` CLI: PATH first, then the per-user
    install the brain's service PATH often lacks (same contract as
    ``_opencode_binary`` in pane_model.py)."""
    found = shutil.which("ai-quotas")
    if found:
        return found
    fallback = os.path.expanduser("~/.local/bin/ai-quotas")
    return fallback if os.access(fallback, os.X_OK) else None


def _clean_str(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _clean_number(value: Any) -> Optional[float]:
    # bool is an int subclass — a bare true/false is not a percentage.
    # NaN/Infinity parse fine from JSON but 500 a Starlette response
    # (allow_nan=False), so only finite numbers survive.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def normalize_record(raw: Any) -> Optional[Dict]:
    """Whitelists one ai-quotas record into the brain's shape.

    Fail-soft: anything without a usable ``provider`` is dropped. A
    zero-epoch ``resets_at`` (gemini-3p sends 1970-01-01) normalizes
    to ``None`` — "resets in 1970" is never a real answer. Unknown
    fields are dropped, not carried (S5: least data out of the host).
    """
    if not isinstance(raw, dict):
        return None
    provider = _clean_str(raw.get("provider"))
    if provider is None:
        return None
    resets_at = _clean_str(raw.get("resets_at"))
    if resets_at is not None and resets_at.startswith("1970-01-01T00:00:00"):
        resets_at = None
    return {
        "provider": provider,
        "display_name": _clean_str(raw.get("display_name")),
        "kind": _clean_str(raw.get("kind")),
        "label": _clean_str(raw.get("label")),
        "used_percent": _clean_number(raw.get("used_percent")),
        "resets_at": resets_at,
        "state": _clean_str(raw.get("state")) or "unknown",
        "status_indicator": _clean_str(raw.get("status_indicator")),
        "status_summary": _clean_str(raw.get("status_summary")),
        "detail": _clean_str(raw.get("detail")),
        "cost_today_usd": _clean_number(raw.get("cost_today_usd")),
        "cost_month_to_date_usd": _clean_number(raw.get("cost_month_to_date_usd")),
    }


def _failure(reason: str) -> Dict:
    return {"providers": [], "reason": reason, "fetched_at": None}


_RESET_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def format_reset(iso: Optional[str]) -> Optional[str]:
    """Human, voice-friendly reset stamp (``Thu 18:59`` local time).

    Returns ``None`` for a missing stamp and the raw string when it
    cannot be parsed — never raises, never dumps bare ISO at the user.
    """
    if not iso:
        return None
    try:
        moment = datetime.fromisoformat(iso).astimezone()
    except (ValueError, OverflowError, OSError):
        return iso
    return f"{_RESET_DAYS[moment.weekday()]} {moment:%H:%M}"


class QuotaSource(Protocol):
    """One quota read for the cache (POST-02 S1).

    Returns ``{"providers": [...], "reason": str, "fetched_at": str|None}``
    — ``reason == ""`` on success, a fail-soft token otherwise. A source
    never raises for predictable failures; anything raised is mapped to
    ``"error"`` by the service.
    """

    def fetch(self) -> Dict:
        ...


class AiQuotasSource:
    """The host's external ``ai-quotas`` CLI as a ``QuotaSource``.

    Owns binary discovery (PATH, then the per-user ``~/.local/bin``
    install), the subprocess with its timeout, and the JSON parse plus
    record whitelisting. Read-only: stderr is discarded unread and
    nothing is logged or persisted (POST-02 S5).
    """

    def __init__(
        self,
        runner: Optional[_Runner] = None,
        binary: Optional[str] = None,
        timeout_s: float = QUOTAS_CLI_TIMEOUT_S,
    ):
        self._run = runner or _default_runner
        self._binary = binary
        self._timeout_s = timeout_s

    def fetch(self) -> Dict:
        binary = self._binary or ai_quotas_binary()
        if not binary:
            return _failure("ai-quotas-not-found")
        try:
            proc = self._run([binary, "check", "--json"], self._timeout_s)
        except subprocess.TimeoutExpired:
            return _failure("timeout")
        except OSError:
            return _failure("unavailable")
        except Exception:  # noqa: BLE001 — a quota read never breaks a card or a call
            return _failure("error")
        if proc.returncode != 0:
            return _failure("cli-failed")
        try:
            data = json.loads(proc.stdout or "")
        except (TypeError, ValueError):
            return _failure("bad-json")
        if not isinstance(data, list):
            return _failure("bad-json")
        records = [
            record for record in (normalize_record(item) for item in data)
            if record is not None
        ]
        if not records:
            return _failure("empty")
        records.sort(key=lambda record: (record["provider"], record["label"] or ""))
        return {
            "providers": records,
            "reason": "",
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }


class QuotaService:
    """Cached provider quota snapshots. Never raises, never blocks.

    Reads through an injectable ``QuotaSource`` (default: the host's
    ``ai-quotas`` CLI). ``snapshot()`` serves the fresh cache; an
    expired cache is served immediately (``stale: True``) while ONE
    background job refreshes it; with no data yet it answers
    ``not-ready`` and kicks the same deduplicated background job.
    ``refresh_sync()`` and ``prewarm()`` give tests and the boot seam
    a deterministic entry point.
    """

    def __init__(
        self,
        source: Optional[QuotaSource] = None,
        runner: Optional[_Runner] = None,
        clock: Optional[Callable[[], float]] = None,
        ttl_s: float = QUOTAS_TTL_S,
        failure_ttl_s: float = QUOTAS_FAILURE_TTL_S,
        timeout_s: float = QUOTAS_CLI_TIMEOUT_S,
        binary: Optional[str] = None,
        spawner: Optional[_Spawner] = None,
    ):
        self._source = source or AiQuotasSource(
            runner=runner, binary=binary, timeout_s=timeout_s
        )
        self._now = clock or time.monotonic
        self._ttl_s = ttl_s
        self._failure_ttl_s = failure_ttl_s
        self._spawn = spawner or _default_spawner
        self._lock = threading.Lock()
        self._cached: Optional[Tuple[float, Dict]] = None
        self._refreshing = False

    def snapshot(self) -> Dict:
        """``{"providers": [...], "reason", "fetched_at", "stale"}``.

        ``reason`` is ``""`` on success. Never raises, never blocks on
        a subprocess."""
        with self._lock:
            now = self._now()
            if self._cached is None:
                cold = True
                payload = None
            elif self._cached[0] > now:
                return self._copy(self._cached[1], stale=False)
            else:
                cold = False
                payload = self._copy(self._cached[1], stale=True)
        # The kick happens OUTSIDE the lock: a spawner that runs jobs
        # inline cannot deadlock on the non-reentrant lock.
        self._kick()
        if cold:
            return {
                "providers": [],
                "reason": "not-ready",
                "fetched_at": None,
                "stale": False,
            }
        return payload

    def refresh_sync(self) -> Dict:
        """Blocking load — tests and the boot prewarm thread only."""
        return self._load_into_cache()

    def prewarm(self) -> None:
        """Kick one background refresh (boot seam; no-op mid-flight)."""
        self._kick()

    def _kick(self) -> None:
        """Starts ONE background refresh. The in-flight flag flips
        under the lock; the spawn itself happens outside it, and a
        failing spawn resets the flag instead of wedging the service
        forever (S2 fail-soft)."""
        with self._lock:
            if self._refreshing:
                return
            self._refreshing = True
        try:
            self._spawn(self._background_refresh)
        except Exception:  # noqa: BLE001 — a failed kick must never wedge the flag
            with self._lock:
                self._refreshing = False

    def _background_refresh(self) -> None:
        try:
            self._load_into_cache()
        finally:
            with self._lock:
                self._refreshing = False

    def _load_into_cache(self) -> Dict:
        try:
            payload = self._source.fetch()
        except Exception:  # noqa: BLE001 — a bad source never breaks the cache
            payload = _failure("error")
        ttl = self._ttl_s if payload["reason"] == "" else self._failure_ttl_s
        with self._lock:
            self._cached = (self._now() + ttl, payload)
            return self._copy(payload, stale=False)

    @staticmethod
    def _copy(payload: Dict, stale: bool) -> Dict:
        return {
            "providers": [dict(record) for record in payload["providers"]],
            "reason": payload["reason"],
            "fetched_at": payload["fetched_at"],
            "stale": stale,
        }
