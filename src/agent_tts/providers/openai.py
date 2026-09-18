"""OpenAI Audio TTS provider (tts-1 / tts-1-hd)."""

import asyncio
import json
import os
import sys
from typing import Callable, Optional
import urllib.error
import urllib.request

from agent_tts.providers.base import TTSProvider, parse_rate_to_multiplier


class OpenAITTSProvider(TTSProvider):
    """OpenAI Audio TTS provider."""

    name = "openai"

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

    async def synthesize(
        self,
        text: str,
        voice: str,
        rate: str,
        volume: str = "+0%",
        pitch: str = "+0Hz",
        stop_checker: Optional[Callable[[], bool]] = None,
    ) -> bytes:
        if stop_checker and stop_checker():
            return b""
        target_voice = self.resolve_voice(voice)
        speed = parse_rate_to_multiplier(rate)
        return await asyncio.to_thread(self._sync_request, text, target_voice, speed)
