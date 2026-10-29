"""Cursor CLI: dialogs are claimed by NOBODY, deliberately.

The Cursor CLI replaces its input box with dialogs whose keystroke
recipes have no verified capture here. This adapter exists so the
agent name is REGISTERED (the pane is recognized as Cursor) while
claiming nothing: the PWA shows the explicit "cannot read this dialog"
card with the manual keypad, which is the honest state. The moment a
recipe is verified, this adapter grows a detector like any other —
fail-closed until then.
"""

from __future__ import annotations

from typing import List, Optional

from ..model import StructuredDialog
from . import AdapterBase


class CursorAdapter(AdapterBase):
    agent = "cursor"

    def detect(self, lines: List[str], styled) -> Optional[StructuredDialog]:
        return None
