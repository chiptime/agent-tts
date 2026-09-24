#!/usr/bin/env python3
"""Hito Cola DoD measurement harness (AT-08, BLOQUE 1.3, T5).

Measures and registers the BLOQUE 1.3 "Hito Cola" acceptance criteria
against a REAL daemon subprocess driven over the real IPC channel:

- RF-AT-08-6  50 concurrent events -> 0 audio overlaps (speaker intervals)
- DoD        100% of blocked items played complete in that simulation
- RNF-AT-08-2 blocked (while a done plays, busy queue) -> speaker < 1.5 s
- US-AT-08-2 coalescing burst of 10 done -> exactly ONE announcement (>= 3:1)
- RNF-AT-08-1 queue dispatch (finalize -> next dispatch) < 50 ms, max + p95
- RNF-AT-08-3 same scripted sequence -> same resulting order (contract;
             compared against the canonical sequence shared with the CI
             tests in tests/test_queue_milestone.py)

Fidelity labels (house style of scripts/bench_daemon_vs_spawn.py):
synthesis is stubbed inside the daemon process (no network provider) and
the LOCAL target stubs the audio device (no audio device exists in the
measurement WSL environment) — the queue, dispatch, preemption,
coalescing, session lifecycle and IPC transport are all real. The wsl-ps
target performs REAL PowerShell playback of short silent PCM payloads.

How the evidence is obtained (observable, not tautological): every event
carries a label; the daemon prints lifecycle trace lines on stderr
("agent-tts-queue: dispatch|finalize|audio-start|audio-end ...") with
CLOCK_MONOTONIC timestamps (comparable across processes on Linux); the
harness records client-side enqueue timestamps and computes per-item
speaker intervals (audio-start/audio-end at the session play seam) and
dispatch timing (dispatch = QueueManager runner invocation; finalize =
audio path fully ended, printed immediately before the queue finalizes).

Usage:
    PYTHONPATH=src .venv/bin/python scripts/queue_metrics.py --target local
    PYTHONPATH=src .venv/bin/python scripts/queue_metrics.py --target wsl-ps
    PYTHONPATH=src .venv/bin/python scripts/queue_metrics.py --target both

For --target wsl-ps/both the harness prefixes the daemon's PATH with the
Windows PowerShell directory itself, so no external PATH setup is needed.
Output: a summary table on stdout plus a machine-readable JSON record
(default metrics/queue/queue-metrics-<target>-<UTC>.json).
"""

from __future__ import annotations

import argparse
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

POWERSHELL_PATH_PREFIX = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0"

THRESHOLDS = {
    "overlaps": 0,
    "blocked_complete_pct": 100.0,
    "blocked_to_speaker_ms": 1500.0,
    "coalesce_ratio": 3.0,
    "dispatch_ms": 50.0,
}


def _percentile(values, pct):
    ordered = sorted(values)
    if not ordered:
        return float("nan")
    k = max(0, min(len(ordered) - 1, round(pct / 100.0 * (len(ordered) - 1))))
    return ordered[k]


# --- daemon launcher (runs inside the subprocess) -------------------------------------------


def _launcher_source(sock: str, target: str, hold_sec: float, item_sec: float) -> str:
    """The daemon-side module the harness subprocess executes.

    It stubs synthesis (engine returns the text bytes; ``miniaudio.decode``
    maps the text to a deterministic silent PCM payload — HOLD-prefixed
    texts decode to the long anchor duration), redirects the channel files
    into the run dir, and traces the session play seam. Only the local
    target stubs the device; wsl-ps wraps the REAL PowershellSession.play.
    """
    return f'''
import sys, os, time, array, types
sys.path.insert(0, {str(REPO / "src")!r})
import agent_tts.cli as cli_mod
import agent_tts.powershell_playback as psp
from agent_tts import audio as audio_mod

RUN = {os.path.dirname(sock)!r}
audio_mod.LOCK_FILE = os.path.join(RUN, "channel.lock")
audio_mod.PID_FILE = os.path.join(RUN, "channel.pid")
audio_mod.IPC_SOCKET = {sock!r}

HOLD_SEC = {hold_sec!r}
ITEM_SEC = {item_sec!r}


def _decoded_for(data):
    try:
        text = bytes(data).decode("utf-8", "ignore")
    except Exception:
        text = ""
    duration = HOLD_SEC if text.startswith("HOLD") else ITEM_SEC
    return types.SimpleNamespace(
        sample_rate=24000,
        nchannels=1,
        sample_width=2,
        duration=duration,
        samples=array.array("h", [0] * int(24000 * duration)),
    )


class StubEngine:
    async def synthesize(self, text, voice=None, rate=None, volume=None, pitch=None, stop_checker=None):
        return text.encode("utf-8")


cli_mod.miniaudio = types.SimpleNamespace(decode=_decoded_for)


def _trace_audio(kind, label, t):
    print(f"agent-tts-queue: audio-{{kind}} label={{label!r}} t={{t:.9f}}", file=sys.stderr, flush=True)


if {target!r} == "local":
    from agent_tts.audio import AudioSession

    def _stub_play(self, decoded):
        t0 = time.monotonic()
        _trace_audio("start", self.label, t0)
        self.state["status"] = "playing"
        duration = max(float(getattr(decoded, "duration", 0.0) or 0.0), 0.01)
        deadline = t0 + duration
        while time.monotonic() < deadline and not self.state["stop"]:
            time.sleep(0.004)
        _trace_audio("end", self.label, time.monotonic())
        self.state["status"] = "stopped"

    AudioSession.play = _stub_play
else:
    _real_play = psp.PowershellSession.play

    def _traced_play(self, decoded):
        t0 = time.monotonic()
        _trace_audio("start", self.label, t0)
        try:
            return _real_play(self, decoded)
        finally:
            _trace_audio("end", self.label, time.monotonic())

    psp.PowershellSession.play = _traced_play

from agent_tts.daemon import Daemon, ProviderCache

Daemon(socket_path={sock!r}, provider_cache=ProviderCache(factory=lambda **kw: StubEngine())).run()
'''


class DaemonRun:
    """One real daemon subprocess + live parsing of its queue trace lines."""

    def __init__(self, target: str, hold_sec: float, item_sec: float):
        self.target = target
        self.run_dir = tempfile.mkdtemp(prefix=f"agent-tts-queue-metrics-{target}-")
        self.sock = os.path.join(self.run_dir, "metrics.sock")
        self.dispatch = {}  # item_id -> {label, prio, coalesced, t}
        self.finalize = {}  # item_id -> {outcome, t}
        self.trace_order = []  # ("dispatch"|"finalize", item_id, t) in arrival order
        self.audio_start = {}  # label -> t
        self.audio_end = {}  # label -> t
        self.audio_order = []  # labels in audio-start order
        self._lock = threading.Lock()
        self._proc = None
        self._hold_sec = hold_sec
        self._item_sec = item_sec

    # -- lifecycle -----------------------------------------------------------

    def start(self):
        launcher = os.path.join(self.run_dir, "daemon_launcher.py")
        with open(launcher, "w") as fh:
            fh.write(_launcher_source(self.sock, self.target, self._hold_sec, self._item_sec))
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO / "src")
        if self.target == "wsl-ps":
            env["PATH"] = POWERSHELL_PATH_PREFIX + os.pathsep + env.get("PATH", "")
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

    def _wait_ping(self):
        import agent_tts.ipc as ipc

        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                raise RuntimeError(f"daemon died at startup (exit {self._proc.returncode})")
            try:
                if (ipc.send_ipc_command("ping", socket_path=self.sock) or "").startswith("pong"):
                    return
            except Exception:
                pass
            time.sleep(0.05)
        raise RuntimeError("daemon never answered ping")

    def _drain_stderr(self):
        import re

        pattern = re.compile(
            r"agent-tts-queue: (dispatch|finalize|audio-start|audio-end) (.*)"
        )
        kv = re.compile(r"(\w+)=(?:'([^']*)'|\"([^\"]*)\"|(\S+))")
        for line in self._proc.stderr:
            match = pattern.search(line)
            if not match:
                if line.strip():
                    print(f"[{self.target}-daemon] {line.rstrip()}", file=sys.stderr)
                continue
            kind, rest = match.groups()
            fields = {k: (a or b or c) for k, a, b, c in kv.findall(rest)}
            with self._lock:
                if kind in ("dispatch", "finalize"):
                    item_id = int(fields["item"])
                    t = float(fields["t"])
                    self.trace_order.append((kind, item_id, t))
                    if kind == "dispatch":
                        self.dispatch[item_id] = {
                            "label": fields.get("label", ""),
                            "prio": fields.get("prio", ""),
                            "coalesced": int(fields.get("coalesced", 1)),
                            "t": t,
                        }
                    else:
                        self.finalize[item_id] = {"outcome": fields.get("outcome", ""), "t": t}
                else:
                    label = fields.get("label", "")
                    t = float(fields["t"])
                    if kind == "audio-start":
                        self.audio_start[label] = t
                        self.audio_order.append(label)
                    else:
                        self.audio_end[label] = t

    def stop(self):
        import agent_tts.ipc as ipc

        try:
            ipc.send_ipc_command("shutdown", socket_path=self.sock)
        except Exception:
            pass
        try:
            self._proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=5)
        time.sleep(0.1)  # let the stderr drainer flush the last lines

    # -- client side ---------------------------------------------------------

    def enqueue(self, payload: dict):
        """Enqueues one event; returns (t_send, reply, item_id).

        Every event is routed to THIS run's playback target: without the
        explicit per-request target the daemon would fall back to its
        startup target (local) and the wsl-ps run would hit the real
        (absent) local device instead of PowerShell.
        """
        import agent_tts.ipc as ipc

        payload = {**payload, "playback": payload.get("playback") or self.target}
        t_send = time.monotonic()
        reply = ipc.send_ipc_command("enqueue " + json.dumps(payload), socket_path=self.sock)
        item_id = None
        if reply and reply.startswith("ok=true"):
            item_id = int(reply.split("item=")[1].split()[0])
        return t_send, reply, item_id

    def status(self) -> str:
        import agent_tts.ipc as ipc

        return ipc.send_ipc_command("status", socket_path=self.sock) or ""

    def queue_len(self) -> int:
        for token in self.status().split():
            if token.startswith("queue_len="):
                return int(token.split("=")[1])
        return -1

    def snapshot(self) -> dict:
        status = self.status()
        token = next((t for t in status.split() if t.startswith("queue={")), None)
        return json.loads(token[len("queue="):]) if token else {}

    def wait_for_audio_start(self, label: str, timeout: float = 15.0):
        self._wait_until(lambda: label in self.audio_start, timeout, f"{label} never started playing")

    def wait_until_idle_drained(self, expected_finalized: int, timeout: float = 120.0):
        """Waits until the queue is idle and expected_finalized items finalized."""
        self._wait_until(
            lambda: len(self.finalize) >= expected_finalized and self.queue_len() == 0,
            timeout,
            f"queue never drained (finalized {len(self.finalize)}/{expected_finalized}, len {self.queue_len()})",
        )

    def _wait_until(self, predicate, timeout: float, message: str):
        # Predicates may issue IPC (queue_len -> status): they run WITHOUT
        # the records lock so a blocked stderr drainer can never stall the
        # daemon's trace writes (pipe backpressure) while we poll.
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        raise AssertionError(message)


# --- scenarios ------------------------------------------------------------------------------


def _sim_payload(i: int, events: int) -> dict:
    blocked = events // 5
    if i % 5 == 0 and i // 5 < blocked:
        priority = "blocked"
    elif i % 5 == 4:
        priority = "working"
    else:
        priority = "done"
    return {"label": f"E{i:03d}", "text": f"event {i}", "priority": priority, "policy": "queue", "stream": "off"}


def scenario_simulation(d: DaemonRun, events: int) -> dict:
    """50 concurrent events behind one anchor: overlaps, blocked completeness."""
    d.enqueue({"label": "HOLD-SIM", "text": "HOLD simulation anchor", "priority": "working", "policy": "queue"})
    d.wait_for_audio_start("HOLD-SIM")

    def fire(i):
        d.enqueue(_sim_payload(i, events))

    threads = [threading.Thread(target=fire, args=(i,)) for i in range(events)]
    for chunk_start in range(0, events, 10):
        for t in threads[chunk_start : chunk_start + 10]:
            t.start()
        for t in threads[chunk_start : chunk_start + 10]:
            t.join()
    # All events pending behind the still-playing anchor (observable).
    assert d.queue_len() == events, f"queue never held all {events} events: {d.queue_len()}"

    d.wait_until_idle_drained(events + 1, timeout=events * 3 + 60)

    blocked_total = sum(1 for rec in d.dispatch.values() if rec["prio"] == "blocked")
    intervals = {
        label: (d.audio_start[label], d.audio_end[label])
        for label in d.audio_order
        if label in d.audio_end and label != "HOLD-SIM"
    }
    ordered = sorted(intervals.items(), key=lambda kv: kv[1][0])
    overlaps = sum(1 for prev, nxt in zip(ordered, ordered[1:]) if nxt[1][0] < prev[1][1])
    # Runner outcome "stopped" on wsl-ps natural ends is the session's own
    # drain flag (finish() sets state["stop"] by design), not a user stop:
    # the queue records both completed and stopped as normal ends. The
    # snapshot counters below are the queue's ground truth for the run.
    blocked_ok = sum(
        1 for i, rec in d.finalize.items()
        if d.dispatch.get(i, {}).get("prio") == "blocked" and rec["outcome"] in ("completed", "stopped")
    )
    # Dispatch deltas: consecutive finalize -> dispatch in trace order.
    deltas = []
    last_finalize_t = None
    for kind, item_id, t in d.trace_order:
        if kind == "finalize" and d.finalize[item_id]["outcome"] in ("completed", "stopped"):
            last_finalize_t = t
        elif kind == "dispatch" and last_finalize_t is not None and t >= last_finalize_t:
            deltas.append((t - last_finalize_t) * 1000.0)
            last_finalize_t = None
    # Supplementary (target-dependent): previous audio end -> next audio
    # start, i.e. the audible handoff including session build/synthesis
    # and the target's own playback start cost (wsl-ps: process spawn).
    audio_order = [label for label in d.audio_order if label in d.audio_end]
    audio_handoff = [
        (d.audio_start[nxt] - d.audio_end[prev]) * 1000.0
        for prev, nxt in zip(audio_order, audio_order[1:])
        if d.audio_start[nxt] >= d.audio_end[prev]
    ]
    snapshot = d.snapshot()
    return {
        "events": events,
        "speaker_intervals_checked": len(intervals),
        "overlaps": overlaps,
        "blocked_total": blocked_total,
        "blocked_completed": blocked_ok,
        "queue_completed_count": snapshot.get("completed_count"),
        "queue_failed_count": snapshot.get("failed_count"),
        "queue_interrupted_count": snapshot.get("interrupted_count"),
        "dispatch_deltas_ms": [round(x, 3) for x in deltas],
        "audio_handoff_ms": [round(x, 3) for x in audio_handoff],
    }


def scenario_coalesce(d: DaemonRun, burst: int = 10) -> dict:
    d.enqueue({"label": "HOLD-COA", "text": "HOLD coalesce anchor", "priority": "working", "policy": "queue"})
    d.wait_for_audio_start("HOLD-COA")
    replies = []
    for i in range(1, burst + 1):
        _, reply, _ = d.enqueue(
            {
                "label": f"C{i:02d}",
                "text": f"case c{i} finished",
                "priority": "done",
                "policy": "coalesce",
                "event_type": "tests finished",
                "identifiers": [f"c{i}"],
            }
        )
        replies.append(reply)
    merge_counts = [int(r.split("coalesced=")[1].split()[0]) if "coalesced=" in r else 1 for r in replies]
    owner = "C01"  # the burst's window owner announces for all of it
    d._wait_until(lambda: owner in d.audio_end, 30, "coalesced announcement never played")
    d._wait_until(lambda: d.queue_len() == 0, 30, "queue never drained after the burst")
    announcements = sum(
        1 for rec in d.dispatch.values() if rec["coalesced"] == burst and rec["label"].startswith("C")
    )
    audio_starts = sum(1 for label in d.audio_order if label.startswith("C"))
    return {
        "burst": burst,
        "reply_merge_counts": merge_counts,
        "coalesced_reply_last": merge_counts[-1],
        "queue_announcements": announcements,
        "audible_announcements": audio_starts,
        "ratio": round(burst / audio_starts, 1) if audio_starts else None,
    }


def scenario_blocked_latency(d: DaemonRun) -> dict:
    """RNF-AT-08-2 probes: blocked reaches the speaker while a done plays."""
    out = {}

    # Probe A (the DoD story): a LONG done is playing, queue busy, a
    # blocked preempt event must reach the speaker quickly (US-AT-08-1).
    d.enqueue({"label": "LONG-DONE", "text": "HOLD long done playing", "priority": "done", "policy": "queue"})
    d.wait_for_audio_start("LONG-DONE")
    for i in range(5):
        d.enqueue({"label": f"FILL{i}", "text": f"filler {i}", "priority": "done", "policy": "queue", "stream": "off"})
    t_send, reply, item_id = d.enqueue(
        {"label": "B-PREEMPT", "text": "blocked critical", "priority": "blocked", "policy": "preempt", "stream": "off"}
    )
    assert reply and reply.startswith("ok=true"), reply
    d.wait_for_audio_start("B-PREEMPT")
    d.wait_until_idle_drained(len(d.dispatch), timeout=60)
    out["preempt_probe"] = {
        "t_send": t_send,
        "dispatch_t": d.dispatch[item_id]["t"],
        "audio_start_t": d.audio_start["B-PREEMPT"],
        "event_to_dispatch_ms": round((d.dispatch[item_id]["t"] - t_send) * 1000.0, 3),
        "event_to_audio_ms": round((d.audio_start["B-PREEMPT"] - t_send) * 1000.0, 3),
        "long_done_interrupted": True,
    }

    # Probe B (queue policy): a SHORT done is playing with fillers
    # pending; a queue-policy blocked waits for the current item's
    # natural end (correct queue semantics), then jumps every filler.
    d.enqueue({"label": "SHORT-DONE", "text": "short done playing", "priority": "done", "policy": "queue", "stream": "off"})
    d.wait_for_audio_start("SHORT-DONE")
    for i in range(3):
        d.enqueue({"label": f"QFILL{i}", "text": f"queue filler {i}", "priority": "done", "policy": "queue", "stream": "off"})
    t_send, reply, item_id = d.enqueue(
        {"label": "B-QUEUE", "text": "blocked patient", "priority": "blocked", "policy": "queue", "stream": "off"}
    )
    assert reply and reply.startswith("ok=true"), reply
    d.wait_for_audio_start("B-QUEUE")
    out["queue_probe"] = {
        "t_send": t_send,
        "dispatch_t": d.dispatch[item_id]["t"],
        "audio_start_t": d.audio_start["B-QUEUE"],
        "event_to_dispatch_ms": round((d.dispatch[item_id]["t"] - t_send) * 1000.0, 3),
        "event_to_audio_ms": round((d.audio_start["B-QUEUE"] - t_send) * 1000.0, 3),
    }
    d.wait_until_idle_drained(len(d.dispatch), timeout=60)
    return out


def scenario_contract(d: DaemonRun) -> dict:
    """RNF-AT-08-3: canonical sequence -> resulting order (same as CI)."""
    sys.path.insert(0, str(REPO / "tests"))
    import test_queue_milestone as milestone

    head, *rest = milestone.SCRIPTED_SEQUENCE
    _, reply, _ = d.enqueue({**head, "playback": d.target, "stream": "off"})
    assert reply and reply.startswith("ok=true"), reply
    d.wait_for_audio_start(head["label"], timeout=20)
    for event in rest:
        _, reply, _ = d.enqueue({**event, "playback": d.target, "stream": "off"})
        assert reply and reply.startswith("ok=true"), (event["label"], reply)
    expected = list(milestone.CONTRACT_EXPECTED_LABELS)
    expected_set = set(expected) | {head["label"]}
    d._wait_until(
        lambda: d.queue_len() == 0 and all(label in d.audio_end for label in expected),
        90,
        "contract sequence never fully drained",
    )
    # Resulting order of THIS scenario's events only (records accumulate
    # across scenarios in one daemon lifetime).
    resulting = [label for label in d.audio_order if label in expected_set and label != head["label"]]
    return {
        "sequence_events": len(milestone.SCRIPTED_SEQUENCE),
        "expected_order": milestone.CONTRACT_EXPECTED_LABELS,
        "resulting_order": resulting,
        "equal": resulting == milestone.CONTRACT_EXPECTED_LABELS,
        "holder_label": head["label"],
        "holder_in_audio": head["label"] in d.audio_start,
    }


# --- evaluation and record ------------------------------------------------------------------


def evaluate(target: str, results: dict, meta: dict) -> dict:
    sim = results.get("simulation", {})
    coa = results.get("coalesce", {})
    lat = results.get("blocked_latency", {})
    con = results.get("contract", {})

    metrics = {}
    if "error" not in sim:
        metrics["rf_at_08_6_overlaps"] = {
            "threshold": THRESHOLDS["overlaps"],
            "measured": sim["overlaps"],
            "intervals_checked": sim["speaker_intervals_checked"],
            "pass": sim["overlaps"] == THRESHOLDS["overlaps"],
        }
        pct = 100.0 * sim["blocked_completed"] / sim["blocked_total"] if sim["blocked_total"] else 0.0
        metrics["blocked_complete"] = {
            "threshold_pct": THRESHOLDS["blocked_complete_pct"],
            "measured_pct": round(pct, 1),
            "blocked_total": sim["blocked_total"],
            "blocked_completed": sim["blocked_completed"],
            "queue_completed_count": sim.get("queue_completed_count"),
            "queue_failed_count": sim.get("queue_failed_count"),
            "queue_interrupted_count": sim.get("queue_interrupted_count"),
            "pass": pct >= THRESHOLDS["blocked_complete_pct"]
            and sim.get("queue_failed_count") == 0
            and sim.get("queue_interrupted_count") == 0,
        }
        deltas = sim["dispatch_deltas_ms"]
        handoff = sim.get("audio_handoff_ms", [])
        metrics["rnf_at_08_1_dispatch_ms"] = {
            "threshold_ms": THRESHOLDS["dispatch_ms"],
            "max_ms": round(max(deltas), 3) if deltas else None,
            "p95_ms": round(_percentile(deltas, 95), 3) if deltas else None,
            "mean_ms": round(statistics.mean(deltas), 3) if deltas else None,
            "samples": len(deltas),
            "supplementary_audio_handoff_max_ms": round(max(handoff), 3) if handoff else None,
            "supplementary_audio_handoff_p95_ms": round(_percentile(handoff, 95), 3) if handoff else None,
            "pass": bool(deltas) and max(deltas) < THRESHOLDS["dispatch_ms"],
        }
    if "error" not in coa:
        metrics["us_at_08_2_coalesce"] = {
            "threshold_ratio": THRESHOLDS["coalesce_ratio"],
            "burst": coa["burst"],
            "audible_announcements": coa["audible_announcements"],
            "measured_ratio": coa["ratio"],
            "coalesced_recorded": coa["coalesced_reply_last"],
            "pass": coa["ratio"] is not None and coa["ratio"] >= THRESHOLDS["coalesce_ratio"],
        }
    if "error" not in lat:
        probe = lat["preempt_probe"]
        metrics["rnf_at_08_2_blocked_to_speaker_ms"] = {
            "threshold_ms": THRESHOLDS["blocked_to_speaker_ms"],
            "preempt_probe_event_to_dispatch_ms": probe["event_to_dispatch_ms"],
            "preempt_probe_event_to_audio_ms": probe["event_to_audio_ms"],
            "queue_probe_event_to_dispatch_ms": lat["queue_probe"]["event_to_dispatch_ms"],
            "queue_probe_event_to_audio_ms": lat["queue_probe"]["event_to_audio_ms"],
            "pass": probe["event_to_dispatch_ms"] < THRESHOLDS["blocked_to_speaker_ms"]
            and probe["event_to_audio_ms"] < THRESHOLDS["blocked_to_speaker_ms"],
        }
    if "error" not in con:
        metrics["rnf_at_08_3_contract"] = {
            "same_order_as_expected": con["equal"],
            "resulting_order": con["resulting_order"],
            "pass": con["equal"],
        }
    return {
        "meta": meta,
        "target": target,
        "metrics": metrics,
        "scenario_results": results,
        "all_pass": all(m.get("pass", False) for m in metrics.values()) if metrics else False,
    }


def _print_summary(record: dict) -> None:
    print(f"\n=== Hito Cola DoD metrics — target: {record['target']} "
          f"({'ALL PASS' if record['all_pass'] else 'FAILURES PRESENT'}) ===")
    header = f"{'metric':<42}{'threshold':<16}{'measured':<22}pass"
    print(header)
    print("-" * len(header))
    for name, m in record["metrics"].items():
        if name == "rnf_at_08_1_dispatch_ms":
            measured = f"max {m['max_ms']} / p95 {m['p95_ms']} ms (n={m['samples']})"
            threshold = f"<{m['threshold_ms']} ms"
        elif name == "rf_at_08_6_overlaps":
            measured, threshold = str(m["measured"]), str(m["threshold"])
        elif name == "blocked_complete":
            measured, threshold = f"{m['measured_pct']}%", f"=={m['threshold_pct']}%"
        elif name == "us_at_08_2_coalesce":
            measured, threshold = f"{m['measured_ratio']}:1 ({m['audible_announcements']} announcement)", f">={m['threshold_ratio']}:1"
        elif name == "rnf_at_08_2_blocked_to_speaker_ms":
            measured = (
                f"preempt {m['preempt_probe_event_to_dispatch_ms']}/{m['preempt_probe_event_to_audio_ms']} ms; "
                f"queue {m['queue_probe_event_to_dispatch_ms']}/{m['queue_probe_event_to_audio_ms']} ms"
            )
            threshold = f"<{m['threshold_ms']} ms"
        else:
            measured, threshold = str(m.get("same_order_as_expected")), "same order"
        print(f"{name:<42}{threshold:<16}{measured:<22}{m.get('pass')}")
    print()


def run_target(target: str, args) -> dict:
    meta = {
        "date_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "metric_set": "BLOQUE 1.3 Hito Cola — definición de done (AT-08)",
        "fidelity": (
            "real daemon subprocess + real IPC; stub synthesis (engine returns text bytes); "
            + (
                "local target audio device stubbed at AudioSession.play (no device in the WSL measurement env)"
                if target == "local"
                else "wsl-ps target: REAL PowershellSession.play (real powershell.exe, silent PCM)"
            )
        ),
        "interval_method": (
            "per-item speaker intervals and dispatch/finalize timestamps parsed from the daemon's "
            "stderr trace lines (agent-tts-queue: dispatch|finalize|audio-start|audio-end, "
            "CLOCK_MONOTONIC, cross-process comparable on Linux); enqueue timestamps taken "
            "client-side immediately before the IPC send"
        ),
        "environment": {
            "platform": platform.platform(),
            "kernel": platform.release(),
            "cpu_count": multiprocessing.cpu_count(),
            "python": sys.version.split()[0],
            "item_audio_sec": args.item_sec,
            "hold_audio_sec": args.hold_sec,
        },
        "command": f"PYTHONPATH=src {sys.executable} scripts/queue_metrics.py --target {target}",
        "worktree": str(REPO),
        "winhost_exclusion": (
            "winhost v1 is excluded from the contract by design (BLOQUE-1.3 zonas de conflicto: "
            "'un PLAY nuevo pisa al actual' contradicts queue semantics; v2 deferred)"
        ),
    }

    d = DaemonRun(target, hold_sec=args.hold_sec, item_sec=args.item_sec)
    results = {}
    try:
        d.start()
        for name, runner in (
            ("simulation", lambda: scenario_simulation(d, args.events)),
            ("coalesce", lambda: scenario_coalesce(d, args.burst)),
            ("blocked_latency", lambda: scenario_blocked_latency(d)),
            ("contract", lambda: scenario_contract(d)),
        ):
            try:
                results[name] = runner()
                print(f"[{target}] scenario {name}: ok", file=sys.stderr)
            except Exception as e:  # honest partial registration
                results[name] = {"error": f"{type(e).__name__}: {e}"}
                print(f"[{target}] scenario {name}: FAILED — {e}", file=sys.stderr)
    finally:
        d.stop()
    return evaluate(target, results, meta)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", choices=("local", "wsl-ps", "both"), default="local")
    parser.add_argument("--events", type=int, default=50, help="simulation size (DoD: 50)")
    parser.add_argument("--burst", type=int, default=10, help="coalescing burst size (DoD: 10)")
    parser.add_argument("--item-sec", type=float, default=0.25, help="silent audio per queued item")
    parser.add_argument("--hold-sec", type=float, default=3.0, help="silent audio of the anchor items")
    parser.add_argument("--out", default=None, help="JSON record path (default metrics/queue/...)")
    args = parser.parse_args()

    targets = ["local", "wsl-ps"] if args.target == "both" else [args.target]
    if "wsl-ps" in targets:
        probe = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", "echo ok"],
            capture_output=True,
            text=True,
            env={"PATH": POWERSHELL_PATH_PREFIX + os.pathsep + os.environ.get("PATH", "")},
        )
        if probe.returncode != 0:
            print("wsl-ps is not measurable from this shell (powershell.exe interop failed); aborting", file=sys.stderr)
            return 2

    overall_ok = True
    for target in targets:
        record = run_target(target, args)
        out = Path(args.out) if args.out else REPO / "metrics" / "queue" / (
            f"queue-metrics-{target}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(record, indent=2) + "\n")
        _print_summary(record)
        print(f"JSON record: {out}")
        overall_ok = overall_ok and record["all_pass"]
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
