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
