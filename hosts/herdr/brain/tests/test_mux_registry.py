"""Registry: env-driven adapter selection, settings input, reachability probe.

Safety: subprocess probes run through InlineRunner doubles; no test ever
executes a real multiplexer binary.
"""

from __future__ import annotations

import subprocess

import pytest

from herdr_brain.config import Settings
from herdr_brain.mux import (
    HerdrMuxAdapter,
    MuxAdapter,
    MuxProbe,
    TmuxAdapter,
    ZellijAdapter,
    create_mux,
    probe_mux,
)


class InlineRunner:
    """Records invocations and returns canned results (or raises)."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls: list = []

    def __call__(self, cmd, **kwargs):
        self.calls.append({"cmd": list(cmd), **kwargs})
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        rc, out, err = result
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr=err)


class TestSelection:
    def test_default_is_herdr(self, monkeypatch):
        monkeypatch.delenv("HERDR_BRAIN_MUX", raising=False)
        mux = create_mux({})
        assert isinstance(mux, HerdrMuxAdapter)
        assert mux._client._settings.herdr_bin == "herdr"

    def test_env_selects_tmux(self):
        mux = create_mux({"HERDR_BRAIN_MUX": "tmux"})
        assert isinstance(mux, TmuxAdapter)
        assert mux.bin_path == "tmux"

    def test_env_selects_zellij(self):
        mux = create_mux({"HERDR_BRAIN_MUX": "zellij"})
        assert isinstance(mux, ZellijAdapter)
        assert mux.bin_path == "zellij"

    def test_selection_is_case_and_whitespace_insensitive(self):
        assert isinstance(create_mux({"HERDR_BRAIN_MUX": "  TMUX "}), TmuxAdapter)

    def test_unknown_name_fails_loudly_with_the_valid_set(self):
        with pytest.raises(ValueError, match="HERDR_BRAIN_MUX") as excinfo:
            create_mux({"HERDR_BRAIN_MUX": "screen"})
        message = str(excinfo.value)
        assert "screen" in message
        for name in ("herdr", "tmux", "zellij"):
            assert name in message

    def test_binary_overrides_follow_the_herdr_bin_convention(self):
        tmux = create_mux({"HERDR_BRAIN_MUX": "tmux", "HERDR_TMUX_BIN": "/opt/tmux"})
        assert isinstance(tmux, TmuxAdapter)
        assert tmux.bin_path == "/opt/tmux"
        zellij = create_mux(
            {"HERDR_BRAIN_MUX": "zellij", "HERDR_ZELLIJ_BIN": "/opt/zellij"}
        )
        assert isinstance(zellij, ZellijAdapter)
        assert zellij.bin_path == "/opt/zellij"
        herdr = create_mux({"HERDR_BRAIN_MUX": "herdr", "HERDR_BIN": "/opt/herdr"})
        assert herdr._client._settings.herdr_bin == "/opt/herdr"


class TestSettingsInput:
    def test_settings_builds_the_herdr_adapter(self, settings):
        mux = create_mux(settings)
        assert isinstance(mux, HerdrMuxAdapter)
        assert mux._client._settings is settings

    def test_settings_feed_screen_lines_to_the_alternates(self, settings, monkeypatch):
        monkeypatch.setenv("HERDR_BRAIN_MUX", "tmux")
        mux = create_mux(settings)
        assert isinstance(mux, TmuxAdapter)
        assert mux._screen_lines == settings.screen_lines

    def test_runner_is_injected_for_tests(self, settings):
        runner = InlineRunner()
        mux = create_mux(settings, runner=runner)
        assert isinstance(mux, HerdrMuxAdapter)
        assert mux._client._run is runner

    def test_invalid_input_type_fails_loudly(self):
        with pytest.raises(TypeError, match="Settings"):
            create_mux(42)


class TestProbe:
    def test_every_selected_adapter_speaks_the_protocol(self):
        for name, expected in (
            ("herdr", HerdrMuxAdapter),
            ("tmux", TmuxAdapter),
            ("zellij", ZellijAdapter),
        ):
            mux = create_mux({"HERDR_BRAIN_MUX": name})
            assert isinstance(mux, expected)
            assert isinstance(mux, MuxAdapter)

    def test_probe_reports_tmux_version(self):
        runner = InlineRunner((0, "tmux 3.6b\n", ""))
        probe = probe_mux(TmuxAdapter(bin_path="tmux-test", runner=runner))
        assert probe == MuxProbe(reachable=True, detail="tmux CLI answered -V",
                                 version="3.6b")
        assert runner.calls[0]["cmd"] == ["tmux-test", "-V"]

    def test_probe_reports_zellij_version(self):
        runner = InlineRunner((0, "zellij 0.44.2\n", ""))
        probe = probe_mux(ZellijAdapter(bin_path="zellij-test", runner=runner))
        assert probe.reachable is True
        assert probe.version == "0.44.2"
        assert runner.calls[0]["cmd"] == ["zellij-test", "--version"]

    def test_probe_herdr_via_help(self, settings):
        runner = InlineRunner((0, "usage: herdr ...\n", ""))
        mux = HerdrMuxAdapter(_make_client(settings, runner))
        probe = probe_mux(mux)
        assert probe.reachable is True
        assert runner.calls[0]["cmd"] == [settings.herdr_bin, "--help"]

    def test_probe_missing_binary_is_not_reachable(self):
        runner = InlineRunner(FileNotFoundError("tmux-test"))
        probe = probe_mux(TmuxAdapter(bin_path="tmux-test", runner=runner))
        assert probe.reachable is False
        assert "tmux-test" in probe.detail

    def test_probe_nonzero_exit_is_not_reachable(self):
        runner = InlineRunner((1, "", "boom"))
        probe = probe_mux(TmuxAdapter(bin_path="tmux-test", runner=runner))
        assert probe.reachable is False

    def test_probe_never_enlarges_capability_claims(self):
        runner = InlineRunner((0, "tmux 3.6b\n", ""))
        mux = TmuxAdapter(bin_path="tmux-test", runner=runner)
        before = mux.capabilities()
        probe = probe_mux(mux)
        assert mux.capabilities() == before  # a probe adds facts, never cells
        assert not hasattr(probe, "supported")


def _make_client(settings: Settings, runner):
    from herdr_brain.herdr import HerdrClient

    return HerdrClient(settings, runner=runner)


def test_protocol_rejects_plain_objects():
    assert not isinstance(object(), MuxAdapter)
