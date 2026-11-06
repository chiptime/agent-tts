"""Tests for WebSocket /call/stream endpoint (PRD-03 Task T1)."""

import io
import wave
import pytest
from fastapi.testclient import TestClient

from herdr_brain.config import Settings
from herdr_brain.pairing import PairingStore
from herdr_brain.server import create_app
from herdr_brain.stt import STATE_READY, Transcriber
from tests.conftest import SETTINGS_KWARGS


class SpyModel:
    """faster-whisper model double capturing the exact WAV path and samples."""

    last_wav_bytes: bytes | None = None
    last_sample_count: int | None = None
    transcribe_calls: int = 0

    def transcribe(self, path, language=None):
        SpyModel.transcribe_calls += 1
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

    def transcribe(self, path, language=None):
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
        frame_100ms = b"\x01\x00" * 1600
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
        ws.send_bytes(b"\x01\x00" * 1600)
        ws.send_json({"type": "transcribe", "duration_ms": 100, "pre_roll_ms": 0})
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

        ws.send_bytes(b"\x01\x00" * 1600)
        ws.send_json({"type": "transcribe", "duration_ms": 100, "pre_roll_ms": 0})
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
        ws.send_bytes(b"\x01\x00" * 1600)
        ws.send_json({"type": "transcribe", "duration_ms": 100, "pre_roll_ms": 0})
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
        def transcribe(self, path, language=None):
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

        ws.send_bytes(b"\x01\x00" * 1600)
        ws.send_json({"type": "transcribe", "duration_ms": 100, "pre_roll_ms": 0})
        assert ws.receive_json() == {"type": "notice", "reason": LIVE_NOTICE_OOM}
        reply = ws.receive_json()
        assert reply["type"] in ("transcript", "final")
        assert reply["text"] == "primera palabra detectada"  # retried on the fallback

        # Second round: no second notice, transcript straight away.
        ws.send_json({"type": "transcribe", "duration_ms": 100, "pre_roll_ms": 0})
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


