"""Constants and configuration defaults for agent-tts."""

import os
import tempfile

DEFAULT_VOICE = "es-ES-ElviraNeural"
DEFAULT_RATE = "+20%"

# Voice mapping for common short names
VOICE_MAP = {
    "elvira": "es-ES-ElviraNeural",
    "alvaro": "es-ES-AlvaroNeural",
    "álvaro": "es-ES-AlvaroNeural",
    "ximena": "es-ES-XimenaNeural",
    "dalia": "es-MX-DaliaNeural",
    "jorge": "es-MX-JorgeNeural",
    "en": "en-US-JennyNeural",
}

# Transient runtime files live in the platform temp directory so the same
# defaults work on POSIX (/tmp) and Windows (TEMP/TEMP dir). Existing
# env-override semantics are preserved exactly.
LOCK_FILE = os.environ.get("AGENT_TTS_LOCK_FILE", os.path.join(tempfile.gettempdir(), "agent-tts-playing.lock"))
PID_FILE = os.environ.get("AGENT_TTS_PID_FILE", os.path.join(tempfile.gettempdir(), "agent-tts-current.pid"))
IPC_SOCKET = os.environ.get("AGENT_TTS_SOCKET", os.path.join(tempfile.gettempdir(), "agent-tts-player.sock"))
# Windows has no AF_UNIX sockets in the default flow: the IPC server binds an
# ephemeral TCP port on 127.0.0.1 and persists it to this marker file.
IPC_PORT_FILE = os.path.join(tempfile.gettempdir(), "agent-tts-ipc.port")

# --- Daemon (vía única, AT-04) -------------------------------------------------
# Client handshake gate (RF-AT-04-5): a ping that gets no answer within this
# budget means "no reachable daemon" and triggers the transparent auto-start.
PING_TIMEOUT_SEC = 0.2
# Default idle timeout, in seconds, for a daemon started implicitly by the
# client's auto-start (RF-AT-04-7): 30 minutes. Explicit starts
# (--serve/--foreground) default to no timeout. Both are configurable via
# AGENT_TTS_IDLE_TIMEOUT (seconds; 0 disables the idle exit).
ENV_IDLE_TIMEOUT = "AGENT_TTS_IDLE_TIMEOUT"
DEFAULT_AUTOSTART_IDLE_TIMEOUT_SEC = 1800.0
# How long the client waits for a spawned daemon to answer ping before
# declaring the auto-start failed (RNF-AT-04-3: clear, logged error).
DAEMON_START_TIMEOUT_SEC = 10.0
# stderr log of auto-started daemons: the place where a respawned daemon's
# diagnostics land when no terminal is attached (RNF-AT-04-3).
DAEMON_LOG_FILE = os.environ.get(
    "AGENT_TTS_DAEMON_LOG", os.path.join(tempfile.gettempdir(), "agent-tts-daemon.log")
)
