#!/usr/bin/env python3
"""RNF-AT-04-1 measurement: warm-daemon event-to-audio vs CLI spawn.

STUB-BASED, clearly labeled: the synthesis provider and the audio device
are faked (no network edge call, no miniaudio playback device). What this
does measure faithfully is the cost the daemon eliminates — the per-event
interpreter + import spawn — plus the real IPC transport round trip and
dispatch, with the same playback start point on both paths.

True edge/kokoro numbers must be recorded on a machine with the real
provider and an audio device; register them in the BLOQUE 1.2 notes.

Usage:
    PYTHONPATH=src python scripts/bench_daemon_vs_spawn.py [--events 30]
"""

import argparse
import array
import json
import os
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

# Allow running from a fresh checkout without installing.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

TEXT = "Build finished successfully in twelve seconds."  # < 200 chars (RNF-AT-04-1 scope)


class StubEngine:
    """Provider stand-in: instant synthesis, no network, no model."""

    async def synthesize(self, text, voice=None, rate=None, volume=None, pitch=None, stop_checker=None):
        return b"stub-audio"


class FakeDecoded:
    sample_rate = 24000
    nchannels = 1
    duration = 0.01
    samples = array.array("h", [0] * 240)


def _percentile(values, pct):
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(pct / 100.0 * (len(ordered) - 1))))
    return ordered[k]


def bench_warm_daemon(events):
    """Event-to-audio through a warm in-process daemon (stub synthesis/playback)."""
    import agent_tts.cli as cli_mod
    from agent_tts.audio import AudioSession
    from agent_tts.daemon import Daemon, ProviderCache
    from agent_tts.ipc import send_ipc_command
    from agent_tts import audio as audio_mod

    tmp = tempfile.mkdtemp(prefix="agent-tts-bench-")
    sock_path = os.path.join(tmp, "bench.sock")

    audio_mod.LOCK_FILE = os.path.join(tmp, "bench.lock")
    audio_mod.PID_FILE = os.path.join(tmp, "bench.pid")
    audio_mod.IPC_SOCKET = sock_path
    cli_mod.miniaudio = type("M", (), {"decode": staticmethod(lambda data: FakeDecoded())})

    play_starts = []
    start_lock = threading.Lock()

    def bench_play(self, decoded):
        with start_lock:
            play_starts.append(time.monotonic())
        self.state["status"] = "stopped"

    AudioSession.play = bench_play

    daemon = Daemon(socket_path=sock_path, provider_cache=ProviderCache(factory=lambda **kw: StubEngine()))
    thread = threading.Thread(target=daemon.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if send_ipc_command("ping", socket_path=sock_path):
            break
        time.sleep(0.02)

    from agent_tts.daemon import send_play

    latencies = []
    for i in range(events):
        sent_at = time.monotonic()
        reply = send_play({"text": TEXT, "provider": "edge"}, socket_path=sock_path)
        elapsed = time.monotonic() - sent_at
        if reply != "status=done":
            raise RuntimeError(f"unexpected reply: {reply}")
        # The fake play returned immediately, so the playback start IS the
        # whole exchange; the recorded timestamp is the audio-start point.
        latencies.append(elapsed)

    daemon.request_shutdown()
    thread.join(timeout=5)
    return latencies


# The spawn path mirrors the pre-daemon per-event CLI: one Python process
# per notification that imports the engine and reaches "audio start"
# (stub synthesis + decode). The parent measures wall time around the
# process, like a host spawning agent-tts per event.
_SPAWN_CHILD = (
    "import sys, time\n"
    "sys.path.insert(0, %r)\n"
    "import agent_tts.cli as cli\n"
    "import asyncio\n"
    "from agent_tts.daemon import ProviderCache\n"
    "class StubEngine:\n"
    "    async def synthesize(self, text, voice=None, **kw):\n"
    "        return b'stub-audio'\n"
    "engine = StubEngine()\n"
    "mp3 = asyncio.run(engine.synthesize('x'))\n"
    "decoded = type('D', (), {'samples': b'\\x00' * 480, 'sample_rate': 24000, 'nchannels': 1})()\n"
    "print(time.time(), flush=True)\n"  # audio start
)


def bench_cli_spawn(events):
    src = str(Path(__file__).resolve().parents[1] / "src")
    latencies = []
    for i in range(events):
        # Warm the filesystem cache once so imports are not first-run cold.
        if i == 0:
            subprocess.run(
                [sys.executable, "-c", _SPAWN_CHILD % src],
                capture_output=True,
                timeout=60,
            )
        sent_at = time.time()
        proc = subprocess.run(
            [sys.executable, "-c", _SPAWN_CHILD % src],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"spawn child failed: {proc.stderr}")
        audio_start = float(proc.stdout.strip())
        latencies.append(audio_start - sent_at)
    return latencies


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=30)
    args = parser.parse_args()

    daemon_lat = bench_warm_daemon(args.events)
    spawn_lat = bench_cli_spawn(args.events)

    def summary(name, values):
        return {
            "p50_ms": round(_percentile(values, 50) * 1000, 1),
            "p95_ms": round(_percentile(values, 95) * 1000, 1),
            "max_ms": round(max(values) * 1000, 1),
            "mean_ms": round(statistics.mean(values) * 1000, 1),
        }

    d_p95 = _percentile(daemon_lat, 95)
    s_p95 = _percentile(spawn_lat, 95)
    reduction = (1 - d_p95 / s_p95) * 100 if s_p95 else 0.0

    report = {
        "label": "STUB measurement: no network provider, no audio device; the delta is the "
        "per-event interpreter+import spawn cost plus real IPC round trip (RNF-AT-04-1)",
        "text_chars": len(TEXT),
        "events": args.events,
        "warm_daemon_event_to_audio": summary("daemon", daemon_lat),
        "cli_spawn_event_to_audio": summary("spawn", spawn_lat),
        "p95_reduction_pct": round(reduction, 1),
        "success_metric_ge_40pct": reduction >= 40.0,
        "pending": "true edge/kokoro measurement on a machine with provider + audio device",
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
