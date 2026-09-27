"""Tests for the herdr-tts daemon liveness probe + fail-noisy warnings.

The probe logic is exercised through the pure `_evaluate` plus I/O edge
cases; the watcher through injected probe/hub/clock — never the real
daemon, never the real pidfile.
"""

from __future__ import annotations

from pathlib import Path

from herdr_brain.config import Settings
from herdr_brain.tts_daemon import (
    DAEMON_DOWN,
    DAEMON_DOWN_TEXT,
    DAEMON_UP,
    DAEMON_UP_TEXT,
    DaemonWatcher,
    _evaluate,
    daemon_status,
)
from herdr_brain.watcher import AnnouncementHub


class FakeHub(AnnouncementHub):
    """Records publishes instead of fanning out to subscribers."""

    def __init__(self):
        super().__init__()
        self.published: list = []

    def publish(self, announcement):  # noqa: D102 — test double
        self.published.append(announcement)


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def make_watcher(settings: Settings, probe, clock, hub=None, **kwargs) -> DaemonWatcher:
    return DaemonWatcher(
        settings,
        hub=hub or FakeHub(),
        probe=probe,
        clock=clock,
        poll_interval_s=0.01,
        **kwargs,
    )


class TestProbeLogic:
    def test_daemon_cmdline_is_up(self):
        assert _evaluate(1, ["bash", "bin/herdr-tts", "_daemon"]) == DAEMON_UP

    def test_dead_process_is_down(self):
        assert _evaluate(1, None) == DAEMON_DOWN

    def test_pid_reuse_to_unrelated_process_is_down(self):
        # The pidfile anchor + alive check passed, but the recycled pid now
        # runs something else — the cmdline mention guard catches it.
        assert _evaluate(1, ["bash", "some-unrelated-tool"]) == DAEMON_DOWN

    def test_missing_pid_file_is_down(self, tmp_path):
        assert daemon_status(pid_file=str(tmp_path / "nope.pid")) == DAEMON_DOWN

    def test_garbage_pid_file_is_down(self, tmp_path):
        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text("not-a-pid\n")
        assert daemon_status(pid_file=str(pid_file)) == DAEMON_DOWN

    def test_dead_pid_in_file_is_down(self, tmp_path):
        # A pid that cannot exist in /proc (SIGKILLed daemon, stale file).
        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text("40000000\n")
        assert daemon_status(pid_file=str(pid_file)) == DAEMON_DOWN


class TestDaemonWatcherTransitions:
    """Every case asserts HUB DELIVERY, not just the return value — the
    announcement must actually reach subscribers (a watcher that only
    built events and never published them once shipped)."""

    def test_boot_while_up_stays_silent(self, settings: Settings):
        clock = FakeClock()
        watcher = make_watcher(settings, probe=lambda: DAEMON_UP, clock=clock)
        assert watcher.poll_once() == []
        assert watcher._hub.published == []

    def test_boot_while_down_announces_exactly_once(self, settings: Settings):
        clock = FakeClock()
        watcher = make_watcher(settings, probe=lambda: DAEMON_DOWN, clock=clock)
        produced = watcher.poll_once()
        assert len(produced) == 1
        ann = produced[0]
        assert ann["type"] == "system"
        assert ann["kind"] == "warning"
        assert ann["text"] == DAEMON_DOWN_TEXT
        assert watcher._hub.published == produced  # delivered to the hub
        # No re-announce while still down before the debounce window.
        clock.advance(60)
        assert watcher.poll_once() == []
        assert len(watcher._hub.published) == 1

    def test_up_to_down_announces_warning(self, settings: Settings):
        clock = FakeClock()
        states = iter([DAEMON_UP, DAEMON_DOWN])
        watcher = make_watcher(settings, probe=lambda: next(states), clock=clock)
        watcher.poll_once()  # baseline up
        produced = watcher.poll_once()
        assert [a["kind"] for a in produced] == ["warning"]
        assert produced[0]["text"] == DAEMON_DOWN_TEXT
        assert produced[0]["audio_url"] is None  # no tts_renderer injected
        assert watcher._hub.published[-1] is produced[0]

    def test_down_to_up_announces_recovery(self, settings: Settings):
        clock = FakeClock()
        states = iter([DAEMON_DOWN, DAEMON_UP])
        watcher = make_watcher(settings, probe=lambda: next(states), clock=clock)
        watcher.poll_once()  # baseline down (one boot warning)
        watcher._hub.published.clear()
        produced = watcher.poll_once()
        assert [a["kind"] for a in produced] == ["info"]
        assert produced[0]["text"] == DAEMON_UP_TEXT
        assert len(watcher._hub.published) == 1

    def test_while_down_reannounces_after_30min_not_before(self, settings: Settings):
        clock = FakeClock()
        watcher = make_watcher(settings, probe=lambda: DAEMON_DOWN, clock=clock)
        watcher.poll_once()  # boot warning
        clock.advance(29 * 60)
        assert watcher.poll_once() == []  # 29 min: still debounced
        clock.advance(2 * 60)  # 31 min since the warning
        produced = watcher.poll_once()
        assert [a["kind"] for a in produced] == ["warning"]
        assert len(watcher._hub.published) == 2

    def test_warning_audio_rendered_through_brain_tts(self, settings: Settings, tmp_path):
        clock = FakeClock()
        rendered: list = []

        def fake_renderer(cfg, text, out_path: Path):
            out_path.write_bytes(b"ID3-fake")
            rendered.append(text)
            return out_path

        cfg = Settings(**{**settings.__dict__, "audio_dir": str(tmp_path)})
        watcher = make_watcher(cfg, probe=lambda: DAEMON_DOWN, clock=clock,
                               tts_renderer=fake_renderer)
        produced = watcher.poll_once()
        assert rendered == [DAEMON_DOWN_TEXT]
        assert produced[0]["audio_url"].startswith("/audio/ann-")

    def test_render_failure_still_publishes_text_event(self, settings: Settings):
        clock = FakeClock()

        def boom(cfg, text, out_path):
            raise RuntimeError("engine down too")

        watcher = make_watcher(settings, probe=lambda: DAEMON_DOWN, clock=clock,
                               tts_renderer=boom)
        produced = watcher.poll_once()
        assert produced[0]["kind"] == "warning"
        assert produced[0]["text"] == DAEMON_DOWN_TEXT
        assert produced[0]["audio_url"] is None
        assert watcher._hub.published == produced  # text event still delivered
