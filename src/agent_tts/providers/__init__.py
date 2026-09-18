"""Provider registry and factory for agent-tts."""

import os
import sys
from typing import Optional

from agent_tts.providers.base import TTSProvider
from agent_tts.providers.edge import EdgeTTSProvider
from agent_tts.providers.elevenlabs import ElevenLabsTTSProvider
from agent_tts.providers.kokoro import KokoroTTSProvider
from agent_tts.providers.openai import OpenAITTSProvider
from agent_tts.providers.piper import PiperTTSProvider


def get_provider(
    provider_name: str = "edge",
    openai_key: Optional[str] = None,
    openai_base_url: Optional[str] = None,
    openai_model: Optional[str] = None,
    eleven_key: Optional[str] = None,
    eleven_model: Optional[str] = None,
    piper_model: Optional[str] = None,
) -> TTSProvider:
    """Returns an instantiated TTS provider backend."""
    name = (provider_name or "edge").lower().strip()
    if name == "openai":
        return OpenAITTSProvider(
            api_key=openai_key or os.environ.get("OPENAI_API_KEY", ""),
            base_url=openai_base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            model=openai_model or os.environ.get("OPENAI_TTS_MODEL", "tts-1"),
        )
    elif name in ("elevenlabs", "eleven"):
        return ElevenLabsTTSProvider(
            api_key=eleven_key or os.environ.get("ELEVENLABS_API_KEY", ""),
            model=eleven_model or os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2"),
        )
    elif name in ("piper", "local"):
        return PiperTTSProvider(model_path=piper_model)
    elif name == "kokoro":
        return KokoroTTSProvider()
    elif name == "edge":
        return EdgeTTSProvider()
    else:
        print(f"Warning: Unknown provider '{provider_name}', falling back to 'edge'", file=sys.stderr)
        return EdgeTTSProvider()


__all__ = [
    "TTSProvider",
    "EdgeTTSProvider",
    "KokoroTTSProvider",
    "OpenAITTSProvider",
    "ElevenLabsTTSProvider",
    "PiperTTSProvider",
    "get_provider",
]
