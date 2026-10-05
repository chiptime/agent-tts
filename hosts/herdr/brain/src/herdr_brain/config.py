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

# AT-11 design Decision 4 port knob: HERDR_BRAIN_PORT -> persisted config
# (<config>/herdr-brain/config.env, key HERDR_BRAIN_PORT) -> this default.
# Mirrors tools/herdr_onboarding/resolve.py: the installed brain package
# cannot import the monorepo-root tools module without adding a runtime
# dependency, which design Decision 1 forbids.
DEFAULT_BRAIN_PORT = 8741

# Verified live layout: herdr-tts is the speech backend, consumed ONLY
# through its versioned CLI surface (contract v1). The home points at the
# herdr-tts repo root; the CLI derives everything else (including its own
# venv) internally.
DEFAULT_TTS_HOME = "~/Code/personal/agent-tts/hosts/herdr/tts-plugin"
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

# Approval gate window (PRD-action-approval-gate §6): lazy server-side
# expiry check on every gate touch — no background timer thread.
DEFAULT_APPROVAL_TIMEOUT_S = 60

# Speech-to-text (faster-whisper). HARD RULE: the model is never
# auto-downloaded — `python -m herdr_brain.stt pull` is the only download
# path. stt_warmup=False (tests) disables the boot warmup thread entirely:
# without warmup the model can never load through HTTP, because /transcribe
# only transcribes once the state is READY and only warmup() sets READY.
DEFAULT_STT_MODEL = "small"
DEFAULT_STT_DEVICE = "auto"
DEFAULT_STT_COMPUTE = "auto"

# Reader (formatted conversation rendering). Distinct from tts_timeout_s
# (240 s, calibrated for speech): an interactive reader turn is a fast
# local transform, so 30 s bounds a hung renderer without pinning the
# reader semaphore for minutes.
DEFAULT_READER_TIMEOUT_S = 30
DEFAULT_READER_CONCURRENCY = 2

# Announcement digest length (product decision 2026-09-25): the digest
# detail carries up to this many chars (preferring a sentence boundary),
# and the SAME clipped string is both the SSE text payload and the spoken
# announcement — audio and visual always match.
DEFAULT_ANNOUNCE_MAX_CHARS = 300

# On-demand context (consult) wiring (T9; PRD herdr-brain-on-demand-
# context): every knob is OPTIONAL — None/0 means "the module default"
# (stores beside the audio dir, provider env/default paths, engine
# constants). The budget/threshold numbers MUST stay in sync with
# queryfsm.DEFAULT_BUDGET_SECONDS / DEFAULT_NARROW_* (config cannot
# import queryfsm without an import cycle through evidence->herdr).
DEFAULT_CONSULT_BUDGET_S = 60.0
DEFAULT_CONSULT_NARROW_ITEMS = 4000
DEFAULT_CONSULT_NARROW_CHARS = 500_000

# Max span (in days) for EXPLICIT consult periods (product decision
# 2026-09-30). Kept deliberately simple: a POSITIVE int only — there is
# no "0/negative = unlimited" spelling; non-positive values fail loudly
# at load time instead of silently disabling the cap. Natural periods
# are unaffected (inherently bounded). Must stay in sync with
# queryfsm.DEFAULT_MAX_SPAN (same import-cycle rule as above).
DEFAULT_CONSULT_MAX_SPAN_DAYS = 60


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
    approval_timeout_s: int
    screen_lines: int
    stt_model: str
    stt_device: str
    stt_compute: str
    stt_warmup: bool
    # Reader knobs are appended WITH defaults: every pre-existing field is
    # non-default and tests construct Settings(**SETTINGS_KWARGS), so a
    # required field here would break every existing construction (and
    # dataclass ordering forbids non-default after default anyway).
    reader_timeout_s: int = DEFAULT_READER_TIMEOUT_S
    reader_concurrency: int = DEFAULT_READER_CONCURRENCY
    announce_max_chars: int = DEFAULT_ANNOUNCE_MAX_CHARS
    # Consult (on-demand context) overrides — appended WITH defaults,
    # same ordering rule as the reader knobs above. None = module
    # default (derived paths / provider env+default resolution).
    consult_budget_s: float = DEFAULT_CONSULT_BUDGET_S
    consult_narrow_items: int = DEFAULT_CONSULT_NARROW_ITEMS
    consult_narrow_chars: int = DEFAULT_CONSULT_NARROW_CHARS
    consult_max_span_days: int = DEFAULT_CONSULT_MAX_SPAN_DAYS
    report_db: Optional[str] = None
    followup_db: Optional[str] = None
    opencode_db: Optional[str] = None
    claude_root: Optional[str] = None
    antigravity_root: Optional[str] = None
    engram_db: Optional[str] = None
    # Appended with a default like the reader knobs: tests construct
    # Settings(**SETTINGS_KWARGS), so a required field would break every
    # existing construction.
    brain_port: int = DEFAULT_BRAIN_PORT

    def __post_init__(self):
        # Accept plain strings for path fields regardless of the caller.
        for field in ("tts_home", "tts_bin", "audio_dir"):
            value = getattr(self, field)
            if not isinstance(value, Path):
                object.__setattr__(self, field, Path(value).expanduser())


def _parse_brain_port(raw: str, source: str) -> int:
    try:
        port = int(raw.strip())
    except ValueError:
        raise ValueError(f"invalid {source} value {raw!r}: expected an integer port") from None
    if not 1 <= port <= 65535:
        raise ValueError(f"invalid {source} value {raw!r}: port must be 1-65535")
    return port


def _persisted_brain_port(environ: dict) -> Optional[int]:
    """Persisted port from ``<config>/herdr-brain/config.env`` (design
    Decision 4). Sourcable KEY=VALUE lines: comments, quotes and unrelated
    keys are tolerated; a missing file or key means "not persisted"."""
    config_home = environ.get("XDG_CONFIG_HOME", "").strip()
    if config_home:
        base = Path(config_home).expanduser()
    else:
        home = environ.get("HOME", "").strip()
        if not home:
            return None
        base = Path(home).expanduser() / ".config"
    path = base / "herdr-brain" / "config.env"
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.split("#", 1)[0].strip()
        if stripped.startswith("HERDR_BRAIN_PORT="):
            raw = stripped[len("HERDR_BRAIN_PORT="):].strip()
            if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
                raw = raw[1:-1]
            return _parse_brain_port(raw, str(path))
    return None


def load_settings(env: Optional[dict] = None) -> Settings:
    """Builds Settings from ``env`` (defaults to ``os.environ``)."""
    environ = os.environ if env is None else env

    def getenv(name: str, default: str = "") -> str:
        return environ.get(name, default)

    def positive_int(name: str) -> int:
        """Positive int only (documented rule for the max-span knob):
        no "unlimited" spelling — 0/negative fail loudly at load."""
        value = int(getenv(name, str(DEFAULT_CONSULT_MAX_SPAN_DAYS)))
        if value < 1:
            raise ValueError(f"{name} must be a positive integer, got {value}")
        return value

    # AT-11 design Decision 4: HERDR_BRAIN_PORT -> persisted config.env ->
    # default 8741 (same order as the launcher resolver block and
    # tools/herdr_onboarding/resolve.py).
    raw_port = getenv("HERDR_BRAIN_PORT").strip()
    if raw_port:
        brain_port = _parse_brain_port(raw_port, "HERDR_BRAIN_PORT")
    else:
        brain_port = _persisted_brain_port(environ) or DEFAULT_BRAIN_PORT


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
        approval_timeout_s=int(getenv("HERDR_BRAIN_APPROVAL_TIMEOUT_S", str(DEFAULT_APPROVAL_TIMEOUT_S))),
        screen_lines=int(getenv("HERDR_BRAIN_SCREEN_LINES", str(DEFAULT_SCREEN_LINES))),
        stt_model=getenv("HERDR_BRAIN_STT_MODEL", DEFAULT_STT_MODEL),
        stt_device=getenv("HERDR_BRAIN_STT_DEVICE", DEFAULT_STT_DEVICE),
        stt_compute=getenv("HERDR_BRAIN_STT_COMPUTE", DEFAULT_STT_COMPUTE),
        stt_warmup=getenv("HERDR_BRAIN_STT_WARMUP", "1") not in ("0", "false", "no"),
        reader_timeout_s=int(getenv(
            "HERDR_BRAIN_READER_TIMEOUT_S", str(DEFAULT_READER_TIMEOUT_S)
        )),
        reader_concurrency=int(getenv(
            "HERDR_BRAIN_READER_CONCURRENCY", str(DEFAULT_READER_CONCURRENCY)
        )),
        announce_max_chars=int(getenv(
            "HERDR_BRAIN_ANNOUNCE_MAX_CHARS", str(DEFAULT_ANNOUNCE_MAX_CHARS)
        )),
        consult_budget_s=float(getenv(
            "HERDR_BRAIN_CONSULT_BUDGET_S", str(DEFAULT_CONSULT_BUDGET_S)
        )),
        consult_narrow_items=int(getenv(
            "HERDR_BRAIN_CONSULT_NARROW_ITEMS", str(DEFAULT_CONSULT_NARROW_ITEMS)
        )),
        consult_narrow_chars=int(getenv(
            "HERDR_BRAIN_CONSULT_NARROW_CHARS", str(DEFAULT_CONSULT_NARROW_CHARS)
        )),
        consult_max_span_days=positive_int("HERDR_BRAIN_CONSULT_MAX_SPAN_DAYS"),
        report_db=getenv("HERDR_BRAIN_REPORT_DB") or None,
        followup_db=getenv("HERDR_BRAIN_FOLLOWUP_DB") or None,
        opencode_db=getenv("HERDR_BRAIN_OPENCODE_DB") or None,
        claude_root=getenv("HERDR_BRAIN_CLAUDE_ROOT") or None,
        antigravity_root=getenv("HERDR_BRAIN_ANTIGRAVITY_ROOT") or None,
        engram_db=getenv("HERDR_BRAIN_ENGRAM_DB") or None,
        brain_port=brain_port,
    )
