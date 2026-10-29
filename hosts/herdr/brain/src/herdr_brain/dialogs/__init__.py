"""Per-harness dialog recognition into the neutral structured model.

The public surface: :func:`recognize` (one agent's read of one screen)
and :func:`registered_agents` (which agents have a recognizer). See
:mod:`herdr_brain.dialogs.model` for the model and
:mod:`herdr_brain.dialogs.registry` for the decision site.
"""

from __future__ import annotations

from .model import DialogOption, StructuredDialog
from .registry import adapter_for, recognize, registered_agents

__all__ = [
    "DialogOption",
    "StructuredDialog",
    "adapter_for",
    "recognize",
    "registered_agents",
]
