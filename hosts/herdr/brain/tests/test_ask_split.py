"""Conversation opt-in uses the existing speech transport with a v1 renderer."""

import threading

from tests.test_speech_cancel import (
    FakeLLM, FakeTTS, SPEECH_ID, cancel_body, make_client, speech_body,
)
from herdr_brain.speech import SpeechRegistry


ANSWER = "First sentence. Remaining sentence."


class SentenceLLM(FakeLLM):
    def ask(self, text, session_id=None, pane_id=None):
        result = super().ask(text, session_id, pane_id)
        result["answer"] = ANSWER
        return result


class SplitTTS(FakeTTS):
    takes_cancel_event = True

    def __init__(self):
        super().__init__()
        self.remainder_started = threading.Event()
        self.release = threading.Event()

    def __call__(self, settings, text, out_path, cancel_event):
        if text == "Remaining sentence.":
            self.remainder_started.set()
            while not self.release.wait(0.01):
                if cancel_event.is_set():
                    raise RuntimeError("cancelled")
        return super().__call__(settings, text, out_path)


def test_conversation_returns_first_audio_before_remainder_finishes(tmp_path):
    registry, renderer = SpeechRegistry(), SplitTTS()
    client = make_client(SentenceLLM(), renderer, registry, tmp_path / "audio")
    try:
        response = client.post("/ask", json=speech_body(conversation=True))
        assert response.status_code == 200
        body = response.json()
        assert body["answer"] == ANSWER
        assert body["audio_url"].startswith("/audio/")
        assert body["speech"]["first_seq"] == 0
        assert body["speech"]["status"] == "delivering"
        assert renderer.remainder_started.wait(2)
        assert [call["text"] for call in renderer.calls] == ["First sentence."]
        first = client.get(f"/speech/{SPEECH_ID}/next", params={"session_id": "s1"}).json()
        assert first["seq"] == 0
        assert first["audio_url"] == body["audio_url"]
        renderer.release.set()
        registry.get(SPEECH_ID).producer.thread.join(2)
        remainder = client.get(f"/speech/{SPEECH_ID}/next", params={"after": 0, "ack": 0}).json()
        assert remainder["seq"] == 1 and remainder["is_final"] is True
        assert [call["text"] for call in renderer.calls] == ["First sentence.", "Remaining sentence."]
    finally:
        renderer.release.set()
        job = registry.get(SPEECH_ID)
        if job and job.producer and job.producer.thread:
            job.producer.thread.join(2)


def test_conversation_cancel_stops_remaining_v1_render(tmp_path):
    registry, renderer = SpeechRegistry(), SplitTTS()
    client = make_client(SentenceLLM(), renderer, registry, tmp_path / "audio")
    try:
        response = client.post("/ask", json=speech_body(conversation=True))
        assert response.status_code == 200
        assert renderer.remainder_started.wait(2)
        assert client.post(f"/speech/{SPEECH_ID}/cancel", json=cancel_body()).json() == {"status": "cancelled"}
        job = registry.get(SPEECH_ID)
        job.producer.thread.join(2)
        assert not job.producer.thread.is_alive()
        assert job.phase == "cancelled"
        assert len(renderer.calls) == 1
    finally:
        renderer.release.set()


def test_conversation_requires_cancellable_speech_identity_before_side_effects(tmp_path):
    llm, renderer, registry = SentenceLLM(), FakeTTS(), SpeechRegistry()
    client = make_client(llm, renderer, registry, tmp_path / "audio")
    response = client.post("/ask", json={"text": "Question", "conversation": True})
    assert response.status_code == 422
    assert not llm.calls and not renderer.calls and len(registry) == 0


def test_legacy_ask_still_renders_one_complete_answer(tmp_path):
    renderer = FakeTTS()
    client = make_client(SentenceLLM(), renderer, SpeechRegistry(), tmp_path / "audio")
    response = client.post("/ask", json={"text": "Question"})
    assert response.status_code == 200
    assert "speech" not in response.json()
    assert [call["text"] for call in renderer.calls] == [ANSWER]
