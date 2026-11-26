"""Machine load monitoring (POST-01, docs/paridad 13 §2).

T1: stdlib metric readers (/proc/stat, /proc/meminfo, /proc/net/dev,
shutil.disk_usage), the LoadSample snapshot, and the rotating 24h JSONL
history. T2 adds the operator threshold file (load-alerts.toml, doc 06
row-by-row pattern), the alert engine and the AnnouncementHub monitor.
T3 adds the /load and /load/history read routes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import herdr_brain.loadmon as loadmon


# ── fixtures: fake /proc content ─────────────────────────────────────────

PROC_STAT_1 = "cpu  100 0 100 700 0 0 0 0 0 0\n"
PROC_STAT_2 = "cpu  150 0 150 750 0 0 0 0 0 0\n"

MEMINFO = "MemTotal:       8000 kB\nMemAvailable:    2000 kB\n"

NET_DEV_1 = (
    "Inter-|   Receive                        |  Transmit\n"
    " face |bytes    packets errs drop fifo frame compressed multicast|bytes"
    "    packets errs drop fifo colls carrier compressed\n"
    "    lo: 1000 10 0 0 0 0 0 0 1000 10 0 0 0 0 0 0\n"
    "  eth0: 5000 50 0 0 0 0 0 0 3000 30 0 0 0 0 0 0\n"
)
NET_DEV_2 = NET_DEV_1.replace("eth0: 5000", "eth0: 11000").replace(
    "3000 30", "7000 30"
)


def make_proc_files(tmp_path: Path, *, stat=PROC_STAT_1, mem=MEMINFO, net=NET_DEV_1):
    paths = {
        "proc_stat": tmp_path / "stat",
        "meminfo": tmp_path / "meminfo",
        "net_dev": tmp_path / "net_dev",
    }
    paths["proc_stat"].write_text(stat, encoding="utf-8")
    paths["meminfo"].write_text(mem, encoding="utf-8")
    paths["net_dev"].write_text(net, encoding="utf-8")
    return paths


class FakeClock:
    """Controlled epoch clock for deterministic deltas."""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def fake_disk_usage(path):
    """Root 80% full, /data 50% full — never touches a real filesystem."""

    class Usage:
        def __init__(self, total, used):
            self.total = total
            self.used = used
            self.free = total - used

    if str(path) == "/data":
        return Usage(total=1000, used=500)
    return Usage(total=1000, used=800)


# ── T1: /proc readers ────────────────────────────────────────────────────


class TestProcReaders:
    def test_proc_stat_extracts_idle_and_total(self, tmp_path):
        paths = make_proc_files(tmp_path)
        idle, total = loadmon.read_proc_stat(paths["proc_stat"])
        # idle column + iowait; total = sum of all ten fields
        assert idle == 700
        assert total == 900

    def test_proc_stat_garbage_is_none(self, tmp_path):
        bad = tmp_path / "stat"
        bad.write_text("not a proc file\n", encoding="utf-8")
        assert loadmon.read_proc_stat(bad) is None

    def test_proc_stat_missing_is_none(self, tmp_path):
        assert loadmon.read_proc_stat(tmp_path / "absent") is None

    def test_meminfo_percent(self, tmp_path):
        paths = make_proc_files(tmp_path)
        assert loadmon.read_meminfo(paths["meminfo"]) == pytest.approx(75.0)

    def test_meminfo_without_available_is_none(self, tmp_path):
        partial = tmp_path / "meminfo"
        partial.write_text("MemTotal:       8000 kB\n", encoding="utf-8")
        assert loadmon.read_meminfo(partial) is None

    def test_net_dev_excludes_loopback(self, tmp_path):
        paths = make_proc_files(tmp_path)
        counters = loadmon.read_net_dev(paths["net_dev"])
        assert "lo" not in counters
        assert counters["eth0"] == (5000, 3000)

    def test_net_dev_missing_is_empty(self, tmp_path):
        assert loadmon.read_net_dev(tmp_path / "absent") == {}


# ── T1: MetricSampler ────────────────────────────────────────────────────


class TestMetricSampler:
    def make_sampler(self, tmp_path, paths, clock):
        return loadmon.MetricSampler(
            proc_stat_path=paths["proc_stat"],
            meminfo_path=paths["meminfo"],
            net_dev_path=paths["net_dev"],
            disk_usage_fn=fake_disk_usage,
            clock=clock,
        )

    def test_first_sample_has_no_deltas(self, tmp_path):
        paths = make_proc_files(tmp_path)
        sampler = self.make_sampler(tmp_path, paths, FakeClock())
        sample = sampler.sample()
        assert sample.cpu_percent is None
        assert sample.net_rx_bps is None
        assert sample.net_tx_bps is None
        assert sample.ram_percent == pytest.approx(75.0)
        assert sample.disk_percent == pytest.approx(80.0)
        assert sample.ts == 1000.0

    def test_second_sample_computes_cpu_and_net_deltas(self, tmp_path):
        paths = make_proc_files(tmp_path)
        clock = FakeClock()
        sampler = self.make_sampler(tmp_path, paths, clock)
        sampler.sample()

        paths["proc_stat"].write_text(PROC_STAT_2, encoding="utf-8")
        paths["net_dev"].write_text(NET_DEV_2, encoding="utf-8")
        clock.advance(60.0)
        sample = sampler.sample()

        # busy delta 100 / total delta 150 → 33.3% busy → 66.7% load
        assert sample.cpu_percent == pytest.approx(100 * (1 - 50 / 150), rel=1e-3)
        # rx delta 6000 bytes over 60 s → 100 B/s; tx 4000/60
        assert sample.net_rx_bps == pytest.approx(100.0)
        assert sample.net_tx_bps == pytest.approx(4000 / 60.0)

    def test_interface_gone_between_polls_drops_its_delta(self, tmp_path):
        paths = make_proc_files(tmp_path)
        clock = FakeClock()
        sampler = self.make_sampler(tmp_path, paths, clock)
        sampler.sample()

        only_lo = NET_DEV_1.splitlines()[0:3]
        (tmp_path / "net_dev").write_text("\n".join(only_lo) + "\n", encoding="utf-8")
        clock.advance(60.0)
        sample = sampler.sample()
        # eth0 disappeared: no common interface, no honest delta
        assert sample.net_rx_bps is None
        assert sample.net_tx_bps is None

    def test_extra_disk_paths_beyond_root(self, tmp_path):
        paths = make_proc_files(tmp_path)
        sampler = loadmon.MetricSampler(
            proc_stat_path=paths["proc_stat"],
            meminfo_path=paths["meminfo"],
            net_dev_path=paths["net_dev"],
            disk_usage_fn=fake_disk_usage,
            clock=FakeClock(),
            extra_disk_paths=("/data",),
        )
        sample = sampler.sample()
        assert sample.disk_percent == pytest.approx(80.0)
        assert dict(sample.extra_disk)["/data"] == pytest.approx(50.0)


# ── T1: LoadHistory (JSONL, 24h window, rotation) ────────────────────────


def sample_row(ts: float, cpu: float = 12.5) -> dict:
    return {
        "ts": ts,
        "cpu_percent": cpu,
        "ram_percent": 61.2,
        "disk_percent": 78.5,
        "net_rx_bps": 100.0,
        "net_tx_bps": 40.0,
    }


class TestLoadHistory:
    def test_append_and_read_roundtrip(self, tmp_path):
        history = loadmon.LoadHistory(
            tmp_path / "load-history.jsonl", clock=lambda: 2000.0
        )
        history.append_row(sample_row(1000.0))
        history.append_row(sample_row(1060.0, cpu=20.0))
        rows = history.read()
        assert len(rows) == 2
        assert rows[1]["cpu_percent"] == 20.0

    def test_read_filters_outside_the_window(self, tmp_path):
        history = loadmon.LoadHistory(
            tmp_path / "load-history.jsonl",
            clock=lambda: 100000.0,
        )
        history.append_row(sample_row(1000.0))  # >24h old
        history.append_row(sample_row(100000.0 - 30.0))  # fresh
        rows = history.read()
        assert len(rows) == 1
        assert rows[0]["ts"] == pytest.approx(100000.0 - 30.0)

    def test_rotation_caps_rows(self, tmp_path):
        path = tmp_path / "load-history.jsonl"
        history = loadmon.LoadHistory(path, max_rows=5, clock=lambda: 100.0)
        for i in range(8):
            history.append_row(sample_row(float(i)))
        rows = history.read()
        assert len(rows) == 5
        assert rows[0]["ts"] == 3.0  # oldest three rotated away

    def test_corrupt_line_is_skipped_not_fatal(self, tmp_path):
        path = tmp_path / "load-history.jsonl"
        history = loadmon.LoadHistory(path, clock=lambda: 2000.0)
        history.append_row(sample_row(1000.0))
        with path.open("a", encoding="utf-8") as handle:
            handle.write("{not json at all\n")
        history.append_row(sample_row(1060.0))
        rows = history.read()
        assert len(rows) == 2

    def test_missing_file_reads_empty(self, tmp_path):
        history = loadmon.LoadHistory(tmp_path / "absent.jsonl")
        assert history.read() == []

    def test_unreadable_row_shapes_never_crash_read(self, tmp_path):
        path = tmp_path / "load-history.jsonl"
        path.write_text('["a","list"]\n{"ts":"nan"}\n', encoding="utf-8")
        assert loadmon.LoadHistory(path).read() == []


# ── T2: load-alerts.toml (doc 06 row-by-row pattern) ─────────────────────


def write_alerts(tmp_path: Path, text: str) -> Path:
    path = tmp_path / loadmon.FILE_ALERTS
    path.write_text(text, encoding="utf-8")
    return path


class TestValidateAlerts:
    def test_valid_rows_with_defaults(self):
        rows, issues = loadmon.validate_alerts(
            {"alerts": [{"metric": "ram", "threshold": 90}]}
        )
        assert issues == ()
        assert rows[0].metric == "ram"
        assert rows[0].threshold == 90
        assert rows[0].path == "/"
        assert rows[0].iface is None
        assert rows[0].reannounce_s == loadmon.DEFAULT_REANNOUNCE_S
        assert rows[0].enabled is True

    def test_unknown_field_skips_the_row_only(self):
        rows, issues = loadmon.validate_alerts(
            {
                "alerts": [
                    {"metric": "cpu", "threshold": 95, "metic": "typo"},
                    {"metric": "ram", "threshold": 90},
                ]
            }
        )
        assert len(rows) == 1
        assert rows[0].metric == "ram"
        assert issues[0].row == 1
        assert "unknown field" in issues[0].reason

    def test_bad_metric_and_threshold_are_skipped(self):
        rows, issues = loadmon.validate_alerts(
            {
                "alerts": [
                    {"metric": "temperature", "threshold": 50},
                    {"metric": "cpu", "threshold": 0},
                    {"metric": "cpu", "threshold": "high"},
                    {"metric": "cpu", "threshold": 95},
                ]
            }
        )
        assert [row.metric for row in rows] == ["cpu"]
        assert len(issues) == 3

    def test_path_only_applies_to_disk_rows(self):
        rows, issues = loadmon.validate_alerts(
            {
                "alerts": [
                    {"metric": "cpu", "threshold": 95, "path": "/data"},
                    {"metric": "net_rx", "threshold": 100, "iface": "wlan0"},
                ]
            }
        )
        assert [row.metric for row in rows] == ["net_rx"]
        assert len(issues) == 1
        assert "path" in issues[0].reason

    def test_iface_only_applies_to_net_rows(self):
        rows, issues = loadmon.validate_alerts(
            {"alerts": [{"metric": "ram", "threshold": 90, "iface": "eth0"}]}
        )
        assert rows == ()

    def test_enabled_false_excludes_the_row(self):
        rows, _ = loadmon.validate_alerts(
            {"alerts": [{"metric": "cpu", "threshold": 95, "enabled": False}]}
        )
        assert len(rows) == 1
        assert rows[0].enabled is False

    def test_bad_enabled_type_is_skipped(self):
        rows, issues = loadmon.validate_alerts(
            {"alerts": [{"metric": "cpu", "threshold": 95, "enabled": "yes"}]}
        )
        assert rows == ()
        assert "enabled" in issues[0].reason


class TestAlertStore:
    def test_missing_file_yields_no_rows(self, tmp_path):
        store = loadmon.AlertStore(tmp_path)
        assert store.rows() == ()

    def test_rows_roundtrip(self, tmp_path):
        write_alerts(tmp_path, '[[alerts]]\nmetric = "cpu"\nthreshold = 95\n')
        store = loadmon.AlertStore(tmp_path)
        rows = store.rows()
        assert len(rows) == 1
        assert rows[0].threshold == 95

    def test_mtime_reload_picks_up_edits(self, tmp_path):
        path = write_alerts(tmp_path, '[[alerts]]\nmetric = "cpu"\nthreshold = 95\n')
        store = loadmon.AlertStore(tmp_path)
        assert store.rows()[0].threshold == 95
        path.write_text(
            '[[alerts]]\nmetric = "ram"\nthreshold = 91\n', encoding="utf-8"
        )
        rows = store.rows()
        assert rows[0].metric == "ram"
        assert rows[0].threshold == 91

    def test_unparsable_file_keeps_last_valid_rows(self, tmp_path):
        path = write_alerts(tmp_path, '[[alerts]]\nmetric = "cpu"\nthreshold = 95\n')
        store = loadmon.AlertStore(tmp_path)
        assert store.rows()[0].threshold == 95
        path.write_text("this is [[ not toml", encoding="utf-8")
        assert store.rows()[0].threshold == 95

    def test_malformed_rows_are_skipped_siblings_survive(self, tmp_path):
        write_alerts(
            tmp_path,
            '[[alerts]]\nmetric = "cpu"\nthreshold = 95\n'
            '[[alerts]]\nmetric = "bogus"\nthreshold = 1\n'
            '[[alerts]]\nmetric = "ram"\nthreshold = 90\n',
        )
        rows = loadmon.AlertStore(tmp_path).rows()
        assert [row.metric for row in rows] == ["cpu", "ram"]


# ── T2: AlertEngine (hysteresis, re-announce, recovery) ──────────────────


def make_sample(**overrides) -> loadmon.LoadSample:
    fields = {
        "ts": 1000.0,
        "cpu_percent": 30.0,
        "ram_percent": 50.0,
        "disk_percent": 40.0,
        "net_rx_bps": 1000.0,
        "net_tx_bps": 500.0,
    }
    fields.update(overrides)
    return loadmon.LoadSample(**fields)


def alert_rows(*rows) -> tuple:
    """The validated rows tuple for one or more raw alert dicts."""
    return loadmon.validate_alerts({"alerts": list(rows)})[0]


class RecordingClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class TestAlertEngine:
    def test_ram_crossing_warns_with_the_design_example_text(self):
        engine = loadmon.AlertEngine("azul")
        alerts = alert_rows(*[{"metric": "ram", "threshold": 90}])
        events = engine.evaluate(make_sample(ram_percent=95.4), alerts)
        assert len(events) == 1
        assert events[0]["kind"] == "warning"
        assert events[0]["text"] == "la máquina azul está al 95 % de RAM"

    def test_sustained_above_stays_quiet_until_reannounce(self):
        clock = RecordingClock()
        engine = loadmon.AlertEngine("azul", clock=clock)
        alerts = alert_rows(*[{"metric": "cpu", "threshold": 90, "reannounce_s": 600}])
        hot = make_sample(cpu_percent=95.0)
        assert len(engine.evaluate(hot, alerts)) == 1  # initial crossing
        clock.advance(300.0)
        assert engine.evaluate(hot, alerts) == []  # still inside window
        clock.advance(300.1)  # 600.1s since the warning
        events = engine.evaluate(hot, alerts)
        assert len(events) == 1
        assert events[0]["kind"] == "warning"

    def test_recovery_after_hysteresis_margin(self):
        engine = loadmon.AlertEngine("azul")
        alerts = alert_rows(*[{"metric": "ram", "threshold": 90}])
        assert len(engine.evaluate(make_sample(ram_percent=95.0), alerts)) == 1
        # inside the dead zone (85 < v < 90): still "above", no recovery yet
        assert engine.evaluate(make_sample(ram_percent=87.0), alerts) == []
        # clearly below the margin: recovery announcement
        events = engine.evaluate(make_sample(ram_percent=80.0), alerts)
        assert len(events) == 1
        assert events[0]["kind"] == "info"
        assert events[0]["text"] == (
            "la máquina azul se normalizó: RAM por debajo del umbral"
        )
        # quiet afterwards, and a fresh crossing warns again
        assert engine.evaluate(make_sample(ram_percent=80.0), alerts) == []
        assert len(engine.evaluate(make_sample(ram_percent=95.0), alerts)) == 1

    def test_hot_boot_warns_once_like_the_daemon_watcher(self):
        engine = loadmon.AlertEngine("azul")
        alerts = alert_rows(*[{"metric": "ram", "threshold": 90}])
        events = engine.evaluate(make_sample(ram_percent=95.0), alerts)
        assert len(events) == 1

    def test_none_value_never_announces(self):
        engine = loadmon.AlertEngine("azul")
        alerts = alert_rows(*[{"metric": "cpu", "threshold": 10}])
        assert engine.evaluate(make_sample(cpu_percent=None), alerts) == []

    def test_disk_row_uses_extra_disk_paths(self):
        engine = loadmon.AlertEngine("azul")
        alerts = alert_rows(*[
                    {"metric": "disk", "threshold": 45, "path": "/data"},
                ])
        sample = make_sample(extra_disk=(("/data", 50.0),))
        events = engine.evaluate(sample, alerts)
        assert events[0]["text"] == "la máquina azul: el disco /data está al 50 %"

    def test_net_threshold_is_absolute_bytes_per_second(self):
        engine = loadmon.AlertEngine("azul")
        rx_alert = alert_rows(*[{"metric": "net_rx", "threshold": 1_000_000}])
        events = engine.evaluate(make_sample(net_rx_bps=1_200_000.0), rx_alert)
        assert events[0]["text"] == "la máquina azul recibe 1.2 MB/s"

    def test_iface_row_evaluates_that_interface_only(self):
        engine = loadmon.AlertEngine("azul")
        alerts = alert_rows(*[{"metric": "net_tx", "threshold": 100, "iface": "wlan0"}])
        # the SUM is quiet but wlan0 alone is hot
        sample = make_sample(
            net_tx_bps=10.0, extra_net=(("wlan0", 5.0, 150.0), ("eth0", 5.0, 5.0))
        )
        events = engine.evaluate(sample, alerts)
        assert len(events) == 1

    def test_iface_absent_from_sample_skips_the_row(self):
        engine = loadmon.AlertEngine("azul")
        alerts = alert_rows(*[{"metric": "net_tx", "threshold": 100, "iface": "wlan1"}])
        sample = make_sample(extra_net=(("wlan0", 5.0, 150.0),))
        assert engine.evaluate(sample, alerts) == []

    def test_active_count_counts_rows_currently_above(self):
        engine = loadmon.AlertEngine("azul")
        alerts = alert_rows(
            {"metric": "ram", "threshold": 90},
            {"metric": "cpu", "threshold": 95},
        )
        engine.evaluate(make_sample(ram_percent=95.0, cpu_percent=96.0), alerts)
        assert engine.active_count() == 2
        engine.evaluate(make_sample(ram_percent=95.0, cpu_percent=10.0), alerts)
        assert engine.active_count() == 1


# ── T2: LoadMonitor (thread body, hub announcements, degradation) ────────


class FakeHub:
    def __init__(self):
        self.published = []

    def publish(self, announcement):
        self.published.append(announcement)


class FakeSampler:
    def __init__(self, samples):
        self._samples = list(samples)
        self.last_extra_disk = None

    def sample(self, *, extra_disk_paths=None):
        self.last_extra_disk = extra_disk_paths
        if self._samples:
            return self._samples.pop(0)
        return make_sample()


class FakeAlertStore:
    def __init__(self, rows):
        self._rows = tuple(rows)

    def rows(self):
        return self._rows


class NullHistory:
    """A dump bucket so polling tests that don't assert history never
    touch the filesystem."""

    def append(self, sample):
        pass

    def append_row(self, row):
        pass

    def read(self, *, window_s=None):
        return []


def make_monitor(sample_list, *, alert_rows=(), hub=None, tts=None, **kwargs):
    parts = dict(
        history=NullHistory(),
    )
    parts.update(kwargs)
    return loadmon.LoadMonitor(
        settings=None,
        hub=hub or FakeHub(),
        tts_renderer=tts,
        sampler=FakeSampler(sample_list),
        alert_store=FakeAlertStore(
            loadmon.validate_alerts({"alerts": list(alert_rows)})[0]
        ),
        **parts,
    )


class TestLoadMonitor:
    def test_poll_appends_history_and_publishes_crossing(self, tmp_path):
        hub = FakeHub()
        history = loadmon.LoadHistory(tmp_path / "h.jsonl", clock=lambda: 1000.0)
        monitor = make_monitor(
            [make_sample(ram_percent=95.0)],
            alert_rows=[{"metric": "ram", "threshold": 90}],
            hub=hub,
            history=history,
        )
        announcements = monitor.poll_once()
        assert len(announcements) == 1
        assert hub.published == announcements
        ann = announcements[0]
        assert ann["type"] == "system"
        assert ann["kind"] == "warning"
        assert ann["label"] == monitor.hostname
        assert "95 % de RAM" in ann["text"]
        assert ann["audio_url"] is None
        assert len(history.read()) == 1

    def test_tts_failure_keeps_the_text_announcement(self):
        def broken_tts(settings, text, out_path):
            raise RuntimeError("no speech backend")

        monitor = make_monitor(
            [make_sample(ram_percent=95.0)],
            alert_rows=[{"metric": "ram", "threshold": 90}],
            tts=broken_tts,
        )
        announcements = monitor.poll_once()
        assert len(announcements) == 1
        assert announcements[0]["audio_url"] is None

    def test_mask_text_seal_before_publish(self, monkeypatch):
        # C1: the outbound mask runs at the announcement seam, every time.
        seen = {}
        real_mask = loadmon.mask_text

        def spy_mask(text):
            seen["called"] = seen.get("called", 0) + 1
            return real_mask(text)

        monkeypatch.setattr(loadmon, "mask_text", spy_mask)
        monitor = make_monitor(
            [make_sample(ram_percent=95.0)],
            alert_rows=[{"metric": "ram", "threshold": 90}],
        )
        monitor.poll_once()
        assert seen.get("called", 0) >= 1

    def test_unsupported_platform_polls_nothing_and_starts_no_thread(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(loadmon, "platform_supported", lambda: False)
        hub = FakeHub()
        monitor = make_monitor(
            [make_sample()],
            alert_rows=[{"metric": "ram", "threshold": 90}],
            hub=hub,
        )
        assert monitor.poll_once() == []
        assert hub.published == []
        monitor.start()
        assert monitor._thread is None  # no sampler thread off /proc

    def test_snapshot_reports_last_sample_and_alert_counts(self, tmp_path):
        monitor = make_monitor(
            [make_sample(ram_percent=95.0)],
            alert_rows=[{"metric": "ram", "threshold": 90}],
        )
        monitor.poll_once()
        snap = monitor.snapshot()
        assert snap["available"] is True
        assert snap["host"] == monitor.hostname
        assert snap["ram_percent"] == pytest.approx(95.0)
        assert snap["alerts"] == {"configured": 1, "active": 1}

    def test_snapshot_without_poll_is_available_with_nones(self):
        monitor = make_monitor([])
        snap = monitor.snapshot()
        assert snap["available"] is True
        assert snap["cpu_percent"] is None

    def test_unsupported_snapshot_is_honest(self, monkeypatch):
        monkeypatch.setattr(loadmon, "platform_supported", lambda: False)
        monitor = make_monitor([])
        snap = monitor.snapshot()
        assert snap["available"] is False
        assert "reason" in snap

    def test_history_view_window_and_unknown_host(self, tmp_path):
        history = loadmon.LoadHistory(tmp_path / "h.jsonl", clock=lambda: 1000.0)
        monitor = make_monitor([], history=history, hostname="azul")
        history.append_row(sample_row(995.0))
        local = monitor.history_view()
        assert local["host"] == "azul"
        assert local["count"] == 1
        other = monitor.history_view(host="otra", hours=24.0)
        assert other == {"host": "otra", "hours": 24.0, "samples": [], "count": 0}


# ── T3: /load and /load/history routes ───────────────────────────────────

from fastapi.testclient import TestClient  # noqa: E402

from herdr_brain.access import classify, is_device_exempt  # noqa: E402
from herdr_brain.config import Settings  # noqa: E402
from herdr_brain.server import create_app  # noqa: E402
from tests.conftest import SETTINGS_KWARGS  # noqa: E402


class StubLoadMonitor:
    """Duck-typed monitor double for the route surface."""

    def __init__(self, snapshot=None, history=None):
        self._snapshot = snapshot or {}
        self._history = history or {}
        self.history_calls = []

    def snapshot(self):
        return dict(self._snapshot)

    def history_view(self, host=None, hours=24.0):
        self.history_calls.append({"host": host, "hours": hours})
        return dict(self._history)


def make_route_client(tmp_path, monitor) -> TestClient:
    settings = Settings(
        **{
            **SETTINGS_KWARGS,
            "state_dir": str(tmp_path / "state"),
            "audio_dir": str(tmp_path / "audio"),
        }
    )
    return TestClient(create_app(settings=settings, loadmon=monitor))


class TestLoadRoutesClassify:
    def test_both_routes_are_device_gated_reads(self):
        assert classify("GET", "/load") == "read"
        assert classify("GET", "/load/history") == "read"
        assert is_device_exempt("GET", "/load") is False
        assert is_device_exempt("GET", "/load/history") is False


class TestLoadRoutes:
    def test_load_serves_the_monitor_snapshot(self, tmp_path):
        monitor = StubLoadMonitor(
            snapshot={
                "host": "azul",
                "available": True,
                "cpu_percent": 12.5,
                "ram_percent": 61.2,
                "disk_percent": 78.5,
                "net_rx_bps": 100.0,
                "net_tx_bps": 40.0,
                "alerts": {"configured": 1, "active": 0},
            }
        )
        client = make_route_client(tmp_path, monitor)
        response = client.get("/load")
        assert response.status_code == 200
        payload = response.json()
        assert payload["host"] == "azul"
        assert payload["available"] is True
        assert payload["ram_percent"] == pytest.approx(61.2)
        assert payload["alerts"] == {"configured": 1, "active": 0}

    def test_unsupported_snapshot_is_an_honest_200(self, tmp_path):
        monitor = StubLoadMonitor(
            snapshot={
                "host": "azul",
                "available": False,
                "reason": "load metrics read Linux /proc (unavailable on darwin)",
                "alerts": {"configured": 0, "active": 0},
            }
        )
        client = make_route_client(tmp_path, monitor)
        response = client.get("/load")
        assert response.status_code == 200
        assert response.json()["available"] is False

    def test_history_defaults_are_local_host_and_24h(self, tmp_path):
        monitor = StubLoadMonitor(
            history={"host": "azul", "hours": 24.0, "samples": [], "count": 0}
        )
        client = make_route_client(tmp_path, monitor)
        response = client.get("/load/history")
        assert response.status_code == 200
        assert response.json()["count"] == 0
        assert monitor.history_calls == [{"host": None, "hours": 24.0}]

    def test_history_query_params_reach_the_monitor(self, tmp_path):
        monitor = StubLoadMonitor(history={"host": "otra", "samples": [], "count": 0})
        client = make_route_client(tmp_path, monitor)
        response = client.get("/load/history", params={"host": "otra", "hours": 6})
        assert response.status_code == 200
        assert monitor.history_calls == [{"host": "otra", "hours": 6.0}]

    def test_hours_outside_the_cap_is_422(self, tmp_path):
        client = make_route_client(tmp_path, StubLoadMonitor())
        assert client.get("/load/history", params={"hours": 0}).status_code == 422
        assert client.get("/load/history", params={"hours": 30}).status_code == 422

    def test_unknown_host_answers_an_honest_empty_window(self, tmp_path):
        monitor = StubLoadMonitor(
            history={"host": "fantasma", "hours": 24.0, "samples": [], "count": 0}
        )
        client = make_route_client(tmp_path, monitor)
        response = client.get("/load/history", params={"host": "fantasma"})
        assert response.status_code == 200
        assert response.json()["samples"] == []


class TestLoadMonitorWiring:
    def make_settings(self, tmp_path):
        return Settings(
            **{
                **SETTINGS_KWARGS,
                "state_dir": str(tmp_path / "state"),
                "audio_dir": str(tmp_path / "audio"),
            }
        )

    def test_gate_on_builds_and_starts_the_real_monitor(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERDR_BRAIN_LOADMON", "on")
        app = create_app(settings=self.make_settings(tmp_path))
        monitor = app.state.loadmon
        assert isinstance(monitor, loadmon.LoadMonitor)
        # the SAME hub as the agent watcher: load alerts ride the existing
        # SSE stream and spoken channel
        assert monitor._hub is app.state.watcher.hub
        if loadmon.platform_supported():
            assert monitor._thread is not None
        else:
            assert monitor._thread is None

    def test_shutdown_handler_stops_the_sampler(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERDR_BRAIN_LOADMON", "on")
        app = create_app(settings=self.make_settings(tmp_path))
        with TestClient(app):
            pass  # lifespan: startup + shutdown
        assert app.state.loadmon._stop.is_set()

    def test_gate_off_auto_starts_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERDR_BRAIN_LOADMON", "off")
        app = create_app(settings=self.make_settings(tmp_path))
        monitor = app.state.loadmon
        assert isinstance(monitor, loadmon.LoadMonitor)
        assert monitor._thread is None  # tests must not leak sampler threads

    def test_injected_monitor_is_not_auto_started(self, tmp_path, monkeypatch):
        class StartableStub(StubLoadMonitor):
            started = False

            def start(self):
                self.started = True

        stub = StartableStub()
        monkeypatch.setenv("HERDR_BRAIN_LOADMON", "on")
        client = make_route_client(tmp_path, stub)
        assert client.app.state.loadmon is stub
        assert stub.started is False  # the caller owns an injected lifecycle


class TestHostnameMaskingC1:
    @staticmethod
    def make(hostname):
        return loadmon.LoadMonitor(
            None, hostname=hostname, history=NullHistory()
        )

    def test_ordinary_hostnames_pass_through(self):
        assert self.make("azul").hostname == "azul"

    def test_hostname_carrying_a_secret_shape_is_masked(self):
        # a hostname is system-controlled, not operator-free-text — but
        # C1 makes outbound masking unconditional, and a lookalike
        # ("sk-" + 20 chars) must never reach SSE, /load or speech.
        monitor = self.make("sk-abcdefghijklmnopqrst")
        assert "sk-abcdefghijklmnopqrst" not in monitor.hostname
        assert "[masked]" in monitor.hostname


class TestImmediateBootSample:
    def test_start_samples_immediately_on_the_boot_thread(self):
        # /load must be useful within seconds of boot (not after the
        # first minute): start() takes the boot sample synchronously,
        # then the thread waits a full interval before the next poll.
        monitor = make_monitor(
            [make_sample(ram_percent=42.0)],
            alert_rows=[{"metric": "ram", "threshold": 90}],
        )
        monitor.start()
        try:
            assert monitor._last_sample is not None
            assert monitor._last_sample.ram_percent == pytest.approx(42.0)
        finally:
            monitor.stop()

    def test_start_off_proc_hosts_never_samples(self, monkeypatch):
        monkeypatch.setattr(loadmon, "platform_supported", lambda: False)
        monitor = make_monitor([make_sample()])
        monitor.start()
        assert monitor._last_sample is None
        assert monitor._thread is None


class TestHermeticGate:
    def test_conftest_loadmon_gate_is_off_for_every_test(self):
        # the hermetic autouse fixture must keep the default sampler OFF
        # for the whole suite (no real /proc reads, no host config reads,
        # no state writes outside tmp)
        source = Path("tests/conftest.py").read_text(encoding="utf-8")
        assert 'patch.setenv("HERDR_BRAIN_LOADMON", "off")' in source


# ── Verifier round: blockers 1-3, 5-6 ────────────────────────────────────


class _Usage:
    def __init__(self, total, used):
        self.total = total
        self.used = used
        self.free = total - used


class TestNonRootDiskAlerts:
    def test_disk_alert_on_non_root_path_fires_end_to_end(self, tmp_path):
        # The monitor must feed the sampler the disk paths the operator
        # configured (and re-read them every poll, mtime reload intact):
        # a disk row targeting anything other than '/' must actually fire.
        data_dir = str(tmp_path / "data")
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / loadmon.FILE_ALERTS).write_text(
            f'[[alerts]]\nmetric = "disk"\nthreshold = 90\npath = "{data_dir}"\n',
            encoding="utf-8",
        )

        def usage_by_path(path):
            if str(path) == data_dir:
                return _Usage(total=100, used=95)
            return _Usage(total=1000, used=10)

        sampler = loadmon.MetricSampler(
            proc_stat_path=tmp_path / "absent-stat",
            meminfo_path=tmp_path / "absent-meminfo",
            net_dev_path=tmp_path / "absent-netdev",
            disk_usage_fn=usage_by_path,
            clock=FakeClock(),
        )
        monitor = loadmon.LoadMonitor(
            None,
            hub=FakeHub(),
            sampler=sampler,
            history=NullHistory(),
            alert_store=loadmon.AlertStore(config_dir),
        )
        announcements = monitor.poll_once()
        assert len(announcements) == 1
        assert data_dir in announcements[0]["text"]
        assert "95 %" in announcements[0]["text"]


class ExplodingHistory(NullHistory):
    def append(self, sample):
        raise OSError(28, "No space left on device")


class TestHistoryFailureIsolation:
    def test_enospc_on_history_never_blocks_the_alert(self):
        # the disk being FULL is exactly when the disk-full alert matters:
        # a history write failure must not stop evaluation
        monitor = make_monitor(
            [make_sample(ram_percent=95.0)],
            alert_rows=[{"metric": "ram", "threshold": 90}],
            history=ExplodingHistory(),
        )
        announcements = monitor.poll_once()
        assert len(announcements) == 1
        assert "95 % de RAM" in announcements[0]["text"]

    def test_invalid_utf8_byte_in_history_is_tolerated(self, tmp_path):
        path = tmp_path / "load-history.jsonl"
        # a stray byte on its own line (a torn write): replaced, skipped,
        # and the healthy rows around it survive
        path.write_bytes(b'\xff\n{"ts": 1000.0, "cpu_percent": 1.0}\n{"ts": 1060.0}\n')
        rows = loadmon.LoadHistory(path, clock=lambda: 2000.0).read()
        assert len(rows) == 2

    def test_history_route_survives_corrupt_bytes(self, tmp_path):
        corrupt = tmp_path / "load-history.jsonl"
        corrupt.write_bytes(b'\xff\n{"ts": 1000.0, "cpu_percent": 2.0}\n')
        history = loadmon.LoadHistory(corrupt, clock=lambda: 2000.0)
        monitor = make_monitor([], history=history)
        client = make_route_client(tmp_path, monitor)
        response = client.get("/load/history")
        assert response.status_code == 200
        assert response.json()["count"] == 1


class TestSamplerDtZero:
    def test_zero_dt_never_divides_by_zero(self, tmp_path):
        paths = make_proc_files(tmp_path)
        clock = FakeClock()
        sampler = loadmon.MetricSampler(
            proc_stat_path=paths["proc_stat"],
            meminfo_path=paths["meminfo"],
            net_dev_path=paths["net_dev"],
            disk_usage_fn=fake_disk_usage,
            clock=clock,
        )
        sampler.sample()
        paths["proc_stat"].write_text(PROC_STAT_2, encoding="utf-8")
        paths["net_dev"].write_text(NET_DEV_2, encoding="utf-8")
        # clock frozen: dt == 0, but the counters moved
        sample = sampler.sample()
        assert sample.net_rx_bps is None
        assert sample.net_tx_bps is None


class TestEngineStateHygiene:
    def test_stale_alert_rules_are_pruned_from_state(self):
        engine = loadmon.AlertEngine("azul")
        hot = alert_rows({"metric": "ram", "threshold": 90})
        engine.evaluate(make_sample(ram_percent=95.0), hot)
        assert engine.active_count() == 1
        # the operator removed the rule: the next poll must prune it
        assert engine.evaluate(make_sample(ram_percent=95.0), ()) == []
        assert engine.active_count() == 0

    def test_nan_and_inf_thresholds_are_rejected(self):
        rows, issues = loadmon.validate_alerts(
            {
                "alerts": [
                    {"metric": "cpu", "threshold": float("nan")},
                    {"metric": "cpu", "threshold": float("inf")},
                    {"metric": "cpu", "threshold": float("-inf")},
                    {"metric": "cpu", "threshold": 95},
                ]
            }
        )
        assert [row.threshold for row in rows] == [95]
        assert len(issues) == 3
        assert "finite" in issues[0].reason

    def test_net_recovery_uses_proportional_margin(self):
        engine = loadmon.AlertEngine("azul")
        alerts = alert_rows({"metric": "net_rx", "threshold": 1000})
        assert len(engine.evaluate(make_sample(net_rx_bps=1200.0), alerts)) == 1
        # 5% margin (50 B/s): 970 is still inside the dead zone
        assert engine.evaluate(make_sample(net_rx_bps=970.0), alerts) == []
        events = engine.evaluate(make_sample(net_rx_bps=900.0), alerts)
        assert len(events) == 1
        assert events[0]["kind"] == "info"


class TestHistoryReadHardening:
    def test_boolean_or_nonfinite_ts_rows_are_ignored(self, tmp_path):
        path = tmp_path / "load-history.jsonl"
        path.write_text('{"ts": true}\n{"ts": NaN}\n{"ts": 1000.0}\n', encoding="utf-8")
        rows = loadmon.LoadHistory(path, clock=lambda: 2000.0).read()
        assert len(rows) == 1
        assert rows[0]["ts"] == 1000.0


class TestHistoryViewHardening:
    def test_host_param_is_masked_before_serving(self):
        monitor = make_monitor([], hostname="azul")
        out = monitor.history_view(host="sk-abcdefghijklmnopqrst")
        assert "sk-abcdefghijklmnopqrst" not in out["host"]
        assert "[masked]" in out["host"]

    def test_off_proc_host_serves_an_honest_empty_window(self, tmp_path, monkeypatch):
        history = loadmon.LoadHistory(tmp_path / "h.jsonl", clock=lambda: 1000.0)
        history.append_row(sample_row(995.0))
        monkeypatch.setattr(loadmon, "platform_supported", lambda: False)
        monitor = make_monitor([], history=history, hostname="azul")
        out = monitor.history_view()
        assert out == {"host": "azul", "hours": 24.0, "samples": [], "count": 0}


class TestProcStatFieldGuard:
    def test_truncated_cpu_line_is_none(self, tmp_path):
        partial = tmp_path / "stat"
        partial.write_text("cpu  100 0\n", encoding="utf-8")
        assert loadmon.read_proc_stat(partial) is None
