"""Environment-driven configuration for herdr-brain.

Every knob is overridable through environment variables so the service can run
on any machine; defaults point at the verified live layout (the herdr-tts
repo with its versioned CLI surface). Secrets (GLM_API_KEY) are only read
from the environment and must never be committed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DEFAULT_GLM_BASE_URL = "https://api.z.ai/api/paas/v4/"
DEFAULT_GLM_MODEL = "glm-5"

# Verified live layout: herdr-tts is the speech backend, consumed ONLY
# through its versioned CLI surface (contract v1). The home points at the
# herdr-tts repo root; the CLI derives everything else (including its own
# venv) internally.
DEFAULT_TTS_HOME = "~/Code/personal/herdr-tts"
TTS_SURFACE_BIN = "bin/herdr-tts"

DEFAULT_AUDIO_DIR = "~/.local/state/herdr-brain/audio"

DEFAULT_TTS_VOICE = "elvira"
DEFAULT_TTS_RATE = "+0%"
# Length capping is herdr-tts's own concern now (surface v1 has no
# --max-chars; its default is unlimited so full answers render).
DEFAULT_TTS_TIMEOUT_S = 240

# Conservative cap: reading more lines than the viewport scrolls the
# operator's real screen for alt-screen agents (herdr caveat).
MAX_SCREEN_LINES = 60
DEFAULT_SCREEN_LINES = 40
# Explicit full-screen reads (GET /screen) read scrollback instead; the
# larger window is justified by explicit user intent to READ, not glance.
MAX_BACKLOG_LINES = 120

DEFAULT_PROMPT_TIMEOUT_MS = 180_000
DEFAULT_MAX_TOOL_ROUNDS = 4

# Speech-to-text (faster-whisper). HARD RULE: the model is never
# auto-downloaded — `python -m herdr_brain.stt pull` is the only download
# path. stt_warmup=False (tests) disables the boot warmup thread entirely:
# without warmup the model can never load through HTTP, because /transcribe
# only transcribes once the state is READY and only warmup() sets READY.
DEFAULT_STT_MODEL = "small"
DEFAULT_STT_DEVICE = "auto"
DEFAULT_STT_COMPUTE = "auto"


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings resolved from the environment."""

    herdr_bin: str
    glm_api_key: Optional[str]
    glm_base_url: str
    glm_model: str
    tts_home: Path
    tts_bin: Path
    tts_voice: str
    tts_rate: str
    tts_timeout_s: int
    audio_dir: Path
    prompt_timeout_ms: int
    max_tool_rounds: int
    screen_lines: int
    stt_model: str
    stt_device: str
    stt_compute: str
    stt_warmup: bool

    def __post_init__(self):
        # Accept plain strings for path fields regardless of the caller.
        for field in ("tts_home", "tts_bin", "audio_dir"):
            value = getattr(self, field)
            if not isinstance(value, Path):
                object.__setattr__(self, field, Path(value).expanduser())


def load_settings(env: Optional[dict] = None) -> Settings:
    """Builds Settings from ``env`` (defaults to ``os.environ``)."""
    environ = os.environ if env is None else env

    def getenv(name: str, default: str = "") -> str:
        return environ.get(name, default)


    # Speech backend contract v1: HERDR_TTS_HOME names the herdr-tts repo
    # root; the surface CLI derives from it. The legacy HERDR_TTS_VENV /
    # HERDR_TTS_PYTHON / HERDR_TTS_ENGINE overrides are RETIRED (the brain
    # no longer knows about the venv or engine — surface only).
    tts_home = Path(getenv("HERDR_TTS_HOME", DEFAULT_TTS_HOME)).expanduser()

    return Settings(
        herdr_bin=getenv("HERDR_BIN", "herdr"),
        glm_api_key=getenv("GLM_API_KEY") or None,
        glm_base_url=getenv("GLM_BASE_URL", DEFAULT_GLM_BASE_URL),
        glm_model=getenv("GLM_MODEL", DEFAULT_GLM_MODEL),
        tts_home=tts_home,
        tts_bin=tts_home / TTS_SURFACE_BIN,
        tts_voice=getenv("HERDR_BRAIN_VOICE", DEFAULT_TTS_VOICE),
        tts_rate=getenv("HERDR_BRAIN_RATE", DEFAULT_TTS_RATE),
        tts_timeout_s=int(getenv("HERDR_BRAIN_TTS_TIMEOUT_S", str(DEFAULT_TTS_TIMEOUT_S))),
        audio_dir=Path(getenv("HERDR_BRAIN_AUDIO_DIR", DEFAULT_AUDIO_DIR)).expanduser(),
        prompt_timeout_ms=int(getenv("HERDR_BRAIN_PROMPT_TIMEOUT_MS", str(DEFAULT_PROMPT_TIMEOUT_MS))),
        max_tool_rounds=int(getenv("HERDR_BRAIN_MAX_TOOL_ROUNDS", str(DEFAULT_MAX_TOOL_ROUNDS))),
        screen_lines=int(getenv("HERDR_BRAIN_SCREEN_LINES", str(DEFAULT_SCREEN_LINES))),
        stt_model=getenv("HERDR_BRAIN_STT_MODEL", DEFAULT_STT_MODEL),
        stt_device=getenv("HERDR_BRAIN_STT_DEVICE", DEFAULT_STT_DEVICE),
        stt_compute=getenv("HERDR_BRAIN_STT_COMPUTE", DEFAULT_STT_COMPUTE),
        stt_warmup=getenv("HERDR_BRAIN_STT_WARMUP", "1") not in ("0", "false", "no"),
    )
