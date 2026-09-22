"""Provider registry and factory for agent-tts."""

import os
import sys
from typing import List, Optional

from agent_tts.constants import DEFAULT_VOICE, VOICE_MAP
from agent_tts.providers.base import TTSProvider
from agent_tts.providers.edge import EdgeTTSProvider
from agent_tts.providers.elevenlabs import ElevenLabsTTSProvider
from agent_tts.providers.openai import OpenAITTSProvider
from agent_tts.providers.piper import PiperTTSProvider

# The kokoro provider is OPTIONAL: it is imported lazily (first use, PEP 562)
# so a default install never touches its module graph, and a missing optional
# dependency can never break provider listing or default edge synthesis.
_KOKORO_EXTRA_ERROR = (
    "kokoro provider requires optional dependencies: pip install 'agent-tts[kokoro]'"
)


def _kokoro_provider_cls():
    """Lazily imports and returns the KokoroTTSProvider class (optional extra)."""
    try:
        from agent_tts.providers.kokoro import KokoroTTSProvider
    except ImportError as e:
        raise RuntimeError(_KOKORO_EXTRA_ERROR) from e
    return KokoroTTSProvider


def __getattr__(name: str):
    if name == "KokoroTTSProvider":
        return _kokoro_provider_cls()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# --- Voice catalog ------------------------------------------------------------
# Public enumeration of the built-in per-provider voice maps. Map-backed
# providers (edge, openai, elevenlabs) derive their entries directly from the
# existing voice tables; model-backed providers (piper, kokoro) derive them
# from the voice store data, so nothing is duplicated here.
_CATALOG_PROVIDER_ORDER = ("edge", "openai", "elevenlabs", "piper", "kokoro")
_CATALOG_PROVIDER_ALIASES = {"eleven": "elevenlabs", "local": "piper"}


def provider_names() -> List[str]:
    """Returns the supported provider names in stable canonical order."""
    return list(_CATALOG_PROVIDER_ORDER)


def provider_voices(provider: str) -> List[str]:
    """Returns the known voices for one provider, derived from its voice data.

    Map-backed providers list their built-in voices; piper lists the voices
    installed in the voice store; kokoro lists the bundled default voice ids.
    Raises ValueError for an unknown provider name.
    """
    raw = (provider or "").strip().lower()
    key = _CATALOG_PROVIDER_ALIASES.get(raw, raw)
    if key == "edge":
        return sorted(set(VOICE_MAP.values()) | {DEFAULT_VOICE})
    if key == "openai":
        return sorted(OpenAITTSProvider.VALID_VOICES)
    if key == "elevenlabs":
        return sorted(ElevenLabsTTSProvider.VOICE_MAP)
    if key == "piper":
        from agent_tts.voices import list_voices

        return [e["name"] for e in list_voices() if e.get("provider") == "piper"]
    if key == "kokoro":
        from agent_tts.voices import KOKORO_VOICE_BINS

        return list(KOKORO_VOICE_BINS)
    raise ValueError(
        f"Unknown provider {provider!r}. Known providers: "
        f"{', '.join(_CATALOG_PROVIDER_ORDER)} (see provider_names())."
    )


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
        return _kokoro_provider_cls()()
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
    "provider_names",
    "provider_voices",
]
