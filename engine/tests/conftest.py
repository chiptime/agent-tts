"""Platform markers for the engine suite (TD-02).

The markers themselves are registered in ``pyproject.toml``
(``[tool.pytest.ini_options]``); this hook turns them into skips:

* ``needs_audio`` — skipped when ``CI`` is non-empty (runners have no
  device; GitHub Actions sets ``CI=true``);
* ``needs_powershell`` / ``windows_only`` — skipped off Windows.
"""

import os
import sys

import pytest

from agent_tts import constants, ipc, ownership


def pytest_runtest_setup(item):
    if "needs_audio" in item.keywords and os.environ.get("CI"):
        pytest.skip("requires a real audio playback device (not available in CI)")
    if "needs_powershell" in item.keywords and sys.platform != "win32":
        pytest.skip("requires powershell.exe (Windows only)")
    if "windows_only" in item.keywords and sys.platform != "win32":
        pytest.skip("Windows-only test")


@pytest.fixture(autouse=True)
def isolate_ipc_port_file(request, tmp_path, monkeypatch):
    """Isolates IPC_PORT_FILE into tmp_path so parallel/sequential tests never collide on Windows."""
    if "test_constants" in request.node.nodeid:
        yield  # that module asserts the real default marker
        return
    port_file = str(tmp_path / "agent-tts-ipc.port")
    monkeypatch.setenv("AGENT_TTS_PORT_FILE", port_file)
    monkeypatch.setattr(constants, "IPC_PORT_FILE", port_file)
    monkeypatch.setattr(ipc, "IPC_PORT_FILE", port_file)
    monkeypatch.setattr(ownership, "IPC_PORT_FILE", port_file)
    # Ownership is process-global state: a test that acquires the channel
    # must not decide the outcome of the next one.
    ownership.release_channel_ownership()
    yield
    ownership.release_channel_ownership()
