"""The harness registry: the one decision site for readable history.

Maps a harness name to its transcript connector. A harness absent from
the registry has no history — an ordinary condition the caller reports
as ``no-session``, never an error. Adding a harness is one entry in the
table below plus its module; no route or reader grows a new branch.

Names come from an agent's own report, so resolution is a plain lookup
with a declared alias map on top (an agent may answer to two names that
write the same on-disk format — ``omp`` is pi's format under Oh My Pi's
home). An alias never adds a harness: the harness list answers "which
connectors does this build have", and an alias does not change the count.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Protocol

from .codex import CodexTranscript
from .cursor import CursorTranscript
from .grok import GrokTranscript
from .hermes import HermesTranscript
from .kiro import KiroTranscript
from .muse import MuseTranscript
from .opencode2 import Opencode2Transcript
from .pi import PiTranscript


class TranscriptReader(Protocol):
    """What every connector offers the registry."""

    name: str

    def read(
        self, session_id: str, n_turns: int = 10, skip_newest: int = 0
    ) -> List:  # pragma: no cover - structural protocol
        ...

    def read_title(self, session_id: str) -> Optional[str]:  # pragma: no cover
        ...


# Second names for a registered connector. The alias resolves THROUGH to
# the canonical reader; it is a map so both the registry and anything
# mirroring the pairs read the same declaration.
HARNESS_ALIASES: Dict[str, str] = {
    "omp": "pi",
    "oa": "opencode",
    "agy": "antigravity",
}


class TranscriptRegistry:
    """Immutable name → connector table with alias resolution."""

    def __init__(self, readers: Dict[str, TranscriptReader], aliases: Optional[Dict[str, str]] = None):
        self._readers = dict(readers)
        self._aliases = dict(aliases or HARNESS_ALIASES)

    def resolve(self, agent: Optional[str]) -> Optional[TranscriptReader]:
        """The connector for ``agent`` (exact name or alias), or None."""
        if not agent:
            return None
        key = agent.strip().lower()
        canonical = self._aliases.get(key, key)
        return self._readers.get(canonical)

    def harnesses(self) -> List[str]:
        """Canonical harness names this registry serves, sorted."""
        return sorted(self._readers)


def default_registry() -> TranscriptRegistry:
    """The registry with every built-in connector at its env-configured roots.

    Lazy import of the three original connectors (they live in the
    parent module) keeps the import graph acyclic at module load.
    """
    from ..transcripts import (
        AntigravityTranscript,
        ClaudeTranscript,
        OpencodeTranscript,
    )

    readers: Dict[str, TranscriptReader] = {
        AntigravityTranscript.name: AntigravityTranscript(),
        ClaudeTranscript.name: ClaudeTranscript(),
        CodexTranscript.name: CodexTranscript(),
        CursorTranscript.name: CursorTranscript(),
        GrokTranscript.name: GrokTranscript(),
        HermesTranscript.name: HermesTranscript(),
        KiroTranscript.name: KiroTranscript(),
        MuseTranscript.name: MuseTranscript(),
        Opencode2Transcript.name: Opencode2Transcript(),
        OpencodeTranscript.name: OpencodeTranscript(),
        PiTranscript.name: PiTranscript(),
    }
    return TranscriptRegistry(readers)
