#!/usr/bin/env python3
"""RNF-AT-04-4 smoke harness: explicit-start daemon at rest over time.

Runs a REAL daemon subprocess (--serve semantics: no idle timeout, the
supervisor owns the lifetime) on an isolated channel, then samples for
--duration seconds:

- VmRSS of the daemon process (RAM drift),
- ping responsiveness (wedged/crashed detection),
- status=idle on every sample (the daemon surface stays coherent; no
  playback session is left mounted at rest — the session-leak dimension
  for plays is asserted per-play in tests/test_daemon.py).

Fails (exit 1) if RSS grows more than 5% between the first and last
sample, if any ping fails, or if the daemon exits early. On success it
prints a JSON report including the resting RSS (RNF-AT-04-2 evidence for
the "no local model loaded" bound).

The CI/pytest short mode runs a few seconds (tests/test_daemon_smoke.py);
the full 8 h run is a manual or nightly execution whose result must be
registered in the BLOQUE 1.2 notes (RNF-AT-04-4 DoD).

Usage:
    python scripts/daemon_smoke.py [--duration-sec 28800] [--interval-sec 60]
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

RAM_GROWTH_LIMIT_PCT = 5.0


def _vm_rss_kb(pid: int):
    """Reads VmRSS (kB) from /proc; None when unavailable (non-POSIX)."""
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except OSError:
        return None
    return None


def _ping(sock_path, timeout=1.0):
    from agent_tts.ipc import send_ipc_command

    return send_ipc_command("ping", socket_path=sock_path)


def _wait_healthy(sock_path, timeout_sec=15.0):
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        reply = _ping(sock_path)
        if reply and reply.startswith("pong"):
            return
        time.sleep(0.1)
    raise RuntimeError("daemon never became healthy")


def run_smoke(duration_sec: float, interval_sec: float) -> dict:
    from agent_tts.ipc import send_ipc_command

    tmp = tempfile.mkdtemp(prefix="agent-tts-smoke-")
    sock_path = os.path.join(tmp, "smoke.sock")
    env = dict(os.environ)
    # The daemon child must import agent_tts even when this script runs
    # from a fresh checkout without an installed package.
    src_dir = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = src_dir + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.update(
        {
            "AGENT_TTS_LOCK_FILE": os.path.join(tmp, "smoke.lock"),
            "AGENT_TTS_PID_FILE": os.path.join(tmp, "smoke.pid"),
            "AGENT_TTS_SOCKET": sock_path,
            "AGENT_TTS_DAEMON_LOG": os.path.join(tmp, "daemon.log"),
        }
    )
    # Explicit start: no idle timeout — the smoke owns the lifetime.
    proc = subprocess.Popen(
        [sys.executable, "-m", "agent_tts.daemon"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    samples = []
    failures = []
    try:
        _wait_healthy(sock_path)
        deadline = time.monotonic() + duration_sec
        while time.monotonic() < deadline:
            time.sleep(interval_sec)
            if proc.poll() is not None:
                failures.append(f"daemon exited early with {proc.returncode}")
                break
            reply = _ping(sock_path)
            rss_kb = _vm_rss_kb(proc.pid)
            if not (reply and reply.startswith("pong")):
                failures.append(f"ping failed at sample {len(samples)}: {reply!r}")
            status = send_ipc_command("status", socket_path=sock_path)
            if not (status and status.startswith("status=idle")):
                failures.append(f"status degraded at sample {len(samples)}: {status!r}")
            samples.append({"t": round(time.monotonic(), 2), "rss_kb": rss_kb, "ping": bool(reply)})
    finally:
        try:
            send_ipc_command("shutdown", socket_path=sock_path)
        except Exception:
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)

    report = {
        "label": "explicit-start daemon at rest (RNF-AT-04-4); play-leak dimension "
        "asserted per-play in tests/test_daemon.py",
        "duration_sec": duration_sec,
        "interval_sec": interval_sec,
        "samples": samples,
        "failures": failures,
        "daemon_pid": proc.pid,
    }
    rss_values = [s["rss_kb"] for s in samples if s["rss_kb"] is not None]
    if rss_values:
        first, last = rss_values[0], rss_values[-1]
        peak = max(rss_values)
        growth_pct = ((last - first) / first) * 100 if first else 0.0
        report["ram"] = {
            "first_kb": first,
            "last_kb": last,
            "peak_kb": peak,
            "growth_pct": round(growth_pct, 2),
            "growth_limit_pct": RAM_GROWTH_LIMIT_PCT,
            "growth_ok": growth_pct <= RAM_GROWTH_LIMIT_PCT,
            "resting_rss_mb": round(first / 1024.0, 1),
            "note": "no local model loaded (RNF-AT-04-2 bound: < 80 MB); "
            "kokoro-warm < 700 MB bound pending a real kokoro environment",
        }
    else:
        report["ram"] = {"note": "VmRSS unavailable (non-POSIX?)"}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-sec", type=float, default=8 * 3600)
    parser.add_argument("--interval-sec", type=float, default=60)
    args = parser.parse_args()

    report = run_smoke(args.duration_sec, args.interval_sec)
    ok = not report["failures"] and report.get("ram", {}).get("growth_ok", False)
    report["result"] = "ok" if ok else "failed"
    print(json.dumps(report, indent=2))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
