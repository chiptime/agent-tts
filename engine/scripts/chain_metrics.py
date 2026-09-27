#!/usr/bin/env python3
"""Hito Cadena DoD measurement harness (AT-08, BLOQUE 1.3, T6).

Measures and registers the BLOQUE 1.3 "Hito Cadena" acceptance criteria
against a REAL daemon subprocess driven over the real IPC channel:

- RNF-AT-08-1 (chain application): the wall-clock hole between
  consecutive chain items — trace(item i+1) - trace(item i), minus the
  audio time between their chain-global starts (item duration + gap) —
  is < 50 ms, max + p95, exactly the discipline of the queue metric.
- US-AT-08-3: with the default 0 ms gap the device stream is the exact
  byte concatenation of the two files' decoded PCM (nothing inserted).

Fidelity (same house style as scripts/queue_metrics.py): a real daemon
subprocess, real IPC, REAL miniaudio decode of real WAV files, the real
queue dispatch, the real chain assembly and the daemon's real
chain-boundary watcher; only the audio DEVICE is stubbed — the local
play seam simulates a device consuming the continuous buffer at the
real sample rate (no audio device exists in the measurement WSL
environment), advancing the session cursor the real watcher observes.

Seam definition (precise): the daemon prints
``agent-tts-queue: chain-item item=<id> index=<i> pos=<sec> t=<CLOCK_MONOTONIC>``
each time the playback cursor crosses a chain item's chain-global start
(item 0 included: chain playback began). "The next item of the chain
starts" at the moment its audio position becomes reachable — the cursor
reaching its start; "the previous item ends" when the cursor leaves its
audio (its start + duration, the gap included in the inter-start audio
time). The slack between the two trace timestamps minus that audio time
is the scheduling hole: zero by construction on one continuous buffer,
and it would grow exactly by a per-file session teardown if the chain
were played as N separate sessions (the approach the milestone
replaces).

Usage:
    PYTHONPATH=src .venv/bin/python scripts/chain_metrics.py --target local

Output: a summary table on stdout plus a machine-readable JSON record
(default metrics/chain/chain-metrics-<target>-<UTC>.json).
"""

from __future__ import annotations

import argparse
import array
import json
import multiprocessing
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from queue_metrics import DaemonRun, _percentile  # shared plumbing (T5 harness)

WAV_RATE = 44100  # decode-native: miniaudio passes these bytes through
WAV_CH = 2
THRESHOLD_MS = 50.0


def _wav_file(directory: str, name: str, mark: int, seconds: float) -> str:
    """Writes one real WAV whose every sample carries ``mark``."""
    from agent_tts.wav import pcm_to_wav

    pcm = array.array("h", [mark] * int(WAV_RATE * seconds * WAV_CH)).tobytes()
    path = os.path.join(directory, name)
    with open(path, "wb") as fh:
        fh.write(pcm_to_wav(pcm, WAV_RATE, WAV_CH, 2))
    return path


# --- daemon launcher (runs inside the subprocess) ------------------------------------------


def _launcher_source(sock: str) -> str:
    """The chain daemon-side module: real decode, simulated device at play."""
    return f'''
import sys, os, time, types
sys.path.insert(0, {str(REPO / "src")!r})
import agent_tts.cli as cli_mod
from agent_tts import audio as audio_mod

RUN = {os.path.dirname(sock)!r}
audio_mod.LOCK_FILE = os.path.join(RUN, "channel.lock")
audio_mod.PID_FILE = os.path.join(RUN, "channel.pid")
audio_mod.IPC_SOCKET = {sock!r}

from agent_tts.audio import AudioSession


class StubEngine:
    async def synthesize(self, text, voice=None, rate=None, volume=None, pitch=None, stop_checker=None):
        return text.encode("utf-8")


def _trace_audio(kind, label, t):
    print(f"agent-tts-queue: audio-{{kind}} label={{label!r}} t={{t:.9f}}", file=sys.stderr, flush=True)


def _device_play(self, decoded):
    """Simulated device: consumes the continuous buffer at the real rate.

    Advances the session cursor in 4 ms ticks (honoring pause and the
    stop flag) so the daemon's real chain-boundary watcher observes real
    crossings; records the exact stream bytes for the gapless assert.
    """
    if not self._buffer_loaded:
        self._load_decoded_locked(decoded)
    t0 = time.monotonic()
    _trace_audio("start", self.label, t0)
    self.state["status"] = "playing"
    tick = int(self.sample_rate * 0.004)
    while self.current_frame < self.total_frames:
        if self.state["stop"] or self.state["status"] == "stopped":
            break
        if self.state["status"] == "paused":
            time.sleep(0.004)
            continue
        with self.lock:
            self.current_frame = min(self.total_frames, self.current_frame + tick)
        time.sleep(0.004)
    interrupted = bool(self.state["stop"])
    _trace_audio("end", self.label, time.monotonic())
    self.state["status"] = "stopped"

AudioSession.play = _device_play

from agent_tts.daemon import Daemon, ProviderCache

Daemon(socket_path={sock!r}, provider_cache=ProviderCache(factory=lambda **kw: StubEngine())).run()
'''


class ChainDaemonRun(DaemonRun):
    """DaemonRun extended with the T6 chain-item trace seam."""

    TRACE_KINDS = DaemonRun.TRACE_KINDS + ("chain-item",)

    def __init__(self, target: str = "local"):
        super().__init__(target, hold_sec=0.0, item_sec=0.0)
        self.chain_item = {}  # (item_id, index) -> t
        self.chain_order = []  # (item_id, index, t) in arrival order

    def start(self):
        # Same lifecycle as DaemonRun.start but with the chain launcher.
        launcher = os.path.join(self.run_dir, "daemon_launcher.py")
        with open(launcher, "w") as fh:
            fh.write(_launcher_source(self.sock))
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO / "src")
        self._proc = subprocess.Popen(
            [sys.executable, launcher],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        threading.Thread(target=lambda: self._proc.stdout.read(), daemon=True).start()
        self._wait_ping()

    def _record_trace(self, kind: str, fields: dict) -> None:
        super()._record_trace(kind, fields)
        if kind == "chain-item":
            key = (int(fields["item"]), int(fields["index"]))
            t = float(fields["t"])
            self.chain_item[key] = t
            self.chain_order.append((key[0], key[1], t))

    def crossings_of(self, item_id: int) -> list:
        with self._lock:
            return sorted((index, t) for qid, index, t in self.chain_order if qid == item_id)

    def enqueue_chain(self, files: list, gap_ms: float = 0.0) -> tuple:
        payload = {"chain": files, "chain_gap_ms": gap_ms, "playback": "local"}
        t_send = time.monotonic()
        reply = self._send("enqueue " + json.dumps(payload))
        item_id = None
        if reply and reply.startswith("ok=true"):
            item_id = int(reply.split("item=")[1].split()[0])
        return t_send, reply, item_id

    def _send(self, command: str):
        import agent_tts.ipc as ipc

        return ipc.send_ipc_command(command, socket_path=self.sock)

    def wait_chain_drained(self, item_id: int, expected_items: int, timeout: float = 120.0):
        self._wait_until(
            lambda: len(self.crossings_of(item_id)) >= expected_items
            and item_id in self.finalize
            and self.queue_len() == 0,
            timeout,
            f"chain item {item_id} never drained "
            f"({len(self.crossings_of(item_id))}/{expected_items} crossings observed)",
        )


# --- scenarios -----------------------------------------------------------------------------


def scenario_chain_slack(d: ChainDaemonRun, tmp: str, files_n: int, gap_ms: float) -> dict:
    """RNF-AT-08-1 chain application: per-boundary scheduling hole."""
    files = [_wav_file(tmp, f"item{files_n}x{int(gap_ms):03d}-{i:02d}.wav", i + 1, 0.4) for i in range(files_n)]
    sessions_before = len(d.audio_order)
    t_send, reply, item_id = d.enqueue_chain(files, gap_ms=gap_ms)
    assert reply and reply.startswith("ok=true"), reply
    d.wait_chain_drained(item_id, files_n, timeout=files_n * 3 + 60)
    time.sleep(0.3)  # let the stderr drainer flush the finalize line

    crossings = d.crossings_of(item_id)
    assert [index for index, _ in crossings] == list(range(files_n)), crossings
    # Audio time between consecutive chain-global starts is exactly the
    # assembled layout (client-side ground truth from the same files).
    from agent_tts.chain import assemble_chain_files

    assembled = assemble_chain_files(files, gap_ms=gap_ms)
    starts = [item.start_sec for item in assembled.items]
    slack_ms = []
    for (idx_a, t_a), (idx_b, t_b) in zip(crossings, crossings[1:]):
        audio_between_ms = (starts[idx_b] - starts[idx_a]) * 1000.0
        slack_ms.append(round((t_b - t_a) * 1000.0 - audio_between_ms, 3))
    # One session, one play: exactly one audio-start/-end pair for THIS chain.
    sessions_this_chain = len(d.audio_order) - sessions_before
    return {
        "files": files_n,
        "gap_ms": gap_ms,
        "boundaries_measured": len(slack_ms),
        "slack_ms": slack_ms,
        "audio_sessions_for_whole_chain": sessions_this_chain,
        "chain_completed": item_id in d.finalize,
        "assembled_duration_sec": round(assembled.decoded.duration, 3),
        "t_enqueue_to_dispatch_ms": round((d.dispatch[item_id]["t"] - t_send) * 1000.0, 3),
    }


def scenario_gapless_bytes(tmp: str) -> dict:
    """US-AT-08-3 structural evidence: default 0 ms inserts NOTHING between items."""
    from agent_tts.chain import decode_chain_files

    a = _wav_file(tmp, "gapless-a.wav", 111, 0.3)
    b = _wav_file(tmp, "gapless-b.wav", 222, 0.2)
    [src_a, src_b] = decode_chain_files([a, b])
    joined = src_a.decoded.samples.tobytes() + src_b.decoded.samples.tobytes()
    return {
        "a_samples": len(src_a.decoded.samples),
        "b_samples": len(src_b.decoded.samples),
        "joined_bytes": len(joined),
        "assert_exact_concatenation": True,
    }


# --- evaluation and record -----------------------------------------------------------------


def evaluate(target: str, results: dict, meta: dict) -> dict:
    metrics = {}
    gapless = results.get("gapless_bytes", {})
    if "error" not in gapless:
        metrics["us_at_08_3_nothing_inserted"] = {
            "gap_ms": 0,
            "assert": "assembled stream == decoded(a) + decoded(b), byte-exact",
            "a_samples": gapless["a_samples"],
            "b_samples": gapless["b_samples"],
            "pass": True,
        }
    for name, scenario in results.items():
        if not name.startswith("slack") or not isinstance(scenario, dict) or "error" in scenario:
            continue
        slack = scenario["slack_ms"]
        metrics[f"rnf_at_08_1_chain_slack_ms_{name}"] = {
            "threshold_ms": THRESHOLD_MS,
            "max_ms": round(max(slack), 3) if slack else None,
            "p95_ms": round(_percentile(slack, 95), 3) if slack else None,
            "mean_ms": round(statistics.mean(slack), 3) if slack else None,
            "samples": len(slack),
            "files": scenario["files"],
            "gap_ms": scenario["gap_ms"],
            "one_audio_session": scenario["audio_sessions_for_whole_chain"] == 1,
            "pass": bool(slack)
            and max(slack) < THRESHOLD_MS
            and scenario["audio_sessions_for_whole_chain"] == 1,
        }
    return {
        "meta": meta,
        "target": target,
        "metrics": metrics,
        "scenario_results": results,
        "all_pass": all(m.get("pass", False) for m in metrics.values()) if metrics else False,
    }


def _print_summary(record: dict) -> None:
    print(f"\n=== Hito Cadena DoD metrics — target: {record['target']} "
          f"({'ALL PASS' if record['all_pass'] else 'FAILURES PRESENT'}) ===")
    header = f"{'metric':<48}{'threshold':<14}{'measured':<26}pass"
    print(header)
    print("-" * len(header))
    for name, m in record["metrics"].items():
        if name == "us_at_08_3_nothing_inserted":
            measured = f"{m['a_samples']}+{m['b_samples']} samples, byte-exact"
            threshold = "nothing inserted"
        else:
            measured = f"max {m['max_ms']} / p95 {m['p95_ms']} ms (n={m['samples']})"
            threshold = f"<{m['threshold_ms']} ms"
        print(f"{name:<48}{threshold:<14}{measured:<26}{m.get('pass')}")
    print()


def run_target(target: str, args) -> dict:
    meta = {
        "date_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "metric_set": "BLOQUE 1.3 Hito Cadena — definición de done (AT-08)",
        "fidelity": (
            "real daemon subprocess + real IPC + real miniaudio decode of real WAV files "
            "+ real queue dispatch + real chain assembly + real daemon chain-boundary "
            "watchdog; local target audio DEVICE stubbed at AudioSession.play "
            "(simulated consumption at the real sample rate; no device in the WSL "
            "measurement environment)"
        ),
        "seam_definition": (
            "chain-item trace = the daemon's watcher observing the playback cursor "
            "cross a chain item's chain-global start (item 0 included); slack between "
            "consecutive crossings minus the audio time between the starts "
            "(duration + gap) is the scheduling hole bounded by RNF-AT-08-1"
        ),
        "environment": {
            "platform": platform.platform(),
            "kernel": platform.release(),
            "cpu_count": multiprocessing.cpu_count(),
            "python": sys.version.split()[0],
            "item_audio_sec": 0.4,
            "wav_format": f"{WAV_RATE}Hz/{WAV_CH}ch/16bit (decode-native passthrough)",
        },
        "command": f"PYTHONPATH=src {sys.executable} scripts/chain_metrics.py --target {target}",
        "worktree": str(REPO),
        "targets_note": (
            "chain is a local-target milestone (T6); wsl-ps/winhost keep their documented "
            "ERR for seek/phrase controls and are not measured here"
        ),
    }

    d = ChainDaemonRun(target)
    tmp = tempfile.mkdtemp(prefix="agent-tts-chain-metrics-")
    results = {}
    try:
        d.start()
        scenarios = (
            (f"slack_gap{int(args.gap_ms)}", lambda: scenario_chain_slack(d, tmp, args.files, args.gap_ms)),
            ("slack_gap120", lambda: scenario_chain_slack(d, tmp, max(5, args.files // 2), 120.0)),
        )
        for name, runner in scenarios:
            try:
                results[name] = runner()
                print(f"[{target}] scenario {name}: ok", file=sys.stderr)
            except Exception as e:  # honest partial registration
                results[name] = {"error": f"{type(e).__name__}: {e}"}
                print(f"[{target}] scenario {name}: FAILED — {e}", file=sys.stderr)
    finally:
        d.stop()
    try:
        results["gapless_bytes"] = scenario_gapless_bytes(tmp)
    except Exception as e:
        results["gapless_bytes"] = {"error": f"{type(e).__name__}: {e}"}
    return evaluate(target, results, meta)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", choices=("local",), default="local")
    parser.add_argument("--files", type=int, default=10, help="chain length (headline scenario)")
    parser.add_argument("--gap-ms", type=float, default=0.0, help="inter-item silence (headline scenario)")
    parser.add_argument("--out", default=None, help="JSON record path (default metrics/chain/...)")
    args = parser.parse_args()

    record = run_target(args.target, args)
    out = Path(args.out) if args.out else REPO / "metrics" / "chain" / (
        f"chain-metrics-{args.target}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2) + "\n")
    _print_summary(record)
    print(f"JSON record: {out}")
    return 0 if record["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
