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

render_mp3_cancellable runs the SAME surface call as a session-leader
job that can be aborted mid-render: cancellation signals the job's OWN
process group only — never any other process on this machine.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Callable, Optional, Tuple

from .config import Settings

Runner = Callable[..., subprocess.CompletedProcess]
# Job factory for the cancellable render: returns a Popen-like handle
# (.pid, .poll(), .wait(timeout), .terminate(), .kill()).
JobRunner = Callable[..., subprocess.Popen]

MP3_SUFFIX = ".mp3"

# The speech backend contract, stated once:
TTS_BACKEND_NAME = "herdr-tts"
TTS_SURFACE_VERSION = 1  # minimum --contract-version the brain accepts
TTS_BACKEND_OK = "ok"
TTS_BACKEND_MISSING = "missing"

CONTRACT_PROBE_TIMEOUT_S = 5.0

# Cancellable render (announcement path): poll cadence for the cancel
# event / child completion, and the TERM→KILL grace given to the job's
# OWN process group on cancel.
CANCEL_POLL_S = 0.2
SPEECH_CANCEL_TERM_S = 5.0

# POSIX process-group isolation for render jobs (no Windows logic: the
# brain deploys on Linux; without setsid the child itself — never a
# foreign group id — is signalled).
_HAS_SETSID = hasattr(os, "setsid")


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


def _render_command(settings: Settings, text: str, out_path: Path) -> list:
    """The v1 --render-text argv shared by BOTH render paths.

    ``text`` must already be sanitized (sanitize_for_speech). Keeping the
    construction here means the legacy render_mp3 command stays
    byte-identical while the cancellable path reuses the same surface.
    """
    return [
        str(settings.tts_bin),
        "--render-text",
        str(out_path),
        text,
        "--voice", settings.tts_voice,
        "--rate", settings.tts_rate,
    ]


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

    cmd = _render_command(settings, clean, out_path)
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


class _PopenRunner:
    """Default job factory: Popen in its OWN session (pgid = child pid).

    stdio never uses undrained pipes — a chatty child would fill one and
    wedge the poll loop — so stderr goes to an auto-removed temp file,
    read once after exit for failure detail (render_mp3 parity). close()
    is called on every outcome and releases whatever this runner created.
    """

    def __init__(self) -> None:
        self._err_files: dict = {}

    def __call__(self, cmd: list, cwd: Optional[str] = None) -> subprocess.Popen:
        err = tempfile.TemporaryFile(mode="w+b")
        try:
            child = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=err,
                start_new_session=_HAS_SETSID,
            )
        except BaseException:
            err.close()
            raise
        self._err_files[child.pid] = err
        return child

    def failure_detail(self, child: subprocess.Popen) -> str:
        err = self._err_files.get(child.pid)
        if err is None:
            return ""
        try:
            err.seek(0)
            return err.read().decode("utf-8", "replace").strip()
        except OSError:
            return ""

    def close(self) -> None:
        while self._err_files:
            _pid, err = self._err_files.popitem()
            try:
                err.close()
            except OSError:
                pass


def render_mp3_cancellable(
    settings: Settings,
    text: str,
    out_path: Path,
    cancel_event,
    runner: Optional[JobRunner] = None,
) -> None:
    """Renders ``text`` to ``out_path`` MP3, abortable by ``cancel_event``.

    Same v1 surface call as render_mp3 (shared _render_command), but the
    job runs as its own session leader (start_new_session): it OWNS a
    private process group (pgid = child pid). The poll loop checks the
    cancel event FIRST every CANCEL_POLL_S, then child completion, under
    the same settings.tts_timeout_s budget.

    Cancel path: SIGTERM to the job's OWN pgid, SPEECH_CANCEL_TERM_S
    grace, SIGKILL to the same pgid, reap with wait(), delete the
    partial out_path (and this call's temp files), raise
    TTSError("cancelled"). NOTHING outside the job's group is ever
    signalled — no kill-by-name exists in this module. Budget expiry
    runs the same teardown and raises the render_mp3-style timeout.

    ``cancel_event`` is any threading.Event-compatible object exposing
    ``is_set()``; ``runner`` is the injectable job factory (tests inject
    fakes; the default wraps subprocess.Popen).
    """
    clean = sanitize_for_speech(text)
    if not clean:
        raise TTSError("refusing to synthesize empty text")

    launch: JobRunner = runner if runner is not None else _PopenRunner()
    cmd = _render_command(settings, clean, out_path)
    try:
        try:
            child = launch(cmd)
        except OSError as exc:
            raise TTSError(
                _contract_detail(f"cannot execute {settings.tts_bin}: {exc}")
            ) from exc

        deadline = time.monotonic() + settings.tts_timeout_s
        while True:
            if cancel_event.is_set():
                _terminate_job(child)
                _discard_partial(out_path)
                raise TTSError("cancelled")
            if child.poll() is not None:
                break  # job finished on its own
            if time.monotonic() >= deadline:
                _terminate_job(child)
                _discard_partial(out_path)
                raise TTSError(
                    f"tts render timed out after {settings.tts_timeout_s}s"
                )
            time.sleep(CANCEL_POLL_S)

        detail_fetch = getattr(launch, "failure_detail", None)
        detail = detail_fetch(child) if detail_fetch is not None else ""
        if child.returncode != 0:
            raise TTSError(
                f"tts render exited with {child.returncode}: {detail[:400]}"
            )
        if not out_path.is_file() or out_path.stat().st_size == 0:
            raise TTSError(f"tts render produced no audio at {out_path}")
        return None
    finally:
        closer = getattr(launch, "close", None)
        if closer is not None:
            closer()


def _signal_job(child: subprocess.Popen, sig: int) -> None:
    """Sends ``sig`` to THE JOB'S OWN process group only.

    start_new_session made the child a group leader (pgid == child.pid),
    so the signal can never reach the LLM worker, an approval action, or
    any other process on this machine. Without setsid the child itself —
    never a foreign group id — is signalled.
    """
    if _HAS_SETSID:
        try:
            os.killpg(child.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass  # the job's group is already gone — nothing to signal
    elif sig == signal.SIGKILL:
        child.kill()
    else:
        child.terminate()


def _terminate_job(child: subprocess.Popen) -> None:
    """Cancel teardown: SIGTERM → grace → SIGKILL, then reap.

    Both signals go to the job's own pgid; SPEECH_CANCEL_TERM_S is the
    grace a well-behaved herdr-tts gets to flush and exit before KILL.
    """
    _signal_job(child, signal.SIGTERM)
    if child.poll() is None:
        grace_until = time.monotonic() + SPEECH_CANCEL_TERM_S
        while child.poll() is None and time.monotonic() < grace_until:
            time.sleep(CANCEL_POLL_S)
    if child.poll() is None:
        _signal_job(child, signal.SIGKILL)
    child.wait()


def _discard_partial(out_path: Path) -> None:
    """Best-effort removal of the half-written artifact (cancelled/timeout)."""
    try:
        out_path.unlink()
    except OSError:
        pass


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
