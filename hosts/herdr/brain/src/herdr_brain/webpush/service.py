"""The facade the rest of the brain talks to (S5).

``WebPushService`` composes the whole package: key resolution (env →
``vapid.json`` → generate once, soft-off if creation is unavailable), the
subscription store with its audit trail, the coalescing coordinator,
encrypted fan-out with store
accounting (prune on 404/410, witnessed eviction), and the prompt-cache
warning tick. Everything injectable — transport, clock, environ — so
tests and the CLI verbs drive it without a network.

Route wiring, the service worker and watcher hooks live OUTSIDE the
package; this object is the contract between them.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional, Tuple

from ..audit import AuditLog
from .cachewarn import CacheWarnEngine
from .coordinator import CACHE_TAG, TEST_TAG, NotificationCoordinator
from .sender import PushSender
from .store import SubscriptionStore, push_service_origin
from .vapid import VapidKeys, load_or_create_keys

logger = logging.getLogger(__name__)

#: What a caller may pass as ``panes`` to :meth:`WebPushService.tick`.
PaneActivity = Tuple[str, str, float]  # (pane_id, label, last_activity)


class WebPushService:
    """One object per process; safe to call from any thread the
    coordinator and store are called from serially (the watcher poll)."""

    def __init__(
        self,
        state_dir: Path,
        transport: Optional[Callable] = None,
        clock: Optional[Callable[[], float]] = None,
        audit_log: Optional[AuditLog] = None,
        _environ: Optional[Dict[str, str]] = None,
        coalesce_window_s: float = 2.0,
        cachewarn_ttl_s: float = 300.0,
        cachewarn_warn_before_s: float = 60.0,
    ):
        self._clock = clock or time.time
        self._audit = audit_log
        environ = os.environ if _environ is None else _environ
        try:
            self._keys: Optional[VapidKeys] = load_or_create_keys(
                Path(state_dir), environ=environ
            )
        except OSError as exc:
            # A state dir we cannot write must not take the brain down:
            # push stays off (routes still answer) and the reason is logged.
            logger.error("web push disabled: cannot create VAPID keys: %s", exc)
            self._keys = None
        self._store = SubscriptionStore(Path(state_dir), clock=self._clock)
        self._sender = (
            PushSender(self._keys, transport=transport, clock=self._clock)
            if self._keys is not None
            else None
        )
        self._cache_engine = CacheWarnEngine(
            ttl_s=cachewarn_ttl_s, warn_before_s=cachewarn_warn_before_s
        )
        self._coordinator = NotificationCoordinator(
            window_s=coalesce_window_s,
            clock=self._clock,
            sink=self._dispatch,
        )
        if self._store.corrupt:
            logger.error(
                "push subscriptions are corrupt; refusing sends until repaired"
            )

    # -- identity -----------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """False ⇒ soft-off: the server runs, nothing sends (doc 08)."""
        return self._keys is not None

    def public_key(self) -> Optional[str]:
        """The base64url ``applicationServerKey`` for the PWA, or None."""
        return self._keys.public_b64url if self._keys is not None else None

    # -- subscriptions ------------------------------------------------------

    def subscribe(self, body: dict, device: Optional[str] = None) -> dict:
        """Validates and persists one browser ``PushSubscription``."""
        endpoint = str(body.get("endpoint") or "").strip()
        keys = body.get("keys") or {}
        p256dh = str(keys.get("p256dh") or "").strip()
        auth = str(keys.get("auth") or "").strip()
        if not endpoint.startswith(("http://", "https://")):
            raise ValueError("subscription needs an http(s) endpoint")
        if not p256dh or not auth:
            raise ValueError("subscription needs keys.p256dh and keys.auth")
        record = self._store.add(
            endpoint,
            p256dh,
            auth,
            device=device or "",
            user_agent=str(body.get("user_agent") or "")[:160],
            replaces=body.get("replaces"),
        )
        self._audit_record(
            "push.subscribe",
            record.endpoint,
            record.device,
            {"replaces": body.get("replaces")} if body.get("replaces") else {},
        )
        return {
            "endpoint": record.endpoint,
            "device": record.device,
            "created_at": record.created_at,
        }

    def unsubscribe(self, endpoint: str, device: Optional[str] = None) -> bool:
        removed = self._store.remove(str(endpoint))
        if removed:
            self._audit_record("push.unsubscribe", str(endpoint), device or "")
        return removed

    def subscriptions(self) -> list:
        """Endpoints, oldest first — the ``push list`` view."""
        return [record.endpoint for record in self._store.list()]

    # -- notifications ------------------------------------------------------

    def notify_transition(
        self,
        pane_id: str,
        agent: str,
        status: str,
        text: Optional[str] = None,
        question: Optional[str] = None,
        gate_id: Optional[str] = None,
        label: Optional[str] = None,
    ) -> None:
        """Feeds a watcher transition into the coalescing window."""
        self._coordinator.transition(
            pane_id,
            label or agent,
            status,
            text=text,
            question=question,
            gate_id=gate_id,
        )

    def notify_handled(
        self, pane_id: str, agent: Optional[str] = None, label: Optional[str] = None
    ) -> None:
        """The pane no longer needs the operator: cancel or retract."""
        self._coordinator.handled(pane_id, label or agent)

    def test_notification(self) -> dict:
        """One round-trip notification per device (``push-test``)."""
        payload = {
            "v": 1,
            "kind": "test",
            "pane_id": None,
            "title": "Web Push operativo",
            "body": "El canal de avisos de tu equipo funciona.",
            "tag": TEST_TAG,
        }
        return {"sent": self._dispatch(payload, TEST_TAG)}

    def tick(
        self,
        now: Optional[float] = None,
        panes: Optional[Iterable[PaneActivity]] = None,
    ) -> None:
        """Flushes elapsed coalescing windows; emits due cache warnings."""
        self._coordinator.tick(now)
        if panes is None:
            return
        current = self._clock() if now is None else now
        for pane_id, label, last_activity in panes:
            due = self._cache_engine.observe(pane_id, float(last_activity), current)
            if due is None:
                continue
            minutes = int(due["idle_s"] // 60)
            remaining = int(due["expires_in_s"])
            self._dispatch(
                {
                    "v": 1,
                    "kind": "cache-warn",
                    "pane_id": pane_id,
                    "title": "Contexto caliente a punto de caducar",
                    "body": (
                        f"{label} lleva {minutes} min sin actividad; su contexto "
                        f"caduca en menos de {remaining} s."
                    ),
                    "tag": CACHE_TAG,
                },
                CACHE_TAG,
            )

    # -- delivery -----------------------------------------------------------

    def _dispatch(self, payload: dict, topic: str) -> int:
        """Encrypts and sends to every subscription; keeps the store
        honest about who is gone. Sends nothing when soft-off, when the
        store is corrupt, or on a store read failure — never a guess."""
        if self._sender is None:
            return 0
        if self._store.corrupt:
            logger.error(
                "web push skipped: subscription store is corrupt (%s)",
                self._store.corrupt,
            )
            return 0
        try:
            subscriptions = self._store.list()
        except Exception as exc:  # noqa: BLE001 — StoreError: refuse loudly
            logger.error("web push skipped: cannot list subscriptions: %s", exc)
            return 0
        sent = 0
        witnessed: set = set()
        for record in subscriptions:
            try:
                status = self._sender.send(record, payload, topic)
            except Exception as exc:  # noqa: BLE001 — one bad row must not
                # abort the others; it ages out through the failure streak.
                logger.warning(
                    "web push to %s could not be built: %s", record.endpoint, exc
                )
                status = 0
            self._store.record_result(record.endpoint, status)
            if 200 <= status < 300:
                sent += 1
                witnessed.add(push_service_origin(record.endpoint))
        if subscriptions:
            self._store.evict_witnessed(witnessed)
        return sent

    def _audit_record(
        self, action: str, endpoint: str, device: str, detail: Optional[dict] = None
    ) -> None:
        if self._audit is None:
            return
        entry: dict = {"action": action, "device": device or None, "detail": detail or {}}
        entry["detail"] = {**entry["detail"], "endpoint": endpoint}
        self._audit.record(entry)
