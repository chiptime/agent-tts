"""A system PATH for subprocess tests that hides machine-installed tools.

Tests that need coreutils/bash used to put ``/usr/bin:/bin`` on their
"hermetic" PATH. That leaks whatever the host has installed there: a
distro-packaged ``herdr`` makes every herdr-discovery fallback test pick
``/usr/bin/herdr``, and a real ``pactl`` lets the doctor talk to the user's
PulseAudio/PipeWire (which writes ``$XDG_CONFIG_HOME/pulse``). ``system_path`` builds
a symlink farm of the system bin directories minus the hidden names, so the
test environment is the same on every machine.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

SYSTEM_BIN_DIRS = ("/usr/bin", "/bin")
DEFAULT_HIDDEN = ("herdr", "brew", "pactl", "paplay", "pw-cli", "aplay")


def system_path(tmp_path: Path, hide: Iterable[str] = DEFAULT_HIDDEN) -> str:
    """Returns a PATH entry with the system tools except ``hide``."""
    hidden = set(hide)
    farm = tmp_path / "system-bin"
    if farm.is_dir():
        return str(farm)
    farm.mkdir(parents=True)
    for directory in SYSTEM_BIN_DIRS:
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        for name in names:
            if name in hidden or (farm / name).exists() or (farm / name).is_symlink():
                continue
            os.symlink(os.path.join(directory, name), farm / name)
    return str(farm)
