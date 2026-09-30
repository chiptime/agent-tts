"""Unit tests for timezone resolution, natural/explicit periods, DST-safe
interval math, timestamp trust, and server-side bound validation (FR-08..10,
FR-37).

All instants are fixed 2025-2026 datetimes; DST anchor facts (gap/fold
offsets, transition instants, weekdays) were verified against the system
zoneinfo database before being hard-coded here.
"""

from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from zoneinfo import ZoneInfo

import pytest

from herdr_brain.periods import (
    Period,
    PeriodError,
    TimezoneUnavailable,
    Verdict,
    classify_timestamp,
    explicit_period,
    period_last_7_days,
    period_this_week,
    period_today,
    resolve_timezone,
    timezone_candidates_from_env,
    validate_bounds,
)

UTC = timezone.utc
MADRID = ZoneInfo("Europe/Madrid")
BERLIN = ZoneInfo("Europe/Berlin")
NEW_YORK = ZoneInfo("America/New_York")
HAVANA = ZoneInfo("America/Havana")
AUCKLAND = ZoneInfo("Pacific/Auckland")


def utc(year, month, day, hour=0, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


class TestResolveTimezone:
    def test_first_valid_candidate_wins_in_order(self):
        zone = resolve_timezone(["Not/AZone", "Europe/Madrid", "UTC"])
        assert zone.key == "Europe/Madrid"

    def test_empty_and_non_string_candidates_are_skipped(self):
        zone = resolve_timezone([None, "", "   ", "Europe/Berlin"])
        assert zone.key == "Europe/Berlin"

    def test_invalid_candidates_are_recorded_on_failure(self):
        with pytest.raises(TimezoneUnavailable) as excinfo:
            resolve_timezone(["Bogus/Zone", "Also/Bogus"])
        names = [name for name, _reason in excinfo.value.rejected]
        assert names == ["Bogus/Zone", "Also/Bogus"]
        assert all(reason for _name, reason in excinfo.value.rejected)

    def test_no_candidates_at_all_is_unavailable(self):
        with pytest.raises(TimezoneUnavailable) as excinfo:
            resolve_timezone([])
        assert excinfo.value.rejected == ()

    def test_posix_tz_string_is_rejected_not_guessed(self):
        # A POSIX TZ spec is not an IANA zone name and must not be guessed.
        with pytest.raises(TimezoneUnavailable):
            resolve_timezone(["CET-1CEST,M3.5.0,M10.5.0/3"])

    def test_unavailable_never_falls_back_to_env_or_system_zone(self, monkeypatch):
        # Even with a perfectly valid TZ in the process environment, an
        # empty candidate list must raise: no implicit os.environ / system
        # read anywhere in the resolution path.
        monkeypatch.setenv("TZ", "Europe/Madrid")
        with pytest.raises(TimezoneUnavailable):
            resolve_timezone([])

    def test_unavailable_is_a_distinct_value_error(self):
        assert issubclass(TimezoneUnavailable, ValueError)
        assert not issubclass(TimezoneUnavailable, PeriodError)

    def test_env_helper_reads_tz_from_supplied_mapping(self):
        assert timezone_candidates_from_env({"TZ": "Europe/Madrid"}) == ["Europe/Madrid"]

    def test_env_helper_strips_whitespace(self):
        assert timezone_candidates_from_env({"TZ": "  America/New_York "}) == [
            "America/New_York"
        ]

    def test_env_helper_empty_or_missing_tz_yields_no_candidates(self):
        assert timezone_candidates_from_env({}) == []
        assert timezone_candidates_from_env({"TZ": ""}) == []
        assert timezone_candidates_from_env({"TZ": "   "}) == []


class TestPeriodToday:
    def test_madrid_afternoon(self):
        now = datetime(2026, 6, 15, 12, 34, 56, tzinfo=MADRID)
        period = period_today(now, MADRID)
        assert period.kind == "today"
        assert period.zone_name == "Europe/Madrid"
        assert period.start == utc(2026, 6, 14, 22, 0, 0)
        assert period.end == now
        assert period.local_start == datetime(2026, 6, 15, 0, 0, 0, tzinfo=MADRID)
        assert period.local_end == datetime(2026, 6, 15, 12, 34, 56, tzinfo=MADRID)

    def test_bounds_are_utc_normalized(self):
        now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=MADRID)
        period = period_today(now, MADRID)
        assert period.start.tzinfo == UTC
        assert period.end.tzinfo == UTC

    def test_exact_local_midnight_gives_empty_period(self):
        now = datetime(2026, 6, 15, 0, 0, 0, tzinfo=MADRID)
        period = period_today(now, MADRID)
        assert period.start == now
        assert period.end == now

    def test_local_date_differs_from_utc_date(self):
        # Auckland is UTC+13 in January: local Jan 10 -> UTC Jan 9.
        now = datetime(2026, 1, 10, 1, 0, 0, tzinfo=AUCKLAND)
        period = period_today(now, AUCKLAND)
        assert period.start == utc(2026, 1, 9, 11, 0, 0)
        assert period.start.date() == date(2026, 1, 9)
        assert period.local_start.date() == date(2026, 1, 10)

    def test_madrid_23h_day_midnight_still_resolves(self):
        # 2026-03-29 is 23h long in Madrid (02:00 -> 03:00); midnight exists
        # in CET even though the query instant is CEST.
        now = datetime(2026, 3, 29, 15, 0, 0, tzinfo=MADRID)
        period = period_today(now, MADRID)
        assert period.start == utc(2026, 3, 28, 23, 0, 0)
        assert period.local_start.utcoffset() == timedelta(hours=1)

    def test_madrid_25h_day_midnight_still_resolves(self):
        now = datetime(2026, 10, 25, 15, 0, 0, tzinfo=MADRID)
        period = period_today(now, MADRID)
        assert period.start == utc(2026, 10, 24, 22, 0, 0)
        assert period.local_start.utcoffset() == timedelta(hours=2)

    def test_havana_nonexistent_midnight_takes_first_valid_instant(self):
        # Havana springs forward AT midnight on 2026-03-08: 00:00 does not
        # exist. Rule: first valid instant after the gap -> 01:00 CDT.
        now = datetime(2026, 3, 8, 12, 0, 0, tzinfo=HAVANA)
        period = period_today(now, HAVANA)
        assert period.start == utc(2026, 3, 8, 5, 0, 0)
        assert period.local_start == datetime(2026, 3, 8, 1, 0, 0, tzinfo=HAVANA)
        assert period.local_start.utcoffset() == timedelta(hours=-4)

    def test_havana_ambiguous_midnight_takes_earliest(self):
        # Havana falls back at 01:00 on 2026-11-01: 00:00-01:00 occurs twice.
        # Rule: earliest instant (fold=0, still CDT).
        now = datetime(2026, 11, 1, 12, 0, 0, tzinfo=HAVANA)
        period = period_today(now, HAVANA)
        assert period.start == utc(2026, 11, 1, 4, 0, 0)
        assert period.local_start.utcoffset() == timedelta(hours=-4)

    def test_naive_now_is_rejected(self):
        with pytest.raises(PeriodError, match="timezone-aware"):
            period_today(datetime(2026, 6, 15, 12, 0, 0), MADRID)


class TestPeriodThisWeek:
    def test_midweek_resolves_to_monday(self):
        now = datetime(2026, 3, 25, 10, 0, 0, tzinfo=MADRID)  # Wednesday
        period = period_this_week(now, MADRID)
        assert period.kind == "this_week"
        assert period.start == utc(2026, 3, 22, 23, 0, 0)  # Mon 2026-03-23 00:00+01
        assert period.end == now

    def test_sunday_belongs_to_previous_monday(self):
        now = datetime(2026, 3, 29, 10, 0, 0, tzinfo=MADRID)  # Sunday
        period = period_this_week(now, MADRID)
        assert period.start == utc(2026, 3, 22, 23, 0, 0)

    def test_monday_early_morning_is_current_week(self):
        now = datetime(2026, 3, 30, 6, 0, 0, tzinfo=MADRID)  # Monday
        period = period_this_week(now, MADRID)
        assert period.start == utc(2026, 3, 29, 22, 0, 0)  # Mon 00:00+02

    def test_monday_exact_midnight_gives_empty_period(self):
        now = datetime(2026, 3, 30, 0, 0, 0, tzinfo=MADRID)
        period = period_this_week(now, MADRID)
        assert period.start == now
        assert period.end == now

    def test_new_york_sunday_across_dst(self):
        now = datetime(2026, 11, 1, 12, 0, 0, tzinfo=NEW_YORK)  # Sunday
        period = period_this_week(now, NEW_YORK)
        assert period.start == utc(2026, 10, 26, 4, 0, 0)  # Mon 00:00-04


class TestPeriodLast7Days:
    def test_plain_week_is_168_hours(self):
        now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=MADRID)
        period = period_last_7_days(now, MADRID)
        assert period.kind == "last_7_days"
        assert period.start == utc(2026, 6, 8, 10, 0, 0)
        assert period.local_start == datetime(2026, 6, 8, 12, 0, 0, tzinfo=MADRID)
        assert period.end == now

    def test_spring_forward_span_is_167_hours_wall_clock_seven_days(self):
        # Wall-clock rule: same local time 7 calendar days earlier. Across
        # Madrid's spring-forward the instant span is 167h, not 168h.
        now = datetime(2026, 3, 31, 12, 0, 0, tzinfo=MADRID)
        period = period_last_7_days(now, MADRID)
        assert period.start == utc(2026, 3, 24, 11, 0, 0)
        assert period.local_start == datetime(2026, 3, 24, 12, 0, 0, tzinfo=MADRID)
        assert period.end - period.start == timedelta(hours=167)
        assert period.local_end - period.local_start == timedelta(days=7)

    def test_fall_back_span_is_169_hours_wall_clock_seven_days(self):
        now = datetime(2026, 10, 27, 12, 0, 0, tzinfo=MADRID)
        period = period_last_7_days(now, MADRID)
        assert period.start == utc(2026, 10, 20, 10, 0, 0)
        assert period.local_start.utcoffset() == timedelta(hours=2)
        assert period.end - period.start == timedelta(hours=169)

    def test_madrid_start_wall_time_in_gap_takes_first_valid(self):
        # 7 days before 2026-04-05 02:30+02 is 2026-03-29 02:30, which does
        # not exist (02:00 -> 03:00). Rule: 03:30 CEST.
        now = datetime(2026, 4, 5, 2, 30, 0, tzinfo=MADRID)
        period = period_last_7_days(now, MADRID)
        assert period.start == utc(2026, 3, 29, 1, 30, 0)
        assert period.local_start == datetime(2026, 3, 29, 3, 30, 0, tzinfo=MADRID)

    def test_madrid_start_wall_time_ambiguous_takes_earliest(self):
        # 7 days before 2026-11-01 02:30+01 is 2026-10-25 02:30, which
        # occurs twice. Rule: earliest (CEST, +02).
        now = datetime(2026, 11, 1, 2, 30, 0, tzinfo=MADRID)
        period = period_last_7_days(now, MADRID)
        assert period.start == utc(2026, 10, 25, 0, 30, 0)
        assert period.local_start.utcoffset() == timedelta(hours=2)

    def test_new_york_start_wall_time_in_gap_takes_first_valid(self):
        # 7 days before 2026-03-15 02:30-04 is 2026-03-08 02:30 (gap).
        now = datetime(2026, 3, 15, 2, 30, 0, tzinfo=NEW_YORK)
        period = period_last_7_days(now, NEW_YORK)
        assert period.start == utc(2026, 3, 8, 7, 30, 0)
        assert period.local_start == datetime(2026, 3, 8, 3, 30, 0, tzinfo=NEW_YORK)

    def test_new_york_start_wall_time_ambiguous_takes_earliest(self):
        # 7 days before 2026-11-08 01:30-05 is 2026-11-01 01:30, which
        # occurs twice. Rule: earliest (EDT, -04).
        now = datetime(2026, 11, 8, 1, 30, 0, tzinfo=NEW_YORK)
        period = period_last_7_days(now, NEW_YORK)
        assert period.start == utc(2026, 11, 1, 5, 30, 0)
        assert period.local_start.utcoffset() == timedelta(hours=-4)


class TestExplicitPeriod:
    def test_aware_bounds_are_preserved_exactly(self):
        period = explicit_period(
            datetime(2026, 3, 2, 9, 0, 0, tzinfo=NEW_YORK),
            datetime(2026, 3, 6, 18, 30, 0, tzinfo=MADRID),
            zone=MADRID,
        )
        assert period.kind == "explicit"
        assert period.start == utc(2026, 3, 2, 14, 0, 0)
        assert period.end == utc(2026, 3, 6, 17, 30, 0)
        assert period.zone_name == "Europe/Madrid"
        assert period.local_start == datetime(2026, 3, 2, 15, 0, 0, tzinfo=MADRID)

    def test_naive_bounds_interpreted_in_explicit_zone(self):
        period = explicit_period(
            datetime(2026, 3, 29, 1, 0, 0),
            datetime(2026, 3, 29, 4, 0, 0),
            zone=MADRID,
        )
        # 01:00 CET -> 04:00 CEST spans the 02:00->03:00 gap: 2 real hours.
        assert period.start == utc(2026, 3, 29, 0, 0, 0)
        assert period.end == utc(2026, 3, 29, 2, 0, 0)

    def test_naive_bound_inside_gap_takes_first_valid(self):
        period = explicit_period(
            datetime(2026, 3, 29, 2, 30, 0),
            datetime(2026, 3, 29, 5, 0, 0),
            zone=MADRID,
        )
        assert period.start == utc(2026, 3, 29, 1, 30, 0)

    def test_naive_bounds_without_zone_are_rejected(self):
        with pytest.raises(PeriodError, match="naive"):
            explicit_period(datetime(2026, 3, 1, 0, 0, 0), datetime(2026, 3, 8, 0, 0, 0))

    def test_date_bounds_mean_local_midnights(self):
        period = explicit_period(date(2026, 3, 1), date(2026, 3, 8), zone=MADRID)
        assert period.start == utc(2026, 2, 28, 23, 0, 0)
        assert period.end == utc(2026, 3, 7, 23, 0, 0)

    def test_dates_without_zone_are_rejected(self):
        with pytest.raises(PeriodError, match="naive"):
            explicit_period(date(2026, 3, 1), date(2026, 3, 8))

    def test_start_not_before_end_is_rejected(self):
        with pytest.raises(PeriodError, match="before"):
            explicit_period(
                datetime(2026, 3, 6, 0, 0, 0, tzinfo=MADRID),
                datetime(2026, 3, 6, 0, 0, 0, tzinfo=MADRID),
                zone=MADRID,
            )
        with pytest.raises(PeriodError, match="before"):
            explicit_period(
                datetime(2026, 3, 7, 0, 0, 0, tzinfo=MADRID),
                datetime(2026, 3, 6, 0, 0, 0, tzinfo=MADRID),
                zone=MADRID,
            )

    def test_bounds_are_never_clamped_or_expanded(self):
        period = explicit_period(
            datetime(2020, 1, 1, 0, 0, 0, tzinfo=NEW_YORK),
            datetime(2020, 1, 31, 0, 0, 0, tzinfo=NEW_YORK),
        )
        assert period.start == utc(2020, 1, 1, 5, 0, 0)
        assert period.end == utc(2020, 1, 31, 5, 0, 0)

    def test_aware_zoneinfo_bounds_without_zone_use_that_zone(self):
        period = explicit_period(
            datetime(2026, 6, 1, 0, 0, 0, tzinfo=MADRID),
            datetime(2026, 6, 8, 0, 0, 0, tzinfo=MADRID),
        )
        assert period.zone_name == "Europe/Madrid"
        assert period.local_start == datetime(2026, 6, 1, 0, 0, 0, tzinfo=MADRID)

    def test_fixed_offset_bounds_without_zone_are_rejected(self):
        fixed = timezone(timedelta(hours=2))
        with pytest.raises(PeriodError, match="zone"):
            explicit_period(
                datetime(2026, 6, 1, 0, 0, 0, tzinfo=fixed),
                datetime(2026, 6, 8, 0, 0, 0, tzinfo=fixed),
            )


class TestClassifyTimestamp:
    def _period(self):
        return explicit_period(
            datetime(2026, 6, 15, 0, 0, 0, tzinfo=MADRID),
            datetime(2026, 6, 16, 0, 0, 0, tzinfo=MADRID),
            zone=MADRID,
        )

    def test_inside(self):
        assert classify_timestamp(datetime(2026, 6, 15, 12, 0, 0, tzinfo=MADRID), self._period()) is Verdict.IN

    def test_exact_start_is_in_half_open(self):
        assert classify_timestamp(utc(2026, 6, 14, 22, 0, 0), self._period()) is Verdict.IN

    def test_exact_end_is_out_half_open(self):
        assert classify_timestamp(utc(2026, 6, 15, 22, 0, 0), self._period()) is Verdict.OUT

    def test_before_and_after_are_out(self):
        period = self._period()
        assert classify_timestamp(datetime(2026, 6, 14, 23, 59, 59, tzinfo=MADRID), period) is Verdict.OUT
        assert classify_timestamp(datetime(2026, 6, 16, 0, 0, 1, tzinfo=MADRID), period) is Verdict.OUT

    def test_other_timezone_compared_by_instant(self):
        period = self._period()
        assert classify_timestamp(datetime(2026, 6, 14, 18, 0, 0, tzinfo=NEW_YORK), period) is Verdict.IN
        assert classify_timestamp(datetime(2026, 6, 15, 18, 0, 1, tzinfo=NEW_YORK), period) is Verdict.OUT

    def test_missing_timestamp_is_unknown(self):
        assert classify_timestamp(None, self._period()) is Verdict.UNKNOWN

    def test_naive_timestamp_is_unknown(self):
        assert classify_timestamp(datetime(2026, 6, 15, 12, 0, 0), self._period()) is Verdict.UNKNOWN

    def test_unparseable_string_is_unknown(self):
        period = self._period()
        assert classify_timestamp("not a timestamp", period) is Verdict.UNKNOWN
        assert classify_timestamp("2026-13-45T99:99:99Z", period) is Verdict.UNKNOWN
        assert classify_timestamp("", period) is Verdict.UNKNOWN

    def test_parseable_iso_string_is_classified(self):
        period = self._period()
        assert classify_timestamp("2026-06-15T10:00:00+02:00", period) is Verdict.IN
        assert classify_timestamp("2026-06-14T22:00:00Z", period) is Verdict.IN

    def test_signature_has_no_mtime_parameter(self):
        # FR-10: a file mtime must never be accepted as a substitute for a
        # message timestamp; the API structurally refuses it.
        assert "mtime" not in inspect.signature(classify_timestamp).parameters

    def test_verdict_is_an_enum(self):
        assert issubclass(Verdict, Enum)


class TestValidateBounds:
    def test_valid_bounds_pass(self):
        validate_bounds(
            utc(2026, 6, 1, 0, 0, 0), utc(2026, 6, 15, 10, 0, 0), now=utc(2026, 6, 15, 10, 0, 0)
        )

    def test_naive_start_is_rejected(self):
        with pytest.raises(PeriodError, match="timezone-aware"):
            validate_bounds(
                datetime(2026, 6, 1, 0, 0, 0), utc(2026, 6, 15, 0, 0, 0), now=utc(2026, 6, 15, 0, 0, 0)
            )

    def test_naive_end_is_rejected(self):
        with pytest.raises(PeriodError, match="timezone-aware"):
            validate_bounds(
                utc(2026, 6, 1, 0, 0, 0), datetime(2026, 6, 15, 0, 0, 0), now=utc(2026, 6, 15, 0, 0, 0)
            )

    def test_naive_now_is_rejected(self):
        with pytest.raises(PeriodError, match="timezone-aware"):
            validate_bounds(
                utc(2026, 6, 1, 0, 0, 0), utc(2026, 6, 15, 0, 0, 0), now=datetime(2026, 6, 15, 0, 0, 0)
            )

    def test_start_equal_to_end_is_rejected(self):
        with pytest.raises(PeriodError, match="before"):
            validate_bounds(
                utc(2026, 6, 1, 0, 0, 0), utc(2026, 6, 1, 0, 0, 0), now=utc(2026, 6, 2, 0, 0, 0)
            )

    def test_start_after_end_is_rejected(self):
        with pytest.raises(PeriodError, match="before"):
            validate_bounds(
                utc(2026, 6, 3, 0, 0, 0), utc(2026, 6, 1, 0, 0, 0), now=utc(2026, 6, 4, 0, 0, 0)
            )

    def test_future_end_is_rejected_by_default(self):
        with pytest.raises(PeriodError, match="future"):
            validate_bounds(
                utc(2026, 6, 1, 0, 0, 0), utc(2026, 6, 16, 0, 0, 0), now=utc(2026, 6, 15, 0, 0, 0)
            )

    def test_end_equal_to_now_is_allowed(self):
        validate_bounds(
            utc(2026, 6, 1, 0, 0, 0), utc(2026, 6, 15, 0, 0, 0), now=utc(2026, 6, 15, 0, 0, 0)
        )

    def test_future_within_documented_tolerance_is_allowed(self):
        validate_bounds(
            utc(2026, 6, 1, 0, 0, 0),
            utc(2026, 6, 15, 0, 0, 30),
            now=utc(2026, 6, 15, 0, 0, 0),
            future_tolerance=timedelta(seconds=60),
        )

    def test_future_beyond_tolerance_is_rejected(self):
        with pytest.raises(PeriodError, match="future"):
            validate_bounds(
                utc(2026, 6, 1, 0, 0, 0),
                utc(2026, 6, 15, 0, 2, 0),
                now=utc(2026, 6, 15, 0, 0, 0),
                future_tolerance=timedelta(seconds=60),
            )

    def test_span_above_max_span_is_rejected(self):
        with pytest.raises(PeriodError, match="max_span"):
            validate_bounds(
                utc(2026, 6, 1, 0, 0, 0),
                utc(2026, 6, 15, 0, 0, 1),
                now=utc(2026, 6, 15, 0, 0, 2),
                max_span=timedelta(days=14),
            )

    def test_span_exactly_at_max_span_is_allowed(self):
        validate_bounds(
            utc(2026, 6, 1, 0, 0, 0),
            utc(2026, 6, 15, 0, 0, 0),
            now=utc(2026, 6, 15, 0, 0, 0),
            max_span=timedelta(days=14),
        )

    def test_default_max_span_is_unlimited(self):
        validate_bounds(
            utc(2010, 1, 1, 0, 0, 0), utc(2026, 6, 15, 0, 0, 0), now=utc(2026, 6, 15, 0, 0, 0)
        )

    def test_period_error_is_a_value_error(self):
        assert issubclass(PeriodError, ValueError)


class TestPeriodKeyAndImmutability:
    def test_key_is_deterministic_across_constructions(self):
        now = datetime(2026, 6, 15, 12, 34, 56, tzinfo=MADRID)
        first = period_today(now, MADRID)
        second = period_today(now, MADRID)
        assert first.key == second.key
        assert first == second

    def test_key_format_is_kind_bounds_and_zone(self):
        now = datetime(2026, 6, 15, 12, 34, 56, tzinfo=MADRID)
        period = period_today(now, MADRID)
        assert period.key == (
            "today|2026-06-14T22:00:00+00:00|2026-06-15T10:34:56+00:00|Europe/Madrid"
        )

    def test_different_zones_produce_different_keys(self):
        # Madrid and Berlin share offsets on this date: identical UTC bounds,
        # so only the recorded zone distinguishes the cache keys.
        now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)
        madrid = period_today(now.astimezone(MADRID), MADRID)
        berlin = period_today(now.astimezone(BERLIN), BERLIN)
        assert madrid.start == berlin.start
        assert madrid.key != berlin.key

    def test_different_intervals_produce_different_keys(self):
        zone = MADRID
        first = period_today(datetime(2026, 6, 15, 12, 0, 0, tzinfo=zone), zone)
        second = period_today(datetime(2026, 6, 16, 12, 0, 0, tzinfo=zone), zone)
        assert first.key != second.key

    def test_different_kinds_produce_different_keys(self):
        now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=MADRID)
        explicit = explicit_period(
            datetime(2026, 6, 15, 0, 0, 0, tzinfo=MADRID), now, zone=MADRID
        )
        assert explicit.start == period_today(now, MADRID).start
        assert explicit.end == period_today(now, MADRID).end
        assert explicit.key != period_today(now, MADRID).key

    def test_period_is_frozen(self):
        period = period_today(datetime(2026, 6, 15, 12, 0, 0, tzinfo=MADRID), MADRID)
        with pytest.raises(FrozenInstanceError):
            period.kind = "explicit"

    def test_period_is_hashable(self):
        period = period_today(datetime(2026, 6, 15, 12, 0, 0, tzinfo=MADRID), MADRID)
        assert period in {period}
