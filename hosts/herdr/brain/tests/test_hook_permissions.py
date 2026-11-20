"""Claude hook-held permissions (parity-f7e S3, audit 02 §5).

The Claude Code ``PreToolUse`` hook blocks on a local brain unix socket
until the operator answers from the PWA or by voice; with no answer
before the timeout — or on ANY error — it DENIES. These tests pin the
broker (decide-once), the socket protocol, the hook client's Claude
contract output, the deny-on-every-failure rule, the masking of
tool_input before it reaches the PWA, the voice gate, and the HTTP
routes.
"""

from __future__ import annotations

import json
import socket
import threading
import time

import pytest
from fastapi.testclient import TestClient

import herdr_brain.server as server_module
from herdr_brain.approval_lexicon import resolve_utterance  # noqa: F401 — sanity import
from herdr_brain.config import Settings
from herdr_brain.hook_permissions import (
    DECISION_ALLOW,
    DECISION_DENY,
    DEFAULT_HOOK_TIMEOUT_S,
    HookPermissionBroker,
    HookPermissionSocketServer,
    default_socket_path,
    run_hook,
)

SECRET_COMMAND = "export API_KEY=sk-ant-api03-real-secret-value-123456 && deploy"


def make_broker(**kwargs) -> HookPermissionBroker:
    return HookPermissionBroker(**kwargs)


# -- broker: hold, decide once, pending shape -------------------------------


def test_hold_and_answer_applies_exactly_once():
    broker = make_broker()
    held = broker.hold(
        tool_name="Bash",
        tool_input={"command": "echo hook-probe"},
        session_id="ses-abc",
        cwd="/repo",
    )
    assert held.request_id
    assert len(held.request_id) >= 20  # unguessable id
    pending = broker.pending()
    assert [item["request_id"] for item in pending] == [held.request_id]
    assert pending[0]["tool_name"] == "Bash"

    resolved, applied = broker.answer(held.request_id, DECISION_ALLOW, by="pwa")
    assert applied is True
    assert resolved.decision == DECISION_ALLOW
    assert broker.pending() == []

    # A second decision on the same request is refused (apply-once).
    again, applied2 = broker.answer(held.request_id, DECISION_DENY, by="pwa")
    assert applied2 is False
    assert again is not None and again.decision == DECISION_ALLOW


def test_answer_unknown_id_applies_nothing():
    broker = make_broker()
    resolved, applied = broker.answer("nope", DECISION_DENY, by="pwa")
    assert resolved is None
    assert applied is False


def test_pending_summary_masks_secrets_before_the_pwa_sees_them():
    broker = make_broker()
    broker.hold(
        tool_name="Bash",
        tool_input={"command": SECRET_COMMAND},
        session_id="ses-abc",
        cwd="/repo",
    )
    summary = broker.pending()[0]["summary"]
    assert "sk-ant-api03-real-secret-value-123456" not in summary
    assert "deploy" in summary  # the safe part survives


def test_pending_summary_is_bounded():
    broker = make_broker()
    broker.hold(
        tool_name="Bash",
        tool_input={"command": "x" * 10000},
        session_id="ses-abc",
        cwd="/repo",
    )
    assert len(broker.pending()[0]["summary"]) <= 300


def test_broker_rejects_unknown_decision_words():
    broker = make_broker()
    held = broker.hold(tool_name="Bash", tool_input={}, session_id="s", cwd="/")
    with pytest.raises(ValueError):
        broker.answer(held.request_id, "once", by="pwa")
    # nothing applied
    assert broker.pending()[0]["request_id"] == held.request_id


# -- socket protocol ---------------------------------------------------------


def _connect(path) -> socket.socket:
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.connect(str(path))
    return conn


def _read_line(conn) -> dict:
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = conn.recv(4096)
        if not chunk:
            break
        buf += chunk
    return json.loads(buf.decode("utf-8").strip())


def test_socket_roundtrip_allows(tmp_path):
    broker = make_broker()
    server = HookPermissionSocketServer(tmp_path, broker)
    server.start()
    try:
        conn = _connect(server.path)
        request = {
            "v": 1,
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "echo hook-probe"},
            "session_id": "ses-abc",
            "cwd": "/repo",
        }
        conn.sendall(json.dumps(request).encode("utf-8") + b"\n")
        # wait for the hold to appear, then answer it
        deadline = time.time() + 5
        while not broker.pending() and time.time() < deadline:
            time.sleep(0.02)
        broker.answer(broker.pending()[0]["request_id"], DECISION_ALLOW, by="pwa")
        reply = _read_line(conn)
        conn.close()
        assert reply["decision"] == DECISION_ALLOW
        assert reply["v"] == 1
    finally:
        server.stop()


def test_socket_timeout_denies_by_default(tmp_path):
    broker = make_broker(timeout_s=0.4)
    server = HookPermissionSocketServer(tmp_path, broker)
    server.start()
    try:
        conn = _connect(server.path)
        conn.sendall(
            json.dumps(
                {"v": 1, "tool_name": "Bash", "tool_input": {}, "session_id": "s", "cwd": "/"}
            ).encode("utf-8")
            + b"\n"
        )
        conn.settimeout(5)
        reply = _read_line(conn)
        conn.close()
        assert reply["decision"] == DECISION_DENY
        assert "time" in reply.get("reason", "").lower()
    finally:
        server.stop()


def test_socket_malformed_line_denies(tmp_path):
    broker = make_broker()
    server = HookPermissionSocketServer(tmp_path, broker)
    server.start()
    try:
        conn = _connect(server.path)
        conn.sendall(b"this is not json\n")
        conn.settimeout(5)
        reply = _read_line(conn)
        conn.close()
        assert reply["decision"] == DECISION_DENY
    finally:
        server.stop()


def test_socket_oversized_line_denies(tmp_path):
    broker = make_broker()
    server = HookPermissionSocketServer(tmp_path, broker)
    server.start()
    try:
        conn = _connect(server.path)
        conn.sendall(b"x" * 200000 + b"\n")
        conn.settimeout(5)
        reply = _read_line(conn)
        conn.close()
        assert reply["decision"] == DECISION_DENY
    finally:
        server.stop()


def test_socket_file_is_user_private(tmp_path):
    broker = make_broker()
    server = HookPermissionSocketServer(tmp_path, broker)
    server.start()
    try:
        mode = server.path.stat().st_mode & 0o777
        assert mode == 0o600
        assert server.path.parent == tmp_path
        assert server.path.name == "hook-permissions.sock"
    finally:
        server.stop()


def test_default_socket_path_lives_under_the_state_dir():
    from pathlib import Path

    assert default_socket_path("/tmp/state-x") == Path(
        "/tmp/state-x/hook-permissions.sock"
    )


# -- hook client (what Claude runs) ------------------------------------------


def _spawn_answerer(broker, decision, by="pwa"):
    def worker():
        deadline = time.time() + 5
        while not broker.pending() and time.time() < deadline:
            time.sleep(0.02)
        if broker.pending():
            broker.answer(broker.pending()[0]["request_id"], decision, by=by)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


STDIN_REQUEST = json.dumps(
    {
        "session_id": "ses-abc",
        "cwd": "/repo",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "echo hook-probe"},
    }
)


def test_run_hook_allows_over_a_live_socket(tmp_path):
    broker = make_broker()
    server = HookPermissionSocketServer(tmp_path, broker)
    server.start()
    try:
        _spawn_answerer(broker, DECISION_ALLOW)
        code, payload = run_hook(STDIN_REQUEST, socket_path=server.path, timeout_s=5)
        assert code == 0
        assert payload["hookSpecificOutput"]["permissionDecision"] == "allow"
        assert payload["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    finally:
        server.stop()


def test_run_hook_denies_on_timeout(tmp_path):
    broker = make_broker(timeout_s=5)
    server = HookPermissionSocketServer(tmp_path, broker)
    server.start()
    try:
        # nobody answers; the hook's own short deadline fires first
        code, payload = run_hook(STDIN_REQUEST, socket_path=server.path, timeout_s=0.4)
        assert code == 0
        out = payload["hookSpecificOutput"]
        assert out["permissionDecision"] == "deny"
        assert out["permissionDecisionReason"]
    finally:
        server.stop()


def test_run_hook_denies_when_the_brain_is_down(tmp_path):
    code, payload = run_hook(
        STDIN_REQUEST, socket_path=tmp_path / "missing.sock", timeout_s=1
    )
    assert code == 0
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_run_hook_denies_on_malformed_stdin(tmp_path):
    broker = make_broker()
    server = HookPermissionSocketServer(tmp_path, broker)
    server.start()
    try:
        code, payload = run_hook("not json", socket_path=server.path, timeout_s=1)
        assert code == 0
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
    finally:
        server.stop()


def test_run_hook_denies_on_malformed_reply(tmp_path):
    path = tmp_path / "hook-permissions.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(1)

    def serve_garbage():
        conn, _ = listener.accept()
        conn.recv(65536)
        conn.sendall(b"garbage-not-json\n")
        conn.close()

    thread = threading.Thread(target=serve_garbage, daemon=True)
    thread.start()
    try:
        code, payload = run_hook(STDIN_REQUEST, socket_path=path, timeout_s=5)
        assert code == 0
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
    finally:
        listener.close()


def test_run_hook_masks_secrets_in_what_the_pwa_sees(tmp_path):
    broker = make_broker()
    server = HookPermissionSocketServer(tmp_path, broker)
    server.start()
    try:
        _spawn_answerer(broker, DECISION_DENY)
        code, _ = run_hook(
            json.dumps(
                {
                    "session_id": "s",
                    "cwd": "/",
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": SECRET_COMMAND},
                }
            ),
            socket_path=server.path,
            timeout_s=5,
        )
        assert code == 0
        pending = broker.pending()
        assert pending == []  # answered
    finally:
        server.stop()


def test_hook_timeout_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("HERDR_BRAIN_HOOK_TIMEOUT_S", "3.5")
    broker = make_broker()
    assert broker.timeout_s == 3.5


def test_default_hook_timeout_is_bounded():
    assert 30 <= DEFAULT_HOOK_TIMEOUT_S <= 600


# -- voice gate ----------------------------------------------------------------


def test_voice_gate_allows_on_a_confirmation_word(tmp_path):
    from herdr_brain.tools import BrainTools

    broker = make_broker()
    tools = BrainTools.__new__(BrainTools)  # no __init__ subprocesses
    tools.attach_hook_broker(broker)
    broker.hold(
        tool_name="Bash",
        tool_input={"command": "echo hook-probe"},
        session_id="ses-abc",
        cwd="/repo",
    )
    tools.held_permissions_context()  # the live context spoke it
    outcome = json.loads(tools.answer_hold("dale", turn_text="dale"))
    assert outcome["outcome"] == "answered"
    assert outcome["decision"] == DECISION_ALLOW


def test_voice_gate_denies_on_a_rejection_word(tmp_path):
    from herdr_brain.tools import BrainTools

    broker = make_broker()
    tools = BrainTools.__new__(BrainTools)
    tools.attach_hook_broker(broker)
    broker.hold(tool_name="Bash", tool_input={}, session_id="s", cwd="/")
    tools.held_permissions_context()
    outcome = json.loads(tools.answer_hold("no", turn_text="no"))
    assert outcome["outcome"] == "answered"
    assert outcome["decision"] == DECISION_DENY


def test_voice_gate_refuses_words_that_are_not_a_decision():
    from herdr_brain.tools import BrainTools

    broker = make_broker()
    tools = BrainTools.__new__(BrainTools)
    tools.attach_hook_broker(broker)
    broker.hold(tool_name="Bash", tool_input={}, session_id="s", cwd="/")
    outcome = json.loads(tools.answer_hold("qué hora es", turn_text="qué hora es"))
    assert outcome["outcome"] == "refused"
    assert broker.pending()  # nothing was decided


def test_voice_gate_requires_the_hold_to_have_been_spoken():
    from herdr_brain.tools import BrainTools

    broker = make_broker()
    tools = BrainTools.__new__(BrainTools)
    tools.attach_hook_broker(broker)
    broker.hold(tool_name="Bash", tool_input={}, session_id="s", cwd="/")
    # never called held_permissions_context(): not explained
    outcome = json.loads(tools.answer_hold("dale", turn_text="dale"))
    assert outcome["outcome"] == "refused"
    assert outcome["reason"] == "not-explained"


def test_voice_gate_ambiguous_hold_needs_the_number():
    from herdr_brain.tools import BrainTools

    broker = make_broker()
    tools = BrainTools.__new__(BrainTools)
    tools.attach_hook_broker(broker)
    broker.hold(tool_name="Bash", tool_input={}, session_id="s1", cwd="/")
    broker.hold(tool_name="Edit", tool_input={}, session_id="s2", cwd="/")
    tools.held_permissions_context()  # explained
    outcome = json.loads(tools.answer_hold("dale", turn_text="dale"))
    assert outcome["outcome"] == "refused"
    assert outcome["reason"] == "ambiguous"
    # with the spoken number it answers that exact hold
    outcome2 = json.loads(tools.answer_hold("dale", turn_text="dale", hold=2))
    assert outcome2["outcome"] == "answered"
    assert outcome2["decision"] == DECISION_ALLOW


def test_held_permissions_context_marks_explained_and_masks():
    from herdr_brain.tools import BrainTools

    broker = make_broker()
    tools = BrainTools.__new__(BrainTools)
    tools.attach_hook_broker(broker)
    broker.hold(
        tool_name="Bash", tool_input={"command": SECRET_COMMAND}, session_id="s", cwd="/"
    )
    lines = tools.held_permissions_context()
    assert lines
    joined = "\n".join(lines)
    assert "sk-ant-api03-real-secret-value-123456" not in joined
    assert "answer_hold" in joined


# -- live context (llm) --------------------------------------------------------


def test_live_context_carries_the_held_hint():
    from herdr_brain.llm import build_live_context

    text = build_live_context(None, holds=["HOLD 1: claude wants Bash: echo hi"])
    assert "HOLD 1: claude wants Bash: echo hi" in text


# -- HTTP routes ----------------------------------------------------------------


def _route_settings_kwargs():
    from tests.conftest import SETTINGS_KWARGS

    return dict(SETTINGS_KWARGS)


@pytest.fixture
def route_client(tmp_path, monkeypatch):
    broker = make_broker(timeout_s=10)
    app = server_module.create_app(
        settings=Settings(**{**_route_settings_kwargs(), "state_dir": str(tmp_path)}),
        hook_broker=broker,
    )
    return TestClient(app), broker, tmp_path


def test_get_lists_pending_requests(route_client):
    client, broker, _ = route_client
    held = broker.hold(
        tool_name="Bash", tool_input={"command": "echo hi"}, session_id="s", cwd="/"
    )
    body = client.get("/hook/permissions").json()
    assert [item["request_id"] for item in body["pending"]] == [held.request_id]
    assert body["pending"][0]["tool_name"] == "Bash"
    assert "expires_in_s" in body["pending"][0]


def test_post_answer_allows_and_audits(route_client):
    client, broker, tmp = route_client
    held = broker.hold(
        tool_name="Bash", tool_input={"command": "echo hi"}, session_id="s", cwd="/"
    )
    response = client.post(
        f"/hook/permissions/{held.request_id}/answer", json={"decision": "allow"}
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True, "decision": "allow"}
    # the waiting hook side sees the decision
    assert broker.get(held.request_id).decision == DECISION_ALLOW
    audit = (tmp / "audit.log").read_text()
    assert "hook.permission.answer" in audit


def test_post_answer_twice_is_409(route_client):
    client, broker, _ = route_client
    held = broker.hold(tool_name="Bash", tool_input={}, session_id="s", cwd="/")
    first = client.post(
        f"/hook/permissions/{held.request_id}/answer", json={"decision": "deny"}
    )
    assert first.status_code == 200
    second = client.post(
        f"/hook/permissions/{held.request_id}/answer", json={"decision": "allow"}
    )
    assert second.status_code == 409


def test_post_answer_unknown_id_is_404(route_client):
    client, _, _ = route_client
    response = client.post("/hook/permissions/nope/answer", json={"decision": "allow"})
    assert response.status_code == 404


def test_post_answer_bad_decision_is_422(route_client):
    client, broker, _ = route_client
    held = broker.hold(tool_name="Bash", tool_input={}, session_id="s", cwd="/")
    response = client.post(
        f"/hook/permissions/{held.request_id}/answer", json={"decision": "once"}
    )
    assert response.status_code == 422
    assert broker.pending()  # untouched


def test_answer_route_is_classified_as_a_write():
    from herdr_brain.access import classify

    assert classify("POST", "/hook/permissions/abc/answer") == "write"
    assert classify("GET", "/hook/permissions") == "read"


def test_holds_are_audited_when_held(route_client):
    client, broker, tmp = route_client
    broker.hold(tool_name="Bash", tool_input={"command": "echo hi"}, session_id="s", cwd="/")
    audit = (tmp / "audit.log").read_text()
    assert "hook.hold" in audit


# -- the hook never fails open (feature-end verifier, parity-f7e) ----------


def _hook_with_fake_brain(tmp_path, reply: bytes, stdin_text: str):
    """Runs the real hook entrypoint against a one-shot fake brain socket
    that answers ``reply``; returns the CompletedProcess."""
    import os
    import subprocess
    import sys

    path = str(tmp_path / "hook.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(1)

    def answer():
        try:
            conn, _ = server.accept()
        except OSError:
            return
        with conn:
            conn.recv(65536)
            conn.sendall(reply)

    thread = threading.Thread(target=answer, daemon=True)
    thread.start()
    try:
        return subprocess.run(
            [sys.executable, "-m", "herdr_brain.hook_permissions"],
            input=stdin_text,
            text=True,
            capture_output=True,
            timeout=10,
            env={**os.environ, "HERDR_BRAIN_HOOK_SOCKET": path},
        )
    finally:
        server.close()
        thread.join(timeout=5)


@pytest.mark.parametrize("reply", [b"[]\n", b'"allow"\n', b"42\n", b"null\n"])
def test_a_non_object_brain_reply_denies(tmp_path, reply):
    result = _hook_with_fake_brain(tmp_path, reply, '{"tool_name":"Bash"}')

    assert result.returncode == 0
    decision = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]
    assert decision == "deny"


def test_a_pathologically_nested_request_still_denies(tmp_path):
    result = _hook_with_fake_brain(tmp_path, b"{}\n", "[" * 5000 + "]" * 5000)

    assert result.returncode == 0
    decision = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]
    assert decision == "deny"


def test_post_answer_keeps_attribution_in_none_mode(tmp_path):
    """T3/S3: the hook answer's `by` field is attribution — it must survive
    HERDR_BRAIN_AUDIT_CONTENT=none exactly like the envelope `device` does
    (the auditor's redaction-gap finding)."""
    import json

    broker = make_broker(timeout_s=10)
    app = server_module.create_app(
        settings=Settings(**{
            **_route_settings_kwargs(), "state_dir": str(tmp_path),
            "audit_content": "none",
        }),
        hook_broker=broker,
    )
    client = TestClient(app)
    held = broker.hold(
        tool_name="Bash", tool_input={"command": "echo hi"}, session_id="s", cwd="/"
    )
    response = client.post(
        f"/hook/permissions/{held.request_id}/answer", json={"decision": "allow"}
    )
    assert response.status_code == 200
    lines = (tmp_path / "audit.log").read_text().splitlines()
    entry = json.loads(next(line for line in lines if "hook.permission.answer" in line))
    assert entry["detail"]["by"] == "pwa"       # unresolved device falls back
    assert entry["detail"]["decision"] == "allow"
    assert entry["detail"]["tool_name"] == "⟨redacted⟩"
