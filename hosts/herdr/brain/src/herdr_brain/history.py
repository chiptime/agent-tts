"""SQLite-backed call history.

Persists every /ask turn (user + assistant) so the call transcript
survives service restarts: the server seeds the conversation ring from
the tail at boot, and the PWA repaints the drawer from GET
/call-history. One global database — the call history is the call, not
a per-session log.

Storage: stdlib sqlite3, table ``turns(id, ts, role, text)`` with an
index on ``ts``. Concurrency mirrors ConversationStore: a single lock
guards every access, and each call opens, uses and closes its own
connection — a connection is never shared across threads, so sqlite's
default ``check_same_thread`` stays on. The public API is the one the
JSONL backend exposed (append / load / load_before / clear / boot
compaction), so server and PWA are unchanged.

Migration: when the legacy ``call_history.jsonl`` sits beside the
database at construction, its records are imported (corrupt lines are
skipped like before; skipped entirely if the DB already holds rows) and
the file is atomically renamed to ``call_history.jsonl.imported`` so no
boot ever double-imports.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

HISTORY_DB_FILENAME = "call_history.db"
LEGACY_HISTORY_FILENAME = "call_history.jsonl"
IMPORTED_SUFFIX = ".imported"

ROLES = ("user", "assistant")

# Boot compaction thresholds: past the soft limit the table is trimmed
# keeping only the recent tail. Runs opportunistically at load-time
# (boot), never on append — appends stay O(1).
COMPACT_ABOVE = 2_000
COMPACT_KEEP = 1_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL
)
"""

_INDEX = "CREATE INDEX IF NOT EXISTS idx_turns_ts ON turns (ts)"


def default_history_path(settings) -> Path:
    """History database location: beside the audio dir (same state root)."""
    return Path(settings.audio_dir).parent / HISTORY_DB_FILENAME


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(ts: str):
    """Best-effort ISO 8601 parse; None for anything else."""
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def is_strictly_older(ts: str, before_ts: str) -> bool:
    """True when record ``ts`` is strictly older than the ``before`` cursor.

    Lexical compare is WRONG for ISO timestamps of varying precision
    (``...T10:00:00+00:00`` vs ``...T10:00:00.5+00:00``), so both sides
    parse to datetime first. Any parse failure — or a mixed
    naive/aware pair, which Python refuses to compare — falls back to
    the raw string compare.
    """
    left, right = _parse_ts(ts), _parse_ts(before_ts)
    if left is not None and right is not None:
        try:
            return left < right
        except TypeError:
            pass
    return ts < before_ts


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
    """Thread-safe sqlite call history (one database, one table).

    Connection-per-call for simplicity: every method opens its own
    connection under the lock and closes it before returning, so there
    is no long-lived connection to guard and no WAL side files linger
    after :meth:`clear`.
    """

    def __init__(self, path):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._ensure_schema()
        self._migrate_legacy_jsonl()

    @property
    def path(self) -> Path:
        return self._path

    def append(self, role: str, text: str) -> None:
        """Writes one record (ts is stamped now)."""
        record = {"ts": _utc_now_iso(), "role": role, "text": text or ""}
        with self._lock:
            if not self._path.exists():  # clear() removed the file
                self._ensure_schema()
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        "INSERT INTO turns (ts, role, text) VALUES (?, ?, ?)",
                        (record["ts"], record["role"], record["text"]),
                    )
            finally:
                conn.close()

    def load(self, last_n: Optional[int] = None) -> list:
        """Returns the stored records, oldest first (optionally only
        ``last_n``). Rows outside the known roles — only reachable
        through direct DB writes — are skipped, never raised."""
        with self._lock:
            records = self._select_all()
        return self._tail(records, last_n)

    def load_before(self, before_ts: Optional[str], limit: int) -> list:
        """Returns up to ``limit`` records strictly OLDER than
        ``before_ts``, oldest first; ``before_ts=None`` returns the
        newest page.

        Pagination cursor for GET /call-history. Reads the whole table
        and filters in Python using the timestamp-aware comparison (see
        :func:`is_strictly_older`) — fine at the ≤2000-row compaction
        bound; ``limit <= 0`` yields an empty page like :meth:`load`.
        """
        with self._lock:
            records = self._select_all()
        if before_ts is not None:
            records = [r for r in records if is_strictly_older(r["ts"], before_ts)]
        return self._tail(records, limit)

    def load_and_compact(self, last_n: Optional[int] = None) -> list:
        """Boot-side load: same as :meth:`load`, plus opportunistic
        compaction — when the table holds more than ``COMPACT_ABOVE``
        records it is trimmed (one DELETE) keeping only the last
        ``COMPACT_KEEP``."""
        with self._lock:
            records = self._select_all()
            if len(records) > COMPACT_ABOVE:
                records = records[-COMPACT_KEEP:]
                conn = self._connect()
                try:
                    with conn:
                        conn.execute(
                            "DELETE FROM turns WHERE id NOT IN ("
                            "SELECT id FROM turns WHERE role IN (?, ?) "
                            "ORDER BY id DESC LIMIT ?)",
                            (*ROLES, COMPACT_KEEP),
                        )
                finally:
                    conn.close()
        return self._tail(records, last_n)

    def clear(self) -> None:
        """Drops the whole history (the database file is removed)."""
        with self._lock:
            try:
                self._path.unlink()
            except FileNotFoundError:
                pass

    # -- internals (callers already hold the lock) --------------------

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def _ensure_schema(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            with conn:
                conn.execute(_SCHEMA)
                conn.execute(_INDEX)
        finally:
            conn.close()

    def _select_all(self) -> list:
        """Every valid record, oldest first (insertion order = id order).

        A missing database file (after :meth:`clear`) reads as empty;
        connecting anyway would create a bare file with no table.
        """
        if not self._path.exists():
            return []
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT ts, role, text FROM turns WHERE role IN (?, ?) ORDER BY id",
                ROLES,
            ).fetchall()
        finally:
            conn.close()
        return [{"ts": ts, "role": role, "text": text} for ts, role, text in rows]

    def _count_rows(self, conn: sqlite3.Connection) -> int:
        return int(conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0])

    def _migrate_legacy_jsonl(self) -> None:
        """One-time import of the JSONL predecessor, then retire it.

        Corrupt or invalid lines are skipped exactly like the JSONL
        reader always did. When the DB already holds rows the import is
        skipped — but the legacy file is still renamed so no later boot
        re-triggers the migration.
        """
        legacy = self._path.parent / LEGACY_HISTORY_FILENAME
        if not legacy.exists():
            return
        with self._lock:
            records = self._read_legacy(legacy)
            conn = self._connect()
            try:
                if records and self._count_rows(conn) == 0:
                    with conn:
                        conn.executemany(
                            "INSERT INTO turns (ts, role, text) VALUES (?, ?, ?)",
                            [(r["ts"], r["role"], r["text"]) for r in records],
                        )
            finally:
                conn.close()
            os.replace(legacy, legacy.with_name(legacy.name + IMPORTED_SUFFIX))

    @staticmethod
    def _read_legacy(path: Path) -> list:
        """Parses the old JSONL file, skipping malformed lines."""
        try:
            raw = path.read_text(encoding="utf-8")
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

    @staticmethod
    def _tail(records: list, last_n: Optional[int]) -> list:
        if last_n is None:
            return records
        return records[-last_n:] if last_n > 0 else []
