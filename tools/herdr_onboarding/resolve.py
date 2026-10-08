"""Portable runtime resolution for every herdr entry point (AT-11 design Decision 4).

One resolution order per concern, shared by the brain launcher, the plugin
launcher and the first-run wizard:

=============  ==============================================================
Concern        Resolution order
=============  ==============================================================
root           ``HERDR_PLUGIN_ROOT`` -> ascend from the resolved real path
               of the executing script (<= 6 levels, first directory
               carrying the monorepo marker ``hosts/herdr/tts-plugin``)
HERDR_TTS_HOME set-and-valid value wins; unset ->
               ``<brain_root>/../tts-plugin``; a hardcoded default is
               never returned or exported (OQ-2, Engram #9681)
HERDR_BIN      set value -> ``PATH`` -> ``$HOMEBREW_PREFIX/bin/herdr`` ->
               ``$(brew --prefix)/bin/herdr`` -> ``$HOME/.local/bin/herdr``
               -> bare ``herdr``
port           ``HERDR_BRAIN_PORT`` -> persisted config
               (``<config>/herdr-brain/config.env`` key
               ``HERDR_BRAIN_PORT``) -> default ``8741``
=============  ==============================================================

The bash launchers mirror the minimal pre-Python subset with an identical
``herdr_resolve_*`` block in ``bin/herdr-tts`` and ``bin/herdr-brain``. The
persisted config file is a sourcable ``KEY=VALUE`` file so bash and Python
read the same knob with their native mechanics. ``hosts/herdr/brain/src/
herdr_brain/config.py`` mirrors the port order locally: the installed brain
package cannot import this module without adding a runtime dependency,
which design Decision 1 forbids.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable, Dict, Optional, Union

ROOT_ASCEND_MAX = 6
_MONOREPO_MARKER = Path("hosts/herdr/tts-plugin")

Env = Optional[Dict[str, str]]


class ResolutionError(RuntimeError):
    """Raised when a portable resolution cannot produce a usable value."""


def _which_on_path(name: str, path_value: str) -> Optional[str]:
    for directory in path_value.split(os.pathsep):
        if not directory:
            continue
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _is_executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def resolve_root(env: Env = None, start: Optional[Union[str, Path]] = None) -> Path:
    """Monorepo/plugin root (design Decision 4).

    ``HERDR_PLUGIN_ROOT`` wins (it names whatever root answers this
    execution — the monorepo checkout in source layouts, the keg prefix on
    the Homebrew route). Otherwise ascend from ``start`` — by default the
    resolved real directory of this module — up to :data:`ROOT_ASCEND_MAX`
    levels, taking the first directory carrying the monorepo marker.
    """
    environ = os.environ if env is None else env
    override = environ.get("HERDR_PLUGIN_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    candidate = Path(start).resolve() if start is not None else Path(__file__).resolve().parent
    for _ in range(ROOT_ASCEND_MAX):
        if (candidate / _MONOREPO_MARKER).is_dir():
            return candidate
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    raise ResolutionError(
        "cannot resolve the installation root: no monorepo marker "
        f"({_MONOREPO_MARKER}) within {ROOT_ASCEND_MAX} levels of "
        f"{candidate}; set HERDR_PLUGIN_ROOT to the installation root"
    )


def resolve_tts_home(brain_root: Optional[Union[str, Path]] = None, env: Env = None) -> Path:
    """tts-plugin home (OQ-2 resolution, Engram #9681 — binding).

    An explicitly set AND usable ``HERDR_TTS_HOME`` always wins — even when
    a sibling ``tts-plugin`` directory exists (the literal RF-8 reading was
    rejected because it would silently break split deployments). Unset or
    unusable derives ``<brain_root>/../tts-plugin``; a hardcoded default is
    never returned. ``brain_root`` defaults to :func:`resolve_root` — the
    correct anchor when the executing entry point is the brain launcher.
    """
    environ = os.environ if env is None else env
    explicit = environ.get("HERDR_TTS_HOME", "").strip()
    explicit_path = Path(explicit).expanduser() if explicit else None
    if explicit_path is not None and explicit_path.is_dir():
        return explicit_path
    root = Path(brain_root).resolve() if brain_root is not None else resolve_root(env=environ)
    sibling = root.parent / "tts-plugin"
    if sibling.is_dir():
        return sibling
    shown = str(explicit_path) if explicit else "<unset>"
    raise ResolutionError(
        "cannot resolve the tts-plugin home: HERDR_TTS_HOME="
        f"{shown} is not a usable directory and {sibling} does not "
        "exist; set HERDR_TTS_HOME to the tts-plugin root"
    )


def resolve_herdr_bin(
    env: Env = None,
    runner: Callable[..., "subprocess.CompletedProcess[str]"] = subprocess.run,
) -> str:
    """herdr binary via the six-step discovery (design Decision 4).

    ``HERDR_BIN`` -> first ``herdr`` on ``PATH`` -> ``$HOMEBREW_PREFIX/bin/
    herdr`` -> ``$(brew --prefix)/bin/herdr`` (only when ``brew`` is on the
    environment's PATH, so hermetic/sandbox environments skip the probe) ->
    ``$HOME/.local/bin/herdr`` -> bare ``herdr``. ``runner`` exists for
    tests; production code never needs to pass it.
    """
    environ = os.environ if env is None else env
    explicit = environ.get("HERDR_BIN", "").strip()
    if explicit:
        return explicit
    on_path = _which_on_path("herdr", environ.get("PATH", ""))
    if on_path:
        return on_path
    prefix = environ.get("HOMEBREW_PREFIX", "").strip()
    if prefix:
        candidate = Path(prefix).expanduser() / "bin" / "herdr"
        if _is_executable(candidate):
            return str(candidate)
    brew = _which_on_path("brew", environ.get("PATH", ""))
    if brew is not None:
        completed = runner(["brew", "--prefix"], capture_output=True, text=True)
        if completed.returncode == 0:
            brew_prefix = completed.stdout.strip()
            if brew_prefix:
                candidate = Path(brew_prefix).expanduser() / "bin" / "herdr"
                if _is_executable(candidate):
                    return str(candidate)
    home = environ.get("HOME", "").strip()
    if home:
        candidate = Path(home).expanduser() / ".local" / "bin" / "herdr"
        if _is_executable(candidate):
            return str(candidate)
    return "herdr"

