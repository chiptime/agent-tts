"""Prompt-cache warning: one heads-up per idle period (S5).

An agent's hot context dies ``ttl`` seconds after its last activity.
This engine watches idle deltas and fires one warning
``warn_before`` seconds before the cliff — once per idle period. A new
activity re-arms it. Pure logic: no clock feeds it at all.

The idle arrives as a DELTA, never an absolute stamp, and observe()
takes no ``now``. Two earlier contracts failed on that edge: an
absolute monotonic stamp subtracted from a wall-clock now produced an
absurd ~1.79e9 s idle; and the reconstructed ``now - idle_s`` dedup
stamp wobbled by microseconds between polls (the delta and the ``now``
came from clocks sampled apart), so every poll of one idle period read
as a NEW period and re-warned. Deduplication now lives on the delta
itself: within one idle period the delta only grows, and it DROPS the
poll after the pane works again — that drop is the re-arm signal. No
clock domain can be mixed because no clock is involved.

Trade-off: if a whole dip (activity AND the climb back past the
margin) falls inside one missed poll gap, that period stays deduped —
acceptable for a best-effort advisory signal, not an alarm.
"""

from __future__ import annotations

from typing import Dict, Optional, Set

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
        # Panes already warned for their CURRENT idle period; cleared
        # when the pane's idle delta drops (new activity began).
        self._warned: Set[str] = set()
        # Last idle delta seen per pane — the drop detector's baseline.
        # Recorded even while hot: the drop is the re-arm signal.
        self._last_idle: Dict[str, float] = {}

    def observe(self, pane_id: str, idle_s: float) -> Optional[dict]:
        """Returns the warning record when due, else ``None``.

        ``expires_in_s`` floors at zero: a late wiring must still say
        the context is cold, not swallow the signal.
        """
        idle_s = max(0.0, float(idle_s))
        last = self._last_idle.get(pane_id)
        self._last_idle[pane_id] = idle_s
        if last is not None and idle_s < last:
            # The delta went DOWN: the pane was active since the last
            # poll — a NEW idle period the engine has not warned for.
            self._warned.discard(pane_id)
        if idle_s + self._warn_before < self._ttl:
            return None  # still comfortably hot
        if pane_id in self._warned:
            return None  # already warned for this idle period
        self._warned.add(pane_id)
        return {
            "pane_id": pane_id,
            "idle_s": idle_s,
            "expires_in_s": max(0.0, self._ttl - idle_s),
        }
