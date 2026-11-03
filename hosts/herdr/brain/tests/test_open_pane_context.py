"""S2/S4 (T2): consulting OTHER currently-open Herdr panes by voice.

The selected/request pane stays the default and the preferred pane. A
model-named pane resolves UNIQUELY against the CURRENT open listing
(``list_open_sessions`` is the discovery surface; there is no second
catalog), and every failure mode — unknown, stale, ambiguous,
unavailable — fails closed with an error string instead of silently
reading the selected or another pane. Consults are read-only: they
never mutate the global selected pane and never redirect later sends,
approvals or dialogs. The consult rides the SAME bounded, masked
transcript path as the selected-pane read.
"""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from herdr_brain.approval import PROPOSED, ApprovalGateStore
from herdr_brain.config import Settings
from herdr_brain.herdr import AgentInfo, HerdrError
from herdr_brain.llm import SYSTEM_PROMPT, BrainLLM
from herdr_brain.tools import TOOLS_SCHEMA, BrainTools

# Same secret shape as the masking regressions: a realistic live-secret
# token that must never reach a provider request.
KEY = "sk-ant-" + "Z" * 24

OTHER_PANE = "w1:p2"
OTHER_SESSION = "ses_open00000other"


def other_agent(**overrides) -> AgentInfo:
    """A second, non-focused open pane (the consult target)."""
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


def transcript_db(tmp_path, session_value, turns) -> str:
    """Materializes an OpenCode store serving ``turns`` for one session."""
    db = tmp_path / f"{session_value}.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INT, data TEXT)"
    )
    conn.execute(
        "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INT, data TEXT)"
    )
    for i, (role, text) in enumerate(turns):
        conn.execute(
            "INSERT INTO message VALUES (?, ?, ?, ?)",
            (f"m{i}", session_value, 1000 + i, json.dumps({"role": role})),
        )
        conn.execute(
            "INSERT INTO part VALUES (?, ?, ?, ?, ?)",
            (f"m{i}-p1", f"m{i}", session_value, 1000 + i,
             json.dumps({"type": "text", "text": text})),
        )
    conn.commit()
    conn.close()
    return str(db)


class SequencedStub:
    """Wraps a StubHerdr whose ``list_agents`` answers from a script.

    Models pane churn: the first listing (the inventory the model saw)
    includes panes a later listing no longer reports.
    """

    def __init__(self, stub, listings):
        self._stub = stub
        self._listings = list(listings)

    def __getattr__(self, name):
        return getattr(self._stub, name)

    def list_agents(self):
        if self._listings:
            return self._listings.pop(0)
        return self._stub.list_agents()


# -- LLM fakes (same shape as tests.test_llm, kept local on purpose) ----------


def tool_call_response(call_id, name, arguments, content=None):
    message = SimpleNamespace(
        content=content,
        tool_calls=[
            SimpleNamespace(
                id=call_id,
                function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
            )
        ],
    )
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def text_response(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=content, tool_calls=None))])


class ScriptedLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.create_kwargs: list = []
        outer = self
        completions = SimpleNamespace()

        def create(**kwargs):
            outer.create_kwargs.append(kwargs)
            return outer.responses.pop(0)

        completions.create = create
        self.chat = SimpleNamespace(completions=completions)


def make_brain(settings: Settings, stub, responses):
    tools = BrainTools(settings, herdr=stub)
    llm = BrainLLM(settings, tools, client=ScriptedLLM(responses))
    llm.attach_approval_store(ApprovalGateStore(timeout_s=settings.approval_timeout_s))
    return llm, tools


class TestSelectedPaneStillDefault:
    """A missing (or empty) pane argument keeps the old selected-pane read."""

    @pytest.mark.parametrize("pane_arg", [None, "", "   "])
    def test_missing_pane_argument_reads_the_selected_pane(
        self, settings, make_stub, active_agent, pane_arg
    ):
        stub = make_stub(agents=[active_agent, other_agent()], screen="selected tail")
        tools = BrainTools(settings, herdr=stub)
        # Hermetic store: no transcript, so the read lands on the screen
        # fallback — the recorded pane proves which pane was read.
        arguments = {"n_turns": 5}
        if pane_arg is not None:
            arguments["pane_id"] = pane_arg
        out = tools.dispatch("read_transcript", arguments, target=active_agent)
        assert "selected tail" in out
        assert [call["pane_id"] for call in stub.screen_calls] == [active_agent.pane_id]

    def test_missing_pane_argument_prefers_the_selected_transcript(
        self, settings, make_stub, active_agent, monkeypatch, tmp_path
    ):
        monkeypatch.setenv(
            "OPENCODE_DB",
            transcript_db(tmp_path, active_agent.session_value,
                          [("user", "Selected session turn.")]),
        )
        tools = BrainTools(settings, herdr=make_stub(agents=[active_agent, other_agent()]))
        out = tools.dispatch("read_transcript", {}, target=active_agent)
        assert "Selected session turn." in out
        assert "transcript unavailable" not in out


class TestNamedOpenPane:
    """A pane_id from the open inventory consults exactly that pane."""

    def test_reads_the_named_pane_transcript(
        self, settings, make_stub, active_agent, monkeypatch, tmp_path
    ):
        monkeypatch.setenv(
            "OPENCODE_DB",
            transcript_db(tmp_path, OTHER_SESSION,
                          [("assistant", "Pantalla del otro agente.")]),
        )
        stub = make_stub(agents=[active_agent, other_agent()])
        tools = BrainTools(settings, herdr=stub)
        out = tools.dispatch("read_transcript", {"pane_id": OTHER_PANE}, target=active_agent)
        assert "Pantalla del otro agente." in out
        assert stub.screen_calls == []  # transcript served: no fallback read

    def test_named_pane_without_transcript_falls_back_to_its_own_screen(
        self, settings, make_stub, active_agent
    ):
        stub = make_stub(agents=[active_agent, other_agent()], screen="other screen")
        tools = BrainTools(settings, herdr=stub)
        out = tools.dispatch("read_transcript", {"pane_id": OTHER_PANE}, target=active_agent)
        assert out.startswith("[transcript unavailable")
        assert "other screen" in out
        assert [call["pane_id"] for call in stub.screen_calls] == [OTHER_PANE]

    def test_named_pane_read_stays_bounded(
        self, settings, make_stub, active_agent, monkeypatch, tmp_path
    ):
        turns = [
            ("user" if i % 2 else "assistant", f"texto numero {i}")
            for i in range(1, 4)
        ]
        monkeypatch.setenv("OPENCODE_DB", transcript_db(tmp_path, OTHER_SESSION, turns))
        stub = make_stub(agents=[active_agent, other_agent()])
        tools = BrainTools(settings, herdr=stub)
        out = tools.dispatch(
            "read_transcript", {"pane_id": OTHER_PANE, "n_turns": 2}, target=active_agent
        )
        assert "texto numero 3" in out and "texto numero 2" in out
        assert "texto numero 1" not in out  # the same window cap as ever


class TestFailClosed:
    """Unknown, stale, ambiguous and unavailable identities never read."""

    @pytest.mark.parametrize("pane_arg", [False, 0, [], {}, True, 1])
    def test_non_string_pane_id_never_reads_the_selected_pane(
        self, settings, make_stub, active_agent, pane_arg
    ):
        stub = make_stub(agents=[active_agent, other_agent()], screen="selected tail")
        tools = BrainTools(settings, herdr=stub)

        out = tools.dispatch("read_transcript", {"pane_id": pane_arg}, target=active_agent)

        assert out.startswith("error: invalid arguments for read_transcript")
        assert stub.screen_calls == [] and stub.prompt_calls == []

    def test_unknown_pane_id_fails_closed(
        self, settings, make_stub, active_agent
    ):
        stub = make_stub(agents=[active_agent, other_agent()], screen="selected tail")
        tools = BrainTools(settings, herdr=stub)
        out = tools.dispatch("read_transcript", {"pane_id": "w9:gone"}, target=active_agent)
        assert out.startswith("error: unknown pane")
        assert "list_open_sessions" in out
        # Nothing was read at all — least of all the selected pane.
        assert stub.screen_calls == [] and stub.prompt_calls == []

    def test_stale_pane_id_fails_closed(
        self, settings, make_stub, active_agent
    ):
        # The model saw the pane in an earlier inventory; it has since
        # closed. Only CURRENT listings may answer an identity.
        stub = SequencedStub(
            make_stub(agents=[active_agent, other_agent()]),
            listings=[[active_agent]],  # the fresh listing no longer has it
        )
        tools = BrainTools(settings, herdr=stub)
        out = tools.dispatch("read_transcript", {"pane_id": OTHER_PANE}, target=active_agent)
        assert out.startswith("error: unknown pane")
        assert stub.screen_calls == [] and stub.prompt_calls == []

    def test_ambiguous_pane_id_fails_closed(
        self, settings, make_stub, active_agent
    ):
        duplicated = [other_agent(title="A"), other_agent(title="B")]
        stub = make_stub(agents=[active_agent, *duplicated])
        tools = BrainTools(settings, herdr=stub)
        out = tools.dispatch("read_transcript", {"pane_id": OTHER_PANE}, target=active_agent)
        assert out.startswith("error: ambiguous pane")
        assert stub.screen_calls == [] and stub.prompt_calls == []

    def test_unavailable_listing_fails_closed(
        self, settings, make_stub, active_agent, monkeypatch
    ):
        stub = make_stub(agents=[active_agent, other_agent()])
        monkeypatch.setattr(stub, "list_agents", Mock(side_effect=HerdrError("list failed")))
        tools = BrainTools(settings, herdr=stub)
        out = tools.dispatch("read_transcript", {"pane_id": OTHER_PANE}, target=active_agent)
        assert out.startswith("error listing open panes")
        assert stub.screen_calls == [] and stub.prompt_calls == []

    def test_non_string_pane_id_fails_closed(self, settings, make_stub, active_agent):
        tools = BrainTools(settings, herdr=make_stub(agents=[active_agent, other_agent()]))
        out = tools.dispatch("read_transcript", {"pane_id": 5}, target=active_agent)
        assert out.startswith("error: invalid arguments for read_transcript")


class TestSelectionAndWriteIsolation:
    """Consulting a pane never redirects writes or the selection."""

    def test_consult_never_mutates_the_selected_pane(
        self, settings, make_stub, active_agent, monkeypatch, tmp_path
    ):
        monkeypatch.setenv(
            "OPENCODE_DB",
            transcript_db(tmp_path, OTHER_SESSION, [("assistant", "other work")]),
        )
        stub = make_stub(agents=[active_agent, other_agent()])
        tools = BrainTools(settings, herdr=stub)
        selected = tools.resolve_target(active_agent.pane_id)
        assert selected.pane_id == active_agent.pane_id

        tools.dispatch("read_transcript", {"pane_id": OTHER_PANE}, target=selected)

        # The global selection is untouched: same object, same inventory.
        assert tools.last_active is selected
        assert json.loads(tools.list_open_sessions())["selected_pane_id"] == (
            active_agent.pane_id
        )

    def test_consult_does_not_redirect_a_later_send(
        self, settings, make_stub, active_agent, monkeypatch, tmp_path
    ):
        monkeypatch.setenv(
            "OPENCODE_DB",
            transcript_db(tmp_path, OTHER_SESSION, [("assistant", "other work")]),
        )
        stub = make_stub(agents=[active_agent, other_agent()])
        tools = BrainTools(settings, herdr=stub)
        selected = tools.resolve_target(active_agent.pane_id)

        tools.dispatch("read_transcript", {"pane_id": OTHER_PANE}, target=selected)
        tools.dispatch("send_to_session", {"text": "sigue"}, target=selected)

        assert [call["pane_id"] for call in stub.prompt_calls] == [active_agent.pane_id]
        assert stub.screen_calls == []  # the consult was served by its transcript

    def test_llm_consult_keeps_the_request_target_for_gated_sends(
        self, settings, make_stub, active_agent, monkeypatch, tmp_path
    ):
        monkeypatch.setenv(
            "OPENCODE_DB",
            transcript_db(tmp_path, OTHER_SESSION,
                          [("assistant", "El otro agente espera input.")]),
        )
        stub = make_stub(agents=[active_agent, other_agent()])
        llm, tools = make_brain(
            settings, stub,
            responses=[
                tool_call_response("c1", "read_transcript", {"pane_id": OTHER_PANE}),
                tool_call_response("c2", "send_to_session", {"text": "run lint"}),
                text_response("Consultado el otro panel; el envío queda propuesto."),
            ],
        )
        result = llm.ask("qué está haciendo el otro panel? y dile que corra lint")

        # The consult really served the OTHER pane's transcript...
        tool_messages = [
            m for m in llm._client.create_kwargs[-1]["messages"] if m.get("role") == "tool"
        ]
        assert "El otro agente espera input." in tool_messages[0]["content"]
        # ...and the turn's identity stayed the request pane throughout.
        assert stub.prompt_calls == []  # the send froze behind the gate
        gate = llm._approval_store.current("default")
        assert gate is not None and gate.state == PROPOSED
        assert gate.action.pane_id == active_agent.pane_id
        assert result["pane_id"] == active_agent.pane_id
        assert tools.last_active.pane_id == active_agent.pane_id

    def test_consult_result_is_masked_at_the_provider_boundary(
        self, settings, make_stub, active_agent, monkeypatch, tmp_path
    ):
        monkeypatch.setenv(
            "OPENCODE_DB",
            transcript_db(tmp_path, OTHER_SESSION,
                          [("assistant", f"clave {KEY} filtrada")]),
        )
        stub = make_stub(agents=[active_agent, other_agent()])
        llm, _ = make_brain(
            settings, stub,
            responses=[
                tool_call_response("c1", "read_transcript", {"pane_id": OTHER_PANE}),
                text_response("listo"),
            ],
        )
        llm.ask("qué pasa en el otro panel?")
        assert KEY not in json.dumps(llm._client.create_kwargs[-1]["messages"])


class TestDiscoverySurfaces:
    """The model can discover and use other open panes."""

    def test_read_transcript_schema_offers_pane_id(self):
        by_name = {tool["function"]["name"]: tool["function"] for tool in TOOLS_SCHEMA}
        schema = by_name["read_transcript"]
        assert schema["parameters"]["required"] == []
        pane_id = schema["parameters"]["properties"]["pane_id"]
        assert pane_id["type"] == "string"
        assert "list_open_sessions" in pane_id["description"]

    def test_read_transcript_description_teaches_the_consult(self):
        by_name = {tool["function"]["name"]: tool["function"]["description"] for tool in TOOLS_SCHEMA}
        description = by_name["read_transcript"]
        assert "pane_id" in description
        assert "list_open_sessions" in description
        # The selected session stays the FIRST-class case.
        assert "FIRST" in description and "selected session" in description

    def test_system_prompt_teaches_the_consult_route(self):
        assert "pane_id" in SYSTEM_PROMPT
        assert "list_open_sessions" in SYSTEM_PROMPT
        # Read-only discipline: consulting never retargets writes, and a
        # failed identity is relayed, never substituted.
        assert "never changes which pane" in SYSTEM_PROMPT
        assert "never silently read" in SYSTEM_PROMPT
