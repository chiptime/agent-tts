"""CLI queue flags and queued acknowledgment (AT-08, BLOQUE 1.3, T4).

Covers the ``--priority``/``--policy`` flags (RF-AT-08-1): client-side
label validation before any daemon contact, the enqueue envelope the
CLI puts on the wire, the queued acknowledgment line (D4/US-AT-08-4),
and the human rendering of the pending queue in ``status``
(RF-AT-08-5/RF-AT-08-3 visibility). CLI mapping tests mock the daemon
client functions at the same boundary tests/test_client_delegation.py
uses; wire-level truth (delegate_enqueue speaking the real framing)
rides the in-process daemon harness from tests/test_queue_ipc.py.
"""

import array
import json
import sys
import threading
import time
import types
from unittest import mock

import pytest

import agent_tts.daemon as daemon_mod
import agent_tts.ipc as ipc
from agent_tts import audio
from agent_tts.audio import AudioSession
from agent_tts.daemon import Daemon, ProviderCache, delegate_enqueue


@pytest.fixture
def channel(tmp_path, monkeypatch):
    """Isolates the transient channel files into a tmp dir (ownership-suite pattern)."""
    paths = {
        "lock": str(tmp_path / "playing.lock"),
        "pid": str(tmp_path / "current.pid"),
        "sock": str(tmp_path / "player.sock"),
    }
    monkeypatch.setattr(audio, "LOCK_FILE", paths["lock"])
    monkeypatch.setattr(audio, "PID_FILE", paths["pid"])
    monkeypatch.setattr(audio, "IPC_SOCKET", paths["sock"])
    yield paths
    audio.cleanup_locks()


def _run_cli(monkeypatch, argv):
    from agent_tts import cli

    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as excinfo:
        cli.main()
    return excinfo.value.code


def _delegation_recorders():
    """Mocks both delegation entry points; returns (play_mock, enqueue_mock)."""
    play = mock.Mock(return_value="status=done")
    enqueue = mock.Mock(return_value="ok=true item=1 queue_len=0")
    return play, enqueue


# --- flag validation (client-side, before any daemon contact) ---------------------------


def test_unknown_priority_label_rejected_client_side(monkeypatch, capsys):
    play, enqueue = _delegation_recorders()
    with mock.patch.object(daemon_mod, "delegate_play", play), mock.patch.object(
        daemon_mod, "delegate_enqueue", enqueue
    ):
        code = _run_cli(monkeypatch, ["cli.py", "hola", "--priority", "urgent"])
    captured = capsys.readouterr()
    assert code == 2  # argparse usage error: the daemon is never contacted
    assert "invalid choice" in captured.err
    assert captured.out == ""
    play.assert_not_called()
    enqueue.assert_not_called()


def test_unknown_policy_label_rejected_client_side(monkeypatch, capsys):
    play, enqueue = _delegation_recorders()
    with mock.patch.object(daemon_mod, "delegate_play", play), mock.patch.object(
        daemon_mod, "delegate_enqueue", enqueue
    ):
        code = _run_cli(monkeypatch, ["cli.py", "hola", "--policy", "asap"])
    captured = capsys.readouterr()
    assert code == 2
    assert "invalid choice" in captured.err
    play.assert_not_called()
    enqueue.assert_not_called()


def test_no_play_with_queue_flags_is_a_client_side_error(monkeypatch, capsys):
    play, enqueue = _delegation_recorders()
    with mock.patch.object(daemon_mod, "delegate_play", play), mock.patch.object(
        daemon_mod, "delegate_enqueue", enqueue
    ):
        code = _run_cli(monkeypatch, ["cli.py", "hola", "--no-play", "--priority", "blocked"])
    captured = capsys.readouterr()
    assert code == 2
    assert "--no-play" in captured.err
    play.assert_not_called()
    enqueue.assert_not_called()


# --- envelope mapping (RF-AT-08-1: flags switch play onto enqueue) ----------------------


def test_flags_send_enqueue_envelope_with_requested_labels(monkeypatch, capsys):
    play, enqueue = _delegation_recorders()
    with mock.patch.object(daemon_mod, "delegate_play", play), mock.patch.object(
        daemon_mod, "delegate_enqueue", enqueue
    ):
        code = _run_cli(
            monkeypatch,
            ["cli.py", "hola", "--voice", "alvaro", "--priority", "blocked", "--policy", "coalesce"],
        )
    assert code == 0
    play.assert_not_called()
    envelope = enqueue.call_args.args[0]
    assert envelope["text"] == "hola"
    assert envelope["voice"] == "alvaro"
    assert envelope["priority"] == "blocked"
    assert envelope["policy"] == "coalesce"
    assert capsys.readouterr().out == ""  # queue_len=0: classic silent success


def test_partial_flags_default_to_the_documented_mapping(monkeypatch):
    play, enqueue = _delegation_recorders()
    with mock.patch.object(daemon_mod, "delegate_play", play), mock.patch.object(
        daemon_mod, "delegate_enqueue", enqueue
    ):
        _run_cli(monkeypatch, ["cli.py", "hola", "--priority", "done"])
        envelope = enqueue.call_args.args[0]
        assert envelope["priority"] == "done"
        assert envelope["policy"] == "queue"  # documented default

        _run_cli(monkeypatch, ["cli.py", "hola", "--policy", "preempt"])
        envelope = enqueue.call_args.args[0]
        assert envelope["priority"] == "working"  # documented default
        assert envelope["policy"] == "preempt"


def test_play_file_path_carries_the_flags_too(monkeypatch):
    play, enqueue = _delegation_recorders()
    with mock.patch.object(daemon_mod, "delegate_play", play), mock.patch.object(
        daemon_mod, "delegate_enqueue", enqueue
    ):
        _run_cli(monkeypatch, ["cli.py", "--play-file", "clip.mp3", "--priority", "blocked"])
    play.assert_not_called()
    envelope = enqueue.call_args.args[0]
    assert envelope["file"].endswith("clip.mp3")
    assert envelope["priority"] == "blocked"


def test_no_flags_keep_the_classic_blocking_play(monkeypatch):
    play, enqueue = _delegation_recorders()
    with mock.patch.object(daemon_mod, "delegate_play", play), mock.patch.object(
        daemon_mod, "delegate_enqueue", enqueue
    ):
        _run_cli(monkeypatch, ["cli.py", "hola"])
    play.assert_called_once()
    enqueue.assert_not_called()


def test_enqueue_rejection_goes_to_stderr_nonzero(monkeypatch, capsys):
    enqueue = mock.Mock(return_value="ok=false error=unknown priority label: 'urgent'")
    with mock.patch.object(daemon_mod, "delegate_play", mock.Mock()), mock.patch.object(
        daemon_mod, "delegate_enqueue", enqueue
    ):
        code = _run_cli(monkeypatch, ["cli.py", "hola", "--priority", "blocked"])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert captured.err == "Error: unknown priority label: 'urgent'\n"


# --- queued acknowledgment (D4/US-AT-08-4) ----------------------------------------------


def _status_with_pending(*pending):
    snapshot = {
        "queue_len": len(pending),
        "pending": list(pending),
        "active": None,
        "failed": [],
        "last_error": None,
        "completed_count": 0,
        "failed_count": 0,
        "interrupted_count": 0,
        "wedged_count": 0,
    }
    return "status=playing pos=1.00 total=9.00 provider=edge playback=local " + (
        daemon_mod.encode_queue_fields(snapshot)
    )


def _pending_item(item_id, priority="working", policy="queue", coalesced=1, announcement=""):
    return {
        "id": item_id,
        "priority": priority,
        "policy": policy,
        "event_type": "",
        "identifiers": [],
        "coalesced": coalesced,
        "enqueued_at": 1.0,
        "announcement": announcement,
    }


def test_queued_ack_line_reports_position_from_status(monkeypatch, capsys):
    enqueue = mock.Mock(return_value="ok=true item=5 queue_len=2")
    status = _status_with_pending(_pending_item(5, priority="blocked"), _pending_item(6))
    with mock.patch.object(daemon_mod, "delegate_enqueue", enqueue), mock.patch.object(
        ipc, "send_ipc_command", return_value=status
    ) as status_call:
        code = _run_cli(monkeypatch, ["cli.py", "hola", "--priority", "blocked"])
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == "queued: item=5 position=1 queue_len=2\n"
    assert captured.err == ""
    status_call.assert_called_once_with("status")


def test_queued_ack_line_appends_coalesced_count(monkeypatch, capsys):
    enqueue = mock.Mock(return_value="ok=true item=5 queue_len=2 coalesced=3")
    status = _status_with_pending(_pending_item(5, coalesced=3), _pending_item(6))
    with mock.patch.object(daemon_mod, "delegate_enqueue", enqueue), mock.patch.object(
        ipc, "send_ipc_command", return_value=status
    ):
        code = _run_cli(monkeypatch, ["cli.py", "hola", "--policy", "coalesce"])
    assert code == 0
    assert capsys.readouterr().out == "queued: item=5 position=1 queue_len=2 coalesced=3\n"


def test_queued_ack_falls_back_to_queue_len_without_status(monkeypatch, capsys):
    enqueue = mock.Mock(return_value="ok=true item=5 queue_len=2")
    with mock.patch.object(daemon_mod, "delegate_enqueue", enqueue), mock.patch.object(
        ipc, "send_ipc_command", return_value=None
    ):
        code = _run_cli(monkeypatch, ["cli.py", "hola", "--priority", "done"])
    assert code == 0
    # No snapshot to refine from: queue_len is the worst-case position bound.
    assert capsys.readouterr().out == "queued: item=5 position=2 queue_len=2\n"


def test_immediate_dispatch_keeps_the_silent_success(monkeypatch, capsys):
    enqueue = mock.Mock(return_value="ok=true item=1 queue_len=0")
    with mock.patch.object(daemon_mod, "delegate_enqueue", enqueue), mock.patch.object(
        ipc, "send_ipc_command"
    ) as status_call:
        code = _run_cli(monkeypatch, ["cli.py", "hola", "--priority", "done"])
    assert code == 0
    assert capsys.readouterr().out == ""
    status_call.assert_not_called()  # no position to report


# --- status human rendering (RF-AT-08-5/RF-AT-08-3 visibility) --------------------------


def test_status_human_output_renders_pending_queue(monkeypatch, capsys):
    long_announcement = (
        "3 deploy pipelines finished: build-alpha, build-beta, build-gamma"
    )
    status = _status_with_pending(
        _pending_item(
            4,
            priority="blocked",
            policy="coalesce",
            coalesced=3,
            announcement=long_announcement,
        ),
        _pending_item(7, priority="working", policy="queue"),
    )
    with mock.patch.object(
        daemon_mod, "send_control_command", lambda cmd, socket_path=None: status
    ):
        code = _run_cli(monkeypatch, ["cli.py", "--ipc-cmd", "status"])
    captured = capsys.readouterr()
    assert code == 0
    lines = captured.out.splitlines()
    # The kv line survives (minus the opaque queue= JSON token).
    assert lines[0].startswith("status=playing")
    assert "queue_len=2" in lines[0]
    assert "queue={" not in captured.out
    # Pending items render in dispatch order with priority/policy, the
    # merge count, and the (truncated) announcement.
    assert lines[1] == (
        "  queued[1] priority=blocked policy=coalesce coalesced=3 "
        "text=3 deploy pipelines finished: build-alpha, build-beta, bui..."
    )
    assert lines[2] == "  queued[2] priority=working policy=queue"


def test_status_ipc_json_keeps_the_raw_snapshot(monkeypatch, capsys):
    status = _status_with_pending(_pending_item(4, priority="blocked"))
    with mock.patch.object(
        daemon_mod, "send_control_command", lambda cmd, socket_path=None: status
    ):
        code = _run_cli(monkeypatch, ["cli.py", "--ipc-cmd", "status", "--ipc-json"])
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["status"] == "playing"
    assert payload["queue_len"] == "1"  # one pending item in this fixture
    assert json.loads(payload["queue"])["pending"][0]["priority"] == "blocked"


def test_foreign_status_without_queue_token_prints_raw(monkeypatch, capsys):
    reply = "status=playing owner=classic"
    with mock.patch.object(
        daemon_mod, "send_control_command", lambda cmd, socket_path=None: reply
    ):
        code = _run_cli(monkeypatch, ["cli.py", "--ipc-cmd", "status"])
    assert code == 0
    assert capsys.readouterr().out == reply + "\n"


# --- wire-level truth: delegate_enqueue against a real daemon ---------------------------


class _StubEngine:
    async def synthesize(self, text, voice=None, **kwargs):
        return b"stub"


def _wait_until(predicate, timeout_sec: float = 5.0, message: str = "condition never held"):
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(message)


def test_delegate_enqueue_hits_the_real_wire(channel, monkeypatch):
    """The flags' transport: one enqueue frame over framing v2, ack parsed."""
    import agent_tts.cli as cli_module

    class FakeDecoded:
        sample_rate = 24000
        nchannels = 1
        duration = 0.01
        samples = array.array("h", [0] * 240)

    monkeypatch.setattr(
        cli_module, "miniaudio", types.SimpleNamespace(decode=lambda data: FakeDecoded())
    )
    events = []

    def blocking_play(self, decoded):
        events.append("start")
        self.state["status"] = "playing"
        deadline = time.monotonic() + 5.0
        while not self.state["stop"] and time.monotonic() < deadline:
            time.sleep(0.02)
        events.append("end")
        self.state["status"] = "stopped"

    monkeypatch.setattr(AudioSession, "play", blocking_play)
    d = Daemon(
        socket_path=channel["sock"],
        provider_cache=ProviderCache(factory=lambda **kw: _StubEngine()),
    )
    thread = threading.Thread(target=d.run, daemon=True)
    thread.start()
    try:
        _wait_until(
            lambda: (reply := ipc.send_ipc_command("ping", socket_path=channel["sock"]))
            and reply.startswith("pong"),
            message="daemon never answered ping",
        )
        reply = delegate_enqueue(
            {"text": "from the cli", "priority": "blocked", "policy": "queue"},
            socket_path=channel["sock"],
        )
        assert reply is not None and reply.startswith("ok=true item="), reply
        assert "queue_len=0" in reply  # idle manager dispatched immediately
        _wait_until(lambda: "start" in events)
        ipc.send_ipc_command("stop", socket_path=channel["sock"])
        _wait_until(lambda: "end" in events)
    finally:
        d.request_shutdown()
        thread.join(timeout=5.0)
        audio.cleanup_locks()
