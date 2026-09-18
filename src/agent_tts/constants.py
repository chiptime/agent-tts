"""Constants and configuration defaults for agent-tts."""

import os

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

LOCK_FILE = os.environ.get("AGENT_TTS_LOCK_FILE", os.environ.get("HERDR_TTS_LOCK_FILE", "/tmp/herdr-tts-playing.lock"))
PID_FILE = os.environ.get("AGENT_TTS_PID_FILE", os.environ.get("HERDR_TTS_PID_FILE", "/tmp/herdr-tts-current.pid"))
IPC_SOCKET = os.environ.get("AGENT_TTS_SOCKET", os.environ.get("HERDR_TTS_SOCKET", "/tmp/herdr-tts-player.sock"))
