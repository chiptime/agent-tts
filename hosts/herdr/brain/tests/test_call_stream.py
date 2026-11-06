"""Tests for WebSocket /call/stream endpoint (PRD-03 Task T1, PRD-04 Task T3)."""

import io
import logging
import struct
import threading
import time
import wave
import pytest
import numpy as np
from fastapi.testclient import TestClient

from herdr_brain.config import Settings
from herdr_brain.pairing import PairingStore
from herdr_brain.server import create_app
from herdr_brain.stt import STATE_READY, Transcriber
from tests.async_support import run_async
from tests.conftest import SETTINGS_KWARGS


# 100 ms of speech-level PCM16 16 kHz mono (a 3000-amplitude tone, RMS ≈ 2100).
# The server never runs inference on silent windows, so every fixture that
# expects the model to be reached must carry audible content.
SPEECH_100MS = (np.sin(np.arange(1600) * 0.2) * 3000).astype("<i2").tobytes()


class SpyModel:
    """faster-whisper model double capturing the exact WAV path and samples."""

    last_wav_bytes: bytes | None = None
    last_sample_count: int | None = None
    transcribe_calls: int = 0
    last_language: str | None = None
    last_initial_prompt: str | None = None

    def transcribe(self, path, language=None, initial_prompt=None):
        SpyModel.transcribe_calls += 1
        SpyModel.last_language = language
        SpyModel.last_initial_prompt = initial_prompt
        if hasattr(path, "read"):
            data = path.read()
            SpyModel.last_wav_bytes = data
            with wave.open(io.BytesIO(data), "rb") as wav_file:
                SpyModel.last_sample_count = wav_file.getnframes()
        else:
            with open(path, "rb") as f:
                SpyModel.last_wav_bytes = f.read()
            with wave.open(path, "rb") as wav_file:
                SpyModel.last_sample_count = wav_file.getnframes()
        return (
            iter([type("Seg", (), {"text": "primera palabra detectada"})]),
            type("Info", (), {"language": "es"}),
        )


def make_spy_transcriber(settings: Settings) -> Transcriber:
    SpyModel.last_wav_bytes = None
    SpyModel.last_sample_count = None
    SpyModel.transcribe_calls = 0
    t = Transcriber(settings, loader=lambda: SpyModel(), cached=lambda: True)
    t.state = STATE_READY
    return t


class ServerSpyModel:
    """Shared /transcribe double — distinct text from the live SpyModel."""

    calls = 0

    def transcribe(self, path, language=None, initial_prompt=None):
        ServerSpyModel.calls += 1
        return (
            iter([type("Seg", (), {"text": "texto servidor"})]),
            type("Info", (), {"language": "es"}),
        )


def make_server_spy_transcriber(settings: Settings) -> Transcriber:
    ServerSpyModel.calls = 0
    t = Transcriber(settings, loader=lambda: ServerSpyModel(), cached=lambda: True)
    t.state = STATE_READY
    return t


def _stream_cfg(tmp_path, **settings_overrides):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir(exist_ok=True)
    return Settings(
        **{
            **SETTINGS_KWARGS,
            "audio_dir": str(audio_dir),
            "state_dir": str(tmp_path),
            "stt_warmup": False,
            **settings_overrides,
        }
    )


def build_app(tmp_path, transcriber=None, live_transcriber=None, **settings_overrides):
    cfg = _stream_cfg(tmp_path, **settings_overrides)
    t = transcriber or make_spy_transcriber(cfg)
    # S4: without an explicit live seam the socket reuses the shared spy,
    # mirroring production's dedicated live transcriber shape.
    live = live_transcriber if live_transcriber is not None else t
    return create_app(settings=cfg, version="test", transcriber=t, live_transcriber=live)


def test_websocket_rejects_unauthenticated_first_message(tmp_path):
    """S1/FR-01: WebSocket rejects connection whose first message is not a valid device token."""
    # Enforce pairing with a registered device
    store = PairingStore(tmp_path)
    minted = store.mint()
    app = build_app(tmp_path)
    client = TestClient(app)
    pair_resp = client.post("/pair", json={"code": minted["code"], "label": "test-phone"})
    assert pair_resp.status_code == 200

    # 1. First message has wrong token
    with pytest.raises(Exception):
        with client.websocket_connect("/call/stream") as ws:
            ws.send_json({"type": "auth", "token": "invalid-token-12345"})
            ws.receive_json()

    # 2. First message is raw audio bytes instead of auth
    with pytest.raises(Exception):
        with client.websocket_connect("/call/stream") as ws:
            ws.send_bytes(b"\x00" * 3200)
            ws.receive_json()

    # 3. First message is valid JSON but wrong type
    with pytest.raises(Exception):
        with client.websocket_connect("/call/stream") as ws:
            ws.send_json({"type": "ping"})
            ws.receive_json()


def test_websocket_accepts_valid_device_token(tmp_path):
    """S1/FR-01: WebSocket accepts connection with valid device token in first message."""
    store = PairingStore(tmp_path)
    minted = store.mint()
    app = build_app(tmp_path)
    client = TestClient(app)
    pair_resp = client.post("/pair", json={"code": minted["code"], "label": "test-phone"})
    assert pair_resp.status_code == 200
    valid_token = pair_resp.json()["token"]

    with client.websocket_connect("/call/stream") as ws:
        ws.send_json({"type": "auth", "token": valid_token})
        resp = ws.receive_json()
        assert resp["type"] == "auth_ok"


def test_client_diag_logs_audio_context_state_build_and_reason(tmp_path, caplog):
    """L53(d): the 2 s client diag must reach the journal with the
    AudioContext state, capture mode, frame counters, analyser peak and
    the served build — numbers/opaque tokens only, never audio/text."""
    store = PairingStore(tmp_path)
    minted = store.mint()
    app = build_app(tmp_path)
    client = TestClient(app)
    token = client.post(
        "/pair", json={"code": minted["code"], "label": "test-phone"}
    ).json()["token"]

    with caplog.at_level(logging.INFO, logger="herdr_brain.call_stream"):
        with client.websocket_connect("/call/stream") as ws:
            ws.send_json({"type": "auth", "token": token})
            assert ws.receive_json()["type"] == "auth_ok"
            ws.send_json({
                "type": "diag",
                "mode": "worklet",
                "posted": 42,
                "sent": 40,
                "queued": 2,
                "buffered": 2048,
                "fails": 1,
                "maxrms": 0.125,
                "ctx": "running",
                "build": "2efa6dc-s2bd2523f",
                "reason": "worklet_no_frames",
                "state": "listening",
            })
            time.sleep(0.15)

    text = caplog.text
    assert "live client diag:" in text
    assert "mode=worklet" in text
    assert "posted=42" in text
    assert "sent=40" in text
    assert "queued=2" in text
    assert "buffered=2048" in text
    assert "fails=1" in text
    assert "ctx=running" in text
    assert "build=2efa6dc-s2bd2523f" in text
    assert "reason=worklet_no_frames" in text
    assert "state=listening" in text
    assert "maxrms=0.1250" in text


def test_client_diag_bounds_untrusted_fields(tmp_path, caplog):
    """The diag payload is client input: long/hostile strings are
    truncated and non-numeric counters degrade to -1, exactly like the
    transcribe diagnostics, so the journal stays one bounded line."""
    store = PairingStore(tmp_path)
    minted = store.mint()
    app = build_app(tmp_path)
    client = TestClient(app)
    token = client.post(
        "/pair", json={"code": minted["code"], "label": "test-phone"}
    ).json()["token"]

    with caplog.at_level(logging.INFO, logger="herdr_brain.call_stream"):
        with client.websocket_connect("/call/stream") as ws:
            ws.send_json({"type": "auth", "token": token})
            assert ws.receive_json()["type"] == "auth_ok"
            ws.send_json({
                "type": "diag",
                "mode": "m" * 200,
                "build": "b" * 200,
                "ctx": "c" * 200,
                "reason": "r" * 200,
                "state": "s" * 200,
                "posted": "not-a-number",
                "sent": None,
                "queued": True,
            })
            time.sleep(0.15)

    text = caplog.text
    assert "live client diag:" in text
    assert "m" * 13 not in text
    assert "b" * 25 not in text
    assert "c" * 13 not in text
    assert "r" * 25 not in text
    assert "s" * 15 not in text
    assert "posted=-1" in text
    assert "sent=-1" in text
    assert "queued=-1" in text


def test_websocket_buffers_frames_and_transcribes_with_preroll(tmp_path):
    """S1/FR-02/FR-03: Buffers PCM16 frames in memory; transcribing utterance includes ~500ms pre-roll."""
    store = PairingStore(tmp_path)
    minted = store.mint()
    app = build_app(tmp_path)
    client = TestClient(app)
    pair_resp = client.post("/pair", json={"code": minted["code"], "label": "test-phone"})
    assert pair_resp.status_code == 200
    valid_token = pair_resp.json()["token"]

    with client.websocket_connect("/call/stream") as ws:
        ws.send_json({"type": "auth", "token": valid_token})
        assert ws.receive_json()["type"] == "auth_ok"

        # 16 kHz mono 16-bit: 1 sample = 2 bytes. 100 ms = 1600 samples = 3200 bytes.
        # Send 15 frames = 1500 ms (48,000 bytes)
        frame_100ms = SPEECH_100MS
        for _ in range(15):
            ws.send_bytes(frame_100ms)

        # Transcribe an utterance window of 1000 ms with default 500 ms pre-roll
        ws.send_json({"type": "transcribe", "duration_ms": 1000, "pre_roll_ms": 500})
        reply = ws.receive_json()
        assert reply["type"] in ("transcript", "final")
        assert reply["text"] == "primera palabra detectada"

        # Check pre-roll verification on the spy model:
        # 1000 ms utterance + 500 ms pre-roll = 1500 ms = 24,000 samples
        assert SpyModel.transcribe_calls == 1
        assert SpyModel.last_sample_count == 24000


def test_websocket_rejects_unallowed_host(tmp_path):
    """WebSocket handshake rejects unallowed Host header (fail-closed, anti-rebinding)."""
    app = build_app(tmp_path, allow_any_host=False)
    client = TestClient(app)

    with pytest.raises(Exception):
        with client.websocket_connect("/call/stream", headers={"host": "evil.example"}) as ws:
            ws.send_json({"type": "auth", "token": "test"})
            ws.receive_json()


def test_websocket_rejects_cross_origin_on_remote_host(tmp_path):
    """WebSocket handshake rejects cross-origin request from remote origin."""
    app = build_app(tmp_path, allow_any_host=False)
    client = TestClient(app)

    with pytest.raises(Exception):
        with client.websocket_connect(
            "/call/stream",
            headers={"host": "node.tail1234.ts.net", "origin": "https://evil.example"},
        ) as ws:
            ws.send_json({"type": "auth", "token": "test"})
            ws.receive_json()


def test_websocket_rejects_untrusted_tailscale_user(tmp_path):
    """WebSocket handshake rejects untrusted Tailscale login."""
    app = build_app(tmp_path, allow_any_host=False, trusted_user="alice")
    client = TestClient(app)

    with pytest.raises(Exception):
        with client.websocket_connect(
            "/call/stream",
            headers={"host": "localhost", "tailscale-user-login": "bob"},
        ) as ws:
            ws.send_json({"type": "auth", "token": "test"})
            ws.receive_json()


def test_websocket_transcribes_strictly_in_memory_without_disk_persistence(tmp_path):
    """D9: raw audio is transcribed strictly in-memory and never written to disk."""
    app = build_app(tmp_path)
    client = TestClient(app)

    # We record files before
    import os, tempfile
    before_files = set(os.listdir(tempfile.gettempdir()))

    with client.websocket_connect("/call/stream") as ws:
        ws.send_json({"type": "auth", "token": ""})
        assert ws.receive_json()["type"] == "auth_ok"

        # Send frames
        ws.send_bytes(SPEECH_100MS * 4)  # 400 ms: above the minimum inference length
        ws.send_json({"type": "transcribe", "duration_ms": 400, "pre_roll_ms": 0})
        reply = ws.receive_json()
        assert reply["type"] in ("transcript", "final")

    after_files = set(os.listdir(tempfile.gettempdir()))
    # Verify no leaked audio files in tempdir
    new_files = [f for f in (after_files - before_files) if "stt" in f or "wav" in f or "webm" in f]
    assert new_files == []


def test_websocket_engine_backend_fails_closed_under_d9(tmp_path):
    """D9: engine backend fails closed when in-memory transcription is requested."""
    from herdr_brain.stt_engine import EngineTranscriber

    cfg = Settings(
        **{
            **SETTINGS_KWARGS,
            "stt_backend": "engine",
            "audio_dir": str(tmp_path / "audio"),
            "state_dir": str(tmp_path),
            "stt_warmup": False,
        }
    )
    t = EngineTranscriber(cfg, runner=lambda *args, **kwargs: None)
    # S4: the socket rides the dedicated live seam — pin the engine double
    # there to prove the D9 fail-closed behavior still holds when the
    # engine backend IS the socket's transcriber.
    app = create_app(settings=cfg, version="test", transcriber=t, live_transcriber=t)
    client = TestClient(app)

    with client.websocket_connect("/call/stream") as ws:
        ws.send_json({"type": "auth", "token": ""})
        assert ws.receive_json()["type"] == "auth_ok"

        ws.send_bytes(SPEECH_100MS * 4)  # 400 ms: above the minimum inference length
        ws.send_json({"type": "transcribe", "duration_ms": 400, "pre_roll_ms": 0})
        reply = ws.receive_json()
        assert reply["type"] in ("error", "transcript")
        # Must fail closed: either empty text or error type indicating no disk path
        if reply["type"] == "transcript":
            assert reply["text"] == ""


def test_call_stream_uses_live_transcriber_transcribe_keeps_shared(tmp_path):
    """S4 preservation: /call/stream rides the dedicated live transcriber
    while POST /transcribe keeps the shared one EXACTLY as today."""
    app = build_app(
        tmp_path,
        transcriber=make_server_spy_transcriber(_stream_cfg(tmp_path)),
        live_transcriber=make_spy_transcriber(_stream_cfg(tmp_path)),
    )
    client = TestClient(app)

    resp = client.post(
        "/transcribe",
        files={"audio": ("clip.webm", io.BytesIO(b"fake-webm"), "audio/webm")},
    )
    assert resp.status_code == 200
    assert resp.json() == {"text": "texto servidor"}  # the SHARED model answered

    with client.websocket_connect("/call/stream") as ws:
        ws.send_json({"type": "auth", "token": ""})
        assert ws.receive_json()["type"] == "auth_ok"
        ws.send_bytes(SPEECH_100MS * 4)  # 400 ms: above the minimum inference length
        ws.send_json({"type": "transcribe", "duration_ms": 400, "pre_roll_ms": 0})
        reply = ws.receive_json()
        assert reply["type"] in ("transcript", "final")
        assert reply["text"] == "primera palabra detectada"  # the LIVE model answered

    assert ServerSpyModel.calls == 1
    assert SpyModel.transcribe_calls == 1


def test_websocket_notice_when_live_model_degrades_at_transcribe(tmp_path):
    """D13: a CUDA OOM at transcribe degrades once to the fallback and the
    socket receives exactly one {"type":"notice"} before the transcript."""
    from herdr_brain.stt import LIVE_NOTICE_OOM, LiveTranscriber

    model_dir = tmp_path / "live-large"
    model_dir.mkdir()
    (model_dir / "model.bin").write_bytes(b"weights")

    class OOMAtTranscribe:
        def transcribe(self, path, language=None, initial_prompt=None):
            raise RuntimeError("CUDA out of memory")

    cfg = _stream_cfg(tmp_path, live_stt_model=str(model_dir))
    live = LiveTranscriber(
        cfg, loader=lambda: OOMAtTranscribe(), fallback_loader=lambda: SpyModel()
    )
    app = create_app(
        settings=cfg, version="test",
        transcriber=make_server_spy_transcriber(cfg), live_transcriber=live,
    )
    client = TestClient(app)

    with client.websocket_connect("/call/stream") as ws:
        ws.send_json({"type": "auth", "token": ""})
        assert ws.receive_json()["type"] == "auth_ok"

        ws.send_bytes(SPEECH_100MS * 4)  # 400 ms: above the minimum inference length
        ws.send_json({"type": "transcribe", "duration_ms": 400, "pre_roll_ms": 0})
        assert ws.receive_json() == {"type": "notice", "reason": LIVE_NOTICE_OOM}
        reply = ws.receive_json()
        assert reply["type"] in ("transcript", "final")
        assert reply["text"] == "primera palabra detectada"  # retried on the fallback

        # Second round: no second notice, transcript straight away.
        ws.send_json({"type": "transcribe", "duration_ms": 400, "pre_roll_ms": 0})
        assert ws.receive_json()["type"] in ("transcript", "final")


def test_websocket_notice_after_auth_when_live_already_degraded(tmp_path):
    """D13/D7: when the live model degraded during boot warmup (here: OOM
    at load), the notice arrives right after auth_ok."""
    from herdr_brain.stt import LIVE_NOTICE_OOM, LiveTranscriber

    model_dir = tmp_path / "live-large"
    model_dir.mkdir()
    (model_dir / "model.bin").write_bytes(b"weights")

    def cuda_oom():
        raise RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")

    cfg = _stream_cfg(tmp_path, live_stt_model=str(model_dir))
    live = LiveTranscriber(
        cfg, loader=cuda_oom, fallback_loader=lambda: SpyModel()
    )
    live.warmup()  # boot warmup degrades: OOM at load -> fallback + notice
    assert live.notice_reason == LIVE_NOTICE_OOM

    app = create_app(
        settings=cfg, version="test",
        transcriber=make_server_spy_transcriber(cfg), live_transcriber=live,
    )
    client = TestClient(app)

    with client.websocket_connect("/call/stream") as ws:
        ws.send_json({"type": "auth", "token": ""})
        assert ws.receive_json()["type"] == "auth_ok"
        assert ws.receive_json() == {"type": "notice", "reason": LIVE_NOTICE_OOM}


# ---- L13 correction: fail-closed transcription error propagation ----
#
# Blocker 1 (L13): a general transcription exception (e.g. CUDA
# unavailable) used to surface as {"type":"transcript","text":""}, which
# the UI read as "not understood". The contract now: ANY exception from
# the transcription call fails closed as {"type":"error"} carrying the
# fixed generic message "live transcription failed" (exception detail
# never reaches the client), while a successful transcription that
# legitimately returns no text stays an empty transcript.


class _FailsAtTranscribe:
    """Model double whose every transcription raises (L13: general failure)."""

    def __init__(self, message):
        self._message = message

    def transcribe(self, path, language=None, initial_prompt=None):
        raise RuntimeError(self._message)


def _boot_stream_app(tmp_path, live):
    """App whose /call/stream rides ``live``; /transcribe keeps the shared spy."""
    cfg = _stream_cfg(tmp_path)
    app = create_app(
        settings=cfg, version="test",
        transcriber=make_server_spy_transcriber(cfg), live_transcriber=live,
    )
    return TestClient(app)


def test_transcribe_exception_fails_closed_as_generic_error(tmp_path):
    """L13/T2-S4: a general transcription failure (CUDA unavailable) must
    surface as {"type":"error"} — never an empty successful transcript the
    UI would read as "not understood". The socket survives (FR-07) and no
    raw audio ever lands on disk (D9)."""
    import os
    import tempfile

    before_files = set(os.listdir(tempfile.gettempdir()))
    cfg = _stream_cfg(tmp_path)
    live = Transcriber(
        cfg, loader=lambda: _FailsAtTranscribe("CUDA unavailable"), cached=lambda: True
    )
    live.state = STATE_READY
    client = _boot_stream_app(tmp_path, live)

    with client.websocket_connect("/call/stream") as ws:
        ws.send_json({"type": "auth", "token": ""})
        assert ws.receive_json()["type"] == "auth_ok"

        ws.send_bytes(SPEECH_100MS * 4)  # 400 ms: above the minimum inference length
        ws.send_json({"type": "transcribe", "duration_ms": 400, "pre_roll_ms": 0})
        reply = ws.receive_json()
        assert reply == {"type": "error", "message": "live transcription failed"}, reply

        # FR-07: the error must not kill the call — the socket still serves.
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}

    after_files = set(os.listdir(tempfile.gettempdir()))
    assert [
        f for f in (after_files - before_files)
        if "stt" in f or "wav" in f or "webm" in f
    ] == []


def test_transcribe_error_message_leaks_no_exception_detail(tmp_path):
    """L13: the error message is the fixed generic string — exception text
    (paths, key material, model details) never reaches the client."""
    cfg = _stream_cfg(tmp_path)
    leak = "GLM_API_KEY=s3cret /home/op/models/large-v3 secret-key.pem"
    live = Transcriber(
        cfg, loader=lambda: _FailsAtTranscribe(leak), cached=lambda: True
    )
    live.state = STATE_READY
    client = _boot_stream_app(tmp_path, live)

    with client.websocket_connect("/call/stream") as ws:
        ws.send_json({"type": "auth", "token": ""})
        assert ws.receive_json()["type"] == "auth_ok"
        ws.send_bytes(SPEECH_100MS * 4)  # 400 ms: above the minimum inference length
        ws.send_json({"type": "transcribe", "duration_ms": 400, "pre_roll_ms": 0})
        reply = ws.receive_json()

    assert reply["type"] == "error"
    assert reply["message"] == "live transcription failed"
    for secret in ("GLM_API_KEY", "s3cret", "secret-key.pem", "/home/op"):
        assert secret not in reply["message"]


def test_primary_and_fallback_failure_fails_closed(tmp_path):
    """L13/T2-S4/D13: when the live model degrades and the FALLBACK also
    fails (a second OOM), the socket reports an error — never an empty
    successful transcript."""
    from herdr_brain.stt import LiveTranscriber

    model_dir = tmp_path / "live-large"
    model_dir.mkdir()
    (model_dir / "model.bin").write_bytes(b"weights")

    cfg = _stream_cfg(tmp_path, live_stt_model=str(model_dir))
    live = LiveTranscriber(
        cfg,
        loader=lambda: _FailsAtTranscribe("CUDA out of memory"),
        fallback_loader=lambda: _FailsAtTranscribe("CUDA out of memory"),
    )
    client = _boot_stream_app(tmp_path, live)

    with client.websocket_connect("/call/stream") as ws:
        ws.send_json({"type": "auth", "token": ""})
        assert ws.receive_json()["type"] == "auth_ok"
        ws.send_bytes(SPEECH_100MS * 4)  # 400 ms: above the minimum inference length
        ws.send_json({"type": "transcribe", "duration_ms": 400, "pre_roll_ms": 0})
        reply = ws.receive_json()

    assert reply == {"type": "error", "message": "live transcription failed"}, reply


def test_successful_empty_transcript_is_not_an_error(tmp_path):
    """L13 preserve: a transcription that legitimately returns no text is
    a successful empty transcript — not an error (the UI asks to repeat)."""

    class SilentModel:
        def transcribe(self, path, language=None, initial_prompt=None):
            return (
                iter([type("Seg", (), {"text": "   "})]),
                type("Info", (), {"language": "es"}),
            )

    cfg = _stream_cfg(tmp_path)
    live = Transcriber(cfg, loader=lambda: SilentModel(), cached=lambda: True)
    live.state = STATE_READY
    client = _boot_stream_app(tmp_path, live)

    with client.websocket_connect("/call/stream") as ws:
        ws.send_json({"type": "auth", "token": ""})
        assert ws.receive_json()["type"] == "auth_ok"
        ws.send_bytes(SPEECH_100MS * 4)  # 400 ms: above the minimum inference length
        ws.send_json({"type": "transcribe", "duration_ms": 400, "pre_roll_ms": 0})
        reply = ws.receive_json()

    assert reply["type"] in ("transcript", "final")
    assert reply["text"] == ""


# ---- T3 (PRD-04/S5): streaming partials and finals over the socket ----
#
# The server-side streaming loop: after {"type":"stream","action":"start"}
# the socket transcribes a growing window from the utterance origin as
# audio arrives (~one pass per second of new audio, FR-05 caps each pass
# at ~5 s), emits {"type":"partial"} with the current best guess and
# {"type":"final"} for the token prefix that stopped changing across
# consecutive passes (FR-02 local agreement, deterministic). The turn-end
# {"type":"transcript"} stays the ONLY dispatch source: finals never
# duplicate it and the window resets after it (prefix-overlap protection).

FRAME_100MS = SPEECH_100MS  # 100 ms of PCM16 16 kHz mono


class ScriptedStreamingModel:
    """Streaming double: pops scripted texts per transcribe call (the last
    one repeats), records (language, initial_prompt, samples) per call,
    tracks concurrent calls, and can gate ONE chosen call (default: the
    first) from the test."""

    def __init__(self, texts, gate_call=1):
        self._texts = list(texts)
        self._last = ""
        self.calls: list[tuple] = []
        self.texts: list[list[str]] = []
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()
        self.gate = threading.Event()
        self.gate_call = gate_call

    def release(self):
        self.gate.set()

    def transcribe(self, path, language=None, initial_prompt=None):
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if len(self.calls) + 1 == self.gate_call:
                self.gate.wait(timeout=10)
                self.gate.set()
            data = path.read() if hasattr(path, "read") else open(path, "rb").read()
            with wave.open(io.BytesIO(data), "rb") as wav_file:
                samples = wav_file.getnframes()
            if self._texts:
                self._last = self._texts.pop(0)
            self.calls.append((language, initial_prompt, samples))
            self.texts.append(self._last.split())
            return (
                iter([type("Seg", (), {"text": self._last})]),
                type("Info", (), {"language": language or "es"}),
            )
        finally:
            with self._lock:
                self.active -= 1


def _streaming_app(tmp_path, model, **settings_overrides):
    cfg = _stream_cfg(tmp_path, **settings_overrides)
    live = Transcriber(cfg, loader=lambda: model, cached=lambda: True)
    live.state = STATE_READY
    return TestClient(
        create_app(
            settings=cfg, version="test",
            transcriber=make_server_spy_transcriber(cfg), live_transcriber=live,
        )
    )


def _auth(ws, locale=None):
    msg = {"type": "auth", "token": ""}
    if locale is not None:
        msg["locale"] = locale
    ws.send_json(msg)
    assert ws.receive_json()["type"] == "auth_ok"


def _send_audio(ws, ms):
    for _ in range(ms // 100):
        ws.send_bytes(FRAME_100MS)


def _recv(ws, timeout=6.0):
    """receive_json with a deadline — a missing streaming message must
    FAIL the test, never hang it (the reader thread is a daemon so an
    abandoned receive dies with the test)."""
    import queue

    box: queue.Queue = queue.Queue()

    def _read():
        try:
            box.put(("ok", ws.receive_json()))
        except Exception as exc:  # noqa: BLE001 — surfaced to the test
            box.put(("err", exc))

    threading.Thread(target=_read, daemon=True).start()
    try:
        kind, payload = box.get(timeout=timeout)
    except queue.Empty:
        pytest.fail(f"no socket message within {timeout}s")
    if kind == "err":
        raise payload
    return payload


def _drain(ws, count, timeout=8.0):
    """Direct MAIN-thread receives bounded by one SIGALRM deadline.

    The TestClient funnels every send and receive through a single
    blocking portal; under tight send-burst interleavings a helper
    thread's portal receive can wedge even though the server queued its
    messages, so a burst drain stays on the main thread and the alarm
    bounds the whole batch (a missing message fails loudly, never hangs).
    """
    import signal

    class _Deadline(Exception):
        pass

    def _fire(_signum, _frame):
        raise _Deadline()

    previous = signal.signal(signal.SIGALRM, _fire)
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        return [ws.receive_json() for _ in range(count)]
    except _Deadline:
        pytest.fail(f"fewer than {count} socket messages within {timeout}s")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def test_streaming_partials_flow_while_speaking(tmp_path):
    """FR-01/S5: after stream start, audio frames alone produce partial
    transcript messages on the same socket."""
    model = ScriptedStreamingModel(["hola que tal"])
    model.release()
    client = _streaming_app(tmp_path, model)

    with client.websocket_connect("/call/stream") as ws:
        _auth(ws)
        ws.send_json({"type": "stream", "action": "start"})
        _send_audio(ws, 1000)
        msg = _recv(ws)
        assert msg["type"] == "partial", msg
        assert msg["text"] == "hola que tal"


def test_local_agreement_finals_only_for_stable_prefix(tmp_path):
    """FR-02: a token prefix becomes final only when identical across
    consecutive passes; the unstable remainder stays partial. The
    partial/final sequence is deterministic for the same audio+script."""
    model = ScriptedStreamingModel(
        ["hola como", "hola como estas", "hola como estas hoy"]
    )
    model.release()
    client = _streaming_app(tmp_path, model)

    with client.websocket_connect("/call/stream") as ws:
        _auth(ws)
        ws.send_json({"type": "stream", "action": "start"})
        _send_audio(ws, 1000)
        # Pass 1: nothing to agree with yet — partial only.
        assert _recv(ws) == {"type": "partial", "text": "hola como"}
        _send_audio(ws, 1000)
        # Pass 2: "hola como" agreed across passes -> final, rest partial.
        assert _recv(ws) == {"type": "final", "text": "hola como"}
        assert _recv(ws) == {"type": "partial", "text": "hola como estas"}
        _send_audio(ws, 1000)
        # Pass 3: the newly agreed tail "estas" becomes final once.
        assert _recv(ws) == {"type": "final", "text": "estas"}
        assert _recv(ws) == {"type": "partial", "text": "hola como estas hoy"}
        # Windows grow from the utterance origin: 1 s, 2 s, 3 s.
        assert [c[2] for c in model.calls] == [16000, 32000, 48000]


def test_streaming_passes_never_overlap_inference(tmp_path):
    """FR-05: one transcription pass in flight per call — audio arriving
    during a pass is picked up by the NEXT pass, never a concurrent one,
    and is never silently lost."""
    model = ScriptedStreamingModel(["primera pasada", "segunda pasada"])
    client = _streaming_app(tmp_path, model)

    with client.websocket_connect("/call/stream") as ws:
        _auth(ws)
        ws.send_json({"type": "stream", "action": "start"})
        _send_audio(ws, 1000)  # pass 1 starts and blocks on the gate
        _send_audio(ws, 2500)  # more audio while the pass is in flight
        import time

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and model.active == 0:
            time.sleep(0.05)
        assert model.active == 1, "gated pass never started"
        # While the pass holds the only inference slot, no second call
        # (the gated call records itself only after release, so <= 1).
        assert len(model.calls) <= 1, "a second pass started while one was in flight"
        assert model.max_active == 1

        model.release()
        first = _recv(ws)
        assert first["type"] == "partial"
        # The audio that arrived mid-pass is processed by a follow-up pass.
        second = _recv(ws)
        assert second["type"] == "partial"
        assert second["text"] == "segunda pasada"
        assert model.max_active == 1, "two inferences ran concurrently"
        assert len(model.calls) == 2
        # No pass window exceeded the ~5 s cap (FR-05).
        assert all(c[2] <= 5 * 16000 + 1600 for c in model.calls)


def test_window_cap_bounds_each_pass_and_commits_at_cap(tmp_path):
    """FR-05: with audio beyond the ~5 s window cap the origin slides and
    the stable prefix commits; no pass ever transcribes more than ~5 s."""
    model = ScriptedStreamingModel(["palabra estable"] * 20)
    model.release()
    client = _streaming_app(tmp_path, model)

    with client.websocket_connect("/call/stream") as ws:
        _auth(ws)
        ws.send_json({"type": "stream", "action": "start"})
        _send_audio(ws, 11000)
        messages = []
        for _ in range(2):
            messages.append(_recv(ws))
        # Every window stayed within the cap.
        assert model.calls, "no streaming pass ran"
        assert all(c[2] <= 5 * 16000 + 1600 for c in model.calls)
        assert any(m["type"] == "final" for m in messages), (
            "no final committed for a stable repeated transcription"
        )
        # The committed prefix is exactly the agreed text, never duplicated
        # into a later window (prefix-overlap protection at the cap).
        finals = [m["text"] for m in messages if m["type"] == "final"]
        assert "".join(finals).count("palabra") == len(finals)


def test_stream_window_starts_at_onset_preroll(tmp_path):
    """S1 pre-roll carry-over: the partial window begins ~500 ms before
    stream start, so audio already in the ring at onset is included."""
    model = ScriptedStreamingModel(["desde el inicio"])
    model.release()
    client = _streaming_app(tmp_path, model)

    with client.websocket_connect("/call/stream") as ws:
        _auth(ws)
        _send_audio(ws, 2000)  # pre-onset audio already buffered
        ws.send_json({"type": "stream", "action": "start"})
        _send_audio(ws, 1000)
        assert _recv(ws)["type"] == "partial"
        # Origin: 2000 - 500 = 1500. The pass fires when audio reaches
        # origin+1000 (2500): the window is [1500, 2500] = 16000 samples,
        # i.e. the 500 ms pre-roll PLUS 500 ms of fresh speech.
        assert model.calls[0][2] == 16000


def test_no_streaming_pass_before_stream_start(tmp_path):
    """Compute guard: idle audio without stream start runs NO inference —
    the turn-end transcribe still works exactly as today."""
    model = ScriptedStreamingModel(["texto del turno"])
    model.release()
    client = _streaming_app(tmp_path, model)

    with client.websocket_connect("/call/stream") as ws:
        _auth(ws)
        _send_audio(ws, 2000)
        import time

        time.sleep(0.4)
        assert model.calls == [], "streaming pass ran without stream start"

        ws.send_json({"type": "transcribe", "duration_ms": 1000, "pre_roll_ms": 0})
        reply = _recv(ws)
        assert reply["type"] == "transcript"
        assert reply["text"] == "texto del turno"
        assert len(model.calls) == 1


def test_late_partial_after_stop_and_transcribe_is_dropped(tmp_path):
    """Async ordering at turn end: the stop flush confirms the residual
    (finals arrive), the transcript is the LAST message of the turn —
    audio that keeps arriving schedules no partial — and the next
    utterance streams a fresh window that never re-emits the finished
    turn's text."""
    model = TagCoveringModel(gate_first=False)
    client = _streaming_app(tmp_path, model)

    with client.websocket_connect("/call/stream") as ws:
        _auth(ws)
        ws.send_json({"type": "stream", "action": "start"})
        ws.send_bytes(tagged_second(1))
        assert _recv(ws) == {"type": "partial", "text": "word1"}

        # Turn end: the flush confirms word1 (two consecutive passes
        # over [0,1s]) and the transcript follows as the last message.
        ws.send_json({"type": "stream", "action": "stop"})
        assert _drain(ws, 2) == [
            {"type": "final", "text": "word1"},
            {"type": "partial", "text": "word1"},
        ]
        ws.send_json({"type": "transcribe", "duration_ms": 1000, "pre_roll_ms": 0})
        reply = _recv(ws)
        assert reply == {"type": "transcript", "text": "word1",
                         "duration_ms": 1000.0, "pre_roll_ms": 0.0}, reply

        # Late audio after the turn end: no pass, no message.
        ws.send_bytes(tagged_second(2))
        import time

        time.sleep(0.4)
        # Utterance 2: fresh window, fresh agreement state — none of the
        # finished turn's words ever reappear. (The 500 ms onset preroll
        # may include tail audio of the unattributed late second — word2
        # — but never word1, the finished turn's own text.)
        ws.send_json({"type": "stream", "action": "start"})
        ws.send_bytes(tagged_second(3))
        partial = _recv(ws)
        assert partial == {"type": "partial", "text": "word2 word3"}, partial
        assert "word1" not in partial["text"]
        # The new window starts inside the unattributed late audio and
        # covers the new utterance's first second: [1.5s, 3s].
        assert model.calls[-1][1] == 24000


# ---- streaming session unit tests (deterministic, no websocket) ----


class _FakeSocket:
    """Captures send_json payloads; never touches a real socket."""

    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, payload):
        self.sent.append(payload)


class _SeamTranscriber:
    """Hint-capable seam double — the session's real inference is patched
    per test, so this only satisfies construction."""

    def transcribe_in_memory(self, data, suffix=".wav", language=None,
                             initial_prompt=None):
        raise AssertionError("patched away by the test")


def test_stop_flushes_inflight_pass_and_confirms_tail():
    """L19: a stop mid-inference no longer discards the in-flight pass —
    it belongs to the SAME utterance. The pass completes, its partial
    reaches the client, and the turn-end flush confirms the words with a
    second pass. Only reset_after_transcript (the true turn boundary)
    drops late results — pinned by the lock-wait test below."""
    import asyncio

    from herdr_brain.call_stream import (
        AudioRingBuffer,
        StreamingTranscriptionSession,
    )

    ring = AudioRingBuffer()
    ring.append(FRAME_100MS * 10)
    sock = _FakeSocket()
    started = asyncio.Event()
    release = asyncio.Event()

    session = StreamingTranscriptionSession(
        sock, _SeamTranscriber(), ring, language="es", initial_prompt=None
    )

    async def slow_transcribe(wav):
        started.set()
        await release.wait()
        return "turno uno"

    async def settle():
        deadline = asyncio.get_event_loop().time() + 2.0
        while session._task is not None and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.02)

    async def scenario():
        session.run_inference = slow_transcribe
        session.start_utterance()
        session._ring.append(FRAME_100MS * 10)
        session.on_audio()
        await started.wait()
        session.stop_utterance()  # the turn ends mid-inference -> flush
        release.set()
        await settle()
        await session.aclose()

    run_async(scenario())
    kinds = [(m["type"], m["text"]) for m in sock.sent]
    assert kinds == [
        ("partial", "turno uno"),   # the once-blocked pass completes
        ("final", "turno uno"),     # the flush confirms it (pass 2)
        ("partial", "turno uno"),
    ], sock.sent


def test_pass_waiting_on_the_transcribe_lock_is_dropped_before_inference():
    """A pass scheduled while the turn-end transcribe holds the inference
    lock never runs its inference once the turn is over."""
    import asyncio

    from herdr_brain.call_stream import (
        AudioRingBuffer,
        StreamingTranscriptionSession,
    )

    ring = AudioRingBuffer()
    ring.append(FRAME_100MS * 30)
    sock = _FakeSocket()

    session = StreamingTranscriptionSession(
        sock, _SeamTranscriber(), ring, language=None, initial_prompt=None
    )
    inferences: list[bytes] = []

    async def scenario():
        transcribing = asyncio.Event()

        async def turn_end_transcribe(wav):
            transcribing.set()
            await asyncio.sleep(0.05)
            return "turno final"

        session.run_inference = turn_end_transcribe
        async with session.inference_lock:
            # The turn-end inference is in flight (lock held) when new
            # audio schedules a pass; the pass waits on the lock.
            session.start_utterance()
            session._ring.append(FRAME_100MS * 10)
            session.on_audio()
            await asyncio.sleep(0.05)
            # The turn completes and resets the session.
            session.reset_after_transcript()

        async def recording(wav):
            inferences.append(wav)
            return "no deberia correr"

        session.run_inference = recording
        await asyncio.sleep(0.1)
        await session.aclose()

    run_async(scenario())
    assert inferences == [], "a stale pass ran its inference after the turn"
    assert sock.sent == []


def test_hangup_cancels_streaming_and_next_call_is_fresh(tmp_path):
    """Disconnect mid-pass ends the streaming session; a new connection
    starts from scratch and serves transcripts normally."""
    model = ScriptedStreamingModel(["turno colgado"])
    client = _streaming_app(tmp_path, model)

    with client.websocket_connect("/call/stream") as ws:
        _auth(ws)
        ws.send_json({"type": "stream", "action": "start"})
        _send_audio(ws, 1000)  # gated pass in flight
    # Hangup: the connection context exits; release the zombie thread so
    # the model's concurrency accounting settles.
    model.release()

    import time

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and model.active > 0:
        time.sleep(0.05)
    assert model.active == 0

    model2 = ScriptedStreamingModel(["nueva llamada"])
    model2.release()
    client2 = _streaming_app(tmp_path, model2)
    with client2.websocket_connect("/call/stream") as ws2:
        _auth(ws2)
        ws2.send_json({"type": "stream", "action": "start"})
        _send_audio(ws2, 1000)
        assert _recv(ws2) == {"type": "partial", "text": "nueva llamada"}


def test_locale_and_vocabulary_hint_reach_the_model(tmp_path, monkeypatch):
    """D8: the app locale from the auth message forces the transcription
    language; the operator-editable vocabulary env var becomes the
    recognition hint — on partial passes AND the turn-end transcript."""
    monkeypatch.setenv(
        "HERDR_BRAIN_LIVE_STT_VOCABULARY", "commit, push, PR, rama, herdr, opencode"
    )
    model = ScriptedStreamingModel(["abre el PR de la rama"])
    model.release()
    client = _streaming_app(tmp_path, model)

    with client.websocket_connect("/call/stream") as ws:
        _auth(ws, locale="es")
        ws.send_json({"type": "stream", "action": "start"})
        _send_audio(ws, 1000)
        assert _recv(ws)["type"] == "partial"
        assert model.calls[0][0] == "es"
        assert model.calls[0][1] == "commit push PR rama herdr opencode"

        # Turn end: the stop's flush pass and the transcript BOTH carry
        # the hints (the flush is a real inference).
        ws.send_json({"type": "stream", "action": "stop"})
        ws.send_json({"type": "transcribe", "duration_ms": 1000, "pre_roll_ms": 0})
        assert _drain(ws, 3)[-1]["type"] == "transcript"
        for call in model.calls[1:3]:
            assert call[0] == "es"
            assert call[1] == "commit push PR rama herdr opencode"


def test_default_vocabulary_hint_applies_without_env(tmp_path, monkeypatch):
    """D8: without the env override the built-in technical vocabulary is
    the hint (operator-editable default)."""
    monkeypatch.delenv("HERDR_BRAIN_LIVE_STT_VOCABULARY", raising=False)
    model = ScriptedStreamingModel(["texto"])
    model.release()
    client = _streaming_app(tmp_path, model)

    with client.websocket_connect("/call/stream") as ws:
        _auth(ws)
        ws.send_json({"type": "stream", "action": "start"})
        _send_audio(ws, 1000)
        assert _recv(ws)["type"] == "partial"
        prompt = model.calls[0][1]
        assert prompt, "no default vocabulary hint"
        for term in ("commit", "push", "PR", "rama", "herdr", "opencode"):
            assert term in prompt


def test_malicious_locale_and_vocabulary_are_bounded(tmp_path, monkeypatch):
    """Hostile inputs never reach the model: an unknown/oversized locale
    falls back to auto-detect; the hint is capped, control-character free
    and never carries injected newlines."""
    monkeypatch.setenv(
        "HERDR_BRAIN_LIVE_STT_VOCABULARY",
        "ok\n" + "x" * 500 + " \tctl " + " ".join(["t"] * 300),
    )
    model = ScriptedStreamingModel(["texto"])
    model.release()
    client = _streaming_app(tmp_path, model)

    with client.websocket_connect("/call/stream") as ws:
        _auth(ws, locale="es<img src=x onerror=alert(1)>")
        ws.send_json({"type": "stream", "action": "start"})
        _send_audio(ws, 1000)
        assert _recv(ws)["type"] == "partial"

        language, prompt, _samples = model.calls[0]
        assert language is None  # unknown locale -> whisper auto-detect
        assert prompt is not None
        assert len(prompt) <= 300
        assert "\n" not in prompt and "\t" not in prompt and "<" not in prompt


def test_streaming_never_writes_audio_files(tmp_path):
    """D9: streaming passes transcribe strictly in memory — no raw audio
    file ever lands in the temp directory."""
    import os
    import tempfile

    before_files = set(os.listdir(tempfile.gettempdir()))
    model = ScriptedStreamingModel(["sin archivos"])
    model.release()
    client = _streaming_app(tmp_path, model)

    with client.websocket_connect("/call/stream") as ws:
        _auth(ws)
        ws.send_json({"type": "stream", "action": "start"})
        _send_audio(ws, 1000)
        assert _recv(ws)["type"] == "partial"

    after_files = set(os.listdir(tempfile.gettempdir()))
    assert [
        f for f in (after_files - before_files)
        if "stt" in f or "wav" in f or "webm" in f
    ] == []


def test_streaming_failure_reports_generic_error_once_and_recovers(tmp_path):
    """L13 policy on the streaming loop: a failing pass emits the fixed
    generic error (never exception detail, never silence), the socket
    keeps serving, and a later utterance streams again."""
    class FailsThenWorks:
        def __init__(self):
            self.calls = 0

        def transcribe(self, path, language=None, initial_prompt=None):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("CUDA unavailable /secret/path key=val")
            return (
                iter([type("Seg", (), {"text": "recuperado"})]),
                type("Info", (), {"language": "es"}),
            )

    model = FailsThenWorks()
    client = _streaming_app(tmp_path, model)

    with client.websocket_connect("/call/stream") as ws:
        _auth(ws)
        ws.send_json({"type": "stream", "action": "start"})
        _send_audio(ws, 1000)
        reply = _recv(ws)
        assert reply == {"type": "error", "message": "live transcription failed"}, reply

        # The socket survives; a new utterance streams normally.
        ws.send_json({"type": "stream", "action": "start"})
        _send_audio(ws, 1000)
        assert _recv(ws) == {"type": "partial", "text": "recuperado"}


# ---- L16 correction: chronological catch-up under slow inference ----
#
# Independent verifier blocker: with the first inference blocked and six
# tagged seconds of capture, the model only ever saw tags 1 and 6 — the
# cap-slide advanced the origin using the ring's CURRENT duration (grown
# during the blocked inference) instead of the boundary the pass actually
# transcribed, silently skipping seconds 2–5, and a final was emitted
# after a single pass. The contract under test (PRD-04 FR-02/FR-04/FR-05):
# every second reaches the model in chronological order (overlap allowed,
# gaps never), finals require consecutive-pass agreement, the forced
# commit happens only at a genuinely-full window, and the turn-end
# transcript still covers the whole utterance exactly once.

TAG_UNIT = 1000  # each 1-second block carries constant samples tag*TAG_UNIT


def tagged_second(tag: int) -> bytes:
    """One second of PCM16 16 kHz mono whose every sample encodes ``tag``."""
    return struct.pack("<h", tag * TAG_UNIT) * 16000


class TagCoveringModel:
    """Deterministic tag-echo model: decodes the audio it received and
    returns one word per distinct tag block (ascending), so the tests can
    assert exactly WHICH seconds every inference covered (and, via
    ``texts``, which passes support each final word). The FIRST call can
    be gated from the test (the slow-inference probe)."""

    def __init__(self, gate_first=True):
        self.calls: list[tuple[frozenset, int]] = []
        self.texts: list[list[str]] = []
        self.active = 0
        self._lock = threading.Lock()
        self.gate = threading.Event()
        self.gate_first = gate_first

    def release(self):
        self.gate.set()

    def transcribe(self, path, language=None, initial_prompt=None):
        with self._lock:
            self.active += 1
        try:
            if self.gate_first and not self.calls:
                self.gate.wait(timeout=10)
                self.gate.set()
            data = path.read() if hasattr(path, "read") else open(path, "rb").read()
            with wave.open(io.BytesIO(data), "rb") as wav_file:
                raw = wav_file.readframes(wav_file.getnframes())
            samples = struct.unpack("<%dh" % (len(raw) // 2), raw)
            tags = frozenset(value // TAG_UNIT for value in samples if value)
            self.calls.append((tags, len(samples)))
            words = [f"word{tag}" for tag in sorted(tags)]
            self.texts.append(words)
            return (
                iter([type("Seg", (), {"text": " ".join(words)})]),
                type("Info", (), {"language": "es"}),
            )
        finally:
            with self._lock:
                self.active -= 1


def test_slow_inference_covers_all_audio_in_order(tmp_path):
    """L16+L17 regression (public WebSocket probe): a blocked first
    inference plus five more captured seconds must transcribe EVERY
    second, in order, and every final word must be supported by two
    consecutive agreeing passes — including the full-window tail."""
    import time

    model = TagCoveringModel(gate_first=True)
    client = _streaming_app(tmp_path, model)

    with client.websocket_connect("/call/stream") as ws:
        _auth(ws)
        ws.send_json({"type": "stream", "action": "start"})
        ws.send_bytes(tagged_second(1))

        # The first one-second inference is in flight (blocked) before
        # any further audio arrives — exactly the verifier's probe.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and model.active == 0:
            time.sleep(0.05)
        assert model.active == 1, "gated first inference never started"

        for tag in (2, 3, 4, 5, 6):
            ws.send_bytes(tagged_second(tag))
        model.release()

        messages = _drain(ws, 6)
        assert messages == [
            # Pass 1 ([0,1s]): nothing to agree with — partial only.
            {"type": "partial", "text": "word1"},
            # Pass 2 ([0,5s] — genuinely full window): the prefix that
            # agrees with pass 1 becomes final...
            {"type": "final", "text": "word1"},
            # ...but the window's unagreed remainder does NOT: the full
            # window parks itself for a confirmation pass (FR-02).
            {"type": "partial", "text": "word1 word2 word3 word4 word5"},
            # Pass 3 (confirmation over the same [0,5s] window): words
            # 2–5 now have two consecutive agreeing appearances and the
            # origin slides to the boundary these passes inferred.
            {"type": "final", "text": "word2 word3 word4 word5"},
            {"type": "partial", "text": "word1 word2 word3 word4 word5"},
            # Pass 4 ([5s,6s]): the fresh window's partial tail.
            {"type": "partial", "text": "word1 word2 word3 word4 word5 word6"},
        ], messages

        # Turn end (L19): the stop FLUSHES the residual <5 s window with
        # one confirming pass, so word6 — stable across passes 4 and 5 —
        # becomes final instead of vanishing from the finals.
        ws.send_json({"type": "stream", "action": "stop"})
        messages.extend(_drain(ws, 2))
        assert messages[-2:] == [
            {"type": "final", "text": "word6"},
            {"type": "partial", "text": "word1 word2 word3 word4 word5 word6"},
        ], messages[-2:]
        finals = [m["text"] for m in messages if m["type"] == "final"]
        assert finals == [
            "word1", "word2 word3 word4 word5", "word6",
        ]

        # Coverage: every second reached the model, chronologically, with
        # no skipped stretch — full windows transcribe TWICE (park +
        # confirm) and the residual confirms once at turn end.
        assert [tags for tags, _samples in model.calls] == [
            frozenset({1}),
            frozenset({1, 2, 3, 4, 5}),
            frozenset({1, 2, 3, 4, 5}),
            frozenset({6}),
            frozenset({6}),
        ], f"model coverage skipped audio: {model.calls}"

        # L17: every final word is supported by two CONSECUTIVE passes
        # whose texts both contain it at the agreed prefix position.
        assert "word1" in model.texts[0] and "word1" in model.texts[1]
        for word in ("word2", "word3", "word4", "word5"):
            assert word in model.texts[1] and word in model.texts[2]
        assert "word6" in model.texts[3] and "word6" in model.texts[4]

        # Turn end: one transcribe request, one transcript covering the
        # WHOLE utterance — and (L19) the concatenated finals EQUAL it.
        ws.send_json({"type": "transcribe", "duration_ms": 6000, "pre_roll_ms": 500})
        transcript = _drain(ws, 1)[0]
        assert transcript["type"] == "transcript", transcript
        assert transcript["text"] == "word1 word2 word3 word4 word5 word6"
        assert model.calls[5] == (frozenset({1, 2, 3, 4, 5, 6}), 96000)
        joined_finals = " ".join(finals)
        assert transcript["text"] == joined_finals, (
            f"finals parity broke: {joined_finals!r} vs {transcript['text']!r}"
        )


def test_first_full_window_needs_two_agreeing_passes_before_finals(
    tmp_path, monkeypatch
):
    """L17 regression (the verifier's exact case): when the FIRST pass
    already covers a full five-second window, nothing it returns may
    become final on that single appearance — and when the confirming
    second pass DIFFERS, only the commonly agreed prefix is final.

    The pass cadence is pinned to the window cap so the first scheduled
    pass deterministically opens a full window (in production this shape
    arises whenever capture outruns the first inference)."""
    import herdr_brain.call_stream as call_stream

    monkeypatch.setattr(call_stream, "PASS_INTERVAL_MS", 5000)
    model = ScriptedStreamingModel(
        ["word1 word2 word3 word4 word5", "word1 word2 word3 word4 wordNUEVE"]
    )
    model.release()
    client = _streaming_app(tmp_path, model)

    with client.websocket_connect("/call/stream") as ws:
        _auth(ws)
        ws.send_json({"type": "stream", "action": "start"})
        _send_audio(ws, 5000)  # the first pass's window is full at start

        messages = _drain(ws, 5)
        assert messages == [
            # First pass over the full window: display only — no final
            # from a single appearance (FR-02), the window parks itself.
            {"type": "partial", "text": "word1 word2 word3 word4 word5"},
            # Confirmation pass: only the prefix the two passes agree on
            # commits; the revised tail does not — the window re-parks.
            {"type": "final", "text": "word1 word2 word3 word4"},
            {"type": "partial", "text": "word1 word2 word3 word4 wordNUEVE"},
            # The retry pass repeats the revision: wordNUEVE now has two
            # consecutive agreeing appearances and commits (L19).
            {"type": "final", "text": "wordNUEVE"},
            {"type": "partial", "text": "word1 word2 word3 word4 wordNUEVE"},
        ], messages

        # Three passes over the same full window; nothing skipped.
        assert [c[2] for c in model.calls] == [80000, 80000, 80000]
        finals = " ".join(m["text"] for m in messages if m["type"] == "final")
        assert finals == "word1 word2 word3 word4 wordNUEVE"
        # word5 appeared in ONE pass only — it can never become final.
        assert "word5" not in finals
        # Every final word has two consecutive agreeing appearances:
        # words 1-4 in passes 1-2, wordNUEVE in passes 2-3.
        pass_texts = model.texts
        for word in ("word1", "word2", "word3", "word4"):
            assert word in pass_texts[0] and word in pass_texts[1]
        assert "wordNUEVE" in pass_texts[1] and "wordNUEVE" in pass_texts[2]


def test_differing_tail_stabilizes_into_finals_parity(tmp_path, monkeypatch):
    """L18 regression (the verifier's exact case): pass 1 reads a WRONG
    tail, pass 2 revises it, pass 3 stabilizes on the revision — the
    stabilized word must become final (two consecutive agreeing passes)
    and concatenated finals must equal the complete turn transcript."""
    import herdr_brain.call_stream as call_stream

    monkeypatch.setattr(call_stream, "PASS_INTERVAL_MS", 5000)
    model = ScriptedStreamingModel(
        ["alpha wrong", "alpha correct", "alpha correct", "alpha correct"]
    )
    model.release()
    client = _streaming_app(tmp_path, model)

    with client.websocket_connect("/call/stream") as ws:
        _auth(ws)
        ws.send_json({"type": "stream", "action": "start"})
        _send_audio(ws, 5000)

        messages = _drain(ws, 5)
        assert messages == [
            # Pass 1 over the full window: display only (park).
            {"type": "partial", "text": "alpha wrong"},
            # Pass 2 revises the tail: the agreed prefix commits, the
            # revision does NOT yet — the window re-parks for a retry.
            {"type": "final", "text": "alpha"},
            {"type": "partial", "text": "alpha correct"},
            # Pass 3 stabilizes on the revision: "correct" now has two
            # consecutive agreeing appearances and commits; the origin
            # slides past the confirmed window.
            {"type": "final", "text": "correct"},
            {"type": "partial", "text": "alpha correct"},
        ], messages

        ws.send_json({"type": "stream", "action": "stop"})
        ws.send_json({"type": "transcribe", "duration_ms": 5000, "pre_roll_ms": 0})
        transcript = _drain(ws, 1)[0]
        assert transcript["type"] == "transcript", transcript

        finals = " ".join(m["text"] for m in messages if m["type"] == "final")
        assert finals == "alpha correct"
        # FR-04 parity: concatenated finals EQUAL the complete turn.
        assert transcript["text"] == finals, (
            f"finals lost words vs the turn: {finals!r} vs {transcript['text']!r}"
        )
        assert "wrong" not in finals, "a one-appearance word became final"


def test_extended_slow_utterance_reaches_full_finals_parity(tmp_path):
    """L18 regression (extended/slow utterance): a gated first inference
    on a 12-second utterance must slide across multiple 5 s windows,
    flush the residual <5 s at turn end, and concatenate finals into
    EXACTLY the complete turn transcript — no missing word6..word12, no
    duplicates — with a single turn dispatch."""
    import time

    model = TagCoveringModel(gate_first=True)
    client = _streaming_app(tmp_path, model)

    with client.websocket_connect("/call/stream") as ws:
        _auth(ws)
        ws.send_json({"type": "stream", "action": "start"})
        ws.send_bytes(tagged_second(1))

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and model.active == 0:
            time.sleep(0.05)
        assert model.active == 1, "gated first inference never started"

        for tag in range(2, 13):
            ws.send_bytes(tagged_second(tag))
        model.release()

        messages = _drain(ws, 9)
        # Turn end: the residual [10s,12s] window flushes and confirms
        # word11–word12 (stable across the last two passes).
        ws.send_json({"type": "stream", "action": "stop"})
        messages.extend(_drain(ws, 2))
        assert messages[-2:] == [
            {"type": "final", "text": "word11 word12"},
            {"type": "partial", "text": " ".join(f"word{t}" for t in range(1, 13))},
        ], messages[-2:]
        finals = " ".join(m["text"] for m in messages if m["type"] == "final")
        assert finals == " ".join(f"word{tag}" for tag in range(1, 13)), (
            f"finals lost or duplicated words: {finals!r}"
        )
        # Every window stayed bounded (FR-05) and the coverage is
        # chronological: [0,1] gated, [0,5]+confirm, [5,10]+confirm,
        # [10,12] residual + its turn-end flush confirmation.
        samples = [c[1] for c in model.calls]
        assert all(s <= 5 * 16000 + 1600 for s in samples), samples
        assert samples == [
            16000,          # pass 1 (gated) [0,1]
            80000, 80000,   # window [0,5]: park + confirm
            80000, 80000,   # window [5,10]: park + confirm
            32000, 32000,   # residual [10,12] + its flush confirmation
        ], samples

        ws.send_json({"type": "transcribe", "duration_ms": 12000, "pre_roll_ms": 0})
        transcript = _drain(ws, 1)[0]
        assert transcript["type"] == "transcript", transcript
        assert transcript["text"] == " ".join(f"word{tag}" for tag in range(1, 13))
        assert transcript["text"] == finals, "finals parity broke on the full turn"


def test_transcript_waits_for_flush_finals(tmp_path):
    """L20 regression (verifier probe 1): a gated slow first pass with
    stop+transcribe sent IMMEDIATELY (while the inference is blocked)
    must not let the full transcript overtake the pending flush — the
    finals come first, in order, exactly once, and the transcript is
    the LAST message of the turn."""
    import time

    model = TagCoveringModel(gate_first=True)
    client = _streaming_app(tmp_path, model)

    with client.websocket_connect("/call/stream") as ws:
        _auth(ws)
        ws.send_json({"type": "stream", "action": "start"})
        ws.send_bytes(tagged_second(1))

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and model.active == 0:
            time.sleep(0.05)
        assert model.active == 1, "gated first inference never started"

        for tag in (2, 3, 4, 5, 6):
            ws.send_bytes(tagged_second(tag))
        # The turn ends while the first inference is still blocked, and
        # the transcript request lands immediately after the stop.
        ws.send_json({"type": "stream", "action": "stop"})
        ws.send_json({"type": "transcribe", "duration_ms": 6000, "pre_roll_ms": 500})
        model.release()

        messages = _drain(ws, 9)
        finals = " ".join(m["text"] for m in messages if m["type"] == "final")
        transcript = messages[-1]
        assert transcript["type"] == "transcript", messages
        # The finals PRECEDE the transcript — never overtaken by it.
        finals_idx = [i for i, m in enumerate(messages) if m["type"] == "final"]
        transcript_idx = len(messages) - 1
        assert finals_idx and max(finals_idx) < transcript_idx, messages
        # Every word of the turn is final exactly once (FR-04 parity).
        assert finals == " ".join(f"word{tag}" for tag in range(1, 7)), finals
        assert transcript["text"] == finals
        # The flush confirmed the residual before the transcript ran.
        assert [tags for tags, _s in model.calls].count(frozenset({6})) == 2


class AlternatingModel:
    """Deterministic never-stabilizing model: alternates its scripted
    variants per call — the pathological backend for the retry bound."""

    def __init__(self, variants):
        self.variants = variants
        self.calls = 0

    def release(self):  # shape parity with the other doubles
        pass

    def transcribe(self, path, language=None, initial_prompt=None):
        text = self.variants[self.calls % len(self.variants)]
        self.calls += 1
        return (
            iter([type("Seg", (), {"text": text})]),
            type("Info", (), {"language": "es"}),
        )


def test_oscillating_model_fails_closed_without_successful_transcript(
    tmp_path, monkeypatch
):
    """L20 regression (verifier probe 2): a backend that never stabilizes
    exhausts the bounded agreement retries — the turn must FAIL CLOSED
    with the generic error (the client's existing visible fallback) and
    NO successful incompatible transcript, and the unstable words must
    never appear in a final."""
    import herdr_brain.call_stream as call_stream

    monkeypatch.setattr(call_stream, "PASS_INTERVAL_MS", 5000)
    model = AlternatingModel(["alpha red", "alpha blue"])
    client = _streaming_app(tmp_path, model)

    with client.websocket_connect("/call/stream") as ws:
        _auth(ws)
        ws.send_json({"type": "stream", "action": "start"})
        _send_audio(ws, 5000)

        messages = _drain(ws, 5)
        assert messages == [
            {"type": "partial", "text": "alpha red"},
            # The prefix both variants share has two consecutive agreeing
            # appearances (passes 1-2) and commits legitimately...
            {"type": "final", "text": "alpha"},
            {"type": "partial", "text": "alpha blue"},
            {"type": "partial", "text": "alpha red"},
            # ...but the tail never stabilizes within the bound: the
            # generic fail-closed error, never a silent parity break.
            {"type": "error", "message": "live transcription failed"},
        ], messages

        # The turn end must NOT answer a successful transcript: the
        # turn failed closed (the client falls back visibly, A2/L13).
        ws.send_json({"type": "stream", "action": "stop"})
        ws.send_json({"type": "transcribe", "duration_ms": 5000, "pre_roll_ms": 0})
        assert _drain(ws, 1) == [
            {"type": "error", "message": "live transcription failed"},
        ]

        # No whole-window inference ever ran for the failed turn, and
        # the unstable words never became final.
        assert model.calls == 4, model.calls
        finals = " ".join(m["text"] for m in messages if m["type"] == "final")
        assert finals == "alpha"
        assert "red" not in finals and "blue" not in finals


def test_ring_bound_gap_is_signaled_never_silently_skipped():
    """L16 honesty rule: when the ring's bounded capacity has already
    dropped audio the origin still expects (recovery impossible), the
    session says so ONCE per utterance as a notice and resumes from the
    earliest surviving audio — it never silently skips."""
    import asyncio

    from herdr_brain.call_stream import (
        LIVE_NOTICE_AUDIO_GAP,
        AudioRingBuffer,
        StreamingTranscriptionSession,
    )

    ring = AudioRingBuffer(max_seconds=2.0)
    sock = _FakeSocket()
    session = StreamingTranscriptionSession(
        sock, _SeamTranscriber(), ring, language=None, initial_prompt=None
    )
    windows: list[int] = []

    async def recording(wav):
        with wave.open(io.BytesIO(wav), "rb") as wav_file:
            windows.append(wav_file.getnframes())
        return "texto"

    async def settle():
        deadline = asyncio.get_event_loop().time() + 2.0
        while session._task is not None and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.02)

    async def scenario():
        session.run_inference = recording
        session.start_utterance()  # origin 0, no audio yet
        ring.append(FRAME_100MS * 30)  # 3 s — the 2 s ring dropped [0,1s)
        session.on_audio()
        await settle()
        ring.append(FRAME_100MS * 10)  # +1 s — earliest is now 2 s
        session.on_audio()
        await settle()
        await session.aclose()

    run_async(scenario())

    notices = [m for m in sock.sent if m.get("type") == "notice"]
    assert notices == [{"type": "notice", "reason": LIVE_NOTICE_AUDIO_GAP}], sock.sent
    assert sock.sent[0]["type"] == "notice", "the gap notice must lead the pass"
    # Recovery: inference resumed from the earliest surviving audio
    # ([1s,3s] then [2s,4s]) — the lost stretch is disclosed, not skipped.
    assert windows == [32000, 32000], windows
    assert any(m["type"] == "partial" for m in sock.sent)


# ---- S7 (PRD-05/D10, T4): the rejoin window on the socket ----
#
# Between a turn's transcript and the next stream start the client keeps
# streaming frames on the SAME socket (the ask for the closed segment is
# in flight). The rejoin contract on the server side: the gap frames are
# retained in the ring, they trigger NO inference while no utterance is
# active (S5: partials run only while speaking), and the resumed
# utterance's transcript covers ONLY the resumed audio — the finished
# turn's words are never re-served (the CLIENT combines the texts), so
# prefix-overlap protection must hold across the rejoin boundary.

GAP_TAG = 9  # thinking-gap silence; tags 1-2 = turn one, 3-4 = resumed


def test_rejoin_gap_retains_frames_without_inference_and_transcript_excludes_turn_one(
    tmp_path,
):
    """S7/D10 server invariants through the public socket: capture stays
    accepted through the thinking gap (no GPU work, no disk), and the
    rejoined utterance's transcript window never reaches into the
    finished turn's audio."""
    import os
    import tempfile

    model = TagCoveringModel(gate_first=False)
    client = _streaming_app(tmp_path, model)
    before_files = set(os.listdir(tempfile.gettempdir()))

    with client.websocket_connect("/call/stream") as ws:
        _auth(ws)

        # Turn one: 2 s of speech, closed by stop + transcribe.
        ws.send_json({"type": "stream", "action": "start"})
        ws.send_bytes(tagged_second(1))
        ws.send_bytes(tagged_second(2))
        ws.send_json({"type": "stream", "action": "stop"})
        ws.send_json({"type": "transcribe", "duration_ms": 2000, "pre_roll_ms": 500})
        first = None
        while first is None or first["type"] != "transcript":
            first = _recv(ws)
        assert first["text"] == "word1 word2", first
        assert model.calls[-1] == (frozenset({1, 2}), 32000), model.calls[-1]

        # The thinking gap: frames keep arriving, NO utterance is active.
        calls_after_turn_one = len(model.calls)
        ws.send_bytes(tagged_second(GAP_TAG))
        import time

        time.sleep(0.4)
        assert len(model.calls) == calls_after_turn_one, \
            "inference ran during the thinking gap (no utterance active)"

        # The resume: a new utterance on the same socket, 2 s more speech.
        ws.send_json({"type": "stream", "action": "start"})
        ws.send_bytes(tagged_second(3))
        ws.send_bytes(tagged_second(4))
        ws.send_json({"type": "stream", "action": "stop"})
        ws.send_json({"type": "transcribe", "duration_ms": 2000, "pre_roll_ms": 500})
        second = None
        while second is None or second["type"] != "transcript":
            second = _recv(ws)

        # The rejoined transcript covers ONLY the resumed window: the
        # 500 ms pre-roll reaches into the retained gap silence, never
        # into the finished turn's words (the client combines texts).
        assert "word1" not in second["text"] and "word2" not in second["text"], second
        assert {"word3", "word4"} <= set(second["text"].split()), second
        tags, samples = model.calls[-1]
        assert tags == frozenset({GAP_TAG, 3, 4}), (tags, samples)
        assert samples == 40000, samples  # 2 s resumed + 500 ms retained pre-roll

    # D9 across the rejoin window: nothing ever hit the disk.
    after_files = set(os.listdir(tempfile.gettempdir()))
    new_files = [
        f for f in (after_files - before_files)
        if "stt" in f or "wav" in f or "webm" in f
    ]
    assert new_files == []


# ---- S6 bounded increment (PRD-05 FR-01/FR-06/FR-07): Silero framing ----
#
# Speech/silence framing ONLY — the semantic turn-completion detector
# (FR-02/FR-03) is NOT part of this increment. The contract here:
#
# - the client OPTS IN via {"vad": true} in the auth message; the server
#   answers exactly one {"type":"vad_status"} naming the VAD actually in
#   use ("silero", or "energy" + a reason token when the model/runtime is
#   unavailable — FR-06 fallback);
# - opted-in connections then receive {"type":"vad"} transition events
#   from a per-connection framer (per-call state isolation);
# - clients that do NOT opt in see ZERO new messages — the protocol is
#   byte-compatible with every pre-S6 client and test;
# - framing never touches the turn-end transcribe/approval path: the
#   energy endpointer semantics and every existing message stays exactly
#   as before (FR-07 hard cap untouched server-side).


class FakeContentVadEngine:
    """Engine double whose speech probability follows chunk amplitude —
    deterministic, content-driven, no onnxruntime."""

    def __init__(self, fail_load=False, fail_on_chunk=None):
        self.fail_load = fail_load
        self.fail_on_chunk = fail_on_chunk
        self.load_calls = 0
        self.framers_minted = 0
        self.unavailable_reason = "silero_vad_model_missing" if fail_load else None

    def ensure_loaded(self):
        self.load_calls += 1
        return not self.fail_load

    def new_framer(self):
        from herdr_brain.stream_vad import VadSpeechFramer

        self.framers_minted += 1
        engine = self

        def step(chunk, context, h, c):
            if engine.fail_on_chunk is not None:
                engine.fail_on_chunk -= 1
                if engine.fail_on_chunk < 0:
                    raise RuntimeError("session died mid-call")
            rms = float(
                np.sqrt(np.mean(chunk.astype(np.float64) ** 2))
            )
            return (0.9 if rms > 0.1 else 0.02), h, c

        return VadSpeechFramer(step)


LOUD_100MS = (np.sin(np.arange(1600) * 0.05) * 22000).astype("<i2").tobytes()
SILENT_100MS = b"\x00\x00" * 1600


def _vad_app(tmp_path, vad_engine):
    cfg = _stream_cfg(tmp_path)
    live = Transcriber(cfg, loader=lambda: SpyModel(), cached=lambda: True)
    live.state = STATE_READY
    return TestClient(
        create_app(
            settings=cfg, version="test",
            transcriber=make_server_spy_transcriber(cfg), live_transcriber=live,
            vad_engine=vad_engine,
        )
    )


def _auth_vad(ws):
    ws.send_json({"type": "auth", "token": "", "vad": True})
    assert ws.receive_json()["type"] == "auth_ok"


def test_vad_opt_in_negotiates_silero_and_streams_framing_events(tmp_path):
    """FR-01: an opted-in connection gets vad_status naming silero, then
    per-connection vad events: speech_start on speech, speech_end with the
    measured silence — and the turn-end transcribe keeps working exactly
    as before on the same socket."""
    engine = FakeContentVadEngine()
    client = _vad_app(tmp_path, engine)

    with client.websocket_connect("/call/stream") as ws:
        _auth_vad(ws)
        status = ws.receive_json()
        assert status == {"type": "vad_status", "engine": "silero", "reason": None}

        # 3 chunks of loud speech: onset (2-chunk debounce) -> speech_start
        for _ in range(3):
            ws.send_bytes(LOUD_100MS)
        ev = _recv(ws)
        assert ev["type"] == "vad" and ev["event"] == "speech_start", ev

        # ~700 ms of silence crosses the 500 ms offset bound -> speech_end
        for _ in range(7):
            ws.send_bytes(SILENT_100MS)
        ev = _recv(ws)
        assert ev["type"] == "vad" and ev["event"] == "speech_end", ev
        assert ev["silence_ms"] >= 500

        # The turn-end path is untouched by framing: transcribe still
        # answers the ordinary transcript shape.
        ws.send_json({"type": "transcribe", "duration_ms": 1000, "pre_roll_ms": 0})
        reply = ws.receive_json()
        assert reply["type"] in ("transcript", "final")
        assert reply["text"] == "primera palabra detectada"

    assert engine.framers_minted == 1  # one framer per connection


def test_vad_without_opt_in_sends_no_new_messages(tmp_path):
    """Protocol preservation: a client that does not ask for framing sees
    zero vad_status/vad messages — the pre-S6 socket, byte for byte."""
    engine = FakeContentVadEngine()
    client = _vad_app(tmp_path, engine)

    with client.websocket_connect("/call/stream") as ws:
        ws.send_json({"type": "auth", "token": ""})  # no "vad" field
        assert ws.receive_json()["type"] == "auth_ok"

        ws.send_bytes(LOUD_100MS * 3)
        # The very next message is the pong — nothing was queued in front.
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}

    assert engine.framers_minted == 0
    assert engine.load_calls == 0  # the engine is never even resolved


def test_vad_unavailable_engine_reports_energy_and_keeps_serving(tmp_path):
    """FR-06: a model/runtime that cannot load fails closed to the energy
    path: vad_status names energy with a reason token, no vad events ever
    arrive, and the socket's turn semantics are untouched."""
    engine = FakeContentVadEngine(fail_load=True)
    client = _vad_app(tmp_path, engine)

    with client.websocket_connect("/call/stream") as ws:
        _auth_vad(ws)
        status = ws.receive_json()
        assert status["type"] == "vad_status"
        assert status["engine"] == "energy"
        assert status["reason"] == "silero_vad_model_missing"

        ws.send_bytes(LOUD_100MS * 5)
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}  # nothing queued
        ws.send_json({"type": "transcribe", "duration_ms": 100, "pre_roll_ms": 0})
        reply = ws.receive_json()
        assert reply["type"] in ("transcript", "final")


def test_vad_runtime_failure_degrades_to_energy_without_killing_socket(tmp_path):
    """FR-06 mid-call: an inference that starts raising fails closed to
    ONE vad unavailable event; framing stops, and the socket keeps its
    exact turn semantics (the client's energy endpointer is then the only
    opinion — never a dead call)."""
    engine = FakeContentVadEngine(fail_on_chunk=2)
    client = _vad_app(tmp_path, engine)

    with client.websocket_connect("/call/stream") as ws:
        _auth_vad(ws)
        assert ws.receive_json()["engine"] == "silero"

        ws.send_bytes(LOUD_100MS * 5)
        ev = _recv(ws)
        assert ev["event"] == "unavailable", ev

        # No further framing, socket healthy, turn path intact.
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}
        ws.send_json({"type": "transcribe", "duration_ms": 100, "pre_roll_ms": 0})
        assert ws.receive_json()["type"] in ("transcript", "final")


def test_vad_framers_are_isolated_per_connection(tmp_path):
    """Per-call state isolation over the public socket: two simultaneous
    connections get independent framers — speech on one never produces
    events on the other."""
    engine = FakeContentVadEngine()
    client = _vad_app(tmp_path, engine)

    with client.websocket_connect("/call/stream") as ws1:
        _auth_vad(ws1)
        assert ws1.receive_json()["engine"] == "silero"
        with client.websocket_connect("/call/stream") as ws2:
            _auth_vad(ws2)
            assert ws2.receive_json()["engine"] == "silero"

            # speech on ws1 only
            for _ in range(3):
                ws1.send_bytes(LOUD_100MS)
            assert _recv(ws1)["event"] == "speech_start"

            # ws2 heard only silence: its next message is a pong, not an event
            ws2.send_bytes(SILENT_100MS * 3)
            ws2.send_json({"type": "ping"})
            assert ws2.receive_json() == {"type": "pong"}

    assert engine.framers_minted == 2


def test_vad_enabled_connection_writes_no_files(tmp_path):
    """D9/no-disk: a framing-enabled connection never persists audio or
    VAD state anywhere on disk."""
    import os
    import tempfile

    before = set(os.listdir(tempfile.gettempdir()))
    engine = FakeContentVadEngine()
    client = _vad_app(tmp_path, engine)

    with client.websocket_connect("/call/stream") as ws:
        _auth_vad(ws)
        assert ws.receive_json()["engine"] == "silero"
        for _ in range(6):
            ws.send_bytes(LOUD_100MS)
        for _ in range(6):
            ws.send_bytes(SILENT_100MS)
        ws.send_json({"type": "transcribe", "duration_ms": 100, "pre_roll_ms": 0})
        # vad framing events interleave ahead of the transcript: drain them.
        for _ in range(20):
            kind = ws.receive_json()["type"]
            if kind == "transcript":
                break
            assert kind in ("vad", "partial", "final"), kind
        else:
            raise AssertionError("no transcript arrived")

    after = set(os.listdir(tempfile.gettempdir()))
    assert [
        f for f in (after - before) if "stt" in f or "wav" in f or "webm" in f
    ] == []


def test_vad_status_uses_one_load_per_process_and_no_engine_injected(tmp_path):
    """Lazy engine resolution: an app built without an injected engine
    still answers vad_status (loading the real model lazily ONCE) —
    pinned here with the real engine's availability contract only; the
    actual onnxruntime load path is exercised by the offline smoke."""
    from herdr_brain.stream_vad import SileroVadEngine

    cfg = _stream_cfg(tmp_path)
    live = Transcriber(cfg, loader=lambda: SpyModel(), cached=lambda: True)
    live.state = STATE_READY
    app = create_app(settings=cfg, version="test", live_transcriber=live)
    client = TestClient(app)

    with client.websocket_connect("/call/stream") as ws:
        _auth_vad(ws)
        status = ws.receive_json()
        assert status["type"] == "vad_status"
        # On this venv the model + runtime exist: the real answer is silero.
        # (A machine without onnxruntime must answer energy + reason —
        # both are valid FR-06 outcomes; what is pinned is that the
        # status ALWAYS arrives and always names a real engine.)
        assert status["engine"] in ("silero", "energy")
        if status["engine"] == "energy":
            assert isinstance(status["reason"], str) and status["reason"]
        else:
            assert SileroVadEngine().ensure_loaded() is True





# ---- Silent-window guard (live-call defect: Whisper echoes its vocabulary
# hint / hallucinates on silence) ----
#
# Evidence: 1 s of digital silence transcribed as the live vocabulary hint
# and very low noise as "you". The server must never run inference on a
# window that carries no speech-level energy — neither the streaming passes
# nor the turn-end transcribe. Threshold rationale lives in
# call_stream.SILENCE_FRAME_RMS_THRESHOLD.


def _tone_100ms(amplitude: float) -> bytes:
    return (np.sin(np.arange(1600) * 0.2) * amplitude).astype("<i2").tobytes()


def _noise_100ms(amplitude: int, seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    return rng.integers(-amplitude, amplitude + 1, 1600).astype("<i2").tobytes()


def _settle(seconds=0.5):
    import time

    time.sleep(seconds)


def _ping_barrier(ws):
    """Everything the server queued before this ping arrives before pong."""
    ws.send_json({"type": "ping"})
    seen = []
    while True:
        msg = _recv(ws)
        if msg["type"] == "pong":
            return seen
        seen.append(msg)


@pytest.mark.parametrize(
    "silent_100ms",
    [
        b"\x00\x00" * 1600,
        _noise_100ms(3, seed=1),
        _noise_100ms(40, seed=2),
    ],
    ids=["digital-silence", "lsb-noise", "room-noise-40"],
)
def test_silent_window_never_reaches_the_model_nor_emits_text(tmp_path, silent_100ms):
    """Streaming passes AND the turn-end transcribe: a silent window runs
    no inference, emits no partial/final, and the turn-end answers an
    EMPTY transcript so the client keeps its not-understood path."""
    model = ScriptedStreamingModel(["PR rama herdr opencode"])
    model.release()
    client = _streaming_app(tmp_path, model)

    with client.websocket_connect("/call/stream") as ws:
        _auth(ws)
        ws.send_json({"type": "stream", "action": "start"})
        for _ in range(15):
            ws.send_bytes(silent_100ms)
        _settle()
        assert _ping_barrier(ws) == [], "text was emitted for a silent window"
        assert model.calls == [], "a streaming pass ran inference on silence"

        ws.send_json({"type": "stream", "action": "stop"})
        ws.send_json({"type": "transcribe", "duration_ms": 1000, "pre_roll_ms": 500})
        reply = _recv(ws)
        assert reply["type"] == "transcript", reply
        assert reply["text"] == ""
        assert model.calls == [], "the turn-end transcribe ran inference on silence"


def test_silent_turn_end_without_streaming_answers_empty_transcript(tmp_path):
    model = ScriptedStreamingModel(["PR rama herdr opencode"])
    model.release()
    client = _streaming_app(tmp_path, model)

    with client.websocket_connect("/call/stream") as ws:
        _auth(ws)
        for _ in range(15):
            ws.send_bytes(b"\x00\x00" * 1600)
        ws.send_json({"type": "transcribe", "duration_ms": 1000, "pre_roll_ms": 500})
        reply = _recv(ws)
        assert reply["type"] == "transcript" and reply["text"] == "", reply
        assert model.calls == []


def test_window_shorter_than_the_minimum_never_reaches_the_model(tmp_path):
    """Even loud audio is skipped below the minimum length: a sliver cannot
    carry a transcribable word and invites hallucination."""
    model = ScriptedStreamingModel(["texto"])
    model.release()
    client = _streaming_app(tmp_path, model)

    with client.websocket_connect("/call/stream") as ws:
        _auth(ws)
        ws.send_bytes(_tone_100ms(3000) * 2)  # 200 ms
        ws.send_json({"type": "transcribe", "duration_ms": 100, "pre_roll_ms": 0})
        reply = _recv(ws)
        assert reply["type"] == "transcript" and reply["text"] == "", reply
        assert model.calls == []


def test_speech_window_still_reaches_the_model_in_passes_and_turn_end(tmp_path):
    """The guard must not eat real speech: a speech-level window produces
    partials through the streaming pass and a normal turn-end transcript."""
    model = ScriptedStreamingModel(["hola que tal"])
    model.release()
    client = _streaming_app(tmp_path, model)

    with client.websocket_connect("/call/stream") as ws:
        _auth(ws)
        ws.send_json({"type": "stream", "action": "start"})
        for _ in range(15):
            ws.send_bytes(_tone_100ms(3000))
        partial = _recv(ws)
        assert partial["type"] == "partial" and partial["text"] == "hola que tal", partial
        pass_calls = len(model.calls)
        assert pass_calls >= 1

        ws.send_json({"type": "stream", "action": "stop"})
        ws.send_json({"type": "transcribe", "duration_ms": 1000, "pre_roll_ms": 500})
        reply = None
        while reply is None or reply["type"] != "transcript":
            reply = _recv(ws)
        assert reply["text"] == "hola que tal"
        assert len(model.calls) > pass_calls, "turn-end transcribe never reached the model"


def test_quiet_speech_still_passes_the_guard(tmp_path):
    """Real quiet speech (a whisper/far-field phone, ~ -43 dBFS RMS) stays
    well above the threshold and must reach the model at the turn end."""
    model = ScriptedStreamingModel(["en voz baja"])
    model.release()
    client = _streaming_app(tmp_path, model)

    with client.websocket_connect("/call/stream") as ws:
        _auth(ws)
        for _ in range(10):
            ws.send_bytes(_tone_100ms(350))  # RMS ≈ 247
        ws.send_json({"type": "transcribe", "duration_ms": 1000, "pre_roll_ms": 500})
        reply = _recv(ws)
        assert reply["type"] == "transcript" and reply["text"] == "en voz baja", reply
        assert len(model.calls) == 1


def test_short_utterance_inside_long_silence_still_reaches_the_model(tmp_path):
    """The energy test is per short frame: a brief word inside seconds of
    silence must not be diluted below the threshold by the silent rest."""
    model = ScriptedStreamingModel(["sí"])
    model.release()
    client = _streaming_app(tmp_path, model)

    with client.websocket_connect("/call/stream") as ws:
        _auth(ws)
        for _ in range(40):
            ws.send_bytes(b"\x00\x00" * 1600)
        ws.send_bytes(_tone_100ms(3000) * 3)  # 300 ms of speech
        ws.send_json({"type": "transcribe", "duration_ms": 4300, "pre_roll_ms": 500})
        reply = _recv(ws)
        assert reply["type"] == "transcript" and reply["text"] == "sí", reply
        assert len(model.calls) == 1


def test_leading_silence_then_speech_streams_after_the_window_fills(tmp_path):
    """A full silent window (cap) slides the origin instead of wedging the
    loop: speech that follows >5 s of silence is still transcribed."""
    model = ScriptedStreamingModel(["hola después"])
    model.release()
    client = _streaming_app(tmp_path, model)

    with client.websocket_connect("/call/stream") as ws:
        _auth(ws)
        ws.send_json({"type": "stream", "action": "start"})
        for _ in range(70):  # 7 s of silence
            ws.send_bytes(b"\x00\x00" * 1600)
        _settle()
        assert model.calls == []
        for _ in range(15):
            ws.send_bytes(_tone_100ms(3000))
        msgs = []
        for _ in range(4):
            msgs.append(_recv(ws))
            if any(m["type"] == "partial" for m in msgs):
                break
        assert any(m["type"] == "partial" for m in msgs), msgs
        assert len(model.calls) >= 1


# ---- S6/FR-02: local semantic turn completion (advisory only) ----
#
# The detector is audio-native and LOCAL (D18), but its verdict is
# ADVISORY: the client's energy endpointer owns every turn close (D3).
# The seam emits exactly one {"type":"vad","event":"turn_complete",...}
# at the client's ``stream stop`` when enabled; a disabled connection sees
# zero new messages and its Silero framing stays byte-identical.


import asyncio


class _ScriptedStreamSocket:
    """Minimal stand-in for ``handle_call_stream``'s WebSocket surface."""

    def __init__(self, messages):
        self._messages = list(messages)
        self.sent = []
        self.closed = False

    async def accept(self):
        return None

    async def receive(self):
        if self._messages:
            message = self._messages.pop(0)
            if "type" not in message:
                # Starlette tags every non-disconnect frame this way.
                message = {**message, "type": "websocket.receive"}
            return message
        return {"type": "websocket.disconnect"}

    async def send_json(self, payload):
        self.sent.append(payload)

    async def close(self, code=1000):
        self.closed = True


class _FakeTurnDetector:
    def __init__(self, result=(0.9, "complete", None), loaded=True):
        self.result = result
        self.loaded = loaded
        self.load_calls = 0
        self.audios = []
        self.unavailable_reason = (
            result[2] if not loaded else None
        )

    def ensure_loaded(self):
        self.load_calls += 1
        return self.loaded

    def classify(self, audio):
        self.audios.append(audio)
        return self.result


def _stream_messages(*, vad):
    auth = '{"type":"auth","token":""}'
    if vad:
        auth = '{"type":"auth","token":"","vad":true}'
    return [
        {"text": auth},
        {"bytes": LOUD_100MS},
        {"bytes": LOUD_100MS},
        {"bytes": LOUD_100MS},
        {"text": '{"type":"stream","action":"start"}'},
        {"bytes": LOUD_100MS},
    ]


def test_smart_turn_disabled_sends_no_advisory_and_leaves_framing_unchanged(
    monkeypatch,
):
    """Default OFF: no turn_complete/unavailable message is ever emitted,
    while the opted-in Silero framing still emits its exact events."""
    import herdr_brain.call_stream as call_stream

    monkeypatch.setattr(call_stream, "default_turn_detector", lambda: None)
    engine = FakeContentVadEngine()
    messages = _stream_messages(vad=True) + [
        {"bytes": SILENT_100MS},
        {"bytes": SILENT_100MS},
        {"bytes": SILENT_100MS},
        {"bytes": SILENT_100MS},
        {"bytes": SILENT_100MS},
        {"bytes": SILENT_100MS},
        {"text": '{"type":"stream","action":"stop"}'},
        {"type": "websocket.disconnect"},
    ]
    sock = _ScriptedStreamSocket(messages)

    run_async(
        call_stream.handle_call_stream(sock, None, None, vad_engine=engine)
    )

    assert sock.sent[0] == {"type": "auth_ok"}
    assert sock.sent[1] == {
        "type": "vad_status",
        "engine": "silero",
        "reason": None,
    }
    events = [m for m in sock.sent if m.get("type") == "vad"]
    assert any(m.get("event") == "speech_start" for m in events), events
    assert any(m.get("event") == "speech_end" for m in events), events
    assert not any(
        m.get("event") in ("turn_complete", "unavailable") for m in events
    ), events
    assert engine.framers_minted == 1
    assert sock.closed is False


def test_smart_turn_enabled_emits_advisory_and_never_closes_the_turn():
    """An enabled detector emits one advisory verdict at the client's
    silence close. It sends no transcript and closes nothing (D3)."""
    import herdr_brain.call_stream as call_stream

    detector = _FakeTurnDetector(result=(0.93, "complete", None))
    messages = _stream_messages(vad=False) + [
        {"text": '{"type":"stream","action":"stop"}'},
        {"type": "websocket.disconnect"},
    ]
    sock = _ScriptedStreamSocket(messages)

    run_async(
        call_stream.handle_call_stream(
            sock, None, None, turn_detector=detector
        )
    )

    assert detector.load_calls == 1
    assert len(detector.audios) == 1
    assert isinstance(detector.audios[0], np.ndarray)
    assert detector.audios[0].dtype == np.float32
    assert detector.audios[0].size > 0

    advisories = [
        m for m in sock.sent if m.get("event") == "turn_complete"
    ]
    assert advisories == [
        {
            "type": "vad",
            "event": "turn_complete",
            "verdict": "complete",
            "prob": 0.93,
        }
    ]
    assert not any(m.get("type") in ("transcript", "error") for m in sock.sent)
    assert sock.closed is False


def test_smart_turn_unavailable_at_load_emits_once_and_never_classifies():
    """An enabled but unavailable model reports one bounded reason and
    otherwise behaves exactly like today (no verdict, no turn change)."""
    import herdr_brain.call_stream as call_stream
    from herdr_brain import turn_detector as turn_detector_module

    detector = _FakeTurnDetector(loaded=False)
    detector.unavailable_reason = turn_detector_module.UNAVAILABLE_MODEL_MISSING
    messages = _stream_messages(vad=False) + [
        {"text": '{"type":"stream","action":"stop"}'},
        {"type": "websocket.disconnect"},
    ]
    sock = _ScriptedStreamSocket(messages)

    run_async(
        call_stream.handle_call_stream(
            sock, None, None, turn_detector=detector
        )
    )

    unavailable = [m for m in sock.sent if m.get("event") == "unavailable"]
    assert unavailable == [
        {
            "type": "vad",
            "event": "unavailable",
            "reason": turn_detector_module.UNAVAILABLE_MODEL_MISSING,
        }
    ]
    assert detector.audios == []
    assert not any(m.get("event") == "turn_complete" for m in sock.sent)
    assert sock.closed is False
