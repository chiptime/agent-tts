"""Speech rendering through the agent-tts engine (herdr-tts venv).

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
from typing import Callable, Optional

from .config import Settings

Runner = Callable[..., subprocess.CompletedProcess]

MP3_SUFFIX = ".mp3"


class TTSError(RuntimeError):
    """Raised when the agent-tts engine fails to render audio."""


def sanitize_for_speech(text: str) -> str:
    """Collapses whitespace/newlines into a single speakable line."""
    return " ".join(text.split())


def render_mp3(
    settings: Settings,
    text: str,
    out_path: Path,
    runner: Optional[Runner] = None,
) -> Path:
    """Renders ``text`` to an MP3 file and returns the path.

    Raises TTSError when the engine exits non-zero or no file appears.
    """
    clean = sanitize_for_speech(text)
    if not clean:
        raise TTSError("refusing to synthesize empty text")

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
