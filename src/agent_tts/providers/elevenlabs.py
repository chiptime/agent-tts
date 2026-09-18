"""ElevenLabs TTS provider (multilingual, ultra-realistic)."""

import asyncio
import json
import os
import re
import sys
from typing import Callable, Optional
import urllib.error
import urllib.request

from agent_tts.boundaries import BoundaryMap, SynthesisResult, estimate_boundaries_from_text
from agent_tts.providers.base import TTSProvider


class ElevenLabsTTSProvider(TTSProvider):
    """ElevenLabs TTS provider."""

    name = "elevenlabs"

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
        mp3_data = await asyncio.to_thread(self._sync_request, text, voice_id)
        if not mp3_data:
            return SynthesisResult(b"", BoundaryMap())

        est_duration = max(1.0, len(text) / 15.0)
        boundaries = estimate_boundaries_from_text(text, est_duration)
        return SynthesisResult(mp3_data, boundaries)
