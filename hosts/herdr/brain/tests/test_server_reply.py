"""POST /pane/reply: HTTP contract of the user-typed direct reply.

S12 extends the contract additively: every reply is VERIFIED (screen
read before and after) and the response carries a ``receipt`` with the
closed outcome set. The legacy mapping is pinned here — ``delivered``
stays ``200 {ok: true, pane_id}`` (plus the receipt), ``blocked`` 409,
``unknown_pane`` 404, ``error`` 502 — and the new surfaces (steer,
typed drafts, stop) get their own rows.

f7f S2 adds the multiplexer parity rows at the bottom: a reply
addressed to a ``zellij:``/``tmux:`` prefixed pane rides the REAL
route with the REAL tools layer over a CompositeMux whose alternate
children are fake-runner adapters — same receipt shapes, same audit
``pane.reply`` row, same body guards as a herdr reply.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import herdr_brain.server as server_module
from herdr_brain.config import Settings
from herdr_brain.herdr import AgentInfo
from herdr_brain.mux import CompositeMux, TmuxAdapter, ZellijAdapter
from herdr_brain.tools import BrainTools


def _receipt(outcome, detail="", text_hint=""):
    return {"outcome": outcome, "detail": detail, "text_hint": text_hint}


class FakeTools:
    outcome = {
        "outcome": "delivered", "pane_id": "w1:p2", "status": "done",
        "detail": "", "receipt": _receipt("effective"),
    }
    replies: list = []
    stop_results: list = []
    follow_results: list = []

    def __init__(self, cfg):
        self.last_active = None

    def verified_reply_to_pane(self, pane_id, text, steer=False):
        type(self).replies.append((pane_id, text, steer))
        return dict(type(self).outcome, pane_id=pane_id)

    def stop_pane(self, pane_id):
        type(self).stop_results.append(pane_id)
        result = type(self).stop_outcome
        return dict(result, pane_id=pane_id)

    stop_outcome = {"outcome": "stopped", "keys": ["esc"], "detail": ""}

    def submit_typed_text(self, pane_id, text_hint):
        type(self).follow_results.append(("submit", pane_id, text_hint))
        return dict(type(self).follow_outcome)

    def discard_typed_text(self, pane_id, agent_kind, text_hint):
        type(self).follow_results.append(("discard", pane_id, text_hint))
        return dict(type(self).follow_outcome)

    follow_outcome = {"outcome": "sent", "detail": "enviado con Intro"}


@pytest.fixture
def client(settings, tmp_path, monkeypatch):
    FakeTools.replies = []
    FakeTools.stop_results = []
    FakeTools.follow_results = []
    FakeTools.outcome = {
        "outcome": "delivered", "pane_id": "w1:p2", "status": "done",
        "detail": "", "receipt": _receipt("effective"),
    }
    FakeTools.stop_outcome = {"outcome": "stopped", "keys": ["esc"], "detail": ""}
    FakeTools.follow_outcome = {"outcome": "sent", "detail": "enviado con Intro"}
    monkeypatch.setattr(server_module, "BrainTools", FakeTools)
    cfg = Settings(**{**settings.__dict__, "audio_dir": str(tmp_path / "audio")})
    return TestClient(server_module.create_app(settings=cfg))


def test_delivered_reply_returns_ok_and_forwards_the_exact_text(client):
    resp = client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "yes, go ahead"})

    assert resp.status_code == 200
    body = resp.json()
    # Legacy fields, unchanged: the additive S12 change is the receipt.
    assert body["ok"] is True
    assert body["pane_id"] == "w1:p2"
    receipt = body["receipt"]
    assert receipt["id"].startswith("rct-")
    assert receipt["outcome"] == "effective"
    assert receipt["pane_id"] == "w1:p2"
    assert FakeTools.replies == [("w1:p2", "yes, go ahead", False)]


@pytest.mark.parametrize(
    "outcome,status",
    [("unknown_pane", 404), ("blocked", 409), ("error", 502)],
)
def test_failure_outcomes_map_to_http_errors(client, outcome, status):
    FakeTools.outcome = {
        "outcome": outcome, "pane_id": "w1:p2", "status": "", "detail": "boom",
        "receipt": _receipt("refused" if outcome == "blocked" else "unknown"),
    }

    resp = client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "yes"})

    assert resp.status_code == status
    assert resp.json()["detail"]


def test_unknown_pane_reuses_the_store_detail(client):
    FakeTools.outcome = {
        "outcome": "unknown_pane", "pane_id": "w1:p2", "status": "", "detail": "pane not found",
        "receipt": None,
    }

    resp = client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "yes"})

    assert resp.json()["detail"] == "pane not found"


def test_blocked_pane_points_at_the_dialog_not_at_the_agent(client):
    """herdr refused before writing input, so the message must not imply
    the agent rejected the text — the dialog is answered with keys."""
    FakeTools.outcome = {
        "outcome": "blocked",
        "pane_id": "w1:p2",
        "status": "blocked",
        "detail": "the agent rejected the reply",
        "receipt": _receipt("refused", "el agente rechazó el texto"),
    }

    resp = client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "yes"})

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "diálogo" in detail
    assert "teclas" in detail
    assert "rejected" not in detail


@pytest.mark.parametrize(
    "body",
    [
        {"text": "yes"},
        {"pane_id": "w1:p2"},
        {"pane_id": "", "text": "yes"},
        {"pane_id": "w1:p2", "text": ""},
        {"pane_id": "w1:p2", "text": "x" * 8_001},
    ],
)
def test_invalid_bodies_are_422_and_nothing_is_sent(client, body):
    resp = client.post("/pane/reply", json=body)

    assert resp.status_code == 422
    assert FakeTools.replies == []


def test_whitespace_only_text_is_rejected_before_sending(client):
    resp = client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "   \n "})

    assert resp.status_code == 422
    assert FakeTools.replies == []


# ── S12: the receipt protocol ─────────────────────────────────────────────────


def test_typed_not_submitted_is_a_200_that_tells_the_truth(client):
    """The blind send is over: a draft the terminal never took is NOT
    ok — the phone keeps the receipt card with Submit/Discard."""
    FakeTools.outcome = {
        "outcome": "delivered", "pane_id": "w1:p2", "status": "timeout",
        "detail": "",
        "receipt": _receipt(
            "typed_not_submitted",
            "el texto quedó escrito en la línea de entrada sin enviarse",
            text_hint="sigue adelante",
        ),
    }

    resp = client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "sigue adelante"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["receipt"]["outcome"] == "typed_not_submitted"
    assert body["receipt"]["text_hint"] == "sigue adelante"


def test_receipts_listing_is_newest_first_and_filters_by_pane(client):
    client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "one"})
    client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "two"})

    resp = client.get("/pane/receipts", params={"pane_id": "w1:p2"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["pane_id"] == "w1:p2"
    assert [r["outcome"] for r in body["receipts"]] == ["effective", "effective"]
    assert len(body["receipts"]) == 2
    other = client.get("/pane/receipts", params={"pane_id": "w9:p9"}).json()
    assert other["receipts"] == []


def test_submit_finishes_a_typed_receipt_with_enter_only(client):
    client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "sigue adelante"})
    FakeTools.outcome = {
        "outcome": "delivered", "pane_id": "w1:p2", "status": "timeout",
        "detail": "", "receipt": _receipt("typed_not_submitted", text_hint="sigue"),
    }
    client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "sigue"})
    receipt_id = client.get("/pane/receipts").json()["receipts"][0]["id"]

    FakeTools.follow_outcome = {"outcome": "sent", "detail": "enviado con Intro"}
    resp = client.post(f"/pane/receipt/{receipt_id}/submit")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["receipt"]["outcome"] == "sent"
    # The follow-up carries NO text: only the receipt's masked tail.
    assert FakeTools.follow_results[-1] == ("submit", "w1:p2", "sigue")
    # The store keeps the resolved state.
    stored = client.get("/pane/receipts").json()["receipts"][0]
    assert stored["outcome"] == "sent"


def test_discard_clears_a_typed_receipt(client):
    FakeTools.outcome = {
        "outcome": "delivered", "pane_id": "w1:p2", "status": "timeout",
        "detail": "", "receipt": _receipt("typed_not_submitted", text_hint="sigue"),
    }
    client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "sigue"})
    receipt_id = client.get("/pane/receipts").json()["receipts"][0]["id"]

    FakeTools.follow_outcome = {"outcome": "discarded", "detail": "entrada descartada"}
    resp = client.post(f"/pane/receipt/{receipt_id}/discard")

    assert resp.status_code == 200
    assert resp.json()["receipt"]["outcome"] == "discarded"
    assert FakeTools.follow_results[-1][0] == "discard"


def test_submit_and_discard_refuse_anything_not_typed(client):
    FakeTools.outcome = {
        "outcome": "delivered", "pane_id": "w1:p2", "status": "done",
        "detail": "", "receipt": _receipt("effective"),
    }
    client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "done"})
    receipt_id = client.get("/pane/receipts").json()["receipts"][0]["id"]

    for action in ("submit", "discard"):
        resp = client.post(f"/pane/receipt/{receipt_id}/{action}")
        assert resp.status_code == 409
        assert resp.json()["detail"]

    assert client.post("/pane/receipt/rct-missing/submit").status_code == 404
    assert client.post("/pane/receipt/rct-missing/discard").status_code == 404
    assert FakeTools.follow_results == []


def test_followup_key_failure_is_502_and_leaves_the_receipt_alone(client):
    FakeTools.outcome = {
        "outcome": "delivered", "pane_id": "w1:p2", "status": "timeout",
        "detail": "", "receipt": _receipt("typed_not_submitted", text_hint="x"),
    }
    client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "x"})
    receipt_id = client.get("/pane/receipts").json()["receipts"][0]["id"]

    FakeTools.follow_outcome = {"outcome": "error", "detail": "mux refused"}
    resp = client.post(f"/pane/receipt/{receipt_id}/submit")

    assert resp.status_code == 502
    stored = client.get("/pane/receipts").json()["receipts"][0]
    assert stored["outcome"] == "typed_not_submitted"


def test_surviving_echo_keeps_the_receipt_re_offerable(client):
    FakeTools.outcome = {
        "outcome": "delivered", "pane_id": "w1:p2", "status": "timeout",
        "detail": "", "receipt": _receipt("typed_not_submitted", text_hint="x"),
    }
    client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "x"})
    receipt_id = client.get("/pane/receipts").json()["receipts"][0]["id"]

    FakeTools.follow_outcome = {"outcome": "typed_not_submitted", "detail": "sigue"}
    resp = client.post(f"/pane/receipt/{receipt_id}/submit")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["receipt"]["outcome"] == "typed_not_submitted"


# ── S12: steer ────────────────────────────────────────────────────────────────


def test_steer_reaches_the_tools_layer_as_a_flag(client):
    resp = client.post(
        "/pane/reply", json={"pane_id": "w1:p2", "text": "keep going", "steer": True}
    )

    assert resp.status_code == 200
    assert FakeTools.replies == [("w1:p2", "keep going", True)]


def test_steer_refusal_is_a_422_that_names_the_alternative(client):
    FakeTools.outcome = {
        "outcome": "unsupported_steer", "pane_id": "w1:p2", "status": "",
        "detail": "a running turn of a shell agent cannot be steered",
        "receipt": None,
    }

    resp = client.post(
        "/pane/reply", json={"pane_id": "w1:p2", "text": "keep going", "steer": True}
    )

    assert resp.status_code == 422
    assert "inyectar" in resp.json()["detail"] or "turno" in resp.json()["detail"]


# ── S12: stop ─────────────────────────────────────────────────────────────────


def test_stop_interrupts_with_the_recipe_and_audits(client):
    resp = client.post("/pane/stop", json={"pane_id": "w1:p2"})

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": True, "pane_id": "w1:p2", "keys": ["esc"]}
    assert FakeTools.stop_results == ["w1:p2"]


@pytest.mark.parametrize(
    "outcome,status",
    [("unknown_pane", 404), ("unsupported_harness", 422), ("error", 502)],
)
def test_stop_failures_map_to_http_errors(client, outcome, status):
    FakeTools.stop_outcome = {
        "outcome": outcome, "keys": [], "detail": "because",
    }

    resp = client.post("/pane/stop", json={"pane_id": "w1:p2"})

    assert resp.status_code == status
    assert resp.json()["detail"]


def test_stop_requires_the_exact_pane_id(client):
    assert client.post("/pane/stop", json={}).status_code == 422
    assert client.post("/pane/stop", json={"pane_id": ""}).status_code == 422
    assert FakeTools.stop_results == []


# ── audit ─────────────────────────────────────────────────────────────────────


def test_every_write_action_lands_in_the_audit_log(client, tmp_path):
    # The settings fixture points state_dir at tmp_path/state.
    audit_file = tmp_path / "state" / "audit.log"
    client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "one"})
    FakeTools.outcome = {
        "outcome": "delivered", "pane_id": "w1:p2", "status": "timeout",
        "detail": "", "receipt": _receipt("typed_not_submitted", text_hint="two"),
    }
    client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "two"})
    receipt_id = client.get("/pane/receipts").json()["receipts"][0]["id"]
    client.post(f"/pane/receipt/{receipt_id}/discard")
    client.post("/pane/stop", json={"pane_id": "w1:p2"})

    assert audit_file.exists()
    actions = [
        json.loads(line)["action"] for line in audit_file.read_text().splitlines()
    ]
    assert "pane.reply" in actions
    assert "pane.receipt.discard" in actions
    assert "pane.stop" in actions


# ── f7f S2: multiplexer pane replies ride the same route ──────────────────────
#
# These rows run the REAL route and the REAL BrainTools.verified reply
# over a CompositeMux: the herdr child is the scripted double the S12
# suites use, the alternate children are the REAL adapters behind
# RecordedRunner fixtures (no real tmux/zellij binary is ever invoked;
# the zellij write-path fixtures are CLI-shape derived and UNVERIFIED
# live — zellij is not installed on this host, f7f D2).


FIXTURES = Path(__file__).parent / "fixtures" / "mux"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


class RecordedRunner:
    """Replays recorded CLI output in call order, pinning each argv."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls: list = []

    def __call__(self, cmd, **kwargs):
        self.calls.append({"cmd": list(cmd), **kwargs})
        if not self.steps:
            raise AssertionError(f"unexpected subprocess call: {cmd}")
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        prefix, rc, out, err = step
        argv = tuple(cmd[1:])
        if argv[: len(prefix)] != tuple(prefix):
            raise AssertionError(
                f"expected argv starting with {list(prefix)}, got {cmd}"
            )
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr=err)


class ScriptedHerdrChild:
    """Duck-typed herdr child: one pane, scripted screens, herdr-shaped
    prompt result — the same double shape test_send_receipts.py drives
    BrainTools with."""

    def __init__(self, agents, screens, prompt):
        self._agents = agents
        self._screens = list(screens)
        self._prompt = prompt

    def list_agents(self):
        return list(self._agents)

    def read_screen(self, pane_id, n_lines=None, source="visible", as_ansi=False):
        if len(self._screens) > 1:
            return self._screens.pop(0)
        return self._screens[0]

    def send_prompt(self, pane_id, text, timeout_ms=None):
        return self._prompt


class _QuietWatcher:
    """Injected watcher: no thread, no listing — status evidence only.
    ``hub`` is None because create_app probes it (a hub without publish
    simply leaves the event seams unset)."""

    hub = None

    def last_known_status(self, pane_id):
        return None


class _MuxTools(BrainTools):
    """The real tools layer bound to a composite mux (fake-runner
    children); the poll sleeps are stubbed so receipts stay fast."""

    mux = None

    def __init__(self, cfg):
        super().__init__(
            cfg, herdr=type(self).mux, clock=lambda: 0.0, sleeper=lambda _s: None
        )


def _herdr_child():
    return ScriptedHerdrChild(
        [AgentInfo("w1:p2", "claude", "idle", "opencode", "sess-1", "/repo",
                   "the pane", True)],
        screens=["❯ 1) Yes  2) No", "user: hi\nassistant: done"],
        prompt={"ok": True, "status": "done", "output": "done"},
    )


def _mux_client(monkeypatch, settings, tmp_path, alternate_name, alternate):
    mux_tools = type("MuxTools", (_MuxTools,), {"mux": CompositeMux({
        "herdr": _herdr_child(),
        alternate_name: alternate,
    })})
    monkeypatch.setattr(server_module, "BrainTools", mux_tools)
    cfg = Settings(**{**settings.__dict__, "audio_dir": str(tmp_path / "audio")})
    app = server_module.create_app(settings=cfg, watcher=_QuietWatcher())
    return TestClient(app)


@pytest.fixture
def zellij_client(settings, tmp_path, monkeypatch):
    """Composite herd (herdr + zellij) with the REAL zellij adapter
    behind a recorded runner. CLI order of one verified reply to pane
    ``zellij:1``: list → dump (tools' before read) → dump (adapter's
    before read) → write-chars → write 10 → dump (adapter's re-read,
    early stop: body moved) → dump (tools' poll read, early stop)."""
    runner = RecordedRunner(
        (("list-panes",), 0, _fixture("zellij/list-panes.json"), ""),
        (("action", "dump-screen"), 0, _fixture("zellij/dump-screen.txt"), ""),
        (("action", "dump-screen"), 0, _fixture("zellij/dump-screen.txt"), ""),
        (("action", "write-chars"), 0, "", ""),
        (("action", "write"), 0, "", ""),
        (("action", "dump-screen"), 0, _fixture("zellij/dump-prompt-after-sent.txt"), ""),
        (("action", "dump-screen"), 0, _fixture("zellij/dump-prompt-after-sent.txt"), ""),
    )
    client = _mux_client(
        monkeypatch, settings, tmp_path, "zellij",
        ZellijAdapter(bin_path="zellij-test", runner=runner),
    )
    return client, runner


@pytest.fixture
def tmux_client(settings, tmp_path, monkeypatch):
    """Composite herd (herdr + tmux) with the REAL tmux adapter behind
    a recorded runner — the same CLI order as the zellij fixture, with
    capture-pane reads and the private-buffer write pipeline (text
    rides stdin; argv only ever carries the uuid buffer name)."""
    runner = RecordedRunner(
        (("list-panes",), 0, _fixture("tmux/list-panes.txt"), ""),
        (("capture-pane",), 0, _fixture("tmux/capture-visible.txt"), ""),
        (("capture-pane",), 0, _fixture("tmux/capture-visible.txt"), ""),
        (("load-buffer",), 0, "", ""),
        (("paste-buffer",), 0, "", ""),
        (("delete-buffer",), 0, "", ""),
        (("capture-pane",), 0, _fixture("tmux/prompt-after-sent.txt"), ""),
        (("capture-pane",), 0, _fixture("tmux/prompt-after-sent.txt"), ""),
    )
    client = _mux_client(
        monkeypatch, settings, tmp_path, "tmux",
        TmuxAdapter(bin_path="tmux-test", runner=runner),
    )
    return client, runner


def _audit_rows(tmp_path):
    audit_file = tmp_path / "state" / "audit.log"
    assert audit_file.exists()
    return [json.loads(line) for line in audit_file.read_text().splitlines()]


class TestMultiplexerReplyParity:
    def test_zellij_pane_reply_returns_a_verified_receipt(self, zellij_client):
        client, runner = zellij_client

        resp = client.post(
            "/pane/reply", json={"pane_id": "zellij:1", "text": "run the probes"}
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["pane_id"] == "zellij:1"
        receipt = body["receipt"]
        # Same receipt shape a herdr reply serves: the store-owned keys
        # plus the closed-set outcome the trackers read.
        assert receipt["id"].startswith("rct-")
        assert receipt["pane_id"] == "zellij:1"
        assert receipt["outcome"] == "sent"
        assert receipt["text_hint"] == "run the probes"
        # The write really went through the zellij CLI surface.
        commands = [call["cmd"] for call in runner.calls]
        assert ["zellij-test", "action", "write-chars", "--pane-id", "1",
                "run the probes"] in commands
        assert ["zellij-test", "action", "write", "--pane-id", "1", "10"] in commands
        # And the receipts endpoint serves it like any herdr receipt.
        listed = client.get("/pane/receipts", params={"pane_id": "zellij:1"}).json()
        assert [r["outcome"] for r in listed["receipts"]] == ["sent"]

    def test_zellij_receipt_is_never_unsupported(self, zellij_client):
        # The S2 acceptance clause, verbatim: a zellij reply returns a
        # closed-set verified outcome, never an `unsupported` refusal.
        client, _runner = zellij_client

        resp = client.post(
            "/pane/reply", json={"pane_id": "zellij:1", "text": "run the probes"}
        )

        assert resp.status_code == 200  # not the 502 of a refused send
        outcome = resp.json()["receipt"]["outcome"]
        assert outcome in ("sent", "typed_not_submitted", "unknown")
        assert outcome != "unsupported"

    def test_zellij_typed_draft_keeps_the_receipt_card(self, settings, tmp_path,
                                                       monkeypatch):
        # The typed_not_submitted fixture: the echo survives every
        # re-read, so the receipt stays re-offerable exactly like a
        # herdr draft (200 + ok:false + Submit/Discard follow-ups).
        runner = RecordedRunner(
            (("list-panes",), 0, _fixture("zellij/list-panes.json"), ""),
            (("action", "dump-screen"), 0, _fixture("zellij/dump-screen.txt"), ""),
            (("action", "dump-screen"), 0, _fixture("zellij/dump-screen.txt"), ""),
            (("action", "write-chars"), 0, "", ""),
            (("action", "write"), 0, "", ""),
            (("action", "dump-screen"), 0, _fixture("zellij/dump-prompt-after-typed.txt"), ""),
            (("action", "dump-screen"), 0, _fixture("zellij/dump-prompt-after-typed.txt"), ""),
            (("action", "dump-screen"), 0, _fixture("zellij/dump-prompt-after-typed.txt"), ""),
            (("action", "dump-screen"), 0, _fixture("zellij/dump-prompt-after-typed.txt"), ""),
            (("action", "dump-screen"), 0, _fixture("zellij/dump-prompt-after-typed.txt"), ""),
        )
        client = _mux_client(
            monkeypatch, settings, tmp_path, "zellij",
            ZellijAdapter(bin_path="zellij-test", runner=runner),
        )

        resp = client.post(
            "/pane/reply", json={"pane_id": "zellij:1", "text": "run the probes"}
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert body["receipt"]["outcome"] == "typed_not_submitted"

    def test_tmux_pane_reply_returns_a_verified_receipt(self, tmux_client):
        client, runner = tmux_client

        resp = client.post(
            "/pane/reply", json={"pane_id": "tmux:%3", "text": "run the probes"}
        )

        assert resp.status_code == 200
        receipt = resp.json()["receipt"]
        assert receipt["outcome"] == "sent"
        assert receipt["pane_id"] == "tmux:%3"
        # The exact text rode the private-buffer pipeline: stdin in, pasted
        # by pane id, buffer deleted — its bytes appear in NO argv.
        commands = [call["cmd"] for call in runner.calls]
        assert not any("run the probes" in " ".join(c) for c in commands)
        load = next(c for c in runner.calls if c["cmd"][1] == "load-buffer")
        name = load["cmd"][3]
        assert name.startswith("herdr-paste-")
        assert load["input"] == "run the probes"
        paste = next(c for c in runner.calls if c["cmd"][1] == "paste-buffer")
        # Paste and its Enter submit ride ONE client invocation.
        assert paste["cmd"][:6] == [
            "tmux-test", "paste-buffer", "-d", "-S", "-b", name,
        ]
        assert paste["cmd"][6:] == ["-t", "%3", ";", "send-keys", "-t", "%3", "Enter"]
        assert "%3" in paste["cmd"]

    def test_tmux_pane_reply_write_refusal_propagates(self, settings, tmp_path,
                                                       monkeypatch):
        # A failed buffer transfer surfaces as the typed refusal the
        # route already maps — the write is never claimed as sent, and
        # the paste-and-Enter invocation is the only write attempted.
        runner = RecordedRunner(
            (("list-panes",), 0, _fixture("tmux/list-panes.txt"), ""),
            (("capture-pane",), 0, _fixture("tmux/capture-visible.txt"), ""),
            (("capture-pane",), 0, _fixture("tmux/capture-visible.txt"), ""),
            (("load-buffer",), 0, "", ""),
            (("paste-buffer",), 1, "", "can't find pane: %3"),
            (("delete-buffer",), 0, "", ""),
        )
        client = _mux_client(
            monkeypatch, settings, tmp_path, "tmux",
            TmuxAdapter(bin_path="tmux-test", runner=runner),
        )
        resp = client.post(
            "/pane/reply", json={"pane_id": "tmux:%3", "text": "run the probes"}
        )
        # The route maps the refusal to 502 and records the honest
        # receipt; nothing ever claims the write was verified.
        assert resp.status_code == 502
        assert "cannot send reply" in resp.json()["detail"]
        listed = client.get("/pane/receipts", params={"pane_id": "tmux:%3"}).json()
        assert [r["outcome"] for r in listed["receipts"]] == ["refused"]
        # The paste-and-Enter invocation was the only write attempted.
        assert [c["cmd"][1] for c in runner.calls] == [
            "list-panes", "capture-pane", "capture-pane",
            "load-buffer", "paste-buffer", "delete-buffer",
        ]

    @pytest.mark.parametrize("pane_id", ["w1:p2", "zellij:1", "tmux:%3"])
    def test_body_guards_apply_identically_to_multiplexer_panes(
        self, zellij_client, pane_id
    ):
        # Same guard behavior as a herdr reply: the route's own body
        # validation (required pair, 8000-char bound, no bare
        # whitespace) refuses BEFORE any pane is touched.
        client, runner = zellij_client

        oversized = client.post(
            "/pane/reply", json={"pane_id": pane_id, "text": "x" * 8_001}
        )
        blank = client.post(
            "/pane/reply", json={"pane_id": pane_id, "text": "  \n "}
        )

        assert oversized.status_code == 422
        assert blank.status_code == 422
        assert runner.calls == []

    def test_unknown_zellij_pane_is_404_never_redirected(self, settings, tmp_path,
                                                         monkeypatch):
        # An unknown zellij pane answers exactly like an unknown herdr
        # pane: 404 with the store detail — a reply is never rerouted
        # to another pane.
        runner = RecordedRunner(
            (("list-panes",), 0, _fixture("zellij/list-panes.json"), ""),
        )
        client = _mux_client(
            monkeypatch, settings, tmp_path, "zellij",
            ZellijAdapter(bin_path="zellij-test", runner=runner),
        )

        resp = client.post(
            "/pane/reply", json={"pane_id": "zellij:99", "text": "hi"}
        )

        assert resp.status_code == 404
        assert resp.json()["detail"] == "pane not found"

    def test_zellij_reply_writes_the_same_audit_row(self, settings, tmp_path,
                                                     monkeypatch):
        # One herdr reply and one zellij reply through the SAME app:
        # their pane.reply rows must have the identical shape (the
        # audit omits None attribution fields — device appears when a
        # paired device made the request, never for one pane kind only).
        runner = RecordedRunner(
            (("list-panes",), 0, _fixture("zellij/list-panes.json"), ""),  # herdr reply listing
            (("list-panes",), 0, _fixture("zellij/list-panes.json"), ""),  # zellij reply listing
            (("action", "dump-screen"), 0, _fixture("zellij/dump-screen.txt"), ""),
            (("action", "dump-screen"), 0, _fixture("zellij/dump-screen.txt"), ""),
            (("action", "write-chars"), 0, "", ""),
            (("action", "write"), 0, "", ""),
            (("action", "dump-screen"), 0, _fixture("zellij/dump-prompt-after-sent.txt"), ""),
            (("action", "dump-screen"), 0, _fixture("zellij/dump-prompt-after-sent.txt"), ""),
        )
        client = _mux_client(
            monkeypatch, settings, tmp_path, "zellij",
            ZellijAdapter(bin_path="zellij-test", runner=runner),
        )

        herdr = client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "hi"})
        zellij = client.post(
            "/pane/reply", json={"pane_id": "zellij:1", "text": "run the probes"}
        )

        assert herdr.status_code == 200
        assert zellij.status_code == 200
        rows = [row for row in _audit_rows(tmp_path) if row["action"] == "pane.reply"]
        assert len(rows) == 2
        herdr_row, zellij_row = rows
        assert herdr_row["pane_id"] == "w1:p2"
        assert zellij_row["pane_id"] == "zellij:1"
        # Identical shape: same keys, same detail structure — only the
        # values differ.
        assert set(herdr_row) == set(zellij_row)
        assert set(herdr_row["detail"]) == set(zellij_row["detail"])
        assert zellij_row["detail"]["outcome"] == "delivered"
        assert zellij_row["detail"]["text"] == "run the probes"
