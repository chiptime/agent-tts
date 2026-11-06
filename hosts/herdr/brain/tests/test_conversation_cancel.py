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
from hashlib import sha256
from pathlib import Path

from fastapi.testclient import TestClient
from openai import AsyncOpenAI

from herdr_brain.approval import SUPERSEDED
from herdr_brain.config import Settings
from herdr_brain.llm import BrainLLM, TurnCancelled
from herdr_brain.server import create_app
from herdr_brain.speech import PHASE_RENDERING, SpeechRegistry
from herdr_brain.tools import BrainTools
from herdr_brain.tts import TTSError
from tests.async_support import run_async
from tests.conftest import ACTIVE_AGENT, SETTINGS_KWARGS, StubHerdr
from tests.test_llm import ScriptedLLM, text_response
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


def _file_digest(path) -> str:
    return sha256(Path(path).read_bytes()).hexdigest()


class ResolvingAfterCancelLLM:
    """The post-answer race (S7 hardening): the turn's answer is READY
    right as the disconnect lands — ask() RETURNS a full result after
    the request's cancel event fires instead of raising, so the server
    persistence seam runs while the connection is already dead."""

    takes_cancel_event = True

    def __init__(self, gate: bool = False):
        self.calls: list = []
        self.entered = threading.Event()
        self.returned = threading.Event()
        self._gate = gate
        self.approval_store = None
        self.last_gate = None

    def attach_store(self, store):
        pass

    def attach_approval_store(self, store):
        self.approval_store = store

    def ask(self, text, session_id=None, pane_id=None, cancel_event=None):
        self.calls.append(text)
        self.entered.set()
        if cancel_event is None:
            cancel_event = threading.Event()
        cancel_event.wait(timeout=10)
        approval = None
        if self._gate and self.approval_store is not None:
            approval = self.approval_store.propose(
                session_id, text="enviar el informe", pane_id="w1:p9", agent="opencode"
            )
            self.last_gate = approval
        self.returned.set()
        return {
            "answer": "Respuesta que nadie escuchará.",
            "pane_id": "w1:p9",
            "agent": "opencode",
            "session_id": session_id or "default",
            "approval": approval,
        }


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

    run_async(scenario())
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


def test_ask_disconnect_after_the_answer_persists_nothing(tmp_path):
    """S7/T4: a disconnect observed AFTER the LLM produced its answer
    still stops the persistence seam — no history rows, no render, the
    speech job dies cancelled, and the history DB stays byte-identical."""
    registry = SpeechRegistry()
    llm = ResolvingAfterCancelLLM()
    tts = FakeTTS()
    app = make_app(llm, tts, registry, tmp_path / "audio")
    db = tmp_path / "call_history.db"
    before = _file_digest(db)

    state = drive_asgi_disconnect(app, "POST", "/ask", speech_body())

    assert response_start(state) == 499
    assert llm.returned.wait(2), "the race really ran: the answer existed"
    assert _file_digest(db) == before  # not one byte of the turn landed
    assert TestClient(app).get("/call-history").json()["turns"] == []
    assert tts.calls == []  # no render ever started
    job = registry.get(SPEECH_ID)
    assert job.phase == "cancelled"
    assert job.cancel_requested is True


def test_ask_disconnect_after_the_answer_retires_gate_and_binds_nothing(tmp_path):
    """S7/T4: the gate a cancelled turn opened is retired (superseded)
    — never left as the session's live proposal — and no explanation
    is bound to a job whose answer nobody heard."""
    registry = SpeechRegistry()
    llm = ResolvingAfterCancelLLM(gate=True)
    app = make_app(llm, FakeTTS(), registry, tmp_path / "audio")

    state = drive_asgi_disconnect(app, "POST", "/ask", speech_body())

    assert response_start(state) == 499
    assert llm.returned.wait(2)
    gate = llm.last_gate
    assert gate is not None
    store = app.state.approval_store
    stored = store.get(gate.gate_id)
    assert stored.state == SUPERSEDED
    assert stored.explained_speech_id is None  # no bind for a dead turn
    assert store.current("s1") is None
    job = registry.get(SPEECH_ID)
    assert job.phase == "cancelled"
    assert TestClient(app).get("/call-history").json()["turns"] == []


def test_connected_ask_still_persists_and_completes_its_job(tmp_path):
    """PRESERVE: the disconnect re-checks never touch a live turn — a
    connected identified /ask persists both rows and completes its job."""
    registry = SpeechRegistry()
    llm = FakeLLM()
    app = make_app(llm, FakeTTS(), registry, tmp_path / "audio")

    response = TestClient(app).post("/ask", json=speech_body())

    assert response.status_code == 200
    assert response.json()["answer"]
    turns = TestClient(app).get("/call-history").json()["turns"]
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert registry.get(SPEECH_ID).phase == "complete"


def _park_commit_window(monkeypatch, ghost_marker: str):
    """Interlock for the commit-window race, valid across both code
    shapes: the turn whose ASSISTANT content carries ``ghost_marker``
    parks right after that message is handed to the ring layer —
    pre-landing (staged) or post-landing (inline append) — holding the
    turn inside the window until the test releases it."""
    from herdr_brain.memory import ConversationStore as RingStore

    ring_parked = threading.Event()
    release = threading.Event()

    original_append = RingStore.append

    def parked_append(self, session_id, role, content):
        message = original_append(self, session_id, role, content)
        if role == "assistant" and ghost_marker in (content or ""):
            ring_parked.set()
            release.wait(timeout=10)
        return message

    monkeypatch.setattr(RingStore, "append", parked_append)

    try:
        from herdr_brain.memory import StagedTurn
    except ImportError:  # pre-staging code shape: the inline hook above is the seam
        return ring_parked, release

    original_staged_append = StagedTurn.append

    def parked_staged_append(self, role, content):
        message = original_staged_append(self, role, content)
        if role == "assistant" and ghost_marker in (content or ""):
            ring_parked.set()
            release.wait(timeout=10)
        return message

    monkeypatch.setattr(StagedTurn, "append", parked_staged_append)
    return ring_parked, release


def _raw_ask_scope():
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/ask",
        "raw_path": b"/ask",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "server": ("testclient", 80),
        "client": ("testclient", 50000),
        "scheme": "http",
        "root_path": "",
    }


def test_ask_disconnect_inside_commit_window_leaves_no_ring_ghost(tmp_path, monkeypatch):
    """L32 regression on the REAL BrainLLM (scripted provider, raw ASGI):
    the disconnect lands AFTER the turn appended its user/assistant pair
    to the in-memory ring but BEFORE run_turn returns. The rejection
    must then hold EVERYWHERE the turn already reached: no SQLite rows,
    and no ghost pair riding the NEXT turn's provider prompt."""
    ring_parked, release = _park_commit_window(monkeypatch, "GHOST-ANSWER-MARKER")
    registry = SpeechRegistry()
    scripted = ScriptedLLM([
        text_response("GHOST-ANSWER-MARKER"),
        text_response("SECOND-TURN-ANSWER"),
    ])
    cfg = Settings(**{**SETTINGS_KWARGS, "audio_dir": str(tmp_path / "audio")})

    def llm_factory(app_cfg, tools):
        stub_tools = BrainTools(app_cfg, herdr=StubHerdr(agents=[ACTIVE_AGENT]))
        return BrainLLM(app_cfg, stub_tools, client=scripted)

    app = create_app(
        settings=cfg,
        llm_factory=llm_factory,
        tts_renderer=FakeTTS(),
        speech_registry=registry,
    )
    db = tmp_path / "call_history.db"  # fresh isolated DB per test (L32 note)
    before = _file_digest(db)

    payload = speech_body()
    state = {"body_sent": False, "disconnected": False, "messages": []}

    async def receive():
        if not state["body_sent"]:
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

    async def scenario():
        task = asyncio.ensure_future(app(scope := _raw_ask_scope(), receive, send))
        # 1) the ghost pair is committed and the turn is parked on it.
        await asyncio.to_thread(ring_parked.wait, 10)
        # 2) the connection dies NOW — before llm.ask returns.
        state["disconnected"] = True
        # 3) wait until the watch fired the abort: request_cancel flips
        #    the job's cancel event right after disconnect_event.set().
        job = registry.get(SPEECH_ID)
        await asyncio.to_thread(job.cancel_event.wait, 10)
        # 4) release the parked turn into the rejection path.
        release.set()
        await asyncio.wait_for(task, timeout=15.0)

    run_async(scenario())

    # The turn was rejected everywhere it had already reached.
    assert response_start(state) == 499
    assert _file_digest(db) == before
    client = TestClient(app)
    assert client.get("/call-history").json()["turns"] == []
    assert registry.get(SPEECH_ID).phase == "cancelled"

    # THE L32 BLOCKER: the ghost pair must not ride the next prompt.
    second = client.post("/ask", json={"text": "segunda pregunta", "session_id": "s1"})
    assert second.status_code == 200
    contents = [m.get("content") or "" for m in scripted.create_kwargs[-1]["messages"]]
    assert any("segunda pregunta" in c for c in contents)  # sanity: real prompt
    assert not any("¿Cómo va todo?" in c for c in contents), "ghost question in next prompt"
    assert not any("GHOST-ANSWER-MARKER" in c for c in contents), "ghost answer in next prompt"

    # The connected successor persists exactly once, ring and history
    # telling the same story.
    turns = client.get("/call-history").json()["turns"]
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert turns[0]["text"] == "segunda pregunta"


def test_ask_disconnect_at_full_ring_capacity_evicts_nothing(tmp_path, monkeypatch):
    """L34 regression on the REAL BrainLLM (scripted provider, raw ASGI,
    fresh isolated DB): the ring is FULL (16 messages) when a turn is
    cancelled inside the commit window. The rejection must leave every
    still-eligible earlier message in place — exactly as if the rejected
    turn had never committed — while a concurrent SUCCESSFUL turn on the
    SAME session commits normally, with its own ordinary bounded
    eviction and nothing else."""
    from herdr_brain.memory import MAX_MESSAGES

    ring_parked, release = _park_commit_window(monkeypatch, "GHOST-ANSWER-MARKER")
    seed_asks = MAX_MESSAGES // 2  # 8 turns × (user+assistant) = a FULL ring
    responses = [text_response(f"SEED-ANSWER-{i}") for i in range(1, seed_asks + 1)]
    responses += [
        text_response("GHOST-ANSWER-MARKER"),  # the cancelled turn
        text_response("CONCURRENT-ANSWER"),    # same-session accepted turn
        text_response("VERIFY-ANSWER"),        # the turn whose prompt we inspect
    ]
    scripted = ScriptedLLM(responses)
    registry = SpeechRegistry()
    cfg = Settings(**{**SETTINGS_KWARGS, "audio_dir": str(tmp_path / "audio")})

    def llm_factory(app_cfg, tools):
        stub_tools = BrainTools(app_cfg, herdr=StubHerdr(agents=[ACTIVE_AGENT]))
        return BrainLLM(app_cfg, stub_tools, client=scripted)

    app = create_app(
        settings=cfg,
        llm_factory=llm_factory,
        tts_renderer=FakeTTS(),
        speech_registry=registry,
    )
    client = TestClient(app)

    # Fill the ring to capacity through the public interface.
    for i in range(1, seed_asks + 1):
        asked = client.post(
            "/ask", json={"text": f"SEED-QUESTION-{i}", "session_id": "s1"}
        )
        assert asked.status_code == 200

    payload = speech_body(text="pregunta fantasma")
    state = {"body_sent": False, "disconnected": False, "messages": []}

    async def receive():
        if not state["body_sent"]:
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

    async def scenario():
        task = asyncio.ensure_future(app(_raw_ask_scope(), receive, send))
        await asyncio.to_thread(ring_parked.wait, 10)   # ghost turn parked in-window
        state["disconnected"] = True                     # its client dies
        job = registry.get(SPEECH_ID)
        await asyncio.to_thread(job.cancel_event.wait, 10)  # watch fired the abort
        # While the doomed turn hangs in its window, a same-session
        # concurrent turn completes and is ACCEPTED.
        concurrent = await asyncio.to_thread(
            client.post,
            "/ask",
            json={"text": "pregunta concurrente", "session_id": "s1"},
        )
        assert concurrent.status_code == 200
        release.set()                                    # doom the parked turn
        await asyncio.wait_for(task, timeout=15.0)

    run_async(scenario())

    assert response_start(state) == 499
    assert registry.get(SPEECH_ID).phase == "cancelled"
    # The rejected turn wrote nothing to SQLite (the concurrent turn did).
    turns = client.get("/call-history").json()["turns"]
    assert len(turns) == seed_asks * 2 + 2  # seeds + concurrent pair, no ghost
    assert not any("fantasma" in (t.get("text") or "") for t in turns)
    assert not any("GHOST-ANSWER" in (t.get("text") or "") for t in turns)

    # THE L34 BLOCKER — the next provider prompt must show the ring as
    # if the rejected turn never existed: the concurrent pair present,
    # the seed context kept up to the concurrent turn's OWN ordinary
    # eviction (the two oldest), and no ghost anywhere.
    verify = client.post("/ask", json={"text": "verifica el contexto", "session_id": "s1"})
    assert verify.status_code == 200
    contents = [m.get("content") or "" for m in scripted.create_kwargs[-1]["messages"]]
    assert any("verifica el contexto" in c for c in contents)  # sanity: real prompt
    assert not any("GHOST-ANSWER-MARKER" in c for c in contents), "ghost answer survived"
    assert not any("pregunta fantasma" in c for c in contents), "ghost question survived"
    assert any("pregunta concurrente" in c for c in contents), "concurrent pair missing"
    assert any("CONCURRENT-ANSWER" in c for c in contents), "concurrent answer missing"
    assert any("SEED-QUESTION-2" in c for c in contents), (
        "prior context lost beyond the concurrent turn's own eviction"
    )
    assert not any("SEED-QUESTION-1" in c for c in contents), (
        "normal bounded eviction of the accepted pair must still happen"
    )
    # Ring stays bounded: the verification turn's own commit evicts in
    # order, never growing memory.
    final_turns = client.get("/call-history").json()["turns"]
    assert len(final_turns) == seed_asks * 2 + 4  # + concurrent, + verify pair


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
