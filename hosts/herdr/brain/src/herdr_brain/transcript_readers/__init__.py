"""Transcript connectors by harness.

``registry`` is the one decision site for "which agent has a readable
history"; each harness module reads its own on-disk format through the
shared ``base`` vocabulary. The original three connectors (OpenCode,
Claude, Antigravity) stay in :mod:`herdr_brain.transcripts` and join the
same registry.
"""

from .base import Turn
from .codex import CodexTranscript
from .grok import GrokTranscript
from .hermes import HermesTranscript
from .pi import PiTranscript
from .registry import HARNESS_ALIASES, TranscriptRegistry, default_registry

__all__ = [
    "CodexTranscript",
    "GrokTranscript",
    "HermesTranscript",
    "HARNESS_ALIASES",
    "PiTranscript",
    "TranscriptRegistry",
    "Turn",
    "default_registry",
]
