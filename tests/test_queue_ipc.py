"""IPC surface for the playback queue (AT-08, BLOQUE 1.3, T3).

Exercised end-to-end over the real in-process daemon and the real IPC
channel: the ``enqueue`` command (priority/policy labels), queue-aware
``status`` (RF-AT-08-5), typed error replies (A3), failure visibility
(A5), the real provider/voice on in-process speak (A3''), the
coalesce-window configuration seam, and the dispatch-after-finalize
no-overlap invariant (RF-AT-08-6). Playback and synthesis are faked at
the same seams as tests/test_daemon.py (AudioSession.play and a stub
engine factory), so no audio device or network provider is involved.
"""

import array
import asyncio
import json
import threading
import time
import types
from unittest import mock

import pytest

import agent_tts.cli as cli_mod
import agent_tts.ipc as ipc
from agent_tts import audio
from agent_tts.audio import AudioSession
from agent_tts.daemon import Daemon, ProviderCache


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


class StubEngine:
    """Minimal provider stand-in: records calls, returns non-empty audio."""

    def __init__(self):
        self.calls = []

    async def synthesize(self, text, voice=None, rate=None, volume=None, pitch=None, stop_checker=None):
        self.calls.append({"text": text, "voice": voice})
        return b"stub-audio"


def _blocking_play(events):
    """AudioSession.play stand-in: records start/end, blocks until stop."""

    def play(self, decoded):
        events.append(("start", self.label, time.monotonic()))
        self.state["status"] = "playing"
        deadline = time.monotonic() + 5.0
        while not self.state["stop"] and time.monotonic() < deadline:
            time.sleep(0.02)
        events.append(("end", self.label, time.monotonic()))
        self.state["status"] = "stopped"

    return play


def _start_daemon(channel, monkeypatch, events, **daemon_kwargs) -> Daemon:
    """Builds and runs an in-process daemon with stubbed synthesis/playback."""
    import agent_tts.cli as cli_module

    class FakeDecoded:
        sample_rate = 24000
        nchannels = 1
        duration = 0.01
        samples = array.array("h", [0] * 240)

    monkeypatch.setattr(cli_module, "miniaudio", types.SimpleNamespace(decode=lambda data: FakeDecoded()))

    stub_engine = StubEngine()
    cache = ProviderCache(factory=lambda **kwargs: stub_engine)
    monkeypatch.setattr(AudioSession, "play", _blocking_play(events))
    d = Daemon(socket_path=channel["sock"], provider_cache=cache, **daemon_kwargs)
    thread = threading.Thread(target=d.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5.0
    reply = None
    while time.monotonic() < deadline:
        reply = ipc.send_ipc_command("ping", socket_path=channel["sock"])
        if reply and reply.startswith("pong"):
            break
        time.sleep(0.02)
    assert reply and reply.startswith("pong"), f"daemon never answered ping: {reply!r}"
    d._test_thread = thread
    d._test_engine = stub_engine
    return d


def _stop_daemon(d: Daemon) -> None:
    d.request_shutdown()
    thread = getattr(d, "_test_thread", None)
    if thread is not None:
        thread.join(timeout=5.0)


def _wait_until(predicate, timeout_sec: float = 5.0, message: str = "condition never held") -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(message)


def _queue_payload(reply: str) -> dict:
    """Extracts and decodes the ``queue=`` JSON field from a status reply."""
    token = next(t for t in reply.split() if t.startswith("queue={"))
    # Whitespace inside the compact JSON is encoded as \\uXXXX escapes, so
    # json.loads restores the original strings (spaces included).
    return json.loads(token[len("queue="):])


def _enqueue(sock, payload: dict) -> str:
    return ipc.send_ipc_command("enqueue " + json.dumps(payload), socket_path=sock)


def _play_async_sock(sock, payload: dict):
    """Sends one blocking delegated play on a thread; returns (reply_box, thread)."""
    from agent_tts.daemon import send_play

    box = []

    def run():
        box.append(send_play(payload, socket_path=sock))

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return box, t


# --- enqueue (RF-AT-08-1/RF-AT-08-2, US-AT-08-4) ---------------------------------------


def test_enqueue_happy_path_replies_item_id_and_queue_len(channel, monkeypatch):
    events = []
    d = _start_daemon(channel, monkeypatch, events)
    try:
        reply = _enqueue(channel["sock"], {"text": "hola", "label": "E1"})
        assert reply.startswith("ok=true item="), reply
        assert int(reply.split("item=")[1].split()[0]) >= 1
        assert "queue_len=0" in reply  # idle manager dispatched immediately

        _wait_until(lambda: any(e[0] == "start" and e[1] == "E1" for e in events))
        ipc.send_ipc_command("stop", socket_path=channel["sock"])
        _wait_until(lambda: any(e[0] == "end" and e[1] == "E1" for e in events))
    finally:
        _stop_daemon(d)


def test_enqueue_unknown_labels_are_typed_errors(channel, monkeypatch):
    events = []
    d = _start_daemon(channel, monkeypatch, events)
    try:
        sock = channel["sock"]
        bad_priority = _enqueue(sock, {"text": "x", "priority": "urgent"})
        assert bad_priority.startswith("ok=false error="), bad_priority
        assert "unknown priority label" in bad_priority

        bad_policy = _enqueue(sock, {"text": "x", "policy": "bogus"})
        assert bad_policy.startswith("ok=false error="), bad_policy
        assert "unknown policy" in bad_policy

        # Nothing entered the queue through the rejected requests.
        status = ipc.send_ipc_command("status", socket_path=sock)
        assert "queue_len=0" in status
    finally:
        _stop_daemon(d)


def test_enqueue_rejects_no_play_payloads(channel, monkeypatch):
    events = []
    d = _start_daemon(channel, monkeypatch, events)
    try:
        reply = _enqueue(channel["sock"], {"text": "x", "no_play": True})
        assert reply.startswith("ok=false error="), reply
        assert "no_play" in reply
    finally:
        _stop_daemon(d)


# --- status queue fields (RF-AT-08-5) ---------------------------------------------------


def test_status_exposes_pending_priorities_in_dispatch_order(channel, monkeypatch):
    events = []
    d = _start_daemon(channel, monkeypatch, events)
    try:
        sock = channel["sock"]
        box, play_thread = _play_async_sock(sock, {"text": "A", "label": "A"})
        _wait_until(lambda: any(e[0] == "start" and e[1] == "A" for e in events))

        id_done = _enqueue(sock, {"text": "B", "label": "B", "priority": "done"})
        id_blocked = _enqueue(sock, {"text": "C", "label": "C", "priority": "blocked"})
        id_working = _enqueue(sock, {"text": "D", "label": "D", "priority": "working"})

        status = ipc.send_ipc_command("status", socket_path=sock)
        assert "queue_len=3" in status, status
        queue = _queue_payload(status)
        # Dispatch order: blocked > done > working (RF-AT-08-1 ordering).
        assert [p["priority"] for p in queue["pending"]] == ["blocked", "done", "working"]
        assert [p["id"] for p in queue["pending"]] == [
            int(id_blocked.split("item=")[1].split()[0]),
            int(id_done.split("item=")[1].split()[0]),
            int(id_working.split("item=")[1].split()[0]),
        ]
        # The active item (a plain play) is working/queue per D4.
        assert queue["active"]["priority"] == "working"
        assert queue["active"]["policy"] == "queue"

        ipc.send_ipc_command("stop", socket_path=sock)
        _wait_until(lambda: any(e[0] == "end" and e[1] == "A" for e in events))
        # The blocked item dispatches next: C reaches the speaker before B/D.
        _wait_until(lambda: any(e[0] == "start" and e[1] == "C" for e in events))
        ipc.send_ipc_command("stop", socket_path=sock)
        _wait_until(lambda: any(e[0] == "end" and e[1] == "C" for e in events))
        _wait_until(lambda: any(e[0] == "start" and e[1] == "B" for e in events))
        ipc.send_ipc_command("stop", socket_path=sock)
        _wait_until(lambda: any(e[0] == "end" and e[1] == "B" for e in events))
        _wait_until(lambda: any(e[0] == "start" and e[1] == "D" for e in events))
        ipc.send_ipc_command("stop", socket_path=sock)
        _wait_until(lambda: any(e[0] == "end" and e[1] == "D" for e in events))
        play_thread.join(timeout=5.0)
        assert not play_thread.is_alive()
        assert box == ["status=stopped"]
    finally:
        _stop_daemon(d)


def test_status_shows_coalesced_merge_before_playing(channel, monkeypatch):
    events = []
    d = _start_daemon(channel, monkeypatch, events)
    try:
        sock = channel["sock"]
        box, play_thread = _play_async_sock(sock, {"text": "A", "label": "A"})
        _wait_until(lambda: any(e[0] == "start" and e[1] == "A" for e in events))

        first = _enqueue(
            sock,
            {
                "text": "job build-a finished",
                "label": "J1",
                "priority": "done",
                "policy": "coalesce",
                "event_type": "jobs finished",
                "identifiers": ["build-a"],
            },
        )
        second = _enqueue(
            sock,
            {
                "text": "job build-b finished",
                "label": "J2",
                "priority": "done",
                "policy": "coalesce",
                "event_type": "jobs finished",
                "identifiers": ["build-b"],
            },
        )
        # The second event merged into the window owner: same item id.
        assert first.split("item=")[1].split()[0] == second.split("item=")[1].split()[0]
        assert "coalesced=2" in second, second

        status = ipc.send_ipc_command("status", socket_path=sock)
        assert "queue_len=1" in status, status
        queue = _queue_payload(status)
        pending = queue["pending"][0]
        assert pending["coalesced"] == 2
        assert pending["event_type"] == "jobs finished"
        assert pending["identifiers"] == ["build-a", "build-b"]
        assert pending["announcement"] == "2 jobs finished: build-a, build-b"

        ipc.send_ipc_command("stop", socket_path=sock)
        _wait_until(lambda: any(e[0] == "start" and e[1] == "J1" for e in events))
        # The merged announcement speaks the summary, not the owner payload.
        assert d._test_engine.calls[-1]["text"] == "2 jobs finished: build-a, build-b"
        ipc.send_ipc_command("stop", socket_path=sock)
        _wait_until(lambda: any(e[0] == "end" and e[1] == "J1" for e in events))
        play_thread.join(timeout=5.0)
    finally:
        _stop_daemon(d)


def test_idle_status_carries_queue_fields(channel, monkeypatch):
    events = []
    d = _start_daemon(channel, monkeypatch, events)
    try:
        status = ipc.send_ipc_command("status", socket_path=channel["sock"])
        assert status.startswith("status=idle ")
        queue = _queue_payload(status)
        assert queue["queue_len"] == 0
        assert queue["pending"] == []
        assert queue["active"] is None
        assert queue["last_error"] is None
    finally:
        _stop_daemon(d)


# --- typed errors (A3 daemon side) -------------------------------------------------------


def test_idle_control_commands_reply_typed_error(channel, monkeypatch):
    events = []
    d = _start_daemon(channel, monkeypatch, events)
    try:
        sock = channel["sock"]
        for command in ("pause", "seek +10", "current-sentence", "scroll-info"):
            reply = ipc.send_ipc_command(command, socket_path=sock)
            assert reply == "ok=false error=no active playback session", (command, reply)
        # stop stays idempotent silence: the user's goal already holds.
        assert ipc.send_ipc_command("stop", socket_path=sock) == "status=stopped"
    finally:
        _stop_daemon(d)


# --- failure visibility (A5) --------------------------------------------------------------


def test_failed_playback_surfaces_last_error_in_status(channel, monkeypatch):
    events = []
    d = _start_daemon(channel, monkeypatch, events)
    try:
        sock = channel["sock"]

        async def failing_speech(session, text, **kwargs):
            raise RuntimeError("synthesis exploded")

        with mock.patch.object(d._cli, "_play_speech", side_effect=failing_speech):
            box, play_thread = _play_async_sock(sock, {"text": "boom"})
            play_thread.join(timeout=5.0)
            assert box == ["ok=false error=synthesis exploded"], box

        status = ipc.send_ipc_command("status", socket_path=sock)
        queue = _queue_payload(status)
        assert queue["last_error"] == "synthesis exploded"
        assert queue["failed_count"] == 1
        assert queue["failed"][0]["error"] == "synthesis exploded"
        # The failed session did not stay mounted: the daemon is idle again.
        assert status.startswith("status=idle ")
    finally:
        _stop_daemon(d)


# --- in-process speak reports the real engine (A3'') --------------------------------------


def test_in_process_speak_reports_real_provider_and_voice(monkeypatch):
    captured = {}

    async def fake_play_speech(session, text, **kwargs):
        captured["session"] = session

    with mock.patch.object(cli_mod, "_play_speech", fake_play_speech), mock.patch.object(
        cli_mod, "_write_player_locks", lambda: None
    ), mock.patch.object(AudioSession, "start_ipc", lambda self: None):
        asyncio.run(
            cli_mod.speak("hola", provider="piper", voice="es-ES-XimenaNeural")
        )
    # The session's status metadata mirrors what synthesis actually used,
    # not the daemon-less defaults (A3'').
    assert captured["session"].provider == "piper"
    assert captured["session"].voice == "es-ES-XimenaNeural"


# --- enqueue configuration seam ------------------------------------------------------------


def test_coalesce_window_is_configurable(channel, monkeypatch):
    """A zero window disables merging; the env var reaches the manager."""
    events = []
    sock = channel["sock"]
    d = _start_daemon(channel, monkeypatch, events, coalesce_window_sec=0.0)
    try:
        box, play_thread = _play_async_sock(sock, {"text": "A", "label": "A"})
        _wait_until(lambda: any(e[0] == "start" and e[1] == "A" for e in events))
        _enqueue(
            sock,
            {"text": "one", "label": "J1", "policy": "coalesce", "event_type": "ev", "priority": "done"},
        )
        _enqueue(
            sock,
            {"text": "two", "label": "J2", "policy": "coalesce", "event_type": "ev", "priority": "done"},
        )
        status = ipc.send_ipc_command("status", socket_path=sock)
        assert "queue_len=2" in status, status  # window 0: no merge
        ipc.send_ipc_command("stop", socket_path=sock)
        _wait_until(lambda: any(e[0] == "end" and e[1] == "A" for e in events))
        play_thread.join(timeout=5.0)
    finally:
        _stop_daemon(d)
        monkeypatch.setattr(AudioSession, "play", _blocking_play(events))

    monkeypatch.setenv("AGENT_TTS_COALESCE_WINDOW", "0")
    monkeypatch.setenv("AGENT_TTS_WEDGED_TIMEOUT", "60")
    from agent_tts.constants import ENV_COALESCE_WINDOW, ENV_WEDGED_TIMEOUT  # noqa: F401

    by_env = Daemon(socket_path="unused.sock")
    assert by_env.queue_manager._coalesce_window_sec == 0.0
    assert by_env.queue_manager._wedged_timeout_sec == 60.0


# --- dispatch after finalize, no overlap (RF-AT-08-6) --------------------------------------


def test_enqueue_while_playing_dispatches_after_finalize_without_overlap(channel, monkeypatch):
    events = []
    sock = channel["sock"]
    d = _start_daemon(channel, monkeypatch, events)
    try:
        box, play_thread = _play_async_sock(sock, {"text": "A", "label": "A"})
        _wait_until(lambda: any(e[0] == "start" and e[1] == "A" for e in events))

        reply = _enqueue(sock, {"text": "B", "label": "B"})
        assert reply.startswith("ok=true item="), reply
        assert "queue_len=1" in reply  # B waits behind the active A

        ipc.send_ipc_command("stop", socket_path=sock)
        _wait_until(lambda: any(e[0] == "end" and e[1] == "A" for e in events))
        _wait_until(lambda: any(e[0] == "start" and e[1] == "B" for e in events))
        ipc.send_ipc_command("stop", socket_path=sock)
        _wait_until(lambda: any(e[0] == "end" and e[1] == "B" for e in events))
        play_thread.join(timeout=5.0)

        starts = {e[1]: e[2] for e in events if e[0] == "start"}
        ends = {e[1]: e[2] for e in events if e[0] == "end"}
        # RF-AT-08-6: B's playback begins only after A's ended.
        assert starts["B"] >= ends["A"]
        assert box == ["status=stopped"]
    finally:
        _stop_daemon(d)


# --- reserved internal keys cannot cross the enqueue wire (R1-05) -------------------------


def test_enqueue_waiter_key_cannot_hijack_a_blocking_play(channel, monkeypatch):
    """A client-supplied ``_waiter`` token must never release someone
    else's blocking play.

    The play handler registers waiters under sequential integer tokens
    (itertools.count), so the next token is guessable. Before R1-05 the
    enqueue path forwarded the key verbatim: an enqueued item carrying
    the predicted token popped the VICTIM's waiter at dispatch and
    released it with the attacker's outcome. The discriminator is the
    reply string itself: the attacker COMPLETES naturally (its outcome
    is status=done) while the victim is STOPPED mid-playback (its own
    outcome is status=stopped) — a hijacked victim would answer
    status=done before its audio ever started.
    """
    events = []
    d = _start_daemon(channel, monkeypatch, events)
    try:
        sock = channel["sock"]

        def decoded_for(data):
            try:
                text = bytes(data).decode("utf-8", "ignore")
            except Exception:
                text = ""
            duration = 10.0 if text.startswith("HOLD") else 0.15
            return types.SimpleNamespace(
                sample_rate=24000,
                nchannels=1,
                sample_width=2,
                duration=duration,
                samples=array.array("h", [0] * int(24000 * min(duration, 0.05))),
            )

        def timed_play(self, decoded):
            events.append(("start", self.label, time.monotonic()))
            self.state["status"] = "playing"
            deadline = time.monotonic() + max(float(getattr(decoded, "duration", 0.0) or 0.0), 0.01)
            while time.monotonic() < deadline and not self.state["stop"]:
                time.sleep(0.005)
            interrupted = bool(self.state["stop"])
            events.append(("end", self.label, time.monotonic(), "interrupted" if interrupted else "completed"))
            self.state["status"] = "stopped"

        monkeypatch.setattr(cli_mod, "miniaudio", types.SimpleNamespace(decode=decoded_for))
        monkeypatch.setattr(AudioSession, "play", timed_play)

        # Holder: a blocking play that owns the speaker (waiter token 1).
        box_holder, holder_thread = _play_async_sock(sock, {"text": "HOLD holder", "label": "HOLD"})
        _wait_until(lambda: any(e[0] == "start" and e[1] == "HOLD" for e in events))

        # Attacker: an enqueue carrying the NEXT waiter token (2 —
        # guessable sequential ids). It queues behind the holder and
        # COMPLETES naturally when it plays.
        atk = _enqueue(sock, {"text": "attack", "label": "ATK", "_waiter": 2})
        assert atk.startswith("ok=true"), atk
        # Victim: the next blocking play — its waiter registers as token 2.
        box_victim, victim_thread = _play_async_sock(sock, {"text": "HOLD victim", "label": "VICTIM"})
        _wait_until(lambda: "queue_len=2" in (ipc.send_ipc_command("status", socket_path=sock) or ""))

        # Release the holder: ATK dispatches next (working FIFO), plays
        # out its 0.15 s, and COMPLETES on its own.
        ipc.send_ipc_command("stop", socket_path=sock)
        _wait_until(lambda: any(e[0] == "end" and e[1] == "HOLD" for e in events))
        _wait_until(lambda: any(e[0] == "start" and e[1] == "ATK" for e in events))
        _wait_until(lambda: any(e[0] == "end" and e[1] == "ATK" for e in events))

        # The victim plays next; a stop cuts IT mid-playback, so its own
        # outcome — the only one allowed to answer its client — is
        # status=stopped. A hijacked waiter would have answered
        # status=done (ATK's outcome) before VICTIM ever started.
        _wait_until(lambda: any(e[0] == "start" and e[1] == "VICTIM" for e in events))
        ipc.send_ipc_command("stop", socket_path=sock)
        _wait_until(lambda: any(e[0] == "end" and e[1] == "VICTIM" for e in events))
        victim_thread.join(timeout=5.0)
        assert box_victim == ["status=stopped"], box_victim
        holder_thread.join(timeout=5.0)
        assert box_holder == ["status=stopped"]
    finally:
        _stop_daemon(d)


# --- watchdog wedge releases the blocking play (R1-02, RS-5 end to end) --------------------


def test_watchdog_wedge_releases_blocking_play_waiter_and_restores_idle(channel, monkeypatch):
    """A playback that ignores terminate() must not park its blocking-play
    client forever nor pin the daemon's idle exit.

    Before R1-02 the watchdog finalized the wedged item and moved the
    queue on, but the daemon-side waiter was only ever released by the
    wedged worker's finally — which never runs — so the client hung on
    ``waiter.event.wait()`` (no timeout) and ``_inflight`` stayed pinned
    (``_idle_expired()`` could never fire again).
    """
    events = []
    unwedge = threading.Event()
    d = _start_daemon(channel, monkeypatch, events, wedged_timeout_sec=0.3)
    try:
        sock = channel["sock"]

        def wedged_play(self, decoded):
            events.append(("start", self.label, time.monotonic()))
            self.state["status"] = "playing"
            unwedge.wait(timeout=30.0)  # ignores the stop flag entirely: a true wedge
            events.append(("unwedge", self.label, time.monotonic()))

        monkeypatch.setattr(AudioSession, "play", wedged_play)

        box, play_thread = _play_async_sock(sock, {"text": "wedged announcement", "label": "WEDGE"})
        _wait_until(lambda: any(e[0] == "start" and e[1] == "WEDGE" for e in events))

        # The bug's snapshot: the client is parked, the daemon stays busy.
        assert d.active_session is not None
        assert d._inflight == 1

        time.sleep(0.35)  # real clock: past the 0.3 s wedged budget
        assert d.queue_manager.check_watchdog() is True

        # The blocked play CLIENT returned with the watchdog's failure.
        play_thread.join(timeout=5.0)
        assert not play_thread.is_alive()
        assert box and box[0].startswith("ok=false error=watchdog: no playback progress"), box

        # The handler's finally ran: _inflight back to 0, mount cleaned —
        # exactly the two busy terms _idle_expired() needs gone.
        assert d._inflight == 0
        assert d.active_session is None
    finally:
        unwedge.set()  # let the wedged worker unwind for teardown hygiene
        _stop_daemon(d)

    # _idle_expired() can fire again: with both busy terms cleared, only
    # the idle clock gates it (proved after the run thread joined, so
    # the daemon's own poll cannot race this assertion).
    d.idle_timeout_sec = 60.0
    d._last_request = time.time() - 120.0
    assert d._idle_expired() is True


# --- watchdog verdict outranks a stop-responsive worker's own release (R2-01) -------------


def test_watchdog_wedge_stop_responsive_worker_answers_watchdog_failure(channel, monkeypatch):
    """A worker that honors terminate() during the bounded wait must not
    overwrite the watchdog verdict the client should receive.

    Before R2-01 the manager fired the finalize hook only AFTER the
    bounded termination wait: a stop-responsive worker released its own
    waiter with STOPPED and popped the dispatched record inside that
    wait, so the delayed hook no-opped and the blocked client answered
    ``status=stopped`` instead of the watchdog failure.
    """
    events = []
    d = _start_daemon(channel, monkeypatch, events, wedged_timeout_sec=0.3)
    try:
        sock = channel["sock"]

        def silent_but_stop_responsive_play(self, decoded):
            # Silent wedge (no progress tokens once "playing" is set)
            # that still honors the stop flag: the exact worker shape
            # that used to steal the reply from the watchdog verdict.
            events.append(("start", self.label, time.monotonic()))
            self.state["status"] = "playing"
            while not self.state["stop"]:
                time.sleep(0.005)
            events.append(("end", self.label, time.monotonic()))
            self.state["status"] = "stopped"

        monkeypatch.setattr(AudioSession, "play", silent_but_stop_responsive_play)

        box, play_thread = _play_async_sock(sock, {"text": "responsive wedge", "label": "RWEDGE"})
        _wait_until(lambda: any(e[0] == "start" and e[1] == "RWEDGE" for e in events))
        assert d._inflight == 1

        time.sleep(0.35)  # real clock: past the 0.3 s wedged budget
        assert d.queue_manager.check_watchdog() is True

        # The blocked play CLIENT carries the WATCHDOG failure — not the
        # STOPPED the worker's own release used to race in first.
        play_thread.join(timeout=5.0)
        assert not play_thread.is_alive()
        assert box and box[0].startswith("ok=false error=watchdog: no playback progress"), box

        # The worker really did unwind during the bounded wait, and the
        # handler's finally ran: mount free, _inflight back to 0.
        _wait_until(lambda: any(e[0] == "end" and e[1] == "RWEDGE" for e in events))
        assert d.active_session is None
        assert d._inflight == 0
    finally:
        _stop_daemon(d)

