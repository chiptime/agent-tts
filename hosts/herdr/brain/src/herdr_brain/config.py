"""Environment-driven configuration for herdr-brain.

Every knob is overridable through environment variables so the service can run
on any machine; defaults point at the verified live layout (herdr-tts venv,
agent-tts engine script). Secrets (GLM_API_KEY) are only read from the
environment and must never be committed.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

DEFAULT_GLM_BASE_URL = "https://api.z.ai/api/paas/v4/"
DEFAULT_GLM_MODEL = "glm-5"

# Verified live layout: herdr-tts ships a venv with the agent-tts engine
# importable, and its lib/tts_engine.py IS the agent-tts engine entrypoint.
DEFAULT_TTS_PYTHON = "~/.local/share/herdr-tts/venv/bin/python"
DEFAULT_TTS_ENGINE = "~/Code/personal/herdr-tts/lib/tts_engine.py"

DEFAULT_AUDIO_DIR = "~/.local/state/herdr-brain/audio"

DEFAULT_TTS_VOICE = "elvira"
DEFAULT_TTS_RATE = "+0%"
DEFAULT_TTS_MAX_CHARS = 300
DEFAULT_TTS_TIMEOUT_S = 120

# Conservative cap: reading more lines than the viewport scrolls the
# operator's real screen for alt-screen agents (herdr caveat).
MAX_SCREEN_LINES = 60
DEFAULT_SCREEN_LINES = 40

DEFAULT_PROMPT_TIMEOUT_MS = 180_000
DEFAULT_MAX_TOOL_ROUNDS = 4


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings resolved from the environment."""

    herdr_bin: str
    glm_api_key: Optional[str]
    glm_base_url: str
    glm_model: str
    tts_python: Path
    tts_engine: Path
    tts_voice: str
    tts_rate: str
    tts_max_chars: int
    tts_extra_args: Tuple[str, ...]
    tts_timeout_s: int
    audio_dir: Path
    prompt_timeout_ms: int
    max_tool_rounds: int
    screen_lines: int

    def __post_init__(self):
        # Accept plain strings for path fields regardless of the caller.
        for field in ("tts_python", "tts_engine", "audio_dir"):
            value = getattr(self, field)
            if not isinstance(value, Path):
                object.__setattr__(self, field, Path(value).expanduser())


def load_settings(env: Optional[dict] = None) -> Settings:
    """Builds Settings from ``env`` (defaults to ``os.environ``)."""
    environ = os.environ if env is None else env

    def getenv(name: str, default: str = "") -> str:
        return environ.get(name, default)

    tts_extra = tuple(shlex.split(getenv("HERDR_BRAIN_TTS_ARGS")))

    return Settings(
        herdr_bin=getenv("HERDR_BIN", "herdr"),
        glm_api_key=getenv("GLM_API_KEY") or None,
        glm_base_url=getenv("GLM_BASE_URL", DEFAULT_GLM_BASE_URL),
        glm_model=getenv("GLM_MODEL", DEFAULT_GLM_MODEL),
        tts_python=Path(getenv("HERDR_TTS_PYTHON", DEFAULT_TTS_PYTHON)).expanduser(),
        tts_engine=Path(getenv("HERDR_TTS_ENGINE", DEFAULT_TTS_ENGINE)).expanduser(),
        tts_voice=getenv("HERDR_BRAIN_VOICE", DEFAULT_TTS_VOICE),
        tts_rate=getenv("HERDR_BRAIN_RATE", DEFAULT_TTS_RATE),
        tts_max_chars=int(getenv("HERDR_BRAIN_MAX_CHARS", str(DEFAULT_TTS_MAX_CHARS))),
        tts_extra_args=tts_extra,
        tts_timeout_s=int(getenv("HERDR_BRAIN_TTS_TIMEOUT_S", str(DEFAULT_TTS_TIMEOUT_S))),
        audio_dir=Path(getenv("HERDR_BRAIN_AUDIO_DIR", DEFAULT_AUDIO_DIR)).expanduser(),
        prompt_timeout_ms=int(getenv("HERDR_BRAIN_PROMPT_TIMEOUT_MS", str(DEFAULT_PROMPT_TIMEOUT_MS))),
        max_tool_rounds=int(getenv("HERDR_BRAIN_MAX_TOOL_ROUNDS", str(DEFAULT_MAX_TOOL_ROUNDS))),
        screen_lines=int(getenv("HERDR_BRAIN_SCREEN_LINES", str(DEFAULT_SCREEN_LINES))),
    )
