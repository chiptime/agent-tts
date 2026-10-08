"""Engine-backed STT: delegate to the agent-tts resident worker (F4.10).

The brain stops running faster-whisper in-process and talks to the
engine-owned resident worker through its PUBLIC CLI ONLY (same seam the
tts-plugin uses — the pinned venv python running ``-m agent_tts.stt.cli``).
The brain never imports ``agent_tts`` and never starts the worker itself:
``agent-tts-stt serve`` is an explicit operator action, so a down worker
surfaces as ``unavailable`` with that hint.

Model policy (unchanged HARD RULE): the model is NEVER auto-downloaded.
The only download path is ``python -m herdr_brain.stt pull``, which in
engine mode delegates to the engine pull CLI (streaming passthrough).

State model: ``state`` is derived from a cached status probe with a short
monotonic TTL, so a polling /health never hammers the worker socket. A
plain read BEFORE the first interaction (boot warmup probe or a
transcribe) reports ``loading`` and spawns NOTHING — the probe runs in
the boot warmup thread (production default) or is implied by transcribe
outcomes; after the first observation a stale cache re-probes on read so
health recovers when the operator restarts the worker.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from .stt import STATE_LOADING, STATE_READY, STATE_UNAVAILABLE

# --- engine CLI contract (mirrored; the brain must not import agent_tts) ---

ENGINE_MODULE = "agent_tts.stt.cli"
WORKER_UNAVAILABLE_EXIT = 5  # worker.py WorkerUnavailableError.exit_code
MODEL_UNAVAILABLE_EXIT = 4  # model missing on the worker side

# Mirrors agent_tts.stt.worker.MAX_AUDIO_BYTES: pre-check here so an
# oversized upload never even writes the temp file.
MAX_AUDIO_BYTES = 24 * 1024 * 1024

STATUS_TIMEOUT_S = 15.0  # the CLI itself gives up on the socket after 10 s
DEFAULT_TRANSCRIBE_TIMEOUT_S = 120.0
TRANSCRIBE_TIMEOUT_ENV = "HERDR_BRAIN_STT_TIMEOUT_S"

STATUS_TTL_S = 5.0  # one status probe per window, monotonic clock

ENGINE_PYTHON_ENV = "HERDR_BRAIN_STT_PYTHON"
# Same pinned interpreter the tts-plugin surface uses (bin/herdr-tts:
# DATA_DIR/venv/bin/python with DATA_DIR = ~/.local/share/herdr-tts).
PLUGIN_VENV_PYTHON = "~/.local/share/herdr-tts/venv/bin/python"

SERVE_HINT = "start the worker: agent-tts-stt serve"

_STDERR_TAIL_CHARS = 400  # bounded error surface, never a full traceback

_Runner = Callable[..., subprocess.CompletedProcess]


class EngineSttError(RuntimeError):
    """Actionable transcribe failure; the message names the remedy."""


class EnginePythonUnavailable(EngineSttError):
    """No usable engine python: names the override env var."""


def default_runner(argv, timeout, capture=True) -> subprocess.CompletedProcess:
    """The only real subprocess path; tests inject a fake ``runner``."""
    return subprocess.run(
        list(argv), capture_output=capture, text=True, timeout=timeout
    )


def resolve_engine_python() -> str:
    """Engine python resolution, in order: explicit env, plugin venv.

    ``HERDR_BRAIN_STT_PYTHON`` wins verbatim (an operator override is
    trusted as-is); otherwise the plugin data venv is used WHEN it
    exists. Anything else is a typed error naming the env var. No import
    probes: the first real CLI invocation is the check.
    """
    explicit = os.environ.get(ENGINE_PYTHON_ENV, "").strip()
    if explicit:
        return explicit
    venv = Path(PLUGIN_VENV_PYTHON).expanduser()
    if venv.is_file():
        return str(venv)
    raise EnginePythonUnavailable(
        f"no engine python found: set {ENGINE_PYTHON_ENV} or install the "
        f"herdr-tts plugin venv at {PLUGIN_VENV_PYTHON}"
    )


def _tail(text: str, limit: int = _STDERR_TAIL_CHARS) -> str:
    return (text or "").strip()[-limit:]


def run_engine_pull(
    python_resolver: Optional[Callable[[], Optional[str]]] = None,
    runner: Optional[_Runner] = None,
) -> int:
    """``pull`` delegation: run the engine pull CLI with streaming output.

    No capture and no timeout: the operator watches the real download
    progress and can interrupt it. The engine exit code is the command's.
    """
    run = runner or default_runner
    try:
        python = (python_resolver or resolve_engine_python)()
        if not python:
            raise EnginePythonUnavailable(
                f"no engine python found: set {ENGINE_PYTHON_ENV}"
            )
    except EnginePythonUnavailable as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    proc = run([python, "-m", ENGINE_MODULE, "pull"], None, capture=False)
    return proc.returncode


class EngineTranscriber:
    """Duck-type twin of ``stt.Transcriber`` backed by the engine worker.

    ``runner``, ``python_resolver`` and ``now`` are injection seams for
    tests (fake subprocesses, fake engine python, fake clock). Stdlib
    only; the engine boundary is the CLI subprocess, nothing else.
    """

    def __init__(
        self,
        settings,
        runner: Optional[_Runner] = None,
        python_resolver: Optional[Callable[[], Optional[str]]] = None,
        now: Optional[Callable[[], float]] = None,
    ):
        self._settings = settings
        self._runner = runner or default_runner
        self._python_resolver = python_resolver or resolve_engine_python
        self._now = now or time.monotonic
        self._lock = threading.Lock()
        self._error: Optional[str] = None
        self._state: str = STATE_LOADING
        self._state_at: Optional[float] = None  # monotonic timestamp
        self._ever_probed = False
        self._python: Optional[str] = None
        self._python_resolved = False
        self._transcribe_timeout = float(
            os.environ.get(TRANSCRIBE_TIMEOUT_ENV, str(DEFAULT_TRANSCRIBE_TIMEOUT_S))
        )

    # -- python resolution (lazy, cached) --

    def _resolve_python(self) -> str:
        if not self._python_resolved:
            python = self._python_resolver()
            if not python:
                raise EnginePythonUnavailable(
                    f"no engine python found: set {ENGINE_PYTHON_ENV}"
                )
            self._python = python
            self._python_resolved = True
        return self._python  # type: ignore[return-value]

    # -- state cache --

    def _observe(self, state: str, error: Optional[str]) -> None:
        with self._lock:
            self._state = state
            self._error = error
            self._state_at = self._now()
            self._ever_probed = True

    def _probe_status(self, force: bool) -> None:
        """Refresh the state cache from a CLI ``status`` op; never raises."""
        with self._lock:
            if not force and self._state_at is not None:
                if self._now() - self._state_at < STATUS_TTL_S:
                    return
            try:
                python = self._resolve_python()
            except EnginePythonUnavailable as exc:
                self._state, self._error = STATE_UNAVAILABLE, str(exc)
                self._state_at, self._ever_probed = self._now(), True
                return
            try:
                proc = self._runner(
                    [python, "-m", ENGINE_MODULE, "status"], STATUS_TIMEOUT_S
                )
            except Exception as exc:  # noqa: BLE001 — a probe failure is state
                self._state = STATE_UNAVAILABLE
                self._error = f"status probe failed: {exc}"
                self._state_at, self._ever_probed = self._now(), True
                return
        # Outside the lock: parsing cannot race the bookkeeping above.
        if proc.returncode == WORKER_UNAVAILABLE_EXIT:
            self._observe(STATE_UNAVAILABLE, SERVE_HINT)
            return
        try:
            payload = json.loads(proc.stdout or "")
        except ValueError:
            self._observe(
                STATE_UNAVAILABLE,
                f"unparseable engine status (exit {proc.returncode}): "
                f"{_tail(proc.stderr)}",
            )
            return
        if payload.get("ok") is not True:
            error = (payload.get("error") or {}).get("message") or _tail(proc.stderr)
            self._observe(STATE_UNAVAILABLE, _tail(str(error)))
            return
        state = payload.get("state")
        if state not in (STATE_LOADING, STATE_READY, STATE_UNAVAILABLE):
            self._observe(STATE_UNAVAILABLE, f"unknown engine state: {state!r}")
            return
        error = payload.get("error") if state == STATE_UNAVAILABLE else None
        self._observe(state, error)

    @property
    def state(self) -> str:
        """Cached worker state; spawns nothing until first interaction."""
        if self._ever_probed:
            self._probe_status(force=False)  # no-op inside the TTL window
        return self._state

    def error(self) -> Optional[str]:
        return self._error

    def model_present(self) -> bool:
        """Honest presence: the worker answers ready or loading."""
        self._probe_status(force=True)
        return self._state in (STATE_READY, STATE_LOADING)

    def maybe_start_warmup(self) -> Optional[threading.Thread]:
        """Boot probe in a daemon thread; nothing but the CLI status op.

        The caller gates on ``settings.stt_warmup`` (server.py), mirroring
        the legacy boot policy: tests with warmup off never probe.
        """
        thread = threading.Thread(
            target=self._probe_status, args=(True,), name="stt-engine-probe", daemon=True
        )
        thread.start()
        return thread

    # -- transcribe --

    def transcribe_bytes(self, data: bytes, suffix: str = ".webm") -> str:
        """One clip through the engine CLI; empty text is a valid result.

        The suffix rides the temp filename (the CLI derives the container
        from the extension). Failures raise ``EngineSttError`` with an
        actionable message — exit 5 flips the state to unavailable with
        the serve hint.
        """
        try:
            python = self._resolve_python()
        except EnginePythonUnavailable as exc:
            self._observe(STATE_UNAVAILABLE, str(exc))  # health tells the truth
            raise
        if len(data) > MAX_AUDIO_BYTES:
            raise EngineSttError(
                f"audio of {len(data)} bytes exceeds the {MAX_AUDIO_BYTES}-byte cap"
            )
        fd, tmp = tempfile.mkstemp(suffix=suffix, prefix="herdr-brain-stt-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            try:
                proc = self._runner(
                    [python, "-m", ENGINE_MODULE, "transcribe", "--file", tmp],
                    self._transcribe_timeout,
                )
            except subprocess.TimeoutExpired as exc:
                raise EngineSttError(
                    f"engine transcribe timed out after {exc.timeout}s"
                ) from None
        finally:
            try:
                Path(tmp).unlink()
            except OSError:
                pass
        return self._transcribe_result(proc)

    def _transcribe_result(self, proc) -> str:
        if proc.returncode == 0:
            try:
                payload = json.loads(proc.stdout or "")
            except ValueError:
                raise EngineSttError(
                    f"unparseable engine reply: {_tail(proc.stdout)}"
                ) from None
            if payload.get("ok") is not True:
                raise EngineSttError(
                    _tail(str((payload.get("error") or {}).get("message", "")))
                    or "engine transcribe failed"
                )
            self._observe(STATE_READY, None)  # a success proves the worker
            return str(payload.get("text", "")).strip()
        if proc.returncode == WORKER_UNAVAILABLE_EXIT:
            self._observe(STATE_UNAVAILABLE, SERVE_HINT)
            raise EngineSttError(SERVE_HINT)
        if proc.returncode == MODEL_UNAVAILABLE_EXIT:
            message = _tail(proc.stderr) or "engine model unavailable"
            self._observe(STATE_UNAVAILABLE, message)
            raise EngineSttError(message)
        raise EngineSttError(
            f"engine transcribe failed (exit {proc.returncode}): {_tail(proc.stderr)}"
        )
