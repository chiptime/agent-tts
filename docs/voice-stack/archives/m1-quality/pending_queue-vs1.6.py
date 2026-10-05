#!/usr/bin/env python3
"""Host-side request identifiers and daemon passthrough (voice-stack VS1.6).

Scope of this module in this milestone: ONLY identifier helpers plus the thin
passthrough that lets the host (a) enqueue an audio file into the engine
daemon carrying an identifier and (b) cancel by identifier. The pending
queue itself (admit/tick/completion, SQLite ledger) arrives with milestone 3.

The host talks to the engine through its public surface only: the daemon's
``enqueue <json>`` and ``cancel <json>`` IPC commands (TECHNICAL-PLAN T3).
Both senders are injectable so every behavior is testable without a daemon.

CLI (used by ``bin/herdr-tts``)::

    pending_queue.py new-id
    pending_queue.py enqueue-file FILE --id ID [--id ID ...] [--priority P]
                                   [--policy P] [--event-type T] [--label L]
    pending_queue.py cancel --id ID [--id ID ...]

Exit status: 0 = ``ok=true``, 1 = ``ok=false`` (engine/daemon refusal),
2 = usage or invalid identifier. The reply line is ``key=value`` tokens.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from typing import Callable, Dict, List, Optional, Sequence

# Same opaque-id rule as the brain's speech_request_id (TECHNICAL-PLAN T1).
ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

_INT_FIELDS = ("item", "queue_len", "removed", "active_stopped", "trimmed", "coalesced")

Sender = Callable[[str], Optional[str]]
Enqueuer = Callable[[dict], Optional[str]]


class IdentifierError(ValueError):
    """An identifier violates the opaque-id rule (or none was given)."""


class EngineUnavailable(RuntimeError):
    """The agent_tts engine could not be imported."""


def new_announcement_id() -> str:
    """Fresh ``ann-<uuid4>`` identifier for a host-minted announcement."""
    return f"ann-{uuid.uuid4()}"


def valid_identifier(value: object) -> bool:
    return isinstance(value, str) and ID_PATTERN.match(value) is not None


def _checked(identifiers: Sequence[str]) -> List[str]:
    """Validated identifiers, order preserved, duplicates dropped."""
    ids: List[str] = []
    for value in identifiers:
        if not valid_identifier(value):
            raise IdentifierError(f"invalid identifier: {value!r}")
        if value not in ids:
            ids.append(value)
    if not ids:
        raise IdentifierError("at least one identifier is required")
    return ids


def build_enqueue_payload(
    path: str,
    identifiers: Sequence[str],
    *,
    label: Optional[str] = None,
    priority: str = "working",
    policy: str = "queue",
    event_type: str = "",
) -> dict:
    """Daemon ``enqueue`` payload for one audio file carrying identifiers."""
    payload = {
        "file": os.path.abspath(path),
        "label": label or os.path.basename(path),
        "priority": priority,
        "policy": policy,
        "identifiers": _checked(identifiers),
    }
    if event_type:
        payload["event_type"] = event_type
    return payload


def build_cancel_command(identifiers: Sequence[str]) -> str:
    """Daemon ``cancel`` IPC line for the given identifiers."""
    body = {"identifiers": _checked(identifiers)}
    return "cancel " + json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def parse_reply(reply: Optional[str]) -> Dict[str, object]:
    """Typed view of a daemon reply (``ok=true item=3 queue_len=0`` ...)."""
    if reply is None:
        return {"ok": False, "error": "daemon unreachable"}
    text = reply.strip()
    if text.startswith("ERR:"):
        return {"ok": False, "error": text[4:].strip()}
    result: Dict[str, object] = {}
    if "error=" in text:
        head, _, error = text.partition("error=")
        result["error"] = error.strip()
        text = head
    for token in text.split():
        key, sep, value = token.partition("=")
        if not sep:
            continue
        if key == "ok":
            result["ok"] = value == "true"
        elif key in _INT_FIELDS and value.lstrip("-").isdigit():
            result[key] = int(value)
        else:
            result[key] = value
    result.setdefault("ok", False)
    return result


def format_reply(result: Dict[str, object]) -> str:
    """``key=value`` line for shell consumers (``ok`` first, error last)."""
    parts = [f"ok={'true' if result.get('ok') else 'false'}"]
    for key, value in result.items():
        if key not in ("ok", "error"):
            parts.append(f"{key}={value}")
    if "error" in result:
        parts.append(f"error={result['error']}")
    return " ".join(parts)


def _engine_enqueue(payload: dict) -> Optional[str]:
    try:
        import tts_engine  # noqa: F401  (sets the herdr socket/lock env first)
        from agent_tts.daemon import delegate_enqueue
    except (ImportError, SystemExit) as exc:
        raise EngineUnavailable(f"agent_tts unavailable: {exc}") from exc
    return delegate_enqueue(payload)


def _engine_send(command: str) -> Optional[str]:
    try:
        import tts_engine  # noqa: F401
        from agent_tts import send_ipc_command
    except (ImportError, SystemExit) as exc:
        raise EngineUnavailable(f"agent_tts unavailable: {exc}") from exc
    return send_ipc_command(command)


def enqueue_file(
    path: str,
    identifiers: Sequence[str],
    *,
    enqueue: Optional[Enqueuer] = None,
    **options: str,
) -> Dict[str, object]:
    """Enqueue an audio file carrying identifiers; returns the typed ack.

    A missing/empty file never reaches the daemon (nothing to play, nothing
    to cancel later).
    """
    payload = build_enqueue_payload(path, identifiers, **options)
    try:
        if os.path.getsize(path) <= 0:
            raise OSError("empty file")
    except OSError:
        return {"ok": False, "error": f"audio file missing or empty: {path}"}
    return parse_reply((enqueue or _engine_enqueue)(payload))


def cancel(
    identifiers: Sequence[str], *, send: Optional[Sender] = None
) -> Dict[str, object]:
    """Cancel by identifiers; an absent daemon is idempotent silence.

    The command never spawns a daemon: with none running there is nothing
    queued or playing to cancel, which is exactly ``removed=0``.
    """
    command = build_cancel_command(identifiers)
    reply = (send or _engine_send)(command)
    if reply is None:
        return {"ok": True, "removed": 0, "active_stopped": 0}
    return parse_reply(reply)


def _emit(result: Dict[str, object]) -> int:
    line = format_reply(result)
    if result.get("ok"):
        print(line)
        return 0
    print(line, file=sys.stderr)
    return 1


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    enqueue: Optional[Enqueuer] = None,
    send: Optional[Sender] = None,
) -> int:
    parser = argparse.ArgumentParser(prog="pending_queue.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("new-id", help="print a fresh ann-<uuid4> identifier")
    p_enq = sub.add_parser("enqueue-file", help="enqueue an audio file with identifiers")
    p_enq.add_argument("file")
    p_enq.add_argument("--id", dest="ids", action="append", default=[])
    p_enq.add_argument("--priority", default="working")
    p_enq.add_argument("--policy", default="queue")
    p_enq.add_argument("--event-type", default="")
    p_enq.add_argument("--label", default=None)
    p_can = sub.add_parser("cancel", help="cancel queued/active audio by identifier")
    p_can.add_argument("--id", dest="ids", action="append", default=[])
    args = parser.parse_args(argv)

    if args.command == "new-id":
        print(new_announcement_id())
        return 0
    try:
        if args.command == "enqueue-file":
            result = enqueue_file(
                args.file,
                args.ids,
                enqueue=enqueue,
                label=args.label,
                priority=args.priority,
                policy=args.policy,
                event_type=args.event_type,
            )
        else:
            result = cancel(args.ids, send=send)
    except IdentifierError as exc:
        print(f"ok=false error={exc}", file=sys.stderr)
        return 2
    except EngineUnavailable as exc:
        print(f"ok=false error={exc}", file=sys.stderr)
        return 1
    return _emit(result)


if __name__ == "__main__":
    sys.exit(main())
