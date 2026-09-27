"""A3 client error discipline (AT-08, BLOQUE 1.3, T4) — frozen client contract.

Any ``ok=false error=...`` reply (and the transport-level ``ERR:``
prefix) the CLI receives prints its message to STDERR and exits 1;
success payloads keep printing to stdout with exit 0, so hosts
parsing stdout never see error text. Covers the control-command path
(``--ipc-cmd`` and the sentence shortcuts) and the delegated speak
path's lost-connection shape; the daemon client functions are mocked
at the same boundary tests/test_client_delegation.py uses.
"""

import json
import sys
from unittest import mock

import pytest

import agent_tts.daemon as daemon_mod


def _run_cli(monkeypatch, argv):
    from agent_tts import cli

    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as excinfo:
        cli.main()
    return excinfo.value.code


def _control_reply(value):
    return lambda cmd, socket_path=None: value


# --- control commands: typed errors leave stdout clean ----------------------------------


def test_idle_control_command_error_goes_to_stderr_nonzero(monkeypatch, capsys):
    with mock.patch.object(
        daemon_mod,
        "send_control_command",
        _control_reply("ok=false error=no active playback session"),
    ):
        code = _run_cli(monkeypatch, ["cli.py", "--ipc-cmd", "pause"])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert captured.err == "Error: no active playback session\n"


def test_sentence_shortcut_shares_the_error_discipline(monkeypatch, capsys):
    with mock.patch.object(
        daemon_mod,
        "send_control_command",
        _control_reply("ok=false error=no active playback session"),
    ):
        code = _run_cli(monkeypatch, ["cli.py", "--next-sentence"])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert "no active playback session" in captured.err


def test_idle_control_command_error_json_mode_emits_json_on_stderr(monkeypatch, capsys):
    with mock.patch.object(
        daemon_mod,
        "send_control_command",
        _control_reply("ok=false error=no active playback session"),
    ):
        code = _run_cli(monkeypatch, ["cli.py", "--ipc-cmd", "pause", "--ipc-json"])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert json.loads(captured.err) == {"ok": "false", "error": "no active playback session"}


def test_transport_err_reply_maps_to_stderr_nonzero(monkeypatch, capsys):
    err_reply = "ERR: command too large (payload of 20000000 bytes exceeds the cap)"
    with mock.patch.object(daemon_mod, "send_control_command", _control_reply(err_reply)):
        code = _run_cli(monkeypatch, ["cli.py", "--ipc-cmd", "seek +10"])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert "command too large" in captured.err


def test_success_payload_stays_on_stdout_exit_zero(monkeypatch, capsys):
    with mock.patch.object(
        daemon_mod,
        "send_control_command",
        _control_reply("ok=true shutting_down=true"),
    ):
        code = _run_cli(monkeypatch, ["cli.py", "--ipc-cmd", "shutdown"])
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == "ok=true shutting_down=true\n"
    assert captured.err == ""


def test_classic_payload_stays_on_stdout_exit_zero(monkeypatch, capsys):
    with mock.patch.object(
        daemon_mod,
        "send_control_command",
        _control_reply("pong version=0.3.0 uptime=42"),
    ):
        code = _run_cli(monkeypatch, ["cli.py", "--ipc-cmd", "ping"])
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == "pong version=0.3.0 uptime=42\n"


# --- delegated speak path ----------------------------------------------------------------


def test_delegate_lost_connection_is_a_clear_stderr_error(monkeypatch, capsys):
    with mock.patch.object(daemon_mod, "delegate_play", return_value=None):
        code = _run_cli(monkeypatch, ["cli.py", "hola"])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert "daemon closed the connection" in captured.err
