#!/usr/bin/env python3
"""MQ-05 final gate runner: executes all 9 M1 gates from the TASKS.md
authority on the frozen candidate, records every one through
gate_evidence.write_gate_evidence (literal argv, full raw logs, sha256,
source bindings) and closes the run manifest.

Run from anywhere; all paths are derived from --repo/--run.
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_evidence as ge

REPO = Path(os.environ["REPO_C"]).resolve()
RUN = Path(os.environ["RUN_DIR"]).resolve()

# Voice-stack run state (immutable baseline run, its blobs and coverage
# lanes) lives under VOICE_STACK_STATE_DIR (default: the user's X-style
# state dir). Every knob is env-overridable — no machine path is embedded:
#   VOICE_STACK_STATE_DIR  state root (default ~/.local/state/voice-stack-runs)
#   VOICE_STACK_B0_RUN     baseline run id under the state root
#   B0_SNAPSHOT            full override for the baseline snapshot file
#   B0_COV_DIR             full override for the baseline coverage dir
#   B0_APP_BLOB            full override for the baseline app.js blob
#   FORMER_EXCLUSIONS      full override for the rejected exclusions file
STATE_DIR = Path(os.environ.get(
    "VOICE_STACK_STATE_DIR", Path.home() / ".local/state/voice-stack-runs"))
B0_RUN = STATE_DIR / os.environ.get("VOICE_STACK_B0_RUN", "20260930T214105Z-vs1")
B0_SNAP = Path(os.environ.get("B0_SNAPSHOT", B0_RUN / "baseline-snapshot.json"))
CAND_SNAP = RUN / "snapshot.json"
B0_COV_DIR = Path(os.environ.get("B0_COV_DIR", B0_RUN / "baseline"))
B0_APP_BLOB = Path(os.environ.get(
    "B0_APP_BLOB",
    B0_RUN / "blobs" / "41d04b1798b440c2d28d67a1f2c0bccb56d0c11df9db94499f105b0c64e0345f"))
FORMER_EXCLUSIONS = Path(os.environ.get(
    "FORMER_EXCLUSIONS", REPO / "scripts/voice-stack/coverage-exclusions.json"))
VENV_HOST = Path(os.environ["VENV_HOST"])
PY = sys.executable


def bind(*rels):
    return {r: hashlib.sha256((REPO / r).read_bytes()).hexdigest() for r in rels}


def record(gate, argv, cwd_rel, kind, contract, suite, artifacts, bindings, timeout=7200):
    print(f"== {gate} ==", flush=True)
    ev = ge.write_gate_evidence(gate, argv, cwd_rel, RUN, REPO, kind, contract,
                                suite, [Path(a) for a in artifacts], bindings,
                                timeout_s=timeout)
    ge.record_gate(RUN, ev)
    print(f"   rc={ev['returncode']} status={ev['derived_status']} counts={ev['parsed_counts']}")
    return ev


def main():
    (RUN / "gates").mkdir(parents=True, exist_ok=True)
    gate = __import__("gate_evidence").GATES

    # ---- G-BOUNDARY (cheap, first)
    record("G-BOUNDARY",
           [str(REPO / "hosts/herdr/brain/.venv/bin/python"), "-m", "pytest",
            "engine/tests/test_monorepo_boundaries.py", "-q"],
           ".", "functional", {0: "pass", "other": "fail"}, "pytest", [],
           bind("engine/tests/test_monorepo_boundaries.py"))

    # ---- G-SMOKE (full hermetic smoke with own venv + namespace)
    # Smoke PATH: the ambient PATH plus the Homebrew prefix when set —
    # nothing machine-specific is appended.
    smoke_path = os.environ["PATH"]
    brew_prefix = os.environ.get("HOMEBREW_PREFIX")
    if brew_prefix:
        smoke_path = f"{brew_prefix}/bin:{smoke_path}"
    smoke_env = {
        "HERDR_TTS_REAL_VENV": str(VENV_HOST / "bin/python"),
        "SMOKE_ROOT": str(RUN.parent.parent / "sr"),  # short path: unix sockets die past 108 chars (case 39c)
        "HOME": str(RUN.parent / "home"),  # sterilized; XDG isolated per case
        "PATH": smoke_path,
    }
    saved = {k: os.environ.get(k) for k in smoke_env}
    os.environ.update(smoke_env)
    record("G-SMOKE",
           ["bash", "scripts/smoke-tests.sh"],
           "hosts/herdr/tts-plugin", "functional", {0: "pass", "other": "fail"},
           "smoke", [],
           bind("hosts/herdr/tts-plugin/scripts/smoke-tests.sh",
                "hosts/herdr/tts-plugin/scripts/bootstrap.sh",
                "hosts/herdr/tts-plugin/bin/herdr-tts"))
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

    # ---- G-E2E (normal browser mode; the instrumented twin runs inside G-JS)
    record("G-E2E",
           [str(REPO / "hosts/herdr/brain/.venv/bin/python"), "-m", "pytest",
            "tests/e2e/", "-q"],
           "hosts/herdr/brain", "functional", {0: "pass", "other": "fail"},
           "pytest", [],
           bind("hosts/herdr/brain/tests/e2e/conftest.py",
                "hosts/herdr/brain/tests/e2e/test_m1_cancel.py",
                "hosts/herdr/brain/tests/e2e/test_m1_glue_paths.py",
                "hosts/herdr/brain/src/herdr_brain/static/app.js",
                "hosts/herdr/brain/src/herdr_brain/static/speech.js"))

    # ---- python coverage gates (two-stage commands, recorded per stage)
    for gate_id, comp, cwd_rel, src_flag, src_dir, pyvenv in (
        ("G-ENG-PY", "engine", "engine", "src/agent_tts", "src/agent_tts",
         REPO / "engine/.venv/bin/python"),
        ("G-BRN-PY", "brain", "hosts/herdr/brain", "src/herdr_brain", "src/herdr_brain",
         REPO / "hosts/herdr/brain/.venv/bin/python"),
        ("G-HOST-PY", "host", "hosts/herdr/tts-plugin", "lib", "lib",
         VENV_HOST / "bin/python"),
    ):
        data = RUN / f"{comp}.coverage"
        j = RUN / f"{comp}.json"
        base_j = B0_COV_DIR / f"{comp}.json"
        argv1 = [str(pyvenv), "-m", "coverage", "run", "--branch",
                 f"--source={src_flag}", f"--data-file={data}", "-m", "pytest"]
        if comp != "host":
            argv1 += ["tests/", "-q", "--ignore=tests/e2e"]
        else:
            argv1 += ["tests/", "-q"]
        bindings = bind("scripts/voice-stack/coverage_gate.py")
        ev1 = record(f"{gate_id}--stage1-pytest", argv1, cwd_rel, "functional",
                     {0: "pass", "other": "fail"}, "pytest", [data], bindings)
        if ev1["returncode"] != 0:
            continue
        argv2 = [str(pyvenv), "-m", "coverage", "json",
                 f"--data-file={data}", "-o", str(j)]
        ev2 = record(f"{gate_id}--stage2-json", argv2, cwd_rel, "functional",
                     {0: "pass", "other": "fail"}, None, [j], bindings)
        if ev2["returncode"] != 0:
            continue
        argv3 = [PY, str(REPO / "scripts/voice-stack/coverage_gate.py"),
                 "--lang", "python", "--component", comp,
                 "--baseline-snapshot", str(B0_SNAP),
                 "--candidate-snapshot", str(CAND_SNAP),
                 "--coverage-json", str(j),
                 "--baseline-coverage", str(base_j)]
        record(gate_id, argv3, ".", "coverage",
               {0: "pass", 1: "fail", 2: "blocked"}, "coverage-gate",
               [j, base_j], bindings)

    # ---- G-JS: full pipeline under the fixed logger (both E2E modes inside)
    js_child = RUN / "js-gate"
    os.environ.update({
        "REPO_C": str(REPO),
        "RUN_DIR": str(js_child),
        "B0_SNAPSHOT": str(B0_SNAP),
        "B0_LCOV": str(B0_COV_DIR / "pwa.lcov"),
        "B0_APP_BLOB": str(B0_APP_BLOB),
        "FORMER_EXCLUSIONS": str(FORMER_EXCLUSIONS),
        "NODE_TOOLS": str(RUN.parent.parent / "tools/nodejs"),
        "PY": PY,
    })
    record("G-JS",
           ["bash", str(REPO / "scripts/voice-stack/js-coverage/run-pwa-gate.sh")],
           ".", "coverage",
           {0: "pass", 1: "fail", 2: "blocked"}, "coverage-gate",
           [js_child / "gate.log", js_child / "map-app.json",
            js_child / "map-speech.json", js_child / "combined-detail.json",
            js_child / "node-tests.stdout.log", js_child / "e2e-tests.stdout.log",
            js_child / "evidence.sha256"],
           bind("scripts/voice-stack/js-coverage/run-pwa-gate.sh",
                "scripts/voice-stack/js-coverage/map-changed-scope.py",
                "scripts/voice-stack/coverage_gate.py",
                "hosts/herdr/brain/src/herdr_brain/static/app.js",
                "hosts/herdr/brain/src/herdr_brain/static/speech.js"),
           timeout=3600)

    # ---- G-BASH-LINES (product herdr-tts + harness lines via PS4 xtrace)
    argv_bl = [PY, str(REPO / "scripts/voice-stack/bash_changed_lines.py"),
               "--baseline-snapshot", str(B0_SNAP),
               "--candidate-snapshot", str(CAND_SNAP),
               "--ps4-harness", "hosts/herdr/tts-plugin/tests/all_bash_harnesses.sh",
               "--repo-root", str(REPO)]
    record("G-BASH-LINES", argv_bl, ".", "coverage",
           {0: "pass", 1: "fail", 2: "blocked"}, "bash-gate", [],
           bind("scripts/voice-stack/bash_changed_lines.py",
                "hosts/herdr/tts-plugin/tests/all_bash_harnesses.sh",
                "hosts/herdr/tts-plugin/tests/host_cli_cases.sh",
                "hosts/herdr/tts-plugin/tests/bootstrap_sterile_harness.sh",
                "hosts/herdr/tts-plugin/bin/herdr-tts"))

    # ---- G-BASH-MATRIX (product decision table)
    argv_bm = [PY, str(REPO / "scripts/voice-stack/bash_matrix.py"),
               "--baseline-snapshot", str(B0_SNAP),
               "--candidate-snapshot", str(CAND_SNAP),
               "--table", "hosts/herdr/tts-plugin/tests/matrix/bash-decisions.json"]
    record("G-BASH-MATRIX", argv_bm, ".", "coverage",
           {0: "pass", 1: "fail", 2: "blocked"}, "bash-gate", [],
           bind("scripts/voice-stack/bash_matrix.py",
                "hosts/herdr/tts-plugin/tests/matrix/bash-decisions.json",
                "hosts/herdr/tts-plugin/tests/matrix/README.md",
                "hosts/herdr/tts-plugin/tests/host_cli_cases.sh"))

    print("== all gates recorded ==")


if __name__ == "__main__":
    main()
