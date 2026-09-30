"""SQLite-backed persistent store for consolidated context reports.

Implements the persistence half of PRD decision D08 (``docs/prds/
herdr-brain-on-demand-context.md``): the latest report plus references
are retained for a bounded horizon, isolated per main call/context and
per scope/normalized-interval/timezone, and publication is atomic and
limited to complete successful revisions (FR-27, FR-33, FR-34).

RETENTION IS NOT A STALENESS TTL. ``retention`` (default 24h) bounds how
long a published report stays retrievable from this store. It says
nothing about freshness: every qualifying query must run its own
freshness check against live sources (FR-28) before reusing a stored
report, and that check lives OUTSIDE this module. An unexpired published
report is therefore NOT implicitly fresh (D08).

Revision semantics (FR-33 and the PRD concurrency rules):

- ``begin_build`` reserves a strictly increasing per-store sequence
  number and a ``building`` row that no read API ever returns.
- ``publish`` runs in ONE atomic ``BEGIN IMMEDIATE`` transaction: the
  previous live revision for the key is marked superseded
  (``superseded_by`` = new id), the building row becomes the published
  latest, and a crash or injected failure mid-transaction rolls back,
  leaving the previous report untouched.
- A cancelled handle, an already-finalized handle, or a handle whose
  sequence is older than an already-published revision for the same key
  is rejected with ``StaleBuildError``: a cancelled or superseded
  request can never overwrite a newer report.
- ``mark_refresh_failed`` flips the latest published revision for the
  key to ``refresh_failed``: still retrievable through ``get_latest``
  for diagnostics, never returned by ``get_current`` — a stale report is
  never presented as current after a failed refresh.

Restart: reopening the same path restores unexpired published and
refresh_failed reports; leftover ``building`` rows from a previous
process are discarded on open and can never be published.

Cleanup (FR-34): ``purge_expired`` deletes at most ``limit`` rows whose
retention horizon has passed — expired latests plus superseded revisions
riding the same horizon, since D08 only needs the latest report per key.
It is bounded per call; callers may invoke it opportunistically.

Concurrency mirrors ``HistoryStore``: one lock guards every access, and
each call opens, uses and closes its own sqlite connection, so a
connection is never shared across threads. "Now" is injected (``clock``)
and must return timezone-aware UTC datetimes; a naive clock result
raises ``ReportStoreError``. The ``expired`` status from the PRD
vocabulary is DERIVED (``clock() >= retention_expires_at``) and never
stored: expired rows are simply excluded from every read.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional

REPORTS_DB_FILENAME = "reports.db"
SCHEMA_VERSION = 1

ReportStatus = Literal["building", "published", "refresh_failed", "expired"]

_REFERENCE_FIELDS = ("source_id", "kind", "locator", "revision_token", "retrieved_at")
_MANIFEST_FIELDS = ("source_id", "revision_token", "state")

_SELECT_COLUMNS = (
    "report_id, main_call_context_id, scope, interval_key, interval_start,"
    " interval_end, interval_zone, timezone, status, body, references_json,"
    " source_manifest_json, created_at, retention_expires_at, superseded_by"
)

# Isolation-key predicate shared by every keyed statement (D08).
_KEY_WHERE = (
    "main_call_context_id = ? AND scope = ? AND interval_key = ? AND timezone = ?"
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id TEXT NOT NULL UNIQUE,
    main_call_context_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    interval_key TEXT NOT NULL,
    interval_start TEXT NOT NULL,
    interval_end TEXT NOT NULL,
    interval_zone TEXT NOT NULL,
    timezone TEXT NOT NULL,
    status TEXT NOT NULL,
    body TEXT NOT NULL,
    references_json TEXT NOT NULL,
    source_manifest_json TEXT NOT NULL,
    created_at TEXT,
    retention_expires_at TEXT,
    superseded_by TEXT
)
"""

_INDEX_KEY = (
    "CREATE INDEX IF NOT EXISTS idx_reports_key ON reports"
    " (main_call_context_id, scope, interval_key, timezone)"
)
_INDEX_RETENTION = (
    "CREATE INDEX IF NOT EXISTS idx_reports_retention"
    " ON reports (retention_expires_at)"
)


class ReportStoreError(Exception):
    """Base class for report store failures."""


class MalformedReportInput(ReportStoreError, ValueError):
    """Malformed report input: bad key/interval/body shape, or a
    references/source_manifest entry that is not a mapping of exactly the
    expected string fields (validated on write, never persisted)."""


class StaleBuildError(ReportStoreError):
    """A build handle that can no longer be published: it was cancelled,
    already finalized, or its sequence is older than an already-published
    revision for the same key (FR-33: stale requests never overwrite a
    newer report)."""


@dataclass(frozen=True)
class Reference:
    """One evidence reference inside a report (PRD Data Schema).

    ``retrieved_at`` is a free-form ISO 8601 string stamped by the
    evidence layer; the store validates it is a string, nothing more.
    """

    source_id: str
    kind: str
    locator: str
    revision_token: str
    retrieved_at: str


@dataclass(frozen=True)
class ManifestEntry:
    """One source-manifest snapshot entry: the revision token plus a
    free-form ``state`` string (open/closed/status snapshot text)."""

    source_id: str
    revision_token: str
    state: str


@dataclass(frozen=True)
class NormalizedInterval:
    """The normalized query interval: the ``Period.key`` string plus its
    UTC start/end instants and the original zone name."""

    key: str
    start: datetime
    end: datetime
    zone_name: str


@dataclass(frozen=True)
class ReportKey:
    """Isolation key (D08): main call context + scope + normalized
    interval (the ``Period.key`` string) + timezone.

    Reports under different keys never see each other — no cross-call
    leakage. ``timezone`` is its own component even though a Period key
    embeds the zone, matching the PRD schema column.
    """

    main_call_context_id: str
    scope: str
    interval_key: str
    timezone: str


@dataclass(frozen=True)
class BuildHandle:
    """An in-flight build granted by ``begin_build``.

    ``sequence`` is the store-wide strictly increasing build number
    (never reused, even after deletes): publish uses it to reject older
    revisions trying to overwrite newer ones.
    """

    report_id: str
    key: ReportKey
    sequence: int


@dataclass(frozen=True)
class ReportRecord:
    """A stored report revision, as returned by the read APIs."""

    report_id: str
    main_call_context_id: str
    scope: str
    normalized_interval: NormalizedInterval
    timezone: str
    status: ReportStatus
    body: str
    references: tuple[Reference, ...]
    source_manifest: tuple[ManifestEntry, ...]
    created_at: datetime
    retention_expires_at: datetime
    superseded_by: Optional[str]


def default_reportstore_path(settings) -> Path:
    """Report database location: beside the audio dir (same state root
    the call-history database uses)."""
    return Path(settings.audio_dir).parent / REPORTS_DB_FILENAME


def _iso(moment: datetime) -> str:
    """Canonical storage form: UTC-normalized ISO 8601.

    Both stored timestamps and read-time bounds go through this single
    helper, so the lexical SQL comparisons stay instant-correct.
    """
    return moment.astimezone(timezone.utc).isoformat()


def _require_str(value: object, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise MalformedReportInput(
            f"{label} must be a non-empty string, got {value!r}"
        )


def _parse_interval_key(interval_key: object) -> NormalizedInterval:
    """Denormalizes a ``Period.key`` string into its interval parts.

    The key format is documented in ``periods.Period.key``:
    ``kind|start|end|zone`` with UTC ISO instants. Anything else — wrong
    part count, unparseable or naive bounds, empty kind/zone — is
    malformed input and rejected (FR-37 spirit: bounds are validated,
    never guessed).
    """
    if not isinstance(interval_key, str):
        raise MalformedReportInput(
            f"interval_key must be a Period.key string, got {type(interval_key).__name__}"
        )
    parts = interval_key.split("|")
    if len(parts) != 4 or not all(parts):
        raise MalformedReportInput(
            f"interval_key must be 'kind|start|end|zone' (Period.key), got {interval_key!r}"
        )
    kind, start_iso, end_iso, zone_name = parts
    try:
        start = datetime.fromisoformat(start_iso)
        end = datetime.fromisoformat(end_iso)
    except ValueError as exc:
        raise MalformedReportInput(
            f"interval_key bounds must be ISO 8601 instants, got {interval_key!r}"
        ) from exc
    if start.tzinfo is None or end.tzinfo is None:
        raise MalformedReportInput(
            f"interval_key bounds must be timezone-aware instants, got {interval_key!r}"
        )
    return NormalizedInterval(
        key=interval_key,
        start=start.astimezone(timezone.utc),
        end=end.astimezone(timezone.utc),
        zone_name=zone_name,
    )


def _coerce_items(
    items: object, cls: type, fields: tuple[str, ...], label: str
) -> tuple:
    """Validates and freezes a references/manifest list for storage.

    Accepts the frozen dataclass instances or plain mappings with EXACTLY
    the expected fields, every value a string (extra keys are rejected —
    they are almost always typos). Anything else raises
    ``MalformedReportInput`` before the database is touched.
    """
    if isinstance(items, (str, bytes, Mapping)):
        raise MalformedReportInput(
            f"{label} must be an iterable of entries, got {type(items).__name__}"
        )
    try:
        iterator = iter(items)
    except TypeError:
        raise MalformedReportInput(
            f"{label} must be an iterable of entries, got {type(items).__name__}"
        ) from None
    coerced = []
    for item in iterator:
        if isinstance(item, cls):
            values = {name: getattr(item, name) for name in fields}
        elif isinstance(item, Mapping):
            if set(item.keys()) != set(fields):
                raise MalformedReportInput(
                    f"{label} entries must have exactly the fields {list(fields)},"
                    f" got {sorted(item.keys())}"
                )
            values = {name: item[name] for name in fields}
        else:
            raise MalformedReportInput(
                f"{label} entries must be mappings or {cls.__name__} instances,"
                f" got {type(item).__name__}"
            )
        for name, value in values.items():
            if not isinstance(value, str):
                raise MalformedReportInput(
                    f"{label} entry field {name!r} must be a str,"
                    f" got {type(value).__name__}"
                )
        coerced.append(cls(**values))
    return tuple(coerced)


def _row_to_record(row: tuple) -> ReportRecord:
    """Rebuilds a ReportRecord from a stored row."""
    (
        report_id,
        context_id,
        scope,
        interval_key,
        interval_start,
        interval_end,
        interval_zone,
        tz,
        status,
        body,
        references_json,
        manifest_json,
        created_at,
        expires_at,
        superseded_by,
    ) = row
    return ReportRecord(
        report_id=report_id,
        main_call_context_id=context_id,
        scope=scope,
        normalized_interval=NormalizedInterval(
            key=interval_key,
            start=datetime.fromisoformat(interval_start),
            end=datetime.fromisoformat(interval_end),
            zone_name=interval_zone,
        ),
        timezone=tz,
        status=status,
        body=body,
        references=tuple(Reference(**item) for item in json.loads(references_json)),
        source_manifest=tuple(
            ManifestEntry(**item) for item in json.loads(manifest_json)
        ),
        created_at=datetime.fromisoformat(created_at),
        retention_expires_at=datetime.fromisoformat(expires_at),
        superseded_by=superseded_by,
    )


class ReportStore:
    """Thread-safe sqlite store for consolidated reports (D08).

    See the module docstring for the retention-vs-freshness contract,
    the atomic publication rules, and the restart/cleanup semantics.
    """

    def __init__(
        self,
        path,
        *,
        clock: Optional[Callable[[], datetime]] = None,
        retention: timedelta = timedelta(hours=24),
    ):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._clock: Callable[[], datetime] = clock or (
            lambda: datetime.now(timezone.utc)
        )
        if not isinstance(retention, timedelta) or retention <= timedelta(0):
            raise ValueError("retention must be a positive timedelta")
        self._retention = retention
        self._ensure_schema()

    @property
    def path(self) -> Path:
        return self._path

    # -- write path ------------------------------------------------------

    def begin_build(self, key: ReportKey) -> BuildHandle:
        """Reserves a build slot: a ``building`` row no read ever returns.

        Returns a ``BuildHandle`` carrying a strictly increasing
        per-store sequence number. Multiple concurrent builds for the
        same key are allowed; publish decides who wins.
        """
        key = self._validate_key(key)
        interval = _parse_interval_key(key.interval_key)
        report_id = uuid.uuid4().hex
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "INSERT INTO reports (report_id, main_call_context_id, scope,"
                    " interval_key, interval_start, interval_end, interval_zone,"
                    " timezone, status, body, references_json, source_manifest_json,"
                    " created_at, retention_expires_at, superseded_by)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'building', '', '[]', '[]',"
                    " NULL, NULL, NULL)",
                    (
                        report_id,
                        key.main_call_context_id,
                        key.scope,
                        key.interval_key,
                        _iso(interval.start),
                        _iso(interval.end),
                        interval.zone_name,
                        key.timezone,
                    ),
                )
                sequence = int(cursor.lastrowid)
            finally:
                conn.close()
        return BuildHandle(report_id=report_id, key=key, sequence=sequence)

    def publish(
        self,
        handle: BuildHandle,
        *,
        body: str,
        references: Iterable,
        source_manifest: Iterable,
    ) -> ReportRecord:
        """Atomically publishes a complete revision (FR-33).

        ONE ``BEGIN IMMEDIATE`` transaction marks the previous live
        revision for the key superseded and flips the building row to
        ``published`` with body, references and manifest. Any exception
        mid-transaction (an injected failure, a crash) rolls back and
        leaves the previous report untouched.

        Input validation happens BEFORE the transaction, so a rejected
        payload leaves the handle usable for a corrected retry. A handle
        that was cancelled, already finalized, or older than an already
        published revision for the key raises ``StaleBuildError`` and
        never overwrites a newer report.
        """
        if not isinstance(handle, BuildHandle):
            raise MalformedReportInput(
                f"handle must be a BuildHandle, got {type(handle).__name__}"
            )
        if not isinstance(body, str):
            raise MalformedReportInput(
                f"body must be a str, got {type(body).__name__}"
            )
        refs = _coerce_items(references, Reference, _REFERENCE_FIELDS, "references")
        manifest = _coerce_items(
            source_manifest, ManifestEntry, _MANIFEST_FIELDS, "source_manifest"
        )
        key = self._validate_key(handle.key)
        references_json = json.dumps([asdict(item) for item in refs])
        manifest_json = json.dumps([asdict(item) for item in manifest])
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                self._assert_publishable(conn, handle, key)
                now = self._now()
                expires = now + self._retention
                conn.execute(
                    "UPDATE reports SET superseded_by = ? WHERE"
                    f" {_KEY_WHERE} AND status IN ('published', 'refresh_failed')"
                    " AND superseded_by IS NULL",
                    (
                        handle.report_id,
                        key.main_call_context_id,
                        key.scope,
                        key.interval_key,
                        key.timezone,
                    ),
                )
                conn.execute(
                    "UPDATE reports SET status = 'published', body = ?,"
                    " references_json = ?, source_manifest_json = ?, created_at = ?,"
                    " retention_expires_at = ?"
                    " WHERE report_id = ? AND status = 'building'",
                    (
                        body,
                        references_json,
                        manifest_json,
                        _iso(now),
                        _iso(expires),
                        handle.report_id,
                    ),
                )
                conn.commit()
                row = conn.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM reports WHERE report_id = ?",
                    (handle.report_id,),
                ).fetchone()
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()
        return _row_to_record(row)

    def cancel(self, handle: BuildHandle) -> None:
        """Discards the building row; nothing is published (PRD
        Cancellation semantics).

        Cancelling an unknown or already-finalized handle is a no-op:
        the loser of a publish race learns that from
        ``StaleBuildError`` instead.
        """
        if not isinstance(handle, BuildHandle):
            raise MalformedReportInput(
                f"handle must be a BuildHandle, got {type(handle).__name__}"
            )
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "DELETE FROM reports WHERE report_id = ? AND status = 'building'",
                    (handle.report_id,),
                )
            finally:
                conn.close()

    def mark_refresh_failed(self, key: ReportKey) -> None:
        """Marks the latest published revision ``refresh_failed`` (FR-33).

        The report stays retrievable through ``get_latest`` for
        diagnostics but ``get_current`` stops returning it: a stale
        report is never presented as current after a failed refresh.
        No-op when the key has no live published revision.
        """
        key = self._validate_key(key)
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE reports SET status = 'refresh_failed' WHERE id = ("
                    " SELECT id FROM reports"
                    f" WHERE {_KEY_WHERE}"
                    " AND status = 'published' AND superseded_by IS NULL"
                    " ORDER BY id DESC LIMIT 1)",
                    (
                        key.main_call_context_id,
                        key.scope,
                        key.interval_key,
                        key.timezone,
                    ),
                )
            finally:
                conn.close()

    # -- read path -------------------------------------------------------

    def get_current(self, key: ReportKey) -> Optional[ReportRecord]:
        """The revision a qualifying query may use, or None.

        Only a ``published``, non-superseded, unexpired revision
        qualifies. ``refresh_failed`` and ``building`` rows are never
        current. Freshness is NOT checked here (see module docstring).
        """
        return self._latest(key, statuses=("published",))

    def get_latest(self, key: ReportKey) -> Optional[ReportRecord]:
        """The newest retrievable revision for diagnostics, or None.

        Includes ``refresh_failed``; excludes ``building`` rows and
        expired rows (expired content is never returned by any read).
        """
        return self._latest(key, statuses=("published", "refresh_failed"))

    def purge_expired(self, *, limit: int = 100) -> int:
        """Bounded cleanup (FR-34): deletes at most ``limit`` rows whose
        retention horizon has passed and returns the deleted count.

        Covers expired latests AND superseded revisions riding the same
        horizon — D08 only needs the latest report per key. Building
        rows and unexpired rows are never touched. Callers may invoke
        this opportunistically (after publish, or at boot); ``limit <= 0``
        deletes nothing.
        """
        if limit <= 0:
            return 0
        now_iso = _iso(self._now())
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "DELETE FROM reports WHERE id IN ("
                    " SELECT id FROM reports"
                    " WHERE retention_expires_at IS NOT NULL"
                    " AND retention_expires_at <= ?"
                    " ORDER BY retention_expires_at, id LIMIT ?)",
                    (now_iso, limit),
                )
                deleted = cursor.rowcount
            finally:
                conn.close()
        return int(deleted or 0)

    # -- internals (callers already hold the lock) ------------------------

    def _connect(self) -> sqlite3.Connection:
        # Autocommit mode: multi-statement transactions are opened
        # explicitly with BEGIN IMMEDIATE where atomicity matters.
        return sqlite3.connect(self._path, isolation_level=None)

    def _now(self) -> datetime:
        moment = self._clock()
        if not isinstance(moment, datetime) or moment.tzinfo is None:
            raise ReportStoreError(
                "clock must return timezone-aware datetimes,"
                f" got {moment!r}"
            )
        return moment.astimezone(timezone.utc)

    def _assert_publishable(
        self, conn: sqlite3.Connection, handle: BuildHandle, key: ReportKey
    ) -> None:
        """Staleness gate inside the publish transaction (FR-33).

        Raises ``StaleBuildError`` — rolling the transaction back — when
        the handle's building row is gone (cancelled, already finalized,
        or discarded on reopen), or when an already-published revision
        for the key carries a newer sequence. In the sequence case the
        building row is deleted first, so a superseded request can never
        come back and never touches the newer report.
        """
        building = conn.execute(
            "SELECT id FROM reports WHERE report_id = ?"
            " AND status = 'building'"
            f" AND {_KEY_WHERE}",
            (
                handle.report_id,
                key.main_call_context_id,
                key.scope,
                key.interval_key,
                key.timezone,
            ),
        ).fetchone()
        if building is None or building[0] != handle.sequence:
            raise StaleBuildError(
                f"build {handle.report_id} cannot be published: it was"
                " cancelled, already finalized, or discarded on reopen"
            )
        published_max = conn.execute(
            "SELECT MAX(id) FROM reports"
            f" WHERE {_KEY_WHERE} AND status != 'building'",
            (
                key.main_call_context_id,
                key.scope,
                key.interval_key,
                key.timezone,
            ),
        ).fetchone()[0]
        if published_max is not None and published_max > handle.sequence:
            conn.execute(
                "DELETE FROM reports WHERE report_id = ? AND status = 'building'",
                (handle.report_id,),
            )
            conn.commit()
            raise StaleBuildError(
                f"build {handle.report_id} sequence {handle.sequence} is older"
                f" than the published sequence {published_max} for its key"
            )

    def _ensure_schema(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.execute(_SCHEMA)
            conn.execute(_INDEX_KEY)
            conn.execute(_INDEX_RETENTION)
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise ReportStoreError(
                    f"report database schema v{version} is newer than the"
                    f" supported v{SCHEMA_VERSION}: {self._path}"
                )
            if version < SCHEMA_VERSION:
                # Migration-on-open: v1 is the initial shape (CREATE IF NOT
                # EXISTS above); future versions migrate forward from here.
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            # Restart reconciliation: a previous process died mid-build.
            # Leftover building rows are discarded here and can never be
            # published (a handle from before the reopen fails publish).
            conn.execute("DELETE FROM reports WHERE status = 'building'")
        finally:
            conn.close()

    def _latest(
        self, key: ReportKey, *, statuses: tuple[str, ...]
    ) -> Optional[ReportRecord]:
        key = self._validate_key(key)
        now_iso = _iso(self._now())
        placeholders = ",".join("?" for _ in statuses)
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM reports"
                    f" WHERE {_KEY_WHERE}"
                    f" AND status IN ({placeholders})"
                    " AND superseded_by IS NULL"
                    " AND retention_expires_at > ?"
                    " ORDER BY id DESC LIMIT 1",
                    (
                        key.main_call_context_id,
                        key.scope,
                        key.interval_key,
                        key.timezone,
                        *statuses,
                        now_iso,
                    ),
                ).fetchone()
            finally:
                conn.close()
        return _row_to_record(row) if row is not None else None

    @staticmethod
    def _validate_key(key: object) -> ReportKey:
        if not isinstance(key, ReportKey):
            raise MalformedReportInput(
                f"key must be a ReportKey, got {type(key).__name__}"
            )
        for field in ("main_call_context_id", "scope", "interval_key", "timezone"):
            _require_str(getattr(key, field), f"key.{field}")
        return key
