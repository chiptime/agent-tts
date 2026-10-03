"""PendingQueue — host pending-announcement arbiter (voice-stack VS3.1, T7;
dispatcher + completion mapping VS3.3; overflow two levels VS3.4; operator
CLI retry/resolve + list/status VS3.5; crash recovery VS3.7; regeneration
after GC VS3.8).

Every test injects a tmp db path and (where timing matters) a fake clock:
the real ``~/.local/state/herdr-tts/pending.db`` is NEVER touched and no test
depends on wall-clock time or physical SLOs (T11). Bounds are always passed
explicitly as constructor parameters, never as globals. The dispatcher's
externals (is_playing, the daemon enqueue seam) are always injectable
doubles: no test ever contacts a daemon or the real /tmp channel files.
Regeneration tests ONLY ever inject fake renderers (policy honesty, T7:
real renders obey the operator's explicit provider policy, never a test's).
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import pending_queue as pq


class FakeClock:
    """Injectable deterministic clock."""

    def __init__(self, start: float = 1000.0):
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, delta: float) -> None:
        self.now += delta


class RecordingEnqueue:
    """Injectable enqueue double: records every payload and answers with
    scripted replies (a string) or raises scripted exceptions."""

    def __init__(self, replies=None):
        self.calls = []
        self.replies = list(replies or [])

    def __call__(self, payload):
        self.calls.append(payload)
        if not self.replies:
            return "ok=true item=1 queue_len=0"
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def make_event(**overrides) -> dict:
    """A brain-shaped ambient transition event (PRD 03)."""
    event = {
        "pane_id": "P1",
        "agent": "codex",
        "status": "done",
        "label": "codex done",
        "text": "Task finished.",
        "pane_pid": 100,
    }
    event.update(overrides)
    return event


def make_audio(tmp_path, name: str, content: bytes = b"mp3"):
    """A non-empty audio file standing in for a rendered announcement."""
    path = tmp_path / name
    path.write_bytes(content)
    return path


def make_queue(db_path, clock=None, **overrides):
    """Queue with every bound passed EXPLICITLY (bounds are parameters, not globals)."""
    kwargs = {
        "max_records": pq.DEFAULT_MAX_RECORDS,
        "max_db_bytes": pq.DEFAULT_MAX_DB_BYTES,
        "consolidation_window_s": pq.DEFAULT_CONSOLIDATION_WINDOW_S,
    }
    if clock is not None:
        kwargs["clock"] = clock
    kwargs.update(overrides)
    return pq.PendingQueue(db_path, **kwargs)


# -- required behaviors ------------------------------------------------------------


def test_repeats_consolidate_metadata_only(tmp_path):
    clock = FakeClock()
    q = make_queue(tmp_path / "pending.db", clock=clock)
    first = q.admit(make_event())
    clock.advance(5)
    second = q.admit(make_event())
    clock.advance(10)
    third = q.admit(make_event())
    assert first["id"] == second["id"] == third["id"]
    records = q.list_all()
    assert len(records) == 1                       # ONE record, no new rows
    rec = records[0]
    assert rec["repeat_count"] == 3
    assert rec["first_seen_ts"] == 1000.0          # admission moment untouched
    assert rec["last_seen_ts"] == 1015.0           # advanced to the latest seen
    assert rec["state"] == "pending"               # metadata only, no state change


def test_busy_admits_pending_not_omitted(tmp_path):
    # The queue has NO busy concept: is_playing belongs to the Bash watcher
    # (VS3.2 wiring). Whatever is playing, admit ALWAYS records pending — the
    # historical omission at the Bash site was the caller's sin, not the queue's.
    clock = FakeClock()
    q = make_queue(tmp_path / "pending.db", clock=clock)
    rec = q.admit(make_event())
    assert rec["state"] == "pending"
    assert rec["audio_path"] is None               # null = regenerable from text
    assert [r["id"] for r in q.list_active()] == [rec["id"]]
    assert [r["id"] for r in q.list_all()] == [rec["id"]]
    other = q.admit(make_event(pane_id="P2", pane_pid=200))
    assert other["state"] == "pending"             # nothing suppressed by busy/backpressure
    assert len(q.list_active()) == 2


def test_pane_pid_change_expires_old(tmp_path):
    clock = FakeClock()
    q = make_queue(tmp_path / "pending.db", clock=clock)
    done_old = q.admit(make_event(status="done", pane_pid=100))
    blocked_old = q.admit(make_event(status="blocked", pane_pid=100))
    q.mark(done_old["id"], "announcing")
    assert q.mark(done_old["id"], "announced")["ok"] is True
    clock.advance(30)
    done_new = q.admit(make_event(status="done", pane_pid=999))  # new epoch
    # Old-epoch NON-TERMINAL record expired (visible, not reproducible)...
    assert q.get(blocked_old["id"])["state"] == "expired"
    # ...terminal old records are never touched.
    assert q.get(done_old["id"])["state"] == "announced"
    # The new-epoch record is pending under its own epoch.
    assert done_new["state"] == "pending"
    assert done_new["epoch"] == "pid-999"
    assert done_new["announce_seq"] == 2           # next of key (P1, done)
    # Expiry is pane-wide: the blocked key gets its own new-epoch record too.
    blocked_new = q.admit(make_event(status="blocked", pane_pid=999))
    assert blocked_new["announce_seq"] == 2
    assert q.get(blocked_old["id"])["state"] == "expired"


def test_bounds_are_parameters(tmp_path, monkeypatch):
    monkeypatch.delenv("PENDING_MAX_RECORDS", raising=False)
    clock = FakeClock()
    q = make_queue(tmp_path / "pending.db", clock=clock, max_records=2)
    first = q.admit(make_event(pane_id="P1", pane_pid=101))
    clock.advance(1)
    second = q.admit(make_event(pane_id="P2", pane_pid=102))
    clock.advance(1)
    third = q.admit(make_event(pane_id="P3", pane_pid=103))
    # Active queue bounded at 2: the OLDEST active was displaced...
    assert [r["id"] for r in q.list_active()] == [second["id"], third["id"]]
    ledger = {r["pane_id"]: r for r in q.list_all()}
    # ...but REMAINS in the ledger with evidence intact (displacing != losing).
    assert set(ledger) == {"P1", "P2", "P3"}
    assert ledger["P1"]["state"] == "evicted"
    assert ledger["P1"]["text"] == "Task finished."
    # The consolidation window is a parameter too: repeats inside it merge.
    clock.advance(5)
    again = q.admit(make_event(pane_id="P3", pane_pid=103))
    assert again["id"] == third["id"] and again["repeat_count"] == 2
    # The cap is NOT a global default: without it the same traffic stays active.
    q2 = pq.PendingQueue(tmp_path / "pending2.db")
    for pane in ("PA", "PB", "PC"):
        q2.admit(make_event(pane_id=pane, pane_pid={"PA": 1, "PB": 2, "PC": 3}[pane]))
    assert len(q2.list_active()) == 3
    # Negative bounds are a wiring bug and fail fast.
    with pytest.raises(ValueError):
        make_queue(tmp_path / "pending3.db", max_records=-1)


def test_no_terminal_reopen(tmp_path):
    clock = FakeClock()
    q = make_queue(tmp_path / "pending.db", clock=clock)
    rec = q.admit(make_event())
    assert q.mark(rec["id"], "announcing")["ok"] is True
    assert q.mark(rec["id"], "announced")["ok"] is True
    # A repeat WITHIN the window on a terminal record: metadata only.
    clock.advance(10)
    repeat = q.admit(make_event())
    assert repeat["id"] == rec["id"]
    assert repeat["repeat_count"] == 2
    assert q.get(rec["id"])["state"] == "announced"
    assert len(q.list_all()) == 1
    # mark() on a terminal record refuses EVERY transition.
    for target in ("expired", "pending", "announcing", "uncertain", "evicted", "cancelled"):
        result = q.mark(rec["id"], target)
        assert result["ok"] is False
        assert "never reopens" in result["error"]
    # A genuinely NEW event (outside the window) creates a NEW record.
    clock.advance(120)
    fresh = q.admit(make_event())
    assert fresh["id"] != rec["id"]
    assert fresh["announce_seq"] == rec["announce_seq"] + 1 == 2
    assert fresh["state"] == "pending"
    # The terminal record stays frozen exactly as it was.
    old = q.get(rec["id"])
    assert old["state"] == "announced" and old["repeat_count"] == 2


# -- focused behaviors ---------------------------------------------------------------


def test_epoch_unverified_when_pane_pid_missing(tmp_path):
    clock = FakeClock()
    q = make_queue(tmp_path / "pending.db", clock=clock)
    rec = q.admit(make_event(pane_pid=None))
    assert rec["state"] == "pending"
    assert rec["epoch_unverified"] is True        # visible on the record
    assert rec["epoch"] == "unverified"
    # A later VERIFIED epoch does NOT invent invalidation for unverified records.
    clock.advance(10)
    verified = q.admit(make_event(pane_pid=100))
    assert verified["epoch_unverified"] is False
    assert q.get(rec["id"])["state"] == "pending"
    # An explicit hint labels the epoch but cannot verify it.
    hinted = q.admit(make_event(pane_pid=None, epoch_hint="boot-7"))
    assert hinted["epoch"] == "boot-7"
    assert hinted["epoch_unverified"] is True


def test_ledger_exhaustion_refuses_visibly_with_envelope(tmp_path):
    clock = FakeClock()
    q = make_queue(tmp_path / "pending.db", clock=clock, max_db_bytes=1)
    assert q.admit(make_event()) is None          # NOT accepted, no fake ack
    assert q.list_all() == [] and q.list_active() == []
    sidecar = tmp_path / "pending-overflow.json"
    assert sidecar.exists()                       # emergency envelope on disk
    envelope = json.loads(sidecar.read_text())
    assert envelope["condition"] == "admission-blocked"
    assert "CANNOT be promised" in envelope["declaration"]
    event = envelope["events"][-1]
    assert "ledger-byte-budget-exceeded" in event["reason"]
    assert event["pane_id"] == "P1" and event["agent"] == "codex"
    assert event["status"] == "done" and event["label"] == "codex done"
    status = q.overflow_status()
    assert status["total"] == 1 and status["condition"] == "admission-blocked"
    assert status["sidecar_error"] is None


def test_admission_blocked_never_deletes_existing_records(tmp_path):
    clock = FakeClock()
    generous = make_queue(tmp_path / "pending.db", clock=clock)
    rec = generous.admit(make_event())
    blocked = make_queue(tmp_path / "pending.db", clock=clock, max_db_bytes=1)
    assert blocked.admit(make_event(pane_id="P9", pane_pid=109)) is None
    # Existing evidence is preserved INTACT: never deleted/completed for room.
    assert [r["id"] for r in blocked.list_all()] == [rec["id"]]
    assert blocked.get(rec["id"])["state"] == "pending"
    assert blocked.overflow_status()["total"] == 1


def test_admission_blocked_when_db_unopenable(tmp_path):
    clock = FakeClock()
    db = tmp_path / "pending.db"
    q = make_queue(db, clock=clock)
    assert q.admit(make_event()) is not None
    db.unlink()
    db.mkdir()                                    # a directory now sits where the db belongs
    assert q.admit(make_event(pane_id="P2", pane_pid=102)) is None
    status = q.overflow_status()
    assert status["total"] == 1
    assert "ledger-unopenable" in status["events"][-1]["reason"]


def test_overflow_sidecar_unwritable_stays_visible_in_memory(tmp_path):
    clock = FakeClock()
    q = make_queue(tmp_path / "pending.db", clock=clock, max_db_bytes=1)
    (tmp_path / "pending-overflow.json").mkdir()   # writes will fail
    assert q.admit(make_event()) is None
    status = q.overflow_status()
    assert status["total"] == 1                    # still counted, never hidden
    assert status["events"] and status["events"][-1]["pane_id"] == "P1"
    assert status["sidecar_error"]                 # honest: durable envelope NOT written


def test_overflow_envelope_is_bounded(tmp_path):
    clock = FakeClock()
    q = make_queue(tmp_path / "pending.db", clock=clock,
                   max_db_bytes=1, max_overflow_events=2)
    for i in range(4):
        assert q.admit(make_event(pane_id=f"P{i}", pane_pid=100 + i)) is None
    status = q.overflow_status()
    assert status["total"] == 4                    # aggregate counts everything
    assert len(status["events"]) == 2              # the envelope itself is bounded
    assert status["events"][-1]["pane_id"] == "P3"  # newest tail kept


# -- overflow two levels (VS3.4: ACTIVE bound + ledger byte budget) ------------------
#
# Level 1: the ACTIVE queue is bounded by max_records — the OLDEST active
# record is displaced to `evicted` and REMAINS in the ledger (displacing is
# not losing). Level 2: the LEDGER is bounded by max_db_bytes — exhaustion
# refuses admission visibly (admission-blocked) without ever deleting or
# completing existing records to make room.


def test_active_full_displaces_to_evicted_in_ledger(tmp_path):
    clock = FakeClock()
    q = make_queue(tmp_path / "pending.db", clock=clock, max_records=2)
    first = q.admit(make_event(pane_id="P1", pane_pid=101))
    clock.advance(1)
    second = q.admit(make_event(pane_id="P2", pane_pid=102))
    clock.advance(1)
    third = q.admit(make_event(pane_id="P3", pane_pid=103))
    assert len(q.list_active()) == 2                    # ACTIVE count == N
    ledger = {r["id"]: r for r in q.list_all()}
    assert len(ledger) == 3                             # nothing left the ledger
    assert ledger[first["id"]]["state"] == "evicted"    # oldest active displaced
    assert ledger[first["id"]]["text"] == "Task finished."   # evidence intact
    assert ledger[first["id"]]["pane_id"] == "P1"
    assert {r["id"] for r in q.list_active()} == {second["id"], third["id"]}


def test_ledger_exhaustion_blocks_admission_visibly(tmp_path, capsys):
    clock = FakeClock()
    db = tmp_path / "pending.db"
    generous = make_queue(db, clock=clock)
    kept = generous.admit(make_event())
    blocked = make_queue(db, clock=clock, max_db_bytes=1)   # tiny byte budget
    assert blocked.admit(make_event(pane_id="P9", pane_pid=109)) is None  # refused
    # The CLI surface says the same thing typed.
    argv = ["admit", "--pane-id", "P9", "--agent", "codex", "--status", "done",
            "--label", "l", "--text", "t"]
    assert pq.main(argv, queue_factory=lambda: blocked) == 1
    assert "ok=false reason=admission-blocked" in capsys.readouterr().err
    # Existing records INTACT: nothing deleted or completed to make room.
    records = blocked.list_all()
    assert [r["id"] for r in records] == [kept["id"]]
    assert records[0]["state"] == "pending"


def test_emergency_envelope_reason_attribution(tmp_path):
    clock = FakeClock()
    q = make_queue(tmp_path / "pending.db", clock=clock, max_db_bytes=1)
    assert q.admit(make_event()) is None
    sidecar = tmp_path / "pending-overflow.json"
    assert sidecar.exists()                             # next to the db
    envelope = json.loads(sidecar.read_text())
    assert envelope["condition"] == "admission-blocked"
    assert envelope["aggregate"]["total"] == 1
    assert envelope["aggregate"]["first_ts"] == 1000.0
    assert envelope["aggregate"]["last_ts"] == 1000.0
    assert len(envelope["events"]) <= pq.DEFAULT_MAX_OVERFLOW_EVENTS  # ≤ 32
    event = envelope["events"][-1]
    assert event["reason"].startswith("ledger-byte-budget-exceeded")  # exact reason
    assert event["pane_id"] == "P1" and event["agent"] == "codex"     # attribution
    assert event["status"] == "done" and event["label"] == "codex done"
    assert "CANNOT be promised" in envelope["declaration"]
    # Unwritable sidecar: the count survives and the failure is carried.
    q2 = make_queue(tmp_path / "p2.db", clock=clock, max_db_bytes=1)
    sidecar.unlink()
    sidecar.mkdir()                                     # writes will fail now
    assert q2.admit(make_event(pane_id="P5", pane_pid=105)) is None
    status = q2.overflow_status()
    assert status["total"] == 1                          # never hidden
    assert status["events"][-1]["pane_id"] == "P5"
    assert status["sidecar_error"]                       # honest: not durable


def test_no_silent_loss_no_fake_accept(tmp_path):
    clock = FakeClock()
    db = tmp_path / "pending.db"
    healthy = make_queue(db, clock=clock)
    kept = healthy.admit(make_event())
    blocked = make_queue(db, clock=clock, max_db_bytes=1)
    refused = make_event(pane_id="P7", pane_pid=107)
    assert blocked.admit(refused) is None
    # The refused event is NOT recorded as accepted anywhere...
    records = blocked.list_all()
    assert [r["id"] for r in records] == [kept["id"]]    # ledger unchanged
    assert all(r["pane_id"] != "P7" for r in records)
    assert [r["id"] for r in blocked.list_active()] == [kept["id"]]
    # ...the aggregate DOES count the refusal...
    status = blocked.overflow_status()
    assert status["total"] == 1
    assert status["events"][-1]["pane_id"] == "P7"
    # ...existing records are unchanged...
    assert blocked.get(kept["id"])["state"] == "pending"
    # ...and admission works again once space exists (budget restored on the
    # same ledger, or a fresh db — the refusal poisoned nothing).
    healed = make_queue(db, clock=clock)
    retry = healed.admit(make_event(pane_id="P8", pane_pid=108))
    assert retry is not None and retry["state"] == "pending"
    fresh = make_queue(tmp_path / "fresh.db", clock=clock)
    rec = fresh.admit(refused)
    assert rec is not None and rec["state"] == "pending" and rec["pane_id"] == "P7"


def test_mark_typed_refusals(tmp_path):
    assert pq.TERMINAL_STATES == ("announced", "expired", "evicted", "cancelled")
    clock = FakeClock()
    q = make_queue(tmp_path / "pending.db", clock=clock)
    rec = q.admit(make_event())
    assert q.mark("no-such-id", "expired")["ok"] is False       # unknown id
    assert q.mark(rec["id"], "bogus")["ok"] is False            # unknown state
    assert q.mark(rec["id"], "pending")["ok"] is False          # self is not a transition
    assert q.mark(rec["id"], "announced")["ok"] is False        # must pass through announcing
    assert q.get(rec["id"])["state"] == "pending"               # refusals changed nothing
    # Legal walk: pending -> announcing -> announced.
    assert q.mark(rec["id"], "announcing")["ok"] is True
    assert q.mark(rec["id"], "announced")["ok"] is True
    # Resolving edges from uncertain (deliberate actions only, PRD 03).
    other = q.admit(make_event(pane_id="P2", pane_pid=102))
    q.mark(other["id"], "announcing")
    q.mark(other["id"], "uncertain")
    assert q.mark(other["id"], "pending")["ok"] is True         # deliberate retry
    q.mark(other["id"], "announcing")
    assert q.mark(other["id"], "cancelled")["ok"] is True       # explicit cancel
    # Enqueue-failure claim reset (T7 dispatcher contract).
    third = q.admit(make_event(pane_id="P3", pane_pid=103))
    q.mark(third["id"], "announcing")
    assert q.mark(third["id"], "pending")["ok"] is True


def test_get_missing_returns_none(tmp_path):
    q = make_queue(tmp_path / "pending.db")
    assert q.get("00000000-0000-0000-0000-000000000000") is None


def test_text_sanitized_and_bounded_like_announce(tmp_path):
    clock = FakeClock()
    q = make_queue(tmp_path / "pending.db", clock=clock)
    messy = q.admit(make_event(text="line one\n  line\ttwo.   " + "x" * 400))
    assert len(messy["text"]) <= pq.DEFAULT_TEXT_MAX_CHARS
    assert "\n" not in messy["text"] and "\t" not in messy["text"]
    assert messy["text"] == "line one line two."   # sentence-preferred cut
    hard_cut = q.admit(make_event(pane_id="P2", pane_pid=102, text="z" * 400))
    assert hard_cut["text"].endswith("...") and len(hard_cut["text"]) == 300
    labeled = q.admit(make_event(pane_id="P3", pane_pid=103, label="L" * 400))
    assert len(labeled["label"]) == pq.LABEL_MAX_CHARS


def test_consolidation_window_boundaries_fake_clock(tmp_path):
    clock = FakeClock()
    q = make_queue(tmp_path / "pending.db", clock=clock, consolidation_window_s=60.0)
    first = q.admit(make_event())
    clock.advance(59)                              # value-1: still inside the window
    assert q.admit(make_event())["id"] == first["id"]
    clock.advance(61)                              # value+1 past last_seen: outside
    second = q.admit(make_event())
    assert second["id"] != first["id"]
    assert second["announce_seq"] == 2
    assert q.get(first["id"])["repeat_count"] == 2  # frozen at the last in-window seen


def test_env_bounds_overrides_and_garbage_ignored(tmp_path, capsys):
    import pending_queue

    monkeypatch = pytest.MonkeyPatch()
    with monkeypatch.context() as mp:
        mp.setenv("PENDING_MAX_RECORDS", "3")
        q = pending_queue.PendingQueue(tmp_path / "a.db")
        assert q.max_records == 3                  # env tunes production defaults
        mp.setenv("PENDING_MAX_RECORDS", "garbage")
        q2 = pending_queue.PendingQueue(tmp_path / "b.db")
        assert q2.max_records == pq.DEFAULT_MAX_RECORDS  # garbage never crashes
        assert "PENDING_MAX_RECORDS" in capsys.readouterr().err


def test_default_db_path_under_fake_home(tmp_path):
    monkeypatch = pytest.MonkeyPatch()
    with monkeypatch.context() as mp:
        mp.setenv("HOME", str(tmp_path))
        q = pq.PendingQueue()
        expected = tmp_path / ".local/state/herdr-tts/pending.db"
        assert q.db_path == expected               # documented default location
        assert expected.exists()                   # schema created lazily


@pytest.mark.parametrize("bad_override", [
    {"pane_id": ""},
    {"agent": None},
    {"status": "running"},
    {"label": 5},
    {"text": None},
    {"pane_pid": "not-a-pid"},
    {"epoch_hint": 7},
    {"audio_path": 9},
])
def test_malformed_events_raise_value_error(tmp_path, bad_override):
    # Malformed events are wiring bugs: visible ValueError, never a fake accept.
    q = make_queue(tmp_path / "pending.db")
    with pytest.raises(ValueError):
        q.admit(make_event(**bad_override))
    with pytest.raises(ValueError):
        q.admit(["not", "a", "dict"])
    assert q.list_all() == []


# -- guard branches of the module's own helpers -------------------------------------


def test_sanitize_text_guard_branches():
    # admit() validates non-empty text first; the guards cover direct callers.
    assert pq._sanitize_text("", 300) == ""
    assert pq._sanitize_text(None, 300) == ""
    assert pq._sanitize_text("   \n\t ", 300) == ""
    # Single long sentence ENDING in a period: cut at the period, no ellipsis.
    assert pq._sanitize_text("y" * 299 + ".", 300) == "y" * 299 + "."
    assert pq._sanitize_text("y" * 299 + "." + "z" * 50, 300) == "y" * 299 + "."
    assert pq._sanitize_label("ok label") == "ok label"


def test_bound_properties_exposed(tmp_path):
    clock = FakeClock()
    q = make_queue(tmp_path / "pending.db", clock=clock,
                   max_db_bytes=4096, consolidation_window_s=30.0)
    assert q.max_db_bytes == 4096
    assert q.consolidation_window_s == 30.0
    assert q.max_records == pq.DEFAULT_MAX_RECORDS


def test_write_failure_mid_transaction_refuses_visibly(tmp_path, monkeypatch):
    import sqlite3

    clock = FakeClock()
    q = make_queue(tmp_path / "pending.db", clock=clock)

    def exploding_seq(conn, pane_id, status):
        raise sqlite3.OperationalError("forced failure for the test")

    monkeypatch.setattr(pq.PendingQueue, "_next_seq", staticmethod(exploding_seq))
    assert q.admit(make_event()) is None          # storage error: NOT accepted
    status = q.overflow_status()
    assert "ledger-write-failed" in status["events"][-1]["reason"]
    assert status["events"][-1]["pane_id"] == "P1"
    assert q.list_all() == []                     # rolled back, nothing half-written


def test_corrupt_shaped_sidecar_restarts_aggregate_visibly(tmp_path):
    clock = FakeClock()
    db = tmp_path / "pending.db"
    q = make_queue(db, clock=clock)
    q.admit(make_event())                         # normal queue, healthy db
    (tmp_path / "pending-overflow.json").write_text(json.dumps({"hello": 1}))
    blocked = make_queue(db, clock=clock, max_db_bytes=1)
    assert blocked.admit(make_event(pane_id="P2", pane_pid=102)) is None
    status = blocked.overflow_status()
    # The corrupt sidecar is NOT trusted as evidence: the aggregate restarts
    # from what this process can prove (1 refusal) and the next successful
    # flush rewrites a valid envelope.
    assert status["total"] == 1
    assert status["events"][-1]["pane_id"] == "P2"
    healed = json.loads((tmp_path / "pending-overflow.json").read_text())
    assert healed["aggregate"]["total"] == 1
    assert healed["condition"] == "admission-blocked"


def test_relative_db_path_without_parent_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    q = pq.PendingQueue("pending-rel.db")
    assert q.db_path == pq.Path("pending-rel.db")
    assert q.admit(make_event())["state"] == "pending"
    assert (tmp_path / "pending-rel.db").exists()


# -- dispatcher tick + completion mapping (VS3.3, T7 LOCKED) -------------------------
#
# claim-before-enqueue: pending -> announcing with claimed_ts stamped in the
# SAME transaction BEFORE any enqueue attempt; FIFO non-preempt ALWAYS; ANY
# enqueue failure resets the claim. is_playing and the daemon enqueue seam
# are always injectable doubles — no test contacts a daemon or /tmp files.


def test_dispatch_fifo_first_seen(tmp_path):
    clock = FakeClock()
    q = make_queue(tmp_path / "p.db", clock=clock)
    a1 = make_audio(tmp_path, "a1.mp3")
    a2 = make_audio(tmp_path, "a2.mp3")
    a3 = make_audio(tmp_path, "a3.mp3")
    first = q.admit(make_event(pane_id="P1", pane_pid=101, audio_path=str(a1)))
    clock.advance(1)
    second = q.admit(make_event(pane_id="P2", pane_pid=102, audio_path=str(a2)))
    clock.advance(1)
    third = q.admit(make_event(pane_id="P3", pane_pid=103, audio_path=str(a3)))
    enq = RecordingEnqueue()
    result = q.dispatch_tick(is_playing=lambda: False, enqueue=enq)
    assert result == {"ok": True, "dispatched": 1, "claimed": first["id"], "item": 1}
    assert len(enq.calls) == 1
    payload = enq.calls[0]
    assert payload["identifiers"] == [first["id"]]   # FIFO head, by first_seen_ts
    assert payload["file"] == os.path.abspath(str(a1))
    assert payload["label"] == "codex done"
    rec = q.get(first["id"])
    assert rec["state"] == "announcing"              # claimed before enqueue
    assert rec["claimed_ts"] == clock.now            # stamped inside the claim
    assert rec["item_id"] == "1"                     # daemon item persisted
    assert q.get(second["id"])["state"] == "pending"  # younger records untouched
    assert q.get(third["id"])["state"] == "pending"


def test_dispatch_uses_isplaying_and_identifiers(tmp_path):
    clock = FakeClock()
    q = make_queue(tmp_path / "p.db", clock=clock)
    audio = make_audio(tmp_path, "a.mp3")
    rec = q.admit(make_event(audio_path=str(audio)))
    enq = RecordingEnqueue()
    busy = q.dispatch_tick(is_playing=lambda: True, enqueue=enq)
    assert busy == {"ok": True, "dispatched": 0, "busy": "true"}
    assert enq.calls == []                           # busy: enqueue double untouched
    free = q.dispatch_tick(is_playing=lambda: False, enqueue=enq)
    assert free["dispatched"] == 1
    assert enq.calls[0]["identifiers"] == [rec["id"]]  # the record's identifiers
    # A raising busy check is typed too — the tick never raises.
    def exploding():
        raise RuntimeError("stat broke")

    broken = q.dispatch_tick(is_playing=exploding, enqueue=enq)
    assert broken["ok"] is False and broken["dispatched"] == 0
    assert "is-playing check failed" in broken["error"]
    assert len(enq.calls) == 1                       # nothing new enqueued


def test_claim_prevents_double_dispatch(tmp_path):
    clock = FakeClock()
    q = make_queue(tmp_path / "p.db", clock=clock)
    a1 = make_audio(tmp_path, "a1.mp3")
    a2 = make_audio(tmp_path, "a2.mp3")
    first = q.admit(make_event(pane_id="P1", pane_pid=101, audio_path=str(a1)))
    clock.advance(1)
    second = q.admit(make_event(pane_id="P2", pane_pid=102, audio_path=str(a2)))
    enq = RecordingEnqueue(replies=["ok=true item=7 queue_len=1",
                                    "ok=true item=8 queue_len=1"])
    tick1 = q.dispatch_tick(is_playing=lambda: False, enqueue=enq)
    assert tick1["dispatched"] == 1 and tick1["claimed"] == first["id"]
    # Second tick, same clock, serialized: the claimed record is announcing —
    # NOT pending — so only the NEXT record is eligible. Same-record double
    # dispatch is impossible through the claim.
    tick2 = q.dispatch_tick(is_playing=lambda: False, enqueue=enq)
    assert tick2["dispatched"] == 1 and tick2["claimed"] == second["id"]
    assert [p["identifiers"][0] for p in enq.calls] == [first["id"], second["id"]]
    assert q.get(first["id"])["state"] == "announcing"
    # After completion the terminal record is never re-dispatched.
    done = q.report_completion(first["id"], "played")
    assert done["ok"] is True and done["state"] == "announced"
    enq2 = RecordingEnqueue()
    tick3 = q.dispatch_tick(is_playing=lambda: False, enqueue=enq2)
    assert tick3 == {"ok": True, "dispatched": 0}    # only announcing remains
    assert enq2.calls == []


def test_enqueue_failure_resets_claim(tmp_path):
    clock = FakeClock()
    q = make_queue(tmp_path / "p.db", clock=clock)
    audio = make_audio(tmp_path, "a.mp3")
    rec = q.admit(make_event(audio_path=str(audio)))
    refused = q.dispatch_tick(is_playing=lambda: False,
                              enqueue=RecordingEnqueue(["ok=false error=no daemon"]))
    assert refused["ok"] is False and refused["dispatched"] == 0
    assert refused["claimed"] == rec["id"] and refused["reset"] == "pending"
    assert "no daemon" in refused["error"]
    after = q.get(rec["id"])
    assert after["state"] == "pending"               # claim reset via FSM edge
    assert after["item_id"] is None                  # nothing persisted
    # A raising seam is an enqueue failure too — same reset, typed not raised.
    clock.advance(1)
    raised = q.dispatch_tick(is_playing=lambda: False,
                             enqueue=RecordingEnqueue([RuntimeError("ipc blew up")]))
    assert raised["ok"] is False and "ipc blew up" in raised["error"]
    assert q.get(rec["id"])["state"] == "pending"
    # The next tick retries the SAME record and succeeds.
    ok = q.dispatch_tick(is_playing=lambda: False,
                         enqueue=RecordingEnqueue(["ok=true item=5 queue_len=0"]))
    assert ok["ok"] is True and ok["dispatched"] == 1 and ok["claimed"] == rec["id"]
    assert q.get(rec["id"])["item_id"] == "5"


def test_completion_maps_itemid_to_announced_uncertain(tmp_path):
    clock = FakeClock()
    q = make_queue(tmp_path / "p.db", clock=clock)
    a1 = make_audio(tmp_path, "a1.mp3")
    a2 = make_audio(tmp_path, "a2.mp3")
    a3 = make_audio(tmp_path, "a3.mp3")
    played = q.admit(make_event(pane_id="P1", pane_pid=101, audio_path=str(a1)))
    q.dispatch_tick(is_playing=lambda: False,
                    enqueue=RecordingEnqueue(["ok=true item=11 queue_len=0"]))
    # Map by ENGINE ITEM ID: played => announced (terminal).
    by_item = q.report_completion("11", "played")
    assert by_item["ok"] is True and by_item["matched"] == "item"
    assert by_item["record"] == played["id"] and by_item["state"] == "announced"
    assert q.get(played["id"])["state"] == "announced"
    # Idempotent re-report of the same outcome...
    assert q.report_completion("11", "played")["ok"] is True
    # ...and the terminal NEVER reopens for anything else.
    for outcome in ("failed", "stopped", "unknown"):
        late = q.report_completion("11", outcome)
        assert late["ok"] is False and "never reopens" in late["error"]
    assert q.get(played["id"])["state"] == "announced"
    # stopped/failed/unknown => uncertain; exactly-once is never claimed.
    stopped = q.admit(make_event(pane_id="P2", pane_pid=102, audio_path=str(a2)))
    q.dispatch_tick(is_playing=lambda: False,
                    enqueue=RecordingEnqueue(["ok=true item=12 queue_len=0"]))
    hit = q.report_completion(stopped["id"], "stopped")   # by record id
    assert hit["ok"] is True and hit["matched"] == "id" and hit["state"] == "uncertain"
    assert q.report_completion(stopped["id"], "failed")["ok"] is True  # already there
    assert q.get(stopped["id"])["state"] == "uncertain"
    # A pending (never-claimed) record has no shortcut to announced.
    fresh = q.admit(make_event(pane_id="P3", pane_pid=103, audio_path=str(a3)))
    refused = q.report_completion(fresh["id"], "completed")
    assert refused["ok"] is False and "FSM forbids" in refused["error"]
    assert q.get(fresh["id"])["state"] == "pending"
    # Ids the ledger never held (immediate-announce path): honest none.
    none = q.report_completion("ann-000000001", "played")
    assert none["ok"] is True and none["matched"] == "none"
    # Unknown outcome vocab is a typed refusal for direct library callers.
    bogus = q.report_completion("ann-000000001", "silenced")
    assert bogus["ok"] is False and "unknown outcome" in bogus["error"]


# -- focused dispatcher branches (module-floor coverage for the new code) ------------


def test_dispatch_skips_records_without_usable_audio(tmp_path):
    clock = FakeClock()
    q = make_queue(tmp_path / "p.db", clock=clock)
    missing = q.admit(make_event(pane_id="P1", pane_pid=101,
                                 audio_path=str(tmp_path / "gone.mp3")))
    empty = make_audio(tmp_path, "empty.mp3", content=b"")
    second = q.admit(make_event(pane_id="P2", pane_pid=102, audio_path=str(empty)))
    # Nothing dispatchable: typed zero, no daemon contact.
    enq = RecordingEnqueue()
    assert q.dispatch_tick(is_playing=lambda: False, enqueue=enq) == {
        "ok": True, "dispatched": 0}
    assert enq.calls == []
    # The first DISPATCHABLE record (by first_seen) is claimed; the unusable
    # older ones stay pending — never a fake enqueue.
    clock.advance(1)
    good = make_audio(tmp_path, "good.mp3")
    third = q.admit(make_event(pane_id="P3", pane_pid=103, audio_path=str(good)))
    result = q.dispatch_tick(is_playing=lambda: False, enqueue=enq)
    assert result["dispatched"] == 1 and result["claimed"] == third["id"]
    assert q.get(missing["id"])["state"] == "pending"
    assert q.get(second["id"])["state"] == "pending"


def test_engine_is_playing_readonly_channel_check(tmp_path, monkeypatch):
    lock = tmp_path / "playing.lock"
    pid_file = tmp_path / "current.pid"
    # No lock: not busy, whatever the pid file says.
    pid_file.write_text(str(os.getpid()))
    assert pq.engine_is_playing(str(lock), str(pid_file)) is False
    # Lock + live pid (this process): busy.
    lock.write_text("")
    assert pq.engine_is_playing(str(lock), str(pid_file)) is True
    # Lock but no readable live owner: NOT busy (never cleans another
    # component's locks — staleness is the engine's own discipline).
    pid_file.unlink()
    assert pq.engine_is_playing(str(lock), str(pid_file)) is False
    pid_file.write_text("garbage")
    assert pq.engine_is_playing(str(lock), str(pid_file)) is False
    pid_file.write_text("-1")
    assert pq.engine_is_playing(str(lock), str(pid_file)) is False
    dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                          capture_output=True, text=True)
    pid_file.write_text(dead.stdout)
    assert pq.engine_is_playing(str(lock), str(pid_file)) is False  # dead pid
    # A live process this user cannot signal still counts as busy.
    def denied(sig, pid):
        raise PermissionError("not yours")

    monkeypatch.setattr(pq.os, "kill", denied)
    assert pq.engine_is_playing(str(lock), str(pid_file)) is True
    def vanished(sig, pid):
        raise ProcessLookupError("gone")

    monkeypatch.setattr(pq.os, "kill", vanished)
    assert pq.engine_is_playing(str(lock), str(pid_file)) is False
    def weird(sig, pid):
        raise OSError("odd")

    monkeypatch.setattr(pq.os, "kill", weird)
    assert pq.engine_is_playing(str(lock), str(pid_file)) is False


def test_tick_default_busy_check_reads_channel_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_TTS_LOCK_FILE", str(tmp_path / "engine.lock"))
    monkeypatch.setenv("AGENT_TTS_PID_FILE", str(tmp_path / "engine.pid"))
    clock = FakeClock()
    q = make_queue(tmp_path / "p.db", clock=clock)
    audio = make_audio(tmp_path, "a.mp3")
    rec = q.admit(make_event(audio_path=str(audio)))
    enq = RecordingEnqueue()
    # Default checker: env-configured channel files absent => engine free.
    assert q.dispatch_tick(enqueue=enq)["dispatched"] == 1
    assert enq.calls[0]["identifiers"] == [rec["id"]]
    # ...and present with a live pid => busy, FIFO non-preempt always.
    (tmp_path / "engine.lock").write_text("")
    (tmp_path / "engine.pid").write_text(str(os.getpid()))
    second = make_audio(tmp_path, "b.mp3")
    clock.advance(1)
    q.admit(make_event(pane_id="P2", pane_pid=102, audio_path=str(second)))
    busy = q.dispatch_tick(enqueue=enq)
    assert busy == {"ok": True, "dispatched": 0, "busy": "true"}
    assert len(enq.calls) == 1


def test_dispatch_reports_persist_failure_without_losing_dispatch(tmp_path):
    clock = FakeClock()
    q = make_queue(tmp_path / "p.db", clock=clock)
    audio = make_audio(tmp_path, "a.mp3")
    rec = q.admit(make_event(audio_path=str(audio)))

    class BrokenConn:
        def execute(self, *args, **kwargs):
            raise sqlite3.OperationalError("forced")

        def close(self):
            pass

    scripted = RecordingEnqueue(["ok=true item=9 queue_len=0"])
    real_connect = q._connect

    def swap_conn(payload):
        # The enqueue succeeded; the item-id bookkeeping that follows fails.
        q._connect = lambda: BrokenConn()
        return scripted(payload)

    try:
        result = q.dispatch_tick(is_playing=lambda: False, enqueue=swap_conn)
    finally:
        q._connect = real_connect
    assert result["ok"] is True and result["dispatched"] == 1  # dispatch is real
    assert "item-id persist failed" in result["item_id_persist_error"]
    assert q.get(rec["id"])["state"] == "announcing"           # claim kept
    assert q.get(rec["id"])["item_id"] is None                 # typed, not faked


def test_tick_never_raises_even_when_ledger_internals_fail(tmp_path):
    clock = FakeClock()
    q = make_queue(tmp_path / "p.db", clock=clock)
    audio = make_audio(tmp_path, "a.mp3")
    rec = q.admit(make_event(audio_path=str(audio)))

    class BrokenConn:
        def execute(self, *args, **kwargs):
            raise sqlite3.OperationalError("forced")

        def close(self):
            pass

    real_connect = q._connect

    def failing_and_broken(payload):
        q._connect = lambda: BrokenConn()   # the claim reset that follows fails
        return "ok=false error=boom"

    try:
        result = q.dispatch_tick(is_playing=lambda: False, enqueue=failing_and_broken)
    finally:
        q._connect = real_connect
    assert result["ok"] is False and result["dispatched"] == 0
    assert "tick-failed" in result["error"]           # typed, never raised
    # The reset could not run: the record stays visibly announcing (the
    # honest orphan the VS3.5/T8 resolution path owns).
    assert q.get(rec["id"])["state"] == "announcing"


def test_dispatch_ok_without_item_token_skips_item_fields(tmp_path):
    clock = FakeClock()
    q = make_queue(tmp_path / "p.db", clock=clock)
    audio = make_audio(tmp_path, "a.mp3")
    rec = q.admit(make_event(audio_path=str(audio)))
    result = q.dispatch_tick(is_playing=lambda: False,
                             enqueue=RecordingEnqueue(["ok=true queue_len=0"]))
    assert result == {"ok": True, "dispatched": 1, "claimed": rec["id"]}
    # No item token in the typed ok: no item to persist, none invented.
    assert q.get(rec["id"])["item_id"] is None


def test_completion_race_typed_refusal(tmp_path, monkeypatch):
    clock = FakeClock()
    q = make_queue(tmp_path / "p.db", clock=clock)
    other = make_queue(tmp_path / "p.db", clock=clock)   # second writer, same db
    audio = make_audio(tmp_path, "a.mp3")
    rec = q.admit(make_event(audio_path=str(audio)))
    q.dispatch_tick(is_playing=lambda: False, enqueue=RecordingEnqueue())
    real_row_to_record = pq._row_to_record
    raced = {}

    def racing_row_to_record(row):
        if row["state"] == "announcing" and "done" not in raced:
            raced["done"] = True
            # Another writer resolves the record between the completion's
            # read and its guarded write: the stale view must not reopen it.
            assert other.mark(rec["id"], "cancelled")["ok"] is True
        return real_row_to_record(row)

    monkeypatch.setattr(pq, "_row_to_record", racing_row_to_record)
    result = q.report_completion(rec["id"], "failed")
    assert result["ok"] is False
    assert "changed under the completion" in result["error"]
    assert q.get(rec["id"])["state"] == "cancelled"   # the other writer won


# -- CLI subcommands (VS3.2 thin watcher wiring + VS3.3 real tick/completion) --------
# admit/tick/completion are bin/herdr-tts's three THIN delegation points; the
# queue logic itself is covered above. ``queue_factory``/``is_playing``/
# ``enqueue`` are the same dependency-injection convention ``main()`` uses
# for ``enqueue``/``send``: the CLI never touches the real default db, a
# daemon or the /tmp channel files in tests.

ADMIT_ARGV = ["admit", "--pane-id", "P1", "--agent", "codex", "--status", "done",
              "--label", "codex done", "--text", "Task finished."]


def test_cli_admit_prints_typed_record(tmp_path, capsys):
    q = make_queue(tmp_path / "p.db")
    rc = pq.main(ADMIT_ARGV + ["--pane-pid", "100", "--audio-path", str(tmp_path / "a.mp3")],
                 queue_factory=lambda: q)
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("ok=true id=")
    assert "state=pending" in out
    assert "repeat_count=1" in out


def test_cli_admit_consolidation_repeats_same_record(tmp_path, capsys):
    q = make_queue(tmp_path / "p.db")
    pq.main(ADMIT_ARGV, queue_factory=lambda: q)
    pq.main(ADMIT_ARGV, queue_factory=lambda: q)
    out = capsys.readouterr().out.strip().splitlines()[-1]
    assert "repeat_count=2" in out and "state=pending" in out


def test_cli_admit_refusal_is_typed_admission_blocked(tmp_path, capsys):
    q = make_queue(tmp_path / "p.db", max_db_bytes=0)   # byte budget 0 => refuse
    assert pq.main(ADMIT_ARGV, queue_factory=lambda: q) == 1
    assert "ok=false reason=admission-blocked" in capsys.readouterr().err


def test_cli_admit_malformed_event_exits_2(tmp_path, capsys):
    q = make_queue(tmp_path / "p.db")
    argv = ["admit", "--pane-id", "P1", "--agent", "codex", "--status", "done",
            "--label", "", "--text", "t"]
    assert pq.main(argv, queue_factory=lambda: q) == 2
    assert "label" in capsys.readouterr().err


def test_cli_admit_default_factory_uses_default_db(monkeypatch, tmp_path):
    # The production call path constructs PendingQueue() with the documented
    # default path; prove the default factory wiring without touching $HOME.
    monkeypatch.setattr(pq, "DEFAULT_DB_PATH", str(tmp_path / "default.db"))
    assert pq.main(ADMIT_ARGV) == 0
    assert (tmp_path / "default.db").exists()


def test_cli_tick_empty_queue_dispatches_nothing(tmp_path, capsys):
    q = make_queue(tmp_path / "p.db")
    enq = RecordingEnqueue()
    assert pq.main(["tick"], queue_factory=lambda: q,
                   is_playing=lambda: False, enqueue=enq) == 0
    out = capsys.readouterr().out.strip()
    assert out == "ok=true dispatched=0"        # typed, cheap, nothing claimed
    assert enq.calls == []                      # no daemon contact


def test_cli_tick_busy_dispatches_nothing(tmp_path, capsys):
    q = make_queue(tmp_path / "p.db")
    audio = make_audio(tmp_path, "a.mp3")
    q.admit(make_event(audio_path=str(audio)))
    enq = RecordingEnqueue()
    assert pq.main(["tick"], queue_factory=lambda: q,
                   is_playing=lambda: True, enqueue=enq) == 0
    out = capsys.readouterr().out.strip()
    assert out == "ok=true dispatched=0 busy=true"   # FIFO non-preempt ALWAYS
    assert enq.calls == []


def test_cli_tick_dispatches_through_injected_doubles(tmp_path, capsys):
    q = make_queue(tmp_path / "p.db")
    audio = make_audio(tmp_path, "a.mp3")
    rec = q.admit(make_event(audio_path=str(audio)))
    rc = pq.main(["tick"], queue_factory=lambda: q, is_playing=lambda: False,
                 enqueue=lambda payload: "ok=true item=3 queue_len=0")
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("ok=true dispatched=1")
    assert f"claimed={rec['id']}" in out and "item=3" in out
    assert q.get(rec["id"])["item_id"] == "3"


def test_cli_tick_failure_exits_1_typed(tmp_path, capsys):
    q = make_queue(tmp_path / "p.db")
    audio = make_audio(tmp_path, "a.mp3")
    rec = q.admit(make_event(audio_path=str(audio)))
    rc = pq.main(["tick"], queue_factory=lambda: q, is_playing=lambda: False,
                 enqueue=lambda payload: "ok=false error=no daemon")
    assert rc == 1                                   # engine refusal: ok=false
    err = capsys.readouterr().err.strip()
    assert err.startswith("ok=false dispatched=0")
    assert f"claimed={rec['id']}" in err and "reset=pending" in err
    assert "no daemon" in err
    assert q.get(rec["id"])["state"] == "pending"    # claim honestly reset


def test_cli_tick_ledger_unopenable_exits_1(tmp_path, capsys):
    def broken_factory():
        raise OSError("disk gone")

    assert pq.main(["tick"], queue_factory=broken_factory) == 1
    assert "ledger-unopenable" in capsys.readouterr().err


def test_cli_completion_unmatched_id_acknowledges_none(tmp_path, capsys):
    # The immediate-announce path (outside the pending queue) legitimately
    # reports outcomes for ids the ledger never held: honest typed none.
    q = make_queue(tmp_path / "p.db")
    assert pq.main(["completion", "--id", "ann-000000001", "--outcome", "played"],
                   queue_factory=lambda: q) == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("ok=true id=ann-000000001 outcome=played")
    assert "matched=none" in out


def test_cli_completion_maps_to_ledger(tmp_path, capsys):
    q = make_queue(tmp_path / "p.db")
    rec = q.admit(make_event())
    q.mark(rec["id"], "announcing")
    assert pq.main(["completion", "--id", rec["id"], "--outcome", "played"],
                   queue_factory=lambda: q) == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith(f"ok=true id={rec['id']} outcome=played")
    assert "state=announced" in out
    assert q.get(rec["id"])["state"] == "announced"


def test_cli_completion_refusal_exits_1(tmp_path, capsys):
    q = make_queue(tmp_path / "p.db")
    rec = q.admit(make_event())                      # still pending: no shortcut
    assert pq.main(["completion", "--id", rec["id"], "--outcome", "played"],
                   queue_factory=lambda: q) == 1
    assert "FSM forbids" in capsys.readouterr().err


def test_cli_completion_ledger_unopenable_exits_1(tmp_path, capsys):
    def broken_factory():
        raise sqlite3.OperationalError("forced")

    assert pq.main(["completion", "--id", "ann-000000001", "--outcome", "played"],
                   queue_factory=broken_factory) == 1
    assert "ledger-unopenable" in capsys.readouterr().err


def test_cli_completion_rejects_invalid_id(capsys):
    assert pq.main(["completion", "--id", "bad", "--outcome", "played"]) == 2
    assert "invalid identifier" in capsys.readouterr().err


# -- operator CLI: retry/resolve + list/status (VS3.5, T8) ---------------------------
#
# Deliberate resolution is attributable by construction: every retry/resolve
# stamps resolved_by (explicit --actor, else $USER, else "operator-cli") and
# resolved_ts. Illegal or terminal-reopening attempts are typed refusals
# that mutate nothing and exit non-zero.


def test_resolve_records_actor_ts(tmp_path, monkeypatch):
    # REQUIRED (VS3.5): deliberate actions leave attributable evidence.
    monkeypatch.setenv("USER", "bruno")
    clock = FakeClock()
    q = make_queue(tmp_path / "p.db", clock=clock)
    rec = q.admit(make_event())
    q.mark(rec["id"], "announcing")
    assert q.report_completion(rec["id"], "failed")["ok"] is True   # -> uncertain
    clock.advance(30)
    resolved = q.resolve(rec["id"], "announced", actor="operator-b")
    assert resolved["ok"] is True
    assert resolved["resolved_by"] == "operator-b"
    assert resolved["resolved_ts"] == clock.now
    row = q.get(rec["id"])
    assert row["state"] == "announced"
    assert row["resolved_by"] == "operator-b" and row["resolved_ts"] == clock.now
    # Without an explicit actor the environment's USER is the attribution.
    other = q.admit(make_event(pane_id="P2", pane_pid=102))
    q.mark(other["id"], "announcing")
    q.report_completion(other["id"], "unknown")
    clock.advance(5)
    retried = q.retry(other["id"])
    assert retried["ok"] is True and retried["to"] == "pending"
    assert retried["resolved_by"] == "bruno"
    assert q.get(other["id"])["resolved_by"] == "bruno"


def test_retry_only_from_uncertain(tmp_path):
    clock = FakeClock()
    q = make_queue(tmp_path / "p.db", clock=clock)
    pending = q.admit(make_event())
    refusal = q.retry(pending["id"])
    assert refusal["ok"] is False and "retry only resolves" in refusal["error"]
    row = q.get(pending["id"])
    assert row["state"] == "pending" and row["resolved_by"] is None   # untouched
    announcing = q.admit(make_event(pane_id="P2", pane_pid=102))
    q.mark(announcing["id"], "announcing")
    refused = q.retry(announcing["id"])
    assert refused["ok"] is False and "announcing" in refused["error"]
    assert q.get(announcing["id"])["state"] == "announcing"
    # The happy path: a dispatched record whose playback stopped.
    audio = make_audio(tmp_path, "a.mp3")
    claimed = q.admit(make_event(pane_id="P3", pane_pid=103, audio_path=str(audio)))
    q.dispatch_tick(is_playing=lambda: False, enqueue=RecordingEnqueue())
    assert q.get(claimed["id"])["item_id"] == "1"
    clock.advance(5)
    assert q.report_completion(claimed["id"], "stopped")["state"] == "uncertain"
    ok = q.retry(claimed["id"], actor="op")
    assert ok["ok"] is True and ok["to"] == "pending"
    after = q.get(claimed["id"])
    assert after["item_id"] is None          # stale engine item must not map twice
    assert after["claimed_ts"] is not None   # the attempt stays as evidence
    assert after["resolved_by"] == "op"
    # Re-queued FOR THE DISPATCHER: the next tick claims it again, exactly once.
    tick = q.dispatch_tick(is_playing=lambda: False,
                           enqueue=RecordingEnqueue(["ok=true item=9 queue_len=0"]))
    assert tick["dispatched"] == 1 and tick["claimed"] == claimed["id"]
    # Terminals never reopen, retry included.
    q.report_completion(claimed["id"], "played")
    terminal = q.retry(claimed["id"])
    assert terminal["ok"] is False and "never reopens" in terminal["error"]


def test_resolve_edge_legality(tmp_path):
    clock = FakeClock()
    q = make_queue(tmp_path / "p.db", clock=clock)

    def at(state, **event):
        rec = q.admit(make_event(**event))
        if state == "uncertain":
            q.mark(rec["id"], "announcing")
            q.report_completion(rec["id"], "failed")
        elif state != "pending":
            assert q.mark(rec["id"], state)["ok"] is True
        return rec["id"]

    # Legal edges only: announcing/uncertain -> announced; pending/uncertain -> expired.
    assert q.resolve(at("announcing", pane_id="A1", pane_pid=201), "announced")["ok"] is True
    assert q.resolve(at("uncertain", pane_id="A2", pane_pid=202), "announced")["ok"] is True
    assert q.resolve(at("uncertain", pane_id="A3", pane_pid=203), "expired")["ok"] is True
    assert q.resolve(at("pending", pane_id="A4", pane_pid=204), "expired")["ok"] is True
    # Illegal edges: typed refusal, nothing mutated.
    shortcut = at("pending", pane_id="B1", pane_pid=211)
    refused = q.resolve(shortcut, "announced")
    assert refused["ok"] is False and "FSM forbids" in refused["error"]
    wrong = at("announcing", pane_id="B2", pane_pid=212)
    denied = q.resolve(wrong, "expired")
    assert denied["ok"] is False and "FSM forbids" in denied["error"]
    row = q.get(wrong)
    assert row["state"] == "announcing" and row["resolved_by"] is None
    # Terminals never reopen through resolve.
    done = at("announcing", pane_id="B3", pane_pid=213)
    q.resolve(done, "announced")
    late = q.resolve(done, "expired")
    assert late["ok"] is False and "never reopens" in late["error"]
    # Unknown ids and invalid targets are typed too.
    assert q.resolve("no-such-record", "announced")["ok"] is False
    bogus = q.resolve(shortcut, "pending")
    assert bogus["ok"] is False and "unknown resolve target" in bogus["error"]


def test_cli_list_and_status_shape(tmp_path, capsys):
    clock = FakeClock()
    q = make_queue(tmp_path / "p.db", clock=clock)
    assert pq.main(["list"], queue_factory=lambda: q) == 0
    assert capsys.readouterr().out == "ok=true count=0\n"   # empty ledger: honest zero
    first = q.admit(make_event(pane_id="P1", pane_pid=101))
    clock.advance(10)
    second = q.admit(make_event(pane_id="P2", pane_pid=102, status="blocked",
                                label="blocked", text="Stuck."))
    q.mark(first["id"], "announcing")
    assert pq.main(["list"], queue_factory=lambda: q) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0] == "ok=true count=2"
    assert lines[1] == (f"id={first['id']} pane=P1 status=done state=announcing"
                        " repeat_count=1 first_seen=1000.0")
    assert lines[2] == (f"id={second['id']} pane=P2 status=blocked state=pending"
                        " repeat_count=1 first_seen=1010.0")
    assert pq.main(["status"], queue_factory=lambda: q) == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("ok=true pending=1 announcing=1 announced=0"
                          " uncertain=0 expired=0 evicted=0 cancelled=0")
    assert f"active=2/{pq.DEFAULT_MAX_RECORDS}" in out
    bytes_token = next(t for t in out.split() if t.startswith("ledger_bytes="))
    assert int(bytes_token.split("=")[1]) > 0
    assert "overflow=ok" in out and "overflow_total=0" in out


def test_cli_status_reports_overflow_condition(tmp_path, capsys):
    clock = FakeClock()
    q = make_queue(tmp_path / "p.db", clock=clock, max_db_bytes=1)
    assert q.admit(make_event()) is None
    assert pq.main(["status"], queue_factory=lambda: q) == 0
    out = capsys.readouterr().out.strip()
    assert "overflow=admission-blocked" in out and "overflow_total=1" in out


def test_cli_operator_actions_typed(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("USER", "cli-user")
    clock = FakeClock()
    q = make_queue(tmp_path / "p.db", clock=clock)
    rec = q.admit(make_event())
    q.mark(rec["id"], "announcing")
    q.report_completion(rec["id"], "failed")            # -> uncertain
    # retry: typed one-line ack carrying the attribution the flag gave.
    assert pq.main(["retry", rec["id"], "--actor", "night-shift"],
                   queue_factory=lambda: q) == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith(f"ok=true id={rec['id']} from=uncertain to=pending")
    assert "resolved_by=night-shift" in out and "resolved_ts=" in out
    # Default actor: $USER when the flag is absent.
    q.mark(rec["id"], "announcing")
    q.report_completion(rec["id"], "stopped")           # uncertain again
    assert pq.main(["retry", rec["id"]], queue_factory=lambda: q) == 0
    assert "resolved_by=cli-user" in capsys.readouterr().out
    # retry outside uncertain: typed refusal, exit 1, nothing mutated.
    assert pq.main(["retry", rec["id"]], queue_factory=lambda: q) == 1
    err = capsys.readouterr().err.strip()
    assert err.startswith(f"ok=false id={rec['id']}") and "retry only resolves" in err
    assert q.get(rec["id"])["state"] == "pending"
    # resolve: legal verdict exits 0; terminal reopen exits 1 typed.
    assert pq.main(["resolve", rec["id"], "expired", "--actor", "op"],
                   queue_factory=lambda: q) == 0
    assert "to=expired" in capsys.readouterr().out
    assert pq.main(["resolve", rec["id"], "announced"], queue_factory=lambda: q) == 1
    assert "never reopens" in capsys.readouterr().err
    # regenerate refusals through the CLI never reach a renderer.
    assert pq.main(["regenerate", rec["id"]], queue_factory=lambda: q) == 1
    assert "not regenerable" in capsys.readouterr().err
    assert pq.main(["regenerate", "ann-000000009"], queue_factory=lambda: q) == 1
    assert "unknown record id" in capsys.readouterr().err
    # Invalid identifiers are usage errors (exit 2), like the other arms.
    assert pq.main(["retry", "bad"], queue_factory=lambda: q) == 2
    assert "invalid identifier" in capsys.readouterr().err
    assert pq.main(["resolve", "bad", "announced"], queue_factory=lambda: q) == 2
    assert pq.main(["regenerate", "bad"], queue_factory=lambda: q) == 2


def test_cli_operator_arms_ledger_unopenable(capsys):
    def broken_factory():
        raise OSError("disk gone")

    for argv in (["list"], ["status"], ["retry", "ann-000000001"],
                 ["resolve", "ann-000000001", "announced"],
                 ["regenerate", "ann-000000001"]):
        assert pq.main(argv, queue_factory=broken_factory) == 1
        assert "ledger-unopenable" in capsys.readouterr().err


def test_default_actor_env(monkeypatch):
    monkeypatch.setenv("USER", "whoever")
    assert pq.default_actor() == "whoever"
    monkeypatch.delenv("USER", raising=False)
    assert pq.default_actor() == "operator-cli"


# -- crash recovery (VS3.7, T7 "Crash") ----------------------------------------------
#
# Orphan `announcing` records (claimed by a tick whose completion never
# landed — the host died between claim and completion) become `uncertain`
# at construction, visibly and idempotently. NEVER auto-replay: uncertain
# re-enqueues only through the deliberate retry/resolve actions.


def test_orphan_announcing_becomes_uncertain_no_replay(tmp_path):
    clock = FakeClock()
    db = tmp_path / "p.db"
    q1 = make_queue(db, clock=clock)
    audio = make_audio(tmp_path, "a.mp3")
    rec = q1.admit(make_event(audio_path=str(audio)))
    q1.dispatch_tick(is_playing=lambda: False,
                     enqueue=RecordingEnqueue(["ok=true item=2 queue_len=0"]))
    assert q1.get(rec["id"])["state"] == "announcing"      # the orphan claim
    clock.advance(45)                                      # the host "died" here
    q2 = make_queue(db, clock=clock)
    row = q2.get(rec["id"])
    assert row["state"] == "uncertain"                     # recovered, visible
    assert row["resolved_by"] == "crash-recovery"          # attributable
    assert row["resolved_ts"] == clock.now
    assert row["item_id"] == "2"        # evidence kept: a late completion still maps
    # NEVER auto-replay: an idle tick dispatches nothing — uncertain does not
    # re-enqueue by itself (only pending records are claimable).
    enq = RecordingEnqueue()
    assert q2.dispatch_tick(is_playing=lambda: False, enqueue=enq) == {
        "ok": True, "dispatched": 0}
    assert enq.calls == []
    # Idempotent: further constructions recover nothing — timestamps included.
    clock.advance(10)
    q3 = make_queue(db, clock=clock)
    row3 = q3.get(rec["id"])
    assert row3["state"] == "uncertain"
    assert row3["resolved_by"] == "crash-recovery"
    assert row3["resolved_ts"] == row["resolved_ts"]        # NOT re-stamped
    assert q3.recover() == {"ok": True, "recovered": 0}     # explicit call too


def test_deliberate_resolve_only(tmp_path):
    clock = FakeClock()
    db = tmp_path / "p.db"
    q1 = make_queue(db, clock=clock)
    a1 = make_audio(tmp_path, "a1.mp3")
    a2 = make_audio(tmp_path, "a2.mp3")
    first = q1.admit(make_event(pane_id="P1", pane_pid=101, audio_path=str(a1)))
    clock.advance(1)
    second = q1.admit(make_event(pane_id="P2", pane_pid=102, audio_path=str(a2)))
    for _ in (first, second):
        q1.dispatch_tick(is_playing=lambda: False, enqueue=RecordingEnqueue())
    assert q1.get(first["id"])["state"] == "announcing"
    assert q1.get(second["id"])["state"] == "announcing"
    # The host died: a fresh construction recovers BOTH orphan claims...
    q2 = make_queue(db, clock=clock)
    assert q2.get(first["id"])["state"] == "uncertain"
    assert q2.get(second["id"])["state"] == "uncertain"
    # ...and they STAY uncertain: inits and idle ticks never replay them.
    enq = RecordingEnqueue()
    assert q2.dispatch_tick(is_playing=lambda: False, enqueue=enq) == {
        "ok": True, "dispatched": 0}
    q3 = make_queue(db, clock=clock)
    assert q3.get(first["id"])["state"] == "uncertain"
    assert enq.calls == []
    # Resolution is deliberate, both directions:
    resolved = q3.resolve(first["id"], "announced", actor="op")
    assert resolved["ok"] is True
    assert q3.get(first["id"])["state"] == "announced"     # the terminal verdict
    retried = q3.retry(second["id"], actor="op")
    assert retried["ok"] is True and retried["to"] == "pending"
    tick = q3.dispatch_tick(is_playing=lambda: False, enqueue=RecordingEnqueue())
    assert tick["dispatched"] == 1 and tick["claimed"] == second["id"]  # replayed ONLY now


# -- regeneration after GC (VS3.8, T7 "Regeneración") --------------------------------
#
# A regenerable record (pending/uncertain/evicted) whose audio_path is gone
# or null re-renders FROM TEXT through an INJECTABLE renderer. Tests only
# ever inject fakes: no network, no provider policy (the operator's explicit
# provider policy governs real renders). The single deliberate FSM edge out
# of a terminal — evicted -> pending — is traversed ONLY by a successful
# regenerate; the brain watcher's GC rule is untouched by this module.


def test_evicted_regenerates_after_gc(tmp_path):
    # REQUIRED (VS3.8): evicted record, audio deleted by the GC, fake
    # renderer => audio exists again, state pending, dispatchable, text
    # preserved verbatim.
    clock = FakeClock()
    q = make_queue(tmp_path / "p.db", clock=clock, max_records=1)
    a1 = make_audio(tmp_path, "a1.mp3")
    a2 = make_audio(tmp_path, "a2.mp3")
    first = q.admit(make_event(pane_id="P1", pane_pid=101, audio_path=str(a1)))
    clock.advance(1)
    second = q.admit(make_event(pane_id="P2", pane_pid=102, audio_path=str(a2)))
    evicted = q.get(first["id"])
    assert evicted["state"] == "evicted"                   # displaced, kept
    text_before = evicted["text"]
    os.unlink(a1)                                          # the brain watcher's GC
    assert not os.path.exists(a1)
    rendered = {}

    def fake_renderer(text, out_path):
        rendered["text"] = text
        Path(out_path).write_bytes(b"mp3")
        return out_path

    result = q.regenerate(first["id"], renderer=fake_renderer, actor="op")
    assert result["ok"] is True
    assert rendered["text"] == text_before                 # re-rendered FROM TEXT
    new_path = Path(result["audio_path"])
    assert new_path.exists() and new_path.stat().st_size > 0
    row = q.get(first["id"])
    assert row["state"] == "pending"                       # the new deliberate edge
    assert row["audio_path"] == str(new_path)
    assert row["text"] == text_before                      # verbatim, never rewritten
    assert row["resolved_by"] == "op" and row["resolved_ts"] == clock.now
    # Dispatchable again: the dispatcher claims it FIFO (oldest first_seen).
    tick = q.dispatch_tick(is_playing=lambda: False, enqueue=RecordingEnqueue())
    assert tick["dispatched"] == 1 and tick["claimed"] == first["id"]
    assert q.get(second["id"])["state"] == "pending"       # untouched by all this


def test_regenerate_refusals(tmp_path):
    clock = FakeClock()
    q = make_queue(tmp_path / "p.db", clock=clock)
    bogus = q.regenerate("no-such-record")
    assert bogus["ok"] is False and "unknown record id" in bogus["error"]
    # Audio still on disk: regeneration is not needed — typed refusal,
    # nothing mutated.
    audio = make_audio(tmp_path, "a.mp3")
    evicted = q.admit(make_event(pane_id="P1", pane_pid=101, audio_path=str(audio)))
    q.mark(evicted["id"], "evicted")
    present = q.regenerate(evicted["id"])
    assert present["ok"] is False and "audio already present" in present["error"]
    row = q.get(evicted["id"])
    assert row["state"] == "evicted" and row["resolved_by"] is None
    # announced is not regenerable even with the audio gone — nothing to replay.
    done = q.admit(make_event(pane_id="P2", pane_pid=102,
                              audio_path=str(make_audio(tmp_path, "b.mp3"))))
    q.mark(done["id"], "announcing")
    q.mark(done["id"], "announced")
    os.unlink(tmp_path / "b.mp3")
    terminal = q.regenerate(done["id"])
    assert terminal["ok"] is False and "not regenerable" in terminal["error"]
    # A raising/lying renderer is typed, and the ledger is untouched.
    os.unlink(audio)                                       # the GC reaps it now

    def boom(text, out_path):
        raise RuntimeError("tts down")

    failed = q.regenerate(evicted["id"], renderer=boom)
    assert failed["ok"] is False and "renderer failed" in failed["error"]
    none = q.regenerate(evicted["id"], renderer=lambda text, out_path: None)
    assert none["ok"] is False and "no audio path" in none["error"]
    ghost = q.regenerate(evicted["id"],
                         renderer=lambda text, out_path: str(tmp_path / "ghost.mp3"))
    assert ghost["ok"] is False and "no usable audio" in ghost["error"]
    row = q.get(evicted["id"])
    assert row["state"] == "evicted" and row["resolved_by"] is None


def test_regenerate_state_policy_and_race(tmp_path):
    clock = FakeClock()
    q = make_queue(tmp_path / "p.db", clock=clock)

    def fake_renderer(text, out_path):
        Path(out_path).write_bytes(b"mp3")
        return out_path

    # pending with a null audio (never rendered): regenerable, state unchanged.
    pending = q.admit(make_event())
    made = q.regenerate(pending["id"], renderer=fake_renderer, actor="op")
    assert made["ok"] is True and made["from"] == "pending" and made["state"] == "pending"
    assert Path(made["audio_path"]).exists()
    assert q.get(pending["id"])["state"] == "pending"
    # uncertain with missing audio: regenerated but NOT resolved (state per
    # caller — resolution stays a deliberate retry/resolve action).
    unsure = q.admit(make_event(pane_id="P2", pane_pid=102))
    q.mark(unsure["id"], "announcing")
    q.report_completion(unsure["id"], "failed")
    fixed = q.regenerate(unsure["id"], renderer=fake_renderer)
    assert fixed["ok"] is True and fixed["state"] == "uncertain"
    assert q.get(unsure["id"])["state"] == "uncertain"
    # A record moved mid-render is never overwritten: typed race refusal.
    racing = q.admit(make_event(pane_id="P3", pane_pid=103))
    other = make_queue(tmp_path / "p.db", clock=clock)

    def racing_renderer(text, out_path):
        assert other.mark(racing["id"], "cancelled")["ok"] is True
        Path(out_path).write_bytes(b"mp3")
        return out_path

    raced = q.regenerate(racing["id"], renderer=racing_renderer)
    assert raced["ok"] is False and "changed under the regeneration" in raced["error"]
    row = q.get(racing["id"])
    assert row["state"] == "cancelled"                     # the other writer won
    assert row["audio_path"] is None                       # ledger untouched


def test_default_renderer_bridges_local_chain(tmp_path, monkeypatch):
    # The production seam is a THIN bridge: engine env first (tts_engine),
    # then the engine's public synthesize with output_file — nothing else.
    import types

    out = tmp_path / "regen.mp3"
    calls = {}

    async def fake_synthesize(text, **kwargs):
        calls["text"] = text
        calls["output_file"] = kwargs.get("output_file")
        out.write_bytes(b"mp3")
        return b"mp3"

    fake_cli = types.ModuleType("agent_tts.cli")
    fake_cli.synthesize = fake_synthesize
    monkeypatch.setitem(sys.modules, "agent_tts.cli", fake_cli)
    monkeypatch.setitem(sys.modules, "tts_engine", types.ModuleType("tts_engine"))
    assert pq.default_renderer("hola", str(out)) == str(out)
    assert calls == {"text": "hola", "output_file": str(out)}
    assert out.read_bytes() == b"mp3"


def test_ledger_upgrade_adds_resolution_columns_in_place(tmp_path):
    # A pre-VS3.5 ledger (no resolved_by/resolved_ts) upgrades in place:
    # the ledger is never rebuilt, and recovery can stamp immediately.
    db = tmp_path / "p.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE pending_records (
            id TEXT PRIMARY KEY, epoch TEXT NOT NULL, pane_id TEXT NOT NULL,
            agent TEXT NOT NULL, status TEXT NOT NULL, label TEXT NOT NULL,
            text TEXT NOT NULL, audio_path TEXT, first_seen_ts REAL NOT NULL,
            last_seen_ts REAL NOT NULL, repeat_count INTEGER NOT NULL,
            state TEXT NOT NULL, announce_seq INTEGER NOT NULL,
            epoch_unverified INTEGER NOT NULL DEFAULT 0, pane_pid INTEGER,
            claimed_ts REAL, item_id TEXT);
        INSERT INTO pending_records VALUES
            ('legacy-1', 'pid-1', 'P1', 'codex', 'done', 'l', 't.', NULL,
             1.0, 1.0, 1, 'announcing', 1, 0, 1, 1.0, NULL);
    """)
    conn.commit()
    conn.close()
    q = make_queue(db)
    rec = q.list_all()[0]
    assert rec["id"] == "legacy-1"
    assert rec["state"] == "uncertain"                     # recovered on upgrade
    assert rec["resolved_by"] == "crash-recovery"          # new column, stamped
    assert rec["resolved_ts"] is not None
