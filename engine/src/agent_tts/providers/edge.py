import asyncio
import queue
import re
import sys
import threading
from typing import Callable, Iterator, List, Optional
import edge_tts

from agent_tts.boundaries import (
    BoundaryMap,
    Sentence,
    SynthesisResult,
    Word,
    build_boundaries_from_word_events,
)
from agent_tts.constants import DEFAULT_VOICE, VOICE_MAP
from agent_tts.providers.base import TTSProvider


class EdgeTTSProvider(TTSProvider):
    """Microsoft Edge Neural TTS."""

    name = "edge"
    supports_stream = True
    stream_yields_group_chunks = False

    def resolve_voice(self, voice: str) -> str:
        v = (voice or "").lower().strip()
        return VOICE_MAP.get(v, voice if voice else DEFAULT_VOICE)

    def synthesize_stream(
        self,
        text: str,
        voice: str,
        rate: str = "+0%",
        volume: str = "+0%",
        pitch: str = "+0Hz",
        stop_checker: Optional[Callable[[], bool]] = None,
        on_event: Optional[Callable[[dict], None]] = None,
    ) -> Iterator[bytes]:
        """Yields raw MP3 chunks incrementally while the Edge TTS WebSocket is streaming."""
        resolved = self.resolve_voice(voice)
        q: queue.Queue = queue.Queue(maxsize=100)

        async def _stream_runner():
            try:
                communicate = edge_tts.Communicate(
                    text=text,
                    voice=resolved,
                    rate=rate,
                    volume=volume,
                    pitch=pitch,
                    boundary="WordBoundary",
                )
                async for chunk in communicate.stream():
                    if stop_checker and stop_checker():
                        break
                    if chunk["type"] == "audio":
                        q.put(chunk["data"])
                    elif chunk["type"] == "WordBoundary" and on_event:
                        try:
                            on_event(chunk)
                        except Exception:
                            pass
            except Exception as e:
                print(f"Edge TTS stream error: {e}", file=sys.stderr)
            finally:
                q.put(None)

        def _thread_target():
            asyncio.run(_stream_runner())

        thread = threading.Thread(target=_thread_target, daemon=True)
        thread.start()

        while True:
            item = q.get()
            if item is None:
                break
            yield item

    async def synthesize(
        self,
        text: str,
        voice: str,
        rate: str,
        volume: str = "+0%",
        pitch: str = "+0Hz",
        stop_checker: Optional[Callable[[], bool]] = None,
    ) -> SynthesisResult:
        resolved = self.resolve_voice(voice)
        communicate = edge_tts.Communicate(
            text=text,
            voice=resolved,
            rate=rate,
            volume=volume,
            pitch=pitch,
            boundary="WordBoundary",
        )
        mp3_chunks = []
        raw_words = []

        async for chunk in communicate.stream():
            if stop_checker and stop_checker():
                return SynthesisResult(b"", BoundaryMap())
            if chunk["type"] == "audio":
                mp3_chunks.append(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                raw_words.append(chunk)

        mp3_data = b"".join(mp3_chunks)
        if not mp3_data:
            return SynthesisResult(b"", BoundaryMap())

        boundary_map = build_boundaries_from_word_events(text, raw_words)
        return SynthesisResult(mp3_data, boundary_map)
