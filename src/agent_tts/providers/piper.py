"""Local Zero-Cloud Neural TTS provider using Piper (100% offline, CPU-only)."""

import asyncio
import os
import shutil
import sys
from typing import Callable, Optional

from agent_tts.boundaries import BoundaryMap, SynthesisResult, estimate_boundaries_from_text
from agent_tts.providers.base import TTSProvider, parse_rate_to_multiplier


class PiperTTSProvider(TTSProvider):
    """Offline local neural TTS provider using Piper ONNX models."""

    name: str = "piper"

    def __init__(
        self,
        model_path: Optional[str] = None,
        binary_path: Optional[str] = None,
    ):
        self.model_path = model_path or os.environ.get("PIPER_MODEL", "")
        self.binary_path = binary_path or self._find_piper_binary()

    def _find_piper_binary(self) -> Optional[str]:
        """Locates the piper executable in PATH or standard user directories."""
        found = shutil.which("piper")
        if found:
            return found

        candidates = [
            os.path.expanduser("~/.local/bin/piper"),
            os.path.expanduser("~/.local/share/herdr-tts/venv/bin/piper"),
            os.path.expanduser("/usr/local/bin/piper"),
        ]
        for c in candidates:
            if os.path.isfile(c) and os.access(c, os.X_OK):
                return c
        return None

    def is_available(self) -> bool:
        """Checks whether Piper binary is installed and executable."""
        if not self.binary_path:
            return False
        return os.path.isfile(self.binary_path) and os.access(self.binary_path, os.X_OK)

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
            print(
                "Error: Piper offline TTS is not installed. To use offline local synthesis, install piper:\n"
                "  pip install piper-tts\n"
                "or download the standalone binary from https://github.com/rhasspy/piper/releases",
                file=sys.stderr,
            )
            return SynthesisResult(b"", BoundaryMap())

        # Resolve model path
        model = self.model_path or voice
        if not model.endswith(".onnx") and os.path.exists(f"{model}.onnx"):
            model = f"{model}.onnx"

        speed = parse_rate_to_multiplier(rate)
        # Piper parameter for length_scale is inverse of speed (1.0 / speed)
        length_scale = f"{1.0 / speed:.2f}"

        cmd = [
            self.binary_path,
            "--output_file", "-",
            "--length_scale", length_scale,
        ]
        if os.path.exists(model):
            cmd.extend(["--model", model])

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
            est_dur = max(1.0, len(text) / (14.0 * speed))
            boundaries = estimate_boundaries_from_text(text, est_dur)
            return SynthesisResult(stdout_data, boundaries)
        except Exception as e:
            print(f"Piper execution error: {e}", file=sys.stderr)
            return SynthesisResult(b"", BoundaryMap())
