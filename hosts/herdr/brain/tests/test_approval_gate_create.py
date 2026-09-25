"""Approval-gate generalization: create_session gates end to end.

Covers the frozen create args (store, cwd included), the payload shape,
the llm interception, the approve replay (dispatch of the patched task,
with the frozen cwd reaching the attach argv) and the PATCH mapping —
plus send-path byte-compatibility spot checks. Fakes everywhere: no real
tab, agent or prompt ever runs.
"""

from __future__ import annotations

import pytest

from herdr_brain.approval import (
    APPROVED,
    PROPOSED,
    ApprovalGateStore,
    CREATE_SESSION,
    SEND_TO_SESSION,
)
from herdr_brain.llm import BrainLLM
from herdr_brain.server import approval_payload
from herdr_brain.tools import BrainTools
from tests.test_llm import (
    ScriptedLLM,
    make_brain,
    text_response,
    tool_call_response,
)


def make_create_stub(make_stub):
    """StubHerdr + create primitives, recording everything."""
    from tests.test_tools_create_session import CreateStub

    return CreateStub(agents=make_stub()._agents)


class TestStoreCreateGates:
    def test_propose_freezes_create_args(self):
        store = ApprovalGateStore()
        gate = store.propose(
            "s1",
            text="",
            tool=CREATE_SESSION,
            agent_kind="opencode",
            title="Refactor del login",
            task="corre los tests",
            cwd="/repo",
        )
        assert gate.state == PROPOSED
        assert gate.tool == CREATE_SESSION
        assert gate.action.agent_kind == "opencode"
        assert gate.action.title == "Refactor del login"
        assert gate.action.task == "corre los tests"
        assert gate.action.cwd == "/repo"
        assert gate.action.text == ""  # no send text on create gates

    def test_send_gates_default_cwd_to_empty(self):
        store = ApprovalGateStore()
        gate = store.propose(
            "s1", text="corre lint", pane_id="w1:p9", agent="opencode",
        )
        assert gate.action.cwd == ""  # send actions stay byte-compatible

    def test_patch_edits_task_on_create_and_text_on_send(self):
        store = ApprovalGateStore()
        create = store.propose(
            "s1", text="", tool=CREATE_SESSION,
            agent_kind="opencode", title="T", task="original",
        )
        send = store.propose(
            "s2", text="original", pane_id="w1:p9", agent="opencode",
        )
        patched_create = store.patch(create.gate_id, "tarea nueva")
        patched_send = store.patch(send.gate_id, "texto nuevo")
        assert patched_create.action.task == "tarea nueva"
        assert patched_create.action.text == ""      # send fields untouched
        assert patched_send.action.text == "texto nuevo"
        assert patched_send.action.task is None      # create fields untouched

    def test_expiry_and_supersede_apply_to_create_gates_too(self):
        store = ApprovalGateStore(timeout_s=0)
        first = store.propose(
            "s1", text="", tool=CREATE_SESSION, agent_kind="pi", title="A", task="",
        )
        assert store.get(first.gate_id).state == "expired"  # lazy expiry
        live_store = ApprovalGateStore(timeout_s=60)
        second = live_store.propose(
            "s1", text="", tool=CREATE_SESSION, agent_kind="pi", title="B", task="",
        )
        live_store.propose("s1", text="x", pane_id="p", agent="a")  # new gate supersedes
        assert live_store.get(second.gate_id).state == "superseded"


class TestApprovalPayloadCreate:
    def test_create_payload_shape(self):
        store = ApprovalGateStore()
        gate = store.propose(
            "s1", text="", tool=CREATE_SESSION,
            agent_kind="opencode", title="Refactor", task="corre lint",
            cwd="/repo",
        )
        payload = approval_payload(gate, 60, now=gate.created_at)
        assert payload == {
            "gate_id": gate.gate_id,
            "tool": "create_session",
            "agent": "opencode",
            "title": "Refactor",
            "task": "corre lint",
            "cwd": "/repo",
            "timeout_ms": None,
            "expires_in_s": 60,
        }
        # No pane/text keys on create gates.
        assert "pane_id" not in payload and "text" not in payload

    def test_create_payload_defaults_cwd_to_empty(self):
        store = ApprovalGateStore()
        gate = store.propose(
            "s1", text="", tool=CREATE_SESSION,
            agent_kind="opencode", title="Refactor", task="",
        )
        payload = approval_payload(gate, 60, now=gate.created_at)
        assert payload["cwd"] == ""

    def test_send_payload_unchanged(self):
        store = ApprovalGateStore()
        gate = store.propose(
            "s1", text="corre los tests", timeout_ms=300000,
            pane_id="w1:p9", agent="opencode",
        )
        payload = approval_payload(gate, 60, now=gate.created_at)
        assert payload == {
            "gate_id": gate.gate_id,
            "tool": "send_to_session",
            "pane_id": "w1:p9",
            "agent": "opencode",
            "text": "corre los tests",
            "timeout_ms": 300000,
            "expires_in_s": 60,
        }


class TestLLMGateCreate:
    def test_create_call_freezes_args_and_never_executes(self, settings, make_stub):
        stub = make_create_stub(make_stub)
        llm, _ = make_brain(
            settings, stub,
            responses=[
                tool_call_response(
                    "c1",
                    "create_session",
                    {"agent_kind": "opencode", "title": "Refactor", "task": "corre lint"},
                ),
                text_response("Voy a abrir un panel nuevo para el refactor."),
            ],
        )
        result = llm.ask("abre un panel para refactorizar", session_id="s1")
        assert stub.tab_calls == [] and stub.start_calls == [] and stub.prompt_calls == []
        gate = llm._approval_store.current("s1")
        assert gate is not None and gate.state == PROPOSED
        assert gate.tool == CREATE_SESSION
        assert gate.action.agent_kind == "opencode"
        assert gate.action.title == "Refactor"
        assert gate.action.task == "corre lint"
        assert result["approval"].gate_id == gate.gate_id
        assert result["answer"].startswith("Voy a abrir")

    def test_create_call_freezes_cwd_and_defaults_empty(self, settings, make_stub):
        # With cwd in the tool call: frozen exactly as given.
        llm, _ = make_brain(
            settings, make_create_stub(make_stub),
            responses=[
                tool_call_response(
                    "c1", "create_session",
                    {"agent_kind": "opencode", "title": "Build", "cwd": "/repo"},
                ),
                text_response("Voy a abrir el panel."),
            ],
        )
        llm.ask("abre un panel en /repo")
        gate = llm._approval_store.current(None)
        assert gate is not None and gate.action.cwd == "/repo"
        # Without cwd: the frozen spec defaults to "" (attach falls back
        # to no --dir on replay).
        llm2, _ = make_brain(
            settings, make_create_stub(make_stub),
            responses=[
                tool_call_response(
                    "c2", "create_session",
                    {"agent_kind": "opencode", "title": "Build"},
                ),
                text_response("Voy a abrir el panel."),
            ],
        )
        llm2.ask("abre un panel")
        gate2 = llm2._approval_store.current(None)
        assert gate2 is not None and gate2.action.cwd == ""

    def test_tool_message_tells_model_the_panel_is_pending(self, settings, make_stub):
        llm, _ = make_brain(
            settings, make_create_stub(make_stub),
            responses=[
                tool_call_response(
                    "c1", "create_session",
                    {"agent_kind": "claude", "title": "Docs"},
                ),
                text_response("Voy a crear el panel de docs."),
            ],
        )
        llm.ask("crea un panel de docs")
        tool_messages = [
            m for m in llm._client.create_kwargs[-1]["messages"] if m.get("role") == "tool"
        ]
        content = tool_messages[0]["content"]
        assert "pending user approval" in content
        assert "NOT" in content and "created" in content

    def test_missing_title_mirrors_tool_error_no_gate(self, settings, make_stub):
        llm, _ = make_brain(
            settings, make_create_stub(make_stub),
            responses=[
                tool_call_response("c1", "create_session", {"agent_kind": "opencode"}),
                text_response("Falta el título."),
            ],
        )
        result = llm.ask("crea un panel")
        assert result["approval"] is None
        tool_messages = [
            m for m in llm._client.create_kwargs[-1]["messages"] if m.get("role") == "tool"
        ]
        assert tool_messages[0]["content"] == "error: agent_kind and title are required"

    def test_no_store_blocks_fail_safe(self, settings, make_stub):
        stub = make_create_stub(make_stub)
        llm = BrainLLM(
            settings,
            BrainTools(settings, herdr=stub),
            client=ScriptedLLM(
                [
                    tool_call_response(
                        "c1", "create_session",
                        {"agent_kind": "opencode", "title": "T"},
                    ),
                    text_response("no pude crearlo."),
                ]
            ),
        )
        result = llm.ask("crea un panel")
        assert stub.tab_calls == []
        assert result["approval"] is None
        tool_messages = [
            m for m in llm._client.create_kwargs[-1]["messages"] if m.get("role") == "tool"
        ]
        assert tool_messages[0]["content"].startswith("error: create_session is blocked")


class TestServerCreateFlow:
    """Approve replay + PATCH through the HTTP surface (fakes wired)."""

    @pytest.fixture
    def create_app_fx(self, settings, tmp_path, monkeypatch):
        def _make(tool_result=None, llm_result=None):
            import herdr_brain.server as server_module
            from tests.test_server import FakeLLM, FakeTTS, make_replay_tools

            tools_cls = make_replay_tools(tool_result) if tool_result is not None else None
            cfg = settings.__class__(
                **{**settings.__dict__, "audio_dir": str(tmp_path / "audio")}
            )
            if tools_cls is not None:
                monkeypatch.setattr(server_module, "BrainTools", tools_cls)
            return server_module.create_app(
                settings=cfg,
                llm_factory=lambda c, t: llm_result or FakeLLM(),
                tts_renderer=FakeTTS(),
            ), tools_cls

        return _make

    def test_approve_dispatches_create_session_with_patched_task(
        self, settings, tmp_path, monkeypatch, create_app_fx
    ):
        from fastapi.testclient import TestClient
        from tests.test_server import FakeLLM

        tool_result = (
            "Panel creado: tab=w2:t7 pane=w2:p3 agente=refactor (opencode). "
            "Tarea entregada."
        )
        llm = FakeLLM(result={
            "answer": "Panel listo: refactor en marcha.",
            "pane_id": None, "agent": None, "session_id": "s1",
        })
        app, tools_cls = create_app_fx(tool_result=tool_result, llm_result=llm)
        gate = app.state.approval_store.propose(
            "s1", text="", tool=CREATE_SESSION,
            agent_kind="opencode", title="Refactor", task="tarea original",
        )
        # The user edits the task before approving.
        app.state.approval_store.patch(gate.gate_id, "tarea editada")
        resp = TestClient(app).post(f"/approval/{gate.gate_id}/approve")
        assert resp.status_code == 200
        assert resp.json()["answer"] == "Panel listo: refactor en marcha."
        tools = tools_cls.created[0]
        assert len(tools.dispatches) == 1
        sent = tools.dispatches[0]
        assert sent["name"] == CREATE_SESSION
        # Proposed without cwd: the frozen spec carries the "" default.
        assert sent["arguments"] == {
            "agent_kind": "opencode",
            "title": "Refactor",
            "task": "tarea editada",
            "cwd": "",
        }
        assert sent["target"] is None  # no pane re-resolution on create gates
        assert tools.resolved_panes == []
        # The report prompt names the panel spec, in the gate's session.
        assert "panel creation" in llm.calls[0]
        assert "opencode" in llm.calls[0] and "Refactor" in llm.calls[0]
        assert llm.session_ids == ["s1"]
        assert app.state.approval_store.get(gate.gate_id).state == APPROVED

    def test_approve_replays_frozen_cwd(
        self, settings, tmp_path, monkeypatch, create_app_fx
    ):
        from fastapi.testclient import TestClient
        from tests.test_server import FakeLLM

        llm = FakeLLM(result={
            "answer": "Panel listo.", "pane_id": None, "agent": None,
            "session_id": "s1",
        })
        app, tools_cls = create_app_fx(tool_result="Panel creado.", llm_result=llm)
        gate = app.state.approval_store.propose(
            "s1", text="", tool=CREATE_SESSION,
            agent_kind="opencode", title="Build", task="", cwd="/repo",
        )
        resp = TestClient(app).post(f"/approval/{gate.gate_id}/approve")
        assert resp.status_code == 200
        sent = tools_cls.created[0].dispatches[0]
        assert sent["name"] == CREATE_SESSION
        assert sent["arguments"]["cwd"] == "/repo"

    def test_approve_without_cwd_attaches_without_dir(
        self, settings, tmp_path, monkeypatch
    ):
        """Full replay chain with REAL BrainTools: a gate proposed without
        cwd replays with cwd="" so the opencode attach argv omits --dir
        and the tab is created unscoped."""
        import herdr_brain.server as server_module
        from fastapi.testclient import TestClient
        from tests.test_server import FakeLLM, FakeTTS
        from tests.test_tools_create_session import CreateStub

        stub = CreateStub(agents=[])
        monkeypatch.setattr(
            server_module, "BrainTools", lambda cfg: BrainTools(cfg, herdr=stub)
        )
        cfg = settings.__class__(
            **{**settings.__dict__, "audio_dir": str(tmp_path / "audio")}
        )
        app = server_module.create_app(
            settings=cfg,
            llm_factory=lambda c, t: FakeLLM(result={
                "answer": "Panel listo.", "pane_id": None, "agent": None,
                "session_id": "s1",
            }),
            tts_renderer=FakeTTS(),
        )
        gate = app.state.approval_store.propose(
            "s1", text="", tool=CREATE_SESSION,
            agent_kind="opencode", title="Build", task="",
        )
        resp = TestClient(app).post(f"/approval/{gate.gate_id}/approve")
        assert resp.status_code == 200
        assert stub.tab_calls == [{"label": "Build", "cwd": None}]
        attach_argv = stub.start_calls[0]["args"]
        assert attach_argv == ["attach", settings.opencode_attach_url]
        assert "--dir" not in attach_argv

    def test_patch_endpoint_returns_create_payload_with_new_task(
        self, settings, tmp_path, monkeypatch, create_app_fx
    ):
        from fastapi.testclient import TestClient

        app, _ = create_app_fx()
        gate = app.state.approval_store.propose(
            "s1", text="", tool=CREATE_SESSION,
            agent_kind="opencode", title="Refactor", task="original",
        )
        resp = TestClient(app).patch(
            f"/approval/{gate.gate_id}", json={"text": "tarea nueva"}
        )
        assert resp.status_code == 200
        approval = resp.json()["approval"]
        assert approval["tool"] == "create_session"
        assert approval["task"] == "tarea nueva"
        assert approval["title"] == "Refactor"
        assert "text" not in approval and "pane_id" not in approval

    def test_ask_reports_create_gate_payload(
        self, settings, tmp_path, monkeypatch, create_app_fx
    ):
        from fastapi.testclient import TestClient

        app, _ = create_app_fx()
        gate = app.state.approval_store.propose(
            "s1", text="", tool=CREATE_SESSION,
            agent_kind="opencode", title="Refactor", task="corre lint",
        )
        # /ask would supersede it — use /approval/current to read the payload.
        resp = TestClient(app).get("/approval/current", params={"session_id": "s1"})
        approval = resp.json()["approval"]
        assert approval["gate_id"] == gate.gate_id
        assert approval["agent"] == "opencode"
        assert approval["title"] == "Refactor"
        assert approval["task"] == "corre lint"

    def test_send_replay_still_resolves_target(
        self, settings, tmp_path, monkeypatch, create_app_fx
    ):
        from fastapi.testclient import TestClient

        app, tools_cls = create_app_fx(tool_result='{"ok": true}')
        gate = app.state.approval_store.propose(
            "s1", text="corre los tests", pane_id="w1:p9", agent="opencode",
        )
        assert TestClient(app).post(f"/approval/{gate.gate_id}/approve").status_code == 200
        tools = tools_cls.created[0]
        assert tools.resolved_panes == ["w1:p9"]
        assert tools.dispatches[0]["name"] == SEND_TO_SESSION
