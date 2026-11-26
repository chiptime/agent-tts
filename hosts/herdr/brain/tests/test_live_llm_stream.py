"""W3-P1 unit tests: GLM token streaming inside BrainLLM (PRD-06 FR-01/04/06).

The provider-level fake speaks both shapes: a scripted chunk stream when
``stream=True`` is requested and scripted full responses otherwise, so
the tests pin the streaming round, the tool loop's gate preservation,
the FR-06 degradation paths and the cooperative disconnect checks
without any network.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from herdr_brain.config import Settings
from herdr_brain.llm import BrainLLM, TurnCancelled
from herdr_brain.memory import ConversationStore
from herdr_brain.tools import BrainTools
from tests.conftest import SETTINGS_KWARGS, StubHerdr, ACTIVE_AGENT
from tests.test_llm import text_response


# -- chunk/response doubles ----------------------------------------------------


def content_chunk(text: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text, tool_calls=None),
                                 finish_reason=None)]
    )


def tool_chunk(call_id="c1", name="list_open_sessions", args="{}", index=0):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            delta=SimpleNamespace(
                content=None,
                tool_calls=[SimpleNamespace(index=index, id=call_id,
                                            function=SimpleNamespace(name=name,
                                                                     arguments=args))],
            ),
            finish_reason=None,
        )]
    )


def stop_chunk():
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=None),
                                 finish_reason="stop")]
    )


class ScriptedStreamClient:
    """Fake OpenAI-compatible client: ``stream=True`` pops a scripted
    chunk iterator; anything else pops a scripted full response."""

    def __init__(self, streams=(), responses=()):
        self.streams = list(streams)
        self.responses = list(responses)
        self.create_kwargs: list[dict] = []

    def _create(self, **kwargs):
        self.create_kwargs.append(kwargs)
        if kwargs.get("stream"):
            chunks = self.streams.pop(0)
            return iter(chunks)
        return self.responses.pop(0)

    class _Completions:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            return self._outer._create(**kwargs)

    @property
    def chat(self):
        return SimpleNamespace(completions=self._Completions(self))


class StreamRejectingClient(ScriptedStreamClient):
    """Answers stream=True with an exception but serves the legacy
    whole-answer shape without the kwarg (FR-01 fallback)."""

    def _create(self, **kwargs):
        self.create_kwargs.append(kwargs)
        if kwargs.get("stream"):
            raise ValueError("streaming unsupported")
        return self.responses.pop(0)


class StreamIgnorerClient(ScriptedStreamClient):
    """Ignores stream=True entirely and returns a full response (the
    classic scripted-client shape must keep working under a sink)."""

    def _create(self, **kwargs):
        self.create_kwargs.append(kwargs)
        return self.responses.pop(0)


def make_llm(settings, client, store=None):
    tools = BrainTools(settings, herdr=StubHerdr(agents=[ACTIVE_AGENT]))
    llm = BrainLLM(settings, tools, client=client)
    if store is not None:
        llm.attach_store(store)
    return llm, tools


@pytest.fixture
def settings():
    return Settings(**SETTINGS_KWARGS)


def test_brain_llm_declares_the_sentence_sink_marker(settings):
    assert BrainLLM.takes_sentence_sink is True


def test_sink_receives_sentences_in_order_and_answer_is_complete(settings):
    stream = [
        content_chunk("Primera"),
        content_chunk(" frase."),
        content_chunk(" Segunda"),
        content_chunk(" final."),
        stop_chunk(),
    ]
    client = ScriptedStreamClient(streams=[stream])
    llm, _ = make_llm(settings, client)
    sink_calls: list[str] = []

    result = llm.ask("andá", sentence_sink=sink_calls.append)

    assert sink_calls == ["Primera frase.", "Segunda final."]
    assert result["answer"] == "Primera frase. Segunda final."
    assert client.create_kwargs[0]["stream"] is True


def test_tool_round_streams_then_gate_is_never_bypassed(settings):
    """S4: a gated send freezes into a gate exactly as before; the sink
    only ever sees the FINAL answer's sentences."""
    stream_tool = [tool_chunk(call_id="g1", name="send_to_session",
                              args='{"text": "hola"}'), stop_chunk()]
    stream_final = [
        content_chunk("El envío quedó"),
        content_chunk(" pendiente de tu confirmación."),
        stop_chunk(),
    ]
    client = ScriptedStreamClient(streams=[stream_tool, stream_final])
    llm, tools = make_llm(settings, client)
    from herdr_brain.approval import ApprovalGateStore

    approval_store = ApprovalGateStore(timeout_s=settings.approval_timeout_s)
    llm.attach_approval_store(approval_store)
    sink_calls: list[str] = []

    result = llm.ask("mandale hola al agente", sentence_sink=sink_calls.append)

    assert result["approval"] is not None, "the gate must open exactly as before"
    assert tools._herdr.prompt_calls == [], "the gated send must never dispatch"
    assert sink_calls == ["El envío quedó pendiente de tu confirmación."]
    # The tool result rode the second provider round as a tool message.
    second = client.create_kwargs[1]["messages"]
    assert any(m.get("role") == "tool" for m in second)


def test_legacy_fallback_when_provider_ignores_stream(settings):
    """The classic scripted-client shape (full response despite
    stream=True) degrades to the whole-answer path: same answer, sink
    still speaks sentence-by-sentence (FR-01 additive fallback)."""
    client = StreamIgnorerClient(
        responses=[text_response("Todo verde. Nada rojo.")]
    )
    llm, _ = make_llm(settings, client)
    sink_calls: list[str] = []

    result = llm.ask("cómo va", sentence_sink=sink_calls.append)

    assert result["answer"] == "Todo verde. Nada rojo."
    assert sink_calls == ["Todo verde.", "Nada rojo."]


def test_stream_creation_failure_falls_back_to_whole_answer(settings):
    client = StreamRejectingClient(responses=[text_response("Plan alterno. Funciona.")])
    llm, _ = make_llm(settings, client)
    sink_calls: list[str] = []

    result = llm.ask("cómo va", sentence_sink=sink_calls.append)

    assert result["answer"] == "Plan alterno. Funciona."
    assert sink_calls == ["Plan alterno.", "Funciona."]


def test_mid_stream_failure_keeps_the_prefix(settings):
    """FR-06: a stream that dies after emitting usable text keeps the
    prefix as the answer — the turn is never lost, and the tail of the
    prefix is flushed to the sink."""

    class DyingStream:
        def __init__(self):
            self._iter = iter([
                content_chunk("Primera frase. Segunda"),
            ])

        def __iter__(self):
            return self

        def __next__(self):
            chunk = next(self._iter)
            # The NEXT call dies: model the provider dropping mid-answer.
            self._iter = iter(self._explode())
            return chunk

        def _explode(self):
            raise RuntimeError("provider dropped the stream")
            yield  # pragma: no cover

    class DyingClient(ScriptedStreamClient):
        def _create(self, **kwargs):
            self.create_kwargs.append(kwargs)
            if kwargs.get("stream"):
                return DyingStream()
            return self.responses.pop(0)

    client = DyingClient(responses=[text_response("never reached")])
    llm, _ = make_llm(settings, client)
    sink_calls: list[str] = []

    result = llm.ask("cómo va", sentence_sink=sink_calls.append)

    assert result["answer"] == "Primera frase. Segunda"
    assert sink_calls == ["Primera frase.", "Segunda"]


def test_stream_failure_without_prefix_retries_whole_answer(settings):
    """FR-06: a stream dying BEFORE any content retries the round
    through the legacy whole-answer path."""

    class ExplodingStream:
        def __iter__(self):
            return self

        def __next__(self):
            raise RuntimeError("provider dropped immediately")

    class ExplodingClient(ScriptedStreamClient):
        def _create(self, **kwargs):
            self.create_kwargs.append(kwargs)
            if kwargs.get("stream"):
                return ExplodingStream()
            return self.responses.pop(0)

    client = ExplodingClient(responses=[text_response("Rescate completo. Sano.")])
    llm, _ = make_llm(settings, client)
    sink_calls: list[str] = []

    result = llm.ask("cómo va", sentence_sink=sink_calls.append)

    assert result["answer"] == "Rescate completo. Sano."
    assert sink_calls == ["Rescate completo.", "Sano."]
    assert len(client.create_kwargs) == 2  # stream attempt + legacy retry


def test_disconnect_mid_stream_raises_turn_cancelled(settings):
    import threading

    client = ScriptedStreamClient(streams=[[content_chunk("A"), content_chunk("B")]])
    llm, _ = make_llm(settings, client)
    cancel_event = threading.Event()
    cancel_event.set()

    with pytest.raises(TurnCancelled):
        llm.ask("cómo va", cancel_event=cancel_event, sentence_sink=lambda s: None)


def test_answer_still_lands_in_the_store(settings):
    stream = [content_chunk("Guardado. Completo."), stop_chunk()]
    client = ScriptedStreamClient(streams=[stream])
    store = ConversationStore()
    llm, _ = make_llm(settings, client, store=store)

    result = llm.ask("pregunta", sentence_sink=lambda s: None)

    turns = list(store.history("default"))
    assert [t.role for t in turns] == ["user", "assistant"]
    assert turns[-1].content == "Guardado. Completo."
