"""Dual-watermark long-poll transport for segmented speech (VS2.5, T5).

GET /speech/{id}/next?after&ack&session_id serves the STRICTLY-AFTER
cursor segment from the registry: seq after+1 and nothing else, so an
acked seq can never be re-served (dedup is inherent). ``ack`` is the
playback watermark — the ONLY thing that releases buffer capacity,
monotone by definition; every violation answers a TYPED 422. Active
jobs with nothing ready long-poll a bounded hold; terminal jobs answer
their status IMMEDIATELY (T5: next() returns the state, never a silent
skip). A connection-level abort cancels NOTHING — only the cancel
endpoint may — and a vanished consumer is swept to expired-unconsumed
on the next touch (lazy, no reaper thread).

Every end-to-end test rides a REAL protocol-2 python3 stub host
(per-test, env-controlled) exactly like tests/test_speech_dispatch.py.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from herdr_brain import speech as speech_mod
from herdr_brain import tts as tts_mod
from herdr_brain.config import Settings
from herdr_brain.server import create_app
from herdr_brain.speech import (
    PHASE_COMPLETE,
    PHASE_DELIVERING,
    PHASE_EXPIRED_UNCONSUMED,
    SpeechRegistry,
    sweep_unconsumed,
)
from herdr_brain.watcher import AgentWatcher
from tests.conftest import SETTINGS_KWARGS, StubHerdr
from tests.test_speech_cancel import FakeLLM, cancel_body, speech_body


@pytest.fixture(autouse=True)
def fresh_probe_cache():
    tts_mod.reset_capabilities_cache()
    yield
    tts_mod.reset_capabilities_cache()


def wait_until(predicate, timeout=10.0, interval=0.02) -> bool:
    """Bounded poll: never a bare sleep-only assert."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# A protocol-2 herdr-tts stub (python3, per-test): answers the probe
# flags and implements --render-text-segmented with atomic manifest
# re-publishes. Behavior is env-controlled:
#   SR_STUB_CONTROL — dir where the stub drops control/marker files
#   SR_STUB_MODE    - "gated":  publish seg 0, BLOCK on `release`, then
#                               publish 1..2 + terminal manifest
#                   - "flood3": publish 0..2 (incomplete), stay alive
#                               until release/TERM (segments exist that
#                               a later cancel must strand unserved)
#                   - "gap":    publish 0,1 then 3 (seq 2 skipped) —
#                               the producer must degrade visibly
#                   - "drip":   SR_STUB_TOTAL segments one by one, each
#                               gated on `release-%04d` (progressive
#                               pressure released by per-seq acks)
_NEXT_STUB = '''#!/usr/bin/env python3
import json, os, signal, sys, time

CONTROL = os.environ.get("SR_STUB_CONTROL", "/tmp")
MODE = os.environ.get("SR_STUB_MODE", "gated")
TOTAL = int(os.environ.get("SR_STUB_TOTAL", "12"))
RID = "unset"


def segs(upto, skip=()):
    return [i for i in range(upto + 1) if i not in skip]


def publish(out_dir, upto_seq, complete, skip=()):
    for seq in segs(upto_seq, skip):
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
            for i in segs(upto_seq, skip)
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
    if MODE == "gated":
        publish(out_dir, 0, complete=False)
        mark("first")
        wait_for(os.path.join(CONTROL, "release"), 20)
        publish(out_dir, 1, complete=False)
        publish(out_dir, 2, complete=True)
        mark("finished")
        sys.exit(0)
    if MODE == "flood3":
        publish(out_dir, 2, complete=False)
        mark("first")
        wait_for(os.path.join(CONTROL, "release"), 20)
        sys.exit(0)
    if MODE == "gap":
        publish(out_dir, 3, complete=False, skip=(2,))
        mark("first")
        wait_for(os.path.join(CONTROL, "release"), 20)
        sys.exit(0)
    if MODE == "drip":
        for seq in range(TOTAL):
            publish(out_dir, seq, complete=False)
            mark("published-%04d" % seq)
            wait_for(os.path.join(CONTROL, "release-%04d" % seq), 30)
        publish(out_dir, TOTAL - 1, complete=True)
        mark("finished")
        sys.exit(0)
sys.exit(2)
'''


def write_next_stub(tmp_path: Path) -> Path:
    stub = tmp_path / "herdr-tts-next"
    stub.write_text(_NEXT_STUB)
    stub.chmod(0o755)
    return stub


def make_app(settings, registry):
    return create_app(
        settings=settings,
        llm_factory=lambda _cfg, _tools: FakeLLM(),
        watcher=AgentWatcher(settings, herdr=StubHerdr(), tts_renderer=None),
        daemon_probe=lambda: "up",
        speech_registry=registry,
    )


def next_url(speech_id, after, ack, session_id="s1"):
    return (
        f"/speech/{speech_id}/next?after={after}&ack={ack}"
        f"&session_id={session_id}"
    )


def start_job(tmp_path, monkeypatch, mode, speech_id, total=None):
    """Spawns one segmented turn over the REAL stub: returns
    (client, registry, control, audio) with the job already delivering."""
    control = tmp_path / "control"
    control.mkdir()
    audio = tmp_path / "a"
    monkeypatch.setenv("SR_STUB_CONTROL", str(control))
    monkeypatch.setenv("SR_STUB_MODE", mode)
    if total is not None:
        monkeypatch.setenv("SR_STUB_TOTAL", str(total))
    settings = Settings(**{
        **SETTINGS_KWARGS,
        "tts_bin": str(write_next_stub(tmp_path)),
        "audio_dir": str(audio),
    })
    registry = SpeechRegistry()
    client = TestClient(make_app(settings, registry))
    resp = client.post("/ask", json=speech_body(speech_request_id=speech_id))
    assert resp.status_code == 200
    assert resp.json()["speech"] == {"id": speech_id, "status": "delivering"}
    return client, registry, control, audio


def drain_stub(job, control, registry, timeout=5.0):
    """Releases a gated stub and joins its producer thread (hygiene)."""
    (control / "release").write_text("go")
    assert wait_until(lambda: registry.get(job.id).phase == PHASE_COMPLETE)
    job.producer.thread.join(timeout=timeout)
    assert not job.producer.thread.is_alive()


# -- 1. cursor semantics ------------------------------------------------------


def test_cursor_dual_watermark_resume_no_dup(tmp_path, monkeypatch):
    """Progressive after/ack consumption, then TWO identical re-polls
    (reconnect simulation): every serve is exactly after+1, the re-poll
    replays the same response (fine), resume continues strictly after,
    and audio_url names the sr-<id>-<seq:04d>.mp3 files."""
    speech_id = "vs25.resume.0001"
    client, registry, control, audio = start_job(tmp_path, monkeypatch, "gated", speech_id)
    job = registry.get(speech_id)
    assert wait_until(lambda: len(job.segments) >= 1)

    r0 = client.get(next_url(speech_id, -1, -1)).json()
    assert r0 == {
        "seq": 0,
        "audio_url": f"/audio/sr-{speech_id}-0000.mp3",
        "is_final": False,
        "mime": "audio/mpeg",
    }
    (control / "release").write_text("go")
    assert wait_until(lambda: registry.get(speech_id).phase == PHASE_COMPLETE)
    job.producer.thread.join(timeout=5)

    r1 = client.get(next_url(speech_id, 0, 0)).json()
    assert r1["seq"] == 1 and r1["is_final"] is False
    # Reconnect with the SAME watermarks: the SAME response replays —
    # duplicated HTTP delivery of one seq is allowed, a BEYOND-cursor
    # rewind never happens.
    dup = client.get(next_url(speech_id, 0, 0)).json()
    assert dup == r1
    r2 = client.get(next_url(speech_id, 1, 1)).json()
    assert r2 == {
        "seq": 2,
        "audio_url": f"/audio/sr-{speech_id}-0002.mp3",
        "is_final": True,
        "mime": "audio/mpeg",
    }
    # Everything consumed: terminal complete answers the wait shape.
    done = client.get(next_url(speech_id, 2, 2)).json()
    assert done == {"wait": True, "status": PHASE_COMPLETE}

    served = [r0["seq"], r1["seq"], dup["seq"], r2["seq"]]
    assert served == [0, 1, 1, 2]  # the only duplicate is the replay
    distinct = []
    for seq in served:
        if not distinct or seq > distinct[-1]:
            distinct.append(seq)
    assert distinct == [0, 1, 2]  # monotone strictly-after resume
    for seq in distinct:
        assert (audio / f"sr-{speech_id}-{seq:04d}.mp3").exists()
    assert job.ack_watermark == 2


# -- 2/3. ack watermark semantics ---------------------------------------------


def test_ack_frees_buffer_monotone_only(tmp_path, monkeypatch):
    """>cap published with the producer gated at the cap: only a valid
    MONOTONE ack releases capacity (backwards ack and ack>after are
    typed 422s, an identical re-ack releases nothing), and the released
    pressure lets the producer stage past the initial cap without
    degrading."""
    monkeypatch.setattr(speech_mod, "SPEECH_SEGMENT_BUFFER", 2)
    speech_id = "vs25.ackfree.001"
    client, registry, control, audio = start_job(tmp_path, monkeypatch, "flood3", speech_id)
    job = registry.get(speech_id)
    # 0,1 staged; the producer is blocked adding seq 2 at the cap.
    assert wait_until(lambda: len(job.segments) == 2)

    # ack > after: typed 422, job untouched (no release, no phase move).
    bad = client.get(next_url(speech_id, -1, 0))
    assert bad.status_code == 422
    assert bad.json()["detail"]["error"] == "invalid-ack"
    assert len(job.segments) == 2 and job.ack_watermark == -1

    # Valid monotone ack: serves seq 1 (after+1) AND releases seq 0 —
    # the blocked producer immediately stages seq 2.
    first = client.get(next_url(speech_id, 0, 0))
    assert first.status_code == 200 and first.json()["seq"] == 1
    assert job.ack_watermark == 0
    assert wait_until(lambda: len(job.segments) == 2)  # [1, 2] now

    # ack backwards vs the job watermark: typed 422.
    back = client.get(next_url(speech_id, 0, -1))
    assert back.status_code == 422
    assert back.json()["detail"] == {
        "error": "invalid-ack", "ack": -1, "after": 0, "job_ack": 0,
    }

    # Double IDENTICAL ack: 200, the same response, releases NOTHING.
    stable = job.segments.snapshot()
    again = client.get(next_url(speech_id, 0, 0))
    assert again.status_code == 200 and again.json() == first.json()
    assert job.segments.snapshot() == stable
    assert job.ack_watermark == 0

    # Next monotone ack keeps freeing capacity: the producer staged 3
    # segments (> cap 2) and the job never degraded.
    r2 = client.get(next_url(speech_id, 1, 1))
    assert r2.status_code == 200 and r2.json()["seq"] == 2
    assert job.ack_watermark == 1
    assert [s["seq"] for s in job.segments.snapshot()] == [2]
    assert job.phase == PHASE_DELIVERING
    assert job.segments.peak <= 2
    for seq in range(3):
        assert (audio / f"sr-{speech_id}-{seq:04d}.mp3").exists()

    # Cleanup: cancel tears the still-alive stub down (real endpoint).
    resp = client.post(f"/speech/{speech_id}/cancel", json=cancel_body())
    assert resp.json() == {"status": "cancelled"}
    assert wait_until(lambda: registry.get(speech_id).phase == "cancelled")
    job.producer.thread.join(timeout=5)
    assert not job.producer.thread.is_alive()
    assert wait_until(lambda: (control / "terminated").exists())


def test_impossible_ack_typed_422(tmp_path, monkeypatch):
    """The exact typed body is pinned for both impossible directions
    (ack>after, ack<job_ack); the job stays untouched by each refusal."""
    monkeypatch.setattr(speech_mod, "SPEECH_NEXT_HOLD_S", 0.1)
    speech_id = "vs25.typed422.001"
    client, registry, control, audio = start_job(tmp_path, monkeypatch, "gated", speech_id)
    job = registry.get(speech_id)
    assert wait_until(lambda: len(job.segments) == 1)

    # ack > after — typed, with all three watermarks in the body.
    r = client.get(next_url(speech_id, -1, 5))
    assert r.status_code == 422
    assert r.json()["detail"] == {
        "error": "invalid-ack", "ack": 5, "after": -1, "job_ack": -1,
    }
    assert job.phase in ("rendering", "delivering")
    assert [s["seq"] for s in job.segments.snapshot()] == [0]
    assert job.ack_watermark == -1

    # Advance the watermark through a bounded hold (nothing at after+1):
    # the ack applies BEFORE the serve decision, the hold times out.
    held = client.get(next_url(speech_id, 0, 0))
    assert held.status_code == 200
    assert held.json() == {"wait": True}  # active hold: no status field
    assert job.ack_watermark == 0
    assert len(job.segments) == 0  # seq 0 released by the valid ack

    # ack < job_ack — typed, job still untouched by the refusal.
    r2 = client.get(next_url(speech_id, 0, -1))
    assert r2.status_code == 422
    assert r2.json()["detail"] == {
        "error": "invalid-ack", "ack": -1, "after": 0, "job_ack": 0,
    }
    assert job.ack_watermark == 0 and len(job.segments) == 0
    assert job.phase in ("rendering", "delivering")

    drain_stub(job, control, registry)


# -- 4/5. terminal honesty -----------------------------------------------------


def test_gap_degrades_visibly(tmp_path, monkeypatch):
    """A stub that skips seq 2 degrades the job; next() then answers
    IMMEDIATELY {"wait": true, "status": "degraded"} — no hold, no
    silent skip, seq 3 never served."""
    monkeypatch.setattr(speech_mod, "SPEECH_NEXT_HOLD_S", 3.0)
    speech_id = "vs25.gapdegraded.1"
    client, registry, control, audio = start_job(tmp_path, monkeypatch, "gap", speech_id)
    job = registry.get(speech_id)
    assert wait_until(lambda: registry.get(speech_id).phase == "degraded")
    job.producer.thread.join(timeout=5)
    # The contiguity hole stranded exactly the pre-gap segments.
    assert [s["seq"] for s in job.segments.snapshot()] == [0, 1]

    start = time.monotonic()
    r = client.get(next_url(speech_id, -1, -1))
    elapsed = time.monotonic() - start
    assert r.status_code == 200
    assert r.json() == {"wait": True, "status": "degraded"}
    assert elapsed < 1.0  # terminal: answered at once, never held
    assert "seq" not in r.json()  # seq 0 sits in the buffer, unserved


def test_cancel_terminal_no_new_segments(tmp_path, monkeypatch):
    """After a real cancel, next() answers {"wait": true, "status":
    "cancelled"} immediately and NEVER serves another segment — even
    though 1 and 2 were published and staged before the cancel."""
    monkeypatch.setattr(speech_mod, "SPEECH_NEXT_HOLD_S", 3.0)
    speech_id = "vs25.cancelnext.01"
    client, registry, control, audio = start_job(tmp_path, monkeypatch, "flood3", speech_id)
    job = registry.get(speech_id)
    assert wait_until(
        lambda: [s["seq"] for s in job.segments.snapshot()] == [0, 1, 2]
    )
    first = client.get(next_url(speech_id, -1, -1))
    assert first.status_code == 200 and first.json()["seq"] == 0

    resp = client.post(f"/speech/{speech_id}/cancel", json=cancel_body())
    assert resp.status_code == 200
    assert resp.json() == {"status": "cancelled"}
    assert wait_until(lambda: registry.get(speech_id).phase == "cancelled")
    job.producer.thread.join(timeout=5)
    assert wait_until(lambda: (control / "terminated").exists())

    start = time.monotonic()
    r1 = client.get(next_url(speech_id, 0, 0))  # seq 1 IS in the buffer…
    assert r1.json() == {"wait": True, "status": "cancelled"}
    r2 = client.get(next_url(speech_id, 1, 1))  # …and seq 2: never served
    assert r2.json() == {"wait": True, "status": "cancelled"}
    assert time.monotonic() - start < 1.0  # immediate, no hold


# -- 6. connection abandonment --------------------------------------------------


def test_client_abort_does_not_cancel(tmp_path, monkeypatch):
    """A next() hold that times out with nothing ready answers
    {"wait": true} and cancels NOTHING: the job stays active, the
    cancel_event is unset, the producer thread lives. Connection-level
    abandonment is not a job cancel — only POST .../cancel is."""
    monkeypatch.setattr(speech_mod, "SPEECH_NEXT_HOLD_S", 0.15)
    speech_id = "vs25.aborthold.001"
    client, registry, control, audio = start_job(tmp_path, monkeypatch, "gated", speech_id)
    job = registry.get(speech_id)
    assert wait_until(lambda: len(job.segments) >= 1)

    start = time.monotonic()
    r = client.get(next_url(speech_id, 0, 0))  # seq 1 is gated: nothing ready
    assert r.status_code == 200
    assert r.json() == {"wait": True}
    assert time.monotonic() - start >= 0.1  # it genuinely held

    assert job.phase in ("rendering", "delivering")
    assert not job.cancel_event.is_set()
    assert job.producer.thread.is_alive()
    drain_stub(job, control, registry)


# -- 7. unconsumed sweep ---------------------------------------------------------


class FakeClock:
    """Controls speech_mod._monotonic for deterministic sweep aging."""

    def __init__(self, start=1000.0):
        self.now = start


def test_disconnected_consumer_finite_unconsumed_timeout(tmp_path, monkeypatch):
    """A delivering job with unacked buffered segments, aged past
    SPEECH_UNCONSUMED_TIMEOUT_S, is swept to expired-unconsumed by the
    NEXT touch — even a next() call for ANOTHER (unknown) job. A
    fully-acked complete job is NOT swept, and the swept job never
    reopens: it answers its terminal state forever."""
    monkeypatch.setattr(speech_mod, "SPEECH_UNCONSUMED_TIMEOUT_S", 1.0)
    clock = FakeClock()
    monkeypatch.setattr(speech_mod, "_monotonic", lambda: clock.now)
    registry = SpeechRegistry()
    client = TestClient(make_app(Settings(**SETTINGS_KWARGS), registry))

    # A: delivering, one unacked staged segment, consumer vanished.
    job_a = registry.register("vs25.sweep.a00001", "tok", "s1")
    registry.mark(job_a.id, PHASE_DELIVERING)
    buffer_a = speech_mod.SegmentBuffer()
    job_a.segments = buffer_a
    buffer_a.add({"seq": 0, "file": "sr-a-0000.mp3", "bytes": 1})
    # C: complete with everything acked — retention's to reclaim.
    job_c = registry.register("vs25.sweep.c00001", "tok", "s1")
    registry.mark(job_c.id, PHASE_COMPLETE)
    buffer_c = speech_mod.SegmentBuffer()
    job_c.segments = buffer_c
    buffer_c.add({"seq": 0, "file": "sr-c-0000.mp3", "bytes": 1})
    job_c.ack_watermark = 0
    assert buffer_c.ack_upto(0) == 1  # fully acked: buffer now empty

    clock.now += 2.0  # both aged past the (patched) timeout
    # The touch is a next() call for ANOTHER job — the sweep at entry
    # fires before the lookup, so even this 404 touch sweeps A.
    r = client.get(next_url("vs25.sweep.none01", -1, -1))
    assert r.status_code == 404
    assert sweep_unconsumed(registry, now=clock.now) == 0  # idempotent
    assert registry.get(job_a.id).phase == PHASE_EXPIRED_UNCONSUMED
    assert registry.get(job_a.id).terminal_ts is not None
    assert registry.get(job_c.id).phase == PHASE_COMPLETE

    # Visible terminal, no reopen — and the unacked seq 0 never serves.
    again = client.get(next_url(job_a.id, -1, -1))
    assert again.status_code == 200
    assert again.json() == {"wait": True, "status": "expired-unconsumed"}


# -- 8. sustained pressure --------------------------------------------------------


def test_gt8_segments_pressure_released_by_acks(tmp_path, monkeypatch):
    """12 progressively published segments consumed one-by-one through
    next+ack: all arrive IN ORDER, the buffer peak never exceeds
    SPEECH_SEGMENT_BUFFER, the job completes, is_final is true ONLY on
    the last seq, and the final ack lands on job.ack_watermark."""
    speech_id = "vs25.drip12.00001"
    client, registry, control, audio = start_job(
        tmp_path, monkeypatch, "drip", speech_id, total=12
    )
    job = registry.get(speech_id)

    received: list[int] = []
    for seq in range(11):  # 0..10 while the job is still active
        target = audio / f"sr-{speech_id}-{seq:04d}.mp3"
        assert wait_until(target.exists), f"seq {seq} never staged"
        r = client.get(next_url(speech_id, seq - 1, seq - 1)).json()
        assert r["seq"] == seq
        assert r["is_final"] is False  # active job: more always coming
        assert r["audio_url"] == f"/audio/sr-{speech_id}-{seq:04d}.mp3"
        received.append(r["seq"])
        (control / f"release-{seq:04d}").write_text("go")  # stage the next

    # Last segment: let the job complete FIRST, then consume it.
    (control / "release-0011").write_text("go")
    assert wait_until(lambda: registry.get(speech_id).phase == PHASE_COMPLETE)
    job.producer.thread.join(timeout=5)
    last = client.get(next_url(speech_id, 10, 10)).json()
    assert last == {
        "seq": 11,
        "audio_url": f"/audio/sr-{speech_id}-0011.mp3",
        "is_final": True,
        "mime": "audio/mpeg",
    }
    received.append(11)
    assert received == list(range(12))
    assert job.segments.peak <= speech_mod.SPEECH_SEGMENT_BUFFER
    assert job.ack_watermark == 10
    drained = client.get(next_url(speech_id, 11, 11)).json()
    assert drained == {"wait": True, "status": PHASE_COMPLETE}
    assert job.ack_watermark == 11  # final ack recorded


# -- focused extras: uncovered transport branches ---------------------------------


def test_next_unknown_id_404_no_enumeration():
    """Unknown ids get the honest 404 — same shape for every reason."""
    registry = SpeechRegistry()
    client = TestClient(make_app(Settings(**SETTINGS_KWARGS), registry))
    r = client.get(next_url("vs25.never.00001", -1, -1))
    assert r.status_code == 404
    assert r.json()["detail"] == "speech job not found"


def test_next_session_mismatch_404_same_shape(tmp_path, monkeypatch):
    """Session binding is ROUTING (PRD 02 §2C), not auth: a mismatched
    session_id gets the unknown-id 404; an absent or matching one
    serves normally."""
    speech_id = "vs25.sessionbind1"
    client, registry, control, audio = start_job(tmp_path, monkeypatch, "gated", speech_id)
    assert wait_until(lambda: len(registry.get(speech_id).segments) >= 1)

    wrong = client.get(next_url(speech_id, -1, -1, session_id="other-session"))
    assert wrong.status_code == 404
    assert wrong.json()["detail"] == "speech job not found"  # same shape

    no_param = client.get(f"/speech/{speech_id}/next?after=-1&ack=-1")
    assert no_param.status_code == 200 and no_param.json()["seq"] == 0
    right = client.get(next_url(speech_id, -1, -1))
    assert right.status_code == 200 and right.json() == no_param.json()
    drain_stub(registry.get(speech_id), control, registry)


def test_next_hold_serves_when_segment_arrives(tmp_path, monkeypatch):
    """The long-poll hold actually WORKS: a segment published mid-hold
    is served during the hold, long before the hold deadline."""
    monkeypatch.setattr(speech_mod, "SPEECH_NEXT_HOLD_S", 5.0)
    speech_id = "vs25.holdserve.001"
    client, registry, control, audio = start_job(tmp_path, monkeypatch, "gated", speech_id)
    job = registry.get(speech_id)
    assert wait_until(lambda: len(job.segments) >= 1)
    assert client.get(next_url(speech_id, -1, -1)).json()["seq"] == 0

    timer = threading.Timer(0.25, lambda: (control / "release").write_text("go"))
    timer.start()
    start = time.monotonic()
    r = client.get(next_url(speech_id, 0, 0))  # holds until seq 1 appears
    elapsed = time.monotonic() - start
    assert r.status_code == 200 and r.json()["seq"] == 1
    assert elapsed < 2.0  # served DURING the hold, not by its timeout
    timer.join()
    assert wait_until(lambda: registry.get(speech_id).phase == PHASE_COMPLETE)
    job.producer.thread.join(timeout=5)


def test_next_job_without_buffer_waits_honestly(monkeypatch):
    """An identified job with no segments attached (legacy/v1 turn):
    /next never crashes on the missing buffer — it records the ack,
    holds bounded while active, and answers the terminal status."""
    monkeypatch.setattr(speech_mod, "SPEECH_NEXT_HOLD_S", 0.1)
    registry = SpeechRegistry()
    client = TestClient(make_app(Settings(**SETTINGS_KWARGS), registry))
    job = registry.register("vs25.legacy.nobuf1", "tok", "s1")
    registry.mark(job.id, PHASE_DELIVERING)  # segments stays None

    r = client.get(next_url(job.id, 0, 0))
    assert r.status_code == 200
    assert r.json() == {"wait": True}
    assert job.ack_watermark == 0  # ack recorded, nothing to release

    registry.mark(job.id, PHASE_COMPLETE)
    r2 = client.get(next_url(job.id, 0, 0))
    assert r2.json() == {"wait": True, "status": PHASE_COMPLETE}


def test_sweep_complete_with_unacked_is_guarded_noop(monkeypatch):
    """A COMPLETE job holding unacked segments is already terminal: the
    sweep's guarded mark is refused (no terminal→terminal reopen) and
    retention owns the cleanup — documented, honest, no crash."""
    monkeypatch.setattr(speech_mod, "SPEECH_UNCONSUMED_TIMEOUT_S", 1.0)
    clock = FakeClock()
    monkeypatch.setattr(speech_mod, "_monotonic", lambda: clock.now)
    reg_clock = FakeClock()
    registry = SpeechRegistry(clock=lambda: reg_clock.now)
    job = registry.register("vs25.sweep.done01", "tok", "s1")
    registry.mark(job.id, PHASE_DELIVERING)
    buffer = speech_mod.SegmentBuffer()
    job.segments = buffer
    buffer.add({"seq": 0, "file": "sr-d-0000.mp3", "bytes": 1})
    registry.mark(job.id, PHASE_COMPLETE)  # terminal_ts = reg clock
    terminal_ts = job.terminal_ts

    clock.now += 50.0  # unacked far past the timeout
    reg_clock.now += 50.0
    assert sweep_unconsumed(registry) == 0
    assert job.phase == PHASE_COMPLETE  # untouched: mark was refused
    assert job.terminal_ts == terminal_ts
