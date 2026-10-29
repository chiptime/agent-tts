"""Adapter selection: one name in the environment, one adapter out.

``HERDR_BRAIN_MUX`` selects the multiplexer — ``herdr`` by default,
``tmux`` and ``zellij`` as the alternates — and binary overrides follow
the established ``HERDR_BIN`` convention: ``HERDR_TMUX_BIN`` /
``HERDR_ZELLIJ_BIN``. An unknown name fails loudly with the valid set:
a typo must never silently select a different multiplexer.

``create_mux`` accepts a :class:`~herdr_brain.config.Settings` (the
herdr path keeps its settings: binary, screen and prompt budgets) or a
plain environment mapping (tests and embedding); ``None`` reads
``os.environ``.
"""

from __future__ import annotations

import os
import subprocess
from typing import Callable, Mapping, Optional, Union

from ..config import MAX_SCREEN_LINES, Settings, load_settings
from ..herdr import HerdrClient
from .adapters.herdr import HerdrMuxAdapter
from .adapters.tmux import TmuxAdapter
from .adapters.zellij import ZellijAdapter
from .capabilities import MuxProbe
from .protocol import MuxAdapter

#: Valid multiplexer names, in selection order.
MUX_NAMES = ("herdr", "tmux", "zellij")

#: Environment key that selects the multiplexer.
ENV_MUX = "HERDR_BRAIN_MUX"

Runner = Callable[..., subprocess.CompletedProcess]


def create_mux(
    settings_or_env: Union[Settings, Mapping, None] = None,
    runner: Optional[Runner] = None,
) -> MuxAdapter:
    """Builds the adapter named by ``HERDR_BRAIN_MUX`` (default ``herdr``)."""
    if isinstance(settings_or_env, Settings):
        settings: Optional[Settings] = settings_or_env
        env: Mapping[str, str] = os.environ
    elif isinstance(settings_or_env, Mapping):
        settings = None
        env = settings_or_env
    elif settings_or_env is None:
        settings, env = None, os.environ
    else:
        raise TypeError(
            "create_mux expects Settings, an environment mapping or None, "
            f"got {type(settings_or_env).__name__}"
        )
    name = str(env.get(ENV_MUX, "herdr")).strip().lower() or "herdr"
    if name not in MUX_NAMES:
        raise ValueError(
            f"unknown multiplexer {name!r} in {ENV_MUX}: "
            f"expected one of {', '.join(MUX_NAMES)}"
        )
    if name == "herdr":
        client_settings = settings if settings is not None else load_settings(dict(env))
        return HerdrMuxAdapter(HerdrClient(client_settings, runner=runner))
    screen_lines = settings.screen_lines if settings is not None else MAX_SCREEN_LINES
    if name == "tmux":
        return TmuxAdapter(
            bin_path=str(env.get("HERDR_TMUX_BIN", "tmux")),
            runner=runner,
            screen_lines=screen_lines,
        )
    return ZellijAdapter(
        bin_path=str(env.get("HERDR_ZELLIJ_BIN", "zellij")),
        runner=runner,
        screen_lines=screen_lines,
    )


def probe_mux(adapter: MuxAdapter) -> MuxProbe:
    """Asks the adapter's multiplexer whether it answers at all.

    A probe adds reachability FACTS; it never claims capability cells.
    """
    return adapter.probe()
