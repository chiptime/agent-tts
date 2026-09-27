"""Local Zero-Cloud Neural TTS provider using Piper (100% offline, CPU-only)."""

import asyncio
import os
import shutil
import struct
import subprocess
import sys
from typing import Callable, IO, Iterator, List, Optional

from agent_tts.boundaries import BoundaryMap, SynthesisResult, estimate_boundaries_from_text
from agent_tts.providers.base import TTSProvider, parse_rate_to_multiplier
from agent_tts.text import split_sentence_groups


def estimate_piper_duration(text: str, speed: float) -> float:
    """Char-based duration estimate for piper output at the given speed multiplier."""
    return max(1.0, len(text) / (14.0 * speed))


def _print_missing_binary_error() -> None:
    """Prints the install-help error shared by the single-shot and streaming paths."""
    print(
        "Error: Piper offline TTS is not installed. To use offline local synthesis, install piper:\n"
        "  pip install piper-tts\n"
        "or download the standalone binary from https://github.com/rhasspy/piper/releases",
        file=sys.stderr,
    )


def _read_exact(stdout: IO[bytes], size: int) -> Optional[bytes]:
    """Reads exactly size bytes from the pipe; None when the stream ends early."""
    buf = bytearray()
    while len(buf) < size:
        piece = stdout.read(size - len(buf))
        if not piece:
            return None
        buf.extend(piece)
    return bytes(buf)


def _read_stream_wav(stdout: IO[bytes]) -> Optional[bytes]:
    """Reads one complete WAV from the pipe by walking its RIFF chunk layout.

    Returns None on a clean end of stream (between two WAVs) and raises
    ValueError when the bytes stop mid-container or break the RIFF layout.
    No fixed header size is assumed: chunks are walked until ``data`` and the
    data chunk's own size is honored.
    """
    header = _read_exact(stdout, 12)
    if header is None:
        return None
    riff, riff_size, wave = struct.unpack("<4sI4s", header)
    if riff != b"RIFF" or wave != b"WAVE":
        raise ValueError("piper stream did not return a RIFF/WAVE payload")
    parts = [header]
    consumed = 12
    while True:
        chunk_header = _read_exact(stdout, 8)
        if chunk_header is None:
            raise ValueError("piper stream ended inside a WAV container")
        chunk_id, chunk_size = struct.unpack("<4sI", chunk_header)
        payload = _read_exact(stdout, chunk_size)
        if payload is None:
            raise ValueError("piper stream ended inside a WAV container")
        parts.append(chunk_header)
        parts.append(payload)
        consumed += 8 + chunk_size
        if chunk_size % 2:
            pad = _read_exact(stdout, 1)
            if pad is None:
                raise ValueError("piper stream ended inside a WAV container")
            parts.append(pad)
            consumed += 1
        if chunk_id == b"data":
            break
    # Drain infrequently written trailing chunks (LIST/INFO) so the next WAV
    # starts at a clean container boundary.
    total = 8 + riff_size
    if total > consumed:
        trailing = _read_exact(stdout, total - consumed)
        if trailing is None:
            raise ValueError("piper stream ended inside a WAV container")
        parts.append(trailing)
    return b"".join(parts)


class PiperTTSProvider(TTSProvider):
    """Offline local neural TTS provider using Piper ONNX models."""

    name: str = "piper"
    supports_stream: bool = True
    # Piper answers one complete WAV per stdin line, and the line per group
    # comes from the shared splitter: chunks map to groups by index.
    stream_yields_group_chunks: bool = True

    def __init__(
        self,
        model_path: Optional[str] = None,
        binary_path: Optional[str] = None,
    ):
        self.model_path = model_path or os.environ.get("PIPER_MODEL", "")
        self.binary_path = binary_path or self._find_piper_binary()

    def _find_piper_binary(self) -> Optional[str]:
        """Locates the piper executable: AGENT_TTS_PIPER_BIN override, then PATH.

        Host-agnostic resolution: (a) the AGENT_TTS_PIPER_BIN env var when it
        points at an existing file, (b) ``shutil.which("piper")``. A set but
        missing env value falls through to PATH instead of failing hard.
        """
        env_bin = os.environ.get("AGENT_TTS_PIPER_BIN", "")
        if env_bin and os.path.isfile(env_bin):
            return env_bin
        return shutil.which("piper")

    def is_available(self) -> bool:
        """Checks whether Piper binary is installed and executable."""
        if not self.binary_path:
            return False
        return os.path.isfile(self.binary_path) and os.access(self.binary_path, os.X_OK)

    def _resolve_model(self, voice: str) -> str:
        """Resolves the ONNX model path from the configured model or voice name."""
        model = self.model_path or voice
        if not model.endswith(".onnx") and os.path.exists(f"{model}.onnx"):
            model = f"{model}.onnx"
        return model

    def _build_command(self, model: str, speed: float) -> List[str]:
        """Builds the piper command shared by the single-shot and streaming paths."""
        # Piper parameter for length_scale is inverse of speed (1.0 / speed)
        length_scale = f"{1.0 / speed:.2f}"
        cmd = [
            self.binary_path,
            "--output_file", "-",
            "--length_scale", length_scale,
        ]
        if os.path.exists(model):
            cmd.extend(["--model", model])
        return cmd

    async def synthesize(
        self,
        text: str,
        voice: str = "es_ES-davefx-medium",
        rate: str = "+0%",
        volume: str = "+0%",
        pitch: str = "+0Hz",
        stop_checker: Optional[Callable[[], bool]] = None,
    ) -> SynthesisResult:
        """Synthesizes text into audio bytes (WAV/PCM) via local Piper process."""
        if stop_checker and stop_checker():
            return SynthesisResult(b"", BoundaryMap())

        if not self.is_available():
            _print_missing_binary_error()
            return SynthesisResult(b"", BoundaryMap())

        # Resolve model path
        model = self._resolve_model(voice)

        speed = parse_rate_to_multiplier(rate)
        cmd = self._build_command(model, speed)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )

            stdout_data, _ = await proc.communicate(input=text.encode("utf-8"))

            if stop_checker and stop_checker():
                return SynthesisResult(b"", BoundaryMap())

            if not stdout_data or proc.returncode != 0:
                return SynthesisResult(b"", BoundaryMap())

            # Estimate boundaries from audio duration
            # WAV header: sample rate at offset 24, byte rate at offset 28
            est_dur = estimate_piper_duration(text, speed)
            boundaries = estimate_boundaries_from_text(text, est_dur)
            return SynthesisResult(stdout_data, boundaries)
        except Exception as e:
            print(f"Piper execution error: {e}", file=sys.stderr)
            return SynthesisResult(b"", BoundaryMap())

    def synthesize_stream(
        self,
        text: str,
        voice: str,
        rate: str = "+0%",
        volume: str = "+0%",
        pitch: str = "+0Hz",
        stop_checker: Optional[Callable[[], bool]] = None,
    ) -> Iterator[bytes]:
        """Yields one complete WAV per sentence group from a single persistent piper process.

        Piper reads one text line from stdin per synthesis and answers with one
        complete WAV on stdout, so keeping the process alive across groups turns
        N model loads into one. Each WAV is reassembled by walking its RIFF
        chunks up to ``data`` and reading exactly the data chunk's size.
        """
        groups = split_sentence_groups(text)
        if not groups:
            return

        if not self.is_available():
            _print_missing_binary_error()
            return

        model = self._resolve_model(voice)
        speed = parse_rate_to_multiplier(rate)
        cmd = self._build_command(model, speed)

        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            for group in groups:
                if stop_checker and stop_checker():
                    proc.terminate()
                    return
                proc.stdin.write(group.encode("utf-8") + b"\n")
                proc.stdin.flush()
                wav = _read_stream_wav(proc.stdout)
                if wav is None:
                    return
                yield wav
        finally:
            if proc is not None:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
                try:
                    proc.wait(timeout=10.0)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    proc.wait()
