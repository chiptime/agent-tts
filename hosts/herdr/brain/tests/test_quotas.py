"""Unit tests for the quota service (POST-02 S1/S2/S5).

The host ``ai-quotas`` binary is NEVER executed here: runner, binary,
clock and background spawner are injected, so these tests are
deterministic and network-free.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from herdr_brain import pairing
from herdr_brain import quotas
from herdr_brain import server as server_module
from herdr_brain.config import Settings
from herdr_brain.quotas import (
    AiQuotasSource,
    QUOTAS_FAILURE_TTL_S,
    QUOTAS_TTL_S,
    QuotaService,
    ai_quotas_binary,
    format_reset,
    normalize_record,
)
from herdr_brain.server import create_app
from herdr_brain.tools import BrainTools, TOOLS_SCHEMA
from fastapi.testclient import TestClient
from tests.conftest import SETTINGS_KWARGS
from tests.test_pairing_api import LOOPBACK

RECORD_KEYS = {
    "provider",
    "display_name",
    "kind",
    "label",
    "used_percent",
    "resets_at",
    "state",
    "status_indicator",
    "status_summary",
    "detail",
    "cost_today_usd",
    "cost_month_to_date_usd",
}


def make_payload(**overrides):
    record = {
        "provider": "claude",
        "display_name": "Anthropic Claude",
        "kind": "window",
        "label": "5h window",
        "used_percent": 34.0,
        "resets_at": "2026-11-26T18:59:59+00:00",
        "state": "ok",
        "status_indicator": None,
        "status_summary": None,
        "detail": None,
        "cost_today_usd": None,
        "cost_month_to_date_usd": None,
    }
    record.update(overrides)
    return record


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class SpawnerStub:
    """Collects background jobs so tests run them deterministically."""

    def __init__(self) -> None:
        self.jobs = []

    def __call__(self, job) -> None:
        self.jobs.append(job)


def ok_runner(records, calls):
    def _run(args, timeout_s):
        calls.append((list(args), timeout_s))
        return subprocess.CompletedProcess(
            args, 0, stdout=json.dumps(records), stderr=""
        )

    return _run


def failing_runner(exit_code=1):
    def _run(args, timeout_s):
        return subprocess.CompletedProcess(args, exit_code, stdout="", stderr="boom")

    return _run


class FakeSource:
    """A custom QuotaSource double: canned payload, call counter."""

    def __init__(self, payload):
        self._payload = payload
        self.calls = 0

    def fetch(self):
        self.calls += 1
        return self._payload


class RaisingSource:
    def fetch(self):
        raise RuntimeError("source exploded")


class TestNormalizeRecord:
    def test_keeps_a_full_window_record(self):
        normalized = normalize_record(make_payload())
        assert normalized["provider"] == "claude"
        assert normalized["used_percent"] == 34.0
        assert normalized["resets_at"] == "2026-11-26T18:59:59+00:00"

    def test_whitelists_keys_and_drops_unknown_fields(self):
        normalized = normalize_record(make_payload(secret_field="sk-123"))
        assert set(normalized) == RECORD_KEYS

    def test_epoch_zero_resets_at_becomes_none(self):
        normalized = normalize_record(
            make_payload(resets_at="1970-01-01T00:00:00+00:00")
        )
        assert normalized["resets_at"] is None

    def test_null_used_percent_is_kept_for_balances(self):
        normalized = normalize_record(
            make_payload(kind="balance", label=None, used_percent=None)
        )
        assert normalized["used_percent"] is None
        assert normalized["label"] is None

    def test_claude_out_of_credits_detail_passes_through(self):
        normalized = normalize_record(
            make_payload(detail="severity: normal · disabled: out_of_credits")
        )
        assert "out_of_credits" in normalized["detail"]

    def test_empty_label_becomes_none(self):
        normalized = normalize_record(make_payload(label="  "))
        assert normalized["label"] is None

    def test_rejects_non_dict_and_missing_provider(self):
        assert normalize_record("nope") is None
        assert normalize_record(["list"]) is None
        assert normalize_record({"kind": "window"}) is None
        assert normalize_record(make_payload(provider="   ")) is None

    def test_bool_is_not_accepted_as_used_percent(self):
        normalized = normalize_record(make_payload(used_percent=True))
        assert normalized["used_percent"] is None

    def test_nan_and_infinity_are_rejected(self):
        normalized = normalize_record(
            make_payload(
                used_percent=float("nan"),
                cost_today_usd=float("inf"),
                cost_month_to_date_usd=float("-inf"),
            )
        )
        assert normalized["used_percent"] is None
        assert normalized["cost_today_usd"] is None
        assert normalized["cost_month_to_date_usd"] is None

    def test_unknown_state_becomes_unknown(self):
        normalized = normalize_record(make_payload(state=42))
        assert normalized["state"] == "unknown"

    def test_cost_numbers_are_normalized_to_float(self):
        normalized = normalize_record(
            make_payload(cost_today_usd=1, cost_month_to_date_usd=12.5)
        )
        assert normalized["cost_today_usd"] == 1.0
        assert normalized["cost_month_to_date_usd"] == 12.5


class TestBinaryFinder:
    def test_uses_path_when_available(self, monkeypatch):
        monkeypatch.setattr(quotas.shutil, "which", lambda name: "/usr/bin/ai-quotas")
        assert ai_quotas_binary() == "/usr/bin/ai-quotas"

    def test_falls_back_to_local_bin_executable(self, monkeypatch, tmp_path):
        fake = tmp_path / "ai-quotas"
        fake.write_text("#!/bin/sh\n", encoding="utf-8")
        fake.chmod(0o755)
        monkeypatch.setattr(quotas.shutil, "which", lambda name: None)
        monkeypatch.setattr(quotas.os.path, "expanduser", lambda p: str(fake))
        assert ai_quotas_binary() == str(fake)

    def test_rejects_non_executable_fallback(self, monkeypatch, tmp_path):
        fake = tmp_path / "ai-quotas"
        fake.write_text("not executable", encoding="utf-8")
        monkeypatch.setattr(quotas.shutil, "which", lambda name: None)
        monkeypatch.setattr(quotas.os.path, "expanduser", lambda p: str(fake))
        assert ai_quotas_binary() is None


class TestQuotaService:
    def test_runner_receives_check_json_and_timeout(self):
        calls = []
        svc = QuotaService(
            runner=ok_runner([make_payload()], calls),
            clock=FakeClock(),
            binary="/fake/ai-quotas",
            spawner=SpawnerStub(),
            timeout_s=123.0,
        )
        svc.refresh_sync()
        assert calls == [(["/fake/ai-quotas", "check", "--json"], 123.0)]

    def test_refresh_sync_parses_providers_sorted(self):
        records = [
            make_payload(provider="zai", label="Weekly", used_percent=42.0),
            make_payload(provider="claude", label="Weekly", used_percent=79.0),
            make_payload(provider="claude", label="5h window", used_percent=34.0),
        ]
        svc = QuotaService(
            runner=ok_runner(records, []),
            clock=FakeClock(),
            binary="/fake/ai-quotas",
            spawner=SpawnerStub(),
        )
        snap = svc.refresh_sync()
        assert snap["reason"] == ""
        assert snap["stale"] is False
        assert snap["fetched_at"] is not None
        order = [(p["provider"], p["label"]) for p in snap["providers"]]
        assert order == [
            ("claude", "5h window"),
            ("claude", "Weekly"),
            ("zai", "Weekly"),
        ]

    def test_fresh_cache_serves_without_rerunning_the_binary(self):
        calls = []
        clock = FakeClock()
        svc = QuotaService(
            runner=ok_runner([make_payload()], calls),
            clock=clock,
            binary="/fake/ai-quotas",
            spawner=SpawnerStub(),
        )
        svc.refresh_sync()
        clock.now += QUOTAS_TTL_S - 1
        snap = svc.snapshot()
        assert len(calls) == 1
        assert snap["stale"] is False
        assert snap["providers"][0]["provider"] == "claude"

    def test_expired_cache_returns_stale_and_kicks_background_refresh(self):
        calls = []
        clock = FakeClock()
        spawner = SpawnerStub()
        svc = QuotaService(
            runner=ok_runner([make_payload()], calls),
            clock=clock,
            binary="/fake/ai-quotas",
            spawner=spawner,
        )
        svc.refresh_sync()
        clock.now += QUOTAS_TTL_S + 1
        snap = svc.snapshot()
        assert snap["stale"] is True
        assert len(spawner.jobs) == 1
        # The background job reloads deterministically when run.
        spawner.jobs[0]()
        assert len(calls) == 2

    def test_cold_miss_is_not_ready_and_kicks_exactly_one_refresh(self):
        calls = []
        spawner = SpawnerStub()
        svc = QuotaService(
            runner=ok_runner([make_payload()], calls),
            clock=FakeClock(),
            binary="/fake/ai-quotas",
            spawner=spawner,
        )
        snap = svc.snapshot()
        assert snap["providers"] == []
        assert snap["reason"] == "not-ready"
        assert snap["stale"] is False
        assert len(spawner.jobs) == 1
        svc.snapshot()
        assert len(spawner.jobs) == 1  # in-flight refresh is never duplicated
        spawner.jobs[0]()
        assert svc.snapshot()["providers"][0]["provider"] == "claude"

    def test_prewarm_kicks_one_background_refresh(self):
        spawner = SpawnerStub()
        svc = QuotaService(
            runner=ok_runner([make_payload()], []),
            clock=FakeClock(),
            binary="/fake/ai-quotas",
            spawner=spawner,
        )
        svc.prewarm()
        assert len(spawner.jobs) == 1
        svc.prewarm()
        assert len(spawner.jobs) == 1

    def test_failure_maps_to_cli_failed_with_short_ttl(self):
        clock = FakeClock()
        svc = QuotaService(
            runner=failing_runner(1),
            clock=clock,
            binary="/fake/ai-quotas",
            spawner=SpawnerStub(),
        )
        snap = svc.refresh_sync()
        assert snap["reason"] == "cli-failed"
        assert snap["providers"] == []
        assert snap["stale"] is False
        clock.now += QUOTAS_FAILURE_TTL_S - 1
        svc.snapshot()
        clock.now += QUOTAS_FAILURE_TTL_S + 1
        expired = svc.snapshot()
        assert expired["reason"] == "cli-failed"
        assert expired["stale"] is True

    def test_timeout_maps_to_timeout_reason(self):
        def _run(args, timeout_s):
            raise subprocess.TimeoutExpired(cmd=args, timeout=timeout_s)

        svc = QuotaService(
            runner=_run, clock=FakeClock(), binary="/fake/ai-quotas", spawner=SpawnerStub()
        )
        assert svc.refresh_sync()["reason"] == "timeout"

    def test_oserror_maps_to_unavailable(self):
        def _run(args, timeout_s):
            raise OSError("noexec")

        svc = QuotaService(
            runner=_run, clock=FakeClock(), binary="/fake/ai-quotas", spawner=SpawnerStub()
        )
        assert svc.refresh_sync()["reason"] == "unavailable"

    def test_missing_binary_maps_to_not_found_without_running(self, monkeypatch):
        calls = []
        # Restore the REAL finder: the hermetic conftest hook stubs the
        # module global, which would short-circuit this test.
        monkeypatch.setattr(quotas, "ai_quotas_binary", ai_quotas_binary)
        monkeypatch.setattr(quotas.shutil, "which", lambda name: None)
        monkeypatch.setattr(
            quotas.os.path, "expanduser", lambda p: "/nonexistent/ai-quotas"
        )
        svc = QuotaService(
            runner=ok_runner([make_payload()], calls),
            clock=FakeClock(),
            spawner=SpawnerStub(),
        )
        assert svc.refresh_sync()["reason"] == "ai-quotas-not-found"
        assert calls == []

    def test_bad_json_maps_to_bad_json(self):
        def _run(args, timeout_s):
            return subprocess.CompletedProcess(args, 0, stdout="not json", stderr="")

        svc = QuotaService(
            runner=_run, clock=FakeClock(), binary="/fake/ai-quotas", spawner=SpawnerStub()
        )
        assert svc.refresh_sync()["reason"] == "bad-json"

    def test_non_list_json_maps_to_bad_json(self):
        def _run(args, timeout_s):
            return subprocess.CompletedProcess(args, 0, stdout='{"a": 1}', stderr="")

        svc = QuotaService(
            runner=_run, clock=FakeClock(), binary="/fake/ai-quotas", spawner=SpawnerStub()
        )
        assert svc.refresh_sync()["reason"] == "bad-json"

    def test_empty_array_maps_to_empty(self):
        svc = QuotaService(
            runner=ok_runner([], []),
            clock=FakeClock(),
            binary="/fake/ai-quotas",
            spawner=SpawnerStub(),
        )
        assert svc.refresh_sync()["reason"] == "empty"

    def test_runner_crash_never_raises(self):
        def _run(args, timeout_s):
            raise RuntimeError("kaboom")

        svc = QuotaService(
            runner=_run, clock=FakeClock(), binary="/fake/ai-quotas", spawner=SpawnerStub()
        )
        snap = svc.refresh_sync()
        assert snap["providers"] == []
        assert snap["reason"]

    def test_stale_copy_is_isolated_from_the_cache(self):
        clock = FakeClock()
        spawner = SpawnerStub()
        svc = QuotaService(
            runner=ok_runner([make_payload()], []),
            clock=clock,
            binary="/fake/ai-quotas",
            spawner=spawner,
        )
        svc.refresh_sync()
        snap = svc.snapshot()
        snap["providers"][0]["provider"] = "mutated"
        clock.now += QUOTAS_TTL_S + 1
        again = svc.snapshot()
        assert again["providers"][0]["provider"] == "claude"

    def test_custom_quota_source_feeds_the_cache(self):
        clock = FakeClock()
        source = FakeSource(
            {
                "providers": [normalize_record(make_payload())],
                "reason": "",
                "fetched_at": "2026-11-26T18:00:00+00:00",
            }
        )
        svc = QuotaService(source=source, clock=clock, spawner=SpawnerStub())
        snap = svc.refresh_sync()
        assert snap["reason"] == ""
        assert snap["providers"][0]["provider"] == "claude"
        clock.now += QUOTAS_TTL_S - 1
        assert svc.snapshot()["providers"][0]["provider"] == "claude"
        assert source.calls == 1  # cached — the source is not re-consulted

    def test_default_source_is_the_ai_quotas_adapter(self):
        svc = QuotaService(clock=FakeClock(), spawner=SpawnerStub())
        assert isinstance(svc._source, AiQuotasSource)

    def test_raising_quota_source_maps_to_error(self):
        svc = QuotaService(
            source=RaisingSource(), clock=FakeClock(), spawner=SpawnerStub()
        )
        snap = svc.refresh_sync()
        assert snap["reason"] == "error"
        assert snap["providers"] == []

    def test_failing_spawner_does_not_wedge_the_service(self):
        def bad_spawner(job):
            raise RuntimeError("no threads today")

        svc = QuotaService(
            runner=ok_runner([make_payload()], []),
            clock=FakeClock(),
            binary="/fake/ai-quotas",
            spawner=bad_spawner,
        )
        snap = svc.snapshot()  # must not raise; must not leave the flag stuck
        assert snap["reason"] == "not-ready"
        # A later working spawner is consulted again (the flag was reset).
        svc._spawn = SpawnerStub()
        svc.snapshot()
        assert len(svc._spawn.jobs) == 1
        svc._spawn.jobs[0]()
        assert svc.snapshot()["providers"][0]["provider"] == "claude"

    def test_inline_spawner_does_not_deadlock(self):
        svc = QuotaService(
            runner=ok_runner([make_payload()], []),
            clock=FakeClock(),
            binary="/fake/ai-quotas",
            spawner=lambda job: job(),  # runs inline: no re-entrancy allowed
        )
        snap = svc.snapshot()  # the old under-lock spawn deadlocked here
        assert snap["reason"] == "not-ready"
        assert svc.snapshot()["providers"][0]["provider"] == "claude"


@pytest.mark.parametrize(
    ("name", "attr"),
    [("ttl", "QUOTAS_TTL_S"), ("failure_ttl", "QUOTAS_FAILURE_TTL_S")],
)
def test_ttl_constants_match_the_spec(name, attr):
    assert getattr(quotas, attr) == (300.0 if name == "ttl" else 15.0)


class TestFormatReset:
    def test_none_stays_none(self):
        assert format_reset(None) is None

    def test_extreme_dates_never_raise(self):
        # astimezone near datetime.max raises OverflowError/OSError on
        # some local zones; the contract is "never raises".
        stamp = format_reset("9999-12-31T23:59:59+00:00")
        assert stamp is None or isinstance(stamp, str)

    def test_unparseable_string_passes_through(self):
        assert format_reset("not-a-date") == "not-a-date"

    def test_parses_to_weekday_and_time(self):
        stamp = format_reset("2026-11-26T18:59:59+00:00")
        assert stamp is not None
        # "Thu 15:59"-style: one weekday + one HH:MM, converted to the
        # local zone — so no fixed hour or fixed weekday is asserted.
        parts = stamp.split(" ")
        assert len(parts) == 2
        day, moment = parts
        assert day in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
        hour, minute = moment.split(":")
        assert 0 <= int(hour) <= 23
        assert 0 <= int(minute) <= 59


def quota_tools(settings, make_stub, records, clock=None):
    """BrainTools wired to a quota service fed by a fake runner."""
    svc = QuotaService(
        runner=ok_runner(records, []),
        clock=clock or FakeClock(),
        binary="/fake/ai-quotas",
        spawner=SpawnerStub(),
    )
    return svc, BrainTools(settings, herdr=make_stub(), quota_service=svc)


class TestCheckQuotasTool:
    RECORDS = [
        make_payload(),
        make_payload(
            provider="deepseek",
            display_name="DeepSeek",
            kind="balance",
            label=None,
            used_percent=None,
            resets_at=None,
            cost_today_usd=1.25,
            cost_month_to_date_usd=14.5,
        ),
        make_payload(provider="gemini-argon", label="Weekly", state="stale"),
    ]

    @pytest.fixture
    def wired(self, settings, make_stub):
        svc, tools = quota_tools(settings, make_stub, self.RECORDS)
        svc.refresh_sync()
        return svc, tools

    def test_describes_windows_percent_costs_and_states(self, wired):
        _, tools = wired
        out = tools.check_quotas()
        assert "claude" in out.lower()
        assert "34%" in out
        assert "$1.25" in out
        assert "$14.50" in out
        assert "[stale]" in out

    def test_balance_line_never_invents_a_percent(self, wired):
        _, tools = wired
        out = tools.check_quotas()
        deepseek_line = next(
            line for line in out.splitlines() if "deepseek" in line.lower()
        )
        assert "%" not in deepseek_line

    def test_dispatch_routes_check_quotas(self, wired):
        _, tools = wired
        assert tools.dispatch("check_quotas", {}) == tools.check_quotas()

    def test_not_ready_is_reported_instead_of_blocking(self, settings, make_stub):
        _, tools = quota_tools(settings, make_stub, self.RECORDS)  # cold cache
        out = tools.check_quotas()
        assert "not ready" in out.lower()
        assert "claude" not in out.lower()

    def test_failure_is_reported_with_its_reason(self, settings, make_stub):
        svc = QuotaService(
            runner=failing_runner(1),
            clock=FakeClock(),
            binary="/fake/ai-quotas",
            spawner=SpawnerStub(),
        )
        svc.refresh_sync()
        tools = BrainTools(settings, herdr=make_stub(), quota_service=svc)
        assert "cli-failed" in tools.check_quotas()

    def test_stale_snapshot_is_marked(self, settings, make_stub):
        clock = FakeClock()
        svc, tools = quota_tools(settings, make_stub, [make_payload()], clock=clock)
        svc.refresh_sync()
        clock.now += QUOTAS_TTL_S + 1
        assert "stale" in tools.check_quotas().lower()

    def test_prewarm_seam_kicks_one_background_job(self, wired):
        svc, tools = wired
        tools.quota_prewarm()
        assert len(svc._spawn.jobs) == 1  # type: ignore[attr-defined]

    def test_tool_output_leaves_masked(self, settings, make_stub):
        svc, tools = quota_tools(
            settings, make_stub, [make_payload(label=MASKED_LABEL)]
        )
        svc.refresh_sync()
        out = tools.check_quotas()
        assert MASKED_LABEL not in out
        assert "[masked]" in out

    def test_detail_surfaces_when_present(self, settings, make_stub):
        svc, tools = quota_tools(
            settings,
            make_stub,
            [make_payload(detail="severity: normal · disabled: out_of_credits")],
        )
        svc.refresh_sync()
        assert "disabled: out_of_credits" in tools.check_quotas()

    def test_schema_declares_a_parameterless_check_quotas(self):
        entry = next(
            item
            for item in TOOLS_SCHEMA
            if item["function"]["name"] == "check_quotas"
        )
        assert entry["type"] == "function"
        assert entry["function"]["parameters"] == {
            "type": "object",
            "properties": {},
            "required": [],
        }


# --- T3: GET /quotas (endpoint + boot prewarm seam, POST-02 S4/S5) ---

MASKED_LABEL = "sk-A1B2C3D4E5F6G7H8I9J0K1L2"  # sk- + >=20 alnum -> masked


class StubQuotaTools:
    """BrainTools double exposing just the quota seam (composition root)."""

    def __init__(self, snapshot=None):
        self._snapshot = snapshot or {
            "providers": [],
            "reason": "not-ready",
            "fetched_at": None,
            "stale": False,
        }
        self.prewarmed = False

    def quota_snapshot(self):
        return self._snapshot

    def quota_prewarm(self):
        self.prewarmed = True


def make_quota_client(tmp_path, monkeypatch, tools) -> TestClient:
    monkeypatch.setattr(server_module, "BrainTools", lambda cfg: tools)
    settings = Settings(**{
        **SETTINGS_KWARGS,
        "state_dir": str(tmp_path / "state"),
        "audio_dir": str(tmp_path / "audio"),
    })
    return TestClient(create_app(settings=settings))


class TestQuotasEndpoint:
    def ready_snapshot(self):
        return {
            "providers": [normalize_record(make_payload())],
            "reason": "",
            "fetched_at": "2026-11-26T18:00:00+00:00",
            "stale": False,
        }

    def test_serves_the_cached_snapshot(self, tmp_path, monkeypatch):
        client = make_quota_client(tmp_path, monkeypatch, StubQuotaTools(self.ready_snapshot()))
        resp = client.get("/quotas")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["reason"] == ""
        assert body["stale"] is False
        assert body["fetched_at"] == "2026-11-26T18:00:00+00:00"
        assert body["providers"][0]["provider"] == "claude"

    def test_not_ready_answers_available_false(self, tmp_path, monkeypatch):
        client = make_quota_client(tmp_path, monkeypatch, StubQuotaTools())
        body = client.get("/quotas").json()
        assert body["available"] is False
        assert body["reason"] == "not-ready"
        assert body["providers"] == []

    def test_string_fields_leave_masked(self, tmp_path, monkeypatch):
        record = normalize_record(
            make_payload(label=MASKED_LABEL, detail="api_key: supersecretvalue123")
        )
        snap = {
            "providers": [record],
            "reason": "",
            "fetched_at": None,
            "stale": False,
        }
        client = make_quota_client(tmp_path, monkeypatch, StubQuotaTools(snap))
        body = client.get("/quotas").json()
        dumped = json.dumps(body)
        assert MASKED_LABEL not in dumped
        assert "supersecretvalue123" not in dumped
        assert "[masked]" in dumped

    def test_provider_key_and_numbers_stay_exact(self, tmp_path, monkeypatch):
        record = normalize_record(make_payload(label=MASKED_LABEL, used_percent=34.0))
        snap = {"providers": [record], "reason": "", "fetched_at": None, "stale": False}
        client = make_quota_client(tmp_path, monkeypatch, StubQuotaTools(snap))
        body = client.get("/quotas").json()
        entry = body["providers"][0]
        assert entry["provider"] == "claude"
        assert entry["used_percent"] == 34.0

    def test_boot_seam_calls_quota_prewarm(self, tmp_path, monkeypatch):
        stub = StubQuotaTools()
        make_quota_client(tmp_path, monkeypatch, stub)
        assert stub.prewarmed is True

    def test_kind_and_resets_at_leave_masked(self, tmp_path, monkeypatch):
        record = normalize_record(
            make_payload(
                kind="api_key: supersecretvalue123",
                resets_at=MASKED_LABEL,
            )
        )
        snap = {
            "providers": [record],
            "reason": "",
            "fetched_at": None,
            "stale": False,
        }
        client = make_quota_client(tmp_path, monkeypatch, StubQuotaTools(snap))
        dumped = json.dumps(client.get("/quotas").json())
        assert "supersecretvalue123" not in dumped
        assert MASKED_LABEL not in dumped
        assert dumped.count("[masked]") >= 2

    def test_normal_resets_at_survives_masking(self, tmp_path, monkeypatch):
        snap = {
            "providers": [normalize_record(make_payload())],
            "reason": "",
            "fetched_at": None,
            "stale": False,
        }
        client = make_quota_client(tmp_path, monkeypatch, StubQuotaTools(snap))
        body = client.get("/quotas").json()
        assert (
            body["providers"][0]["resets_at"] == "2026-11-26T18:59:59+00:00"
        )

    def test_quotas_require_a_paired_device_once_paired(
        self, tmp_path, monkeypatch
    ):
        state_dir = tmp_path / "state"
        monkeypatch.setattr(
            server_module, "BrainTools", lambda cfg: StubQuotaTools()
        )
        settings = Settings(**{
            **SETTINGS_KWARGS,
            "state_dir": str(state_dir),
            "audio_dir": str(tmp_path / "audio"),
            "allow_any_host": False,
        })
        client = TestClient(create_app(settings=settings), base_url=LOOPBACK)
        store = pairing.PairingStore(state_dir)
        minted = store.mint()
        status, outcome = store.claim(minted["code"], "phone")
        assert status == "ok", outcome
        resp = client.get("/quotas")
        assert resp.status_code == 403

    def test_real_tools_expose_quota_snapshot(self, settings, make_stub):
        svc, tools = quota_tools(settings, make_stub, [make_payload()])
        svc.refresh_sync()
        assert tools.quota_snapshot()["reason"] == ""
        assert tools.quota_snapshot()["providers"][0]["provider"] == "claude"
