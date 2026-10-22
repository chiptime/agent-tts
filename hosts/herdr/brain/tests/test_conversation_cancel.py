"""Client-disconnect cancellation of an /ask turn (FR-06 / T-06).

The tests drive the raw ASGI interface so a real ``http.disconnect``
arrives mid-turn — Starlette's ``is_disconnected`` peek only sees a
message that is returned without suspending, which is exactly what a
dead connection produces and what a healthy TestClient never does.

LLM and TTS stay fakes; the doubles that model cancellation declare
``takes_cancel_event`` exactly like the TTS seam convention, so the
production marker on BrainLLM opts the real tool loop in.
"""

from __future__ import annotations

import asyncio
import json
import threading

from fastapi.testclient import TestClient
from openai import AsyncOpenAI

from herdr_brain.config import Settings
from herdr_brain.llm import BrainLLM, TurnCancelled
from herdr_brain.server import create_app
from herdr_brain.speech import PHASE_RENDERING, SpeechRegistry
from herdr_brain.tools import BrainTools
from herdr_brain.tts import TTSError
from tests.conftest import ACTIVE_AGENT, SETTINGS_KWARGS, StubHerdr
from tests.test_speech_cancel import (
    FakeLLM,
    FakeTTS,
    SPEECH_ID,
    TOKEN,
    make_app,
    speech_body,
)


class BlockingLLM:
    """Models the BrainLLM disconnect contract: the turn blocks like a
    multi-round provider call and aborts promptly once the request's
    cancel event fires, exactly where the real loop checks."""

    takes_cancel_event = True

    def __init__(self):
        self.calls: list = []
        self.entered = threading.Event()
        self.released = threading.Event()

    def attach_store(self, store):
        pass

    def attach_approval_store(self, store):
        pass

    def ask(self, text, session_id=None, pane_id=None, cancel_event=None):
        self.calls.append(text)
        self.entered.set()
        if cancel_event is None:
            cancel_event = threading.Event()
        cancel_event.wait(timeout=10)  # bounded: a broken test must not hang
        self.released.set()
        raise TurnCancelled("client disconnected mid-turn")


class BlockingSentenceTTS(FakeTTS):
    """Blocks the first-sentence render until the job cancel event."""

    takes_cancel_event = True

    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.released = threading.Event()

    def __call__(self, settings, text, out_path, cancel_event=None):
        self.calls.append({"text": text, "out": out_path})
        self.started.set()
        cancel_event.wait(timeout=10)
        self.released.set()
        raise TTSError("cancelled")


class FastSentenceLLM(FakeLLM):
    takes_cancel_event = True

    def ask(self, text, session_id=None, pane_id=None, cancel_event=None):
        result = super().ask(text, session_id=session_id, pane_id=pane_id)
        result["answer"] = "First sentence. Remaining sentence."
        return result


def drive_asgi_disconnect(
    app,
    method: str,
    path: str,
    payload: dict | None = None,
    *,
    disconnect_after: float = 0.3,
    timeout: float = 15.0,
) -> dict:
    """One raw ASGI request whose connection dies mid-turn.

    ``receive`` hands over the full JSON body once, then parks on a long
    sleep until the disconnect flag flips — the cancelled-scope peek in
    ``Request.is_disconnected`` absorbs the parked await and only sees
    the disconnect once it is returned synchronously.
    """
    raw_path, _, query = path.partition("?")
    state = {"body_sent": False, "disconnected": False, "messages": []}

    async def receive():
        if payload is not None and not state["body_sent"]:
            state["body_sent"] = True
            return {
                "type": "http.request",
                "body": json.dumps(payload).encode(),
                "more_body": False,
            }
        if state["disconnected"]:
            return {"type": "http.disconnect"}
        await asyncio.sleep(3600)

    async def send(message):
        state["messages"].append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "path": raw_path,
        "raw_path": raw_path.encode(),
        "query_string": query.encode(),
        "headers": [(b"content-type", b"application/json")],
        "server": ("testclient", 80),
        "client": ("testclient", 50000),
        "scheme": "http",
        "root_path": "",
    }

    async def scenario():
        task = asyncio.ensure_future(app(scope, receive, send))
        await asyncio.sleep(disconnect_after)
        state["disconnected"] = True
        await asyncio.wait_for(task, timeout=timeout)

    asyncio.run(scenario())
    return state


def response_start(state: dict):
    messages = [m for m in state["messages"] if m["type"] == "http.response.start"]
    return messages[0]["status"] if messages else None


def test_ask_disconnect_stops_llm_and_speech_and_persists_nothing(tmp_path):
    registry = SpeechRegistry()
    llm = BlockingLLM()
    tts = FakeTTS()
    app = make_app(llm, tts, registry, tmp_path / "audio")

    state = drive_asgi_disconnect(app, "POST", "/ask", speech_body())

    assert response_start(state) == 499
    # The ACTUAL work stopped: the provider loop released on the abort.
    assert llm.entered.is_set()
    assert llm.released.wait(2)
    assert llm.calls == ["¿Cómo va todo?"]
    assert tts.calls == []  # no render ever started
    job = registry.get(SPEECH_ID)
    assert job.phase == "cancelled"
    assert job.cancel_requested is True
    assert job.cancel_event.is_set()
    # An abandoned turn leaves no transcript behind.
    assert TestClient(app).get("/call-history").json()["turns"] == []


def test_ask_disconnect_aborts_first_sentence_render(tmp_path):
    registry = SpeechRegistry()
    tts = BlockingSentenceTTS()
    app = make_app(FastSentenceLLM(), tts, registry, tmp_path / "audio")

    state = drive_asgi_disconnect(
        app, "POST", "/ask", speech_body(conversation=True), disconnect_after=0.3
    )

    # The render rode the cancellable seam and released when the job died.
    assert tts.started.wait(2)
    assert tts.released.wait(2)
    job = registry.get(SPEECH_ID)
    assert job.phase == "cancelled"
    assert job.cancel_event.is_set()
    assert list((tmp_path / "audio").glob("*.mp3")) == []
    # A response was still produced (nobody is left to read it); the
    # speech status honestly reports the cancel, never a fake delivery.
    body = json.loads(
        b"".join(
            m.get("body", b"")
            for m in state["messages"]
            if m["type"] == "http.response.body"
        )
    )
    assert body["speech"]["status"] == "cancelled"
    assert body["audio_url"] is None


def test_legacy_ask_disconnect_stops_llm_without_registry_side_effects(tmp_path):
    llm = BlockingLLM()
    registry = SpeechRegistry()
    app = make_app(llm, FakeTTS(), registry, tmp_path / "audio")

    state = drive_asgi_disconnect(app, "POST", "/ask", {"text": "Question"})

    assert response_start(state) == 499
    assert llm.released.wait(2)
    assert len(registry) == 0  # a legacy turn never touches the registry
    assert TestClient(app).get("/call-history").json()["turns"] == []


def test_speech_next_disconnect_never_cancels_the_job(tmp_path, monkeypatch):
    monkeypatch.setattr("herdr_brain.speech.SPEECH_NEXT_HOLD_S", 0.5)
    registry = SpeechRegistry()
    job = registry.register(SPEECH_ID, TOKEN, "s1")
    registry.mark(SPEECH_ID, PHASE_RENDERING)
    app = make_app(FakeLLM(), FakeTTS(), registry, tmp_path / "audio")

    state = drive_asgi_disconnect(
        app,
        "GET",
        f"/speech/{SPEECH_ID}/next?session_id=s1&after=-1&ack=-1",
        disconnect_after=0.15,
    )

    # The transport intentionally ignores the disconnect: the hold ran
    # its full course and answered, and ONLY /speech/{id}/cancel may
    # end a job (VS2.5).
    assert response_start(state) == 200
    survived = registry.get(SPEECH_ID)
    assert survived.phase == PHASE_RENDERING
    assert survived.cancel_requested is False
    assert not survived.cancel_event.is_set()


def test_ask_disconnect_aborts_the_production_provider_transport(tmp_path):
    """FR-06 end-to-end on the REAL SDK path: the app's production
    BrainLLM (real tool loop, real AsyncOpenAI stack) talks to an httpx
    MockTransport that blocks inside HTTP I/O; the client disconnects
    mid-generation and the transport itself is aborted — not merely a
    flag set while the HTTP call rides on."""
    import httpx

    cancelled = threading.Event()
    closed = threading.Event()
    requests: list = []

    async def handler(request):
        requests.append(request.url.path)
        try:
            await asyncio.Event().wait()  # in-flight generation HTTP I/O
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return httpx.Response(200, json={})

    class ProbeHttpx(httpx.AsyncClient):
        async def aclose(self):
            closed.set()
            await super().aclose()

    probe = ProbeHttpx(transport=httpx.MockTransport(handler))

    def factory():
        return AsyncOpenAI(
            api_key=SETTINGS_KWARGS["glm_api_key"],
            base_url=SETTINGS_KWARGS["glm_base_url"],
            http_client=probe,
        )

    registry = SpeechRegistry()
    cfg = Settings(**{**SETTINGS_KWARGS, "audio_dir": str(tmp_path / "audio")})

    def llm_factory(app_cfg, tools):
        # The REAL BrainLLM: its class marker opts it into the request's
        # disconnect event; the only double is the HTTP transport.
        stub_tools = BrainTools(app_cfg, herdr=StubHerdr(agents=[ACTIVE_AGENT]))
        return BrainLLM(app_cfg, stub_tools, async_client_factory=factory)

    app = create_app(
        settings=cfg,
        llm_factory=llm_factory,
        tts_renderer=FakeTTS(),
        speech_registry=registry,
    )

    state = drive_asgi_disconnect(app, "POST", "/ask", speech_body())

    assert response_start(state) == 499
    assert cancelled.wait(3), "the in-flight provider HTTP task was cancelled"
    assert closed.wait(3), "the owned transport was closed"
    assert len(requests) == 1
    job = registry.get(SPEECH_ID)
    assert job.phase == "cancelled"
    assert TestClient(app).get("/call-history").json()["turns"] == []
