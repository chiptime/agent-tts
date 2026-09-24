"""Hito Cadena daemon end-to-end tests (AT-08, BLOQUE 1.3, T6).

The chain through the REAL in-process daemon and IPC channel: one queue
item per chain, one session per chain, in-order gapless bytes at the
play seam (US-AT-08-3), stop-flag semantics (the T5 convention: a
terminated chain stops cutting audio — remaining files never play),
controls over chain-global positions, the ``chain-item`` trace seam, and
the typed wire validation of the freeze-critical payload fields.
Timing thresholds live in scripts/chain_metrics.py (flake discipline).
"""

import array
import json
import threading
import time
import types
from pathlib import Path

import pytest

import agent_tts.cli as cli_module
import agent_tts.ipc as ipc
from agent_tts import audio
from agent_tts.audio import AudioSession
from agent_tts.daemon import Daemon, ProviderCache
from agent_tts.wav import pcm_to_wav

RATE = 24000  # fabricated decode stub (text holders)
WAV_RATE = 44100  # decode-native format: miniaudio passthrough, values preserved
WAV_CH = 2


def _file_pcm(mark: int, seconds: float) -> bytes:
    """Expected decoded PCM of a file written by _write_wav (native format)."""
    return array.array("h", [mark] * int(WAV_RATE * seconds * WAV_CH)).tobytes()


def _write_wav(path: Path, mark: int, seconds: float) -> Path:
    """Writes one real WAV file whose every sample carries ``mark``.

    44100/stereo is the decode-native format: ``miniaudio.decode`` passes
    the bytes through unchanged, so tests can assert on exact PCM.
    """
    path.write_bytes(pcm_to_wav(_file_pcm(mark, seconds), WAV_RATE, WAV_CH, 2))
    return path

# --- daemon end-to-end -----------------------------------------------------------------


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


class _StubEngine:
    async def synthesize(self, text, voice=None, rate=None, volume=None, pitch=None, stop_checker=None):
        return text.encode("utf-8")


def _wait_until(predicate, timeout_sec: float = 10.0, message: str = "condition never held") -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(message)


def _advancing_play(events, gate=None):
    """AudioSession.play stand-in simulating a device consuming the buffer.

    Advances ``current_frame`` at the real sample rate (4 ms ticks),
    honoring pause and the stop flag; a ``gate`` holds the cursor at 0
    until opened (the deterministic build-up seam). Records the exact
    PCM stream handed to the device so tests assert on the bytes.
    """

    def play(self, decoded):
        if not self._buffer_loaded:
            self._load_decoded_locked(decoded)
        events.append(("start", self.label, time.monotonic()))
        self.state["status"] = "playing"
        if gate is not None:
            while not gate.is_set() and not self.state["stop"] and self.state["status"] != "stopped":
                time.sleep(0.004)
        tick_frames = int(self.sample_rate * 0.004)
        while self.current_frame < self.total_frames:
            if self.state["stop"] or self.state["status"] == "stopped":
                break
            if self.state["status"] == "paused":
                time.sleep(0.004)
                continue
            with self.lock:
                self.current_frame = min(self.total_frames, self.current_frame + tick_frames)
            time.sleep(0.004)
        interrupted = bool(self.state["stop"])
        events.append(("end", self.label, time.monotonic(), "interrupted" if interrupted else "completed", bytes(decoded.samples)))
        self.state["status"] = "stopped"

    return play


def _start_daemon(channel, monkeypatch, events, *, gate=None) -> Daemon:
    monkeypatch.setattr(AudioSession, "play", _advancing_play(events, gate=gate))
    # Text payloads (holders) synthesize through the stub engine and must
    # decode through a stub too; chain files decode through agent_tts.chain's
    # own miniaudio import, which this leaves real.
    monkeypatch.setattr(
        cli_module,
        "miniaudio",
        types.SimpleNamespace(decode=lambda data: types.SimpleNamespace(
            sample_rate=RATE, nchannels=1, sample_width=2, duration=0.05,
            samples=array.array("h", [0] * int(RATE * 0.05)),
        )),
    )
    d = Daemon(socket_path=channel["sock"], provider_cache=ProviderCache(factory=lambda **kw: _StubEngine()))
    thread = threading.Thread(target=d.run, daemon=True)
    thread.start()
    _wait_until(
        lambda: (reply := ipc.send_ipc_command("ping", socket_path=channel["sock"])) and reply.startswith("pong"),
        message="daemon never answered ping",
    )
    d._test_thread = thread
    return d


def _stop_daemon(d: Daemon) -> None:
    d.request_shutdown()
    thread = getattr(d, "_test_thread", None)
    if thread is not None:
        thread.join(timeout=5.0)


def _queue_payload(sock) -> dict:
    status = ipc.send_ipc_command("status", socket_path=sock)
    token = next(t for t in status.split() if t.startswith("queue={"))
    return json.loads(token[len("queue=") :])


def _chain_traces(capsys) -> list:
    """chain-item trace lines seen so far: [(item_id, index, t), ...]."""
    out = []
    for line in capsys.readouterr().err.splitlines():
        if line.startswith("agent-tts-queue: chain-item"):
            fields = dict(
                tok.split("=", 1) for tok in line.split("agent-tts-queue: chain-item ")[1].split() if "=" in tok
            )
            out.append((int(fields["item"]), int(fields["index"]), float(fields["t"])))
    return out


def test_chain_plays_once_in_order_gapless_as_one_queue_item(channel, monkeypatch, capsys, tmp_path):
    """US-AT-08-3: two files, ONE session, in order, no inserted gap (default 0 ms)."""
    a = _write_wav(tmp_path / "a.wav", 111, 0.3)
    b = _write_wav(tmp_path / "b.wav", 222, 0.2)
    events = []
    built = []
    real_build = cli_module._build_playback_session

    def counting_build(*args, **kwargs):
        built.append(1)
        return real_build(*args, **kwargs)

    monkeypatch.setattr(cli_module, "_build_playback_session", counting_build)
    d = _start_daemon(channel, monkeypatch, events)
    try:
        sock = channel["sock"]
        reply = ipc.send_ipc_command(
            "enqueue " + json.dumps({"chain": [str(a), str(b)], "priority": "working", "policy": "queue"}),
            socket_path=sock,
        )
        assert reply.startswith("ok=true"), reply
        assert "queue_len=0" in reply  # ONE queue item, dispatched immediately
        _wait_until(lambda: any(e[0] == "end" for e in events), message="chain never finished")

        # One-session invariant: exactly one session built, one play start.
        assert len(built) == 1
        starts = [e for e in events if e[0] == "start"]
        assert len(starts) == 1
        # In order, gapless: the device stream is a's samples then b's, nothing between.
        assert events[-1][4] == _file_pcm(111, 0.3) + _file_pcm(222, 0.2)
        # Exactly one finalize for the whole chain.
        snapshot = _queue_payload(sock)
        assert snapshot["completed_count"] == 1
        assert snapshot["failed_count"] == 0
    finally:
        _stop_daemon(d)


def test_chain_queued_behind_active_item_dispatches_by_policy(channel, monkeypatch, tmp_path):
    """The chain is one queue item and waits its turn (policy application)."""
    a = _write_wav(tmp_path / "a.wav", 1, 0.3)
    b = _write_wav(tmp_path / "b.wav", 2, 0.3)
    events = []
    gate = threading.Event()
    d = _start_daemon(channel, monkeypatch, events, gate=gate)
    try:
        sock = channel["sock"]
        ipc.send_ipc_command("enqueue " + json.dumps({"label": "HOLDER", "text": "hold", "stream": "off"}), socket_path=sock)
        _wait_until(lambda: any(e[0] == "start" and e[1] == "HOLDER" for e in events))
        reply = ipc.send_ipc_command(
            "enqueue " + json.dumps({"chain": [str(a), str(b)]}), socket_path=sock
        )
        assert reply.startswith("ok=true") and "queue_len=1" in reply
        gate.set()
        _wait_until(lambda: len([e for e in events if e[0] == "end"]) == 2, message="holder and chain never drained")

        order = [e[1] for e in events if e[0] == "start"]
        assert order[0] == "HOLDER"
        chain_start = [e for e in events if e[0] == "start"][1][1]
        assert "a.wav" in chain_start  # the chain item speaks after the holder
        # No overlap: chain started only after the holder ended.
        holder_end = next(e[2] for e in events if e[0] == "end" and e[1] == "HOLDER")
        chain_start_t = [e for e in events if e[0] == "start"][1][2]
        assert chain_start_t >= holder_end
    finally:
        _stop_daemon(d)


def test_chain_honors_stop_flag_mid_file_remaining_files_not_played(channel, monkeypatch, capsys, tmp_path):
    """CRITICAL T5 convention: terminate cuts; a stopped chain does not drain."""
    a = _write_wav(tmp_path / "a.wav", 1, 1.2)
    b = _write_wav(tmp_path / "b.wav", 2, 1.2)
    events = []
    d = _start_daemon(channel, monkeypatch, events)
    try:
        sock = channel["sock"]
        ipc.send_ipc_command("enqueue " + json.dumps({"chain": [str(a), str(b)]}), socket_path=sock)
        _wait_until(lambda: any(e[0] == "start" for e in events))
        time.sleep(0.3)  # well inside file 1 (its audio spans 0.0–1.2 s)
        ipc.send_ipc_command("stop", socket_path=sock)
        _wait_until(lambda: any(e[0] == "end" for e in events), message="stopped chain never ended")

        end = next(e for e in events if e[0] == "end")
        assert end[3] == "interrupted"
        # The cursor never crossed into file 2's chain-global start (1.2 s).
        traces = _chain_traces(capsys)
        indexes = [t[1] for t in traces]
        assert 0 in indexes  # file 1 started (chain-item seam observed it)
        assert 1 not in indexes  # file 2 NEVER started
        # Queue ground truth: one item finalized as a normal (stopped) end.
        snapshot = _queue_payload(sock)
        assert snapshot["completed_count"] == 1
        assert snapshot["failed_count"] == 0
    finally:
        _stop_daemon(d)


def test_chain_controls_over_the_whole_chain_via_ipc(channel, monkeypatch, capsys, tmp_path):
    """RF-AT-08-4: seek/pause/phrase navigation address chain-global positions."""
    files = [_write_wav(tmp_path / f"f{i}.wav", i + 1, 1.0) for i in range(3)]
    events = []
    d = _start_daemon(channel, monkeypatch, events)
    try:
        sock = channel["sock"]
        ipc.send_ipc_command("enqueue " + json.dumps({"chain": [str(f) for f in files]}), socket_path=sock)
        _wait_until(lambda: any(e[0] == "start" for e in events))

        # Chain-global seek: +1.5 s lands inside file 2 (the cursor keeps
        # advancing while live, so assert a landed range, not a frozen pos).
        status = ipc.send_ipc_command("seek +1.5", socket_path=sock)
        assert "total=3.00" in status
        pos = float(next(t for t in status.split() if t.startswith("pos=")).split("=")[1])
        assert 1.5 <= pos < 1.7
        # Synthetic navigation unit: the sentence at ~1.5 s is file 2.
        sentence = ipc.send_ipc_command("sentence", socket_path=sock)
        assert "sent_idx=1" in sentence and "f1.wav" in sentence
        # Phrase navigation crosses the file boundary in one hop.
        nxt = ipc.send_ipc_command("next-sentence", socket_path=sock)
        assert "sent_idx=2" in nxt
        pos = float(next(t for t in nxt.split() if t.startswith("pos=")).split("=")[1])
        assert 2.0 <= pos < 2.3
        # Pause holds the chain mid-flight; resume continues it.
        assert "status=paused" in ipc.send_ipc_command("pause", socket_path=sock)
        assert "status=playing" in ipc.send_ipc_command("resume", socket_path=sock)
        _wait_until(lambda: any(e[0] == "end" for e in events), timeout_sec=15.0, message="chain never drained")

        # Trace seam: every item boundary observed, in order, one item id.
        traces = _chain_traces(capsys)
        assert [t[1] for t in traces] == [0, 1, 2]
        assert len({t[0] for t in traces}) == 1
    finally:
        _stop_daemon(d)


def test_chain_gap_travels_the_wire_and_spaces_items(channel, monkeypatch, tmp_path):
    a = _write_wav(tmp_path / "a.wav", 1, 0.3)
    b = _write_wav(tmp_path / "b.wav", 2, 0.3)
    events = []
    d = _start_daemon(channel, monkeypatch, events)
    try:
        sock = channel["sock"]
        reply = ipc.send_ipc_command(
            "enqueue " + json.dumps({"chain": [str(a), str(b)], "chain_gap_ms": 200}), socket_path=sock
        )
        assert reply.startswith("ok=true"), reply
        _wait_until(lambda: any(e[0] == "end" for e in events), message="chain never drained")
        silence = array.array("h", [0] * int(WAV_RATE * 0.2 * WAV_CH)).tobytes()
        assert events[-1][4] == _file_pcm(1, 0.3) + silence + _file_pcm(2, 0.3)
    finally:
        _stop_daemon(d)


# --- wire validation (typed errors, freeze-critical surface) ----------------------------


def test_chain_wire_validation_errors_are_typed(channel, monkeypatch):
    events = []
    d = _start_daemon(channel, monkeypatch, events)
    try:
        sock = channel["sock"]
        cases = [
            ({"chain": "a.wav"}, "chain must be a list of file paths"),
            ({"chain": []}, "chain must be a list of file paths"),
            ({"chain": [1, 2]}, "chain must be a list of file paths"),
            ({"chain": ["a.wav"], "text": "hi"}, "one of"),
            ({"chain": ["a.wav"], "file": "b.wav"}, "one of"),
            ({"chain": ["a.wav"], "chain_gap_ms": -5}, "chain_gap_ms"),
            ({"chain": ["a.wav"], "chain_gap_ms": "soon"}, "chain_gap_ms"),
            ({"chain": ["a.wav"], "no_play": True}, "no_play"),
        ]
        for payload, fragment in cases:
            reply = ipc.send_ipc_command("enqueue " + json.dumps(payload), socket_path=sock)
            assert reply.startswith("ok=false error="), (payload, reply)
            assert fragment in reply, (payload, reply)
    finally:
        _stop_daemon(d)


def test_missing_chain_file_fails_at_dispatch_and_is_visible(channel, monkeypatch, tmp_path):
    """Client-side validation is advisory; the daemon surfaces a missing file as a typed failure (A5)."""
    gone = str(tmp_path / "gone.wav")
    events = []
    d = _start_daemon(channel, monkeypatch, events)
    try:
        sock = channel["sock"]
        reply = ipc.send_ipc_command("enqueue " + json.dumps({"chain": [gone]}), socket_path=sock)
        assert reply.startswith("ok=true"), reply  # accepted; failure happens at dispatch
        _wait_until(lambda: _queue_payload(sock)["failed_count"] == 1, message="missing file never failed")
        snapshot = _queue_payload(sock)
        assert gone in snapshot["last_error"]
    finally:
        _stop_daemon(d)


def test_blocking_play_of_a_chain_replies_done(channel, monkeypatch, tmp_path):
    """The classic play command carries a chain too, keeping its blocking reply."""
    a = _write_wav(tmp_path / "a.wav", 5, 0.2)
    events = []
    d = _start_daemon(channel, monkeypatch, events)
    try:
        reply = ipc.send_ipc_command(
            "play " + json.dumps({"chain": [str(a)]}), socket_path=channel["sock"]
        )
        assert reply == "status=done", reply
    finally:
        _stop_daemon(d)
