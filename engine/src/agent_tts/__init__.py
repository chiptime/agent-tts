"""agent-tts: Lightweight neural TTS engine with interactive controls for AI agents.

Public names are resolved lazily (PEP 562) so importing ``agent_tts`` never
pulls heavy or optional dependency graphs at import time: the TTS audio stack
(``miniaudio``, ``edge_tts``, providers) loads only when a TTS name is
actually used, and the optional ``stt``/``KokoroTTSProvider`` surfaces stay
inert until accessed. This keeps the STT CLI usable on interpreters that
ship faster-whisper without the TTS stack (and vice versa).
"""

__version__ = "0.3.0"

from importlib import import_module
from typing import Any

# Public name -> owning submodule. Attribute access imports the submodule
# once and resolves the name from it; the import system's own submodule
# machinery keeps `from agent_tts import X` working unchanged.
_LAZY_EXPORTS: dict = {
    "audio_store": "agent_tts.audio_store",
    "AudioSession": "agent_tts.audio",
    "cleanup_locks": "agent_tts.audio",
    "play_mp3_data": "agent_tts.audio",
    "play_mp3_file": "agent_tts.audio",
    "audio_duration": "agent_tts.audio_store",
    "BoundaryMap": "agent_tts.boundaries",
    "Paragraph": "agent_tts.boundaries",
    "Sentence": "agent_tts.boundaries",
    "SynthesisResult": "agent_tts.boundaries",
    "Word": "agent_tts.boundaries",
    "apply_bionic_reading": "agent_tts.boundaries",
    "bionic_word": "agent_tts.boundaries",
    "estimate_boundaries_from_text": "agent_tts.boundaries",
    "clean_agent_text": "agent_tts.cleaner",
    "extract_last_turn": "agent_tts.cleaner",
    "strip_ansi": "agent_tts.cleaner",
    "main": "agent_tts.cli",
    "speak": "agent_tts.cli",
    "synthesize": "agent_tts.cli",
    "IPCServer": "agent_tts.ipc",
    "ipc_reply_json": "agent_tts.ipc",
    "send_ipc_command": "agent_tts.ipc",
    "detect_language": "agent_tts.lang_detector",
    "resolve_voice_for_language": "agent_tts.lang_detector",
    "segment_by_language": "agent_tts.lang_detector",
    "PodcastEpisode": "agent_tts.podcast",
    "PodcastFeed": "agent_tts.podcast",
    "run_podcast_server": "agent_tts.podcast",
    "TTSProvider": "agent_tts.providers",
    "EdgeTTSProvider": "agent_tts.providers",
    "ElevenLabsTTSProvider": "agent_tts.providers",
    "OpenAITTSProvider": "agent_tts.providers",
    "get_provider": "agent_tts.providers",
    "provider_names": "agent_tts.providers",
    "provider_voices": "agent_tts.providers",
    "redact_secrets": "agent_tts.redact",
    "summarize": "agent_tts.summarizer",
}

_ALL_NAMES = [
    "__version__",
    *_LAZY_EXPORTS.keys(),
    "KokoroTTSProvider",
    "stt",
]

__all__ = _ALL_NAMES


def __getattr__(name: str) -> Any:
    # KokoroTTSProvider is OPTIONAL: resolved lazily so attribute access is
    # what pulls the kokoro module graph or its heavy extras.
    if name == "KokoroTTSProvider":
        from agent_tts.providers import KokoroTTSProvider

        return KokoroTTSProvider
    # The STT subpackage is likewise optional and lazy: attribute access
    # imports a stdlib-only package, never faster-whisper. import_module
    # (not `from agent_tts import stt`) — the fromlist form would re-enter
    # this __getattr__ and recurse before the submodule lands in sys.modules.
    if name == "stt":
        return import_module("agent_tts.stt")
    target = _LAZY_EXPORTS.get(name)
    if target is not None:
        return getattr(import_module(target), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list:
    return sorted(set(globals()) | set(_ALL_NAMES))
