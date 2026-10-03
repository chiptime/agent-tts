"""Speech cancel endpoint tests (voice-stack VS1.3): honest idempotent
cancel, mid-render abort through the cancellable renderer seam, no
enumeration of unknown vs expired ids, and capability-token hygiene
(constant-time compare, never logged, never echoed). LLM and TTS are
faked; the registry is the real one injected through create_app."""

from __future__ import annotations

import hmac
import inspect
import logging
import threading
import traceback
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from herdr_brain.config import Settings
from herdr_brain.server import create_app
from herdr_brain.speech import (
    PHASE_CANCELLED,
    PHASE_COMPLETE,
    SEGMENTED_UNAVAILABLE,
    SpeechRegistry,
)
from herdr_brain.tts import TTSError
from tests.conftest import SETTINGS_KWARGS

TOKEN = "cap-token-abc123"
SPEECH_ID = "vs1.job.alpha"
EXPIRED_ID = "vs1.job.gone0"
ANSWER = "Estás en la fase 2 del brain; todo verde."


class FakeTTS:
    """Classic 3-arg renderer double (no cancel modeling)."""

    def __init__(self):
        self.calls: list = []

    def __call__(self, settings, text, out_path: Path) -> Path:
        self.calls.append({"text": text, "out": out_path})
        out_path.write_bytes(b"ID3-fake")
        return out_path


class HangingCancellableTTS:
    """Marker fake for the cancellable seam: blocks mid-render until the
    job's cancel_event fires, then aborts exactly like the production
    render_mp3_cancellable (TTSError("cancelled"), no file written)."""

    takes_cancel_event = True

    def __init__(self):
        self.started = threading.Event()
        self.calls: list = []

    def __call__(self, settings, text, out_path: Path, cancel_event) -> Path:
        self.calls.append({"text": text, "out": out_path})
        self.started.set()
        cancel_event.wait(timeout=10)
        raise TTSError("cancelled")


class FakeLLM:
    def __init__(self, on_ask=None):
        self.on_ask = on_ask
        self.calls: list = []

    def attach_store(self, store):
        pass

    def attach_approval_store(self, store):
        pass

    def ask(self, text, session_id=None, pane_id=None):
        self.calls.append(text)
        if self.on_ask is not None:
            self.on_ask()
        return {
            "answer": ANSWER,
            "pane_id": "w1:p9",
            "agent": "opencode",
            "session_id": session_id or "default",
            "approval": None,
        }


@pytest.fixture
def audio_dir(tmp_path) -> Path:
    # Beside the audio dir sits the history DB, so tmp_path isolates it.
    return tmp_path / "audio"


def make_app(llm, tts, registry, audio_dir) -> object:
    cfg = Settings(**{**SETTINGS_KWARGS, "audio_dir": str(audio_dir)})
    return create_app(
        settings=cfg,
        llm_factory=lambda _cfg, _tools: llm,
        tts_renderer=tts,
        speech_registry=registry,
    )


def make_client(llm, tts, registry, audio_dir) -> TestClient:
    return TestClient(make_app(llm, tts, registry, audio_dir))


def speech_body(**overrides) -> dict:
    body = {
        "text": "¿Cómo va todo?",
        "session_id": "s1",
        "speech_request_id": SPEECH_ID,
        "speech_cancel_token": TOKEN,
    }
    body.update(overrides)
    return body


def cancel_body(**overrides) -> dict:
    body = {"session_id": "s1", "speech_cancel_token": TOKEN}
    body.update(overrides)
    return body


def run_ask_concurrently(app, result: dict, body: dict = None) -> threading.Thread:
    """Drives /ask on its own thread (its render blocks on the fake)."""
    ask_client = TestClient(app)

    def _post():
        result["resp"] = ask_client.post("/ask", json=body or speech_body())

    thread = threading.Thread(target=_post)
    thread.start()
    return thread


def test_cancel_idempotent_terminal_honest(audio_dir):
    registry = SpeechRegistry()
    tts = HangingCancellableTTS()
    app = make_app(FakeLLM(), tts, registry, audio_dir)
    cancel_client = TestClient(app)
    url = f"/speech/{SPEECH_ID}/cancel"

    result: dict = {}
    thread = run_ask_concurrently(app, result)
    try:
        assert tts.started.wait(timeout=10)  # render is in flight
        first = cancel_client.post(url, json=cancel_body())
        assert first.status_code == 200
        assert first.json() == {"status": "cancelled"}
    finally:
        thread.join(timeout=10)
    assert not thread.is_alive()

    # The aborted turn: text intact, no audio, job terminal-cancelled.
    # The session stub is a v1-only host, so the identified turn rode the
    # LEGACY path with the contract's visible degraded marker
    # (contracts/tts-brain-v2.md, Negotiation).
    resp = result["resp"]
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == ANSWER
    assert body["audio_url"] is None
    assert body["speech"] == {
        "id": SPEECH_ID,
        "status": "cancelled",
        "degraded": SEGMENTED_UNAVAILABLE,
    }
    job = registry.get(SPEECH_ID)
    assert job.phase == PHASE_CANCELLED
    assert job.cancel_event.is_set()  # the abort signal stays set

    # Convergence: a terminal job never reopens — the honest repeat
    # answer is already-complete, and the phase does not flip.
    second = cancel_client.post(url, json=cancel_body())
    assert second.status_code == 200
    assert second.json() == {"status": "already-complete"}
    assert registry.get(SPEECH_ID).phase == PHASE_CANCELLED


def test_unknown_id_noop_no_enumeration(audio_dir):
    now = [1000.0]
    registry = SpeechRegistry(retention_s=300.0, clock=lambda: now[0])
    client = make_client(FakeLLM(), FakeTTS(), registry, audio_dir)
    # A REAL job: registered, forced terminal, aged past retention.
    registry.register(EXPIRED_ID, TOKEN, "s1")
    registry.mark(EXPIRED_ID, PHASE_COMPLETE)
    now[0] += 301.0
    # No manual purge: the cancel path itself must reclaim it, so an
    # expired id answers down the SAME code path as a never-seen id —
    # identical status, body and work (nothing to enumerate on).
    expired = client.post(f"/speech/{EXPIRED_ID}/cancel", json=cancel_body())
    unknown = client.post("/speech/never.seen.id/cancel", json=cancel_body())
    assert expired.status_code == unknown.status_code == 200
    assert expired.json() == unknown.json() == {"status": "unknown-or-expired"}
    assert registry.get(EXPIRED_ID) is None  # retention reclaimed it
    assert len(registry) == 0  # the no-op registered nothing


def test_capability_token_required_constant_time(audio_dir, monkeypatch):
    registry = SpeechRegistry()
    client = make_client(FakeLLM(), FakeTTS(), registry, audio_dir)
    registry.register(SPEECH_ID, TOKEN, "s1")  # ACTIVE: cancel window open
    url = f"/speech/{SPEECH_ID}/cancel"

    # Missing or empty credentials never reach the registry: 422.
    assert client.post(url, json={"session_id": "s1"}).status_code == 422
    assert client.post(url, json={"speech_cancel_token": TOKEN}).status_code == 422
    assert client.post(url, json=cancel_body(session_id="")).status_code == 422
    assert client.post(url, json=cancel_body(speech_cancel_token="")).status_code == 422

    # Wrong token: 403 with the bare forbidden verdict, no mark landed.
    wrong = client.post(url, json=cancel_body(speech_cancel_token="wrong-token"))
    assert wrong.status_code == 403
    assert wrong.json() == {"detail": "forbidden"}
    assert registry.get(SPEECH_ID).cancel_requested is False

    # Right token: cancelled.
    right = client.post(url, json=cancel_body())
    assert right.status_code == 200
    assert right.json() == {"status": "cancelled"}
    assert registry.get(SPEECH_ID).cancel_requested is True

    # Constant-time, verified two honest ways:
    # (1) empirical — a spy proves every verdict rides hmac.compare_digest
    #     and that the comparison sees sha256 DIGESTS, not plaintext;
    compare_calls: list = []
    real_compare = hmac.compare_digest

    def spy(a, b):
        compare_calls.append((a, b))
        return real_compare(a, b)

    monkeypatch.setattr(hmac, "compare_digest", spy)
    forbidden = client.post(url, json=cancel_body(speech_cancel_token="also-wrong"))
    assert forbidden.status_code == 403
    assert compare_calls, "request_cancel must digest-compare on every call"
    for a, b in compare_calls:
        assert (len(a), len(b)) == (64, 64)  # sha256 hex digests, not tokens
        assert TOKEN.encode("utf-8") not in (a + b)
    # (2) code-level — the implementation names the primitive itself.
    assert "hmac.compare_digest" in inspect.getsource(
        SpeechRegistry.request_cancel
    )


def test_token_never_in_logs(audio_dir, caplog):
    registry = SpeechRegistry()
    secret = "super-secret-capability-token"
    tts = HangingCancellableTTS()
    app = make_app(FakeLLM(), tts, registry, audio_dir)
    cancel_client = TestClient(app)

    result: dict = {}
    thread = run_ask_concurrently(
        app, result, body=speech_body(speech_cancel_token=secret)
    )
    try:
        assert tts.started.wait(timeout=10)
        with caplog.at_level(logging.DEBUG, logger="herdr_brain.server"):
            wrong = cancel_client.post(
                f"/speech/{SPEECH_ID}/cancel",
                json=cancel_body(speech_cancel_token=f"wrong-{secret}"),
            )
            right = cancel_client.post(
                f"/speech/{SPEECH_ID}/cancel",
                json=cancel_body(speech_cancel_token=secret),
            )
    finally:
        thread.join(timeout=10)
    assert not thread.is_alive()
    assert wrong.status_code == 403
    assert right.status_code == 200

    # The flow DID log (the abort surfaces as a render failure warning)
    # — and no captured record carries the token, message or traceback.
    messages = [record.getMessage() for record in caplog.records]
    assert any("TTS render failed" in message for message in messages)
    for record in caplog.records:
        assert secret not in record.getMessage()
        if record.exc_info:
            formatted = "".join(traceback.format_exception(*record.exc_info))
            assert secret not in formatted


def test_cancel_mid_render_aborts_and_skips_audio(audio_dir):
    registry = SpeechRegistry()
    tts = HangingCancellableTTS()
    app = make_app(FakeLLM(), tts, registry, audio_dir)
    cancel_client = TestClient(app)

    result: dict = {}
    thread = run_ask_concurrently(app, result)
    try:
        assert tts.started.wait(timeout=10)  # render is mid-flight
        cancel = cancel_client.post(f"/speech/{SPEECH_ID}/cancel", json=cancel_body())
        assert cancel.status_code == 200
        assert cancel.json() == {"status": "cancelled"}
    finally:
        thread.join(timeout=10)
    assert not thread.is_alive()

    resp = result["resp"]
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == ANSWER  # the textual answer survived
    assert body["audio_url"] is None  # ...but no audio was delivered
    # v1-only session stub: the legacy path plus the contract's visible
    # degraded marker (contracts/tts-brain-v2.md).
    assert body["speech"] == {
        "id": SPEECH_ID,
        "status": "cancelled",
        "degraded": SEGMENTED_UNAVAILABLE,
    }
    assert registry.get(SPEECH_ID).phase == PHASE_CANCELLED

    # The render rode the cancellable seam and left no artifact behind.
    assert len(tts.calls) == 1
    assert list(audio_dir.glob("*.mp3")) == []

    # Answer/history behave exactly as always (VS1.1 flow): the raw
    # turn is persisted once, cancel or not.
    turns = TestClient(app).get("/call-history").json()["turns"]
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert turns[0]["text"] == "¿Cómo va todo?"
    assert turns[1]["text"] == ANSWER


def test_cancel_response_never_echoes_token(audio_dir):
    registry = SpeechRegistry()
    secret = "cap-token-do-not-echo"
    client = make_client(FakeLLM(), FakeTTS(), registry, audio_dir)
    registry.register(SPEECH_ID, secret, "s1")  # ACTIVE job
    url = f"/speech/{SPEECH_ID}/cancel"

    wrong = client.post(url, json=cancel_body(speech_cancel_token=f"nope-{secret}"))
    right = client.post(url, json=cancel_body(speech_cancel_token=secret))
    assert wrong.status_code == 403
    assert right.status_code == 200
    # Neither verdict body carries the token (a leaked WRONG token
    # would contain the secret as a substring — both are checked).
    assert secret not in wrong.text
    assert secret not in right.text
