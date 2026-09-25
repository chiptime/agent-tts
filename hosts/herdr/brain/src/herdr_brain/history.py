"""Append-only JSONL call history.

Persists every /ask turn (user + assistant) so the call transcript
survives service restarts: the server seeds the conversation ring from
the tail at boot, and the PWA repaints the drawer from GET
/call-history. One global file — the call history is the call, not a
per-session log.

Concurrency mirrors ConversationStore: a single lock guards the file.
A crash mid-append can leave a truncated trailing line; loads skip any
line that is not a well-formed record instead of failing the boot.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

HISTORY_FILENAME = "call_history.jsonl"

ROLES = ("user", "assistant")

# Boot compaction thresholds: past the soft limit the file is rewritten
# keeping only the recent tail. Runs opportunistically at load-time
# (boot), never on append — appends stay O(1).
COMPACT_ABOVE = 2_000
COMPACT_KEEP = 1_000


def default_history_path(settings) -> Path:
    """History file location: beside the audio dir (same state root)."""
    return Path(settings.audio_dir).parent / HISTORY_FILENAME


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _valid_record(record: object) -> Optional[dict]:
    """Returns the record as a plain dict, or None when malformed."""
    if not isinstance(record, dict):
        return None
    role = record.get("role")
    text = record.get("text")
    ts = record.get("ts")
    if role not in ROLES or not isinstance(text, str) or not isinstance(ts, str):
        return None
    return {"ts": ts, "role": role, "text": text}


class HistoryStore:
    """Thread-safe JSONL call history (one append-only file)."""

    def __init__(self, path):
        self._path = Path(path)
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def append(self, role: str, text: str) -> None:
        """Writes one record as a single JSON line (ts is stamped now)."""
        record = {"ts": _utc_now_iso(), "role": role, "text": text or ""}
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def load(self, last_n: Optional[int] = None) -> list:
        """Returns the stored records, oldest first (optionally only
        ``last_n``). Malformed lines — e.g. a truncated trailing line
        from a crash mid-write — are skipped, never raised."""
        with self._lock:
            records = self._read_all()
        return self._tail(records, last_n)

    def load_and_compact(self, last_n: Optional[int] = None) -> list:
        """Boot-side load: same as :meth:`load`, plus opportunistic
        compaction — when the file holds more than ``COMPACT_ABOVE``
        records it is rewritten (atomically) keeping only the last
        ``COMPACT_KEEP``."""
        with self._lock:
            records = self._read_all()
            if len(records) > COMPACT_ABOVE:
                records = records[-COMPACT_KEEP:]
                self._rewrite(records)
        return self._tail(records, last_n)

    def clear(self) -> None:
        """Drops the whole history (the file is removed)."""
        with self._lock:
            try:
                self._path.unlink()
            except FileNotFoundError:
                pass

    # -- internals (callers already hold the lock) --------------------

    @staticmethod
    def _tail(records: list, last_n: Optional[int]) -> list:
        if last_n is None:
            return records
        return records[-last_n:] if last_n > 0 else []

    def _read_all(self) -> list:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        records = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                record = _valid_record(json.loads(line))
            except ValueError:  # json.JSONDecodeError — corrupt line, skip
                continue
            if record is not None:
                records.append(record)
        return records

    def _rewrite(self, records: list) -> None:
        """Crash-safe rewrite: temp file + atomic replace."""
        tmp = self._path.with_name(self._path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        os.replace(tmp, self._path)
