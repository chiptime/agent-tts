#!/usr/bin/env python3
"""herdr_reader.py — read-only karaoke follow-along renderer for CURRENT playback.

Attaches to the engine IPC channel (highlight + scroll-info) and renders a
live, word-highlighted frame in the alternate screen buffer. It NEVER starts
audio: playback ownership stays with the daemon. Exits cleanly on q/Esc/
Ctrl-C, or when playback ends (socket gone → repeated fetch failures).

The socket path binds at import time from AGENT_TTS_SOCKET (same defaults as
lib/tts_engine.py), so tests can point the renderer at a stub server.
"""

import os

# Engine locations must be in place BEFORE importing agent_tts: the socket
# path is captured at import time (agent_tts.constants reads the env once).
os.environ.setdefault("AGENT_TTS_LOCK_FILE", "/tmp/herdr-tts-playing.lock")
os.environ.setdefault("AGENT_TTS_PID_FILE", "/tmp/herdr-tts-current.pid")
os.environ.setdefault("AGENT_TTS_SOCKET", "/tmp/herdr-tts-player.sock")

import re
import select
import shutil
import signal
import sys
import termios
import time

from agent_tts import send_ipc_command

FRAME_INTERVAL = 0.125  # ~8 Hz render loop
SCROLL_EVERY = 4        # refresh the progress header every 4th frame
MAX_FAILED = 3          # consecutive failed highlight fetches → playback ended

ALT_ON = "\x1b[?1049h"
ALT_OFF = "\x1b[?1049l"
CLEAR = "\x1b[H\x1b[2J"
DIM = "\x1b[2m"
RESET = "\x1b[0m"
NO_PLAYBACK_MSG = "No playback in progress."
QUIT_KEYS = ("q", "Q", "\x1b")  # Ctrl-C arrives as SIGINT, not as stdin bytes

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def fetch(command):
    """One IPC round-trip; None = no reply / error / engine down."""
    try:
        reply = send_ipc_command(command)
    except Exception:
        return None
    # "Error" = local engine errors; "ERR:" = remote-playback IPC refusals
    # (e.g. an engine without the karaoke family). Both mean "no usable
    # body" — degrade to the no-playback path instead of rendering them.
    if not reply or reply.startswith(("Error", "ERR")):
        return None
    return reply


def parse_kv(reply):
    """Parse a `k=v k=v` reply line (values without spaces, like scroll-info)."""
    fields = {}
    for token in reply.split():
        key, sep, value = token.partition("=")
        if sep:
            fields[key] = value
    return fields


def visible_len(text):
    return len(ANSI_RE.sub("", text))


def wrap_tokens(line, width):
    """Word-wrap an SGR-colored line by VISIBLE width; escape sequences ride
    inside their token and are never split or counted."""
    width = max(width, 8)
    lines = []
    current = ""
    current_len = 0
    for token in line.split():
        token_len = visible_len(token)
        if current and current_len + 1 + token_len > width:
            lines.append(current)
            current = ""
            current_len = 0
        if current:
            current += " " + token
            current_len += 1 + token_len
        else:
            current = token
            current_len = token_len
    if current:
        lines.append(current)
    return lines


def header_line(scroll):
    """Progress header with -1-safe fallbacks when fields are missing."""
    pct = scroll.get("pct", "0.0")
    try:
        sent_idx = int(scroll.get("sent_idx", "-1"))
    except ValueError:
        sent_idx = -1
    try:
        total_sents = int(scroll.get("total_sents", "0"))
    except ValueError:
        total_sents = 0
    return (
        f"{DIM}\N{HEADPHONE} herdr reader \N{MIDDLE DOT} {pct}% "
        f"\N{MIDDLE DOT} sentence {sent_idx + 1}/{total_sents} "
        f"\N{MIDDLE DOT} q close{RESET}"
    )


def render(highlight, scroll):
    """One frame: clear, dim header, then the wrapped highlight line.
    An empty/failed highlight renders NO body that frame (never raw codes)."""
    out = [CLEAR, header_line(scroll)]
    if highlight and highlight.strip():
        columns = shutil.get_terminal_size().columns
        out.extend(wrap_tokens(highlight, columns))
    try:
        sys.stdout.write("\n".join(out) + "\n")
        sys.stdout.flush()
    except (BrokenPipeError, OSError):
        raise SystemExit(0)


def main():
    first = fetch("highlight")
    if first is None:
        # No playback: the alternate screen was never taken — nothing to
        # restore, one English line to stderr, exit 0.
        print(NO_PLAYBACK_MSG, file=sys.stderr)
        return 0

    interrupted = {"flag": False}

    def on_signal(_signum, _frame):
        interrupted["flag"] = True

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    sys.stdout.write(ALT_ON)
    sys.stdout.flush()

    saved_term = None
    if sys.stdin.isatty():
        try:
            saved_term = termios.tcgetattr(sys.stdin.fileno())
            raw = termios.tcgetattr(sys.stdin.fileno())
            # cbreak-style: single-char reads without echo; ISIG stays on so
            # Ctrl-C raises SIGINT, OPOST stays on so "\n" still moves the
            # cursor to the next line.
            raw[3] &= ~(termios.ECHO | termios.ICANON)
            raw[6][termios.VMIN] = 0
            raw[6][termios.VTIME] = 0
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, raw)
        except termios.error:
            saved_term = None

    try:
        failed = 0
        frame_no = 0
        scroll = {}
        stdin_open = True
        while True:
            highlight = fetch("highlight")
            if highlight is None:
                failed += 1
                if failed >= MAX_FAILED:
                    return 0  # playback ended → clean exit
            else:
                failed = 0
            if frame_no % SCROLL_EVERY == 0:
                info = fetch("scroll-info")
                if info is not None:
                    scroll = parse_kv(info)
            render(highlight, scroll)
            frame_no += 1

            if interrupted["flag"]:
                return 0
            # Frame clock + key poll in one select on stdin.
            ready = []
            if stdin_open:
                try:
                    ready, _, _ = select.select([sys.stdin], [], [], FRAME_INTERVAL)
                except (ValueError, OSError):
                    stdin_open = False
                    time.sleep(FRAME_INTERVAL)
            else:
                time.sleep(FRAME_INTERVAL)
            if ready:
                char = sys.stdin.read(1)
                if not char:  # EOF on a pipe: stop polling, keep rendering
                    stdin_open = False
                elif char in QUIT_KEYS:
                    return 0
    finally:
        if saved_term is not None:
            try:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, saved_term)
            except termios.error:
                pass
        try:
            sys.stdout.write(ALT_OFF)
            sys.stdout.flush()
        except (BrokenPipeError, OSError):
            pass


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)
    except BrokenPipeError:
        sys.exit(0)
