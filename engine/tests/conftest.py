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


def pytest_runtest_setup(item):
    if "needs_audio" in item.keywords and os.environ.get("CI"):
        pytest.skip("requires a real audio playback device (not available in CI)")
    if "needs_powershell" in item.keywords and sys.platform != "win32":
        pytest.skip("requires powershell.exe (Windows only)")
    if "windows_only" in item.keywords and sys.platform != "win32":
        pytest.skip("Windows-only test")
