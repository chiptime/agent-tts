#!/usr/bin/env python3
"""
tts_engine.py — Bridge adapter between Herdr and the agent-tts standalone engine.
Delegates audio synthesis, miniaudio C playback, IPC controls, and text cleaning to agent_tts.
"""

import os
import sys

# Configure Herdr-specific socket and lock file locations for the agent-tts core
os.environ.setdefault("AGENT_TTS_LOCK_FILE", "/tmp/herdr-tts-playing.lock")
os.environ.setdefault("AGENT_TTS_PID_FILE", "/tmp/herdr-tts-current.pid")
os.environ.setdefault("AGENT_TTS_SOCKET", "/tmp/herdr-tts-player.sock")

try:
    from agent_tts import (
        AudioSession,
        EdgeTTSProvider,
        ElevenLabsTTSProvider,
        OpenAITTSProvider,
        TTSProvider,
        clean_agent_text,
        cleanup_locks,
        extract_last_turn,
        get_provider,
        main,
        play_mp3_data,
        play_mp3_file,
        send_ipc_command,
        speak,
        strip_ansi,
        synthesize,
        apply_bionic_reading,
        bionic_word,
    )

    # Backwards-compatible alias
    synthesize_and_play = speak
except ImportError as e:
    print(
        f"Error: Missing agent-tts or required dependencies ({e}). Run scripts/bootstrap.sh",
        file=sys.stderr,
    )
    sys.exit(1)

if __name__ == "__main__":
    main()
