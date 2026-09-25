#!/usr/bin/env python3
"""RNF-AT-04-1 real-provider campaign: warm-daemon event->audio-start p95 with edge.

The BLOQUE 1.2 registration measured the daemon-vs-spawn delta with a
stub provider; the real-edge leg was pending a network environment.
This harness closes that gap (folded into BLOQUE 1.3 T8 by decision D3):
N speak events through a WARM daemon started by the REAL auto-start
path (``ensure_daemon``), measuring event->audio-start p95 for a text
under 200 characters (RNF-AT-04-1 scope).

Fidelity labels (house style of scripts/queue_metrics.py): the edge
synthesis is REAL (network WebSocket to the Microsoft speech endpoint,
real MP3 bytes), the decode is the real miniaudio path, the daemon is a
real subprocess driven over the real framed IPC channel, and the queue
dispatch is the real QueueManager. Only the audio DEVICE is stubbed at
``AudioSession.play`` (no audio device exists in the measurement WSL
environment): the stub records the play-seam entry instant — the
audio-start point — on the daemon log, same seam and trace discipline
as the T5/T6 harnesses. "Event" is the client-side instant immediately
before the play IPC write (CLOCK_MONOTONIC, comparable across
processes on Linux).

Usage:
    PYTHONPATH=src .venv/bin/python scripts/edge_p95_metrics.py [--events 25]

Output: a summary on stdout plus a machine-readable JSON record
(default metrics/edge/edge-p95-local-<UTC>.json).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Same text as scripts/bench_daemon_vs_spawn.py (46 chars < 200,
# RNF-AT-04-1 scope) so the stub delta and the real leg compose.
TEXT = "Build finished successfully in twelve seconds."
WARMUP_EVENTS = 2
P95_BUDGET_MS = 250.0  # RNF-AT-04-1

_TRACE_RE = re.compile(r"agent-tts-queue: audio-start label='([^']+)' t=([0-9.]+)")

# Loaded by the daemon CHILD only (installed via PYTHONPATH precedence
# at interpreter startup): the device stub + audio-start trace seam.
_SITECUSTOMIZE = '''\
"""Installed by scripts/edge_p95_metrics.py into the daemon child only.

The measurement environment has no audio device, so AudioSession.play
is replaced by a seam that records the audio-start instant
(CLOCK_MONOTONIC) and returns immediately. Synthesis (real edge over
the network), the MP3 decode, the queue dispatch and the IPC transport
stay real.
"""
import sys
import time


def _traced_play(self, decoded):
    t0 = time.monotonic()
    print(
        f"agent-tts-queue: audio-start label={self.label!r} t={t0:.9f}",
        file=sys.stderr,
        flush=True,
    )
    self.state["status"] = "stopped"


try:
    from agent_tts.audio import AudioSession

    AudioSession.play = _traced_play
except Exception:
    pass
'''


def _percentile(values, pct):
    ordered = sorted(values)
    if not ordered:
        return float("nan")
    k = max(0, min(len(ordered) - 1, round(pct / 100.0 * (len(ordered) - 1))))
    return ordered[k]


def run_campaign(events: int) -> dict:
    run_dir = tempfile.mkdtemp(prefix="agent-tts-edge-p95-")
    sock = os.path.join(run_dir, "metrics.sock")
    daemon_log = os.path.join(run_dir, "daemon.log")
    site_dir = os.path.join(run_dir, "site")
    os.makedirs(site_dir)
    (Path(site_dir) / "sitecustomize.py").write_text(_SITECUSTOMIZE, encoding="utf-8")

    # Channel isolation + child patching, BEFORE the first agent_tts
    # import in THIS process: the auto-started daemon child inherits
    # this environment (lock/pid/socket/log overrides + the site dir at
    # the head of PYTHONPATH), so the real channel is never touched and
    # only the child gets the device stub.
    os.environ["AGENT_TTS_LOCK_FILE"] = os.path.join(run_dir, "channel.lock")
    os.environ["AGENT_TTS_PID_FILE"] = os.path.join(run_dir, "channel.pid")
    os.environ["AGENT_TTS_SOCKET"] = sock
    os.environ["AGENT_TTS_DAEMON_LOG"] = daemon_log
    os.environ["PYTHONPATH"] = site_dir + os.pathsep + str(REPO / "src")

    from agent_tts.daemon import ensure_daemon, send_play
    from agent_tts.ipc import send_ipc_command

    # Warm daemon through the REAL auto-start path (RF-AT-04-5).
    pong = ensure_daemon()

    def play(label: str) -> str:
        return send_play({"text": TEXT, "label": label, "provider": "edge"})

    failures = []
    for i in range(WARMUP_EVENTS):
        reply = play(f"warmup-{i}")
        if reply != "status=done":
            failures.append(f"warmup-{i}: unexpected reply {reply!r}")

    sends = {}
    for i in range(events):
        label = f"evt-{i}"
        sends[label] = time.monotonic()  # the event instant: right before the IPC write
        reply = play(label)
        if reply != "status=done":
            failures.append(f"{label}: unexpected reply {reply!r}")

    try:
        send_ipc_command("shutdown", socket_path=sock)
    except Exception:
        pass

    starts = {}
    try:
        with open(daemon_log, errors="ignore") as fh:
            for line in fh:
                m = _TRACE_RE.search(line)
                if m:
                    starts[m.group(1)] = float(m.group(2))
    except OSError as e:
        failures.append(f"cannot read daemon log: {e}")

    latencies_ms = []
    missing = []
    for label, t_send in sends.items():
        t_start = starts.get(label)
        if t_start is None:
            missing.append(label)
            continue
        latencies_ms.append((t_start - t_send) * 1000.0)

    return {
        "run_dir": run_dir,
        "pong": pong,
        "failures": failures,
        "missing_labels": missing,
        "latencies_ms": latencies_ms,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=25)
    args = parser.parse_args()

    run = run_campaign(args.events)
    lat = run["latencies_ms"]

    report = {
        "label": "RNF-AT-04-1 real edge: warm-daemon event->audio-start p95 "
        "(auto-started daemon, real network synthesis, device stubbed at the "
        "AudioSession.play seam)",
        "metric": "event->audio-start",
        "threshold_ms": P95_BUDGET_MS,
        "text_chars": len(TEXT),
        "events": args.events,
        "warmup_events": WARMUP_EVENTS,
        "provider": "edge (real network synthesis)",
        "fidelity": "real edge WebSocket synthesis + real miniaudio decode + real "
        "framed IPC + real queue dispatch; the audio DEVICE is stubbed at the "
        "play seam (no device in the measurement WSL environment) — audio-start "
        "is the play-seam entry instant, same seam as metrics/queue",
        "failures": run["failures"],
        "missing_labels": run["missing_labels"],
        "environment": {
            "kernel": platform.release(),
            "python": platform.python_version(),
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        },
        "run_dir": run["run_dir"],
        "pong": run["pong"],
    }

    if lat:
        report["summary"] = {
            "p50_ms": round(_percentile(lat, 50), 1),
            "p95_ms": round(_percentile(lat, 95), 1),
            "max_ms": round(max(lat), 1),
            "mean_ms": round(statistics.mean(lat), 1),
        }
        report["threshold_ok"] = report["summary"]["p95_ms"] < P95_BUDGET_MS
        report["samples_ms"] = [round(v, 1) for v in lat]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path("metrics/edge")
    out.mkdir(parents=True, exist_ok=True)
    record = out / f"edge-p95-local-{stamp}.json"
    record.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(report, indent=2))
    print(f"\nrecord: {record}")
    ok = not run["failures"] and not run["missing_labels"] and lat and report["threshold_ok"]
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
