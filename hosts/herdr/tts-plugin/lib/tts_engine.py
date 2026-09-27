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


def _render_html_main(argv):
    """HT-15 transient --render-html entry: exits 0/1/2/3, never main().

    Runs BEFORE the agent_tts import below so an engine-down venv maps to
    exit 3 (design exit-code contract) instead of the module-level exit 1.
    Exit codes: 0 success, 1 usage, 2 input unreadable / redaction failure
    (fail closed, no file written), 3 agent_tts unavailable.
    """
    args = argv[2:]  # argv[1] == "--render-html"
    in_path = None
    out_path = None
    map_path = None
    i = 0
    while i < len(args):
        if args[i] == "--map" and i + 1 < len(args):
            map_path = args[i + 1]
            i += 2
            continue
        if in_path is None:
            in_path = args[i]
        elif out_path is None:
            out_path = args[i]
        i += 1
    if not in_path or not out_path:
        print(
            "usage: tts_engine --render-html <in> <out.html> [--map <map.json>]",
            file=sys.stderr,
        )
        return 1
    try:
        with open(in_path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        print(f"render-html: cannot read input: {exc}", file=sys.stderr)
        return 2
    try:
        import reader_pipeline  # same directory; engine import surfaces here
    except Exception as exc:  # engine down → exit 3, no partial HTML
        print(f"render-html: agent_tts unavailable: {exc}", file=sys.stderr)
        return 3
    # The CLI consumes a FILE whose content IS the whole document: scrollback
    # turn extraction (extract_last_turn) must never run here — the heuristic
    # eats leading non-turn lines (e.g. a top `# heading`). The pane-reading
    # caller keeps render()'s default (pre_extracted=False); extraction is
    # that caller's purpose, not this one's.
    return reader_pipeline.render_to_files(raw, out_path, map_path, pre_extracted=True)


if len(sys.argv) > 1 and sys.argv[1] == "--render-html":
    sys.exit(_render_html_main(sys.argv))

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

    # HT-15 reader pipeline re-export for host consumers
    # (from tts_engine import reader_pipeline). Guarded so contexts without
    # lib/ on sys.path never break the engine bridge itself.
    try:
        import reader_pipeline
    except ImportError:
        reader_pipeline = None
except ImportError as e:
    print(
        f"Error: Missing agent-tts or required dependencies ({e}). Run scripts/bootstrap.sh",
        file=sys.stderr,
    )
    sys.exit(1)

if __name__ == "__main__":
    main()
