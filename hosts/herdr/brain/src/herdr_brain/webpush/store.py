"""Push subscription store — one private JSON file, fail-closed (risk 1).

``<state_dir>/push-subscriptions.json`` holds one row per device with
its delivery keys, written atomically (tmp + rename, 0600 file, 0700
dir). The lifecycle of a row mirrors doc 08: a re-subscribing device
names the endpoint it ``replaces``; a 404/410 from the push service
prunes it on the spot; other failures accumulate and only evict the
device when a sibling of the SAME push service delivered successfully —
a global outage must never prune healthy phones.

A file that cannot be parsed is never reset: every write and every
recipient listing raises :class:`StoreError` loudly (and the sender
sends nothing) until the operator repairs or removes the file. Wiping
it to "keep working" would silently kill push on every device.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlsplit

from ..pairing import ensure_private_dir
from .errors import StoreError

logger = logging.getLogger(__name__)

#: Store file name inside the state dir; owned here.
STORE_FILENAME = "push-subscriptions.json"

#: Default bound: the herd is the operator's phones, not a crowd.
MAX_SUBSCRIPTIONS = 8

#: Consecutive non-gone failures before a row is evictable (witnessed).
EVICT_AFTER = 5

#: Statuses the push service uses to say "this endpoint is dead".
GONE_STATUSES = (404, 410)


@dataclass
class Subscription:
    """One device's delivery identity."""

    endpoint: str
    p256dh: str  # base64url P-256 public key from the browser
    auth: str  # base64url auth secret
    created_at: float
    device: str = ""  # operator-facing label, attributed at subscribe
    user_agent: str = ""
    failures: int = 0
    last_success: float = 0.0


def push_service_origin(endpoint: str) -> str:
    """``scheme://host`` of a subscription endpoint — the witness group."""
    parts = urlsplit(endpoint)
    return f"{parts.scheme}://{parts.netloc}"


def _row_to_subscription(endpoint: str, row: dict) -> Subscription:
    keys = row.get("keys") or {}
    return Subscription(
        endpoint=str(row.get("endpoint") or endpoint),
        p256dh=str(keys.get("p256dh") or ""),
        auth=str(keys.get("auth") or ""),
        created_at=float(row.get("created_at") or 0.0),
        device=str(row.get("device") or ""),
        user_agent=str(row.get("user_agent") or ""),
        failures=int(row.get("failures") or 0),
        last_success=float(row.get("last_success") or 0.0),
    )


class SubscriptionStore:
    """The only code that touches the subscriptions file."""

    def __init__(
        self,
        state_dir: Path,
        clock=time.time,
        max_subscriptions: int = MAX_SUBSCRIPTIONS,
    ):
        self._dir = Path(state_dir)
        self._path = self._dir / STORE_FILENAME
        self._clock = clock
        self._max = max_subscriptions
        self._rows: dict = {}
        self.corrupt: Optional[str] = None
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                subs = raw["subscriptions"] if isinstance(raw, dict) else None
                if not isinstance(subs, dict):
                    raise ValueError("unexpected store shape")
                self._rows = {
                    str(ep): _row_to_subscription(str(ep), row)
                    for ep, row in subs.items()
                }
            except (OSError, ValueError, TypeError, KeyError) as exc:
                # Fail closed: keep the bytes, refuse everything, say why.
                self.corrupt = f"cannot load {self._path}: {exc}"
                logger.error("push subscription store is corrupt: %s", self.corrupt)

    # -- guards -------------------------------------------------------------

    def _writable(self) -> None:
        if self.corrupt:
            raise StoreError(self.corrupt)

    # -- write side ---------------------------------------------------------

    def add(
        self,
        endpoint: str,
        p256dh: str,
        auth: str,
        device: str = "",
        user_agent: str = "",
        replaces: Optional[str] = None,
        _now: Optional[float] = None,
    ) -> Subscription:
        """Upserts one endpoint; ``replaces`` retires the old row in the
        same atomic write (a device that re-subscribes names its dead
        twin — nobody unsubscribes it for us)."""
        self._writable()
        if not str(endpoint).startswith(("http://", "https://")):
            raise ValueError("endpoint must be an http(s) URL")
        if not p256dh or not auth:
            raise ValueError("subscription needs keys.p256dh and keys.auth")
        record = Subscription(
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
            created_at=self._clock() if _now is None else _now,
            device=device or "",
            user_agent=user_agent or "",
        )
        self._rows.pop(replaces, None)
        existing = self._rows.get(endpoint)
        if existing is not None:
            # Re-subscribe keeps the original attribution and age.
            record.created_at = existing.created_at
            if not record.device:
                record.device = existing.device
            if not record.user_agent:
                record.user_agent = existing.user_agent
        self._rows[endpoint] = record
        self._evict_over_bound()
        self._save()
        return record

    def remove(self, endpoint: str) -> bool:
        self._writable()
        if endpoint in self._rows:
            del self._rows[endpoint]
            self._save()
            return True
        return False

    def record_result(self, endpoint: str, status: int) -> None:
        """One send outcome for one device (``0`` = transport failure)."""
        self._writable()
        record = self._rows.get(endpoint)
        if record is None:
            return
        if status in GONE_STATUSES:
            del self._rows[endpoint]
            self._save()
            return
        if 200 <= status < 300:
            record.failures = 0
            record.last_success = self._clock()
        else:
            record.failures += 1
        self._save()

    def evict_witnessed(self, witnessed_origins: set) -> List[str]:
        """Evicts failed-out rows whose push service proved itself alive.

        ``witnessed_origins`` are the origins that delivered at least one
        success in the same send round; an origin with no witness keeps
        its failing devices (the outage is the service's, not theirs).
        """
        self._writable()
        evicted: List[str] = []
        for endpoint, record in list(self._rows.items()):
            if record.failures >= EVICT_AFTER and (
                push_service_origin(endpoint) in witnessed_origins
            ):
                del self._rows[endpoint]
                evicted.append(endpoint)
        if evicted:
            self._save()
        return evicted

    # -- read side ----------------------------------------------------------

    def list(self) -> List[Subscription]:
        """All subscriptions, oldest first; raises on a corrupt store."""
        if self.corrupt:
            raise StoreError(self.corrupt)
        return sorted(self._rows.values(), key=lambda r: r.created_at)

    # -- internals ----------------------------------------------------------

    def _evict_over_bound(self) -> None:
        while len(self._rows) > self._max:
            oldest = min(self._rows.values(), key=lambda r: r.created_at)
            del self._rows[oldest.endpoint]

    def _save(self) -> None:
        ensure_private_dir(self._dir)
        payload = json.dumps(
            {
                "version": 1,
                "subscriptions": {
                    ep: {
                        "endpoint": rec.endpoint,
                        "keys": {"p256dh": rec.p256dh, "auth": rec.auth},
                        "created_at": rec.created_at,
                        "device": rec.device,
                        "user_agent": rec.user_agent,
                        "failures": rec.failures,
                        "last_success": rec.last_success,
                    }
                    for ep, rec in self._rows.items()
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        tmp = self._dir / (STORE_FILENAME + ".tmp")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, payload.encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
        except OSError as exc:
            tmp.unlink(missing_ok=True)  # never leave a partial state
            raise StoreError(f"cannot save {self._path}: {exc}") from exc
