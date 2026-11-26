"""W3-P1 integration tests: streaming conversation turns over protocol-2.

Drives the REAL /ask conversation path with a sink-marked LLM double and
faked TTS, pinning PRD-06 acceptance: synthesis begins at the first
sentence boundary WHILE generation runs (S2), segments arrive through
/speech/{id}/next in order, cancel semantics are untouched (S3), the
approval gate still closes the turn and speaks its closer (S4), and the
legacy batch path stays byte-compatible for marker-less doubles.
"""

from __future__ import annotations

import threading

from fastapi.testclient import TestClient

from herdr_brain.speech import PHASE_CANCELLED, PHASE_COMPLETE, SpeechRegistry
from herdr_brain.tts import TTSError
from tests.test_speech_cancel import (
    FakeTTS,
    SPEECH_ID,
    cancel_body,
    make_app,
    speech_body,
)

CLOSER_ES = "¿Se envía?"  # default spoken-locale approval closer prefix


class StreamingLLM:
    """Sink-marked double: hands sentences to the sink mid-turn, then
    returns the same text as the turn's answer."""

    takes_cancel_event = True
    takes_sentence_sink = True

    def __init__(self, sentences, approval=False, after_sink=None):
        self.sentences = list(sentences)
        self.approval = approval
        self.calls: list = []
        self.sunk: list[str] = []
        self.after_sink = after_sink  # optional callable(sunk) mid-turn hook
        self.approval_store = None

    def attach_store(self, store):
        pass

    def attach_approval_store(self, store):
        self.approval_store = store

    def ask(self, text, session_id=None, pane_id=None, cancel_event=None,
            sentence_sink=None):
        self.calls.append(text)
        for sentence in self.sentences:
            sentence_sink(sentence)
            self.sunk.append(sentence)
        if self.after_sink is not None:
            self.after_sink(self)
        approval = None
        if self.approval and self.approval_store is not None:
            approval = self.approval_store.propose(
                session_id, text="enviar el informe", pane_id="w1:p9",
                agent="opencode",
            )
        return {
            "answer": " ".join(self.sentences),
            "pane_id": "w1:p9",
            "agent": "opencode",
            "session_id": session_id or "default",
            "approval": approval,
        }


def test_streaming_turn_serves_segments_over_next_in_order(tmp_path):
    registry = SpeechRegistry()
    renderer = FakeTTS()
    llm = StreamingLLM(["Primera frase.", "Segunda frase final."])
    app = make_app(llm, renderer, registry, tmp_path / "audio")
    client = TestClient(app)

    response = client.post("/ask", json=speech_body(conversation=True))

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == " ".join(llm.sentences)
    assert body["audio_url"] and body["audio_url"].startswith("/audio/")
    assert body["speech"]["status"] == "delivering"
    assert body["speech"]["first_seq"] == 0
    job = registry.get(SPEECH_ID)
    job.producer.thread.join(5)
    assert job.phase == PHASE_COMPLETE
    first = client.get(
        f"/speech/{SPEECH_ID}/next", params={"session_id": "s1"}
    ).json()
    assert first["seq"] == 0 and first["audio_url"] == body["audio_url"]
    second = client.get(
        f"/speech/{SPEECH_ID}/next", params={"session_id": "s1", "after": 0, "ack": 0}
    ).json()
    assert second["seq"] == 1 and second["is_final"] is True
    assert [c["text"] for c in renderer.calls] == llm.sentences


def test_synthesis_starts_before_the_turn_finishes(tmp_path):
    """S2 core: the first sentence is already being rendered WHILE the
    LLM is still inside ask()."""
    llm_entered = threading.Event()
    llm_released = threading.Event()

    def park_mid_turn(llm):
        llm_entered.set()
        assert llm_released.wait(10), "the test never released the turn"

    llm = StreamingLLM(
        ["Primera frase.", "Segunda frase final."], after_sink=park_mid_turn
    )
    renderer = FakeTTS()
    app = make_app(llm, renderer, registry := SpeechRegistry(), tmp_path / "audio")

    result: dict = {}

    def drive():
        result["resp"] = TestClient(app).post(
            "/ask", json=speech_body(conversation=True)
        )

    thread = threading.Thread(target=drive)
    thread.start()
    try:
        assert llm_entered.wait(10)
        # The producer worker picks the sentence up asynchronously —
        # bounded poll until its render starts. The turn thread is
        # parked INSIDE ask() the whole time, so any render observed
        # here proves synthesis began before the answer existed (S2).
        import time

        deadline = time.monotonic() + 5
        while not renderer.calls and time.monotonic() < deadline:
            time.sleep(0.01)
        assert renderer.calls and renderer.calls[0]["text"] == "Primera frase.", (
            "first sentence must reach synthesis before the answer exists"
        )
    finally:
        llm_released.set()
        thread.join(15)
    assert result["resp"].status_code == 200


def test_cancel_mid_stream_aborts_pending_synthesis(tmp_path):
    """S3: /speech/{id}/cancel aborts the pending synthesis exactly as
    the batch path does."""
    second_started = threading.Event()
    release = threading.Event()

    class BlockingSecondTTS(FakeTTS):
        takes_cancel_event = True

        def __call__(self, settings, text, out_path, cancel_event=None):
            # Record ONCE per render (not via super(), which would
            # double-count) and model the cancel abort like the
            # production cancellable render does.
            self.calls.append({"text": text, "out": out_path})
            if text.startswith("Segunda"):
                second_started.set()
                while not release.wait(0.01):
                    if cancel_event.is_set():
                        raise TTSError("cancelled")
            out_path.write_bytes(b"ID3-fake")
            return out_path

    registry = SpeechRegistry()
    renderer = BlockingSecondTTS()
    llm = StreamingLLM(["Primera frase.", "Segunda frase final."])
    app = make_app(llm, renderer, registry, tmp_path / "audio")
    client = TestClient(app)

    response = client.post("/ask", json=speech_body(conversation=True))
    assert response.status_code == 200
    assert second_started.wait(5)
    assert client.post(
        f"/speech/{SPEECH_ID}/cancel", json=cancel_body()
    ).json() == {"status": "cancelled"}
    job = registry.get(SPEECH_ID)
    job.producer.thread.join(5)
    assert job.phase == PHASE_CANCELLED
    assert [c["text"] for c in renderer.calls] == llm.sentences  # attempted, aborted
    assert response.json()["answer"] == " ".join(llm.sentences)  # text survives (VS1.3)


def test_gate_turn_speaks_the_closer_and_keeps_the_gate(tmp_path):
    """S4: the approval object reaches the response untouched and the
    spoken stream ends with the deterministic closer."""
    registry = SpeechRegistry()
    renderer = FakeTTS()
    llm = StreamingLLM(["El envío quedó pendiente."], approval=True)
    app = make_app(llm, renderer, registry, tmp_path / "audio")
    client = TestClient(app)

    response = client.post("/ask", json=speech_body(conversation=True))

    assert response.status_code == 200
    body = response.json()
    assert body["approval"] is not None, "gate must open exactly as before"
    job = registry.get(SPEECH_ID)
    job.producer.thread.join(5)
    assert job.phase == PHASE_COMPLETE
    spoken = [c["text"] for c in renderer.calls]
    assert spoken[0] == "El envío quedó pendiente."
    assert spoken[1] == CLOSER_ES, "the closer must be spoken last"


def test_disconnect_mid_stream_kills_the_job_and_persists_nothing(tmp_path):
    registry = SpeechRegistry()
    release = threading.Event()

    class ParkedSinkLLM(StreamingLLM):
        def ask(self, text, session_id=None, pane_id=None, cancel_event=None,
                sentence_sink=None):
            self.calls.append(text)
            sentence_sink("Primera frase.")
            if cancel_event is not None:
                cancel_event.wait(10)
            raise RuntimeError("turn abandoned mid-stream")

    renderer = FakeTTS()
    app = make_app(ParkedSinkLLM([]), renderer, registry, tmp_path / "audio")

    from tests.test_conversation_cancel import drive_asgi_disconnect, response_start

    state = drive_asgi_disconnect(
        app, "POST", "/ask", speech_body(conversation=True), disconnect_after=0.3
    )

    assert response_start(state) == 499
    job = registry.get(SPEECH_ID)
    # The worker marks cancelled when it observes the job's cancel event
    # (the mark races the test thread — join first).
    if job.producer is not None and job.producer.thread is not None:
        job.producer.thread.join(5)
    assert job.phase == PHASE_CANCELLED
    assert TestClient(app).get("/call-history").json()["turns"] == []


def test_streaming_turn_reports_first_timings(tmp_path):
    """FR-07: the response carries first-sentence and first-segment
    timings so the improvement against the PRD-02 baseline is measurable."""
    registry = SpeechRegistry()
    llm = StreamingLLM(["Una sola frase final."])
    app = make_app(llm, FakeTTS(), registry, tmp_path / "audio")
    client = TestClient(app)

    response = client.post("/ask", json=speech_body(conversation=True))

    speech = response.json()["speech"]
    assert isinstance(speech["first_sentence_ms"], int) and speech["first_sentence_ms"] >= 0
    assert isinstance(speech["first_segment_ms"], int) and speech["first_segment_ms"] >= 0


def test_markerless_llm_keeps_the_batch_conversation_path(tmp_path):
    """Additive contract: a double without takes_sentence_sink rides the
    exact pre-W3-P1 SentenceSpeechProducer shape."""

    class BatchLLM:
        takes_cancel_event = True

        def __init__(self):
            self.calls = []

        def attach_store(self, store):
            pass

        def attach_approval_store(self, store):
            pass

        def ask(self, text, session_id=None, pane_id=None, cancel_event=None):
            self.calls.append(text)
            return {
                "answer": "First sentence. Remaining sentence.",
                "pane_id": "w1:p9",
                "agent": "opencode",
                "session_id": session_id or "default",
                "approval": None,
            }

    registry = SpeechRegistry()
    renderer = FakeTTS()
    app = make_app(BatchLLM(), renderer, registry, tmp_path / "audio")
    client = TestClient(app)

    response = client.post("/ask", json=speech_body(conversation=True))

    body = response.json()
    assert body["audio_url"].startswith("/audio/")
    assert body["speech"]["status"] == "delivering"
    assert body["speech"]["first_is_final"] is False
    registry.get(SPEECH_ID).producer.thread.join(5)
    assert [c["text"] for c in renderer.calls] == ["First sentence.", "Remaining sentence."]


def test_call_context_advertises_streaming_ask(tmp_path):
    app = make_app(StreamingLLM(["Hola final."]), FakeTTS(), SpeechRegistry(),
                   tmp_path / "audio")
    assert TestClient(app).get("/call/context").json()["streaming_ask"] is True


def test_sink_handed_pieces_are_rendered_verbatim_and_job_completes(tmp_path):
    """The producer renders exactly what the sink handed it — the LLM
    layer owns sentence boundaries — and the job completes."""
    registry = SpeechRegistry()
    renderer = FakeTTS()
    llm = StreamingLLM(["Todo verde. Una pieza."])
    app = make_app(llm, renderer, registry, tmp_path / "audio")
    client = TestClient(app)

    response = client.post("/ask", json=speech_body(conversation=True))

    assert response.status_code == 200
    job = registry.get(SPEECH_ID)
    job.producer.thread.join(5)
    assert job.phase == PHASE_COMPLETE
    assert [c["text"] for c in renderer.calls] == ["Todo verde. Una pieza."]
