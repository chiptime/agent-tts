"""Brain messages over the existing authenticated crew star.

The owner alone delivers. An origin keeps private receipts in a separate
outbox, using the existing Message schema without changing old ledgers.
Network calls never hold the local delivery lock: two peers replying at
the same time must not wait for each other's filesystem lock.
"""

from contextlib import contextmanager
from dataclasses import asdict, replace
import fcntl
import os
from pathlib import Path
import re
import threading

from ..crew.client import CrewRemoteRefused, CrewUnreachable
from ..crew.roster import NAME_RE
from ..pairing import ensure_private_dir
from ..secrets_mask import mask_text
from .errors import MessageNotFound, StoreError, WalkyRefusal
from .identity import WhoRow, format_address, parse_address
from .routing import qualify, route_address
from .service import (
    MAX_BODY_CHARS, MAX_HOPS, PAIR_MAX, SENDER_MAX, WINDOW_S,
    WalkyService, _preview,
)
from .store import Message, MessageStore


class FederatedWalkyService(WalkyService):
    def __init__(self, state_dir, mux, *, gateway=None, **kwargs):
        super().__init__(state_dir, mux, **kwargs)
        self.gateway = gateway
        self._outbox = MessageStore(Path(state_dir) / "crew-outbox", clock=self._clock)
        self._op_dir = Path(state_dir) / "crew-outbox"
        self._mutex = threading.RLock()
        self._depth = 0

    @contextmanager
    def _locked(self):
        """Serialize admission/cancel/typing across the watcher and CLIs.

        The ledger's save lock only protects the file replacement, not
        a read-check-type transition. This separate lock covers that
        transition; nested tick() reuses the same descriptor.
        """
        with self._mutex:
            if self._depth:
                yield
                return
            ensure_private_dir(self._op_dir)
            fd = os.open(self._op_dir / "operation.lock", os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                self._depth += 1
                try:
                    yield
                finally:
                    self._depth -= 1
            finally:
                os.close(fd)

    def _roster(self):
        roster = self.gateway.roster() if self.gateway is not None else None
        if self.gateway is not None and roster is None:
            raise WalkyRefusal("crew roster unavailable; nothing was sent")
        return roster

    def _client(self, host):
        roster = self._roster()
        via = roster.lead_name if roster.role == "peer" else host
        client = self.gateway.client_for(via)
        if client is None:
            raise WalkyRefusal(f"crew host {host} unavailable; nothing was sent")
        return client

    def _rpc(self, host, operation, payload):
        return self._client(host).message(operation, {**payload, "host": host})

    def who(self, me_pane=None):
        rows = super().who(me_pane)
        roster = self._roster()
        if roster is None or not roster.has_crew:
            return rows
        targets = [roster.lead_name] if roster.role == "peer" else list(roster.peers)
        for host in targets:
            try:
                response = self._client(host).message("who", {"fleet": roster.role == "peer"})
                for raw in response["rows"]:
                    if raw.get("host") != roster.self_name:
                        rows.append(WhoRow(**{k: v for k, v in raw.items() if k != "host"}))
            except (CrewUnreachable, CrewRemoteRefused, WalkyRefusal, KeyError, TypeError):
                rows.append(WhoRow("-", "", "", "unreachable", f"[{host}] unreachable"))
        return rows

    def _check_limits(self, from_key, to_pane):
        recent = [m for m in [*self._store.all(), *self._outbox.all()]
                  if self._clock() - m.created <= WINDOW_S]
        if sum(m.from_key == from_key for m in recent) >= SENDER_MAX:
            raise WalkyRefusal(
                f"sender rate limit reached: {SENDER_MAX} messages per "
                f"{int(WINDOW_S / 60)} minutes; wait before sending more"
            )
        pair = frozenset({from_key, to_pane})
        if sum(frozenset({m.from_key, m.to_pane}) == pair for m in recent) >= PAIR_MAX:
            raise WalkyRefusal(
                f"pair rate limit reached: {PAIR_MAX} messages per "
                f"{int(WINDOW_S / 60)} minutes between these two panes; slow down"
            )

    def send(self, sender, address, body):
        return self._send(sender, None, address, body, 0, None)

    def operator_send(self, device, address, body):
        return self._send(None, device, address, body, 0, None)

    def reply(self, sender, in_reply_to, body):
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
        return self._send(sender, None, parent.from_address, body, parent.hops + 1, parent.id)

    def _send(self, sender, device, address, body, hops, parent_id):
        roster = self._roster()
        if roster is None or not roster.has_crew:
            # Byte-compatible solo behavior, including existing messages.
            return self._deliver(sender, device, address, body, hops, parent_id)
        route = route_address(address, roster=roster, local_pane_ids=[p.pane_id for p in self._panes()])
        if route.kind == "local":
            with self._locked():
                return self._deliver(sender, device, format_address(route.pane_id, route.token), body, hops, parent_id)
        if not isinstance(body, str) or not body.strip() or len(body) > MAX_BODY_CHARS:
            raise WalkyRefusal(f"message must contain 1–{MAX_BODY_CHARS} characters")
        # Read-only preflight: offline/unknown/replaced destinations leave
        # no record. After this boundary a transport failure is uncertain.
        try:
            found = self._rpc(route.host, "who", {})
            expected = format_address(qualify(route.host, route.pane_id), route.token)
            if not any(r.get("address") == expected for r in found["rows"]):
                raise WalkyRefusal("unknown or replaced pane; nothing was sent")
        except (CrewUnreachable, CrewRemoteRefused, KeyError, TypeError) as exc:
            raise WalkyRefusal(f"crew host {route.host} unreachable; nothing was sent") from exc
        now = self._clock()
        body = mask_text(body)  # redact before it leaves the owning host
        actor = {"kind": "agent" if sender else "operator",
                 "pane": sender.pane_id if sender else "",
                 "address": sender.address if sender else None,
                 "harness": sender.harness if sender else "",
                 "label": sender.label if sender else "", "device": device}
        key = sender.pane_id if sender else f"operator:{device}"
        with self._locked():
            self._check_limits(key, qualify(route.host, route.pane_id))
            receipt = Message(
                f"{route.host}:{self._ids()}", actor["kind"], key, actor["address"],
                actor["harness"], actor["label"], device,
                qualify(route.host, route.pane_id), route.token, body,
                "uncertain", now, now, hops=hops, parent_id=parent_id,
                reason="remote outcome unknown; do not resend",
            )
            self._outbox.upsert(receipt)
            self._outbox.save()  # survives an origin crash after dispatch
        payload = {"actor": actor, "id": receipt.id, "address": format_address(route.pane_id, route.token),
                   "body": body, "hops": hops, "parent_id": parent_id}
        try:
            response = self._rpc(route.host, "send", payload)
            receipt = self._receipt(receipt, response["message"])
        except (CrewUnreachable, WalkyRefusal, KeyError, TypeError, StoreError):
            pass  # no automatic resend after a potentially accepted write
        except CrewRemoteRefused as exc:
            if exc.status < 500:
                receipt = replace(receipt, state="refused", reason=f"remote refused ({exc.status})")
        self._outbox.upsert(receipt)
        self._outbox.save()
        self._audit.record({"action": "msg.reply" if parent_id else "msg.send",
                            "detail": {"id": receipt.id, "host": route.host, "preview": _preview(body)}})
        return receipt

    def _deliver(self, sender, device, address, body, hops, parent_id, message_id=None):
        return self._deliver_new(
            from_kind="agent" if sender else "operator",
            from_key=sender.pane_id if sender else f"operator:{device}",
            from_address=sender.address if sender else None,
            from_harness=sender.harness if sender else "",
            from_label=sender.label if sender else "", from_device=device,
            address=address, body=body, hops=hops, parent_id=parent_id,
            action="msg.reply" if parent_id else "msg.send", message_id=message_id,
        )

    @staticmethod
    def _receipt(cached, raw):
        message = Message.from_dict(raw)
        if message.id != cached.id or message.state not in (
            "queued", "held", "typed", "waiting_in_inbox", "refused", "uncertain", "canceled"
        ):
            raise StoreError("invalid remote receipt")
        return replace(cached, state=message.state, reason=message.reason, updated=message.updated)

    def _outgoing(self, mid):
        try:
            return self._outbox.get(mid)
        except KeyError:
            return None

    def status(self, message_id):
        cached = self._outgoing(message_id)
        if cached is None:
            return super().status(message_id)
        try:
            result = self._rpc(message_id.partition(":")[0], "status", {"id": message_id})
            return self._receipt(cached, result["message"])
        except (CrewUnreachable, CrewRemoteRefused, WalkyRefusal, KeyError, TypeError, StoreError):
            return replace(cached, state="uncertain", reason="remote outcome unknown; do not resend")

    def cancel(self, sender_pane, message_id):
        cached = self._outgoing(message_id)
        if cached is None:
            with self._locked():
                return super().cancel(sender_pane, message_id)
        if cached.from_key != sender_pane:
            raise WalkyRefusal("you can only cancel your own messages")
        try:
            result = self._rpc(message_id.partition(":")[0], "cancel", {"id": message_id, "pane": sender_pane})
        except (CrewUnreachable, CrewRemoteRefused) as exc:
            raise WalkyRefusal("remote cancel outcome unknown; do not retry blindly") from exc
        self._audit.record({"action": "msg.cancel", "detail": {"id": message_id}})
        return result["result"]

    def tick(self):
        if self.gateway is None:
            return super().tick()
        with self._locked():
            return super().tick()

    def crew_handlers(self):
        return {f"msg_{op}": self._handler(op) for op in ("who", "send", "status", "cancel")}

    def _handler(self, op):
        def handle(*, member, payload):
            try:
                return self._receive(op, member, payload)
            except (WalkyRefusal, MessageNotFound) as exc:
                return 422, {"detail": str(exc)}
            except (ValueError, TypeError, KeyError):
                return 400, {"detail": "invalid message payload"}
            except (CrewUnreachable, CrewRemoteRefused):
                return 503, {"detail": "crew destination unreachable"}
        return handle

    def _receive(self, op, member, payload):
        roster = self._roster()
        origin = member
        if "_origin" in payload:
            if roster.role != "peer" or member != roster.lead_name:
                raise WalkyRefusal("only the authenticated lead may relay a message origin")
            origin = payload["_origin"]
            if not isinstance(origin, str) or not NAME_RE.fullmatch(origin):
                raise WalkyRefusal("invalid relayed origin")
        target = payload.get("host", roster.self_name)
        if target != roster.self_name:
            if roster.role != "lead" or target not in roster.peers:
                raise WalkyRefusal("unknown crew destination; nothing was sent")
            # Relays cannot nest. Only a lead forwards, only to its members.
            client = self._client(target)
            if op in ("send", "cancel"):
                # Record dispatch BEFORE crossing the wire: an owner may
                # accept even if its response never returns. This is an
                # attempt record, not an assertion of remote success.
                self._audit.record({"action": f"msg.relay.{op}", "detail": {
                    "id": payload.get("id"), "origin": origin, "host": target,
                    "phase": "dispatch", "preview": _preview(payload.get("body", "")),
                }})
            return client.message(op, {**payload, "_origin": origin})
        if op == "who":
            rows = [{**asdict(replace(r, address=qualify(roster.self_name, r.address),
                                     label=f"[{roster.self_name}] {r.label}", you=False)),
                     "host": roster.self_name} for r in super().who()]
            if payload.get("fleet") and roster.role == "lead":
                for host in roster.peers:
                    try:
                        rows.extend(self._client(host).message("who", {"host": host})["rows"])
                    except (CrewUnreachable, CrewRemoteRefused, WalkyRefusal):
                        rows.append({**asdict(WhoRow("-", "", "", "unreachable", f"[{host}] unreachable")), "host": host})
            return {"rows": rows}
        mid = payload.get("id", "")
        if not isinstance(mid, str) or not re.fullmatch(re.escape(roster.self_name) + r":bm_[a-z0-9]{1,64}", mid):
            raise WalkyRefusal("invalid message owner or id")
        with self._locked():
            if op == "send":
                return self._accept(origin, payload)
            message = self._get(mid)
            if not (message.from_key.startswith(f"{origin}:") or
                    message.from_key.startswith(f"operator:{origin}:")):
                raise WalkyRefusal("message does not belong to the authenticated origin")
            if op == "status":
                return {"message": message.as_dict()}
            return {"result": super().cancel(qualify(origin, payload["pane"]), mid)}

    def _accept(self, origin, payload):
        from .identity import AgentSender

        actor = payload["actor"]
        hops = payload.get("hops", 0)
        if type(hops) is not int or not 0 <= hops <= MAX_HOPS:
            raise WalkyRefusal("reply chain hop limit exceeded")
        body = payload["body"]
        address = payload["address"]
        if not isinstance(body, str) or not isinstance(address, str):
            raise WalkyRefusal("invalid message body or address")
        parse_address(address)
        if actor["kind"] == "agent":
            pane, _ = parse_address(actor["address"])
            if pane != actor["pane"]:
                raise WalkyRefusal("sender pane and address disagree")
            sender = AgentSender(qualify(origin, pane), qualify(origin, actor["address"]),
                                 actor["harness"], actor["label"])
            device = None
            expected_key = sender.pane_id
        elif actor["kind"] == "operator":
            sender, device = None, qualify(origin, actor["device"])
            expected_key = f"operator:{device}"
        else:
            raise WalkyRefusal("invalid sender kind")
        try:
            previous = self._get(payload["id"])
        except MessageNotFound:
            previous = None
        if previous is not None:
            if previous.from_key != expected_key or previous.body != body:
                raise WalkyRefusal("message id already belongs to another envelope")
            return {"message": previous.as_dict()}  # never type a replay twice
        message = self._deliver(sender, device, address, body, hops,
                                payload.get("parent_id"), payload["id"])
        return {"message": message.as_dict()}


def create_message_service(state_dir, mux, *, gateway=None, **kwargs):
    """Solo stays byte-compatible; crew uses the same factory in CLI/server."""
    from ..crew.roster import CrewRoster, CrewRosterCorrupt
    from ..crew_runtime import CrewGateway

    try:
        roster = CrewRoster.load(Path(state_dir))
    except CrewRosterCorrupt as exc:
        raise StoreError("crew roster is corrupt; message routing refused") from exc
    if gateway is None and not roster.has_crew:
        return WalkyService(state_dir, mux, **kwargs)
    return FederatedWalkyService(state_dir, mux, gateway=gateway or CrewGateway(state_dir), **kwargs)
