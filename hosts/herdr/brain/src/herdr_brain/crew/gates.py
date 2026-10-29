"""Gate ownership across the crew (S7, doc 10 risk 4).

An approval gate lives on the host that owns the agent — the host
that ran the send and opened the gate against its own store. The
lead's role when asked to resolve a gate is ROUTING, never deciding:

- a gate this host owns resolves through the local store (whose own
  approve-once semantics are the invariant of record);
- a gate owned by an enrolled peer is FORWARDED, and the owner's
  answer travels back verbatim — a 404 from the owner is the answer,
  not something to retry or reinterpret;
- a gate owned by nobody this roster knows is dead: refusing beats
  resolving it into a store that never held it.

``GateIndex`` is the lead's memory of which host owns which remote
gate, filled when the lead learns of a remote gate (a forwarded send
or a proxied ``approval/current``). Concurrent resolves funnel into
the owner's single store, so exactly one applies.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Optional

#: Handler contract: (gate_id, decision) -> (status, payload).
LocalResolve = Callable[[str, str], tuple]
#: Router contract: (peer_name, gate_id, decision) -> (status, payload).
Forward = Callable[[str, str, str], tuple]


@dataclass
class GateDecision:
    """The owner's verdict as the lead will hand it back."""

    applied: bool
    status: int
    payload: object
    owner: Optional[str]
    forwarded: bool


class GateIndex:
    """gate_id → owning host, recorded when the lead learns of a
    remote gate. Thread-safe; the lead refreshes entries whenever a
    peer reports a live gate."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owners: dict[str, str] = {}

    def record(self, gate_id: str, host: str) -> None:
        with self._lock:
            self._owners[gate_id] = host

    def owner_of(self, gate_id: str) -> Optional[str]:
        with self._lock:
            return self._owners.get(gate_id)

    def forget(self, gate_id: str) -> None:
        with self._lock:
            self._owners.pop(gate_id, None)


class GateRouter:
    """Routes a gate decision to the host that owns the gate."""

    def __init__(
        self,
        this_host: str,
        local_resolve: LocalResolve,
        forward: Forward,
        known_members: Optional[Callable[[], set]] = None,
    ):
        self.this_host = this_host
        self._local_resolve = local_resolve
        self._forward = forward
        self._known_members = known_members

    def resolve(
        self,
        gate_id: str,
        decision: str,
        *,
        index: Optional[GateIndex] = None,
        owner_host: Optional[str] = None,
    ) -> GateDecision:
        """Apply a decision where the gate lives.

        ``owner_host`` (explicit) beats ``index`` (learned). An owner
        that is neither this host nor a known member is not routed at
        all — unknown ownership can never become a local resolve,
        because a store that never held the gate would happily "apply"
        a decision to nothing.
        """
        owner = owner_host or (index.owner_of(gate_id) if index is not None else None)
        if owner is None or owner == self.this_host:
            status, payload = self._local_resolve(gate_id, decision)
            return GateDecision(
                applied=200 <= int(status) < 300,
                status=int(status),
                payload=payload,
                owner=self.this_host,
                forwarded=False,
            )

        if self._known_members is not None:
            known = self._known_members() or set()
            if owner not in known:
                # a stranger's gate is a dead gate
                return GateDecision(
                    applied=False,
                    status=404,
                    payload={"detail": "gate dead"},
                    owner=owner,
                    forwarded=False,
                )

        status, payload = self._forward(owner, gate_id, decision)
        return GateDecision(
            applied=200 <= int(status) < 300,
            status=int(status),
            payload=payload,
            owner=owner,
            forwarded=True,
        )
