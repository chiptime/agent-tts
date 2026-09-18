"""OpenAI Audio TTS provider (tts-1 / tts-1-hd)."""

import asyncio
import json
import os
import sys
from typing import Callable, Iterator, List, Optional
import urllib.error
import urllib.request

from agent_tts.boundaries import BoundaryMap, SynthesisResult, estimate_boundaries_from_text
from agent_tts.providers.base import TTSProvider, parse_rate_to_multiplier


class OpenAITTSProvider(TTSProvider):
    """OpenAI Audio TTS provider."""

    name = "openai"
    supports_stream = True

    STREAM_CHUNK_SIZE = 8192

    VOICE_FALLBACK = {
        "elvira": "nova",
        "ximena": "nova",
        "dalia": "nova",
        "alvaro": "onyx",
        "álvaro": "onyx",
        "jorge": "onyx",
        "en": "alloy",
    }
    VALID_VOICES = {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1",
        model: str = "tts-1",
    ):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.model = model or os.environ.get("OPENAI_TTS_MODEL", "tts-1")

    def resolve_voice(self, voice: str) -> str:
        v = (voice or "").lower().strip()
        if v in self.VALID_VOICES:
            return v
        return self.VOICE_FALLBACK.get(v, "nova")

    def _sync_request(self, text: str, voice: str, speed: float) -> bytes:
        if not self.api_key:
            print("Error: OpenAI TTS requires an API key (set OPENAI_API_KEY or --openai-key)", file=sys.stderr)
            return b""
        url = f"{self.base_url}/audio/speech"
        payload = json.dumps({
            "model": self.model,
            "input": text,
            "voice": voice,
            "response_format": "mp3",
            "speed": speed,
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "agent-tts/0.1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            print(f"OpenAI TTS API error ({e.code}): {err_body}", file=sys.stderr)
            return b""
        except Exception as e:
            print(f"OpenAI TTS network error: {e}", file=sys.stderr)
            return b""

    def synthesize_stream(
        self,
        text: str,
        voice: str,
        rate: str = "+0%",
        volume: str = "+0%",
        pitch: str = "+0Hz",
        stop_checker: Optional[Callable[[], bool]] = None,
    ) -> Iterator[bytes]:
        """Yields MP3 chunks incrementally from the OpenAI TTS streaming response."""
        if not self.api_key:
            raise RuntimeError("OpenAI TTS requires an API key (set OPENAI_API_KEY or --openai-key)")
        return self._stream_chunks(text, self.resolve_voice(voice), parse_rate_to_multiplier(rate), stop_checker)

    def _stream_chunks(self, text: str, voice: str, speed: float, stop_checker: Optional[Callable[[], bool]] = None) -> Iterator[bytes]:
        """Yields MP3 chunks as the chunked HTTP response arrives."""
        url = f"{self.base_url}/audio/speech"
        payload = json.dumps({
            "model": self.model,
            "input": text,
            "voice": voice,
            "response_format": "mp3",
            "speed": speed,
            "stream": True,
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
                "User-Agent": "agent-tts/0.1.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            while True:
                if stop_checker and stop_checker():
                    return
                chunk = resp.read(self.STREAM_CHUNK_SIZE)
                if not chunk:
                    return
                yield chunk

    def _sync_streamed(self, text: str, voice: str, speed: float, stop_checker: Optional[Callable[[], bool]] = None) -> Optional[bytes]:
        """Returns MP3 bytes from the streaming endpoint.

        Returns None when streaming could not deliver audio (missing key, network or
        API failure mid-stream) so the caller can fall back to the full-response
        request; returns b"" when synthesis was stopped and partial audio must be
        discarded.
        """
        if not self.api_key:
            return None
        try:
            chunks: List[bytes] = []
            for chunk in self._stream_chunks(text, voice, speed, stop_checker):
                chunks.append(chunk)
        except Exception as e:
            print(f"OpenAI TTS stream failed ({e}); falling back to full-response synthesis", file=sys.stderr)
            return None
        if stop_checker and stop_checker():
            return b""
        data = b"".join(chunks)
        return data if data else None

    def _synthesize_sync(self, text: str, voice: str, speed: float, stop_checker: Optional[Callable[[], bool]] = None) -> bytes:
        """Streams MP3 chunks when possible; falls back to the full-response request otherwise."""
        streamed = self._sync_streamed(text, voice, speed, stop_checker)
        if streamed is not None:
            return streamed
        return self._sync_request(text, voice, speed)

    async def synthesize(
        self,
        text: str,
        voice: str,
        rate: str,
        volume: str = "+0%",
        pitch: str = "+0Hz",
        stop_checker: Optional[Callable[[], bool]] = None,
    ) -> SynthesisResult:
        if stop_checker and stop_checker():
            return SynthesisResult(b"", BoundaryMap())
        target_voice = self.resolve_voice(voice)
        speed = parse_rate_to_multiplier(rate)
        mp3_data = await asyncio.to_thread(self._synthesize_sync, text, target_voice, speed, stop_checker)
        if not mp3_data:
            return SynthesisResult(b"", BoundaryMap())

        est_duration = max(1.0, len(text) / (15.0 * speed))
        boundaries = estimate_boundaries_from_text(text, est_duration)
        return SynthesisResult(mp3_data, boundaries)
