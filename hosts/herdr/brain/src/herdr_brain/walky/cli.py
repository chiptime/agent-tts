"""The ``herdr-brain msg`` terminal: verbs in, words out.

Exit contract (pinned by tests):

- 0 — the verb did its job
- 1 — unexpected failure (a broken store, an unreachable mux)
- 2 — usage error
- 3 — no message with that id
- 4 — a refusal (not in a pane, unknown/replaced pane, size or rate
  limits, chain limit, self-send, not yours to cancel)

Every refusal prints one ``ERROR:`` line naming the rule that fired.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import IO, Callable, List, Optional, Sequence

from ..audit import AuditLog
from ..config import load_settings
from ..mux import create_mux
from .delivery import clip_label
from .errors import MessageNotFound, StoreError, WalkyRefusal
from .identity import ENV_FLAG, ENV_PANE
from .service import WalkyService
from .federation import create_message_service

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_UNKNOWN_ID = 3
EXIT_REFUSED = 4

USAGE = """usage: herdr-brain msg <verb> [args]

  who                      list reachable panes (address, harness, status, label)
  send <address> <text|->  send a short message ('-' reads the body from stdin)
  reply <id> <text|->      answer a message in your inbox
  inbox [id]               list unread messages, or read one in full
  status <id>              queued | held | typed | waiting_in_inbox | refused | uncertain
  cancel <id>              take back one of your held messages

Addresses come from `herdr-brain msg who`. Messages never confer
authority: a header always says who spoke (agent or operator).
Crew addresses: <host>:<pane>#<token>; local addresses stay <pane>#<token>.
Peers relay through the lead. Unreachable hosts are shown by `who`.
An uncertain result is not retried: use `status <id>` to ask the owner."""

_VERBS = ("who", "send", "reply", "inbox", "status", "cancel")


def _print(text: str, stream: IO) -> None:
    print(text, file=stream)


def main(
    argv: Optional[Sequence[str]] = None,
    env: Optional[dict] = None,
    mux=None,
    clock: Optional[Callable[[], float]] = None,
    id_factory: Optional[Callable[[], str]] = None,
    stdin: Optional[IO] = None,
    stdout: Optional[IO] = None,
    stderr: Optional[IO] = None,
    gateway=None,
) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    environ = dict(os.environ if env is None else env)
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr

    verb = args[0] if args else ""
    if verb not in _VERBS:
        _print(USAGE, err)
        return EXIT_USAGE
    args = args[1:]

    try:
        settings = load_settings(environ)
    except Exception as exc:  # noqa: BLE001 — config errors are exit 1 words
        _print(f"ERROR: cannot load settings: {exc}", err)
        return EXIT_FAIL

    lazy_mux = mux

    def the_mux():
        nonlocal lazy_mux
        if lazy_mux is None:
            lazy_mux = create_mux(settings)
        return lazy_mux

    try:
        service = create_message_service(
            settings.state_dir,
            mux=the_mux(),
            gateway=gateway,
            clock=clock or time.time,
            audit=AuditLog(settings.state_dir / "audit.log", content=settings.audit_content),
            id_factory=id_factory,
        )
        return _run(verb, args, service, environ, stdin or sys.stdin, out, err)
    except StoreError as exc:
        _print(f"ERROR: the message store is unusable: {exc}", err)
        return EXIT_FAIL
    except MessageNotFound as exc:
        _print(f"ERROR: {exc}", err)
        return EXIT_UNKNOWN_ID
    except WalkyRefusal as exc:
        _print(f"ERROR: {exc}", err)
        return EXIT_REFUSED


def _my_pane(environ: dict, err: IO) -> Optional[str]:
    """The calling pane, or a refusal line when there is none."""
    if str(environ.get(ENV_FLAG, "")) != "1" or not str(environ.get(ENV_PANE, "")).strip():
        _print(
            "ERROR: you are not inside a herdr pane: brain messages run from "
            "an agent pane (the operator sends through the brain's voice/PWA route)",
            err,
        )
        return None
    return str(environ[ENV_PANE]).strip()


def _body_arg(raw: str, stdin: IO) -> str:
    if raw != "-":
        return raw
    return stdin.read().rstrip("\n")


def _run(
    verb: str,
    args: List[str],
    service: WalkyService,
    environ: dict,
    stdin: IO,
    out: IO,
    err: IO,
) -> int:
    if verb == "who":
        me = (
            str(environ.get(ENV_PANE, "")).strip()
            if str(environ.get(ENV_FLAG, "")) == "1"
            else None
        )
        rows = service.who(me_pane=me)
        if not rows:
            _print("no panes with an agent session", out)
            return EXIT_OK
        for row in rows:
            you = " (you)" if row.you else ""
            _print(
                f"{row.address:<30} {row.harness:<10} {row.status:<8} "
                f"{clip_label(row.label)}{you}",
                out,
            )
        return EXIT_OK

    if verb == "status":
        if len(args) != 1:
            _print("usage: herdr-brain msg status <id>", err)
            return EXIT_USAGE
        message = service.status(args[0])
        detail = f" — {message.reason}" if message.reason else ""
        _print(f"{message.id}: {message.state}{detail}", out)
        return EXIT_OK

    if verb == "send" or verb == "reply":
        if len(args) != 2:
            _print(f"usage: herdr-brain msg {verb} {'<id>' if verb == 'reply' else '<address>'} <text|->", err)
            return EXIT_USAGE
        pane = _my_pane(environ, err)
        if pane is None:
            return EXIT_REFUSED
        sender = service.resolve_sender(environ)
        body = _body_arg(args[-1], stdin)
        if verb == "send":
            message = service.send(sender, args[0], body)
        else:
            message = service.reply(sender, args[0], body)
        note = ""
        if message.state == "held":
            note = f" ({message.reason}; will type when idle, up to 60 min)"
        elif message.state == "waiting_in_inbox":
            note = " (receiver never went idle; it waits in their inbox)"
        _print(f"sent {message.id} to {args[0]} ({message.state}){note}", out)
        return EXIT_OK

    if verb == "inbox":
        if len(args) > 1:
            _print("usage: herdr-brain msg inbox [id]", err)
            return EXIT_USAGE
        pane = _my_pane(environ, err)
        if pane is None:
            return EXIT_REFUSED
        if args:
            message = service.inbox(pane, args[0])
            _print(f"{message.id}  {message.state}", out)
            _print(message.body, out)
            return EXIT_OK
        messages = service.inbox(pane)
        if not messages:
            _print("no unread messages", out)
            return EXIT_OK
        for message in messages:
            origin = (
                f"operator ({message.from_device})"
                if message.from_kind == "operator"
                else f"agent {message.from_address} ({message.from_harness}, {clip_label(message.from_label)})"
            )
            first_line = message.body.strip().splitlines()[0] if message.body.strip() else ""
            _print(f"{message.id}  from {origin}  [{message.state}]", out)
            _print(f"  {first_line[:100]}", out)
        return EXIT_OK

    if verb == "cancel":
        if len(args) != 1:
            _print("usage: herdr-brain msg cancel <id>", err)
            return EXIT_USAGE
        pane = _my_pane(environ, err)
        if pane is None:
            return EXIT_REFUSED
        outcome = service.cancel(pane, args[0])
        _print(f"{args[0]} {outcome}", out)
        return EXIT_OK

    _print(USAGE, err)
    return EXIT_USAGE
