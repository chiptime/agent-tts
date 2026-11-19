"""Claude hook-held permissions: broker, unix socket server, hook client.

parity-f7e S3 (audit 02 §5). A Claude Code ``PreToolUse`` hook blocks on
a local brain unix socket until the operator answers from the PWA or by
voice; with no answer before the timeout — or on ANY error — it DENIES.

Hook contract (Claude Code hooks, verified 2026-11-19 against the
official documentation at https://code.claude.com/docs/en/hooks-guide
and https://code.claude.com/docs/en/hooks via the Context7 docs tool):

* stdin carries one JSON object with at least ``hook_event_name``,
  ``tool_name``, ``tool_input``, ``session_id`` and ``cwd``.
* A ``PreToolUse`` hook decides by printing JSON on stdout and exiting
  0::

      {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                              "permissionDecision": "allow" | "deny" | "ask",
                              "permissionDecisionReason": "..."}}

* An exit code other than 0 or 2 without valid JSON is a NON-BLOCKING
  error — the tool call would PROCEED. That is why this hook always
  prints a valid decision JSON (deny) before its own deadline and never
  relies on being killed: Claude's ``timeout`` field in the installed
  settings entry is the hook deadline plus a margin, so the deny always
  lands first.

Socket protocol (v1, one request and one reply per connection, each a
newline-terminated JSON object):

* hook → brain: ``{"v":1,"hook_event_name":"PreToolUse","tool_name":…,
  "tool_input":{…},"session_id":…,"cwd":…}``
* brain → hook: ``{"v":1,"decision":"allow"|"deny","reason":…}``

The socket lives under the brain's state dir (0700 dir, 0600 socket,
same user only) and never carries a credential: the hook needs no
device bearer, and a decision can only come back over the connection
that asked.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .secrets_mask import mask_text

LOGGER = logging.getLogger(__name__)

DECISION_ALLOW = "allow"
DECISION_DENY = "deny"
DECISIONS = (DECISION_ALLOW, DECISION_DENY)

PROTOCOL_VERSION = 1

#: Name of the socket file inside the brain state dir.
SOCKET_NAME = "hook-permissions.sock"

#: How long the hook waits for an operator answer before denying, by
#: default. Both sides (hook client and socket server) use it, so the
#: deny happens at the same moment on both ends. Env knob:
#: ``HERDR_BRAIN_HOOK_TIMEOUT_S``.
DEFAULT_HOOK_TIMEOUT_S = 120.0

#: One wire line is bounded; a bigger request is denied without being
#: buffered (the socket peer cannot make the brain allocate at will).
MAX_WIRE_BYTES = 64 * 1024

#: Bound on the tool_input summary that reaches the PWA card, the push
#: payload and the spoken context. Masking happens BEFORE this cut.
SUMMARY_MAX_CHARS = 240

#: Bash commands and file edits carry the one field worth previewing;
#: anything else falls back to a compact JSON rendering.
_PREVIEW_FIELDS = ("command", "file_path", "path", "url", "pattern", "description")


def hook_timeout_s_from_env(env: Optional[Dict[str, str]] = None) -> float:
    """The hook deny deadline, from ``HERDR_BRAIN_HOOK_TIMEOUT_S``.

    Unset, empty, non-numeric or out-of-range values fall back to the
    default — a knob that would brick every tool call (0s) or hold one
    forever is not a knob worth honoring."""
    raw = (env or os.environ).get("HERDR_BRAIN_HOOK_TIMEOUT_S", "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_HOOK_TIMEOUT_S
    if value <= 0 or value > 3600:
        return DEFAULT_HOOK_TIMEOUT_S
    return value


def default_socket_path(state_dir: str | os.PathLike) -> Path:
    """The socket path inside the brain state dir."""
    return Path(state_dir) / SOCKET_NAME


def summarize_tool_input(tool_name: str, tool_input: Any) -> str:
    """A bounded, MASKED one-line preview of what Claude wants to do.

    Masking happens BEFORE truncation (the brain security invariant):
    a secret cut in half by the length cap would otherwise leak its
    unmasked half through the PWA card, the push payload and the spoken
    context — every surface this summary feeds.
    """
    if isinstance(tool_input, dict):
        for key in _PREVIEW_FIELDS:
            value = tool_input.get(key)
            if isinstance(value, str) and value.strip():
                text = f"{key}: {value}"
                break
        else:
            text = json.dumps(tool_input, ensure_ascii=False, default=str)
    elif tool_input is None:
        text = ""
    else:
        text = json.dumps(tool_input, ensure_ascii=False, default=str)
    masked = mask_text(text)
    if len(masked) <= SUMMARY_MAX_CHARS:
        return masked
    return masked[: SUMMARY_MAX_CHARS - 1] + "…"


@dataclass
class HeldPermission:
    """One hook connection waiting for the operator's decision."""

    request_id: str
    tool_name: str
    summary: str
    session_id: str
    cwd: str
    created_at: float
    deadline_at: float
    pane_id: Optional[str] = None
    agent: Optional[str] = None
    decision: Optional[str] = None
    decided_by: Optional[str] = None
    event: threading.Event = field(default_factory=threading.Event)

    def as_payload(self, now: Optional[float] = None) -> dict:
        """The PWA/list wire shape: masked summary, no secrets, no tokens."""
        current = time.time() if now is None else now
        return {
            "request_id": self.request_id,
            "tool_name": self.tool_name,
            "summary": self.summary,
            "session_id": self.session_id,
            "cwd": self.cwd,
            "pane_id": self.pane_id,
            "agent": self.agent,
            "created_at": self.created_at,
            "expires_in_s": max(0.0, round(self.deadline_at - current, 1)),
        }


#: Decided holds kept addressable (for get/second-answer reporting)
#: before the oldest are dropped. Pending holds are never evicted.
RETAIN_DECIDED = 128


class HookPermissionBroker:
    """Thread-safe registry of held requests; a decision applies once.

    The socket thread holds, the PWA route / voice gate answer, and the
    socket thread wakes on the per-request :class:`threading.Event`.
    Terminal states are immutable: a second answer (late tap, replayed
    POST) never reports as applied.
    """

    def __init__(
        self,
        timeout_s: float = None,
        clock: Callable[[], float] = time.time,
        on_hold: Optional[Callable[[HeldPermission], None]] = None,
        on_resolved: Optional[Callable[[HeldPermission, str], None]] = None,
    ):
        self.timeout_s = (
            hook_timeout_s_from_env() if timeout_s is None else float(timeout_s)
        )
        self._clock = clock
        self._on_hold = on_hold
        self._on_resolved = on_resolved
        self._lock = threading.Lock()
        # Retained holds: undecided AND recently decided (so a second
        # answer reports the applied decision instead of vanishing).
        # Bounded — decided holds beyond the newest RETAIN_DECIDED are
        # dropped; pending holds are never evicted.
        self._held: Dict[str, HeldPermission] = {}
        self._order: List[str] = []
        self._decided = 0

    def attach_listeners(
        self,
        on_hold: Optional[Callable[[HeldPermission], None]] = None,
        on_resolved: Optional[Callable[[HeldPermission, str], None]] = None,
    ) -> None:
        """Fills ONLY the unset notification hooks (server wiring).

        A broker constructed bare (tests) gains the app's audit/push
        wiring when create_app adopts it; one that already carries its
        own listeners is never silently overridden."""
        if self._on_hold is None and on_hold is not None:
            self._on_hold = on_hold
        if self._on_resolved is None and on_resolved is not None:
            self._on_resolved = on_resolved

    # -- holds -----------------------------------------------------------

    def hold(
        self,
        tool_name: str,
        tool_input: Any,
        session_id: str,
        cwd: str,
        pane_id: Optional[str] = None,
        agent: Optional[str] = None,
        timeout_s: Optional[float] = None,
    ) -> HeldPermission:
        """Registers one held request with an unguessable id."""
        now = self._clock()
        window = self.timeout_s if timeout_s is None else float(timeout_s)
        held = HeldPermission(
            request_id=secrets.token_urlsafe(24),
            tool_name=str(tool_name or "?")[:64],
            summary=summarize_tool_input(tool_name, tool_input),
            session_id=str(session_id or "")[:128],
            cwd=str(cwd or "")[:512],
            created_at=now,
            deadline_at=now + window,
            pane_id=pane_id,
            agent=agent,
        )
        with self._lock:
            self._sweep(now)
            self._held[held.request_id] = held
            self._order.append(held.request_id)
        if self._on_hold is not None:
            try:
                self._on_hold(held)
            except Exception:  # noqa: BLE001 — notification is advisory
                LOGGER.warning("held-permission on_hold hook failed", exc_info=True)
        return held

    def pending(self) -> List[dict]:
        """The undecided holds, oldest first, as wire payloads."""
        now = self._clock()
        with self._lock:
            self._sweep(now)
            return [
                self._held[request_id].as_payload(now)
                for request_id in self._order
                if request_id in self._held
                and self._held[request_id].decision is None
            ]

    def get(self, request_id: str) -> Optional[HeldPermission]:
        with self._lock:
            return self._held.get(request_id)

    def wait(self, held: HeldPermission, timeout_s: Optional[float] = None) -> bool:
        """Blocks until decided or the deadline passes (True = decided)."""
        now = self._clock()
        budget = (
            max(0.0, held.deadline_at - now)
            if timeout_s is None
            else min(float(timeout_s), max(0.0, held.deadline_at - now))
        )
        return held.event.wait(budget)

    # -- decisions ---------------------------------------------------------

    def answer(
        self, request_id: str, decision: str, by: str = "pwa"
    ) -> Tuple[Optional[HeldPermission], bool]:
        """Applies the operator's decision exactly once.

        Returns ``(held, applied)``; ``applied`` is False for an unknown
        id, an already-decided request, or an expired hold — the PWA
        maps that to 404/409 and the waiting hook denies on its own
        deadline.
        """
        if decision not in DECISIONS:
            raise ValueError(f"unknown hook permission decision: {decision!r}")
        now = self._clock()
        with self._lock:
            held = self._held.get(request_id)
            if held is None:
                return None, False
            if held.decision is not None:
                return held, False
            if now >= held.deadline_at:
                # Expired but not yet swept: decide-deny ourselves so a
                # late answer can never apply after the hook denied.
                self._settle(held, DECISION_DENY, "timeout")
                return held, False
            self._settle(held, decision, by)
        if self._on_resolved is not None:
            try:
                self._on_resolved(held, decision)
            except Exception:  # noqa: BLE001 — notification is advisory
                LOGGER.warning("held-permission on_resolved hook failed", exc_info=True)
        return held, True

    # -- internals -----------------------------------------------------------

    def _settle(self, held: HeldPermission, decision: str, by: str) -> None:
        """Terminal transition under the lock; wakes the waiter."""
        held.decision = decision
        held.decided_by = by
        self._decided += 1
        self._evict_decided()
        held.event.set()

    def _evict_decided(self) -> None:
        """Drops the OLDEST decided holds beyond RETAIN_DECIDED."""
        while self._decided > RETAIN_DECIDED:
            for request_id in self._order:
                held = self._held.get(request_id)
                if held is not None and held.decision is not None:
                    self._held.pop(request_id, None)
                    self._order.remove(request_id)
                    self._decided -= 1
                    break
            else:  # pragma: no cover — counter and store agree
                self._decided = 0
                return

    def _sweep(self, now: float) -> None:
        """Lazy expiry: undecided holds past their deadline deny themselves."""
        for request_id in list(self._order):
            held = self._held.get(request_id)
            if held is None or held.decision is not None or now < held.deadline_at:
                continue
            self._settle(held, DECISION_DENY, "timeout")


class HookPermissionSocketServer:
    """The brain-side unix socket the Claude hooks connect to.

    One thread accepts; one thread per connection reads a single
    request line, holds it on the broker and writes one reply line —
    the operator's decision, or a deny when the deadline passes with no
    answer. Every failure replies deny when it can reply at all.
    """

    def __init__(
        self,
        state_dir: str | os.PathLike,
        broker: HookPermissionBroker,
        pane_resolver: Optional[Callable[[str], Optional[Tuple[str, Optional[str]]]]] = None,
    ):
        self.path = default_socket_path(state_dir)
        self._broker = broker
        self._pane_resolver = pane_resolver
        self._socket: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stopping = threading.Event()

    def start(self) -> None:
        """Binds the socket (0600, same-user state dir) and serves."""
        from .pairing import ensure_private_dir

        ensure_private_dir(self.path.parent)
        # A stale socket file from a dead brain is replaced; a LIVE one
        # is unlinked too, which orphans the old listener's accept loop
        # (it keeps its fd) — the newest brain instance always owns the
        # path, exactly like every unix-socket daemon in this position.
        if self.path.exists():
            self.path.unlink()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.path))
        os.chmod(self.path, 0o600)
        server.listen(16)
        server.settimeout(0.5)
        self._socket = server
        self._stopping.clear()
        self._thread = threading.Thread(
            target=self._accept_loop, name="hook-permissions", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        try:
            if self.path.exists():
                self.path.unlink()
        except OSError:
            pass

    def _accept_loop(self) -> None:
        while not self._stopping.is_set():
            assert self._socket is not None
            try:
                conn, _ = self._socket.accept()
            except socket.timeout:
                continue
            except OSError:
                if not self._stopping.is_set():
                    LOGGER.warning("hook socket accept failed", exc_info=True)
                return
            threading.Thread(
                target=self._serve, args=(conn,), daemon=True
            ).start()

    # -- one connection -----------------------------------------------------

    def _serve(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(30.0)
            line = self._read_line(conn)
            reply = self._answer_line(line)
            conn.settimeout(5.0)
            conn.sendall(reply + b"\n")
        except Exception as exc:  # noqa: BLE001 — deny on ANY error
            LOGGER.warning("hook connection failed: %s", exc)
            try:
                conn.sendall(
                    json.dumps(
                        {
                            "v": PROTOCOL_VERSION,
                            "decision": DECISION_DENY,
                            "reason": "brain error",
                        }
                    ).encode("utf-8")
                    + b"\n"
                )
            except OSError:
                pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _read_line(self, conn: socket.socket) -> bytes:
        """One newline-terminated line, bounded; bigger inputs raise."""
        buf = bytearray()
        while b"\n" not in buf:
            if len(buf) > MAX_WIRE_BYTES:
                raise ValueError("request line exceeds the wire limit")
            chunk = conn.recv(min(4096, MAX_WIRE_BYTES + 1 - len(buf)))
            if not chunk:
                break
            buf.extend(chunk)
        line = bytes(buf).split(b"\n", 1)[0]
        if len(line) > MAX_WIRE_BYTES:
            raise ValueError("request line exceeds the wire limit")
        return line

    def _answer_line(self, line: bytes) -> bytes:
        try:
            request = json.loads(line.decode("utf-8"))
            if not isinstance(request, dict):
                raise ValueError("request is not a JSON object")
            tool_name = request.get("tool_name")
            if not isinstance(tool_name, str) or not tool_name.strip():
                raise ValueError("request lacks a tool_name")
        except (ValueError, UnicodeDecodeError) as exc:
            return self._deny(f"malformed request: {exc}"[:200])

        pane_id = agent = None
        if self._pane_resolver is not None:
            try:
                resolved = self._pane_resolver(str(request.get("session_id") or ""))
                if resolved is not None:
                    pane_id, agent = resolved
            except Exception:  # noqa: BLE001 — mapping is best-effort
                LOGGER.debug("pane resolution failed for held request", exc_info=True)

        held = self._broker.hold(
            tool_name=tool_name,
            tool_input=request.get("tool_input"),
            session_id=str(request.get("session_id") or ""),
            cwd=str(request.get("cwd") or ""),
            pane_id=pane_id,
            agent=agent,
        )
        decided = self._broker.wait(held)
        if not decided or held.decision is None:
            return self._deny("no answer before the timeout")
        return json.dumps(
            {
                "v": PROTOCOL_VERSION,
                "decision": held.decision,
                "reason": f"answered by the operator ({held.decided_by})",
            },
            ensure_ascii=False,
        ).encode("utf-8")

    @staticmethod
    def _deny(reason: str) -> bytes:
        return json.dumps(
            {"v": PROTOCOL_VERSION, "decision": DECISION_DENY, "reason": reason},
            ensure_ascii=False,
        ).encode("utf-8")


# -- the hook client (what Claude Code runs) ---------------------------------


def _hook_output(decision: str, reason: str) -> dict:
    """The Claude Code PreToolUse decision contract, verbatim shape.

    Source: https://code.claude.com/docs/en/hooks-guide (“Deny a tool
    call with PreToolUse JSON output”) — exit 0 with a
    ``hookSpecificOutput.permissionDecision`` of allow/deny/ask.
    """
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }


def run_hook(
    stdin_text: str,
    socket_path: Optional[str | os.PathLike] = None,
    timeout_s: Optional[float] = None,
    env: Optional[Dict[str, str]] = None,
) -> Tuple[int, dict]:
    """One hook invocation: ask the brain, ALWAYS print a decision.

    Returns ``(exit_code, output_payload)`` — exit 0 with a valid
    decision JSON in every path, deny on timeout, brain-down, malformed
    input, malformed reply and any error (S3: reject by default).
    """
    deadline_s = (
        hook_timeout_s_from_env(env) if timeout_s is None else float(timeout_s)
    )
    path = Path(
        socket_path
        or os.environ.get("HERDR_BRAIN_HOOK_SOCKET")
        or default_socket_path(
            (env or os.environ).get("HERDR_BRAIN_STATE_DIR")
            or Path(
                os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
            )
            / "herdr-brain"
        )
    )
    started = time.monotonic()

    def deny(reason: str) -> Tuple[int, dict]:
        return 0, _hook_output(DECISION_DENY, reason)

    try:
        request = json.loads(stdin_text or "")
        if not isinstance(request, dict):
            raise ValueError("stdin is not a JSON object")
        payload = {
            "v": PROTOCOL_VERSION,
            "hook_event_name": str(request.get("hook_event_name") or "PreToolUse"),
            "tool_name": request.get("tool_name"),
            "tool_input": request.get("tool_input"),
            "session_id": request.get("session_id"),
            "cwd": request.get("cwd"),
        }
    except ValueError as exc:
        return deny(f"herdr-brain hook could not read the request: {exc}")

    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        conn.settimeout(max(0.1, deadline_s))
        conn.connect(str(path))
        conn.sendall(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
        conn.shutdown(socket.SHUT_WR)

        buf = bytearray()
        while b"\n" not in buf:
            budget = deadline_s - (time.monotonic() - started)
            if budget <= 0:
                return deny("no answer from herdr-brain before the timeout")
            conn.settimeout(budget)
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf.extend(chunk)
            if len(buf) > MAX_WIRE_BYTES:
                return deny("herdr-brain reply exceeds the wire limit")
        line = bytes(buf).split(b"\n", 1)[0].decode("utf-8", "replace").strip()
        if not line:
            return deny("herdr-brain closed the socket without a decision")
        reply = json.loads(line)
        decision = reply.get("decision")
        if decision not in (DECISION_ALLOW, DECISION_DENY):
            return deny(f"herdr-brain replied with an unusable decision: {decision!r}")
        reason = str(reply.get("reason") or "answered by the operator")
        return 0, _hook_output(decision, reason)
    except (OSError, ValueError) as exc:
        # brain down, socket missing, garbage reply: deny, never block
        return deny(f"herdr-brain unreachable or failed: {exc}"[:200])
    finally:
        try:
            conn.close()
        except OSError:
            pass


def hook_main(argv: Optional[List[str]] = None) -> int:
    """``python -m herdr_brain.hook_permissions`` — Claude runs this.

    Reads the PreToolUse payload on stdin and prints the decision JSON
    on stdout with exit 0 (see :func:`run_hook`)."""
    try:
        stdin_text = sys.stdin.read(MAX_WIRE_BYTES + 1)
    except OSError:
        stdin_text = ""
    code, payload = run_hook(stdin_text)
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return code


if __name__ == "__main__":  # pragma: no cover - the Claude hook entrypoint
    raise SystemExit(hook_main())
