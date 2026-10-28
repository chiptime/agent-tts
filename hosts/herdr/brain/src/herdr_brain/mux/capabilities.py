"""Declared, probed capability facts for the multiplexer port.

Two rules, enforced by construction:

- A capability cell is backed by recorded first-hand evidence (a pinned
  fixture, a verified CLI shape) or it is not declared at all. A cell
  without evidence can never be claimed: :class:`Capabilities` refuses to
  build. "A cell that was never probed is never declared supported."
- Facts describe the multiplexer's shape (how stale topology can get,
  whether there is one space or many); they are strings, not booleans,
  because they answer "how", not "whether".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

#: The port's capability vocabulary. Every API method maps to one cell:
#: grid listing (``pane_grid``), styled screen reads (``ansi_screen``),
#: backlog reads (``scrollback``), prompt submission (``prompt_submit``),
#: logical keys (``keys``), tab creation (``tabs``), agent lifecycle
#: (``agent_start``), agent identity and status (``agent_detection``) and
#: the stable session reference (``session_ref``).
CAPABILITY_NAMES = (
    "pane_grid",
    "ansi_screen",
    "scrollback",
    "prompt_submit",
    "keys",
    "tabs",
    "agent_start",
    "agent_detection",
    "session_ref",
)

_ALL_CAPABILITIES = frozenset(CAPABILITY_NAMES)


@dataclass(frozen=True)
class Capabilities:
    """The matrix one adapter declares: cells, facts, evidence."""

    supported: frozenset
    evidence: Mapping[str, str] = field(default_factory=dict)
    facts: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        supported = frozenset(self.supported)
        unknown = supported - _ALL_CAPABILITIES
        if unknown:
            raise ValueError(
                f"unknown capability {', '.join(sorted(unknown))}: "
                f"expected one of {', '.join(CAPABILITY_NAMES)}"
            )
        unprobed = supported - set(self.evidence)
        if unprobed:
            raise ValueError(
                f"capability claimed without probe evidence: "
                f"{', '.join(sorted(unprobed))}"
            )
        object.__setattr__(self, "supported", supported)
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))
        object.__setattr__(self, "facts", MappingProxyType(dict(self.facts)))

    def supports(self, name: str) -> bool:
        """Whether this adapter declares (with evidence) the capability."""
        return name in self.supported

    def fact(self, name: str, default: str = "") -> str:
        """A declared fact (e.g. ``topology_latency``); ``default`` if absent."""
        return self.facts.get(name, default)


@dataclass(frozen=True)
class MuxProbe:
    """Outcome of a first-hand reachability probe.

    A probe answers "did the multiplexer's CLI answer at all". It carries
    no capability cells on purpose: probing can drop or annotate claims at
    the call site, but never enlarge them.
    """

    reachable: bool
    detail: str
    version: str = ""
