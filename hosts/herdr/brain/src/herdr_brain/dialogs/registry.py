"""The harness registry: the one decision site for dialog recognition.

Maps a herdr ``agent`` string to its dialog recognizer. An agent absent
from the map has NO recognizer: the pane keeps the generic heuristic
parse and the manual keypad — never another harness's recipes.

Exact match only. Prefix folding here would hand one harness's live
keystroke recipes to any agent string sharing the prefix, which is a
keystroke aimed at a dialog that never promised it.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Protocol

from .model import StructuredDialog


class DialogAdapter(Protocol):
    """What every recognizer offers the registry."""

    agent: str

    def detect(self, lines: List[str], styled: Optional[list]) -> Optional[StructuredDialog]:
        ...  # pragma: no cover - structural protocol


# Second names for a registered recognizer. An alias resolves THROUGH
# to the canonical adapter; it never adds a harness to the list.
DIALOG_ALIASES: Dict[str, str] = {
    "omp": "pi",
    "agy": "antigravity",
}


def _build() -> Dict[str, DialogAdapter]:
    from .adapters.antigravity import AntigravityAdapter
    from .adapters.claude import ClaudeAdapter
    from .adapters.codex import CodexAdapter
    from .adapters.cursor import CursorAdapter
    from .adapters.grok import GrokAdapter
    from .adapters.kiro import KiroAdapter
    from .adapters.muse import MuseAdapter
    from .adapters.opencode import OpencodeAdapter, Opencode2Adapter
    from .adapters.pi import PiAdapter

    adapters = (
        AntigravityAdapter(),
        ClaudeAdapter(),
        CodexAdapter(),
        CursorAdapter(),
        GrokAdapter(),
        KiroAdapter(),
        MuseAdapter(),
        OpencodeAdapter(),
        Opencode2Adapter(),
        PiAdapter(),
    )
    # Built FROM the adapter list (not a hand-written literal) so a key
    # can never silently drift from the recognizer it points at.
    return {adapter.agent: adapter for adapter in adapters}


_REGISTRY = _build()


def adapter_for(agent: Optional[str]) -> Optional[DialogAdapter]:
    """The recognizer for ``agent`` (exact name or declared alias)."""
    if not agent:
        return None
    key = agent.strip().lower()
    canonical = DIALOG_ALIASES.get(key, key)
    return _REGISTRY.get(canonical)


def registered_agents() -> List[str]:
    """Every agent string the registry maps, sorted."""
    return sorted(_REGISTRY)


def recognize(
    agent: Optional[str], screen: str, styled: Optional[list] = None
) -> Optional[StructuredDialog]:
    """One harness's read of one screen — never a guess.

    ``None`` means "no claim" (the caller falls back to the generic
    heuristic); an UNREAD model means "this is my harness's dialog and
    no button may drive it".
    """
    adapter = adapter_for(agent)
    if adapter is None:
        return None
    lines = (screen or "").splitlines()
    if not any(line.strip() for line in lines):
        return None
    try:
        return adapter.detect(lines, styled)
    except Exception:  # noqa: BLE001 — a broken read is never a claim
        return None
