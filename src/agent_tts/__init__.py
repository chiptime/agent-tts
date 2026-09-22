"""agent-tts: Lightweight neural TTS engine with interactive controls for AI agents."""

__version__ = "0.3.0"

from agent_tts import audio_store
from agent_tts.audio import AudioSession, cleanup_locks, play_mp3_data, play_mp3_file
from agent_tts.audio_store import audio_duration
from agent_tts.boundaries import (
    BoundaryMap,
    Paragraph,
    Sentence,
    SynthesisResult,
    Word,
    apply_bionic_reading,
    bionic_word,
    estimate_boundaries_from_text,
)
from agent_tts.cleaner import clean_agent_text, extract_last_turn, strip_ansi
from agent_tts.cli import main, speak, synthesize
from agent_tts.ipc import IPCServer, send_ipc_command
from agent_tts.lang_detector import (
    detect_language,
    resolve_voice_for_language,
    segment_by_language,
)
from agent_tts.podcast import (
    PodcastEpisode,
    PodcastFeed,
    run_podcast_server,
)
from agent_tts.providers import (
    EdgeTTSProvider,
    ElevenLabsTTSProvider,
    OpenAITTSProvider,
    TTSProvider,
    get_provider,
    provider_names,
    provider_voices,
)
from agent_tts.redact import redact_secrets
from agent_tts.summarizer import summarize


def __getattr__(name: str):
    # KokoroTTSProvider is OPTIONAL: resolved lazily (PEP 562) so importing
    # agent_tts never pulls the kokoro module graph or its heavy extras.
    if name == "KokoroTTSProvider":
        from agent_tts.providers import KokoroTTSProvider

        return KokoroTTSProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "__version__",
    "main",
    "speak",
    "synthesize",
    "audio_store",
    "audio_duration",
    "clean_agent_text",
    "extract_last_turn",
    "strip_ansi",
    "summarize",
    "detect_language",
    "segment_by_language",
    "resolve_voice_for_language",
    "PodcastEpisode",
    "PodcastFeed",
    "run_podcast_server",
    "AudioSession",
    "cleanup_locks",
    "play_mp3_data",
    "play_mp3_file",
    "BoundaryMap",
    "Paragraph",
    "Sentence",
    "Word",
    "SynthesisResult",
    "estimate_boundaries_from_text",
    "apply_bionic_reading",
    "bionic_word",
    "IPCServer",
    "send_ipc_command",
    "TTSProvider",
    "EdgeTTSProvider",
    "OpenAITTSProvider",
    "ElevenLabsTTSProvider",
    "get_provider",
    "provider_names",
    "provider_voices",
    "redact_secrets",
]
