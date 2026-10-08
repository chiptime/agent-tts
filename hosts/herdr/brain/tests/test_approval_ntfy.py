"""F5 / HT-04: ntfy ``http`` action buttons resolving approval gates.

ntfy action buttons are plain HTTP requests fired by the phone: a POST with
an optional raw/JSON/form body and optional custom headers. These tests play
the role of the ntfy app against ``POST /approval/{gate_id}/action`` and
assert the gate resolves immediately, the frozen send is dispatched exactly
once on approve, and nothing happens on a bad token or a dead gate.
"""

from __future__ import annotations

import logging
import time

import pytest
from fastapi.testclient import TestClient

import herdr_brain.server as server_module
from herdr_brain.config import Settings, load_settings
from tests.test_server import FakeLLM, FakeTTS, make_replay_tools

TOKEN = "s3cret-Token_0.9~x"


@pytest.fixture
def audio_dir(tmp_path):
    return tmp_path / "audio"


def ntfy_app(settings, audio_dir, monkeypatch, token=TOKEN, approval_timeout_s=None,
             llm=None, tools_cls=None):
    """create_app wired like the approval tests, with an approval token."""
    overrides = {"audio_dir": str(audio_dir), "approval_token": token}
    if approval_timeout_s is not None:
        overrides["approval_timeout_s"] = approval_timeout_s
    cfg = Settings(**{**settings.__dict__, **overrides})
    tools_cls = tools_cls or make_replay_tools()
    monkeypatch.setattr(server_module, "BrainTools", tools_cls)
    llm = llm or FakeLLM(result={
        "answer": "Sent.", "pane_id": "w7:p4", "agent": "opencode", "session_id": "s1",
    })
    app = server_module.create_app(
        settings=cfg, llm_factory=lambda c, t: llm, tts_renderer=FakeTTS()
    )
    app.state.test_tools_cls = tools_cls
    app.state.test_llm = llm
    return app


def propose(app, text="corre los tests"):
    return app.state.approval_store.propose(
        "s1", text=text, timeout_ms=300000, pane_id="w7:p4", agent="opencode"
    )


def bearer(token=TOKEN):
    return {"Authorization": f"Bearer {token}"}


def dispatches(app):
    created = app.state.test_tools_cls.created
    return [d for tools in created for d in tools.dispatches]


class TestNtfyApprove:
    def test_approve_raw_text_body_resolves_gate_and_unblocks_agent(
        self, settings, audio_dir, monkeypatch
    ):
        app = ntfy_app(settings, audio_dir, monkeypatch)
        gate = propose(app)
        resp = TestClient(app).post(
            f"/approval/{gate.gate_id}/action", content="approve", headers=bearer()
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "decision": "approve", "state": "approved"}
        assert app.state.approval_store.get(gate.gate_id).state == "approved"
        # THE unblock: exact frozen args dispatched exactly once.
        sent = dispatches(app)
        assert len(sent) == 1
        assert sent[0]["name"] == "send_to_session"
        assert sent[0]["arguments"] == {"text": "corre los tests", "timeout_ms": 300000}
        assert sent[0]["target"].pane_id == "w7:p4"
        # The model still gets to report the outcome (conversation ring stays truthful).
        assert len(app.state.test_llm.calls) == 1

    def test_approve_does_not_render_tts(self, settings, audio_dir, monkeypatch):
        app = ntfy_app(settings, audio_dir, monkeypatch)
        gate = propose(app)
        TestClient(app).post(
            f"/approval/{gate.gate_id}/action", content="approve", headers=bearer()
        )
        # Nobody is listening on the phone: no MP3 is rendered for the report.
        assert not list(audio_dir.glob("*")) if audio_dir.exists() else True

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"json": {"decision": "approve"}},
            {"data": {"decision": "approve"}},
            {"content": "decision=approve", "headers": {"Content-Type": "text/plain"}},
            {"content": "  APPROVE \n"},
            {"params": {"decision": "approve"}},
        ],
        ids=["json", "form", "form-as-text", "raw-case-whitespace", "query"],
    )
    def test_accepted_body_shapes(self, settings, audio_dir, monkeypatch, kwargs):
        app = ntfy_app(settings, audio_dir, monkeypatch)
        gate = propose(app)
        headers = {**bearer(), **kwargs.pop("headers", {})}
        resp = TestClient(app).post(
            f"/approval/{gate.gate_id}/action", headers=headers, **kwargs
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["decision"] == "approve"
        assert len(dispatches(app)) == 1


class TestNtfyReject:
    def test_reject_resolves_gate_and_dispatches_nothing(
        self, settings, audio_dir, monkeypatch
    ):
        app = ntfy_app(settings, audio_dir, monkeypatch)
        gate = propose(app)
        resp = TestClient(app).post(
            f"/approval/{gate.gate_id}/action", content="reject", headers=bearer()
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "decision": "reject", "state": "rejected"}
        assert app.state.approval_store.get(gate.gate_id).state == "rejected"
        assert dispatches(app) == []
        assert app.state.test_llm.calls == []

    def test_unknown_decision_is_422_and_leaves_gate_live(
        self, settings, audio_dir, monkeypatch
    ):
        app = ntfy_app(settings, audio_dir, monkeypatch)
        gate = propose(app)
        client = TestClient(app)
        for body in ("maybe", "", "{}", '{"decision": 3}', "decision=stop"):
            resp = client.post(
                f"/approval/{gate.gate_id}/action", content=body, headers=bearer()
            )
            assert resp.status_code == 422, body
        assert app.state.approval_store.get(gate.gate_id).state == "proposed"
        assert dispatches(app) == []


class TestNtfyAuth:
    def test_missing_token_is_401_and_gate_untouched(self, settings, audio_dir, monkeypatch):
        app = ntfy_app(settings, audio_dir, monkeypatch)
        gate = propose(app)
        resp = TestClient(app).post(f"/approval/{gate.gate_id}/action", content="approve")
        assert resp.status_code == 401
        assert resp.headers["www-authenticate"] == "Bearer"
        assert app.state.approval_store.get(gate.gate_id).state == "proposed"
        assert dispatches(app) == []

    @pytest.mark.parametrize(
        "headers",
        [
            {"Authorization": "Bearer wrong"},
            {"Authorization": f"Basic {TOKEN}"},
            {"Authorization": TOKEN},
            {"Authorization": "Bearer "},
            {"Authorization": f"Bearer {TOKEN}x"},
            {"Authorization": f"Bearer {TOKEN[:-1]}"},
        ],
        ids=["wrong", "wrong-scheme", "no-scheme", "empty", "longer", "shorter"],
    )
    def test_bad_token_is_401_and_gate_untouched(
        self, settings, audio_dir, monkeypatch, headers
    ):
        app = ntfy_app(settings, audio_dir, monkeypatch)
        gate = propose(app)
        resp = TestClient(app).post(
            f"/approval/{gate.gate_id}/action", content="approve", headers=headers
        )
        assert resp.status_code == 401
        assert app.state.approval_store.get(gate.gate_id).state == "proposed"
        assert dispatches(app) == []

    def test_reject_also_requires_the_token(self, settings, audio_dir, monkeypatch):
        app = ntfy_app(settings, audio_dir, monkeypatch)
        gate = propose(app)
        resp = TestClient(app).post(f"/approval/{gate.gate_id}/action", content="reject")
        assert resp.status_code == 401
        assert app.state.approval_store.get(gate.gate_id).state == "proposed"

    def test_query_token_is_accepted(self, settings, audio_dir, monkeypatch):
        app = ntfy_app(settings, audio_dir, monkeypatch)
        gate = propose(app)
        resp = TestClient(app).post(
            f"/approval/{gate.gate_id}/action", content="approve", params={"token": TOKEN}
        )
        assert resp.status_code == 200
        assert app.state.approval_store.get(gate.gate_id).state == "approved"

    def test_wrong_query_token_is_401(self, settings, audio_dir, monkeypatch):
        app = ntfy_app(settings, audio_dir, monkeypatch)
        gate = propose(app)
        resp = TestClient(app).post(
            f"/approval/{gate.gate_id}/action", content="approve", params={"token": "nope"}
        )
        assert resp.status_code == 401
        assert app.state.approval_store.get(gate.gate_id).state == "proposed"

    def test_auth_runs_before_gate_lookup(self, settings, audio_dir, monkeypatch):
        """A bad token must not tell the caller whether a gate id exists."""
        app = ntfy_app(settings, audio_dir, monkeypatch)
        resp = TestClient(app).post("/approval/doesnotexist/action", content="approve")
        assert resp.status_code == 401

    def test_token_comparison_is_constant_time(self, settings, audio_dir, monkeypatch):
        calls = []
        real = server_module.hmac.compare_digest

        def spy(a, b):
            calls.append((a, b))
            return real(a, b)

        monkeypatch.setattr(server_module.hmac, "compare_digest", spy)
        app = ntfy_app(settings, audio_dir, monkeypatch)
        gate = propose(app)
        TestClient(app).post(
            f"/approval/{gate.gate_id}/action", content="approve", headers=bearer("wrong")
        )
        assert len(calls) == 1

    def test_secret_is_never_logged_or_echoed(
        self, settings, audio_dir, monkeypatch, caplog
    ):
        app = ntfy_app(settings, audio_dir, monkeypatch)
        gate = propose(app)
        client = TestClient(app)
        with caplog.at_level(logging.DEBUG):
            bad = client.post(
                f"/approval/{gate.gate_id}/action", content="approve",
                headers=bearer("attacker-guess"),
            )
            good = client.post(
                f"/approval/{gate.gate_id}/action", content="approve", headers=bearer()
            )
        for text in (bad.text, good.text, caplog.text):
            assert TOKEN not in text
            assert "attacker-guess" not in text

    def test_no_token_configured_keeps_the_open_behaviour(
        self, settings, audio_dir, monkeypatch
    ):
        app = ntfy_app(settings, audio_dir, monkeypatch, token="")
        gate = propose(app)
        resp = TestClient(app).post(f"/approval/{gate.gate_id}/action", content="approve")
        assert resp.status_code == 200
        assert app.state.approval_store.get(gate.gate_id).state == "approved"

    def test_existing_pwa_routes_do_not_require_the_token(
        self, settings, audio_dir, monkeypatch
    ):
        """The PWA is same-origin and sends no token: it must keep working."""
        app = ntfy_app(settings, audio_dir, monkeypatch)
        gate = propose(app)
        client = TestClient(app)
        assert client.post(f"/approval/{gate.gate_id}/reject").status_code == 200


class TestNtfyDeadGates:
    def test_unknown_gate_is_404(self, settings, audio_dir, monkeypatch):
        app = ntfy_app(settings, audio_dir, monkeypatch)
        resp = TestClient(app).post(
            "/approval/doesnotexist/action", content="approve", headers=bearer()
        )
        assert resp.status_code == 404
        assert dispatches(app) == []

    def test_second_press_is_404_and_executes_once(self, settings, audio_dir, monkeypatch):
        app = ntfy_app(settings, audio_dir, monkeypatch)
        gate = propose(app)
        client = TestClient(app)
        url = f"/approval/{gate.gate_id}/action"
        assert client.post(url, content="approve", headers=bearer()).status_code == 200
        assert client.post(url, content="approve", headers=bearer()).status_code == 404
        assert client.post(url, content="reject", headers=bearer()).status_code == 404
        assert len(dispatches(app)) == 1
        assert app.state.approval_store.get(gate.gate_id).state == "approved"

    def test_expired_gate_is_404(self, settings, audio_dir, monkeypatch):
        app = ntfy_app(settings, audio_dir, monkeypatch, approval_timeout_s=0)
        gate = propose(app)
        time.sleep(0.01)
        resp = TestClient(app).post(
            f"/approval/{gate.gate_id}/action", content="approve", headers=bearer()
        )
        assert resp.status_code == 404
        assert dispatches(app) == []

    def test_gate_rejected_in_the_pwa_cannot_be_approved_from_ntfy(
        self, settings, audio_dir, monkeypatch
    ):
        app = ntfy_app(settings, audio_dir, monkeypatch)
        gate = propose(app)
        client = TestClient(app)
        assert client.post(f"/approval/{gate.gate_id}/reject").status_code == 200
        resp = client.post(
            f"/approval/{gate.gate_id}/action", content="approve", headers=bearer()
        )
        assert resp.status_code == 404
        assert dispatches(app) == []


class TestApprovalTokenSetting:
    def test_default_is_empty(self):
        assert load_settings({}).approval_token == ""

    def test_env_value_is_loaded_and_stripped(self):
        cfg = load_settings({"HERDR_BRAIN_APPROVAL_TOKEN": f"  {TOKEN}  "})
        assert cfg.approval_token == TOKEN

    def test_repr_never_contains_the_token(self):
        cfg = load_settings({"HERDR_BRAIN_APPROVAL_TOKEN": TOKEN})
        assert TOKEN not in repr(cfg)
