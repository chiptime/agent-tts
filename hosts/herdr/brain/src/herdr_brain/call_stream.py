"""WebSocket streaming uplink endpoint and in-memory audio ring buffer (PRD-03)."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import wave
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

from .pairing import PairingStore
from .stt import Transcriber

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit PCM = 2 bytes per sample
BYTES_PER_SECOND = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH  # 32,000 bytes/sec
DEFAULT_PREROLL_MS = 500


class AudioRingBuffer:
    """In-memory bounded ring buffer for PCM16 16 kHz mono audio (D9: never persisted to disk)."""

    def __init__(self, max_seconds: float = 120.0, sample_rate: int = SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.max_bytes = int(max_seconds * BYTES_PER_SECOND)
        self._buffer = bytearray()

    def append(self, pcm16_bytes: bytes) -> None:
        """Appends raw PCM16 samples and bounds buffer to max_bytes by dropping oldest."""
        if not pcm16_bytes:
            return
        self._buffer.extend(pcm16_bytes)
        if len(self._buffer) > self.max_bytes:
            excess = len(self._buffer) - self.max_bytes
            del self._buffer[:excess]

    def get_window(self, duration_ms: float = 0, pre_roll_ms: float = DEFAULT_PREROLL_MS) -> bytes:
        """Extracts an audio slice covering the utterance duration plus pre-roll before onset."""
        total_ms = (duration_ms if duration_ms > 0 else 0) + (pre_roll_ms if pre_roll_ms > 0 else 0)
        if total_ms <= 0:
            return bytes(self._buffer)
        needed_samples = int(total_ms * self.sample_rate / 1000.0)
        needed_bytes = needed_samples * SAMPLE_WIDTH
        if needed_bytes >= len(self._buffer):
            return bytes(self._buffer)
        return bytes(self._buffer[-needed_bytes:])

    def clear(self) -> None:
        self._buffer.clear()

    def __len__(self) -> int:
        return len(self._buffer)


def pcm16_to_wav_bytes(pcm16_data: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Wraps raw 16 kHz mono PCM16 bytes in a standard WAV container in memory."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(CHANNELS)
        wav_file.setsampwidth(SAMPLE_WIDTH)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm16_data)
    return buf.getvalue()


def validate_device_token(
    pairing_store: Optional[PairingStore],
    token: Optional[str],
) -> tuple[bool, Optional[str]]:
    """Validates the device token against PairingStore when enforcement is active."""
    if pairing_store is None:
        return True, None
    if pairing_store.enforced():
        if not token:
            return False, "missing device token"
        status, _device = pairing_store.resolve_status(token)
        if status == "ok":
            return True, None
        if status == "expired":
            return False, "device credential expired"
        return False, "device not paired"
    # When pairing is not enforced, token is optional but first-message contract still applies
    return True, None


async def handle_call_stream(
    websocket: WebSocket,
    pairing_store: Optional[PairingStore],
    transcriber: Optional[Transcriber],
    active_call: Optional[dict] = None,
) -> None:
    """Handles a WebSocket /call/stream connection lifecycle: auth -> frames -> transcribe.

    ``transcriber`` is the live engine's DEDICATED transcriber (S4); when
    it has degraded to its fallback model it exposes ``notice_reason``,
    forwarded to the client exactly once per connection as an informational
    ``{"type": "notice"}`` — unlike an ``error`` message, a notice never
    triggers the client's transport fallback.
    """
    await websocket.accept()

    # 1. First-message auth
    try:
        first_msg = await asyncio.wait_for(websocket.receive(), timeout=5.0)
    except Exception:
        try:
            await websocket.close(code=1008)
        except Exception:
            pass
        return

    token = None
    is_auth_shape = False
    if "text" in first_msg and first_msg["text"]:
        try:
            data = json.loads(first_msg["text"])
            if isinstance(data, dict) and data.get("type") == "auth":
                token = data.get("token") or data.get("device_token")
                is_auth_shape = True
        except Exception:
            is_auth_shape = False

    if not is_auth_shape:
        await websocket.close(code=1008)
        return

    # Reject if pairing is enforced and token is invalid
    if pairing_store and pairing_store.enforced():
        valid, _reason = validate_device_token(pairing_store, token)
        if not valid:
            await websocket.close(code=1008)  # Policy Violation
            return

    await websocket.send_json({"type": "auth_ok"})

    # D13: forward a degraded-live-model notice exactly once per
    # connection — right after auth when the model degraded at boot, else
    # before the first transcript that survived the degradation.
    notice_sent = False

    async def send_stt_notice_once() -> None:
        nonlocal notice_sent
        if notice_sent:
            return
        reason = getattr(transcriber, "notice_reason", None)
        if reason:
            notice_sent = True
            await websocket.send_json({"type": "notice", "reason": reason})

    await send_stt_notice_once()

    ring_buffer = AudioRingBuffer(max_seconds=120.0)

    # 2. Frame streaming and transcription loop
    try:
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if "bytes" in msg and msg["bytes"]:
                ring_buffer.append(msg["bytes"])
            elif "text" in msg and msg["text"]:
                try:
                    payload = json.loads(msg["text"])
                except Exception:
                    continue
                cmd = payload.get("type")
                if cmd == "transcribe":
                    duration_ms = float(payload.get("duration_ms", 0))
                    pre_roll_ms = float(payload.get("pre_roll_ms", DEFAULT_PREROLL_MS))
                    pcm16 = ring_buffer.get_window(duration_ms=duration_ms, pre_roll_ms=pre_roll_ms)
                    wav_data = pcm16_to_wav_bytes(pcm16)
                    loop = asyncio.get_running_loop()
                    text = ""
                    d9_err = None
                    if transcriber is not None:
                        if hasattr(transcriber, "transcribe_in_memory"):
                            try:
                                text = await loop.run_in_executor(
                                    None, transcriber.transcribe_in_memory, wav_data, ".wav"
                                )
                            except Exception as exc:
                                d9_err = str(exc)
                        else:
                            try:
                                text = await loop.run_in_executor(
                                    None, transcriber.transcribe_bytes, wav_data, ".wav"
                                )
                            except Exception as exc:
                                d9_err = str(exc)
                    if d9_err and ("D9 violation" in d9_err or "engine backend" in d9_err):
                        await websocket.send_json({
                            "type": "error",
                            "message": d9_err,
                        })
                    else:
                        await send_stt_notice_once()
                        text_str = (text or "").strip()
                        await websocket.send_json({
                            "type": "transcript",
                            "text": text_str,
                            "duration_ms": duration_ms,
                            "pre_roll_ms": pre_roll_ms,
                        })
                elif cmd == "clear":
                    ring_buffer.clear()
                elif cmd == "ping":
                    await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("call_stream connection ended: %s", exc)
