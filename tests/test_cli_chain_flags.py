"""CLI --play-chain/--chain-gap flags (AT-08, BLOQUE 1.3, T6).

Covers the client side of the chain milestone (RF-AT-08-4/US-AT-08-3):
the chain payload the CLI puts on the wire (absolute paths, the gap in
ms), composition with the queue flags (--priority/--policy switch the
chain onto the fire-and-forget enqueue command exactly like speak and
--play-file), the queued acknowledgment for chains, and the error
discipline — a missing file fails on stderr with a non-zero exit BEFORE
the daemon is contacted.
"""

import sys
from unittest import mock

import pytest

import agent_tts.daemon as daemon_mod
import agent_tts.ipc as ipc


def _run_cli(monkeypatch, argv):
    from agent_tts import cli

    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as excinfo:
        cli.main()
    return excinfo.value.code


def _delegation_recorders():
    play = mock.Mock(return_value="status=done")
    enqueue = mock.Mock(return_value="ok=true item=1 queue_len=0")
    return play, enqueue


# --- happy paths ------------------------------------------------------------------------


def test_play_chain_sends_chain_payload_blocking(monkeypatch, tmp_path):
    play, enqueue = _delegation_recorders()
    a, b = str(tmp_path / "a.wav"), str(tmp_path / "b.wav")
    open(a, "wb").close()
    open(b, "wb").close()
    with mock.patch.object(daemon_mod, "delegate_play", play), mock.patch.object(
        daemon_mod, "delegate_enqueue", enqueue
    ):
        code = _run_cli(monkeypatch, ["cli.py", "--play-chain", a, b])
    assert code == 0
    play.assert_called_once()
    enqueue.assert_not_called()
    payload = play.call_args.args[0]
    assert payload["chain"] == [a, b]  # absolute, in order
    assert payload["chain_gap_ms"] == 0  # default: no inserted gap
    assert "a.wav" in payload["label"]


def test_chain_gap_flag_maps_to_chain_gap_ms(monkeypatch, tmp_path):
    play, _ = _delegation_recorders()
    a = str(tmp_path / "a.wav")
    open(a, "wb").close()
    with mock.patch.object(daemon_mod, "delegate_play", play):
        _run_cli(monkeypatch, ["cli.py", "--play-chain", a, "--chain-gap", "250"])
    assert play.call_args.args[0]["chain_gap_ms"] == 250


def test_play_chain_composes_with_priority_policy_flags(monkeypatch, tmp_path):
    play, enqueue = _delegation_recorders()
    a, b = str(tmp_path / "a.wav"), str(tmp_path / "b.wav")
    open(a, "wb").close()
    open(b, "wb").close()
    with mock.patch.object(daemon_mod, "delegate_play", play), mock.patch.object(
        daemon_mod, "delegate_enqueue", enqueue
    ):
        code = _run_cli(
            monkeypatch,
            ["cli.py", "--play-chain", a, b, "--priority", "blocked", "--policy", "preempt"],
        )
    assert code == 0
    play.assert_not_called()
    envelope = enqueue.call_args.args[0]
    assert envelope["chain"] == [a, b]
    assert envelope["priority"] == "blocked"
    assert envelope["policy"] == "preempt"


def test_queued_ack_line_works_for_chains(monkeypatch, capsys, tmp_path):
    enqueue = mock.Mock(return_value="ok=true item=5 queue_len=2")
    a = str(tmp_path / "a.wav")
    open(a, "wb").close()
    status = (
        "status=playing pos=1.00 total=9.00 provider=edge playback=local "
        + daemon_mod.encode_queue_fields(
            {
                "queue_len": 2,
                "pending": [
                    {"id": 5, "priority": "working", "policy": "queue", "event_type": "",
                     "identifiers": [], "coalesced": 1, "enqueued_at": 1.0, "announcement": ""},
                    {"id": 6, "priority": "working", "policy": "queue", "event_type": "",
                     "identifiers": [], "coalesced": 1, "enqueued_at": 1.1, "announcement": ""},
                ],
                "active": None, "failed": [], "last_error": None,
                "completed_count": 0, "failed_count": 0, "interrupted_count": 0, "wedged_count": 0,
            }
        )
    )
    with mock.patch.object(daemon_mod, "delegate_enqueue", enqueue), mock.patch.object(
        ipc, "send_ipc_command", return_value=status
    ):
        code = _run_cli(monkeypatch, ["cli.py", "--play-chain", a, "--priority", "done"])
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == "queued: item=5 position=1 queue_len=2\n"


# --- client-side error discipline (before any daemon contact) ----------------------------


def test_missing_chain_file_fails_client_side_before_daemon(monkeypatch, capsys, tmp_path):
    play, enqueue = _delegation_recorders()
    a = str(tmp_path / "a.wav")
    open(a, "wb").close()
    gone = str(tmp_path / "gone.wav")
    with mock.patch.object(daemon_mod, "delegate_play", play), mock.patch.object(
        daemon_mod, "delegate_enqueue", enqueue
    ):
        code = _run_cli(monkeypatch, ["cli.py", "--play-chain", a, gone])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert "gone.wav" in captured.err
    play.assert_not_called()
    enqueue.assert_not_called()


def test_play_chain_rejected_with_play_file(monkeypatch, capsys, tmp_path):
    play, enqueue = _delegation_recorders()
    a = str(tmp_path / "a.wav")
    open(a, "wb").close()
    with mock.patch.object(daemon_mod, "delegate_play", play), mock.patch.object(
        daemon_mod, "delegate_enqueue", enqueue
    ):
        code = _run_cli(monkeypatch, ["cli.py", "--play-chain", a, "--play-file", a])
    captured = capsys.readouterr()
    assert code == 2
    play.assert_not_called()


def test_negative_chain_gap_rejected_client_side(monkeypatch, capsys, tmp_path):
    play, enqueue = _delegation_recorders()
    a = str(tmp_path / "a.wav")
    open(a, "wb").close()
    with mock.patch.object(daemon_mod, "delegate_play", play), mock.patch.object(
        daemon_mod, "delegate_enqueue", enqueue
    ):
        code = _run_cli(monkeypatch, ["cli.py", "--play-chain", a, "--chain-gap", "-10"])
    captured = capsys.readouterr()
    assert code == 2
    assert "--chain-gap" in captured.err
    play.assert_not_called()


def test_chain_gap_without_play_chain_rejected(monkeypatch, capsys):
    play, enqueue = _delegation_recorders()
    with mock.patch.object(daemon_mod, "delegate_play", play), mock.patch.object(
        daemon_mod, "delegate_enqueue", enqueue
    ):
        code = _run_cli(monkeypatch, ["cli.py", "hola", "--chain-gap", "100"])
    captured = capsys.readouterr()
    assert code == 2
    play.assert_not_called()
