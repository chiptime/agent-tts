"""Playback target resolution and Windows host (winhost) connection settings.

Targets:
- "local": play on this machine via miniaudio (default; behavior unchanged).
- "winhost": stream PCM over TCP to ``agent-tts --winhost`` running on the
  Windows host (native WASAPI playback there).
- "wsl-ps": zero-install WSL mode; pipe WAV bytes to PowerShell on the
  Windows host.
"""

import os
import subprocess

VALID_TARGETS = ("local", "winhost", "wsl-ps")

ENV_PLAYBACK = "AGENT_TTS_PLAYBACK"
ENV_WINHOST_HOST = "AGENT_TTS_WINHOST_HOST"
ENV_WINHOST_PORT = "AGENT_TTS_WINHOST_PORT"
ENV_WINHOST_BIND = "AGENT_TTS_WINHOST_BIND"

DEFAULT_WINHOST_PORT = 7717
DEFAULT_WINHOST_BIND = "0.0.0.0"

# Bounded so a dead host cannot stall synthesis for long.
CONNECT_TIMEOUT_SEC = 0.5


class InvalidPlaybackTarget(ValueError):
    """Raised when a playback target value is not one of the known targets."""


def normalize_target(value: str) -> str:
    """Validates and normalizes a playback target value."""
    value = (value or "").strip().lower()
    if value not in VALID_TARGETS:
        raise InvalidPlaybackTarget(
            f"invalid playback target {value!r} (expected one of: {', '.join(VALID_TARGETS)})"
        )
    return value


def resolve_target(flag_value, env=None) -> str:
    """Resolves the active playback target: CLI flag wins, then AGENT_TTS_PLAYBACK, then "local"."""
    env = os.environ if env is None else env
    if flag_value:
        return normalize_target(flag_value)
    env_value = env.get(ENV_PLAYBACK, "")
    if env_value:
        return normalize_target(env_value)
    return "local"


def winhost_port(env=None) -> int:
    """TCP port of the winhost server (AGENT_TTS_WINHOST_PORT, default 7717)."""
    env = os.environ if env is None else env
    raw = env.get(ENV_WINHOST_PORT, "")
    if not raw:
        return DEFAULT_WINHOST_PORT
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"invalid {ENV_WINHOST_PORT}: {raw!r} (expected an integer)") from None


def winhost_bind_host(env=None) -> str:
    """Bind address for the winhost server (AGENT_TTS_WINHOST_BIND, default 0.0.0.0)."""
    env = os.environ if env is None else env
    return env.get(ENV_WINHOST_BIND) or DEFAULT_WINHOST_BIND


def explicit_winhost_host(env=None):
    """Explicit client-side host override (AGENT_TTS_WINHOST_HOST), or None."""
    env = os.environ if env is None else env
    return (env.get(ENV_WINHOST_HOST) or "").strip() or None


def gateway_from_ip_route(text: str) -> str:
    """Extracts the default gateway address from `ip route show default` output."""
    tokens = text.split()
    if "via" in tokens:
        idx = tokens.index("via")
        if idx + 1 < len(tokens):
            return tokens[idx + 1]
    return ""


def gateway_from_proc_route(text: str) -> str:
    """Extracts the default gateway address from /proc/net/route content."""
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) > 2 and parts[1] == "00000000":
            try:
                raw = bytes.fromhex(parts[2])
            except ValueError:
                continue
            return ".".join(str(byte) for byte in reversed(raw))
    return ""


def _proc_route_gateway() -> str:
    try:
        with open("/proc/net/route", "r", encoding="ascii") as f:
            return gateway_from_proc_route(f.read())
    except OSError:
        return ""


def default_route_gateway() -> str:
    """Best-effort WSL2 default gateway address (empty string when undetectable)."""
    try:
        result = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        gateway = gateway_from_ip_route(result.stdout or "")
        if gateway:
            return gateway
    except Exception:
        pass
    return _proc_route_gateway()


def candidate_hosts(env=None):
    """Ordered Windows host candidates for winhost connections.

    An explicit AGENT_TTS_WINHOST_HOST wins. Otherwise loopback is tried
    first (WSL2 mirrored networking mode), then the WSL2 default gateway
    (classic NAT networking mode).
    """
    env = os.environ if env is None else env
    explicit = explicit_winhost_host(env)
    if explicit:
        return [explicit]
    hosts = ["127.0.0.1"]
    gateway = default_route_gateway()
    if gateway and gateway not in hosts:
        hosts.append(gateway)
    return hosts
