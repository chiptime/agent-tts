"""The multiplexer port: one interface, herdr/tmux/zellij adapters.

The brain used to depend directly on :class:`~herdr_brain.herdr.HerdrClient`.
This package isolates that dependency behind the :class:`MuxAdapter`
protocol so a second multiplexer is a module, not a branch in every
route:

- :mod:`refusal` — :class:`MuxRefusal`, the one typed "no"
  (unsupported / gone / refused / unreachable), a ``HerdrError`` subtype
  so existing ``except HerdrError`` degradation keeps working.
- :mod:`capabilities` — :class:`Capabilities`, declared cells that MUST
  cite probe evidence (a cell never probed is never declared supported)
  plus shape facts (``topology_latency``, ``spaces``).
- :mod:`keys` — the neutral key orthography and per-multiplexer
  translation; the dialog allowlist is validated before any spawn.
- :mod:`adapters` — ``herdr`` (composition, byte-identical behavior),
  ``tmux`` (mirror + keys + tabs; agents refused), ``zellij``
  (raw mirror; every write refused).
- :mod:`registry` — :func:`create_mux` selects by ``HERDR_BRAIN_MUX``;
  :func:`probe_mux` checks reachability.
"""

from __future__ import annotations

from .adapters.herdr import HerdrMuxAdapter
from .adapters.tmux import TmuxAdapter
from .adapters.zellij import ZellijAdapter
from .capabilities import CAPABILITY_NAMES, Capabilities, MuxProbe
from .keys import SENDABLE_KEYS, is_sendable_key, normalize_key, tmux_key
from .protocol import MuxAdapter
from .refusal import REFUSAL_REASONS, MuxRefusal
from .registry import ENV_MUX, MUX_NAMES, create_mux, probe_mux

__all__ = [
    "CAPABILITY_NAMES",
    "ENV_MUX",
    "MUX_NAMES",
    "REFUSAL_REASONS",
    "Capabilities",
    "HerdrMuxAdapter",
    "MuxAdapter",
    "MuxProbe",
    "MuxRefusal",
    "SENDABLE_KEYS",
    "TmuxAdapter",
    "ZellijAdapter",
    "create_mux",
    "is_sendable_key",
    "normalize_key",
    "probe_mux",
    "tmux_key",
]
