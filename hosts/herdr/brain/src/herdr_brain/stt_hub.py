"""Model-free client for the llm-hub-whisper WebSocket protocol."""

from __future__ import annotations

import json
import inspect
import threading
import time

import httpx
from websockets.sync.client import connect

from .stt import STATE_LOADING, STATE_READY, STATE_UNAVAILABLE

_SUPPORTS_SYNC_KEEPALIVE = "ping_interval" in inspect.signature(connect).parameters


class HubTranscriber:
    """One independent WebSocket per audio window; no models in this process."""

    unavailable_hint = "Comprueba que llm-hub-whisper esté disponible"

    def __init__(self, ws_url="ws://127.0.0.1:8765/ws/transcribe",
                 health_url="http://127.0.0.1:8766/health"):
        self.ws_url = ws_url
        self.health_url = health_url
        self._state = STATE_LOADING
        self._error = None
        self._probed_at = None
        self._lock = threading.Lock()

    def _probe(self):
        with self._lock:
            try:
                with httpx.Client(timeout=2.0, trust_env=False) as client:
                    response = client.get(self.health_url)
                    response.raise_for_status()
                    if response.json().get("status") != "ok":
                        raise ValueError("whisper health is not ok")
                self._state, self._error = STATE_READY, None
            except Exception as exc:  # noqa: BLE001 — health is fail-soft
                self._state, self._error = STATE_UNAVAILABLE, str(exc)
            self._probed_at = time.monotonic()

    @property
    def state(self):
        if self._probed_at is not None and time.monotonic() - self._probed_at >= 5:
            self._probe()
        return self._state

    def error(self):
        return self._error

    def model_present(self):
        self._probe()
        return self._state == STATE_READY

    def maybe_start_warmup(self):
        thread = threading.Thread(target=self._probe, name="stt-hub-probe", daemon=True)
        thread.start()
        return thread

    def transcribe_bytes(self, data, suffix=".webm"):
        return self.transcribe_in_memory(data, suffix=suffix)

    def transcribe_in_memory(self, data, suffix=".wav", language=None, initial_prompt=None):
        # The service ignores chunks under 100 bytes without responding.
        if len(data) < 100:
            raise ValueError("audio chunk is too short for llm-hub-whisper")
        try:
            # Whisper performs inference synchronously in its event loop and
            # cannot answer keepalive pings until inference completes.
            options = {"open_timeout": 3, "close_timeout": 2, "max_size": None}
            # websockets 13 and 14 have no synchronous keepalive parameter.
            if _SUPPORTS_SYNC_KEEPALIVE:
                options["ping_interval"] = None
            with connect(self.ws_url, **options) as ws:
                # Per-connection language (D8): llm-hub applies it to this
                # connection only. It rides the initial_prompt when there is
                # one; alone it goes as a dedicated config message. Absent →
                # no field → the hub keeps its global default (legacy format).
                if initial_prompt and language:
                    first = {"type": "initial_prompt", "text": initial_prompt,
                             "language": language}
                elif initial_prompt:
                    first = {"type": "initial_prompt", "text": initial_prompt}
                elif language:
                    first = {"type": "config", "language": language}
                else:
                    first = None
                if first is not None:
                    ws.send(json.dumps(first))
                    if json.loads(ws.recv(timeout=5)).get("type") != "ack":
                        raise ValueError("invalid whisper prompt acknowledgement")
                ws.send(data)
                result = json.loads(ws.recv(timeout=120))
                if result.get("type") != "partial" or not isinstance(result.get("text"), str):
                    raise ValueError("invalid whisper transcription response")
            with self._lock:
                self._state, self._error, self._probed_at = STATE_READY, None, time.monotonic()
            return " ".join(result["text"].split())
        except Exception as exc:
            with self._lock:
                self._state, self._error, self._probed_at = STATE_UNAVAILABLE, str(exc), time.monotonic()
            raise
