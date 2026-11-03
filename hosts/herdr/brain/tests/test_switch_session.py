"""S5/S7 (T4): switching the call's target pane BY VOICE, server side.

`switch_session` retargets the call: it resolves the named pane against
the CURRENT open listing (fail-closed like every named-pane read), makes
it the request target for the REST of the turn (a same-turn gated send
freezes against the new pane), publishes the shared selection so the
other device follows, and rides the /ask response as an additive
`switch_pane` field. An approved `create_session` replay retargets the
call to the new panel the same way. Switching never executes or bypasses
any approval gate.
"""

from __future__ import annotations

import pytest

from herdr_brain.config import Settings
from herdr_brain.herdr import AgentInfo, HerdrError
from herdr_brain.server import create_app
from herdr_brain.tools import TOOLS_SCHEMA, BrainTools
from tests.conftest import SETTINGS_KWARGS, ACTIVE_AGENT
from tests.test_llm import ScriptedLLM, make_brain, text_response, tool_call_response

OTHER_PANE = "w1:p2"
OTHER_SESSION = "ses_open0000other"


def other_agent(**overrides) -> AgentInfo:
    fields = dict(
        pane_id=OTHER_PANE,
        agent="opencode",
        status="idle",
        session_kind="id",
        session_value=OTHER_SESSION,
        cwd="/other",
        title="Other",
        focused=False,
    )
    fields.update(overrides)
    return AgentInfo(**fields)


class FakeCallContext:
    """Records the shared-selection publishes."""

    def __init__(self):
        self.selects: list = []

    def select(self, pane_id, locale=None):
        self.selects.append(pane_id)
        return {"pane_id": pane_id}

    def snapshot(self):
        return {"pane_id": self.selects[-1] if self.selects else None}


class SequencedStub:
    """Wraps a stub whose ``list_agents`` answers from a script."""

    def __init__(self, stub, listings):
        self._stub = stub
        self._listings = list(listings)

    def __getattr__(self, name):
        return getattr(self._stub, name)

    def list_agents(self):
        if self._listings:
            return self._listings.pop(0)
        return self._stub.list_agents()


class TestSwitchTool:
    def test_switch_resolves_tracks_confirms_and_publishes(
        self, settings, make_stub, active_agent
    ):
        stub = make_stub(agents=[active_agent, other_agent()])
        tools = BrainTools(settings, herdr=stub)
        ctx = FakeCallContext()
        tools.attach_call_context(ctx)

        out = tools.dispatch(
            "switch_session", {"pane_id": OTHER_PANE}, target=active_agent
        )

        assert OTHER_PANE in out and "Other" in out
        assert tools.last_active.pane_id == OTHER_PANE
        assert ctx.selects == [OTHER_PANE]

    def test_unknown_pane_fails_closed(self, settings, make_stub, active_agent):
        stub = make_stub(agents=[active_agent, other_agent()])
        tools = BrainTools(settings, herdr=stub)
        tools.resolve_target(active_agent.pane_id)
        ctx = FakeCallContext()
        tools.attach_call_context(ctx)

        out = tools.dispatch(
            "switch_session", {"pane_id": "w9:gone"}, target=active_agent
        )

        assert out.startswith("error: unknown pane")
        assert tools.last_active.pane_id == active_agent.pane_id
        assert ctx.selects == []

    def test_stale_pane_fails_closed(self, settings, make_stub, active_agent):
        stub = SequencedStub(
            make_stub(agents=[active_agent, other_agent()]),
            listings=[[active_agent, other_agent()], [active_agent]],
        )
        tools = BrainTools(settings, herdr=stub)
        tools.resolve_target(active_agent.pane_id)

        out = tools.dispatch(
            "switch_session", {"pane_id": OTHER_PANE}, target=active_agent
        )

        assert out.startswith("error: unknown pane")
        assert tools.last_active.pane_id == active_agent.pane_id

    def test_ambiguous_pane_fails_closed(self, settings, make_stub, active_agent):
        stub = make_stub(
            agents=[active_agent, other_agent(title="A"), other_agent(title="B")]
        )
        tools = BrainTools(settings, herdr=stub)
        tools.resolve_target(active_agent.pane_id)

        out = tools.dispatch(
            "switch_session", {"pane_id": OTHER_PANE}, target=active_agent
        )

        assert out.startswith("error: ambiguous pane")
        assert tools.last_active.pane_id == active_agent.pane_id

    def test_non_string_pane_id_is_rejected(self, settings, make_stub, active_agent):
        tools = BrainTools(settings, herdr=make_stub(agents=[active_agent, other_agent()]))
        tools.resolve_target(active_agent.pane_id)

        out = tools.dispatch("switch_session", {"pane_id": 5}, target=active_agent)

        assert out.startswith("error: invalid arguments for switch_session")
        assert tools.last_active.pane_id == active_agent.pane_id

    def test_switch_works_without_an_attached_call_context(
        self, settings, make_stub, active_agent
    ):
        tools = BrainTools(settings, herdr=make_stub(agents=[active_agent, other_agent()]))

        out = tools.dispatch(
            "switch_session", {"pane_id": OTHER_PANE}, target=active_agent
        )

        assert OTHER_PANE in out
        assert tools.last_active.pane_id == OTHER_PANE

    def test_schema_teaches_the_switch(self):
        by_name = {tool["function"]["name"]: tool["function"] for tool in TOOLS_SCHEMA}
        schema = by_name["switch_session"]
        assert schema["parameters"]["required"] == ["pane_id"]
        pane_id = schema["parameters"]["properties"]["pane_id"]
        assert pane_id["type"] == "string"
        assert "list_open_sessions" in pane_id["description"]


class TestSameTurnRetarget:
    def test_switch_then_send_freezes_the_gate_on_the_new_pane(
        self, settings, make_stub, active_agent
    ):
        from herdr_brain.approval import PROPOSED

        stub = make_stub(agents=[active_agent, other_agent()])
        llm, tools = make_brain(
            settings,
            stub,
            responses=[
                tool_call_response("c1", "switch_session", {"pane_id": OTHER_PANE}),
                tool_call_response("c2", "send_to_session", {"text": "corre lint"}),
                text_response("Cambiado y enviado a aprobación."),
            ],
        )
        tools.attach_call_context(FakeCallContext())

        result = llm.ask("cambiate al panel Other y decile que corra lint",
                         session_id="s1", pane_id=active_agent.pane_id)

        gate = llm._approval_store.current("s1")
        assert gate is not None and gate.state == PROPOSED
        assert gate.action.pane_id == OTHER_PANE  # the send froze on the NEW pane
        assert [call["pane_id"] for call in stub.prompt_calls] == []  # gated, not sent
        assert result["switch_pane"]["pane_id"] == OTHER_PANE
        assert result["switch_pane"]["title"] == "Other"

    def test_switch_alone_rides_the_response(self, settings, make_stub, active_agent):
        llm, _tools = make_brain(
            settings,
            make_stub(agents=[active_agent, other_agent()]),
            responses=[
                tool_call_response("c1", "switch_session", {"pane_id": OTHER_PANE}),
                text_response("Listo, apuntando al otro panel."),
            ],
        )

        result = llm.ask("cambiate al panel Other", session_id="s1")

        assert result["switch_pane"]["pane_id"] == OTHER_PANE
        assert llm._approval_store.current("s1") is None

    def test_the_switch_does_not_leak_into_a_later_turn(
        self, settings, make_stub, active_agent
    ):
        llm, _tools = make_brain(
            settings,
            make_stub(agents=[active_agent, other_agent()]),
            responses=[
                tool_call_response("c1", "switch_session", {"pane_id": OTHER_PANE}),
                text_response("Cambiado."),
                text_response("Una respuesta normal."),
            ],
        )

        first = llm.ask("cambiate al panel Other", session_id="s1")
        second = llm.ask("qué está pasando?", session_id="s1")

        assert first["switch_pane"]["pane_id"] == OTHER_PANE
        assert "switch_pane" not in second


class FakeSwitchLLM:
    """/ask double: returns a canned result, optionally proposing a gate."""

    def __init__(self, result=None, propose_create=None):
        self.result = result or {"answer": "ok", "pane_id": "w1:p9", "agent": "opencode"}
        self.propose_create = propose_create

    def attach_store(self, store):
        pass

    def attach_approval_store(self, store):
        self.approval_store = store

    def ask(self, text, session_id=None, pane_id=None, **kwargs):
        result = dict(self.result, session_id=session_id or "default",
                      pane_id=pane_id or self.result.get("pane_id"))
        if self.propose_create is not None:
            result["approval"] = self.approval_store.propose(
                session_id or "default", text="", **self.propose_create
            )
        return result


class FakeTTS:
    def __call__(self, settings, text, out_path):
        out_path.write_bytes(b"ID3-fake")
        return out_path


def make_client(tmp_path, llm, monkeypatch, stub=None):
    from tests.test_tools_create_session import CreateStub

    if stub is None:
        stub = CreateStub(agents=[ACTIVE_AGENT])
    monkeypatch.setattr("herdr_brain.tools.create_mux", lambda _settings: stub)
    cfg = Settings(**{**SETTINGS_KWARGS, "state_dir": str(tmp_path / "state")})
    from fastapi.testclient import TestClient

    app = create_app(settings=cfg, llm_factory=lambda _c, _t: llm, tts_renderer=FakeTTS())
    return TestClient(app)


class TestServerSurfaces:
    def test_ask_response_carries_switch_pane(self, tmp_path, monkeypatch):
        llm = FakeSwitchLLM(result={
            "answer": "Cambiado.",
            "pane_id": "w1:p9",
            "agent": "opencode",
            "switch_pane": {"pane_id": OTHER_PANE, "title": "Other"},
        })
        client = make_client(tmp_path, llm, monkeypatch)

        resp = client.post("/ask", json={"text": "cambiate al panel Other"})

        assert resp.status_code == 200
        assert resp.json()["switch_pane"] == {"pane_id": OTHER_PANE, "title": "Other"}

    def test_approved_create_retargets_the_call(
        self, tmp_path, monkeypatch
    ):
        from tests.test_tools_create_session import NEW_PANE

        llm = FakeSwitchLLM(propose_create=dict(
            tool="create_session", agent_kind="pi", title="Pagos", task=""
        ))
        client = make_client(tmp_path, llm, monkeypatch)

        opened = client.post("/ask", json={"text": "abrí un panel de Pagos"}).json()
        gate_id = opened["approval"]["gate_id"]
        approved = client.post(f"/approval/{gate_id}/approve").json()

        assert approved["switch_pane"]["pane_id"] == NEW_PANE
        assert approved["switch_pane"]["title"] == "Pagos"
        assert client.get("/call/context").json()["pane_id"] == NEW_PANE
