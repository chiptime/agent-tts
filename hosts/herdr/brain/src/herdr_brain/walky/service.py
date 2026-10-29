"""The brain-messages service: send, hold, deliver, inbox, cancel.

One place owns the state machine every message rides::

    queued ──attempt──▶ typed                 (delivered: header in the pane)
      │                 uncertain             (typing raised; never retried)
      │                 refused               (pane closed / session replaced /
      │                                       multiplexer cannot type)
      ▼
     held ◀──retry while receiver busy or drafting──
      │
      └──60 min──▶ waiting_in_inbox           (never typed; inbox only)

    any of queued/held ──sender cancel──▶ canceled

Anti-loop limits are enforced BEFORE a message is recorded (a refused
send leaves no state behind): bodies cap at :data:`MAX_BODY_CHARS`, a
sender may emit :data:`SENDER_MAX` messages per
:data:`WINDOW_S`, an unordered pair may trade :data:`PAIR_MAX`, and a
reply chain stops after :data:`MAX_HOPS` hops. Every accepted send,
reply and cancel writes one audit line with a masked body preview.

The delivery loop is :meth:`WalkyService.tick` — call it from the
watcher's poll cycle or after any send; it is idempotent and never
raises for a routine mux hiccup (the message simply stays in flight).
"""

from __future__ import annotations

import secrets
import time
from pathlib import Path
from string import ascii_lowercase, digits
from typing import Callable, List, Optional

from ..audit import AuditLog
from ..herdr import AgentInfo, HerdrError
from ..mux import MuxRefusal
from ..secrets_mask import mask_text
from .delivery import agent_line, delivery_gate, operator_line
from .errors import MessageNotFound, WalkyRefusal
from .identity import (
    AgentSender,
    WhoRow,
    find_pane,
    format_address,
    parse_address,
    resolve_sender,
    who_rows,
)
from .store import DEAD_STATES, Message, MessageStore

#: Safety rails (spec S13).
MAX_BODY_CHARS = 4000
WINDOW_S = 600.0
SENDER_MAX = 30
PAIR_MAX = 6
MAX_HOPS = 4
HOLD_MAX_S = 3600.0

#: States that still owe a delivery attempt.
IN_FLIGHT = ("queued", "held")

_ID_ALPHABET = ascii_lowercase + digits


def default_id_factory() -> Callable[[], str]:
    def factory() -> str:
        return "bm_" + "".join(secrets.choice(_ID_ALPHABET) for _ in range(10))

    return factory


def _preview(body: str) -> str:
    """Mask BEFORE clipping (the S14 rule): a clip could cut a secret in
    half the detectors no longer match; the mask placeholder survives."""
    return mask_text(body)[:120]


class WalkyService:
    """Everything the CLI and the server route drive, mux injected."""

    def __init__(
        self,
        state_dir: Path,
        mux,
        clock: Optional[Callable[[], float]] = None,
        audit: Optional[AuditLog] = None,
        id_factory: Optional[Callable[[], str]] = None,
    ) -> None:
        self._clock = clock or time.time
        self._mux = mux
        self._ids = id_factory or default_id_factory()
        self._audit = audit or AuditLog(Path(state_dir) / "audit.log")
        self._store = MessageStore(Path(state_dir), clock=self._clock)

    # -- identity -------------------------------------------------------------

    def who(self, me_pane: Optional[str] = None) -> List[WhoRow]:
        return who_rows(self._panes(), me_pane)

    def resolve_sender(self, env) -> AgentSender:
        return resolve_sender(env, self._panes())

    def _panes(self) -> List[AgentInfo]:
        try:
            return self._mux.list_agents()
        except Exception as exc:  # noqa: BLE001 — one refusal shape for the CLI
            raise WalkyRefusal(f"cannot see the panes right now: {exc}") from exc

    # -- sending ---------------------------------------------------------------

    def send(self, sender: AgentSender, address: str, body: str) -> Message:
        return self._deliver_new(
            from_kind="agent",
            from_key=sender.pane_id,
            from_address=sender.address,
            from_harness=sender.harness,
            from_label=sender.label,
            from_device=None,
            address=address,
            body=body,
            hops=0,
            parent_id=None,
            action="msg.send",
        )

    def operator_send(self, device: str, address: str, body: str) -> Message:
        return self._deliver_new(
            from_kind="operator",
            from_key=f"operator:{device}",
            from_address=None,
            from_harness="",
            from_label="",
            from_device=device,
            address=address,
            body=body,
            hops=0,
            parent_id=None,
            action="msg.send",
        )

    def reply(self, sender: AgentSender, in_reply_to: str, body: str) -> Message:
        parent = self._get(in_reply_to)
        if parent.to_pane != sender.pane_id:
            raise WalkyRefusal("that message is not in your inbox")
        if parent.from_kind == "operator":
            raise WalkyRefusal(
                "operator messages cannot be answered from a pane; "
                "the operator reads this pane's output directly"
            )
        if parent.hops >= MAX_HOPS:
            raise WalkyRefusal(
                f"reply chain limit reached ({MAX_HOPS} hops); start a fresh send instead"
            )
        return self._deliver_new(
            from_kind="agent",
            from_key=sender.pane_id,
            from_address=sender.address,
            from_harness=sender.harness,
            from_label=sender.label,
            from_device=None,
            address=parent.from_address or "",
            body=body,
            hops=parent.hops + 1,
            parent_id=parent.id,
            action="msg.reply",
        )

    def _deliver_new(
        self,
        *,
        from_kind: str,
        from_key: str,
        from_address: Optional[str],
        from_harness: str,
        from_label: str,
        from_device: Optional[str],
        address: str,
        body: str,
        hops: int,
        parent_id: Optional[str],
        action: str,
    ) -> Message:
        if not body or not body.strip():
            raise WalkyRefusal("refusing to send an empty message")
        if len(body) > MAX_BODY_CHARS:
            raise WalkyRefusal(
                f"message body is {len(body)} characters; the maximum is {MAX_BODY_CHARS}"
            )
        to_pane, to_token = parse_address(address)
        if to_pane == from_key:
            raise WalkyRefusal("refusing to send a message to yourself")

        panes = self._panes()
        target = find_pane(panes, to_pane)
        if target is None:
            raise WalkyRefusal(
                f"unknown pane {to_pane}; get live addresses from: herdr-brain msg who"
            )
        if target.session_value != to_token:
            raise WalkyRefusal(
                f"the pane {to_pane} was replaced (its agent session changed); "
                "ask for a fresh address with: herdr-brain msg who"
            )
        self._check_limits(from_key, to_pane)

        now = self._clock()
        message = Message(
            id=self._ids(),
            from_kind=from_kind,
            from_key=from_key,
            from_address=from_address,
            from_harness=from_harness,
            from_label=from_label,
            from_device=from_device,
            to_pane=to_pane,
            to_token=to_token,
            body=body,
            state="queued",
            created=now,
            updated=now,
            hops=hops,
            parent_id=parent_id,
        )
        self._store.upsert(message)
        self._store.save()
        self._audit.record(
            {
                "action": action,
                "pane_id": to_pane,
                "device": from_device,
                "detail": {
                    "from": from_address or from_key,
                    "to": format_address(to_pane, to_token),
                    "chars": len(body),
                    "hops": hops,
                    "preview": _preview(body),
                },
            }
        )
        self.tick()
        return self._get(message.id)

    def _check_limits(self, from_key: str, to_pane: str) -> None:
        now = self._clock()
        recent = [m for m in self._store.all() if now - m.created <= WINDOW_S]
        sender_count = sum(1 for m in recent if m.from_key == from_key)
        if sender_count >= SENDER_MAX:
            raise WalkyRefusal(
                f"sender rate limit reached: {SENDER_MAX} messages per "
                f"{int(WINDOW_S / 60)} minutes; wait before sending more"
            )
        pair = frozenset({from_key, to_pane})
        pair_count = sum(
            1
            for m in recent
            if frozenset({m.from_key, m.to_pane}) == pair
        )
        if pair_count >= PAIR_MAX:
            raise WalkyRefusal(
                f"pair rate limit reached: {PAIR_MAX} messages per "
                f"{int(WINDOW_S / 60)} minutes between these two panes; slow down"
            )

    # -- inbox / status / cancel -------------------------------------------------

    def inbox(self, pane_id: str, message_id: Optional[str] = None):
        if message_id is not None:
            message = self._get(message_id)
            if message.to_pane != pane_id or message.state in DEAD_STATES:
                raise MessageNotFound(f"no message {message_id} in the inbox of {pane_id}")
            if not message.read:
                message.read = True
                message.updated = self._clock()
                self._store.upsert(message)
                self._store.save()
            return message

        unread = [
            m
            for m in self._store.all()
            if m.to_pane == pane_id and not m.read and m.state not in DEAD_STATES
        ]
        for m in unread:
            m.read = True
            m.updated = self._clock()
            self._store.upsert(m)
        if unread:
            self._store.save()
        return unread

    def status(self, message_id: str) -> Message:
        return self._get(message_id)

    def cancel(self, sender_pane: str, message_id: str) -> str:
        message = self._get(message_id)
        if message.from_key != sender_pane:
            raise WalkyRefusal("you can only cancel your own messages")
        if message.state not in IN_FLIGHT:
            if message.state == "typed":
                raise WalkyRefusal("already typed, nothing to take back")
            raise WalkyRefusal(f"nothing to take back: the message is {message.state}")
        former = message.state
        message.state = "canceled"
        message.reason = "withdrawn by the sender before delivery"
        message.read = True  # withdrawn: never shown in the receiver's inbox
        message.updated = self._clock()
        self._store.upsert(message)
        self._store.save()
        self._audit.record(
            {
                "action": "msg.cancel",
                "pane_id": message.to_pane,
                "detail": {"id": message.id, "outcome": "canceled", "state": former},
            }
        )
        return f"canceled ({former}; never typed)"

    # -- the delivery loop ---------------------------------------------------------

    def tick(self) -> List[Message]:
        """One delivery pass; returns the messages whose state changed."""
        in_flight = [m for m in self._store.all() if m.state in IN_FLIGHT]
        if not in_flight:
            return []
        now = self._clock()
        changed: List[Message] = []

        try:
            panes = self._mux.list_agents()
        except Exception:  # noqa: BLE001 — a down mux keeps messages in flight
            return []

        for message in in_flight:
            if now - message.created >= HOLD_MAX_S:
                self._move(message, changed, "waiting_in_inbox",
                           "hold timed out after 60 minutes; left waiting in the inbox")
                continue
            target = find_pane(panes, message.to_pane)
            if target is None:
                self._move(message, changed, "refused", "the receiving pane closed")
                continue
            if target.session_value != message.to_token:
                self._move(
                    message, changed, "refused",
                    "the receiving pane's agent session was replaced",
                )
                continue
            if target.status not in ("idle", "done"):
                self._hold(message, changed, f"receiver is {target.status}")
                continue
            try:
                screen = self._mux.read_screen(message.to_pane)
            except Exception:  # noqa: BLE001 — screen read is best-effort
                self._hold(message, changed, "receiver screen unreadable")
                continue
            may_type, reason = delivery_gate(target.status, screen or "")
            if not may_type:
                self._hold(message, changed, reason)
                continue
            line = (
                operator_line(message.id, message.from_device or "?", message.body)
                if message.from_kind == "operator"
                else agent_line(
                    message.id,
                    message.from_address or "",
                    message.from_harness or "",
                    message.from_label or "",
                )
            )
            try:
                self._mux.pane_run(message.to_pane, line)
            except MuxRefusal as exc:
                if exc.reason == "unsupported":
                    self._move(message, changed, "refused", f"multiplexer cannot type: {exc}")
                else:
                    self._move(message, changed, "uncertain", f"typing failed: {exc}")
                continue
            except HerdrError as exc:
                self._move(message, changed, "uncertain", f"typing outcome unknown: {exc}")
                continue
            self._move(message, changed, "typed", "")

        if changed:
            self._store.save()
        return changed

    # -- internals -----------------------------------------------------------------

    def _get(self, message_id: str) -> Message:
        try:
            return self._store.get(message_id)
        except KeyError:
            raise MessageNotFound(f"no message {message_id!r}") from None

    def _move(self, message: Message, changed: List[Message], state: str, reason: str) -> None:
        if message.state == state and message.reason == reason:
            return
        message.state = state
        message.reason = reason
        message.updated = self._clock()
        self._store.upsert(message)
        changed.append(message)

    def _hold(self, message: Message, changed: List[Message], reason: str) -> None:
        self._move(message, changed, "held", reason)
