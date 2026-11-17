"""Prompt-cache warning: one heads-up per idle period (S5).

An agent's hot context dies ``ttl`` seconds after its last activity.
This engine watches idle deltas and fires one warning
``warn_before`` seconds before the cliff — once per idle period. A new
activity re-arms it. Pure logic: the parent decides what clock feeds
it (the natural home is the watcher's existing poll).

The idle arrives as a DELTA, never an absolute stamp: the production
bug this contract prevents was a monotonic watcher stamp subtracted
from a wall-clock now — an idle of ~1.79e9 s and an instant, absurd
warning for every pane. Deltas carry no clock domain.
"""

from __future__ import annotations

from typing import Dict, Optional

#: Defaults from doc 08: a 5-minute cache window with a minute of margin.
DEFAULT_TTL_S = 300.0
DEFAULT_WARN_BEFORE_S = 60.0


class CacheWarnEngine:
    """Remembers which idle periods already warned."""

    def __init__(
        self,
        ttl_s: float = DEFAULT_TTL_S,
        warn_before_s: float = DEFAULT_WARN_BEFORE_S,
    ):
        self._ttl = float(ttl_s)
        self._warn_before = float(warn_before_s)
        # pane_id → the idle-origin it last warned for, reconstructed
        # as ``now - idle_s`` in the caller's own domain: constant
        # across the ticks of one idle period, changed by new activity.
        self._warned_for: Dict[str, float] = {}

    def observe(
        self, pane_id: str, idle_s: float, now: float
    ) -> Optional[dict]:
        """Returns the warning record when due, else ``None``.

        ``expires_in_s`` floors at zero: a late wiring must still say
        the context is cold, not swallow the signal.
        """
        idle_s = max(0.0, float(idle_s))
        if idle_s + self._warn_before < self._ttl:
            return None  # still comfortably hot
        stamp = float(now) - idle_s
        if self._warned_for.get(pane_id) == stamp:
            return None  # already warned for this idle period
        self._warned_for[pane_id] = stamp
        return {
            "pane_id": pane_id,
            "idle_s": idle_s,
            "expires_in_s": max(0.0, self._ttl - idle_s),
        }
