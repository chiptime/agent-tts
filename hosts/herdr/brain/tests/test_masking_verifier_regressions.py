"""Adapted S14 verifier regressions (B5/B6); synthetic data only.

Faithful ports of the independent boundary probes: the assertion
boundary is the OUTBOUND provider request and the actual HTTP
serialization seams, not ``mask_text()`` in isolation. Auth-blocker
probes (B1-B4/B7) live with the security writer, not here.
"""

from __future__ import annotations

import copy
import json
import queue
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from herdr_brain import server
from herdr_brain.herdr import AgentInfo, HerdrError
from herdr_brain.llm import BrainLLM
from herdr_brain.secrets_mask import ENV_VAR

KEY = "sk-ant-" + "Z" * 24
PEM_BODY = "U1lOVEhFTElDLU9OTFktTk9ULUEtUkVBTC1LRVk="
PEM = "-----BEGIN PRIVATE KEY-----\n" + PEM_BODY + "\n-----END PRIVATE KEY-----"

AGENT = AgentInfo(
    pane_id="w1:p9", agent="claude", status="working", session_kind="id",
    session_value="00000000-1111-2222-3333-444444444444",
    cwd="/synthetic", title="synthetic", focused=True,
)


class ProbeHerdr:
    """HerdrClient double with the canned surfaces the probes mutate."""

    def __init__(self, agents):
        self._agents = list(agents)
        self._screen = "ordinary output"
        self._screen_ansi = "\x1b[48;2;60;60;60mAllow once\x1b[0m Allow always Reject"
        self.screen_calls: list = []

    def list_agents(self):
        return list(self._agents)

    def active_agent(self):
        from herdr_brain.herdr import pick_active

        return pick_active(self._agents)

    def read_screen(self, pane_id, n_lines=None, source="visible", as_ansi=False):
        self.screen_calls.append({"pane_id": pane_id, "as_ansi": as_ansi})
        if self._screen is None:
            raise HerdrError("read failed")
        return self._screen_ansi if as_ansi else self._screen


class ProbeHub:
    """Pre-loaded SSE hub double: replays queued announcements."""

    events = []

    def subscribe(self):
        q = queue.Queue()
        for value in self.events:
            q.put(value)
        return 1, q

    def unsubscribe(self, key):
        pass

    def publish(self, value):
        pass


@pytest.fixture
def rig(settings, tmp_path, monkeypatch):
    cfg = replace(settings, audio_dir=tmp_path / "audio", tts_bin=tmp_path / "missing")
    herdr = ProbeHerdr([AGENT])

    class Tools(server.BrainTools):
        def __init__(self, cfg):
            super().__init__(cfg, herdr=herdr)

    monkeypatch.setattr(server, "BrainTools", Tools)
    monkeypatch.setattr(server.DaemonWatcher, "start", lambda self: None)
    monkeypatch.setattr(server, "tts_backend_status", lambda cfg: ("missing", "synthetic"))

    def create(**kwargs):
        return server.create_app(
            cfg,
            watcher=NS(hub=ProbeHub()),
            transcriber=NS(state="unavailable"),
            daemon_probe=lambda: "down",
            sse_stream_limit=1,
            sse_heartbeat_s=0.001,
            version="probe",
            **kwargs,
        )

    app = create()
    return NS(
        cfg=cfg, herdr=herdr, tools=Tools(cfg), create=create, app=app,
        client=TestClient(app, base_url="http://testserver"),
    )


def synth(cfg, text, output):
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"ID3")


# -- B5: outbound provider requests carry no raw secret ----------------------


@pytest.mark.parametrize("tool", ["read_screen", "read_transcript"])
def test_tool_result_is_masked_at_provider(rig, monkeypatch, tool):
    """The cloud request is the assertion boundary: a raw read_screen or
    read_transcript result must never leave inside provider messages."""
    rig.herdr._screen = KEY
    monkeypatch.setattr("herdr_brain.tools.read_transcript", lambda *args: KEY)
    calls = []

    def create(**kwargs):
        calls.append(copy.deepcopy(kwargs["messages"]))
        message = NS(content="ordinary answer", tool_calls=None)
        if len(calls) == 1:
            message = NS(content="", tool_calls=[NS(id="probe", function=NS(
                name=tool, arguments="{}"))])
        return NS(choices=[NS(message=message)])

    provider = NS(chat=NS(completions=NS(create=create)))
    BrainLLM(rig.cfg, rig.tools, client=provider).ask("Read the selected pane")
    assert KEY not in json.dumps(calls[-1])


def test_user_and_history_text_masked_in_provider_requests(rig, monkeypatch):
    """The user question and ring history ride provider requests masked;
    the internal ring keeps its bytes."""
    from herdr_brain.memory import ConversationStore

    store = ConversationStore()
    store.append(None, "user", f"earlier {KEY}")
    calls = []

    def create(**kwargs):
        calls.append(copy.deepcopy(kwargs["messages"]))
        return NS(choices=[NS(message=NS(content="ok", tool_calls=None))])

    provider = NS(chat=NS(completions=NS(create=create)))
    BrainLLM(rig.cfg, rig.tools, client=provider, store=store).ask(KEY)
    assert KEY not in json.dumps(calls[-1])
    # Internal storage is untouched: the ring still holds the raw turn.
    assert any(KEY in (m.content or "") for m in store.history(None))


def test_provider_request_raw_when_masking_off(rig, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "off")
    calls = []

    def create(**kwargs):
        calls.append(copy.deepcopy(kwargs["messages"]))
        return NS(choices=[NS(message=NS(content="ok", tool_calls=None))])

    provider = NS(chat=NS(completions=NS(create=create)))
    BrainLLM(rig.cfg, rig.tools, client=provider).ask(f"use {KEY}")
    assert any(f"use {KEY}" in (m.get("content") or "") for m in calls[-1])


# -- B5: /ask answer, /call-history ------------------------------------------


@pytest.mark.parametrize("surface", ["provider", "ask", "history"])
def test_conversation_boundaries(rig, surface):
    """A provider echoing the secret back must not reach the /ask
    response, the provider requests, or the persisted call history
    serialization — while the store keeps the raw user question."""
    calls = []

    def create(**kwargs):
        calls.append(copy.deepcopy(kwargs["messages"]))
        return NS(choices=[NS(message=NS(content=KEY, tool_calls=None))])

    provider = NS(chat=NS(completions=NS(create=create)))
    app = rig.create(
        llm_factory=lambda cfg, tools: BrainLLM(cfg, tools, client=provider),
        tts_renderer=synth,
    )
    client = TestClient(app, base_url="http://testserver")
    response = client.post("/ask", json={"text": KEY})
    assert response.status_code == 200
    value = (
        json.dumps(calls)
        if surface == "provider"
        else (response.text if surface == "ask" else client.get("/call-history").text)
    )
    assert KEY not in value


def test_call_history_masks_but_store_stays_raw(rig, tmp_path):
    """The /call-history page masks; the sqlite rows keep every raw byte
    the turn arrived with."""
    def create(**kwargs):
        return NS(choices=[NS(message=NS(content="done", tool_calls=None))])

    provider = NS(chat=NS(completions=NS(create=create)))
    app = rig.create(
        llm_factory=lambda cfg, tools: BrainLLM(cfg, tools, client=provider),
        tts_renderer=synth,
    )
    client = TestClient(app, base_url="http://testserver")
    assert client.post("/ask", json={"text": f"guarda {KEY}"}).status_code == 200
    body = client.get("/call-history").json()
    assert KEY not in json.dumps(body)
    assert any("guarda" in turn["text"] for turn in body["turns"])
    # Raw bytes on disk (internal data preserved).
    import sqlite3

    from herdr_brain.history import default_history_path

    db = default_history_path(rig.cfg)
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT text FROM turns").fetchall()
    finally:
        conn.close()
    assert any(KEY in row[0] for row in rows)


# -- B5: herd/view metadata ---------------------------------------------------


def test_http_secret_boundaries_metadata(rig):
    """A recognized secret inside title/cwd is masked on /herd while the
    lookup ids (pane_id, session_id) keep their exact values."""
    rig.herdr._agents = [replace(AGENT, title=KEY, cwd="/" + KEY)]
    response = rig.client.get("/herd")
    assert response.status_code == 200
    assert KEY not in response.text
    entry = response.json()[0]
    assert entry["pane_id"] == AGENT.pane_id
    assert entry["session_id"] == AGENT.session_value


def test_state_metadata_masked(rig):
    rig.herdr._agents = [replace(AGENT, title=KEY, cwd="/" + KEY)]
    response = rig.client.get("/state")
    assert response.status_code == 200
    assert KEY not in response.text


# -- B6: detection gaps --------------------------------------------------------


def test_http_secret_boundary_pem_across_ansi_runs(rig):
    """A PEM body wrapped in ANSI colour mid-body is masked as ONE block
    across line and run boundaries."""
    rig.herdr._screen_ansi = PEM.replace("U1lO", "\x1b[31mU1lO\x1b[0m")
    response = rig.client.get("/pane/w1:p9/mirror")
    assert response.status_code == 200
    assert PEM_BODY not in response.text
    assert "[masked:private-key]" in response.text


def test_http_secret_boundary_clipped_pending(rig):
    """The pending excerpt masks the WHOLE screen before clipping, so a
    key beyond the 200-char cut never survives as a partial prefix."""
    rig.herdr._screen = "allow? " + "x" * 171 + " " + KEY
    response = rig.client.get("/view")
    assert response.status_code == 200
    assert "sk-ant-" not in response.json()["pending"]["excerpt"]


def test_http_secret_boundary_numeric_assignment(rig):
    """A numeric password assignment is masked; unrelated numbers,
    token counters and versions keep their exact bytes."""
    rig.herdr._screen = "password=1234567890"
    response = rig.client.get("/screen")
    assert response.status_code == 200
    assert "1234567890" not in response.text
    # Unrelated numeric assignments stay byte-identical.
    rig.herdr._screen = "max_tokens=8192 next_token=1699999999 code=1234567890"
    response = rig.client.get("/screen")
    assert "max_tokens=8192" in response.text
    assert "next_token=1699999999" in response.text
    assert "code=1234567890" in response.text


# -- B5: SSE announcement label -------------------------------------------------


def test_sse_label_boundary(rig, monkeypatch):
    """A secret in a real agent label (cwd-derived) reaches the SSE
    serializer masked."""
    from herdr_brain.watcher import AgentWatcher

    monkeypatch.setattr("herdr_brain.watcher.read_turns", lambda *args: [])
    agent = replace(AGENT, status="done", cwd="/" + KEY)
    watcher = AgentWatcher(rig.cfg, herdr=rig.herdr, tts_renderer=lambda *args: None)
    rig.app.state.watcher.hub.events = [watcher._build_announcement(agent)]
    assert KEY not in rig.client.get("/events").text


def test_off_mode_restores_raw_outputs(rig, monkeypatch):
    """One off-mode pin per new seam: masking off restores the exact
    previous (pre-masking) output."""
    monkeypatch.setenv(ENV_VAR, "off")
    rig.herdr._agents = [replace(AGENT, title=KEY, cwd="/" + KEY)]
    herd = rig.client.get("/herd").json()
    assert herd[0]["title"] == KEY
    rig.herdr._screen = "password=1234567890"
    assert "password=1234567890" in rig.client.get("/screen").text
