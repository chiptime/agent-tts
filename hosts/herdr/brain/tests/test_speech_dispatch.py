"""Speech dispatch negotiation + async segmented dispatch (voice-stack
VS2.3/VS2.4, contract tts-brain-v2).

Fail-soft by design: a host without --contract-capabilities, a garbage
reply, or protocols the brain does not know can never block /ask — the
identified turn degrades VISIBLY to the legacy full-file path instead.
VS2.4 pins the async dispatch itself: a protocol-2 host answers right
after the LLM (speech.status "delivering", no audio_url) while a
producer thread moves segments into the audio dir under a bounded
buffer; a full buffer degrades the job and kills the render.
"""

from __future__ import annotations

import json
import subprocess
import time
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from herdr_brain import speech as speech_mod
from herdr_brain import tts as tts_mod
from herdr_brain.config import Settings
from herdr_brain.server import create_app
from herdr_brain.speech import (
    PHASE_COMPLETE,
    SEGMENTED_UNAVAILABLE,
    SpeechRegistry,
    choose_speech_path,
)
from herdr_brain.watcher import AgentWatcher
from tests.conftest import SETTINGS_KWARGS, StubHerdr
from tests.test_speech_cancel import ANSWER, FakeLLM, FakeTTS, speech_body

@pytest.fixture(autouse=True)
def fresh_probe_cache():
    tts_mod.reset_capabilities_cache()
    yield
    tts_mod.reset_capabilities_cache()


def reply(stdout="", returncode=0):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")


def test_legacy_ask_full_file_path_intact(tmp_path):
    """No speech id: the legacy /ask keeps its exact full-file shape."""
    tts = FakeTTS()
    client = TestClient(create_app(
        settings=Settings(**{**SETTINGS_KWARGS, "audio_dir": str(tmp_path / "a")}),
        llm_factory=lambda c, t: FakeLLM(),
        tts_renderer=tts,
        watcher=AgentWatcher(Settings(**SETTINGS_KWARGS), herdr=StubHerdr(), tts_renderer=None),
        daemon_probe=lambda: "up",
    ))
    resp = client.post("/ask", json={"text": "hola", "session_id": "s1"})
    body = resp.json()
    assert resp.status_code == 200
    assert body["audio_url"].startswith("/audio/")
    assert "speech" not in body            # legacy: no speech key, byte-parity
    assert len(tts.calls) == 1 and tts.calls[0]["text"] == ANSWER  # ONE full-file render


def test_v1_host_degrades_visibly(tmp_path):
    """Host without capabilities flag: identified turn → legacy + reason."""
    settings = Settings(**{**SETTINGS_KWARGS, "audio_dir": str(tmp_path / "a")})
    # no runner injected and tts_bin does not exist → probe fails → v1 host
    path, reason = choose_speech_path(settings, protocols=())
    assert path == "legacy" and reason == SEGMENTED_UNAVAILABLE

    class NoFlagRunner:
        def __call__(self, cmd, **kw):
            assert "--contract-capabilities" in cmd
            return reply(stdout="", returncode=1)  # v1 host: flag unknown

    assert tts_mod.host_supported_protocols(settings, runner=NoFlagRunner()) == ()
    assert choose_speech_path(settings, protocols=()) == ("legacy", SEGMENTED_UNAVAILABLE)


def test_unknown_protocol_not_enabled():
    """Claimed protocols above the brain's maximum are ignored (fail-soft)."""
    settings = Settings(**SETTINGS_KWARGS)

    class Claims:
        def __init__(self, protocols):
            self.protocols = protocols
            self.calls = 0

        def __call__(self, cmd, **kw):
            self.calls += 1
            return reply(stdout=json.dumps({"supported_protocols": self.protocols}))

    future = Claims([1, 2, 99])
    assert tts_mod.host_supported_protocols(settings, runner=future) == (1, 2)
    assert choose_speech_path(settings, protocols=(1, 2)) == ("segmented", None)

    tts_mod.reset_capabilities_cache()  # different claim set, same binary
    only_future = Claims([3, 99])
    assert tts_mod.host_supported_protocols(settings, runner=only_future) == ()
    assert choose_speech_path(settings, protocols=()) == ("legacy", SEGMENTED_UNAVAILABLE)


@pytest.mark.parametrize(
    "stdout, rc",
    [
        ("not json{", 0),
        ('{"supported_protocols": "1,2"}', 0),
        ('{"supported_protocols": [1, "2"]}', 0),
        ('{"unexpected": true}', 0),
        ('{"supported_protocols": [1, 2]}', 3),
    ],
)
def test_probe_failures_are_v1_only_hosts(stdout, rc):
    settings = Settings(**SETTINGS_KWARGS)
    assert tts_mod.host_supported_protocols(
        settings, runner=lambda cmd, **kw: reply(stdout=stdout, returncode=rc)) == ()


def test_probe_timeout_and_crash_fail_soft():
    settings = Settings(**SETTINGS_KWARGS)

    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 5)

    def missing(cmd, **kw):
        raise FileNotFoundError("no host binary")

    assert tts_mod.host_supported_protocols(settings, runner=boom) == ()
    assert tts_mod.host_supported_protocols(settings, runner=missing) == ()


def test_probe_result_is_cached_per_host_binary():
    settings = Settings(**SETTINGS_KWARGS)
    calls = []

    def counting(cmd, **kw):
        calls.append(cmd)
        return reply(stdout='{"supported_protocols":[1,2]}')

    assert tts_mod.host_supported_protocols(settings, runner=counting) == (1, 2)
    assert tts_mod.host_supported_protocols(settings, runner=counting) == (1, 2)
    assert len(calls) == 1  # one probe per host binary, cached


# -- VS2.4: async segmented dispatch over a REAL protocol-2 stub --------

# A protocol-2 herdr-tts stub (python3, per-test): answers the two probe
# flags and implements --render-text-segmented with atomic manifest
# re-publishes. Behavior is env-controlled:
#   SR_STUB_CONTROL — dir where the stub drops control/marker files
#   SR_STUB_MODE    - "gated": publish seg 0, BLOCK until a `release`
#                     file appears, then publish 1..2 + terminal manifest
#                     (proves /ask returns while the render is stuck)
#                   - "flood": publish 10 segments + terminal manifest
#                     fast, then stay alive until SIGTERM (bounded-buffer
#                     degrade must kill it); TERM writes `terminated`
# The stub always copies its argv and the input file into the control
# dir so tests can pin "text by FILE, never argv".
_SEGMENTED_STUB = '''#!/usr/bin/env python3
import json, os, signal, sys, time

CONTROL = os.environ.get("SR_STUB_CONTROL", "/tmp")
MODE = os.environ.get("SR_STUB_MODE", "gated")
RID = "unset"


def publish(out_dir, upto_seq, complete):
    for seq in range(upto_seq + 1):
        seg = os.path.join(out_dir, "seg-%04d.mp3" % seq)
        tmp = seg + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(("ID3-seg-%04d" % seq).encode())
        os.replace(tmp, seg)  # atomic segment publication
    manifest = {
        "speech_request_id": RID, "voice": "elvira", "rate": "+0%",
        "revision": upto_seq + (2 if complete else 1),
        "is_complete": complete, "cancelled": False, "error": None,
        "segments": [
            {"seq": i, "file": "seg-%04d.mp3" % i, "bytes": 12}
            for i in range(upto_seq + 1)
        ],
    }
    mtmp = os.path.join(out_dir, "manifest.json.tmp")
    with open(mtmp, "w") as fh:
        json.dump(manifest, fh)
    os.replace(mtmp, os.path.join(out_dir, "manifest.json"))


def mark(name):
    with open(os.path.join(CONTROL, name), "w") as fh:
        fh.write(name)


def wait_for(path, timeout):
    deadline = time.monotonic() + timeout
    while not os.path.exists(path):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)
    return True


def on_term(_signum, _frame):
    mark("terminated")
    os._exit(0)


signal.signal(signal.SIGTERM, on_term)

args = sys.argv[1:]
if args[0] == "--contract-version":
    print(1)
    sys.exit(0)
if args[0] == "--contract-capabilities":
    print(json.dumps({"supported_protocols": [1, 2]}))
    sys.exit(0)
if args[0] == "--render-text-segmented":
    out_dir, in_file = args[1], args[2]
    RID = args[args.index("--speech-request-id") + 1]
    with open(os.path.join(CONTROL, "argv.json"), "w") as fh:
        json.dump(sys.argv, fh)
    with open(os.path.join(CONTROL, "input.txt"), "w") as fh:
        fh.write(open(in_file, encoding="utf-8").read())
    if MODE == "gated":
        publish(out_dir, 0, complete=False)
        mark("first")
        wait_for(os.path.join(CONTROL, "release"), 20)
        publish(out_dir, 1, complete=False)
        publish(out_dir, 2, complete=True)
        mark("finished")
        sys.exit(0)
    if MODE == "flood":
        for seq in range(10):
            publish(out_dir, seq, complete=False)
        publish(out_dir, 9, complete=True)
        wait_for(os.path.join(CONTROL, "release"), 20)  # alive until TERM
        mark("finished")
        sys.exit(0)
sys.exit(2)
'''


def write_segmented_stub(tmp_path: Path) -> Path:
    stub = tmp_path / "herdr-tts-segmented"
    stub.write_text(_SEGMENTED_STUB)
    stub.chmod(0o755)
    return stub


def wait_until(predicate, timeout=10.0, interval=0.02) -> bool:
    """Bounded poll: never a bare sleep-only assert."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def make_segmented_app(settings, registry):
    return create_app(
        settings=settings,
        llm_factory=lambda _cfg, _tools: FakeLLM(),
        watcher=AgentWatcher(settings, herdr=StubHerdr(), tts_renderer=None),
        daemon_probe=lambda: "up",
        speech_registry=registry,
    )


def identified_body(speech_id, token="cap-token-vs24"):
    return {
        "text": "¿Cómo va todo?",
        "session_id": "s1",
        "speech_request_id": speech_id,
        "speech_cancel_token": token,
    }


def test_first_segment_delivered_before_render_completes(tmp_path, monkeypatch):
    """Protocol-2 host: /ask answers right after the LLM (delivering, no
    audio_url) while the render is STILL running, and sr-0000.mp3 lands
    in the audio dir before the render completes."""
    control = tmp_path / "control"
    control.mkdir()
    audio = tmp_path / "a"
    monkeypatch.setenv("SR_STUB_CONTROL", str(control))
    monkeypatch.setenv("SR_STUB_MODE", "gated")
    settings = Settings(**{
        **SETTINGS_KWARGS,
        "tts_bin": str(write_segmented_stub(tmp_path)),
        "audio_dir": str(audio),
    })
    registry = SpeechRegistry()
    client = TestClient(make_segmented_app(settings, registry))
    speech_id = "vs24.gated.0001"

    resp = client.post("/ask", json=identified_body(speech_id))
    body = resp.json()
    assert resp.status_code == 200
    assert body["answer"] == ANSWER  # the textual answer is never blocked
    assert body["audio_url"] is None  # segments arrive via /next (VS2.5)
    assert body["speech"] == {"id": speech_id, "status": "delivering"}
    # The stub subprocess is STILL ALIVE: blocked before its release
    # file, terminal marker absent — /ask did NOT wait for the render.
    assert not (control / "release").exists()
    assert not (control / "finished").exists()
    # The first segment lands in the audio dir BEFORE the render ends.
    first = audio / f"sr-{speech_id}-0000.mp3"
    assert wait_until(first.exists), "first segment never arrived"
    assert not (control / "finished").exists()  # still mid-render
    job = registry.get(speech_id)
    assert job.phase in ("rendering", "delivering")
    # Buffer + producer ride on the job so /next (VS2.5) can serve them.
    assert job.segments is job.producer.buffer
    # Text traveled by FILE: the stub got the answer via input.txt and
    # the argv carries only paths + the opaque id (contract v2).
    assert ANSWER in (control / "input.txt").read_text(encoding="utf-8")
    argv = " ".join(json.loads((control / "argv.json").read_text()))
    assert ANSWER not in argv
    assert "--render-text-segmented" in argv and speech_id in argv

    # Release the stub: remaining segments + terminal manifest flow in.
    (control / "release").write_text("go")
    assert wait_until(lambda: registry.get(speech_id).phase == "complete")
    job.producer.thread.join(timeout=5)
    assert not job.producer.thread.is_alive()
    assert (control / "finished").exists()
    # All segments moved, in order, staged on the job's buffer.
    assert [seg["seq"] for seg in job.segments.snapshot()] == [0, 1, 2]
    for seq in range(3):
        assert (audio / f"sr-{speech_id}-{seq:04d}.mp3").exists()
    assert registry.get(speech_id).phase == PHASE_COMPLETE


def test_producer_blocks_bounded_then_degrades(tmp_path, monkeypatch):
    """A full buffer that nothing drains blocks only SPEECH_PRODUCER_BLOCK_S,
    then the job degrades: the subprocess is terminated, exactly the
    buffered segments exist, nothing appears afterwards, and the job
    stays terminal."""
    monkeypatch.setattr(speech_mod, "SPEECH_PRODUCER_BLOCK_S", 0.3)
    monkeypatch.setattr(speech_mod, "SPEECH_SEGMENT_BUFFER", 2)
    control = tmp_path / "control"
    control.mkdir()
    audio = tmp_path / "a"
    monkeypatch.setenv("SR_STUB_CONTROL", str(control))
    monkeypatch.setenv("SR_STUB_MODE", "flood")
    settings = Settings(**{
        **SETTINGS_KWARGS,
        "tts_bin": str(write_segmented_stub(tmp_path)),
        "audio_dir": str(audio),
    })
    registry = SpeechRegistry()
    client = TestClient(make_segmented_app(settings, registry))
    speech_id = "vs24.flood.0001"

    resp = client.post("/ask", json=identified_body(speech_id))
    assert resp.status_code == 200
    assert resp.json()["speech"] == {"id": speech_id, "status": "delivering"}
    job = registry.get(speech_id)

    assert wait_until(lambda: registry.get(speech_id).phase == "degraded")
    job.producer.thread.join(timeout=5)
    assert not job.producer.thread.is_alive()
    # Exactly capacity-many segments were moved — the flood stopped there.
    assert sorted(p.name for p in audio.glob("sr-*.mp3")) == [
        f"sr-{speech_id}-0000.mp3",
        f"sr-{speech_id}-0001.mp3",
    ]
    # The bounded buffer never exceeded its cap (high-water mark).
    assert job.segments.peak <= 2
    assert len(job.segments) == 2
    # The subprocess got the teardown signal (stub's TERM marker).
    assert wait_until(lambda: (control / "terminated").exists())
    # Terminal and honest: degraded with a timestamp, never reopened.
    final = registry.get(speech_id)
    assert final.phase == "degraded"
    assert final.terminal_ts is not None
    # Stability: no further segments appear after the degrade.
    stable = sorted(p.name for p in audio.glob("sr-*.mp3"))
    time.sleep(0.4)  # bounded stability window
    assert sorted(p.name for p in audio.glob("sr-*.mp3")) == stable


def test_v1_host_identified_turn_marks_degraded(tmp_path):
    """v1-only host (the session conftest stub: no capabilities flag):
    an identified turn still gets the ONE full-file legacy render with
    audio, plus the contract's visible degraded marker."""
    audio = tmp_path / "a"
    settings = Settings(**{**SETTINGS_KWARGS, "audio_dir": str(audio)})
    tts = FakeTTS()
    registry = SpeechRegistry()
    client = TestClient(create_app(
        settings=settings,
        llm_factory=lambda _cfg, _tools: FakeLLM(),
        tts_renderer=tts,
        watcher=AgentWatcher(settings, herdr=StubHerdr(), tts_renderer=None),
        daemon_probe=lambda: "up",
        speech_registry=registry,
    ))
    speech_id = "vs24.v1host.001"
    resp = client.post("/ask", json=identified_body(speech_id))
    body = resp.json()
    assert resp.status_code == 200
    assert body["audio_url"].startswith("/audio/")  # legacy full-file render
    assert len(tts.calls) == 1 and tts.calls[0]["text"] == ANSWER  # ONE render
    # contracts/tts-brain-v2.md Negotiation: v1-only host on an
    # identified turn ⇒ legacy path + visible degraded marker.
    assert body["speech"] == {
        "id": speech_id,
        "status": "complete",
        "degraded": SEGMENTED_UNAVAILABLE,
    }
    assert registry.get(speech_id).phase == PHASE_COMPLETE


# -- VS2.4: producer unit matrix (deterministic, no subprocesses) --------
# The producer's runner/child seams drive every terminal branch
# synchronously: _run() is invoked DIRECTLY (no thread), the host world
# is a static manifest materialized at launch, and tts._terminate_job is
# doubled (the real TERM→KILL grace is tts.py's, tested in test_tts.py).


class FakeChild:
    """Popen-like handle: dead or alive at will; terminate/kill/wait reap."""

    def __init__(self, rc=0, alive=False):
        self.pid = 999_001  # bogus pgid: any real signal would ESRCH
        self._rc = None if alive else rc

    def poll(self):
        return self._rc

    def terminate(self):
        self._rc = -15

    def kill(self):
        self._rc = -9

    def wait(self, timeout=None):
        if self._rc is None:  # a real wait blocks; the fake just reaps
            self._rc = -15
        return self._rc


class ScriptedHost:
    """Runner double: materializes a STATIC manifest world into the
    producer's temp out-dir at launch, returns a canned child. The text
    discipline (input.txt by FILE) is captured for assertions."""

    def __init__(
        self,
        segs=(),
        complete=False,
        cancelled=False,
        error=None,
        rc=0,
        alive=False,
        raw_manifest=None,
        omit_manifest=False,
        omit_seg_files=(),
    ):
        self.segs = list(segs)
        self.complete = complete
        self.cancelled = cancelled
        self.error = error
        self.rc = rc
        self.alive = alive
        self.raw_manifest = raw_manifest
        self.omit_manifest = omit_manifest
        self.omit_seg_files = set(omit_seg_files)
        self.launch_calls: list = []
        self.input_text: str = ""
        self.child: FakeChild = None

    def __call__(self, cmd):
        self.launch_calls.append(list(cmd))
        out_dir = Path(cmd[2])
        self.out_dir = out_dir
        self.input_text = Path(cmd[3]).read_text(encoding="utf-8")
        for seq in self.segs:
            if seq not in self.omit_seg_files:
                (out_dir / f"seg-{seq:04d}.mp3").write_bytes(b"ID3")
        if self.raw_manifest is not None:
            (out_dir / "manifest.json").write_text(self.raw_manifest)
        elif not self.omit_manifest:
            payload = {
                "speech_request_id": cmd[cmd.index("--speech-request-id") + 1],
                "voice": "elvira",
                "rate": "+0%",
                "revision": len(self.segs),
                "is_complete": self.complete,
                "cancelled": self.cancelled,
                "error": self.error,
                "segments": [
                    {"seq": seq, "file": f"seg-{seq:04d}.mp3", "bytes": 4}
                    for seq in self.segs
                ],
            }
            (out_dir / "manifest.json").write_text(json.dumps(payload))
        self.child = FakeChild(rc=self.rc, alive=self.alive)
        return self.child


class ExplodingHost:
    """Runner double modeling an unlaunchable binary."""

    def __call__(self, cmd):
        raise OSError("cannot execute")


@pytest.fixture
def fake_terminate(monkeypatch):
    """Doubles speech._terminate_job: records the teardown, no real
    signals/grace (the real teardown contract lives in test_tts.py)."""
    terminated: list = []

    def _term(child):
        terminated.append(child)
        child.terminate()
        child.wait()

    monkeypatch.setattr(speech_mod, "_terminate_job", _term)
    return terminated


def drive_producer(tmp_path, host, text=ANSWER, job=None, timeout_s=None):
    """Runs one producer SYNCHRONOUSLY against a scripted host."""
    overrides = {"audio_dir": str(tmp_path / "a")}
    if timeout_s is not None:
        overrides["tts_timeout_s"] = timeout_s
    settings = Settings(**{**SETTINGS_KWARGS, **overrides})
    registry = SpeechRegistry()
    if job is None:
        job = registry.register("vs24.unit.0001", "tok", "s1")
    producer = speech_mod.SegmentedSpeechProducer(
        settings, registry, job, text, runner=host
    )
    producer._run()  # direct call: deterministic, no thread
    return producer, registry, job


def test_producer_unit_happy_path_completes(tmp_path):
    host = ScriptedHost(segs=[0, 1], complete=True, rc=0)
    producer, _registry, job = drive_producer(tmp_path, host)
    assert job.phase == "complete"
    assert [s["seq"] for s in producer.buffer.snapshot()] == [0, 1]
    audio = tmp_path / "a"
    assert (audio / f"sr-{job.id}-0000.mp3").exists()
    assert (audio / f"sr-{job.id}-0001.mp3").exists()
    assert not host.out_dir.exists()  # drained → scratch dir removed
    # Text discipline: sanitized answer in input.txt, never in argv.
    assert host.input_text == ANSWER
    assert ANSWER not in " ".join(host.launch_calls[0])
    assert "--render-text-segmented" in host.launch_calls[0]


def test_producer_unit_gap_degrades(tmp_path, fake_terminate):
    host = ScriptedHost(segs=[0, 2])  # seq 1 never published: a hole
    producer, _registry, job = drive_producer(tmp_path, host)
    assert job.phase == "degraded"
    assert [s["seq"] for s in producer.buffer.snapshot()] == [0]
    assert len(fake_terminate) == 1  # the render was torn down


def test_producer_unit_hard_cap_degrades(tmp_path, monkeypatch, fake_terminate):
    monkeypatch.setattr(speech_mod, "SPEECH_MAX_SEGMENTS", 2)
    host = ScriptedHost(segs=[0, 1, 2], complete=True)
    producer, _registry, job = drive_producer(tmp_path, host)
    assert job.phase == "degraded"
    assert [s["seq"] for s in producer.buffer.snapshot()] == [0, 1]
    assert len(fake_terminate) == 1


def test_producer_unit_error_manifest_fails_drained(tmp_path):
    host = ScriptedHost(segs=[0], error="engine exploded", rc=1)
    _producer, _registry, job = drive_producer(tmp_path, host)
    assert job.phase == "failed"
    assert not host.out_dir.exists()  # every published segment was moved


def test_producer_unit_failed_keeps_unmoved_evidence(tmp_path):
    # The manifest publishes two segments but seg-0001.mp3 never lands:
    # the move retries transiently, then the exited child ends the job
    # failed — and the scratch dir KEEPS the unprocessed segment.
    host = ScriptedHost(
        segs=[0, 1], complete=True, rc=0, omit_seg_files=(1,)
    )
    producer, _registry, job = drive_producer(tmp_path, host)
    assert job.phase == "failed"
    assert [s["seq"] for s in producer.buffer.snapshot()] == [0]
    assert host.out_dir.exists()
    assert (host.out_dir / "manifest.json").exists()


def test_producer_unit_host_cancel_without_ours_degrades(tmp_path, fake_terminate):
    # cancelled:true terminal manifest while the host is STILL alive:
    # teardown fires (alive child) and, without OUR cancel mark, the
    # honest outcome is degraded — never a silent "cancelled".
    host = ScriptedHost(cancelled=True, alive=True)
    _producer, _registry, job = drive_producer(tmp_path, host)
    assert job.phase == "degraded"
    assert len(fake_terminate) == 1  # live child was torn down


def test_producer_unit_rc3_with_our_cancel_is_cancelled(tmp_path):
    registry = SpeechRegistry()
    job = registry.register("vs24.unit.cl1", "tok", "s1")
    job.cancel_requested = True  # the brain asked before rc 3 landed
    host = ScriptedHost(segs=[0], cancelled=True, rc=3)
    producer = speech_mod.SegmentedSpeechProducer(
        Settings(**{**SETTINGS_KWARGS, "audio_dir": str(tmp_path / "a")}),
        registry, job, ANSWER, runner=host,
    )
    producer._run()
    assert job.phase == "cancelled"
    assert job.terminal_ts is not None


def test_producer_unit_cancel_event_mid_render(tmp_path, fake_terminate):
    registry = SpeechRegistry()
    job = registry.register("vs24.unit.cx1", "tok", "s1")
    job.cancel_event.set()  # a cancel lands while the render runs
    host = ScriptedHost(alive=True)  # never publishes anything
    producer = speech_mod.SegmentedSpeechProducer(
        Settings(**{**SETTINGS_KWARGS, "audio_dir": str(tmp_path / "a")}),
        registry, job, ANSWER, runner=host,
    )
    producer._run()
    assert job.phase == "cancelled"
    assert len(fake_terminate) == 1
    assert not host.out_dir.exists()
    assert producer.buffer.snapshot() == []


def test_producer_unit_early_failure_no_manifest(tmp_path):
    host = ScriptedHost(omit_manifest=True, rc=7)  # died before publishing
    _producer, _registry, job = drive_producer(tmp_path, host)
    assert job.phase == "failed"


def test_producer_unit_garbage_manifest_fails_not_crashes(tmp_path):
    host = ScriptedHost(raw_manifest="not json{", rc=0)
    _producer, _registry, job = drive_producer(tmp_path, host)
    assert job.phase == "failed"


def test_producer_unit_malformed_segments_keep_polling_then_fail(tmp_path):
    # Non-list segments and malformed entries are TRANSIENT garbage:
    # never a crash, never a false process — the exited child ends failed.
    host = ScriptedHost(raw_manifest='{"segments": [{"nope": 1}]}', rc=0)
    _producer, _registry, job = drive_producer(tmp_path, host)
    assert job.phase == "failed"


def test_producer_unit_segments_not_a_list_is_transient(tmp_path):
    host = ScriptedHost(raw_manifest='{"segments": "nope"}', rc=0)
    _producer, _registry, job = drive_producer(tmp_path, host)
    assert job.phase == "failed"


def test_producer_unit_empty_text_fails_without_launch(tmp_path):
    host = ScriptedHost(segs=[0], complete=True)
    _producer, _registry, job = drive_producer(tmp_path, host, text="   ")
    assert job.phase == "failed"
    assert host.launch_calls == []  # nothing was ever spawned


def test_producer_unit_launch_oserror_fails(tmp_path):
    _producer, _registry, job = drive_producer(tmp_path, ExplodingHost())
    assert job.phase == "failed"


def test_producer_unit_budget_expiry_fails_and_tears_down(
    tmp_path, monkeypatch, fake_terminate
):
    monkeypatch.setattr(speech_mod, "SEGMENT_POLL_S", 0.01)
    host = ScriptedHost(segs=[0], complete=False, alive=True)
    _producer, _registry, job = drive_producer(
        tmp_path, host, timeout_s=1  # never completes, never exits
    )
    assert job.phase == "failed"
    assert len(fake_terminate) == 1  # budget teardown reached the child


def test_producer_unit_marks_never_reopen_terminal_job(tmp_path):
    registry = SpeechRegistry()
    job = registry.register("vs24.unit.tx1", "tok", "s1")
    registry.mark(job.id, PHASE_COMPLETE)  # terminal BEFORE the producer
    terminal_ts = job.terminal_ts
    host = ScriptedHost(segs=[0], complete=True)
    producer = speech_mod.SegmentedSpeechProducer(
        Settings(**{**SETTINGS_KWARGS, "audio_dir": str(tmp_path / "a")}),
        registry, job, ANSWER, runner=host,
    )
    producer._run()
    # Every guarded mark was refused: the job stays exactly as it was.
    assert job.phase == "complete"
    assert job.terminal_ts == terminal_ts


def test_segment_buffer_ack_semantics():
    buffer = speech_mod.SegmentBuffer()
    for seq in range(3):
        assert buffer.add({"seq": seq, "file": f"s{seq}", "bytes": 1})
    assert len(buffer) == 3
    assert buffer.peak == 3
    assert buffer.ack_upto(0) == 1  # monotone contiguous release
    assert len(buffer) == 2
    assert buffer.ack_upto(0) == 0  # non-monotone: silently ignored
    assert [s["seq"] for s in buffer.snapshot()] == [1, 2]
    assert buffer.ack_upto(2) == 2  # release everything
    assert len(buffer) == 0
    assert buffer.snapshot() == []
    # Staging below the old high-water mark exercises the non-peak add.
    assert buffer.add({"seq": 3, "file": "s3", "bytes": 1})
    assert buffer.peak == 3 and len(buffer) == 1
