"""The message ledger: one private, atomically-written JSON file.

Everything brain messages remembers lives in
``<state_dir>/messages/ledger.json`` — a dict of message records keyed
by id. The directory is 0700 and the file 0600 (bodies are private
conversation text); every write goes through a temporary file in the
same directory plus :func:`os.replace`, so a crash mid-write leaves the
previous ledger intact and never a half-written one. A ledger that
cannot be parsed is reported loudly instead of being silently reset:
wiping conversation history to keep working would be the worse failure.

Volume is bounded twice over — the rate limits cap how fast messages
accumulate, and each save prunes records that are read and older than a
day — so a single JSON document stays the simplest honest store.
"""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from ..pairing import ensure_private_dir
from .errors import StoreError

#: Ledger lives at ``<state_dir>/messages/``; the name is owned here.
MESSAGES_DIRNAME = "messages"
LEDGER_FILENAME = "ledger.json"
#: Lock file serializing saves across the server and CLI processes.
LOCK_FILENAME = "ledger.lock"

#: Read messages older than this are dropped at the next save.
RETENTION_S = 86400.0

#: Every delivery state a record can carry.
STATES = (
    "queued",
    "held",
    "typed",
    "waiting_in_inbox",
    "refused",
    "uncertain",
    "canceled",
)
#: States that no longer participate in delivery.
DEAD_STATES = ("refused", "uncertain", "canceled")


@dataclass
class Message:
    """One brain message: the full record, both for delivery and inbox."""

    id: str
    from_kind: str  # "agent" | "operator"
    from_key: str  # rate-limit key: pane id, or "operator:<device>"
    from_address: Optional[str]  # agent address (pane#token); None for operator
    from_harness: str
    from_label: str
    from_device: Optional[str]  # operator device label
    to_pane: str
    to_token: str
    body: str
    state: str
    created: float
    updated: float
    hops: int = 0
    parent_id: Optional[str] = None
    read: bool = False
    reason: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "Message":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        missing = known - set(raw)
        if missing:
            raise StoreError(f"ledger record is missing fields: {sorted(missing)}")
        return cls(**{k: raw[k] for k in known})


def _decode(raw_text: str) -> Dict[str, dict]:
    try:
        payload = json.loads(raw_text)
    except ValueError as exc:
        raise StoreError(f"message ledger is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("messages"), dict):
        raise StoreError("message ledger has an unexpected shape")
    records: Dict[str, dict] = {}
    for mid, record in payload["messages"].items():
        Message.from_dict(record)  # validates; StoreError on a bad record
        records[str(mid)] = record
    return records


class MessageStore:
    """Load/save the ledger; the only code that touches the file.

    The long-lived server and short-lived CLI processes each hold their own
    view of the same file, so this store never writes a view blindly:

    * reads refresh from disk when the file changed (one stat per read),
      keeping this process's unsaved changes on top;
    * a save takes an exclusive lock, re-reads the file, applies only the
      records THIS view changed (a record already newer on disk wins), and
      replaces the file through a uniquely named temporary file.
    """

    def __init__(self, state_dir: Path, clock: Optional[Callable[[], float]] = None):
        self._dir = Path(state_dir) / MESSAGES_DIRNAME
        self._path = self._dir / LEDGER_FILENAME
        self._lock_path = self._dir / LOCK_FILENAME
        self._clock = clock or time.time
        self._records: Dict[str, dict] = {}
        # Unsaved changes of this view: id -> record, or None for a drop.
        self._dirty: Dict[str, Optional[dict]] = {}
        self._disk_key: Optional[tuple] = None
        self._thread_lock = threading.Lock()
        self._refresh()

    # -- disk view ------------------------------------------------------------

    def _stat_key(self) -> Optional[tuple]:
        try:
            st = self._path.stat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise StoreError(f"cannot read the message ledger: {exc}") from exc
        return (st.st_ino, st.st_mtime_ns, st.st_size)

    def _read_disk(self) -> Dict[str, dict]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise StoreError(f"cannot read the message ledger: {exc}") from exc
        return _decode(raw)

    def _overlay(self, disk: Dict[str, dict]) -> Dict[str, dict]:
        merged = dict(disk)
        for mid, record in self._dirty.items():
            if record is None:
                merged.pop(mid, None)
                continue
            current = merged.get(mid)
            if current is not None and float(current.get("updated", 0)) > float(
                record.get("updated", 0)
            ):
                continue  # another process saved a newer change of this record
            merged[mid] = record
        return merged

    def _refresh(self) -> None:
        key = self._stat_key()
        if key == self._disk_key and self._disk_key is not None:
            return
        disk = self._read_disk()
        self._disk_key = key
        self._records = self._overlay(disk)

    # -- read side ----------------------------------------------------------

    def get(self, message_id: str) -> Message:
        with self._thread_lock:
            self._refresh()
            record = self._records.get(message_id)
        if record is None:
            raise KeyError(message_id)
        return Message.from_dict(record)

    def all(self) -> List[Message]:
        with self._thread_lock:
            self._refresh()
            items = sorted(self._records.items(), key=lambda kv: kv[1]["created"])
        return [Message.from_dict(r) for _, r in items]

    def upsert(self, message: Message) -> None:
        record = message.as_dict()
        with self._thread_lock:
            self._records[message.id] = record
            self._dirty[message.id] = record

    def drop(self, message_id: str) -> None:
        with self._thread_lock:
            self._records.pop(message_id, None)
            self._dirty[message_id] = None

    # -- write side ----------------------------------------------------------

    def save(self) -> None:
        """Merge this view into the file under an exclusive lock, prune, and
        atomically replace the ledger (never a partial file)."""
        with self._thread_lock:
            ensure_private_dir(self._dir)
            lock_fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                merged = self._overlay(self._read_disk())
                now = self._clock()
                for mid in [
                    mid
                    for mid, rec in merged.items()
                    if rec.get("read") and now - float(rec.get("updated", 0)) > RETENTION_S
                ]:
                    del merged[mid]
                payload = json.dumps(
                    {"version": 1, "messages": merged},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                tmp = self._dir / f"{LEDGER_FILENAME}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    try:
                        os.write(fd, payload.encode("utf-8"))
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                    os.replace(tmp, self._path)
                except BaseException:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                    raise
                self._records = merged
                self._dirty = {}
                self._disk_key = self._stat_key()
            finally:
                os.close(lock_fd)  # closing the descriptor releases the lock
