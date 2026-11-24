"""The one harness source of truth for the PWA session routes.

``GET /session/roots`` publishes the CLOSED harness list and
``POST /session/new`` / ``POST /session/resume`` validate against it.
Both read it HERE, so what the "New" sheet lists is exactly what a
launch accepts — and nothing else is: an alias (``oa``, ``omp``,
``agy``) never adds a harness, and no client input becomes a command.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional


@lru_cache(maxsize=1)
def launchable_harnesses() -> tuple:
    """The CLOSED harness list a PWA launch may name: the
    transcript-readers registry CANONICAL names (an alias never adds a
    harness). Frozen per process — the registry is immutable."""
    from .transcript_readers.registry import default_registry

    return tuple(default_registry().harnesses())


def resolve_launch_harness(raw: Optional[str]) -> Optional[str]:
    """The canonical harness for client input, or None when it is not
    on the closed list.

    Only surrounding whitespace and letter case are normalized; an
    alias or any unlisted name resolves to None (the routes answer
    ``unknown_harness``)."""
    name = (raw or "").strip().lower()
    return name if name and name in launchable_harnesses() else None
