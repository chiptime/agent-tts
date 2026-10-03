"""Speech job registry tests (voice-stack VS1.1): /ask admission, cancel
suppression, and registry bounds. LLM and TTS are faked; the registry is
the real one with small bounds injected through the create_app seam."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from herdr_brain.config import Settings
from herdr_brain.server import create_app
from herdr_brain.speech import (
    PHASE_COMPLETE,
    PHASE_RENDERING,
    PHASE_WAITING_LLM,
    SEGMENTED_UNAVAILABLE,
    JobAlreadyTerminal,
    SpeechRegistry,
    validate_speech_request_id,
)
from tests.conftest import SETTINGS_KWARGS

TOKEN = "cap-token-abc123"
SPEECH_ID = "vs1.job.alpha"
ANSWER = "Estás en la fase 2 del brain; todo verde."


class FakeTTS:
    def __init__(self):
        self.calls: list = []

    def __call__(self, settings, text, out_path: Path) -> Path:
        self.calls.append({"text": text, "out": out_path})
        out_path.write_bytes(b"ID3-fake")
        return out_path


class FakeLLM:
    """LLM double with a mid-ask probe hook (registry checks, cancels)."""

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


def make_client(llm, tts, registry, audio_dir) -> TestClient:
    cfg = Settings(**{**SETTINGS_KWARGS, "audio_dir": str(audio_dir)})
    app = create_app(
        settings=cfg,
        llm_factory=lambda _cfg, _tools: llm,
        tts_renderer=tts,
        speech_registry=registry,
    )
    return TestClient(app)


def speech_body(**overrides) -> dict:
    body = {
        "text": "¿Cómo va todo?",
        "session_id": "s1",
        "speech_request_id": SPEECH_ID,
        "speech_cancel_token": TOKEN,
    }
    body.update(overrides)
    return body


def test_ask_registers_speech_job_before_llm(audio_dir):
    registry = SpeechRegistry()
    seen = {}

    def probe():
        job = registry.get(SPEECH_ID)
        seen["phase_at_llm"] = job.phase if job else "absent"

    tts = FakeTTS()
    client = make_client(FakeLLM(on_ask=probe), tts, registry, audio_dir)
    resp = client.post("/ask", json=speech_body())
    assert resp.status_code == 200
    # At the moment llm.ask runs, the job already waits in waiting_llm.
    assert seen["phase_at_llm"] == PHASE_WAITING_LLM
    body = resp.json()
    # v1-only session stub: legacy path + contract's visible degraded
    # marker (contracts/tts-brain-v2.md).
    assert body["speech"] == {
        "id": SPEECH_ID,
        "status": "complete",
        "degraded": SEGMENTED_UNAVAILABLE,
    }
    assert body["audio_url"].startswith("/audio/")
    assert registry.get(SPEECH_ID).phase == PHASE_COMPLETE


def test_duplicate_active_409(audio_dir):
    registry = SpeechRegistry()
    tts = FakeTTS()
    client = make_client(FakeLLM(), tts, registry, audio_dir)
    # An ACTIVE job holds the id (mid-flight elsewhere): admission 409s.
    registry.register(SPEECH_ID, TOKEN, "s1")
    resp = client.post("/ask", json=speech_body())
    assert resp.status_code == 409
    assert registry.get(SPEECH_ID).phase == PHASE_WAITING_LLM
    # Terminal frees the id: the same id registers fresh and completes.
    registry.mark(SPEECH_ID, PHASE_COMPLETE)
    resp = client.post("/ask", json=speech_body())
    assert resp.status_code == 200
    # Same v1-host degraded marker as above (contracts/tts-brain-v2.md).
    assert resp.json()["speech"] == {
        "id": SPEECH_ID,
        "status": "complete",
        "degraded": SEGMENTED_UNAVAILABLE,
    }
    # And through the HTTP flow itself: a finished job's id is reusable.
    resp = client.post("/ask", json=speech_body())
    assert resp.status_code == 200
    assert resp.json()["speech"]["status"] == "complete"


def test_registry_full_text_only_no_audio(audio_dir):
    registry = SpeechRegistry(max_jobs=1)
    registry.register("occupied-job", "other-token", "s1")  # active forever
    tts = FakeTTS()
    client = make_client(FakeLLM(), tts, registry, audio_dir)
    resp = client.post("/ask", json=speech_body(speech_request_id="fresh.job.id"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == ANSWER  # the LLM answer is intact
    assert body["audio_url"] is None  # no uncancellable legacy render
    assert body["speech"] == {"status": "degraded", "reason": "registry-full"}
    assert tts.calls == []
    assert registry.get("fresh.job.id") is None  # never registered


def test_legacy_ask_untouched(audio_dir):
    registry = SpeechRegistry()
    tts = FakeTTS()
    client = make_client(FakeLLM(), tts, registry, audio_dir)
    resp = client.post("/ask", json={"text": "¿Cómo va todo?", "session_id": "s1"})
    assert resp.status_code == 200
    body = resp.json()
    assert "speech" not in body
    assert set(body) == {
        "answer",
        "pane_id",
        "agent",
        "session_id",
        "audio_url",
        "approval",
    }
    assert body["audio_url"].startswith("/audio/")
    assert len(tts.calls) == 1
    assert len(registry) == 0


def test_cancel_in_waiting_llm_disables_speech_only(audio_dir):
    registry = SpeechRegistry()
    verdicts = {}

    def cancel_mid_flight():
        verdicts["cancel"] = registry.request_cancel(SPEECH_ID, TOKEN, "s1")

    tts = FakeTTS()
    client = make_client(FakeLLM(on_ask=cancel_mid_flight), tts, registry, audio_dir)
    resp = client.post("/ask", json=speech_body())
    assert resp.status_code == 200
    body = resp.json()
    assert verdicts["cancel"] == "cancelled"
    # Cancel touches ONLY speech: answer, audio suppression, history.
    assert body["answer"] == ANSWER
    assert body["audio_url"] is None
    assert body["speech"] == {"id": SPEECH_ID, "status": "cancelled"}
    assert tts.calls == []
    job = registry.get(SPEECH_ID)
    assert job.phase == "cancelled"
    assert job.cancel_requested is True
    # History survived untouched: the raw turn is persisted exactly once.
    turns = client.get("/call-history").json()["turns"]
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert turns[0]["text"] == "¿Cómo va todo?"
    assert turns[1]["text"] == ANSWER


def test_speech_id_validation_422(audio_dir):
    registry = SpeechRegistry()
    llm = FakeLLM()
    client = make_client(llm, FakeTTS(), registry, audio_dir)
    for bad in ["short", "has spaces!", "x" * 65, "üñïçødé", "slash/id", ""]:
        resp = client.post("/ask", json=speech_body(speech_request_id=bad))
        assert resp.status_code == 422, bad
    # An id without its capability token is equally malformed.
    resp = client.post("/ask", json=speech_body(speech_cancel_token=None))
    assert resp.status_code == 422
    # Nothing registered, no LLM call: 422 leaves no side effects.
    assert len(registry) == 0
    assert llm.calls == []


def test_token_hash_not_stored_plain(audio_dir):
    registry = SpeechRegistry()
    secret = "super-secret-capability-token"
    client = make_client(FakeLLM(), FakeTTS(), registry, audio_dir)
    resp = client.post("/ask", json=speech_body(speech_cancel_token=secret))
    assert resp.status_code == 200
    job = registry.get(SPEECH_ID)
    assert job.token_hash == hashlib.sha256(secret.encode()).hexdigest()
    assert job.token_hash != secret
    # Redaction: repr carries id + phase only; the wire never sees it.
    assert repr(job) == f"SpeechJob(id={SPEECH_ID!r}, phase={PHASE_COMPLETE!r})"
    assert secret not in resp.text
    # Token verification: constant-time digest match decides the verdict.
    assert registry.request_cancel(SPEECH_ID, "wrong-token", "s1") == "forbidden"
    assert registry.request_cancel(SPEECH_ID, secret, "s1") == "already-terminal"


def test_terminal_never_reopened():
    registry = SpeechRegistry()
    registry.register("terminal.job.01", "tok", "s1")
    assert registry.get("terminal.job.01").terminal_ts is None
    registry.mark("terminal.job.01", PHASE_COMPLETE)
    job = registry.get("terminal.job.01")
    assert job.terminal_ts is not None
    with pytest.raises(JobAlreadyTerminal):
        registry.mark("terminal.job.01", PHASE_RENDERING)
    assert registry.get("terminal.job.01").phase == PHASE_COMPLETE
    assert registry.request_cancel("terminal.job.01", "tok", "s1") == "already-terminal"
    # Unknown phase names are refused outright (typo guard).
    registry.register("active.job..02", "tok2", "s1")
    with pytest.raises(ValueError):
        registry.mark("active.job..02", "nonsense-phase")
    assert registry.get("active.job..02").phase == PHASE_WAITING_LLM


def test_purge_only_terminal():
    now = [1000.0]
    registry = SpeechRegistry(retention_s=300.0, clock=lambda: now[0])
    # Terminal long ago: past retention, must go.
    registry.register("stale.term.01", "t1", "s1")
    registry.mark("stale.term.01", PHASE_COMPLETE)
    # Active since forever: TTL never touches ACTIVE jobs.
    registry.register("active.job..03", "t3", "s1")
    # Registered early, but terminal only after the clock advances: its
    # terminal_ts is fresh, so retention keeps it.
    registry.register("fresh.term.01", "t2", "s1")
    now[0] += 301.0
    registry.mark("fresh.term.01", PHASE_COMPLETE)
    removed = registry.purge_expired()
    assert removed == 1
    assert registry.get("stale.term.01") is None
    assert registry.get("fresh.term.01") is not None
    assert registry.get("active.job..03") is not None


def test_validate_speech_request_id_cases():
    assert validate_speech_request_id("abcdefgh")
    assert validate_speech_request_id("x" * 64)
    assert validate_speech_request_id("Job._-id-9")
    assert not validate_speech_request_id("x" * 65)
    assert not validate_speech_request_id("short")
    assert not validate_speech_request_id("bad space")
    assert not validate_speech_request_id("")
