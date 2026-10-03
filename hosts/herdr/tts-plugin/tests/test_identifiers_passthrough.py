"""Host identifiers + daemon passthrough (voice-stack VS1.6, TECHNICAL-PLAN T3).

Unit tests inject the senders, so no daemon, socket or audio device is
needed. One integration test drives the real engine ``Daemon`` command
handler to prove the identifiers reach the typed enqueue ack and that the
targeted cancel stops exactly that item.
"""

from __future__ import annotations

import json

import pytest

import pending_queue as pq


# -- identifiers ---------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        ("ann-0123456789", True),
        ("a" * 8, True),
        ("a" * 64, True),
        ("A.b_c-d.1234", True),
        ("short", False),  # 5 chars
        ("a" * 7, False),
        ("a" * 65, False),
        ("has space 12345", False),
        ("slash/not-allowed", False),
        ("", False),
        (None, False),
        (12345678, False),
    ],
)
def test_valid_identifier_matrix(value, expected):
    assert pq.valid_identifier(value) is expected


def test_new_announcement_id_shape_and_uniqueness():
    first, second = pq.new_announcement_id(), pq.new_announcement_id()
    assert first != second
    assert first.startswith("ann-") and pq.valid_identifier(first)


# -- command builders ------------------------------------------------------------


def test_build_enqueue_payload_carries_identifiers(tmp_path):
    audio = tmp_path / "turn.mp3"
    payload = pq.build_enqueue_payload(str(audio), ["ann-000000001", "ann-000000001", "apr-00000002"])
    assert payload["file"] == str(audio)  # absolute
    assert payload["label"] == "turn.mp3"
    assert payload["priority"] == "working" and payload["policy"] == "queue"
    assert payload["identifiers"] == ["ann-000000001", "apr-00000002"]  # deduped, ordered
    assert "event_type" not in payload
    with_event = pq.build_enqueue_payload(str(audio), ["ann-000000001"], event_type="jobs finished",
                                          label="custom", priority="done", policy="coalesce")
    assert with_event["event_type"] == "jobs finished" and with_event["label"] == "custom"
    assert with_event["priority"] == "done" and with_event["policy"] == "coalesce"


def test_build_cancel_command_json():
    command = pq.build_cancel_command(["ann-000000001", "ann-000000002"])
    verb, _, body = command.partition(" ")
    assert verb == "cancel"
    assert json.loads(body) == {"identifiers": ["ann-000000001", "ann-000000002"]}
    assert " " not in body.replace('", "', "")  # compact separators


def test_invalid_identifiers_rejected_before_any_send(tmp_path):
    calls = []
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    for bad in ([], ["short"], ["ann-000000001", "bad id 123"]):
        with pytest.raises(pq.IdentifierError):
            pq.enqueue_file(str(audio), bad, enqueue=lambda p: calls.append(p))
        with pytest.raises(pq.IdentifierError):
            pq.cancel(bad, send=lambda c: calls.append(c))
    assert calls == []  # nothing ever reached a daemon


# -- reply parsing ----------------------------------------------------------------


@pytest.mark.parametrize(
    "reply, expected",
    [
        ("ok=true item=3 queue_len=0", {"ok": True, "item": 3, "queue_len": 0}),
        ("ok=true item=4 queue_len=2 coalesced=3", {"ok": True, "item": 4, "queue_len": 2, "coalesced": 3}),
        ("ok=true removed=1 active_stopped=1", {"ok": True, "removed": 1, "active_stopped": 1}),
        ("ok=true removed=0 active_stopped=0 trimmed=2",
         {"ok": True, "removed": 0, "active_stopped": 0, "trimmed": 2}),
        ("ok=false error=daemon shutting down", {"ok": False, "error": "daemon shutting down"}),
        ("ERR: frame too large", {"ok": False, "error": "frame too large"}),
        (None, {"ok": False, "error": "daemon unreachable"}),
        ("garbage", {"ok": False}),
    ],
)
def test_parse_reply_matrix(reply, expected):
    assert pq.parse_reply(reply) == expected


def test_format_reply_is_shell_friendly():
    assert pq.format_reply({"ok": True, "item": 3, "queue_len": 0}) == "ok=true item=3 queue_len=0"
    assert pq.format_reply({"ok": False, "error": "boom x"}) == "ok=false error=boom x"


# -- enqueue / cancel behavior -----------------------------------------------------


def test_identifiers_reach_enqueue_ack(tmp_path):
    audio = tmp_path / "turn.mp3"
    audio.write_bytes(b"\xff\xfb")
    seen = []

    def enqueue(payload):
        seen.append(payload)
        return "ok=true item=7 queue_len=1"

    result = pq.enqueue_file(str(audio), ["ann-000000001"], enqueue=enqueue)

    assert result == {"ok": True, "item": 7, "queue_len": 1}
    assert seen[0]["identifiers"] == ["ann-000000001"]
    assert seen[0]["file"] == str(audio)


def test_enqueue_missing_or_empty_file_never_contacts_daemon(tmp_path):
    calls = []
    missing = pq.enqueue_file(str(tmp_path / "nope.mp3"), ["ann-000000001"], enqueue=calls.append)
    empty = tmp_path / "empty.mp3"
    empty.write_bytes(b"")
    zero = pq.enqueue_file(str(empty), ["ann-000000001"], enqueue=calls.append)
    assert missing["ok"] is False and "missing or empty" in missing["error"]
    assert zero["ok"] is False and "missing or empty" in zero["error"]
    assert calls == []


def test_enqueue_daemon_unreachable_is_a_typed_error(tmp_path):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    assert pq.enqueue_file(str(audio), ["ann-000000001"], enqueue=lambda p: None) == {
        "ok": False, "error": "daemon unreachable"}


def test_cancel_reply_is_parsed():
    sent = []

    def send(command):
        sent.append(command)
        return "ok=true removed=1 active_stopped=1"

    assert pq.cancel(["ann-000000001"], send=send) == {"ok": True, "removed": 1, "active_stopped": 1}
    assert sent == [pq.build_cancel_command(["ann-000000001"])]


def test_cancel_daemon_absent_is_idempotent_silence():
    assert pq.cancel(["ann-000000001"], send=lambda c: None) == {
        "ok": True, "removed": 0, "active_stopped": 0}


# -- CLI ---------------------------------------------------------------------------


def test_cli_new_id(capsys):
    assert pq.main(["new-id"]) == 0
    assert pq.valid_identifier(capsys.readouterr().out.strip())


def test_cli_enqueue_file_ok_and_refused(tmp_path, capsys):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    argv = ["enqueue-file", str(audio), "--id", "ann-000000001"]
    assert pq.main(argv, enqueue=lambda p: "ok=true item=2 queue_len=0") == 0
    assert capsys.readouterr().out.strip() == "ok=true item=2 queue_len=0"
    assert pq.main(argv, enqueue=lambda p: "ok=false error=daemon shutting down") == 1
    assert "error=daemon shutting down" in capsys.readouterr().err


def test_cli_cancel_ok_and_invalid_id(capsys):
    assert pq.main(["cancel", "--id", "ann-000000001"],
                   send=lambda c: "ok=true removed=0 active_stopped=0") == 0
    assert capsys.readouterr().out.strip() == "ok=true removed=0 active_stopped=0"
    assert pq.main(["cancel", "--id", "bad"], send=lambda c: "never") == 2
    assert "invalid identifier" in capsys.readouterr().err
    assert pq.main(["cancel"], send=lambda c: "never") == 2  # no ids at all


def test_cli_engine_unavailable_maps_to_exit_1(tmp_path, capsys):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")

    def boom(_payload):
        raise pq.EngineUnavailable("agent_tts unavailable: test")

    assert pq.main(["enqueue-file", str(audio), "--id", "ann-000000001"], enqueue=boom) == 1
    assert "agent_tts unavailable" in capsys.readouterr().err


# -- integration with the real engine daemon command handler ---------------------


def test_identifiers_reach_real_daemon_enqueue_ack_and_cancel(tmp_path):
    daemon_mod = pytest.importorskip("agent_tts.daemon")
    queue_mod = pytest.importorskip("agent_tts.queue_manager")
    if not hasattr(daemon_mod.Daemon, "_handle_cancel"):
        pytest.skip("engine without targeted cancel (voice-stack VS1.5)")

    class Handle:
        terminate_calls = 0

        def progress_token(self):
            return 0

        def is_paused(self):
            return False

        def terminate(self):
            Handle.terminate_calls += 1

        def wait_stopped(self, timeout):
            return True

    dispatched = []

    def runner(item, on_finished):
        dispatched.append(item.identifiers)
        return Handle()

    daemon = daemon_mod.Daemon(socket_path=str(tmp_path / "unused.sock"))
    daemon.queue_manager.shutdown()
    daemon.queue_manager = queue_mod.QueueManager(runner, supervisor_interval_sec=None)
    audio = tmp_path / "turn.mp3"
    audio.write_bytes(b"\xff\xfb")

    def enqueue(payload):
        return daemon.handle_command("enqueue " + json.dumps(payload))

    try:
        first = pq.enqueue_file(str(audio), ["ann-000000001"], enqueue=enqueue)
        second = pq.enqueue_file(str(audio), ["ann-000000002"], enqueue=enqueue)
        assert first["ok"] and first["item"] == 1 and first["queue_len"] == 0
        assert second["ok"] and second["item"] == 2 and second["queue_len"] == 1
        assert dispatched == [("ann-000000001",)]  # the id rode into the queue item

        result = pq.cancel(["ann-000000001"], send=daemon.handle_command)

        assert result == {"ok": True, "removed": 0, "active_stopped": 1}
        assert Handle.terminate_calls == 1
        assert dispatched == [("ann-000000001",), ("ann-000000002",)]  # foreign item moved up
    finally:
        daemon.queue_manager.shutdown()


# -- engine senders (public surface only), import failure, script entry -------------


def _install_fake_engine(monkeypatch, *, enqueue, send):
    """A stand-in ``tts_engine``/``agent_tts`` pair registered in sys.modules."""
    import sys
    import types

    monkeypatch.setitem(sys.modules, "tts_engine", types.ModuleType("tts_engine"))
    agent = types.ModuleType("agent_tts")
    agent.send_ipc_command = send
    daemon = types.ModuleType("agent_tts.daemon")
    daemon.delegate_enqueue = enqueue
    agent.daemon = daemon
    monkeypatch.setitem(sys.modules, "agent_tts", agent)
    monkeypatch.setitem(sys.modules, "agent_tts.daemon", daemon)


def test_parse_reply_keeps_unknown_and_non_numeric_fields():
    assert pq.parse_reply("ok=true provider=edge item=abc") == {
        "ok": True, "provider": "edge", "item": "abc"}


def test_engine_senders_use_the_public_engine_surface(monkeypatch, tmp_path):
    calls = []
    _install_fake_engine(
        monkeypatch,
        enqueue=lambda payload: calls.append(("enqueue", payload)) or "ok=true item=9 queue_len=0",
        send=lambda command: calls.append(("send", command)) or "ok=true removed=0 active_stopped=0",
    )
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")

    assert pq.enqueue_file(str(audio), ["ann-000000001"])["item"] == 9
    assert pq.cancel(["ann-000000001"]) == {"ok": True, "removed": 0, "active_stopped": 0}

    assert [kind for kind, _ in calls] == ["enqueue", "send"]
    assert calls[0][1]["identifiers"] == ["ann-000000001"]
    assert calls[1][1] == pq.build_cancel_command(["ann-000000001"])


def test_engine_import_failure_is_engine_unavailable(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "tts_engine", None)  # makes the import raise
    with pytest.raises(pq.EngineUnavailable):
        pq._engine_send("cancel {}")
    with pytest.raises(pq.EngineUnavailable):
        pq._engine_enqueue({})


def test_module_runs_as_a_script(monkeypatch, capsys):
    import runpy
    import sys

    monkeypatch.setattr(sys, "argv", ["pending_queue.py", "new-id"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(pq.__file__, run_name="__main__")
    assert exc.value.code == 0
    assert pq.valid_identifier(capsys.readouterr().out.strip())
