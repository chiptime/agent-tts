"""What gets typed into the receiver's pane, and when it is safe to type.

Delivery is one line — the header — typed as the receiver's next input.
For agent senders the header never carries the body: the body waits in
the inbox until the receiver asks for it. Operator messages are the
exception the other design makes: they carry the body inline (the
operator has no inbox to read), collapsed to a single line because a
newline would submit the receiver's input box mid-message.

WHEN TO TYPE: only into a pane that is ``idle``/``done`` AND whose
input line is empty. A draft is detected from the visible screen's
last non-blank line: a bare prompt marker (``>``, ``❯``, ``$``…)
means the box is empty; leftover words mean somebody is mid-thought
and the message must wait. The heuristic errs toward holding — typing
over a half-written prompt is the one mistake this feature cannot undo
— so an output line resting at the bottom reads as a draft and costs
one retry interval, never a mangled input.
"""

from __future__ import annotations

import re
from typing import Tuple

#: Header for a message from an agent: the body stays in the inbox.
AGENT_HEADER = (
    "[brain-msg {mid}] from agent {sender} ({harness}, {label}): "
    'read it with: herdr-brain msg inbox {mid} · reply: '
    'herdr-brain msg reply {mid} "<text>"'
)

#: Header for a message from the operator: the body rides along.
OPERATOR_HEADER = "[brain-msg {mid}] from operator ({device}): {body}"

#: Cap on the label inside a header; titles can be arbitrarily long.
LABEL_CAP = 60

#: Agent states safe for typing; everything else holds.
DELIVER_STATUSES = frozenset({"idle", "done"})

_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b."
)
_PROMPT_LEAD_RE = re.compile(r"^\s*(?:[>❯➜│|#$%]|└>|┌|╭)*\s*")
_WORD_RE = re.compile(r"\w+")
_NEWLINES_RE = re.compile(r"[\r\n]+")


def single_line(text: str) -> str:
    """Collapses newlines: one typed line, per the input-box contract."""
    return _NEWLINES_RE.sub(" ", text).strip()


def clip_label(label: str) -> str:
    clean = single_line(label or "")
    if len(clean) > LABEL_CAP:
        return clean[: LABEL_CAP - 1] + "…"
    return clean


def agent_line(mid: str, sender_address: str, harness: str, label: str) -> str:
    return AGENT_HEADER.format(
        mid=mid, sender=sender_address, harness=harness, label=clip_label(label)
    )


def operator_line(mid: str, device: str, body: str) -> str:
    return OPERATOR_HEADER.format(mid=mid, device=single_line(device), body=single_line(body))


def screen_has_draft(screen: str) -> bool:
    """True when the visible screen's last line still holds typed text.

    ANSI codes are stripped, the cursor block ignored; what remains is
    judged after its leading prompt marker. Fewer than three word
    characters count as no draft (typing over a stray fragment beats
    holding forever); anything wordier is somebody's unfinished input.
    """
    lines = [ln for ln in _ANSI_RE.sub("", screen or "").splitlines() if ln.strip()]
    if not lines:
        return False
    bottom = lines[-1].replace("█", "").rstrip()
    content = _PROMPT_LEAD_RE.sub("", bottom).strip()
    if not content:
        return False  # a bare prompt marker: the input box is empty
    return len("".join(_WORD_RE.findall(content)[:3])) >= 3


def delivery_gate(status: str, screen: str) -> Tuple[bool, str]:
    """(may-type-now, hold-reason) for one receiver pane."""
    if status not in DELIVER_STATUSES:
        return False, f"receiver is {status}"
    if screen_has_draft(screen):
        return False, "receiver has a draft in its input"
    return True, ""
