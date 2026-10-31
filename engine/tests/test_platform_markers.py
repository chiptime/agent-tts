"""Pins the TD-02 marker hook in ``tests/conftest.py``."""

from types import SimpleNamespace

import pytest

import conftest as hook

MESSAGES = {
    "needs_audio": "requires a real audio playback device (not available in CI)",
    "needs_powershell": "requires powershell.exe (Windows only)",
    "windows_only": "Windows-only test",
}


def _skip_reason(monkeypatch, marker, ci, platform):
    monkeypatch.delenv("CI", raising=False)
    if ci is not None:
        monkeypatch.setenv("CI", ci)
    monkeypatch.setattr(hook.sys, "platform", platform)
    try:
        hook.pytest_runtest_setup(SimpleNamespace(keywords={marker: True}))
    except pytest.skip.Exception as exc:
        return str(exc)
    return None


@pytest.mark.parametrize("ci, skipped", [(None, False), ("", False), ("true", True)])
def test_needs_audio_skips_only_under_a_non_empty_ci(monkeypatch, ci, skipped):
    reason = _skip_reason(monkeypatch, "needs_audio", ci, "linux")
    assert reason == (MESSAGES["needs_audio"] if skipped else None)


@pytest.mark.parametrize("marker", ["needs_powershell", "windows_only"])
@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_windows_markers_skip_off_windows(monkeypatch, marker, platform):
    reason = _skip_reason(monkeypatch, marker, None, platform)
    assert reason == (None if platform == "win32" else MESSAGES[marker])


def test_unmarked_tests_never_skip(monkeypatch):
    monkeypatch.setenv("CI", "true")
    hook.pytest_runtest_setup(SimpleNamespace(keywords={}))
