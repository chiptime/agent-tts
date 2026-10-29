"""Kiro CLI V3: the approval panels and the workspace trust prompt.

Shapes this recognizer claims (pinned by synthesized fixtures in the
layouts Kiro draws):

- tool approval — a ``… requires approval`` header over unnumbered
  rows (``› Allow`` / ``Always allow`` / ``Deny`` / ``Always deny``)
  with the footer ``esc to close · ↑↓ to navigate · ↵ to select``.
  Probed fact: Up/Down walk and Enter commits; a digit does NOTHING.
  Recipe: the walk. Only the one-shot ``Allow`` and ``Deny`` rows
  become options — the ``Always …`` rows persist trust rules.
- workspace trust — ``Trust this workspace?`` over ``› Trust,
  continue`` / ``Don't trust, exit`` rows with the footer
  ``esc to exit · ↑↓ to navigate · ↵ to select``. Both rows are
  options; the walk is the recipe.
"""

from __future__ import annotations

import re
from typing import List, Optional

from ..model import DialogOption, StructuredDialog, fingerprint_region
from . import AdapterBase, dialog_region, footer_line

_WALK_FOOTER = re.compile(
    r"(?:↑↓|up/down).*?(?:↵|enter)\s*to\s*select", re.IGNORECASE
)
_APPROVAL_HEADER = re.compile(r"requires approval\s*$", re.IGNORECASE)
_TRUST_HEADER = re.compile(r"^\s*trust this workspace\?\s*$", re.IGNORECASE)
_ROW = re.compile(r"^\s*(?P<pointer>[›❯>])?\s*(?P<label>Allow|Deny|Always allow|Always deny|Trust, continue|Don't trust, exit)\s*$")
_PERSISTENT = re.compile(r"^always ", re.IGNORECASE)


class KiroAdapter(AdapterBase):
    agent = "kiro"

    def detect(self, lines: List[str], styled) -> Optional[StructuredDialog]:
        footer = footer_line(lines)
        if not _WALK_FOOTER.search(footer):
            return None
        is_trust = any(_TRUST_HEADER.match(line) for line in lines)
        is_approval = any(_APPROVAL_HEADER.search(line) for line in lines)
        if not (is_trust or is_approval):
            return None

        rows = []
        for index, line in enumerate(lines):
            match = _ROW.match(line)
            if match is None:
                continue
            rows.append((index, match.group("label"), bool(match.group("pointer"))))
        if len(rows) < 2:
            return None
        question = (
            "Trust this workspace?"
            if is_trust
            else next(
                (
                    line.strip()
                    for line in lines
                    if _APPROVAL_HEADER.search(line)
                ),
                "",
            )
        )
        keep = [
            (index, label, pointed)
            for index, label, pointed in rows
            if is_trust or not _PERSISTENT.match(label)
        ]
        if len(keep) < 2:
            return None
        first = keep[0][0]
        return StructuredDialog(
            agent=self.agent,
            kind="confirm" if is_trust else "permission",
            question=question,
            options=tuple(
                DialogOption(label=label) for _index, label, _pointed in keep
            ),
            fingerprint=fingerprint_region(dialog_region(lines, first)),
            hint=footer,
            nav_keys=("up", "down"),
            commit_keys=("enter",),
            selected=next(
                (
                    position
                    for position, (_index, _label, pointed) in enumerate(keep)
                    if pointed
                ),
                None,
            ),
        )
