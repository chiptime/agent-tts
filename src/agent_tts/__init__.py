"""agent-tts: Lightweight neural TTS engine with interactive controls for AI agents."""

__version__ = "0.1.0"

from agent_tts.audio import AudioSession, cleanup_locks, play_mp3_data, play_mp3_file
from agent_tts.cleaner import clean_agent_text, extract_last_turn, strip_ansi
from agent_tts.cli import speak, synthesize
from agent_tts.ipc import IPCServer, send_ipc_command
from agent_tts.providers import (
    EdgeTTSProvider,
    ElevenLabsTTSProvider,
    OpenAITTSProvider,
    TTSProvider,
    get_provider,
)

__all__ = [
    "__version__",
    "speak",
    "synthesize",
    "clean_agent_text",
    "extract_last_turn",
    "strip_ansi",
    "AudioSession",
    "cleanup_locks",
    "play_mp3_data",
    "play_mp3_file",
    "IPCServer",
    "send_ipc_command",
    "TTSProvider",
    "EdgeTTSProvider",
    "OpenAITTSProvider",
    "ElevenLabsTTSProvider",
    "get_provider",
]
