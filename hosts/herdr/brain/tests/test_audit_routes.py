"""HTTP-surface record points of the audit trail (Panel parity F1,
doc 02): one JSONL line per write action through the real routes,
device attribution from the access gate, the redaction mode, the
failure-isolation contract and the routes that must stay unaudited.
"""

from __future__ import annotations

import json
import logging
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import herdr_brain.server as server_module
from herdr_brain import pairing
from herdr_brain.config import Settings
from herdr_brain.permissions import PermissionRequest

from tests.conftest import SETTINGS_KWARGS
from tests.test_server import FakeLLM, FakeTTS, make_replay_tools

STATE_KEY = "state"


class ReplyTools:
    """BrainTools double: the narrow pane-write paths always succeed."""

    def __init__(self, cfg):
        self.last_active = None

    def reply_to_pane(self, pane_id, text):
        return {"outcome": "delivered", "pane_id": pane_id, "status": "done", "detail": ""}

    def send_keys_to_pane(self, pane_id, keys):
        return {"outcome": "sent", "pane_id": pane_id, "status": "done", "detail": ""}

    def status_payload(self):
        return {"active": False}

    def herd(self):
        return []


PANE = {"pane_id": "w1:p2", "agent": "opencode", "session_id": "ses_abc", "cwd": "/repo"}


class PaneInfoTools:
    """BrainTools double for the opencode permission surface."""

    def __init__(self, cfg):
        self.last_active = None

    def pane_info(self, pane_id):
        return dict(PANE) if pane_id == PANE["pane_id"] else None


class FakePermissions:
    def __init__(self):
        self.replies = []

    def list_pending(self, session_id):
        return [
            PermissionRequest(
                request_id="per_1", session_id=session_id,
                permission="edit", patterns=("src/x.py",),
            )
        ]

    def reply(self, request_id, reply, session_id):
        self.replies.append((request_id, reply, session_id))
        return True


def audit_app(
    tmp_path: Path,
    monkeypatch,
    *,
    audit_content: str = "preview",
    tools_cls=None,
    state_dir: Path = None,
    **create_kwargs,
) -> TestClient:
    state = state_dir if state_dir is not None else tmp_path / STATE_KEY
    kwargs = {
        **SETTINGS_KWARGS,
        "audio_dir": str(tmp_path / "audio"),
        "state_dir": str(state),
        "audit_content": audit_content,
    }
    cfg = Settings(**kwargs)
    tools_cls = tools_cls or make_replay_tools()
    monkeypatch.setattr(server_module, "BrainTools", tools_cls)
    create_kwargs.setdefault("llm_factory", lambda c, t: FakeLLM())
    create_kwargs.setdefault("tts_renderer", FakeTTS())
    return TestClient(server_module.create_app(settings=cfg, **create_kwargs))


def audit_path(tmp_path: Path) -> Path:
    return tmp_path / STATE_KEY / "audit.log"


def audit_lines(tmp_path: Path) -> list:
    path = audit_path(tmp_path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def propose(app, text="corre los tests"):
    return app.state.approval_store.propose(
        "s1", text=text, timeout_ms=300000, pane_id="w7:p4", agent="opencode"
    )


def pair_device(client: TestClient, state_dir: Path, label="phone") -> str:
    """Mints a code out-of-band (the operator's shell) and claims it."""
    minted = pairing.PairingStore(state_dir).mint()
    resp = client.post("/pair", json={"code": minted["code"], "label": label})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


# ── AC1: one line per successful /pane/reply, 0600 file, 0700 dir ─────────────


def test_pane_reply_success_writes_exactly_one_line(tmp_path, monkeypatch):
    client = audit_app(tmp_path, monkeypatch, tools_cls=ReplyTools)
    resp = client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "go"})
    assert resp.status_code == 200
    lines = audit_lines(tmp_path)
    assert len(lines) == 1
    line = lines[0]
    assert line["action"] == "pane.reply"
    assert line["pane_id"] == "w1:p2"
    assert line["detail"] == {"outcome": "delivered", "text": "go"}
    assert line["ts"]
    # No paired device on this request: the field is omitted, not null.
    assert "device" not in line
    path = audit_path(tmp_path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / STATE_KEY).stat().st_mode) == 0o700


# ── AC2: none mode redacts content, allowlisted metadata survives ─────────────


def test_none_mode_redacts_reply_text_and_keeps_keys(tmp_path, monkeypatch):
    client = audit_app(tmp_path, monkeypatch, audit_content="none", tools_cls=ReplyTools)
    assert client.post(
        "/pane/reply", json={"pane_id": "w1:p2", "text": "purge the database"}
    ).status_code == 200
    assert client.post(
        "/pane/keys", json={"pane_id": "w1:p2", "keys": ["enter"]}
    ).status_code == 200
    reply_line, keys_line = audit_lines(tmp_path)
    assert reply_line["detail"]["text"] == "⟨redacted⟩"
    assert reply_line["detail"]["outcome"] == "delivered"
    # keys come from a closed allowlist of 7 values: metadata, not content.
    assert keys_line["detail"]["keys"] == ["enter"]
    assert keys_line["detail"]["outcome"] == "sent"
    raw = audit_path(tmp_path).read_text(encoding="utf-8")
    assert "purge the database" not in raw


# ── AC3: an unwritable audit path never changes the audited action ────────────


def test_unwritable_audit_path_keeps_action_status_and_body(
    tmp_path, monkeypatch, caplog
):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the state dir should be")
    client = audit_app(tmp_path, monkeypatch, tools_cls=ReplyTools, state_dir=blocker)
    with caplog.at_level(logging.WARNING):
        resp = client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "go"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "pane_id": "w1:p2"}
    assert not (blocker / "audit.log").exists()
    # The failure is visible in the log, never silent.
    assert "audit" in caplog.text


# ── AC5: gate decisions carry gate_id, tool, revision; PATCH bumps ────────────


def test_gate_decisions_carry_gate_id_tool_revision_and_patch_bumps(
    tmp_path, monkeypatch
):
    client = audit_app(tmp_path, monkeypatch)
    gate = propose(client.app)

    assert client.patch(
        f"/approval/{gate.gate_id}", json={"text": "corre los tests con coverage"}
    ).status_code == 200
    assert client.post(f"/approval/{gate.gate_id}/approve").status_code == 200

    gate2 = propose(client.app, text="no mandar")
    assert client.post(f"/approval/{gate2.gate_id}/reject").status_code == 200

    lines = [l for l in audit_lines(tmp_path) if l["action"].startswith("approval.")]
    patch_line = next(l for l in lines if l["action"] == "approval.patch")
    assert patch_line["detail"]["gate_id"] == gate.gate_id
    assert patch_line["detail"]["tool"] == "send_to_session"
    assert patch_line["detail"]["revision"] == 1  # the NEW revision after the bump
    approve_line = next(l for l in lines if l["action"] == "approval.approve")
    assert approve_line["detail"] == {
        "gate_id": gate.gate_id, "tool": "send_to_session", "revision": 1,
    }
    assert approve_line["pane_id"] == "w7:p4"
    reject_line = next(l for l in lines if l["action"] == "approval.reject")
    assert reject_line["detail"] == {
        "gate_id": gate2.gate_id, "tool": "send_to_session", "revision": 0,
    }


# ── AC6: the ntfy action line and the background replay line ──────────────────


def test_ntfy_action_line_and_replay_line_carry_no_device(tmp_path, monkeypatch):
    client = audit_app(tmp_path, monkeypatch)
    gate = propose(client.app)
    resp = client.post(f"/approval/{gate.gate_id}/action", content="approve")
    assert resp.status_code == 200

    lines = audit_lines(tmp_path)
    action_line = next(l for l in lines if l["action"] == "approval.action")
    assert action_line["detail"]["decision"] == "approve"
    assert action_line["detail"]["gate_id"] == gate.gate_id
    assert "device" not in action_line  # ntfy carries no device identity
    replay_line = next(l for l in lines if l["action"] == "approval.replay")
    assert replay_line["detail"]["outcome"] == "ok"
    assert replay_line["detail"]["gate_id"] == gate.gate_id
    assert "device" not in replay_line


# ── AC7: reads, /tts, the speech transport and SSE produce no lines ───────────


def test_reads_tts_speech_and_sse_produce_no_lines(tmp_path, monkeypatch):
    client = audit_app(
        tmp_path, monkeypatch,
        tools_cls=ReplyTools, sse_stream_limit=2, sse_heartbeat_s=0.05,
    )
    assert client.get("/health").status_code == 200
    assert client.get("/state").status_code == 200
    assert client.get("/herd").status_code == 200
    assert client.post("/tts", json={"text": "eco local del PWA"}).status_code == 200
    assert client.get("/speech/neverseen/next").status_code == 404
    assert client.get("/events").status_code == 200
    assert audit_lines(tmp_path) == []
    assert not audit_path(tmp_path).exists()


# ── AC9: a pre-existing write route answers exactly as before ─────────────────


def test_preexisting_reject_route_response_is_unchanged(tmp_path, monkeypatch):
    client = audit_app(tmp_path, monkeypatch)
    gate = propose(client.app, text="no mandar")
    resp = client.post(f"/approval/{gate.gate_id}/reject")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "state": "rejected"}


# ── record points beyond the ACs: ask, permission reply, pair/revoke, voice ──


def test_ask_records_chars_and_target_pane_never_the_transcript(tmp_path, monkeypatch):
    client = audit_app(tmp_path, monkeypatch)
    text = "dile que arregle el login"
    resp = client.post(
        "/ask", json={"text": text, "session_id": "s1", "pane_id": "w1:p9"}
    )
    assert resp.status_code == 200
    lines = audit_lines(tmp_path)
    assert len(lines) == 1
    line = lines[0]
    assert line["action"] == "ask"
    assert line["detail"] == {"chars": len(text)}  # length only
    assert line["pane_id"] == "w1:p9"
    assert line["session_id"] == "s1"
    assert text not in audit_path(tmp_path).read_text(encoding="utf-8")


def test_permission_reply_records_reply_and_request_id(tmp_path, monkeypatch):
    client = audit_app(tmp_path, monkeypatch, tools_cls=PaneInfoTools,
                       permissions=FakePermissions())
    resp = client.post(
        "/pane/permission/reply",
        json={"pane_id": "w1:p2", "request_id": "per_1", "reply": "once"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    lines = audit_lines(tmp_path)
    assert len(lines) == 1
    line = lines[0]
    assert line["action"] == "pane.permission.reply"
    assert line["pane_id"] == "w1:p2"
    assert line["detail"] == {"reply": "once", "request_id": "per_1"}


def test_pair_and_revoke_are_audited_with_device_attribution(tmp_path, monkeypatch):
    client = audit_app(tmp_path, monkeypatch, tools_cls=ReplyTools)
    state_dir = tmp_path / STATE_KEY
    token = pair_device(client, state_dir, label="phone")

    # With a paired device the write routes require the bearer, and the
    # audit line carries its label.
    resp = client.post(
        "/pane/reply",
        json={"pane_id": "w1:p2", "text": "go"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    resp = client.post(
        "/devices/revoke", json={"label": "phone"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    lines = {l["action"]: l for l in audit_lines(tmp_path)}
    assert set(lines) == {"pair", "pane.reply", "device.revoke"}
    # The bootstrap pair happens before any credential: no device field.
    assert "device" not in lines["pair"]
    assert lines["pair"]["detail"] == {"label": "phone"}
    assert lines["pane.reply"]["device"] == "phone"
    assert lines["device.revoke"]["device"] == "phone"
    assert lines["device.revoke"]["detail"] == {"label": "phone"}


def test_voice_resolve_records_spoken_proof_and_voice_reject(tmp_path, monkeypatch):
    client = audit_app(tmp_path, monkeypatch)
    gate = propose(client.app)
    jobs = client.app.state.speech_jobs
    jobs.register("sp-approve-0001", "tok", "s1")
    jobs.mark("sp-approve-0001", "complete")
    client.app.state.approval_store.bind_explanation(gate.gate_id, "sp-approve-0001")
    resp = client.post(
        f"/approval/{gate.gate_id}/resolve",
        json={"utterance": "sí, envíalo", "speech_request_id": "sp-approve-0001"},
    )
    assert resp.json()["decision"] == "approve"

    gate2 = propose(client.app, text="no mandar")
    assert client.post(
        f"/approval/{gate2.gate_id}/resolve", json={"utterance": "no"}
    ).json()["decision"] == "reject"

    lines = [l for l in audit_lines(tmp_path) if l["action"] == "approval.resolve"]
    spoken, rejected = lines
    assert spoken["detail"]["gate_id"] == gate.gate_id
    assert spoken["detail"]["tool"] == "send_to_session"
    assert spoken["detail"]["revision"] == 0
    assert spoken["detail"]["spoken"] is True  # executed via spoken proof
    assert spoken["detail"]["decision"] == "approve"
    assert rejected["detail"]["decision"] == "reject"
    assert "spoken" not in rejected["detail"]  # a voice «no» needs no proof
