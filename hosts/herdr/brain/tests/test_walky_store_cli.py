"""Store durability (modes, atomicity, retention) and the CLI surface."""

from __future__ import annotations

import io
import json
import stat

import pytest

from herdr_brain.herdr import AgentInfo
from herdr_brain.walky import MessageNotFound, WalkyService, cli
from herdr_brain.walky.store import StoreError


class Clock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeMux:
    def __init__(self, panes) -> None:
        self.panes = list(panes)
        self.typed: list = []

    def list_agents(self):
        return list(self.panes)

    def read_screen(self, pane_id, n_lines=None, source="visible", as_ansi=False):
        return ""

    def pane_run(self, pane_id, command):
        self.typed.append((pane_id, command))


def pane(pid, token, status="idle", harness="opencode", title="pane title"):
    return AgentInfo(pid, harness, status, "id" if token else "", token, "/repo", title, False)


def ids(start=1):
    n = start

    def factory():
        nonlocal n
        mid = f"bm_{n:010d}"
        n += 1
        return mid

    return factory


def env(pid, state_dir):
    return {
        "HERDR_ENV": "1",
        "HERDR_PANE_ID": pid,
        "HERDR_BRAIN_STATE_DIR": str(state_dir),
    }


# -- store durability ------------------------------------------------------------


def test_message_dir_is_0700_and_ledger_0600(tmp_path):
    mux = FakeMux([pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b")])
    svc = WalkyService(state_dir=tmp_path, mux=mux, clock=Clock(), id_factory=ids())
    svc.operator_send("phone", "w2:p2#ses_b", "hi")
    messages_dir = tmp_path / "messages"
    assert stat.S_IMODE(messages_dir.stat().st_mode) == 0o700
    ledger = messages_dir / "ledger.json"
    assert ledger.exists()
    assert stat.S_IMODE(ledger.stat().st_mode) == 0o600


def test_ledger_survives_reopen(tmp_path):
    mux = FakeMux([pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b", status="working")])
    clock = Clock()
    svc = WalkyService(state_dir=tmp_path, mux=mux, clock=clock, id_factory=ids())
    svc.operator_send("phone", "w2:p2#ses_b", "persisted")

    reopened = WalkyService(state_dir=tmp_path, mux=mux, clock=clock, id_factory=ids())
    assert reopened.inbox("w2:p2")[0].body == "persisted"


def test_no_temp_files_left_behind(tmp_path):
    mux = FakeMux([pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b")])
    svc = WalkyService(state_dir=tmp_path, mux=mux, clock=Clock(), id_factory=ids())
    for i in range(5):
        svc.operator_send("phone", "w2:p2#ses_b", f"msg {i}")
    # The ledger and its persistent save lock are the only files; no temp
    # file of an interrupted or completed save may remain.
    leftovers = [
        p.name for p in (tmp_path / "messages").iterdir()
        if p.name not in ("ledger.json", "ledger.lock")
    ]
    assert leftovers == []


def test_corrupt_ledger_fails_loudly(tmp_path):
    mux = FakeMux([pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b")])
    (tmp_path / "messages").mkdir(parents=True)
    (tmp_path / "messages" / "ledger.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(StoreError):
        WalkyService(state_dir=tmp_path, mux=mux, clock=Clock(), id_factory=ids())


def test_old_read_messages_are_pruned_recent_kept(tmp_path):
    mux = FakeMux([pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b")])
    clock = Clock()
    svc = WalkyService(state_dir=tmp_path, mux=mux, clock=clock, id_factory=ids())
    old = svc.operator_send("phone", "w2:p2#ses_b", "old and read")
    svc.inbox("w2:p2")  # marks it read

    clock.advance(30 * 86400)
    svc.operator_send("phone", "w2:p2#ses_b", "fresh unread")
    with pytest.raises(MessageNotFound):
        svc.status(old.id)  # pruned: no longer known


# -- CLI ---------------------------------------------------------------------------


def run_cli(argv, state_dir, mux, env_extra=None, stdin=None):
    e = {
        "HERDR_BRAIN_STATE_DIR": str(state_dir),
        **(env_extra or {}),
    }
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(
        argv,
        env=e,
        mux=mux,
        clock=Clock(),
        id_factory=ids(),
        stdin=stdin,
        stdout=out,
        stderr=err,
    )
    return code, out.getvalue(), err.getvalue()


def test_cli_who_prints_rows_and_you_marker(tmp_path):
    mux = FakeMux([pane("w1:p1", "ses_a", title="fix auth"), pane("w2:p2", "ses_b", status="working", harness="claude", title="refactor")])
    code, out, err = run_cli(["who"], tmp_path, mux, env_extra=env("w1:p1", tmp_path))
    assert code == 0
    assert "w1:p1#ses_a" in out and "(you)" in out
    assert "w2:p2#ses_b" in out and "claude" in out and "working" in out


def test_cli_send_and_header(tmp_path):
    mux = FakeMux([pane("w1:p1", "ses_a", title="fix auth"), pane("w2:p2", "ses_b")])
    code, out, err = run_cli(
        ["send", "w2:p2#ses_b", "hello there"], tmp_path, mux, env_extra=env("w1:p1", tmp_path)
    )
    assert code == 0, err
    assert "sent bm_0000000001" in out
    assert mux.typed and mux.typed[0][0] == "w2:p2"


def test_cli_send_reads_body_from_stdin_dash(tmp_path):
    mux = FakeMux([pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b")])
    code, out, err = run_cli(
        ["send", "w2:p2#ses_b", "-"],
        tmp_path,
        mux,
        env_extra=env("w1:p1", tmp_path),
        stdin=io.StringIO("body from stdin\n"),
    )
    assert code == 0, err
    reopened = WalkyService(state_dir=tmp_path, mux=mux, clock=Clock(), id_factory=ids())
    assert reopened.inbox("w2:p2")[0].body == "body from stdin"


def test_cli_refusal_exit_codes_and_stderr(tmp_path):
    mux = FakeMux([pane("w1:p1", "ses_a")])
    code, out, err = run_cli(
        ["send", "w1:p1#ses_a", "to myself"], tmp_path, mux, env_extra=env("w1:p1", tmp_path)
    )
    assert code == 4
    assert "ERROR" in err

    code, out, err = run_cli(["send", "w2:p2#ses_b", "hi"], tmp_path, mux, env_extra=env("w1:p1", tmp_path))
    assert code == 4
    assert "unknown pane" in err.lower()


def test_cli_refuses_when_not_in_a_pane(tmp_path):
    mux = FakeMux([pane("w2:p2", "ses_b")])
    code, out, err = run_cli(["send", "w2:p2#ses_b", "hi"], tmp_path, mux)
    assert code == 4
    assert "not inside a herdr pane" in err.lower()


def test_cli_usage_error_is_exit_2(tmp_path):
    mux = FakeMux([pane("w1:p1", "ses_a")])
    code, out, err = run_cli(["frobnicate"], tmp_path, mux)
    assert code == 2


def test_cli_status_unknown_id_is_exit_3(tmp_path):
    mux = FakeMux([pane("w1:p1", "ses_a")])
    code, out, err = run_cli(["status", "bm_deadbeef00"], tmp_path, mux)
    assert code == 3


def test_cli_inbox_and_status(tmp_path):
    mux = FakeMux([pane("w1:p1", "ses_a", title="fix auth"), pane("w2:p2", "ses_b")])
    code, _, err = run_cli(
        ["send", "w2:p2#ses_b", "read me please"], tmp_path, mux, env_extra=env("w1:p1", tmp_path)
    )
    assert code == 0, err
    code, out, err = run_cli(["inbox"], tmp_path, mux, env_extra=env("w2:p2", tmp_path))
    assert code == 0
    assert "bm_0000000001" in out
    assert "read me please" in out
    code, out, err = run_cli(["status", "bm_0000000001"], tmp_path, mux)
    assert code == 0
    assert "typed" in out
    code, out, err = run_cli(["inbox"], tmp_path, mux, env_extra=env("w2:p2", tmp_path))
    assert "no unread" in out.lower()


def test_cli_reply_and_cancel(tmp_path):
    mux = FakeMux([pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b", status="working")])
    code, _, err = run_cli(
        ["send", "w2:p2#ses_b", "held message"], tmp_path, mux, env_extra=env("w1:p1", tmp_path)
    )
    assert code == 0, err
    code, out, err = run_cli(["cancel", "bm_0000000001"], tmp_path, mux, env_extra=env("w1:p1", tmp_path))
    assert code == 0, err
    assert "canceled" in out
    code, out, err = run_cli(["status", "bm_0000000001"], tmp_path, mux)
    assert "canceled" in out


def test_cli_audit_line_masks_secrets_in_preview(tmp_path):
    mux = FakeMux([pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b")])
    secret = "sk-ant-abcdefghijklmnopqrstuv"
    code, _, err = run_cli(
        ["send", "w2:p2#ses_b", f"the key is {secret} ok"],
        tmp_path,
        mux,
        env_extra=env("w1:p1", tmp_path),
    )
    assert code == 0, err
    raw = (tmp_path / "audit.log").read_text(encoding="utf-8")
    assert secret not in raw
    line = json.loads(raw.strip().splitlines()[-1])
    assert line["action"] == "msg.send"
    assert "sk-ant-" in line["detail"]["preview"]  # masked placeholder survived
    assert "[masked]" in line["detail"]["preview"]
