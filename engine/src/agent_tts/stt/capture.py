"""Engine-owned microphone capture on the Windows host (PowerShell + MCI).

TECHNIQUE (one concrete choice, documented honestly): the default runner
locates ``powershell.exe`` exactly like :mod:`agent_tts.powershell_playback`
(PATH via ``shutil.which`` — WSL interop or native Windows), spawns it with
the same ``-NoLogo -NoProfile -NonInteractive -Command`` pattern (NEVER
``-EncodedCommand``: on real WSL interop the Windows security stack silently
kills those processes), and runs ONE inline script that records the default
input through the classic MCI waveaudio device (``winmm.dll``
``mciSendString`` P/Invoke via ``Add-Type``):

    open -> set 16-bit/mono/rate -> record -> bounded sleep -> stop ->
    save to a host temp file -> base64 on stdout -> temp removed.

The transport back to the engine is stdout base64; the temp WAV never
leaves the host.

HONEST LIMITS (v1) — read before trusting ended_by:

- There is NO live stop. MCI exposes no streaming level meter while
  recording, so the script always records the FULL ``max_seconds`` window
  and endpointing happens post-hoc in Python. Consequences:
  ``CaptureResult.ended_by`` is always ``"max_duration"`` (the recorder
  stopped because the window ended, never because silence was heard live);
  ``stats["trimmed"]`` / ``stats["trailing_silence_sec"]`` say whether the
  tail was cut afterwards; ``stats["evidence_ended_by"]`` is what the pure
  endpointer concluded about the recorded audio. Wall-clock cost is the
  full window even for two words.
- Only the Windows DEFAULT input device is supported (``device="default"``).
  MCI has no reliable per-name capture-device picker, so any other value is
  a typed ``InvalidConfigError`` refusal, never a silent ignore.
- 16-bit mono PCM only — what MCI is told to produce and what
  faster-whisper wants.
- The MCI script follows documented winmm behavior but has NOT been
  smoke-tested on real Windows hardware yet; engine-side tests use fake
  runners exclusively.
"""

from __future__ import annotations

import base64
import os
import binascii
import io
import shutil
import struct
import subprocess
import wave
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from agent_tts.stt.transcriber import InvalidConfigError, SttError

# Analysis frame for per-frame RMS endpointing (30ms is the usual VAD grain).
FRAME_MS = 30

# Default silence gate for 16-bit PCM: quiet-room noise floors sit well
# below 300 RMS while speech energy is typically >1000, so 300 splits them.
DEFAULT_SILENCE_THRESHOLD_RMS = 300.0

# Absolute bound on any WAV handed to trim_trailing_silence; checked BEFORE
# parsing so absurd inputs never allocate parse structures.
MAX_WAV_BYTES = 64 * 1024 * 1024

# Extra wall-clock budget over the recording window for powershell spawn,
# Add-Type compilation and the base64 transfer, before the runner gives up.
PS_SPAWN_SLACK_SEC = 20.0


class CaptureUnavailableError(SttError):
    """powershell.exe is missing, failed, timed out, or returned garbage."""

    kind = "capture_unavailable"
    exit_code = 8


# WSL interop frequently ships powershell.exe OFF the Linux PATH (PATH append
# disabled); these are the standard install locations (2026-10-08 U4).
_WELL_KNOWN_POWERSHELL_PATHS = (
    "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
    "/mnt/c/Windows/Sysnative/WindowsPowerShell/v1.0/powershell.exe",
)


class CaptureOversizeError(CaptureUnavailableError):
    """Runner output exceeds the byte ceiling implied by the config."""

    kind = "capture_oversize"


class EmptyCaptureError(SttError):
    """The recording contains no speech above the silence threshold."""

    kind = "empty_capture"
    exit_code = 9


@dataclass(frozen=True)
class CaptureConfig:
    """Recording + endpointing parameters (v1: 16-bit mono, default device)."""

    sample_rate: int = 16000
    channels: int = 1
    bits: int = 16
    silence_threshold_rms: float = DEFAULT_SILENCE_THRESHOLD_RMS
    silence_seconds: float = 1.2
    max_seconds: float = 30.0
    device: str = "default"

    def __post_init__(self) -> None:
        if not isinstance(self.sample_rate, int) or not 8000 <= self.sample_rate <= 48000:
            raise InvalidConfigError(
                f"capture sample_rate must be an int in [8000, 48000]; "
                f"got {self.sample_rate!r}"
            )
        if self.channels != 1:
            raise InvalidConfigError(
                "capture supports exactly 1 channel (mono) in v1; "
                f"got {self.channels!r}"
            )
        if self.bits != 16:
            raise InvalidConfigError(
                f"capture supports exactly 16-bit PCM in v1; got {self.bits!r}"
            )
        if not 1.0 <= self.silence_threshold_rms <= 32768.0:
            raise InvalidConfigError(
                "capture silence_threshold_rms must be within [1.0, 32768.0] "
                f"for 16-bit PCM; got {self.silence_threshold_rms!r}"
            )
        if not 0.1 <= self.silence_seconds <= 30.0:
            raise InvalidConfigError(
                f"capture silence_seconds must be in [0.1, 30.0]; "
                f"got {self.silence_seconds!r}"
            )
        if not 0.5 <= self.max_seconds <= 300.0:
            raise InvalidConfigError(
                f"capture max_seconds must be in [0.5, 300.0]; "
                f"got {self.max_seconds!r}"
            )
        if self.silence_seconds >= self.max_seconds:
            raise InvalidConfigError(
                f"capture silence_seconds ({self.silence_seconds}) must be "
                f"strictly below max_seconds ({self.max_seconds}), otherwise "
                "an utterance can never be detected inside the window"
            )
        if self.device != "default":
            raise InvalidConfigError(
                f"only the default capture device is supported in v1 "
                f"(MCI backend has no per-name picker); got {self.device!r}"
            )


@dataclass(frozen=True)
class PcmAnalysis:
    """Deterministic endpointing verdict over raw PCM frames."""

    sample_rate: int
    frame_ms: int
    frames: int
    speech_frames: int
    last_speech_frame: int
    speech_end_sec: float
    trailing_silence_sec: float
    duration_sec: float
    ended_by: str
    threshold_rms: float


@dataclass(frozen=True)
class CaptureResult:
    """Outcome of one capture run. ``ended_by`` is the recorder truth (v1:
    always "max_duration"); the endpointing evidence lives in ``stats``."""

    wav_bytes: bytes
    duration: float
    ended_by: str
    stats: dict


def _analyze(
    pcm: bytes,
    sample_rate: int,
    channels: int,
    threshold: float,
    silence_seconds: float,
    frame_ms: int,
) -> PcmAnalysis:
    """Per-frame RMS endpointing core. Frames are analyzed only when FULL:
    a trailing partial frame (or sub-frame input) is ignored, so the
    resolution of every verdict is exactly frame_ms."""
    bytes_per_sample = 2  # 16-bit; enforced by callers
    frame_samples = max(1, sample_rate * channels * frame_ms // 1000)
    byte_rate = sample_rate * channels * bytes_per_sample
    duration_sec = len(pcm) / float(byte_rate) if byte_rate else 0.0
    n_samples = len(pcm) // bytes_per_sample
    frames = n_samples // frame_samples
    samples = struct.unpack_from("<%dh" % n_samples, pcm) if n_samples else ()
    speech_frames = 0
    last_speech = -1
    for i in range(frames):
        chunk = samples[i * frame_samples : (i + 1) * frame_samples]
        acc = 0
        for s in chunk:
            acc += s * s
        if (acc / frame_samples) ** 0.5 > threshold:
            speech_frames += 1
            last_speech = i
    frame_sec = frame_ms / 1000.0
    trailing_silence_sec = (frames - (last_speech + 1)) * frame_sec
    if last_speech >= 0:
        speech_end_sec = (last_speech + 1) * frame_sec
    else:
        speech_end_sec = 0.0
    ended_by = (
        "silence" if trailing_silence_sec >= silence_seconds else "max_duration"
    )
    return PcmAnalysis(
        sample_rate=sample_rate,
        frame_ms=frame_ms,
        frames=frames,
        speech_frames=speech_frames,
        last_speech_frame=last_speech,
        speech_end_sec=speech_end_sec,
        trailing_silence_sec=trailing_silence_sec,
        duration_sec=duration_sec,
        ended_by=ended_by,
        threshold_rms=threshold,
    )


def analyze_pcm(pcm_bytes: bytes, config: CaptureConfig, frame_ms: int = FRAME_MS):
    """Pure, deterministic endpointing over raw 16-bit PCM (per config)."""
    return _analyze(
        bytes(pcm_bytes),
        config.sample_rate,
        config.channels,
        config.silence_threshold_rms,
        config.silence_seconds,
        frame_ms,
    )


def _parse_wav(wav_bytes: bytes):
    """Validates RIFF structure and returns (pcm, channels, rate). Stdlib only;
    absurd sizes are rejected BEFORE any parsing allocation."""
    if len(wav_bytes) > MAX_WAV_BYTES:
        raise SttError(
            f"WAV input of {len(wav_bytes)} bytes is absurdly large "
            f"(cap {MAX_WAV_BYTES}); refusing to parse"
        )
    if (
        len(wav_bytes) < 44
        or wav_bytes[:4] != b"RIFF"
        or wav_bytes[8:12] != b"WAVE"
    ):
        raise SttError("input is not a WAV container (missing RIFF/WAVE header)")
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            channels = w.getnchannels()
            width = w.getsampwidth()
            rate = w.getframerate()
            pcm = w.readframes(w.getnframes())
    except wave.Error as exc:
        raise SttError(f"invalid WAV input: {exc}") from exc
    if width not in (1, 2):
        raise SttError(
            f"only 8-bit and 16-bit PCM WAV are supported for endpointing "
            f"(got {width * 8}-bit)"
        )
    if width == 1:
        # unsigned 8-bit -> signed 16-bit little-endian (driver-default
        # fallback recordings can be 8-bit — 2026-10-08 U4)
        pcm = b"".join(
            ((int(b) - 128) * 257).to_bytes(2, "little", signed=True)
            for b in pcm
        )
    if channels < 1 or rate < 1:
        raise SttError("invalid WAV parameters (channels/rate)")
    return pcm, channels, rate


def trim_trailing_silence(
    wav_bytes: bytes, config: CaptureConfig
) -> Tuple[bytes, dict]:
    """Trims trailing silence from a 16-bit PCM WAV per the config thresholds.

    Returns ``(trimmed_wav, stats)``. All-silence (or sub-frame) input is a
    typed ``EmptyCaptureError`` — nothing was said. Stats carry the full
    story: raw_duration_sec, kept_sec, trailing_silence_sec, trimmed,
    frames, speech_frames, evidence_ended_by, threshold_rms.
    """
    pcm, channels, rate = _parse_wav(bytes(wav_bytes))
    analysis = _analyze(
        pcm,
        rate,
        channels,
        config.silence_threshold_rms,
        config.silence_seconds,
        FRAME_MS,
    )
    if analysis.speech_frames == 0:
        raise EmptyCaptureError(
            f"capture contains only silence (no {FRAME_MS}ms frame above RMS "
            f"{config.silence_threshold_rms:.0f}); nothing was said"
        )
    if analysis.trailing_silence_sec >= config.silence_seconds:
        keep_frames = analysis.last_speech_frame + 1
        keep_bytes = keep_frames * FRAME_MS * rate * channels * 2 // 1000
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(channels)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm[:keep_bytes])
        trimmed_wav = buf.getvalue()
        trimmed = True
        kept_sec = keep_frames * FRAME_MS / 1000.0
    else:
        trimmed_wav = bytes(wav_bytes)
        trimmed = False
        kept_sec = analysis.duration_sec
    stats = {
        "raw_duration_sec": analysis.duration_sec,
        "kept_sec": kept_sec,
        "trailing_silence_sec": analysis.trailing_silence_sec,
        "trimmed": trimmed,
        "frames": analysis.frames,
        "speech_frames": analysis.speech_frames,
        "evidence_ended_by": analysis.ended_by,
        "threshold_rms": analysis.threshold_rms,
    }
    return trimmed_wav, stats


def _stderr_tail(stderr: str) -> str:
    """Last human-readable stderr line, skipping the CLIXML wrapper noise
    Windows PowerShell 5.1 adds to redirected stderr (playback pattern)."""
    lines = (stderr or "").strip().splitlines()
    readable = [
        line
        for line in lines
        if not line.startswith(("#<", "<Objs", "</Objs"))
    ]
    tail = readable[-1] if readable else (lines[-1] if lines else "")
    return f": {tail}" if tail else ""


class PowerShellCapture:
    """Microphone capture through one bounded powershell.exe invocation."""

    @staticmethod
    def script(config: CaptureConfig) -> str:
        """Inline MCI recording script: one line, no double quotes, no
        newlines (same argv-safety contract as powershell_playback). The
        recording window is a bounded Start-Sleep; there is no live stop."""
        max_ms = int(config.max_seconds * 1000)
        parts = [
            "$q=[char]34",
            "$sb=New-Object System.Text.StringBuilder 512",
            "$src='using System;using System.Text;"
            "using System.Runtime.InteropServices;"
            "public class AtTtsMci{[DllImport('+$q+'winmm.dll'+$q+"
            "',CharSet=CharSet.Unicode)]public static extern int "
            "mciSendString(string c,StringBuilder b,int l,IntPtr h);"
            "[DllImport('+$q+'winmm.dll'+$q+',CharSet=CharSet.Unicode)]"
            "public static extern bool mciGetErrorString(int e,StringBuilder b,"
            "int l);}'",
            "Add-Type -TypeDefinition $src",
            "function Mc([string]$c){$r=[AtTtsMci]::mciSendString("
            "$c,$sb,512,[IntPtr]::Zero);"
            "if($r -ne 0){$t='';"
            "if(-not [AtTtsMci]::mciGetErrorString($r,$sb,512)){$t=''}"
            "else{$t=$sb.ToString()};"
            "throw ('at-tts capture failed rc='+$r+' cmd='+$c+' err='+$t)}}",
            "try{",
            "Mc 'open new type waveaudio alias atrec'",
            "Mc 'set atrec time format ms'",
            # Format set is BEST-EFFORT: several real drivers accept the set
            # but then refuse to record in that exact format (rc=322,
            # 2026-10-08 U4). If it fails we just record in the driver's
            # default format; faster-whisper decodes any WAV.
            "try{Mc 'set atrec bitspersample {bits} channels {ch} "
            "samplespersec {rate}}'}catch{{}}",
            # If the exact-format record fails, reopen and record with the
            # driver default (mapper picks a workable format).
            "try{Mc 'record atrec'}catch{"
            "Mc 'close atrec';"
            "Mc 'open new type waveaudio alias atrec';"
            "Mc 'record atrec'}",
            f"Start-Sleep -Milliseconds {max_ms}",
            "Mc 'stop atrec'",
            "$t=[System.IO.Path]::GetTempFileName()",
            "Mc ('save atrec '+$q+$t+$q)",
            "Mc 'close atrec'",
            "$b=[System.IO.File]::ReadAllBytes($t)",
            "Remove-Item -Force $t",
            "[Console]::Out.Write([Convert]::ToBase64String($b))",
            "exit 0",
            "}catch{",
            "[AtTtsMci]::mciSendString('close atrec',$sb,512,[IntPtr]::Zero)",
            "[Console]::Error.Write($_.Exception.Message)",
            "exit 3}",
        ]
        script = ";".join(parts).replace(
            "{bits}", str(config.bits)
        ).replace("{ch}", str(config.channels)).replace(
            "{rate}", str(config.sample_rate)
        ).replace("{{}}", "{}")
        # Same hard invariant as powershell_playback: double quotes and
        # newlines both break the argv -> Windows command-line handoff.
        assert '"' not in script and "\n" not in script
        return script

    @staticmethod
    def argv_and_timeout(config: CaptureConfig) -> Tuple[List[str], float]:
        argv = [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            PowerShellCapture.script(config),
        ]
        return argv, config.max_seconds + PS_SPAWN_SLACK_SEC

    @staticmethod
    def _decode_runner_output(stdout, config: CaptureConfig) -> bytes:
        """stdout base64 -> WAV bytes, with size ceilings enforced BEFORE
        the base64 decode allocation."""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        text = (stdout or "").strip()
        ceiling = (
            int(
                config.sample_rate
                * config.channels
                * (config.bits // 8)
                * (config.max_seconds + 1.0)
            )
            + 4096
        )
        b64_ceiling = ((ceiling + 2) // 3) * 4 + 1024
        if len(text) > b64_ceiling:
            raise CaptureOversizeError(
                f"capture output of {len(text)} base64 chars exceeds the "
                f"{b64_ceiling}-char ceiling for a {config.max_seconds}s "
                f"window at {config.sample_rate}Hz; refusing to decode"
            )
        try:
            wav = base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise CaptureUnavailableError(
                f"capture output is not valid base64: {exc}"
            ) from exc
        if len(wav) > ceiling:
            raise CaptureOversizeError(
                f"decoded capture is {len(wav)} bytes, above the "
                f"{ceiling}-byte ceiling for this config"
            )
        if not wav:
            raise CaptureUnavailableError("capture produced no output")
        return wav

    @staticmethod
    def _locate_powershell() -> str:
        """Finds powershell.exe: PATH first, then well-known WSL interop
        paths — interop often exists with PATH append off (2026-10-08 U4)."""
        found = shutil.which("powershell.exe")
        if found:
            return found
        for candidate in _WELL_KNOWN_POWERSHELL_PATHS:
            if os.path.isfile(candidate):
                return candidate
        raise CaptureUnavailableError(
            "powershell.exe was not found on PATH or at the standard WSL "
            "interop locations (/mnt/c/Windows/...); microphone capture "
            "runs on the Windows host through PowerShell"
        )

    @staticmethod
    def _default_runner(argv: List[str], timeout: float) -> str:
        """Real runner: spawn the resolved powershell.exe, bounded."""
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired as exc:
            raise CaptureUnavailableError(
                f"microphone capture timed out after {timeout:.0f}s "
                f"(recording window plus process overhead)"
            ) from exc
        except OSError as exc:
            raise CaptureUnavailableError(
                f"could not launch powershell.exe: {exc}"
            ) from exc
        if proc.returncode != 0:
            raise CaptureUnavailableError(
                f"PowerShell capture failed (exit code {proc.returncode})"
                f"{_stderr_tail(proc.stderr)}"
            )
        return proc.stdout

    def run(
        self,
        config: CaptureConfig,
        runner: Optional[Callable[[List[str], float], str]] = None,
    ) -> CaptureResult:
        """Records, trims, and reports. ``runner`` is the injectable seam
        (tests pass fakes; the default runs the real powershell.exe)."""
        argv, timeout = self.argv_and_timeout(config)
        # Resolve powershell.exe BEFORE dispatching to any runner (PATH or
        # WSL well-known paths — 2026-10-08 U4), so a missing interpreter is
        # a typed error even with a custom runner injected.
        argv[0] = self._locate_powershell()
        runner = runner or self._default_runner
        try:
            stdout = runner(argv, timeout)
        except SttError:
            raise
        except Exception as exc:  # noqa: BLE001 — any runner failure is typed
            raise CaptureUnavailableError(f"microphone capture failed: {exc}") from exc
        wav = self._decode_runner_output(stdout, config)
        try:
            trimmed, stats = trim_trailing_silence(wav, config)
        except EmptyCaptureError:
            raise
        except SttError as exc:
            raise CaptureUnavailableError(
                f"capture output is unusable: {exc}"
            ) from exc
        # v1 HONESTY: the MCI recorder has no live stop, so the recording
        # always ran the full window. ended_by says exactly that; the trim
        # story (evidence, trailing silence) lives in stats.
        return CaptureResult(
            wav_bytes=trimmed,
            duration=stats["kept_sec"],
            ended_by="max_duration",
            stats=dict(stats),
        )
