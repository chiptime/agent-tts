"""Microsoft Edge Neural TTS provider (zero-config, high quality, 100% free)."""

from typing import Callable, Optional
import edge_tts

from agent_tts.constants import DEFAULT_VOICE, VOICE_MAP
from agent_tts.providers.base import TTSProvider


class EdgeTTSProvider(TTSProvider):
    """Microsoft Edge Neural TTS."""

    name = "edge"

    def resolve_voice(self, voice: str) -> str:
        v = (voice or "").lower().strip()
        return VOICE_MAP.get(v, voice if voice else DEFAULT_VOICE)

    async def synthesize(
        self,
        text: str,
        voice: str,
        rate: str,
        volume: str = "+0%",
        pitch: str = "+0Hz",
        stop_checker: Optional[Callable[[], bool]] = None,
    ) -> bytes:
        resolved = self.resolve_voice(voice)
        communicate = edge_tts.Communicate(
            text=text,
            voice=resolved,
            rate=rate,
            volume=volume,
            pitch=pitch,
        )
        mp3_chunks = []
        async for chunk in communicate.stream():
            if stop_checker and stop_checker():
                return b""
            if chunk["type"] == "audio":
                mp3_chunks.append(chunk["data"])
        return b"".join(mp3_chunks)
