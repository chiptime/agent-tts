"""Read-only local audio activity detection and opt-in ducking policy.

This module never changes audio-server configuration or playback volume.
Callers apply ``gain_linear(-duck_db())`` to their own PCM when ``decide``
returns ``duck``; playback integration and sweep scheduling live elsewhere.
"""

import math
import os
import re
import subprocess
import sys
import threading


_POLICIES = ("duck", "pause", "ignore")
_DEFAULT_DB = 12.0
_PACTL_TIMEOUT = 0.2
_warned = set()
_warning_lock = threading.Lock()
_PROPERTY = re.compile(r'([\w.-]+)\s*=\s*"((?:[^"\\]|\\.)*)"')


def _warn_once(key: str, message: str) -> None:
    """Suppress repeated sweep/config warnings for this process's lifetime."""
    with _warning_lock:
        if key not in _warned:
            _warned.add(key)
            print(f"Warning: {message}", file=sys.stderr)


def resolve_policy(target, env=None) -> str:
    """Resolve the exact upper-case target override, then global, then ignore.

    A present but invalid override does not fall back to a global active policy.
    Target spelling is preserved (e.g. ``wsl-ps`` uses the ``WSL-PS`` suffix).
    """
    env = os.environ if env is None else env
    override = f"AGENT_TTS_DUCK_POLICY_{target.upper()}"
    key = override if override in env else "AGENT_TTS_DUCK_POLICY"
    policy = env.get(key, "ignore")
    if policy not in _POLICIES:
        _warn_once(key, f"invalid {key}; audio ducking policy falls back to ignore")
        return "ignore"
    return policy


def gain_linear(db) -> float:
    """Convert signed decibels to linear gain without rounding or clamping."""
    return 10 ** (db / 20)


def duck_db(env=None) -> float:
    """Return nonnegative attenuation in dB; invalid values use the default."""
    env = os.environ if env is None else env
    try:
        db = float(env.get("AGENT_TTS_DUCK_DB", _DEFAULT_DB))
        if not math.isfinite(db) or db < 0:
            raise ValueError("attenuation must be finite and nonnegative")
        return db
    except (TypeError, ValueError):
        _warn_once("AGENT_TTS_DUCK_DB", "invalid AGENT_TTS_DUCK_DB; using 12 dB attenuation")
        return _DEFAULT_DB


def decide(state, policy) -> str:
    """Apply duck/pause only while external communication or capture is active."""
    if policy not in ("duck", "pause"):
        return "ignore"
    return policy if state.get("comm_active") or state.get("capture_active") else "ignore"


def _list_streams(entity: str, heading: str) -> list:
    """Parse C-locale pactl text; empty output means no streams, not failure."""
    result = subprocess.run(
        ["pactl", "list", entity],
        capture_output=True,
        text=True,
        check=True,
        timeout=_PACTL_TIMEOUT,
        env={**os.environ, "LC_ALL": "C"},
    )
    header = re.compile(rf"{re.escape(heading)} #[0-9]+")
    streams = []
    current = None
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        if header.fullmatch(line):
            current = {}
            streams.append(current)
        elif current is None or not raw[0].isspace():
            raise ValueError("unrecognized pactl stream output")
        elif line.startswith("Corked:"):
            value = line.partition(":")[2].strip()
            if value not in ("yes", "no") or "corked" in current:
                raise ValueError("invalid pactl stream activity")
            current["corked"] = value == "yes"
        elif re.match(r"[\w.-]+\s*=", line):
            prop = _PROPERTY.fullmatch(line)
            if prop is None:
                raise ValueError("invalid pactl stream property")
            key, value = prop.groups()
            if key in current:
                raise ValueError("duplicate pactl stream property")
            current[key] = value
        elif ":" not in line:
            raise ValueError("unrecognized pactl stream field")
    if any("corked" not in stream for stream in streams):
        raise ValueError("missing pactl stream activity")
    return streams


def _is_own_stream(stream: dict) -> bool:
    """Match our PID or engine identity, not unrelated Python applications."""
    if stream.get("application.process.id") == str(os.getpid()):
        return True
    identities = ("agent-tts", "agent_tts")
    name = stream.get("application.name", "").strip().lower()
    binary = os.path.basename(stream.get("application.process.binary", "")).lower()
    return name in identities or binary in identities


def detect_local() -> dict:
    """Snapshot uncorked external communication and capture on the default server.

    Source outputs represent actual capture streams, unlike merely available
    sources. Media playback alone is not communication. Any command/parse
    failure discards the entire snapshot, warns once, and never blocks playback
    beyond the bounded subprocess timeout. No server is selected explicitly.
    """
    inactive = {"comm_active": False, "capture_active": False}
    try:
        sinks = _list_streams("sink-inputs", "Sink Input")
        captures = _list_streams("source-outputs", "Source Output")
        return {
            "comm_active": any(
                not stream["corked"]
                and not _is_own_stream(stream)
                and stream.get("media.role") == "communication"
                for stream in sinks
            ),
            "capture_active": any(
                not stream["corked"] and not _is_own_stream(stream)
                for stream in captures
            ),
        }
    except (OSError, subprocess.SubprocessError, ValueError, UnicodeError):
        _warn_once("detect_local", "audio ducking detection unavailable; continuing without ducking")
        return inactive
