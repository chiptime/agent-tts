"""Speech rendering through the agent-tts engine (herdr-tts venv).

herdr-tts is the brain's OFFICIAL speech backend — a designed dependency,
not an accidental borrow: herdr-tts is the TUI-native voice layer AND the
engine that renders every answer and announcement the PWA plays. The
contract is checked fail-soft at startup (see tts_backend_status) and
enforced at render time with an explicit, actionable error.

The herdr-tts plugin invokes the agent-tts engine as:

    $VENV_PYTHON lib/tts_engine.py "<text>" --voice V --rate R --max-chars N
        [--output FILE] [--no-play] [provider flags]

``--output`` + ``--no-play`` renders an MP3 to disk without ever playing on
the local machine — exactly what the brain needs, since audio must play on
the client (phone/PC browser), never on PC speakers. Verified live: the call
produces a valid MPEG layer III file.

The engine is invoked via subprocess in the herdr-tts venv (which ships an
editable agent-tts install); tests mock the subprocess.
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from typing import Callable, Optional, Tuple

from .config import Settings

Runner = Callable[..., subprocess.CompletedProcess]

MP3_SUFFIX = ".mp3"

# The speech backend contract, stated once:
TTS_BACKEND_NAME = "herdr-tts"
TTS_BACKEND_MIN_VERSION = "v0.14"
TTS_BACKEND_OK = "ok"
TTS_BACKEND_MISSING = "missing"


class TTSError(RuntimeError):
    """Raised when the agent-tts engine fails to render audio."""


def tts_backend_status(settings: Settings) -> Tuple[str, str]:
    """Fail-soft check of the herdr-tts backend contract. Never raises.

    Returns (status, detail) with status 'ok' | 'missing'. Missing must
    never crash the server: text answers keep working, only speech is
    degraded.
    """
    missing = []
    if not settings.tts_python.is_file():
        missing.append(f"venv python at {settings.tts_python}")
    if not settings.tts_engine.is_file():
        missing.append(f"engine entry at {settings.tts_engine}")
    if not missing:
        return TTS_BACKEND_OK, (
            f"{TTS_BACKEND_NAME} speech backend ok "
            f"(venv {settings.tts_venv}, engine {settings.tts_engine})"
        )
    detail = (
        f"TTS backend {TTS_BACKEND_NAME} not found — missing "
        f"{' and '.join(missing)} — herdr-brain requires "
        f"{TTS_BACKEND_NAME} >= {TTS_BACKEND_MIN_VERSION} as its speech "
        f"backend. Install herdr-tts (or point HERDR_TTS_VENV at its venv, "
        f"HERDR_TTS_ENGINE at the engine script) and restart."
    )
    return TTS_BACKEND_MISSING, detail


def sanitize_for_speech(text: str) -> str:
    """Collapses whitespace/newlines into a speakable line."""
    return " ".join(text.split())


def render_mp3(
    settings: Settings,
    text: str,
    out_path: Path,
    runner: Optional[Runner] = None,
) -> Path:
    """Renders ``text`` to an MP3 file and returns the path.

    Raises TTSError when the backend contract is missing, the engine exits
    non-zero or no file appears.
    """
    clean = sanitize_for_speech(text)
    if not clean:
        raise TTSError("refusing to synthesize empty text")

    status, detail = tts_backend_status(settings)
    if status != TTS_BACKEND_OK:
        raise TTSError(detail)

    cmd = [
        str(settings.tts_python),
        str(settings.tts_engine),
        clean,
        "--voice", settings.tts_voice,
        "--rate", settings.tts_rate,
        "--max-chars", str(settings.tts_max_chars),
        "--output", str(out_path),
        "--no-play",
        *settings.tts_extra_args,
    ]
    run: Runner = runner if runner is not None else subprocess.run
    try:
        proc = run(cmd, capture_output=True, text=True, timeout=settings.tts_timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise TTSError(f"tts engine timed out after {settings.tts_timeout_s}s") from exc
    except OSError as exc:
        raise TTSError(f"failed to execute tts engine: {exc}") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise TTSError(f"tts engine exited with {proc.returncode}: {detail[:400]}")
    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise TTSError(f"tts engine produced no audio at {out_path}")
    return out_path


def new_audio_path(settings: Settings, prefix: str = "") -> Path:
    """Returns a fresh unique MP3 path inside the audio dir (created).

    Announcements use the ``ann-`` prefix so the watcher can garbage-collect
    its own files without touching chat-answer audio.
    """
    settings.audio_dir.mkdir(parents=True, exist_ok=True)
    return settings.audio_dir / f"{prefix}{uuid.uuid4().hex}{MP3_SUFFIX}"
