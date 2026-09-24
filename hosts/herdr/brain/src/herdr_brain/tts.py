"""Speech rendering through the herdr-tts CLI SURFACE (contract v1).

herdr-tts is the brain's OFFICIAL speech backend — a designed dependency.
The brain consumes ONLY its versioned CLI surface:

    bin/herdr-tts --contract-version            # prints "1"
    bin/herdr-tts --render-text OUT.mp3 TEXT [--voice V] [--rate R]

Everything underneath the surface (the engine, its venv, provider flags)
is herdr-tts's private detail: the brain invokes no venv internals and
must keep it that way. The command is self-sufficient — herdr-tts
bootstraps its own environment on first use.

The contract is verified fail-soft at boot (see tts_backend_status) and
render failures raise an explicit error naming the required surface
version. Tests inject a runner so no real subprocess runs.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path
from typing import Callable, Optional, Tuple

from .config import Settings

Runner = Callable[..., subprocess.CompletedProcess]

MP3_SUFFIX = ".mp3"

# The speech backend contract, stated once:
TTS_BACKEND_NAME = "herdr-tts"
TTS_SURFACE_VERSION = 1  # minimum --contract-version the brain accepts
TTS_BACKEND_OK = "ok"
TTS_BACKEND_MISSING = "missing"

CONTRACT_PROBE_TIMEOUT_S = 5.0


class TTSError(RuntimeError):
    """Raised when the speech backend fails to render audio."""


class ReaderError(RuntimeError):
    """Raised when the reader backend fails to produce an HTML/map pair."""


# Reader surface: herdr-tts --render-html INPUT OUTPUT --map MAP renders a
# whole Markdown document to anchored HTML plus the reader-pipeline/anchors@1
# sidecar. Same binary, same versioned CLI contract as speech.
READER_HTML_FLAG = "--render-html"
READER_MAP_FLAG = "--map"


def _contract_detail(reason: str) -> str:
    return (
        f"TTS backend {TTS_BACKEND_NAME} surface contract v{TTS_SURFACE_VERSION} "
        f"not satisfied — {reason} — herdr-brain requires {TTS_BACKEND_NAME} with "
        f"`bin/herdr-tts --contract-version` printing >= {TTS_SURFACE_VERSION}. "
        f"Install herdr-tts (or point HERDR_TTS_HOME at its repo root) and restart."
    )


def tts_backend_status(
    settings: Settings, runner: Optional[Runner] = None
) -> Tuple[str, str]:
    """Fail-soft check of the herdr-tts surface contract. Never raises.

    Runs ``bin/herdr-tts --contract-version`` (an ~instant early exit in
    herdr-tts) and requires an integer >= TTS_SURFACE_VERSION. Returns
    (status, detail) with status 'ok' | 'missing'.
    """
    run: Runner = runner if runner is not None else subprocess.run
    if not settings.tts_bin.is_file():
        return TTS_BACKEND_MISSING, _contract_detail(
            f"no CLI at {settings.tts_bin}"
        )
    try:
        proc = run(
            [str(settings.tts_bin), "--contract-version"],
            capture_output=True,
            text=True,
            timeout=CONTRACT_PROBE_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001 — timeout / exec errors → missing
        return TTS_BACKEND_MISSING, _contract_detail(f"probe failed: {exc}")
    if proc.returncode != 0:
        return TTS_BACKEND_MISSING, _contract_detail(
            f"probe exited {proc.returncode}"
        )
    version_text = (proc.stdout or "").strip()
    try:
        version = int(version_text)
    except ValueError:
        return TTS_BACKEND_MISSING, _contract_detail(
            f"probe printed {version_text!r}, expected an integer version"
        )
    if version < TTS_SURFACE_VERSION:
        return TTS_BACKEND_MISSING, _contract_detail(
            f"surface version {version} < required {TTS_SURFACE_VERSION}"
        )
    return TTS_BACKEND_OK, (
        f"{TTS_BACKEND_NAME} speech backend ok "
        f"(surface contract v{version} via {settings.tts_bin})"
    )


def sanitize_for_speech(text: str) -> str:
    """Collapses whitespace/newlines into a speakable line."""
    return " ".join(text.split())


def render_mp3(
    settings: Settings,
    text: str,
    out_path: Path,
    runner: Optional[Runner] = None,
) -> Path:
    """Renders ``text`` to an MP3 file through the herdr-tts surface.

    The surface VERSION is gated once at boot (tts_backend_status), not per
    render; here a missing/unexecutable CLI surfaces as TTSError naming the
    contract.
    """
    clean = sanitize_for_speech(text)
    if not clean:
        raise TTSError("refusing to synthesize empty text")

    cmd = [
        str(settings.tts_bin),
        "--render-text",
        str(out_path),
        clean,
        "--voice", settings.tts_voice,
        "--rate", settings.tts_rate,
    ]
    run: Runner = runner if runner is not None else subprocess.run
    try:
        proc = run(cmd, capture_output=True, text=True, timeout=settings.tts_timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise TTSError(f"tts render timed out after {settings.tts_timeout_s}s") from exc
    except OSError as exc:
        raise TTSError(_contract_detail(f"cannot execute {settings.tts_bin}: {exc}")) from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise TTSError(f"tts render exited with {proc.returncode}: {detail[:400]}")
    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise TTSError(f"tts render produced no audio at {out_path}")
    return out_path


def new_audio_path(settings: Settings, prefix: str = "") -> Path:
    """Returns a fresh unique MP3 path inside the audio dir (created).

    Announcements use the ``ann-`` prefix so the watcher can garbage-collect
    its own files without touching chat-answer audio.
    """
    settings.audio_dir.mkdir(parents=True, exist_ok=True)
    return settings.audio_dir / f"{prefix}{uuid.uuid4().hex}{MP3_SUFFIX}"


def render_html(
    settings: Settings,
    text: str,
    workdir: Optional[Path] = None,
    runner: Optional[Runner] = None,
) -> tuple[str, dict]:
    """Renders ``text`` to ``(html, sidecar)`` through the herdr-tts surface.

    The COMPLETE text travels as one UTF-8 document file (the CLI runs
    pre_extracted=True, so leading headings are preserved); argv carries
    only server-generated paths — transcript text NEVER enters argv. One
    ``TemporaryDirectory`` (mode 0o700) per invocation holds ``input.md``,
    ``out.html`` and ``out.map.json`` and is removed on every outcome:
    success, failure, timeout. Raises ``ReaderError`` on missing binary,
    exit 1/2/3, timeout, missing outputs, or undecodable output — the
    caller (reader cache) maps that to a fail-soft null pair.
    """
    import tempfile

    run: Runner = runner if runner is not None else subprocess.run
    if not settings.tts_bin.is_file():
        raise ReaderError(f"no reader CLI at {settings.tts_bin}")
    with tempfile.TemporaryDirectory(
        prefix="reader-render-",
        dir=None if workdir is None else str(workdir),
        ignore_cleanup_errors=True,
    ) as tmp:
        tmp_dir = Path(tmp)
        in_path = tmp_dir / "input.md"
        html_path = tmp_dir / "out.html"
        map_path = tmp_dir / "out.map.json"
        in_path.write_text(text, encoding="utf-8")
        cmd = [
            str(settings.tts_bin),
            READER_HTML_FLAG,
            str(in_path),
            str(html_path),
            READER_MAP_FLAG,
            str(map_path),
        ]
        try:
            proc = run(
                cmd,
                capture_output=True,
                text=True,
                timeout=settings.reader_timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise ReaderError(
                f"reader render timed out after {settings.reader_timeout_s}s"
            ) from exc
        except OSError as exc:
            raise ReaderError(
                f"cannot execute reader CLI {settings.tts_bin}: {exc}"
            ) from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise ReaderError(
                f"reader render exited with {proc.returncode}: {detail[:200]}"
            )
        if not html_path.is_file() or not map_path.is_file():
            raise ReaderError("reader render produced no outputs")
        try:
            html = html_path.read_text(encoding="utf-8", errors="strict")
            raw_map = map_path.read_text(encoding="utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ReaderError(f"reader output is not UTF-8: {exc}") from exc
        try:
            sidecar = json.loads(raw_map)
        except ValueError as exc:
            raise ReaderError(f"reader sidecar is not JSON: {exc}") from exc
        return html, sidecar
