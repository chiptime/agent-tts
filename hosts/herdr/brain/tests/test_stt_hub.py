"""The hub backend delegates STT without loading a local model."""

import json

import pytest
from fastapi.testclient import TestClient

from herdr_brain.config import load_settings


def test_hub_is_the_default_even_with_legacy_live_model_settings():
    settings = load_settings({
        "HERDR_TTS_HOME": "/tmp/herdr-tts-surface",
        "HERDR_BRAIN_LIVE_STT_MODEL": "/some/local/model",
    })
    assert settings.stt_backend == "hub"


def test_hub_transcribes_independent_audio_windows_and_prompt(monkeypatch):
    from herdr_brain.stt_hub import HubTranscriber

    sent = []

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def send(self, data):
            sent.append(data)

        def recv(self, timeout):
            if len(sent) == 1 and isinstance(sent[0], str):
                return json.dumps({"type": "ack"})
            return json.dumps({"type": "partial", "text": "  hello world  "})

    connections = []

    def connect(url, **kwargs):
        connections.append((url, kwargs))
        return Socket()

    monkeypatch.setattr("herdr_brain.stt_hub.connect", connect)
    monkeypatch.setattr("herdr_brain.stt_hub._SUPPORTS_SYNC_KEEPALIVE", True)
    t = HubTranscriber()
    assert t.transcribe_in_memory(b"a" * 120, initial_prompt="vocabulary") == "hello world"
    assert json.loads(sent[0]) == {"type": "initial_prompt", "text": "vocabulary"}
    assert sent[1] == b"a" * 120
    sent.clear()
    assert t.transcribe_bytes(b"b" * 120) == "hello world"
    assert sent == [b"b" * 120]
    assert len(connections) == 2
    assert all(options["ping_interval"] is None for _, options in connections)


def test_hub_sends_per_connection_language(monkeypatch):
    """The per-call locale (D8) reaches the hub on the wire."""
    from herdr_brain.stt_hub import HubTranscriber

    sent = []

    class Socket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def send(self, data): sent.append(data)
        def recv(self, timeout):
            if len(sent) == 1 and isinstance(sent[0], str):
                return json.dumps({"type": "ack", "language": "en"})
            return json.dumps({"type": "partial", "text": "hello"})

    monkeypatch.setattr("herdr_brain.stt_hub.connect", lambda *a, **kw: Socket())

    t = HubTranscriber()
    # With a prompt: the language rides the initial_prompt message.
    assert t.transcribe_in_memory(b"a" * 120, language="en", initial_prompt="vocabulary") == "hello"
    assert json.loads(sent[0]) == {
        "type": "initial_prompt", "text": "vocabulary", "language": "en"}
    assert sent[1] == b"a" * 120
    sent.clear()
    # Language alone: a dedicated config message before the audio.
    assert t.transcribe_in_memory(b"b" * 120, language="en") == "hello"
    assert json.loads(sent[0]) == {"type": "config", "language": "en"}
    assert sent[1] == b"b" * 120
    sent.clear()
    # No language: the legacy wire format, byte for byte.
    assert t.transcribe_in_memory(b"c" * 120, language=None, initial_prompt="vocabulary") == "hello"
    assert json.loads(sent[0]) == {"type": "initial_prompt", "text": "vocabulary"}


def test_hub_pull_does_not_load_or_download_local_model(monkeypatch, capsys):
    from herdr_brain import stt

    settings = load_settings({"HERDR_TTS_HOME": "/tmp/herdr-tts-surface"})
    monkeypatch.setattr(stt, "load_settings", lambda: settings)
    monkeypatch.setattr(stt, "Transcriber", lambda *_: pytest.fail("local model loaded"))
    assert stt.main(["pull"]) == 0
    assert "no local model was downloaded" in capsys.readouterr().out


def test_hub_rejects_ignored_short_audio_and_invalid_responses(monkeypatch):
    from herdr_brain.stt_hub import HubTranscriber

    t = HubTranscriber()
    with pytest.raises(ValueError):
        t.transcribe_bytes(b"short")

    class Socket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def send(self, data): pass
        def recv(self, timeout): return '{"type":"partial","text":null}'

    monkeypatch.setattr("herdr_brain.stt_hub.connect", lambda *a, **kw: Socket())
    with pytest.raises(ValueError):
        t.transcribe_bytes(b"a" * 120)


def test_default_app_uses_hub_for_batch_and_live_without_local_models(settings, tmp_path, monkeypatch):
    from dataclasses import replace
    import herdr_brain.call_stream as streaming
    import herdr_brain.server as server
    import herdr_brain.stt_hub as hub

    class FakeHub:
        state = "ready"
        def __init__(self): self.calls = []
        def maybe_start_warmup(self): pass
        def transcribe_bytes(self, data, suffix=".webm"):
            self.calls.append(data)
            return "batch result"
        def transcribe_in_memory(self, data, suffix=".wav", language=None, initial_prompt=None):
            self.calls.append(data)
            return "live result"

    fake = FakeHub()
    monkeypatch.setattr(hub, "HubTranscriber", lambda *_: fake)
    monkeypatch.setattr(server, "LiveTranscriber", lambda *_: pytest.fail("local STT loaded"))
    monkeypatch.setattr(streaming, "SileroVadEngine", lambda: pytest.fail("local VAD loaded"))
    monkeypatch.setattr(streaming, "default_turn_detector", lambda: pytest.fail("local turn model loaded"))
    config = replace(settings, stt_backend="hub", stt_warmup=False, state_dir=tmp_path)
    client = TestClient(server.create_app(settings=config, version="test"))
    response = client.post("/transcribe", files={"audio": ("audio.webm", b"x" * 120)})
    assert response.status_code == 200
    assert response.json() == {"text": "batch result"}
    with client.websocket_connect("/call/stream") as ws:
        ws.send_json({"type": "auth", "vad": True})
        assert ws.receive_json()["type"] == "auth_ok"
        # Field-level assertions: the payload grows with unrelated features
        # (smart_turn rides the same vad_status message); this test pins the
        # hub-mode framing guarantee only — energy fallback with the
        # local-models-disabled reason, never a local Silero load.
        status = ws.receive_json()
        assert status["type"] == "vad_status"
        assert status["engine"] == "energy"
        assert status["reason"] == "local_models_disabled"
        ws.send_bytes(b"\xe8\x03" * 6400)
        ws.send_json({"type": "transcribe", "duration_ms": 400, "pre_roll_ms": 0})
        assert ws.receive_json()["text"] == "live result"
    assert len(fake.calls) == 2


def test_hub_probe_runs_even_when_local_warmup_is_disabled(settings, monkeypatch):
    from dataclasses import replace
    import herdr_brain.server as server
    import herdr_brain.stt_hub as hub

    calls = []

    class FakeHub:
        state = "ready"
        def maybe_start_warmup(self): calls.append("probe")

    monkeypatch.setattr(hub, "HubTranscriber", lambda *_: FakeHub())
    server.create_app(settings=replace(settings, stt_backend="hub", stt_warmup=False))
    assert calls == ["probe"]


def test_older_websockets_omits_keepalive_option(monkeypatch):
    from herdr_brain import stt_hub

    options = []

    class Socket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def send(self, data): pass
        def recv(self, timeout): return '{"type":"partial","text":"ok"}'

    monkeypatch.setattr(stt_hub, "_SUPPORTS_SYNC_KEEPALIVE", False)
    monkeypatch.setattr(stt_hub, "connect", lambda url, **kw: (options.append(kw) or Socket()))
    assert stt_hub.HubTranscriber().transcribe_bytes(b"a" * 120) == "ok"
    assert "ping_interval" not in options[0]
