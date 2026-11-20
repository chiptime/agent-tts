"""Adapter selection: one name in the environment, one adapter out.

``HERDR_BRAIN_MUX`` selects the multiplexer — ``herdr`` by default,
``tmux`` and ``zellij`` as the alternates — and may also hold a
comma-separated list (e.g. ``herdr,tmux,zellij``) whose result is a
:class:`~herdr_brain.mux.CompositeMux` routing pane ids by origin
prefix. A single name keeps today's behavior byte-for-byte: the same
class, no composite wrapper. Binary overrides follow the established
``HERDR_BIN`` convention: ``HERDR_TMUX_BIN`` / ``HERDR_ZELLIJ_BIN``,
honored per child. An unknown name fails loudly with the valid set and
a duplicate fails loudly too: a typo must never silently select a
different multiplexer.

``create_mux`` accepts a :class:`~herdr_brain.config.Settings` (the
herdr path keeps its settings: binary, screen and prompt budgets) or a
plain environment mapping (tests and embedding); ``None`` reads
``os.environ``.
"""

from __future__ import annotations

import os
import subprocess
from typing import Callable, Dict, List, Mapping, Optional, Union

from ..config import MAX_SCREEN_LINES, Settings, load_settings
from ..herdr import HerdrClient
from .adapters.herdr import HerdrMuxAdapter
from .adapters.tmux import TmuxAdapter
from .adapters.zellij import ZellijAdapter
from .capabilities import MuxProbe
from .composite import CompositeMux
from .protocol import MuxAdapter

#: Valid multiplexer names, in selection order.
MUX_NAMES = ("herdr", "tmux", "zellij")

#: Environment key that selects the multiplexer.
ENV_MUX = "HERDR_BRAIN_MUX"

Runner = Callable[..., subprocess.CompletedProcess]


def _parse_mux_names(raw: str) -> List[str]:
    """Splits ``HERDR_BRAIN_MUX`` into validated names (loud on anything odd)."""
    value = raw.strip()
    if not value:
        return ["herdr"]
    names: List[str] = []
    for part in value.split(","):
        name = part.strip().lower()
        if name not in MUX_NAMES:
            raise ValueError(
                f"unknown multiplexer {name!r} in {ENV_MUX}: "
                f"expected one of {', '.join(MUX_NAMES)}"
            )
        if name in names:
            raise ValueError(
                f"duplicate multiplexer {name!r} in {ENV_MUX}: "
                f"list each multiplexer once"
            )
        names.append(name)
    return names


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
    names = _parse_mux_names(str(env.get(ENV_MUX, "herdr")))
    if len(names) == 1 and names[0] == "herdr":
        client_settings = settings if settings is not None else load_settings(dict(env))
        return HerdrMuxAdapter(HerdrClient(client_settings, runner=runner))
    screen_lines = settings.screen_lines if settings is not None else MAX_SCREEN_LINES
    if len(names) == 1:
        if names[0] == "tmux":
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
    children: Dict[str, MuxAdapter] = {}
    for name in names:
        if name == "herdr":
            client_settings = (
                settings if settings is not None else load_settings(dict(env))
            )
            children[name] = HerdrMuxAdapter(HerdrClient(client_settings, runner=runner))
        elif name == "tmux":
            children[name] = TmuxAdapter(
                bin_path=str(env.get("HERDR_TMUX_BIN", "tmux")),
                runner=runner,
                screen_lines=screen_lines,
            )
        else:
            children[name] = ZellijAdapter(
                bin_path=str(env.get("HERDR_ZELLIJ_BIN", "zellij")),
                runner=runner,
                screen_lines=screen_lines,
            )
    return CompositeMux(children)


def probe_mux(adapter: MuxAdapter) -> MuxProbe:
    """Asks the adapter's multiplexer whether it answers at all.

    A probe adds reachability FACTS; it never claims capability cells.
    """
    return adapter.probe()
