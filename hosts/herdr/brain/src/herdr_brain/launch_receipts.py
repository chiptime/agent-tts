"""Launch receipt store — idempotent session launches (parity-f7c S2).

``<state_dir>/launch-receipts.json`` remembers, per client-minted
``request_id``, the response of the FIRST successful ``POST
/session/new`` so a repeat (double-tap, duplicated send, retry) replays
the SAME body — one tab, one ``create_session`` call. Entries older
than a short TTL are pruned: a retry after expiry may create a second
tab; the TTL bounds the dedup window, it does not eliminate it.

Failures are never receipts: a launch that raises records nothing, so
the same id can retry for real.

A file that cannot be parsed is degraded, not obeyed and not fatal:
receipts are a dedup cache over data the server can recreate (unlike
the push-subscription store, whose bytes ARE the credentials), so the
store starts empty, logs loudly and heals on the next successful save.
Worst case is one duplicate tab, never a blocked launch.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional

from .crew.roster import write_private_atomic

logger = logging.getLogger(__name__)

#: Store file name inside the state dir; owned here.
LAUNCH_RECEIPTS_FILENAME = "launch-receipts.json"

#: How long a receipt still replays. Short on purpose (doc 03 §4).
LAUNCH_RECEIPT_TTL_S = 24 * 60 * 60.0

_Response = dict


class LaunchReceiptStore:
    """The only code that touches the launch-receipts file.

    One ``threading.Lock`` spans read → launch → register (doc 03 risk
    item 4): two in-flight identical requests serialize inside
    :meth:`launch_once` — the loser waits and then replays the winner's
    receipt. Receipted launches therefore serialize with each other; a
    personal PWA launches rarely, and exactly-one-tab beats throughput.
    """

    def __init__(
        self,
        state_dir: Path,
        clock: Callable[[], float] = time.time,
        ttl_s: float = LAUNCH_RECEIPT_TTL_S,
    ) -> None:
        self._dir = Path(state_dir)
        self._path = self._dir / LAUNCH_RECEIPTS_FILENAME
        self._clock = clock
        self._ttl = ttl_s
        self._lock = threading.Lock()
        self._entries: Dict[str, dict] = {}
        self._loaded = False  # lazy: boot reads nothing (pairing discipline)

    # -- the launch-dedup seam ---------------------------------------------

    def launch_once(self, request_id: str, launch: Callable[[], _Response]) -> _Response:
        """One exactly-once launch for ``request_id``.

        Under the lock: prune expired entries, replay a live receipt's
        cached response, otherwise run ``launch()`` and register ITS
        return value. A ``launch`` that raises records nothing — the
        id stays retryable. A disk that refuses the save logs loudly
        and keeps the receipt in memory (the tab already exists; the
        response must not fail over a cache).
        """
        with self._lock:
            self._ensure_loaded()
            self._prune_expired_locked()
            entry = self._entries.get(request_id)
            if entry is not None:
                return dict(entry["response"])
            response = launch()
            self._entries[request_id] = {
                "request_id": request_id,
                "response": dict(response),
                "ts": self._clock(),
            }
            self._save_locked()
            return dict(response)

    # -- internals -----------------------------------------------------------

    def _ensure_loaded(self) -> None:
        """First use loads the file (caller holds the lock)."""
        if self._loaded:
            return
        self._loaded = True
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            rows = raw["receipts"] if isinstance(raw, dict) else None
            if not isinstance(rows, dict):
                raise ValueError("unexpected store shape")
            self._entries = {
                str(rid): row for rid, row in rows.items() if isinstance(row, dict)
            }
        except FileNotFoundError:
            self._entries = {}
        except (OSError, ValueError, TypeError, KeyError) as exc:
            # Degrade + heal (see module docstring); never obey junk.
            logger.error(
                "launch receipt store unreadable, dedup restarts empty: %s: %s",
                self._path, exc,
            )
            self._entries = {}

    def _prune_expired_locked(self) -> None:
        """Drops entries past the TTL; the next save persists the cut."""
        horizon = self._clock() - self._ttl
        self._entries = {
            rid: row for rid, row in self._entries.items()
            if float(row.get("ts") or 0.0) > horizon
        }

    def _save_locked(self) -> None:
        payload = json.dumps(
            {"version": 1, "receipts": self._entries},
            ensure_ascii=False,
            indent=2,
        ) + "\n"
        try:
            # tmp+rename, 0600 file, 0700 dir — the roster's discipline.
            write_private_atomic(self._path, payload)
        except OSError as exc:
            logger.error(
                "launch receipt save failed (dedup is in-memory only): %s: %s",
                self._path, exc,
            )
