"""The ledger is shared by the long-lived server and short-lived CLI processes.

Each process holds its own in-memory view. A save must never write a stale
view over records another process saved in the meantime, and concurrent
saves must neither fail nor lose messages.
"""

from __future__ import annotations

import json
import threading

from herdr_brain.walky.store import LEDGER_FILENAME, MESSAGES_DIRNAME, Message, MessageStore


def make(mid: str, created: float, state: str = "queued", updated: float | None = None) -> Message:
    return Message(
        id=mid,
        from_kind="agent",
        from_key="w1:p1",
        from_address="w1:p1#tok",
        from_harness="opencode",
        from_label="sender",
        from_device=None,
        to_pane="w1:p2",
        to_token="tok2",
        body="body " + mid,
        state=state,
        created=created,
        updated=created if updated is None else updated,
    )


def ledger_ids(state_dir) -> set:
    raw = (state_dir / MESSAGES_DIRNAME / LEDGER_FILENAME).read_text(encoding="utf-8")
    return set(json.loads(raw)["messages"])


def test_a_stale_view_never_overwrites_another_process_save(tmp_path):
    server = MessageStore(tmp_path, clock=lambda: 100.0)  # loaded before the CLI ran
    cli = MessageStore(tmp_path, clock=lambda: 100.0)
    cli.upsert(make("m_cli", 10.0))
    cli.save()

    server.upsert(make("m_server", 11.0))
    server.save()

    assert ledger_ids(tmp_path) == {"m_cli", "m_server"}


def test_a_long_lived_view_sees_records_saved_by_another_process(tmp_path):
    server = MessageStore(tmp_path, clock=lambda: 100.0)
    cli = MessageStore(tmp_path, clock=lambda: 100.0)
    cli.upsert(make("m_cli", 10.0))
    cli.save()

    assert [m.id for m in server.all()] == ["m_cli"]
    assert server.get("m_cli").body == "body m_cli"


def test_the_newer_update_of_one_record_wins(tmp_path):
    first = MessageStore(tmp_path, clock=lambda: 100.0)
    first.upsert(make("m1", 10.0))
    first.save()

    a = MessageStore(tmp_path, clock=lambda: 100.0)
    b = MessageStore(tmp_path, clock=lambda: 100.0)
    a.upsert(make("m1", 10.0, state="typed", updated=20.0))
    b.upsert(make("m1", 10.0, state="canceled", updated=15.0))
    a.save()
    b.save()  # older change saved later: must not undo the delivery

    assert MessageStore(tmp_path).get("m1").state == "typed"


def test_concurrent_saves_lose_nothing_and_never_fail(tmp_path):
    errors = []

    def worker(prefix: str) -> None:
        try:
            for i in range(25):
                store = MessageStore(tmp_path, clock=lambda: 100.0)
                store.upsert(make(f"{prefix}-{i}", float(i)))
                store.save()
        except Exception as exc:  # noqa: BLE001 — the test reports any failure
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(name,)) for name in "abcd"]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(ledger_ids(tmp_path)) == 100
    leftovers = [p.name for p in (tmp_path / MESSAGES_DIRNAME).iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []
