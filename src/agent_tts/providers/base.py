"""Base interface for TTS synthesis providers."""

from typing import Callable, Optional


def parse_rate_to_multiplier(rate_str: str) -> float:
    """Converts rate strings like '+20%', '-10%', '1.2' to float multiplier (e.g. 1.2)."""
    if not rate_str:
        return 1.0
    s = str(rate_str).strip()
    if s.endswith("%"):
        try:
            val = float(s[:-1])
            return max(0.25, min(4.0, 1.0 + (val / 100.0)))
        except ValueError:
            return 1.0
    try:
        val = float(s)
        return max(0.25, min(4.0, val))
    except ValueError:
        return 1.0


class TTSProvider:
    """Base interface for TTS synthesis backends."""

    name: str = "base"

    async def synthesize(
        self,
        text: str,
        voice: str,
        rate: str,
        volume: str = "+0%",
        pitch: str = "+0Hz",
        stop_checker: Optional[Callable[[], bool]] = None,
    ) -> bytes:
        """Synthesizes text into raw MP3 bytes."""
        raise NotImplementedError
