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

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from ..pairing import ensure_private_dir
from .errors import StoreError

#: Ledger lives at ``<state_dir>/messages/``; the name is owned here.
MESSAGES_DIRNAME = "messages"
LEDGER_FILENAME = "ledger.json"

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
    """Load/save the ledger; the only code that touches the file."""

    def __init__(self, state_dir: Path, clock: Optional[Callable[[], float]] = None):
        self._dir = Path(state_dir) / MESSAGES_DIRNAME
        self._path = self._dir / LEDGER_FILENAME
        self._clock = clock or time.time
        self._records: Dict[str, dict] = {}
        if self._path.exists():
            try:
                raw = self._path.read_text(encoding="utf-8")
            except OSError as exc:
                raise StoreError(f"cannot read the message ledger: {exc}") from exc
            self._records = _decode(raw)

    # -- read side ----------------------------------------------------------

    def get(self, message_id: str) -> Message:
        record = self._records.get(message_id)
        if record is None:
            raise KeyError(message_id)
        return Message.from_dict(record)

    def all(self) -> List[Message]:
        return [
            Message.from_dict(r)
            for _, r in sorted(self._records.items(), key=lambda kv: kv[1]["created"])
        ]

    def upsert(self, message: Message) -> None:
        self._records[message.id] = message.as_dict()

    def drop(self, message_id: str) -> None:
        self._records.pop(message_id, None)

    # -- write side ----------------------------------------------------------

    def save(self) -> None:
        """Prune, then atomically replace the ledger (never a partial file)."""
        now = self._clock()
        for mid in [
            mid
            for mid, rec in self._records.items()
            if rec.get("read") and now - float(rec.get("updated", 0)) > RETENTION_S
        ]:
            del self._records[mid]
        payload = json.dumps(
            {"version": 1, "messages": self._records},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        ensure_private_dir(self._dir)
        tmp = self._dir / (LEDGER_FILENAME + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, payload.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self._path)
