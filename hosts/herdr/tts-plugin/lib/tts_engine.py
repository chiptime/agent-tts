#!/usr/bin/env python3
"""
tts_engine.py — Bridge adapter between Herdr and the agent-tts standalone engine.
Delegates audio synthesis, miniaudio C playback, IPC controls, and text cleaning to agent_tts.
"""

import sys

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
        play_mp3_data,
        play_mp3_file,
        send_ipc_command,
        speak,
        strip_ansi,
        synthesize,
    )
    from agent_tts.cli import main
    from agent_tts.constants import (
        DEFAULT_RATE,
        DEFAULT_VOICE,
        IPC_SOCKET,
        LOCK_FILE,
        PID_FILE,
        VOICE_MAP,
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
