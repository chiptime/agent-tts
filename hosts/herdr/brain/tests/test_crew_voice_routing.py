"""Crew voice routing (parity T1, S1): a voice request aimed at a crew
pane either acts on the correct remote pane or is refused EXPLICITLY —
never by silently steering writes to the focused local pane, and never
by freezing a gate against a pane the user did not name.

These tests run in FULL isolation: a StubHerdr double, a stub crew
router, a scripted-or-refusing LLM client. No crew process, no peer,
no network, no real panes. The REST crew seam (server.py
``crew_remote_peer``) already refuses host-shaped non-local ids instead
of falling back; the voice loop must obey the same rule through the
tools seam, solo builds keep their exact legacy fallback, and a refused
turn must not open a gate, run the provider loop, or write to ANY pane.
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace

import pytest

from herdr_brain.approval import ApprovalGateStore
from herdr_brain.config import Settings
from herdr_brain.herdr import AgentInfo, HerdrError
from herdr_brain.llm import BrainLLM
from herdr_brain.memory import ConversationStore
from herdr_brain.tools import BrainTools, CrewTargetRefused

from tests.conftest import ACTIVE_AGENT


def router_for(name: str):
    """Stub crew router: ``<name>:*`` routes to that peer, nothing else."""

    def route(pane_id):
        return name if pane_id.startswith(name + ":") else None

    return route


def crew_tools(settings, stub, router_peer: str = "peer-alpha"):
    tools = BrainTools(settings, herdr=stub)
    tools.attach_crew_router(router_for(router_peer))
    return tools


class RefusingProviderClient:
    """Client double that records every provider round and refuses to
    serve one: a rejected crew turn must never run the loop at all."""

    def __init__(self):
        self.rounds = 0
        outer = self
        completions = SimpleNamespace()

        def create(**kwargs):
            outer.rounds += 1
            raise AssertionError("the provider loop must not run for a refused crew turn")

        completions.create = create
        self.chat = SimpleNamespace(completions=completions)


def append_tools_key(store: ConversationStore):
    key = ConversationStore.normalize(None)
    return key


# ── resolve_target: the seam every voice tool and gate freeze shares ────────


def test_resolve_target_refuses_routed_remote_pane_without_local_fallback(
    settings, make_stub
):
    stub = make_stub(agents=[ACTIVE_AGENT])  # the only local pane is w1:p9
    tools = crew_tools(settings, stub)
    with pytest.raises(CrewTargetRefused) as excinfo:
        tools.resolve_target("peer-alpha:p3")
    assert "peer-alpha:p3" in str(excinfo.value)
    assert "nada se envió" in str(excinfo.value)
    # Nothing was written anywhere while refusing.
    assert stub.prompt_calls == []


def test_resolve_target_refuses_unknown_crew_host_prefix(settings, make_stub):
    stub = make_stub(agents=[ACTIVE_AGENT])
    tools = crew_tools(settings, stub)
    with pytest.raises(CrewTargetRefused) as excinfo:
        tools.resolve_target("ghost-host:p3")
    assert "ghost-host" in str(excinfo.value)
    assert stub.prompt_calls == []


def test_resolve_target_keeps_local_host_shaped_pane(settings, make_stub):
    """A pane whose id merely LOOKS host-shaped is still a real local
    pane: the crew rule must never un-address it."""
    stub = make_stub(agents=[ACTIVE_AGENT])
    tools = crew_tools(settings, stub)
    target = tools.resolve_target("w1:p9")
    assert target is not None
    assert target.pane_id == "w1:p9"


def test_resolve_target_solo_without_router_keeps_legacy_fallback(
    settings, make_stub
):
    """Solo builds have no crew at all: the legacy fallback for unknown
    ids is preserved exactly as today (S6)."""
    stub = make_stub(agents=[ACTIVE_AGENT])
    tools = BrainTools(settings, herdr=stub)
    target = tools.resolve_target("peer-alpha:p3")
    assert target is not None
    assert target.pane_id == ACTIVE_AGENT.pane_id


def test_resolve_target_plain_unknown_id_keeps_legacy_fallback(settings, make_stub):
    """Only crew-SHAPED ids fail closed: a plain unknown id keeps the
    legacy 'stale selection never bricks the brain' fallback."""
    stub = make_stub(agents=[ACTIVE_AGENT])
    tools = crew_tools(settings, stub)
    target = tools.resolve_target("plainly-unknown")
    assert target is not None
    assert target.pane_id == ACTIVE_AGENT.pane_id


class _BrokenInventory:
    """Stub whose enumeration fails exactly like a dead mux does."""

    def __init__(self, inner):
        self._inner = inner

    def list_agents(self):
        raise HerdrError("agent list unavailable")

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_resolve_target_refuses_routed_remote_even_when_enumeration_fails(
    settings, make_stub
):
    """S1 blocker (verifier): a HerdrError in list_agents must never let
    an explicit crew target degrade into None -> the focused local pane.
    The refusal runs BEFORE enumeration; nothing is read or written."""
    stub = make_stub(agents=[ACTIVE_AGENT])
    tools = crew_tools(settings, stub)
    tools._herdr = _BrokenInventory(stub)
    with pytest.raises(CrewTargetRefused) as excinfo:
        tools.resolve_target("peer-alpha:p3")
    assert "peer-alpha:p3" in str(excinfo.value)
    assert stub.prompt_calls == []


def test_ask_refuses_remote_pane_when_enumeration_fails(settings, make_stub):
    """The ask turn keeps the explicit refusal story without enumeration:
    spoken refusal, no provider round, no gate, no ring write."""
    stub = make_stub(agents=[ACTIVE_AGENT])
    tools = crew_tools(settings, stub)
    tools._herdr = _BrokenInventory(stub)
    client = RefusingProviderClient()
    llm = BrainLLM(settings, tools, client=client, store=ConversationStore())
    result = llm.ask("estado del agente", pane_id="peer-alpha:p3")
    assert "peer-alpha:p3" in result["answer"]
    assert "nada se envió" in result["answer"]
    assert result["pane_id"] is None and result["approval"] is None
    assert client.rounds == 0
    assert stub.prompt_calls == []


def test_enumeration_failure_keeps_local_paths_unchanged(settings, make_stub):
    """Preservation pin: with enumeration failing, BARE/None targets keep
    the exact legacy degraded behavior (None, no refusal) — solo and crew
    alike. Only crew-shaped explicit targets fail closed."""
    stub = make_stub(agents=[ACTIVE_AGENT])
    broken = _BrokenInventory(stub)
    solo = BrainTools(settings, herdr=broken)
    crewed = crew_tools(settings, stub)
    crewed._herdr = broken
    assert solo.resolve_target("w1:p9") is None
    assert solo.resolve_target(None) is None
    assert crewed.resolve_target("w1:p9") is None
    assert crewed.resolve_target(None) is None
    assert stub.prompt_calls == []


# ── the ask turn: explicit refusal, no gate, no write, no provider round ────


def test_ask_refuses_remote_pane_and_never_runs_the_loop(settings, make_stub):
    stub = make_stub(agents=[ACTIVE_AGENT])
    tools = crew_tools(settings, stub)
    store = ConversationStore()
    llm = BrainLLM(settings, tools, client=RefusingProviderClient(), store=store)
    result = llm.ask("muéstrame el estado del agente", pane_id="peer-alpha:p3")
    # The refusal is EXPLICIT in the answer the voice user hears.
    assert "peer-alpha:p3" in result["answer"]
    assert "nada se envió" in result["answer"]
    # No pane was targeted, no write happened, the loop never ran, and
    # the rejected turn never lands in the ring.
    assert result["pane_id"] is None
    assert result["approval"] is None
    assert stub.prompt_calls == []
    assert store.history(append_tools_key(store)) == []


def test_ask_remote_pane_freezes_no_gate_against_the_focused_local_pane(
    settings, make_stub
):
    """The dangerous silent outcome today: the gate would be frozen with
    the FOCUSED local pane as target. The refusal must make that
    impossible."""
    stub = make_stub(agents=[ACTIVE_AGENT])
    tools = crew_tools(settings, stub)
    approval_store = ApprovalGateStore(timeout_s=settings.approval_timeout_s)
    llm = BrainLLM(
        settings, tools, client=RefusingProviderClient(), store=ConversationStore()
    )
    llm.attach_approval_store(approval_store)
    result = llm.ask("envía el resumen", pane_id="peer-alpha:p3")
    assert result["approval"] is None
    key = ConversationStore.normalize(None)
    assert approval_store.current(key) is None
    assert stub.prompt_calls == []


def test_ask_local_pane_still_resolves(settings, make_stub):
    """A plain local ask keeps resolving its named pane (regression pin
    alongside the new rule)."""
    stub = make_stub(agents=[ACTIVE_AGENT])
    tools = crew_tools(settings, stub)
    store = ConversationStore()
    llm = BrainLLM(
        settings, tools,
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: SimpleNamespace(choices=[])))),
        store=store,
    )
    target = llm._tools.resolve_target("w1:p9")
    assert target is not None and target.pane_id == "w1:p9"


# ── the real app: the router binds in crew mode and never in solo ───────────


class _AskStubLLM:
    """A llm double that exercises the REAL tools plumbing per turn: it
    resolves the ask pane through BrainTools like the production loop
    and relays a crew refusal as the answer. The RULE is tested above
    against the real BrainLLM; this double only observes the wiring."""

    def __init__(self, cfg, tools):
        self.tools = tools

    def attach_store(self, store):
        pass

    def attach_approval_store(self, store):
        pass

    def ask(self, question, session_id=None, pane_id=None, **kwargs):
        try:
            target = self.tools.resolve_target(pane_id)
        except CrewTargetRefused as exc:
            return {
                "answer": str(exc), "pane_id": None, "agent": None,
                "session_id": session_id, "approval": None,
            }
        return {
            "answer": "listo", "pane_id": target.pane_id if target else None,
            "agent": target.agent if target else None,
            "session_id": session_id, "approval": None,
        }


def _client(tmp_path, monkeypatch, *, crew, agents=(ACTIVE_AGENT,)):
    import herdr_brain.server as server_module
    from herdr_brain.server import create_app
    from fastapi.testclient import TestClient
    from tests.conftest import SETTINGS_KWARGS, StubHerdr

    captured = {}
    monkeypatch.setattr(
        server_module, "BrainTools",
        lambda cfg: captured.setdefault(
            "tools", BrainTools(cfg, herdr=StubHerdr(agents=list(agents)))
        ),
    )
    settings = Settings(**{
        **SETTINGS_KWARGS,
        "state_dir": str(tmp_path / "state"),
        "audio_dir": str(tmp_path / "audio"),
    })
    llm = _AskStubLLM(settings, captured.get("tools"))
    app = create_app(
        settings=settings, crew=crew, llm_factory=lambda cfg, tools: llm
    )
    # The factory closure bound BEFORE the app built its tools; rebind to
    # the SAME instance create_app attached the crew router to.
    llm.tools = captured["tools"]
    return TestClient(app), captured["tools"]


def test_app_binds_the_crew_front_router_for_voice(tmp_path, monkeypatch):
    from herdr_brain.crew_runtime import CrewGateway
    from herdr_brain.crew.roster import CrewRoster
    from herdr_brain.crew.client import CrewHttpResponse

    class Wire:
        def request(self, method, url, *, headers, json_body=None, timeout_s=None):
            raise OSError("peer must never be reached by the voice refusal")

    FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "crew"
    pem = (FIXTURES / "peer.pem").read_text(encoding="utf-8")
    state = tmp_path / "state"
    roster = CrewRoster.load(state, now=lambda: 1_000.0)
    roster.init_lead("host-a", "crew-secret-1", now=1.0)
    roster.record_peer("host-b", "https://host-b.example:8747", pem, now=1.0)
    roster.persist()
    (state / "crew-secret").write_text("crew-secret-1\n")
    gateway = CrewGateway(
        state, secret_provider=lambda: "crew-secret-1",
        transport_factory=lambda peer, url, cert: Wire(), now=lambda: 1_000.0,
    )
    client, tools = _client(tmp_path, monkeypatch, crew=gateway)
    assert tools._crew_router is not None
    assert tools._crew_router("host-b:p3") == "host-b"

    response = client.post("/ask", json={"text": "hola host-b", "pane_id": "host-b:p3"})
    assert response.status_code == 200  # a refusal, not an error page
    body = response.json()
    assert "host-b:p3" in body["answer"]
    assert "nada se envió" in body["answer"]
    assert tools.last_active is None  # no pane was ever tracked/steered

    # A local ask through the same app still resolves the real pane.
    ok = client.post("/ask", json={"text": "estado", "pane_id": "w1:p9"})
    assert ok.json()["answer"] == "listo"


def test_app_solo_binds_no_router_and_keeps_the_legacy_fallback(
    tmp_path, monkeypatch
):
    import herdr_brain.server as server_module
    from herdr_brain.server import create_app
    from fastapi.testclient import TestClient
    from tests.conftest import SETTINGS_KWARGS, StubHerdr

    captured = {}
    monkeypatch.setattr(
        server_module, "BrainTools",
        lambda cfg: captured.setdefault(
            "tools", BrainTools(cfg, herdr=StubHerdr(agents=[ACTIVE_AGENT]))
        ),
    )
    settings = Settings(**{
        **SETTINGS_KWARGS,
        "state_dir": str(tmp_path / "state"),
        "audio_dir": str(tmp_path / "audio"),
    })
    app = create_app(settings=settings, crew=None,
                     llm_factory=lambda cfg, tools: _AskStubLLM(cfg, tools))
    tools = captured["tools"]
    assert app.state.crew is None
    assert tools._crew_router is None
    # Solo legacy: host-shaped unknown ids fall back to the focused pane.
    try:
        fallback = tools.resolve_target("peer-alpha:p3")
    except CrewTargetRefused:  # pragma: no cover - the rule must not fire solo
        fallback = None
    assert fallback is not None and fallback.pane_id == "w1:p9"
