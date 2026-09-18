"""ElevenLabs TTS provider (multilingual, ultra-realistic)."""

import asyncio
import json
import os
import re
import sys
from typing import Callable, Iterator, List, Optional
import urllib.error
import urllib.parse
import urllib.request

from agent_tts.boundaries import BoundaryMap, SynthesisResult, estimate_boundaries_from_text
from agent_tts.providers.base import TTSProvider


class ElevenLabsTTSProvider(TTSProvider):
    """ElevenLabs TTS provider."""

    name = "elevenlabs"
    supports_stream = True

    STREAM_CHUNK_SIZE = 8192
    STREAM_OUTPUT_FORMAT = "mp3_44100_128"

    VOICE_MAP = {
        "rachel": "21m00Tcm4TlvDq8ikWAM",
        "bella": "EXAVITQu4vr4xnSDxMaL",
        "antoni": "ErXwobaYiN019PkySvjV",
        "adam": "pNInz6obpgDQGcFmaJgB",
        "domi": "AZnzlk1XvdvUeBnXmlld",
        "elli": "MF3mGyEYCl7XYWbV9V6O",
        "josh": "TxGEqnHWrfWFTfGW9XjX",
        "arnold": "VR6AewLTigWG4xSOukaG",
        "sam": "yoZ06aMxZJJ28mfd3POQ",
    }
    DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # Rachel (multilingual)

    def __init__(
        self,
        api_key: str = "",
        model: str = "eleven_multilingual_v2",
    ):
        self.api_key = api_key or os.environ.get("ELEVENLABS_API_KEY", "")
        self.model = model or os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2")

    def resolve_voice_id(self, voice: str) -> str:
        v = (voice or "").lower().strip()
        if v in self.VOICE_MAP:
            return self.VOICE_MAP[v]
        if len(voice) >= 15 and not re.search(r"\s", voice):
            return voice
        return self.DEFAULT_VOICE_ID

    def _sync_request(self, text: str, voice_id: str) -> bytes:
        if not self.api_key:
            print("Error: ElevenLabs TTS requires an API key (set ELEVENLABS_API_KEY or --eleven-key)", file=sys.stderr)
            return b""
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        payload = json.dumps({
            "text": text,
            "model_id": self.model,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
            },
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "xi-api-key": self.api_key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
                "User-Agent": "agent-tts/0.1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            print(f"ElevenLabs TTS API error ({e.code}): {err_body}", file=sys.stderr)
            return b""
        except Exception as e:
            print(f"ElevenLabs TTS network error: {e}", file=sys.stderr)
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
        """Yields MP3 chunks incrementally from the ElevenLabs streaming endpoint."""
        if not self.api_key:
            raise RuntimeError("ElevenLabs TTS requires an API key (set ELEVENLABS_API_KEY or --eleven-key)")
        return self._stream_chunks(text, self.resolve_voice_id(voice), stop_checker)

    def _stream_chunks(self, text: str, voice_id: str, stop_checker: Optional[Callable[[], bool]] = None) -> Iterator[bytes]:
        """Yields MP3 chunks as the chunked HTTP response arrives."""
        url = (
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream?"
            + urllib.parse.urlencode({"output_format": self.STREAM_OUTPUT_FORMAT})
        )
        payload = json.dumps({
            "text": text,
            "model_id": self.model,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
            },
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "xi-api-key": self.api_key,
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

    def _sync_streamed(self, text: str, voice_id: str, stop_checker: Optional[Callable[[], bool]] = None) -> Optional[bytes]:
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
            for chunk in self._stream_chunks(text, voice_id, stop_checker):
                chunks.append(chunk)
        except Exception as e:
            print(f"ElevenLabs TTS stream failed ({e}); falling back to full-response synthesis", file=sys.stderr)
            return None
        if stop_checker and stop_checker():
            return b""
        data = b"".join(chunks)
        return data if data else None

    def _synthesize_sync(self, text: str, voice_id: str, stop_checker: Optional[Callable[[], bool]] = None) -> bytes:
        """Streams MP3 chunks when possible; falls back to the full-response request otherwise."""
        streamed = self._sync_streamed(text, voice_id, stop_checker)
        if streamed is not None:
            return streamed
        return self._sync_request(text, voice_id)

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
        voice_id = self.resolve_voice_id(voice)
        mp3_data = await asyncio.to_thread(self._synthesize_sync, text, voice_id, stop_checker)
        if not mp3_data:
            return SynthesisResult(b"", BoundaryMap())

        est_duration = max(1.0, len(text) / 15.0)
        boundaries = estimate_boundaries_from_text(text, est_duration)
        return SynthesisResult(mp3_data, boundaries)
