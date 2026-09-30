"""Natural and explicit time periods for historical queries (FR-08, FR-09,
FR-10, FR-37).

Pure library: stdlib only (``datetime``/``zoneinfo``), no I/O, no implicit
clock. Every function that needs "now" takes it as an argument; nothing in
this module calls ``datetime.now``. Callers (server/FSM) inject the clock and
the timezone candidates (session/config/env mapping).

Product rules implemented here (PRD decision D04):

- Timezone is never guessed: candidates are validated in order and the first
  available IANA zone wins; if none is valid the caller gets a distinct
  ``TimezoneUnavailable`` outcome it can turn into a runtime question.
  POSIX TZ spec strings (e.g. ``CET-1CEST,...``) are not IANA names and are
  rejected, not interpreted.
- Natural periods resolve in the USER's timezone: today = local midnight to
  the query instant; this week = Monday 00:00 local (ISO week) to the query
  instant; last 7 days = ROLLING through the query instant.
- All periods are half-open intervals ``[start, end)``.
- DST safety: local wall-clock values are converted with fold=0 (PEP 495),
  which deterministically means: an AMBIGUOUS local time (fall-back fold)
  resolves to the EARLIEST matching instant, and a NONEXISTENT local time
  (spring-forward gap) resolves to the FIRST VALID INSTANT AFTER the GAP.
- A file mtime is never accepted as a substitute for a message timestamp:
  ``classify_timestamp`` has no mtime parameter and returns UNKNOWN for
  missing, unparseable, or naive timestamps.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc

PeriodKind = Literal["today", "this_week", "last_7_days", "explicit"]


class PeriodError(ValueError):
    """Invalid period bounds or misuse of the period constructors (FR-37)."""


class TimezoneUnavailable(ValueError):
    """No candidate resolved to an available IANA zone (FR-09).

    A distinct "timezone unavailable" outcome: the caller must ask the user
    at runtime, never fall back to UTC or the system zone. ``rejected``
    records every skipped candidate as a ``(candidate, reason)`` pair so the
    caller can explain why the ask is happening.
    """

    def __init__(self, rejected: tuple[tuple[str, str], ...] = ()):
        self.rejected = rejected
        if rejected:
            detail = ", ".join(f"{name} ({reason})" for name, reason in rejected)
            message = f"no available timezone among candidates: {detail}"
        else:
            message = "no timezone candidates provided"
        super().__init__(message)


class Verdict(Enum):
    """Trust classification of a message timestamp against a Period."""

    IN = "in"
    OUT = "out"
    UNKNOWN = "unknown"


def resolve_timezone(candidates: Iterable[str | None]) -> ZoneInfo:
    """Returns the first candidate that is a valid, available IANA zone.

    Candidates are tried in order; invalid, empty, or non-string entries are
    skipped and recorded. If nothing resolves, raises
    ``TimezoneUnavailable`` (carrying the rejections) so the caller can ask
    the user at runtime — this function never falls back to UTC or to the
    system zone.
    """
    rejected: list[tuple[str, str]] = []
    for candidate in candidates:
        name = candidate.strip() if isinstance(candidate, str) else ""
        if not name:
            rejected.append((repr(candidate), "empty or non-string candidate"))
            continue
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
            rejected.append((name, f"not an available IANA zone: {exc}"))
    raise TimezoneUnavailable(tuple(rejected))


def timezone_candidates_from_env(env: Mapping[str, str]) -> list[str]:
    """Extracts timezone candidates from a ``TZ`` entry in ``env``.

    ``env`` is supplied by the caller (e.g. ``os.environ`` at the edge);
    this pure helper never reads the process environment itself. Returns a
    single-element list with the stripped value, or an empty list when unset
    or blank. The value is only a CANDIDATE: POSIX TZ spec strings will be
    rejected by ``resolve_timezone`` because they are not IANA names.
    """
    value = (env.get("TZ") or "").strip()
    return [value] if value else []


@dataclass(frozen=True)
class Period:
    """A resolved half-open interval ``[start, end)`` (frozen).

    Attributes:
        kind: one of ``today``, ``this_week``, ``last_7_days``, ``explicit``.
        start: interval start as a timezone-aware UTC-normalized instant.
        end: interval end as a timezone-aware UTC-normalized instant.
        zone_name: the original IANA zone the period was resolved in.
        local_start: ``start`` expressed in the user's zone (wall clock for
            display).
        local_end: ``end`` expressed in the user's zone (wall clock for
            display).
    """

    kind: PeriodKind
    start: datetime
    end: datetime
    zone_name: str
    local_start: datetime
    local_end: datetime

    @property
    def key(self) -> str:
        """Deterministic cache key: ``kind|start|end|zone`` in UTC isoformat.

        Deterministic by construction (derived, never stored): identical
        kind + instants + zone always produce the identical key, and any
        difference in those components produces a different key.
        """
        return f"{self.kind}|{self.start.isoformat()}|{self.end.isoformat()}|{self.zone_name}"


def _wall_to_instant(wall: datetime, zone: ZoneInfo) -> datetime:
    """Converts a naive local wall-clock datetime to an aware instant.

    DST rule (PEP 495, fold=0, the documented product rule):
    - ambiguous local time (fall-back fold) -> the EARLIEST matching instant;
    - nonexistent local time (spring-forward gap) -> the first valid instant
      AFTER the gap (fold=0 resolves through the pre-gap offset, which lands
      after the transition).
    """
    return wall.replace(tzinfo=zone)


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise PeriodError(f"{label} must be a timezone-aware datetime")


def _period(
    kind: PeriodKind, start: datetime, end: datetime, zone: ZoneInfo
) -> Period:
    """Assembles a Period with UTC-normalized bounds and zone-local display."""
    start_utc = start.astimezone(UTC)
    end_utc = end.astimezone(UTC)
    return Period(
        kind=kind,
        start=start_utc,
        end=end_utc,
        zone_name=zone.key,
        local_start=start_utc.astimezone(zone),
        local_end=end_utc.astimezone(zone),
    )


def period_today(now: datetime, zone: ZoneInfo) -> Period:
    """Today in the user's timezone: [local midnight of now's local date, now).

    On a day whose local midnight does not exist (zones that spring forward
    at midnight), the start is the first valid instant after the gap per the
    module DST rule.
    """
    _require_aware(now, "now")
    local = now.astimezone(zone)
    start = _wall_to_instant(datetime(local.year, local.month, local.day), zone)
    return _period("today", start, now, zone)


def period_this_week(now: datetime, zone: ZoneInfo) -> Period:
    """This ISO week in the user's timezone: [Monday 00:00 local, now).

    Weeks start on Monday; a Sunday query belongs to the Monday six days
    earlier. The Monday-midnight start follows the module DST rule.
    """
    _require_aware(now, "now")
    local = now.astimezone(zone)
    monday = local.date() - timedelta(days=local.weekday())
    start = _wall_to_instant(datetime(monday.year, monday.month, monday.day), zone)
    return _period("this_week", start, now, zone)


def period_last_7_days(now: datetime, zone: ZoneInfo) -> Period:
    """Rolling last 7 days: [same local wall-clock time 7 calendar days
    earlier, now).

    Interpretation: WALL-CLOCK 7 calendar days, not 168 fixed hours. The
    start is computed as naive field arithmetic on the local wall clock and
    then converted with the module DST rule, so across a spring-forward the
    instant span is 167 hours and across a fall-back it is 169 hours, while
    the displayed wall-clock distance is exactly 7 days.
    """
    _require_aware(now, "now")
    wall = now.astimezone(zone).replace(tzinfo=None)
    start = _wall_to_instant(wall - timedelta(days=7), zone)
    return _period("last_7_days", start, now, zone)


def explicit_period(
    start: datetime | date,
    end: datetime | date,
    zone: ZoneInfo | None = None,
) -> Period:
    """An explicit period bounded EXACTLY to the supplied start and end.

    Accepts timezone-aware datetimes (kept as exact instants; never clamped
    or expanded) or naive datetimes/dates. Naive values are interpreted as
    wall clock in ``zone`` ONLY when a zone is explicitly passed, and are
    rejected with ``PeriodError`` otherwise (never guessed). ``date`` inputs
    mean local midnight of that day.

    ``zone`` also selects the display zone and the recorded ``zone_name``.
    Without it, an IANA name is derived from ``start``'s tzinfo when it is a
    ``ZoneInfo``; fixed-offset bounds without a zone are rejected because
    they have no honest IANA name.

    Requires ``start < end``. This constructor does NOT police future ends
    or span limits: the server enforces those separately via
    ``validate_bounds`` (FR-37).
    """
    start_dt, end_dt = _coerce_datetime(start, "start"), _coerce_datetime(end, "end")
    if zone is not None and not isinstance(zone, ZoneInfo):
        raise PeriodError("zone must be a zoneinfo.ZoneInfo instance")

    if zone is not None:
        display_zone = zone
        if start_dt.tzinfo is None:
            start_dt = _wall_to_instant(start_dt, zone)
        if end_dt.tzinfo is None:
            end_dt = _wall_to_instant(end_dt, zone)
    elif start_dt.tzinfo is None or end_dt.tzinfo is None:
        raise PeriodError(
            "naive start/end bounds require an explicit zone to be interpreted;"
            " refusing to guess one"
        )
    elif isinstance(start_dt.tzinfo, ZoneInfo):
        display_zone = start_dt.tzinfo
    else:
        raise PeriodError(
            "cannot derive an IANA zone name from a fixed-offset start;"
            " pass an explicit zone"
        )

    if start_dt >= end_dt:
        raise PeriodError(
            f"start must be before end (start={start_dt.isoformat()},"
            f" end={end_dt.isoformat()})"
        )
    return _period("explicit", start_dt, end_dt, display_zone)


def _coerce_datetime(value: datetime | date, label: str) -> datetime:
    """Views a date as its local midnight; validates the input type."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    raise PeriodError(f"{label} must be a datetime or date, got {type(value).__name__}")


def classify_timestamp(timestamp: datetime | str | None, period: Period) -> Verdict:
    """Classifies a message timestamp against a Period (FR-10).

    Half-open semantics: IN iff ``period.start <= timestamp < period.end``
    (exact start is IN, exact end is OUT), compared as instants regardless
    of the timestamp's own zone.

    UNKNOWN (never OUT — trust is not inferred) for: missing (None), any
    non-datetime/non-string value, unparseable strings, and NAIVE datetimes
    (no timezone). There is deliberately no mtime parameter: a file mtime
    must never stand in as proof of a message's date.
    """
    if isinstance(timestamp, str):
        try:
            timestamp = datetime.fromisoformat(timestamp)
        except ValueError:
            return Verdict.UNKNOWN
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
        return Verdict.UNKNOWN
    if period.start <= timestamp < period.end:
        return Verdict.IN
    return Verdict.OUT


def validate_bounds(
    start: datetime,
    end: datetime,
    *,
    now: datetime,
    max_span: timedelta | None = None,
    future_tolerance: timedelta = timedelta(0),
) -> None:
    """Server-side validation of model/tool-supplied bounds (FR-37).

    Raises ``PeriodError`` for, in order: naive (or non-datetime) values;
    ``start >= end``; ``end`` in the future relative to ``now`` beyond
    ``future_tolerance`` (default: no tolerance); and a span strictly above
    ``max_span`` (default None = no limit — the product has not chosen a
    limit value yet). A span exactly equal to ``max_span`` is allowed.
    Returns None when the bounds are acceptable.
    """
    for label, value in (("start", start), ("end", end), ("now", now)):
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise PeriodError(f"{label} must be a timezone-aware datetime")
    if start >= end:
        raise PeriodError(
            f"start must be before end (start={start.isoformat()},"
            f" end={end.isoformat()})"
        )
    if end - now > future_tolerance:
        raise PeriodError(
            f"end is in the future relative to now (end={end.isoformat()},"
            f" now={now.isoformat()})"
        )
    if max_span is not None and end - start > max_span:
        raise PeriodError(
            f"period span {end - start} exceeds max_span {max_span}"
        )
