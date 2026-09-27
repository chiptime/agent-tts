"""Hito Cola acceptance tests: contract invariants (AT-08, BLOQUE 1.3, T5).

Deterministic structural invariants of the BLOQUE 1.3 "Hito Cola" DoD,
run in CI over the real in-process daemon and the real IPC channel:

- RF-AT-08-6: a 50-event concurrent simulation with zero audio overlaps
  (per-item speaker intervals recorded at the ``AudioSession.play``
  seam — the same seam test_queue_ipc/test_daemon fake — with real
  synthesis-to-playback machinery above it and a stub engine).
- Blocked completeness: 100% of blocked items play complete (never
  trampled) in the simulation, and a blocked event preempting a playing
  done reaches the speaker while itself completing (US-AT-08-1).
- US-AT-08-2: a burst of 10 coalescible done events produces exactly ONE
  announcement with ``coalesced=10``.
- RNF-AT-08-3 (order contract): the SAME scripted event sequence
  produces the SAME resulting order on local and wsl-ps. The wsl-ps leg
  performs REAL ``PowershellSession.play`` calls (real powershell.exe)
  with stub synthesis, and runs only when wsl-ps is measurable — the
  same skip condition as tests/test_powershell_playback.py. winhost v1
  is excluded by design (documented limitation in BLOQUE-1.3).

Timing thresholds (RNF-AT-08-1 < 50 ms, RNF-AT-08-2 < 1.5 s) are
deliberately NOT asserted here (flake discipline): they are measured by
scripts/queue_metrics.py against a real daemon subprocess and registered
in the BLOQUE 1.3 evidence doc. This module exports the canonical
SCRIPTED_SEQUENCE / CONTRACT_EXPECTED_LABELS the metrics script reuses,
so CI contract and registered evidence can never drift apart.

Flake hardening (T5.1, 2026-09-25): the 50-event simulation flaked once
under full-suite load (2026-09-24) on a transient IPC failure —
``send_ipc_command`` returns None on a refused connect or when its 1 s
client timeout expires while the in-process daemon's connection thread
is starved — and these helpers used to dereference that reply
unconditionally. Idempotent status reads now retry transient failures
(``_status``), polling predicates are None-tolerant (``_queue_len``
returns -1), and enqueue keeps loud no-retry semantics: a None enqueue
reply is ambiguous (the item may have landed) and re-sending could
double-enqueue. The zero-overlap invariant itself is sound — the play
seam stamps CLOCK_MONOTONIC and the queue's single active slot gives
end->start happens-before ordering, so a false overlap is impossible by
construction.
"""

import array
import json
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor

import pytest

import agent_tts.cli as cli_module
import agent_tts.powershell_playback as psp
import agent_tts.ipc as ipc
from agent_tts import audio
from agent_tts.audio import AudioSession
from agent_tts.daemon import Daemon, ProviderCache


# --- Canonical contract sequence (RNF-AT-08-3) --------------------------------------------
#
# The scripted event sequence for the order contract. The first item holds
# the speaker (locally via a gate in the play stub, on wsl-ps via 2 s of
# real audio) so the whole sequence enqueues while one item is active and
# the resulting order is fully determined by the queue semantics.
# ``text`` starting with "HOLD" marks the holder; the decode stub maps it
# to a long payload. scripts/queue_metrics.py imports these constants.

SCRIPTED_SEQUENCE = [
    {"label": "S-HOLD", "text": "HOLD contract anchor", "priority": "done", "policy": "queue"},
    {"label": "S-B1", "text": "blocked one", "priority": "blocked", "policy": "queue"},
    {"label": "S-D1", "text": "done one", "priority": "done", "policy": "queue"},
    {"label": "S-W1", "text": "working one", "priority": "working", "policy": "queue"},
    {"label": "S-B2", "text": "blocked two", "priority": "blocked", "policy": "queue"},
]
SCRIPTED_SEQUENCE += [
    {
        "label": f"S-C{i}",
        "text": f"test t{i} finished",
        "priority": "done",
        "policy": "coalesce",
        "event_type": "tests finished",
        "identifiers": [f"t{i}"],
    }
    for i in range(1, 7)
]
SCRIPTED_SEQUENCE += [
    {"label": "S-D2", "text": "done two", "priority": "done", "policy": "queue"},
    # Strictly-higher preempt arriving while the holder (done) still plays.
    {"label": "S-B3P", "text": "blocked three preempt", "priority": "blocked", "policy": "preempt"},
]

# Expected resulting order: S-B3P preempts S-HOLD (its announcement is
# lost — preempt semantics, not re-queued); dispatch then follows
# blocked FIFO (S-B1, S-B2, S-B3P), done FIFO (S-D1, the coalesced burst
# owner S-C1 announcing all six events, S-D2), working FIFO (S-W1).
CONTRACT_EXPECTED_LABELS = ["S-B1", "S-B2", "S-B3P", "S-D1", "S-C1", "S-D2", "S-W1"]

# How many queue items the sequence builds behind the holder: 6 plain
# events + 1 coalesce window owner (6 burst events merged) + preempt.
CONTRACT_PENDING_AFTER_ENQUEUE = 7


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
    """Provider stand-in: records calls, returns the text as "audio".

    Synthesis returns the text bytes themselves so the decode stub below
    can derive each payload's duration from what synthesis produced —
    the same text-driven shape for every target.
    """

    def __init__(self):
        self.calls = []

    async def synthesize(self, text, voice=None, rate=None, volume=None, pitch=None, stop_checker=None):
        self.calls.append({"text": text, "voice": voice})
        return text.encode("utf-8")


def _install_decode_stub(monkeypatch, *, hold_sec: float, item_sec: float) -> None:
    """Replaces cli.miniaudio.decode: text -> fixed PCM of mapped duration.

    ``HOLD``-prefixed texts decode to ``hold_sec`` of silence, every
    other text to ``item_sec`` — deterministic, device-free payloads
    with a real shape (array samples, rate, channels, width).
    """

    def decoded_for(data):
        try:
            text = bytes(data).decode("utf-8", "ignore")
        except Exception:
            text = ""
        duration = hold_sec if text.startswith("HOLD") else item_sec
        holder = types.SimpleNamespace(
            sample_rate=24000,
            nchannels=1,
            sample_width=2,
            duration=duration,
            samples=array.array("h", [0] * int(24000 * duration)),
        )
        return holder

    monkeypatch.setattr(cli_module, "miniaudio", types.SimpleNamespace(decode=decoded_for))


def _recording_play(events, gate=None):
    """AudioSession.play stand-in: records speaker intervals, self-completes.

    Intervals are recorded at the play seam (start/end timestamps plus
    whether playback ended interrupted via the session stop flag). A
    long payload (``decoded.duration > 1 s``) with a ``gate`` blocks
    until the gate opens or the session is stopped — the deterministic
    queue build-up; every other payload plays out its duration.
    """

    def play(self, decoded):
        t0 = time.monotonic()
        label = self.label
        events.append(("start", label, t0))
        self.state["status"] = "playing"
        duration = max(float(getattr(decoded, "duration", 0.0) or 0.0), 0.01)
        hold = duration > 1.0 and gate is not None
        deadline = time.monotonic() + (60.0 if hold else duration)
        while time.monotonic() < deadline:
            if self.state["stop"]:
                break
            if hold and gate.is_set():
                break
            time.sleep(0.004)
        interrupted = bool(self.state["stop"])
        events.append(("end", label, time.monotonic(), "interrupted" if interrupted else "completed"))
        self.state["status"] = "stopped"

    return play


def _start_daemon(channel, monkeypatch, events, *, gate=None, **daemon_kwargs) -> Daemon:
    """In-process daemon with stub synthesis and the recording play seam."""
    stub_engine = StubEngine()
    cache = ProviderCache(factory=lambda **kwargs: stub_engine)
    if "play_patch" not in daemon_kwargs or daemon_kwargs.pop("play_patch"):
        monkeypatch.setattr(AudioSession, "play", _recording_play(events, gate=gate))
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


def _wait_until(predicate, timeout_sec: float = 10.0, message: str = "condition never held") -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(message)


def _enqueue(sock, payload: dict) -> str:
    # Deliberately NO retry (T5.1): a None reply is ambiguous — the
    # item may already be inserted and only its reply lost — and
    # re-sending could double-enqueue, breaking "each event played
    # exactly once". Failure stays loud and diagnostic (_item_id).
    return ipc.send_ipc_command("enqueue " + json.dumps(payload), socket_path=sock)


def _item_id(reply: str) -> int:
    assert isinstance(reply, str) and reply.startswith("ok=true "), (
        f"enqueue reply unusable (transient IPC failure?): {reply!r}"
    )
    return int(reply.split("item=")[1].split()[0])


def _status(sock, timeout_sec: float = 10.0) -> str:
    """Status reply, retrying transient IPC failures under load (T5.1).

    ``send_ipc_command`` returns None on ANY transport hiccup — a
    refused connect, or the 1 s client timeout expiring while the
    in-process daemon's connection thread is starved by full-suite
    load. Status is a pure read, so a transient failure only means
    "not observed yet": retry until the deadline, then fail loudly.
    These test daemons never idle out, so a channel that stays dead is
    a real defect, never a race worth waiting out.
    """
    deadline = time.monotonic() + timeout_sec
    reply = None
    while time.monotonic() < deadline:
        reply = ipc.send_ipc_command("status", socket_path=sock)
        if reply and "queue_len=" in reply:
            return reply
        time.sleep(0.02)
    raise AssertionError(f"daemon never answered status: last reply {reply!r}")


def _queue_len(sock) -> int:
    """Pending count; -1 on a transiently unusable status reply (T5.1).

    Called from ``_wait_until`` predicates at ~50 Hz during the drain:
    a transient None must read as "condition not observed yet" instead
    of crashing the predicate — the enclosing wait's own deadline still
    bounds the total time.
    """
    status = ipc.send_ipc_command("status", socket_path=sock)
    if not status:
        return -1
    token = next((t for t in status.split() if t.startswith("queue_len=")), None)
    return int(token.split("=")[1]) if token is not None else -1


def _queue_payload(status: str) -> dict:
    token = next(t for t in status.split() if t.startswith("queue={"))
    return json.loads(token[len("queue="):])


def _starts(events):
    return [e[1] for e in events if e[0] == "start"]


def _intervals(events):
    """Per-label (start, end) speaker intervals from the play seam."""
    out = {}
    for e in events:
        if e[0] == "start":
            out.setdefault(e[1], [e[2], None])[0] = e[2]
        elif e[0] == "end":
            out.setdefault(e[1], [None, e[2]])[1] = e[2]
    return {label: tuple(bounds) for label, bounds in out.items() if bounds[0] is not None and bounds[1] is not None}


def _assert_no_overlaps(intervals: dict) -> None:
    """No two speaker intervals intersect; each ends after it starts."""
    ordered = sorted(intervals.items(), key=lambda kv: kv[1][0])
    previous_label, previous = None, None
    for label, (start, end) in ordered:
        assert end >= start, f"{label}: interval ends before it starts"
        if previous is not None:
            assert start >= previous[1], (
                f"audio overlap: {label} started at {start:.6f} before "
                f"{previous_label} ended at {previous[1]:.6f}"
            )
        previous_label, previous = label, (start, end)


# --- 50-event simulation (RF-AT-08-6, blocked completeness) -------------------------------

# Priority mix of the 50-event simulation: i % 5 == 0 -> blocked (10),
# i % 5 == 4 -> working (10), otherwise done (30).
SIMULATION_EVENTS = 50


def _simulation_payload(i: int) -> dict:
    if i % 5 == 0:
        priority = "blocked"
    elif i % 5 == 4:
        priority = "working"
    else:
        priority = "done"
    return {"label": f"E{i:02d}", "text": f"event {i}", "priority": priority, "policy": "queue", "stream": "off"}


def _run_simulation(sock, events, gate):
    """Fires the 50-event concurrent simulation and returns the ack records."""
    _enqueue(sock, {"label": "HOLD50", "text": "HOLD simulation anchor", "priority": "working", "policy": "queue"})
    _wait_until(lambda: any(e[0] == "start" and e[1] == "HOLD50" for e in events))

    acks = []
    ack_lock = threading.Lock()

    def enqueue_one(i):
        payload = _simulation_payload(i)
        reply = _enqueue(sock, payload)
        with ack_lock:
            acks.append((_item_id(reply), payload["priority"], payload["label"]))

    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(enqueue_one, range(SIMULATION_EVENTS)))

    # Deterministic build-up: all 50 pending behind the held speaker.
    _wait_until(lambda: _queue_len(sock) == SIMULATION_EVENTS, message="queue never reached 50 pending items")
    gate.set()  # release the anchor: it completes naturally and the queue drains
    _wait_until(lambda: _queue_len(sock) == 0, message="queue never drained")
    _wait_until(
        lambda: sum(1 for e in events if e[0] == "end") == SIMULATION_EVENTS + 1,
        message="the last simulated playback never completed",
    )
    return acks


def test_50_event_simulation_no_overlaps_all_blocked_complete(channel, monkeypatch):
    events = []
    gate = threading.Event()
    _install_decode_stub(monkeypatch, hold_sec=30.0, item_sec=0.04)
    d = _start_daemon(channel, monkeypatch, events, gate=gate)
    try:
        sock = channel["sock"]
        acks = _run_simulation(sock, events, gate)
        _wait_until(lambda: len(_starts(events)) == SIMULATION_EVENTS + 1, message="not every item played")

        played = _starts(events)
        assert played[0] == "HOLD50"
        simulated = played[1:]
        assert sorted(simulated) == sorted(f"E{i:02d}" for i in range(SIMULATION_EVENTS)), "each event played exactly once"

        # RF-AT-08-6: zero audio overlaps across every speaker interval.
        _assert_no_overlaps(_intervals(events))

        # Nothing was interrupted; every blocked event played COMPLETE.
        ends = {e[1]: e[3] for e in events if e[0] == "end"}
        assert set(ends.values()) == {"completed"}, {label: outcome for label, outcome in ends.items() if outcome != "completed"}
        blocked_labels = [label for _, priority, label in acks if priority == "blocked"]
        assert len(blocked_labels) == 10
        assert all(ends[label] == "completed" for label in blocked_labels)

        # Dispatch order: blocked class strictly before done strictly
        # before working (all 50 were pending together behind the anchor).
        position = {label: idx for idx, label in enumerate(simulated)}
        blocked_pos = [position[label] for label in blocked_labels]
        done_pos = [position[label] for _, priority, label in acks if priority == "done"]
        working_pos = [position[label] for _, priority, label in acks if priority == "working"]
        assert max(blocked_pos) < min(done_pos) < max(done_pos) < min(working_pos)

        # FIFO within each priority class follows arrival order (item id).
        ids_by_label = {label: item_id for item_id, _, label in acks}
        for positions in (blocked_labels, [label for _, priority, label in acks if priority == "done"],
                          [label for _, priority, label in acks if priority == "working"]):
            played_in_class = [label for label in simulated if label in set(positions)]
            assert [ids_by_label[label] for label in played_in_class] == sorted(ids_by_label[label] for label in played_in_class)

        # Final snapshot: everything completed, nothing failed/interrupted/wedged.
        snapshot = _queue_payload(_status(sock))
        assert snapshot["completed_count"] == SIMULATION_EVENTS + 1
        assert snapshot["failed_count"] == 0
        assert snapshot["interrupted_count"] == 0
        assert snapshot["wedged_count"] == 0
    finally:
        _stop_daemon(d)


# --- coalescing burst (US-AT-08-2) ----------------------------------------------------------

def test_coalesce_burst_of_10_done_is_one_announcement(channel, monkeypatch):
    events = []
    gate = threading.Event()
    _install_decode_stub(monkeypatch, hold_sec=30.0, item_sec=0.04)
    d = _start_daemon(channel, monkeypatch, events, gate=gate)
    try:
        sock = channel["sock"]
        _enqueue(sock, {"label": "HOLDC", "text": "HOLD coalesce anchor", "priority": "working", "policy": "queue"})
        _wait_until(lambda: any(e[0] == "start" and e[1] == "HOLDC" for e in events))

        replies = [
            _enqueue(
                sock,
                {
                    "label": f"C{i:02d}",
                    "text": f"case c{i} finished",
                    "priority": "done",
                    "policy": "coalesce",
                    "event_type": "tests finished",
                    "identifiers": [f"c{i}"],
                },
            )
            for i in range(1, 11)
        ]
        # The window owner's own reply carries no coalesced= token
        # (coalesced=1 is implicit); every merged reply does.
        merge_counts = [
            int(r.split("coalesced=")[1].split()[0]) if "coalesced=" in r else 1 for r in replies
        ]
        assert merge_counts == list(range(1, 11)), replies  # US-AT-08-2: coalesced=10 recorded
        assert len({_item_id(r) for r in replies}) == 1  # every burst event merged into ONE item

        pending = _queue_payload(_status(sock))["pending"]
        assert len(pending) == 1
        assert pending[0]["coalesced"] == 10
        assert pending[0]["identifiers"] == [f"c{i}" for i in range(1, 11)]
        assert pending[0]["announcement"] == "10 tests finished: c1, c2, c3 and 7 more"

        gate.set()
        _wait_until(
            lambda: sum(1 for e in events if e[0] == "end") == 2,
            message="the merged announcement never played",
        )

        # Exactly ONE audible announcement for the 10-event burst: the
        # window owner's label, speaking the synthesized summary.
        burst_starts = [label for label in _starts(events) if label.startswith("C")]
        assert burst_starts == ["C01"]
        assert d._test_engine.calls[-1]["text"] == "10 tests finished: c1, c2, c3 and 7 more"

        snapshot = _queue_payload(_status(sock))
        assert snapshot["completed_count"] == 2
        assert snapshot["failed_count"] == 0
    finally:
        _stop_daemon(d)


# --- blocked preempts a playing done (US-AT-08-1, RNF-AT-08-2 structural) --------------------

def test_blocked_preempts_playing_done_and_reaches_speaker_complete(channel, monkeypatch):
    events = []
    gate = threading.Event()
    _install_decode_stub(monkeypatch, hold_sec=30.0, item_sec=0.04)
    d = _start_daemon(channel, monkeypatch, events, gate=gate)
    try:
        sock = channel["sock"]
        _enqueue(sock, {"label": "PLAYING-DONE", "text": "HOLD done anchor", "priority": "done", "policy": "queue"})
        _wait_until(lambda: any(e[0] == "start" and e[1] == "PLAYING-DONE" for e in events))
        _enqueue(sock, {"label": "F1", "text": "filler one", "priority": "done", "policy": "queue"})
        _enqueue(sock, {"label": "F2", "text": "filler two", "priority": "done", "policy": "queue"})

        # A blocked event while a done plays: strictly higher priority,
        # preempt policy — the done is interrupted, blocked reaches the
        # speaker (the < 1.5 s budget is measured by the metrics script).
        _enqueue(sock, {"label": "B-CRIT", "text": "critical block", "priority": "blocked", "policy": "preempt"})

        _wait_until(lambda: all(any(e[0] == "end" and e[1] == label for e in events) for label in ("B-CRIT", "F1", "F2")))
        _wait_until(lambda: len(_starts(events)) == 4)

        order = _starts(events)
        assert order[0] == "PLAYING-DONE"
        assert order[1] == "B-CRIT"  # jumped both pending fillers
        assert set(order[2:]) == {"F1", "F2"}

        ends = {e[1]: e[3] for e in events if e[0] == "end"}
        assert ends["PLAYING-DONE"] == "interrupted"  # preempted: announcement lost, not re-queued
        assert ends["B-CRIT"] == "completed"  # the blocked event itself plays COMPLETE

        # RF-AT-08-6 over EVERY interval, the preempted one included: the
        # manager waits out the terminated session before dispatching the
        # preemptor (R1-01), so B-CRIT starts only after PLAYING-DONE's
        # speaker interval ended.
        _assert_no_overlaps(_intervals(events))

        snapshot = _queue_payload(_status(sock))
        assert snapshot["completed_count"] == 3
        assert snapshot["interrupted_count"] == 1
        assert snapshot["failed_count"] == 0
    finally:
        _stop_daemon(d)


# --- order contract (RNF-AT-08-3): local and wsl-ps -----------------------------------------


def _run_contract_sequence(sock, events, *, playback: str, start_label: str) -> list:
    """Enqueues the canonical sequence; returns speaker-start labels in order.

    Everything except the final preempt lands while the holder plays
    (observed as the pending count); the preempt enqueued LAST is what
    interrupts the holder and fires the drain, so the order is fully
    determined by the queue semantics.
    """
    head, *rest = SCRIPTED_SEQUENCE
    _enqueue(sock, {**head, "playback": playback, "stream": "off"})
    _wait_until(lambda: any(e[0] == "start" and e[1] == start_label for e in events))
    for event in rest[:-1]:  # the whole sequence except the preempt
        reply = _enqueue(sock, {**event, "playback": playback, "stream": "off"})
        assert reply.startswith("ok=true"), (event["label"], reply)
    _wait_until(lambda: _queue_len(sock) == CONTRACT_PENDING_AFTER_ENQUEUE - 1)
    reply = _enqueue(sock, {**rest[-1], "playback": playback, "stream": "off"})
    assert reply.startswith("ok=true"), (rest[-1]["label"], reply)
    return _starts(events)


def test_contract_sequence_order_on_local(channel, monkeypatch):
    events = []
    gate = threading.Event()
    _install_decode_stub(monkeypatch, hold_sec=30.0, item_sec=0.04)
    d = _start_daemon(channel, monkeypatch, events, gate=gate)
    try:
        played = _run_contract_sequence(channel["sock"], events, playback="local", start_label="S-HOLD")
        gate.set()  # S-B3P already preempted the anchor; drain the rest
        _wait_until(
            lambda: sum(1 for e in events if e[0] == "end") == len(CONTRACT_EXPECTED_LABELS) + 1,
            message="contract sequence never fully drained",
        )

        completed_order = [label for label in _starts(events) if label != "S-HOLD"]
        assert completed_order == CONTRACT_EXPECTED_LABELS

        ends = {e[1]: e[3] for e in events if e[0] == "end"}
        assert ends["S-HOLD"] == "interrupted"  # preempted by S-B3P (semantics, not a failure)
        assert all(ends[label] == "completed" for label in CONTRACT_EXPECTED_LABELS)
        assert d._test_engine.calls[-1]["text"] != ""
        # RF-AT-08-6 including the preempted holder (R1-01): the manager
        # waits out the terminated session before dispatching S-B3P.
        _assert_no_overlaps(_intervals(events))

        snapshot = _queue_payload(_status(channel["sock"]))
        assert snapshot["completed_count"] == len(CONTRACT_EXPECTED_LABELS)
        assert snapshot["interrupted_count"] == 1
        assert snapshot["failed_count"] == 0
    finally:
        _stop_daemon(d)


@pytest.mark.skipif(
    not psp.is_wsl_ps_available(),
    reason="real powershell.exe on PATH + WSL interop required (PATH prefix "
    "/mnt/c/Windows/System32/WindowsPowerShell/v1.0)",
)
def test_contract_sequence_order_on_wsl_ps(channel, monkeypatch):
    """Same sequence, same resulting order, REAL PowerShell playback.

    wsl-ps runs the real ``PowershellSession.play`` (a real
    powershell.exe per announcement, silent PCM payloads): synthesis is
    stubbed, everything above the play seam — queue, dispatch, preemption,
    coalescing, IPC — is the real machinery. Determinism comes from a
    2 s real audio anchor instead of the local gate.
    """
    events = []
    _install_decode_stub(monkeypatch, hold_sec=2.0, item_sec=0.25)
    real_play = psp.PowershellSession.play

    def traced_play(self, decoded):
        t0 = time.monotonic()
        events.append(("start", self.label, t0))
        try:
            return real_play(self, decoded)
        finally:
            events.append(("end", self.label, time.monotonic(), "completed"))

    monkeypatch.setattr(psp.PowershellSession, "play", traced_play)
    d = _start_daemon(channel, monkeypatch, events, play_patch=False)
    try:
        played = _run_contract_sequence(channel["sock"], events, playback="wsl-ps", start_label="S-HOLD")
        _wait_until(
            lambda: sum(1 for e in events if e[0] == "end") == len(CONTRACT_EXPECTED_LABELS) + 1,
            timeout_sec=30.0,
            message="wsl-ps contract sequence never fully drained",
        )

        # RNF-AT-08-3: the SAME sequence produced the SAME resulting
        # order as on local (test_contract_sequence_order_on_local).
        completed_order = [label for label in _starts(events) if label != "S-HOLD"]
        assert completed_order == CONTRACT_EXPECTED_LABELS

        snapshot = _queue_payload(_status(channel["sock"]))
        assert snapshot["completed_count"] == len(CONTRACT_EXPECTED_LABELS)
        assert snapshot["interrupted_count"] == 1  # S-HOLD preempted by S-B3P here too
        assert snapshot["failed_count"] == 0

        # Zero overlaps among the completed announcements on the real target
        # — the preempted holder included (R1-01: same wait discipline).
        _assert_no_overlaps(_intervals(events))
    finally:
        _stop_daemon(d)
