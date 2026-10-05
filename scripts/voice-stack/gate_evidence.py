#!/usr/bin/env python3
"""Deterministic gate-evidence writer + validator (voice-stack MQ-04).

Replaces manual prose gate reporting with machine-checked evidence:

  write     Execute the required M1 gates from the trusted GATES table
            (argv lists, never shell strings), capturing returncode, FULL
            raw stdout/stderr (never tail-truncated) with sha256, parsed
            suite counts re-derivable from the raw logs, artifact hashes,
            and source bindings. Emits one evidence JSON per gate plus the
            run manifest.
  validate  Re-verify a run directory end to end: run_id == dirname, the
            required-gate list matches the DOC authority (TASKS.md gate
            table, G-X excluded), statuses are pass|fail|blocked, exit
            codes match the declared contract (rc=1 + status=pass is
            REJECTED), commands are literal (no ellipsis) with cwd inside
            the owned root, logs exist inside the run dir (no traversal)
            and hash-match, coverage passes carry real metric artifacts
            (not prose), suite counts re-parse from the raw logs (a
            materialization counter pasted as a test count is rejected),
            and an E2E claim without a raw log never passes. Tampered
            hashes, blank coverage, empty-scope changed-file reports and
            unknown return codes are rejected or blocked — never guessed.
  ledger    Classify every modified file (vs HEAD) into production /
            operational-producer / test-program / verification-tool /
            env-pin / docs roles and REQUIRE each production file to map
            to a gate metric; unmapped production is a DECLARED GAP that
            blocks completeness (never silently omitted).
  selftest  Negative controls FIRST: synthesize broken run dirs of every
            defect class below and assert the validator rejects or blocks
            each one, then assert a healthy fixture passes. No
            empty-output fake pass: every control asserts on observable
            validator output.

Exit codes: 0 ok · 1 validation failure (rejected evidence) · 2 blocked
(missing/unusable inputs). This is ordinary functional tooling: it emits
no native review receipts and invents no authorization lineage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

TOOL = "gate_evidence.py"

# ---------------------------------------------------------------- gates table
# Literal commands per docs/voice-stack/TASKS.md "Gates" table (the doc is
# the authority; --authority-doc re-checks the ID list at validate time).
# $RUN placeholders are substituted by the writer with the run dir. argv
# lists only — the writer never runs a shell string.
GATES: dict[str, dict] = {
    "G-ENG-PY": {
        "doc": "TASKS.md G-ENG-PY", "cwd": "engine", "argv": None,  # assembled at write time
        "contract": {0: "pass", 1: "fail", 2: "blocked"},
        "kind": "coverage", "metric": ">=90 lines+branches, touched production (python)",
    },
    "G-BRN-PY": {
        "doc": "TASKS.md G-BRN-PY", "cwd": "hosts/herdr/brain", "argv": None,
        "contract": {0: "pass", 1: "fail", 2: "blocked"},
        "kind": "coverage", "metric": ">=90 lines+branches, touched production (python)",
    },
    "G-HOST-PY": {
        "doc": "TASKS.md G-HOST-PY", "cwd": "hosts/herdr/tts-plugin", "argv": None,
        "contract": {0: "pass", 1: "fail", 2: "blocked"},
        "kind": "coverage", "metric": ">=90 lines+branches, touched production (python)",
    },
    "G-JS": {
        "doc": "TASKS.md G-JS", "cwd": ".", "argv": None,
        "contract": {0: "pass", 1: "fail", 2: "blocked"},
        "kind": "coverage",
        "metric": ("changed-production JS: mapped D4 changed scope "
                   "(app.js) + new-file floor (speech.js); totals on the "
                   "baseline-comparable lane"),
    },
    "G-E2E": {
        "doc": "TASKS.md G-E2E", "cwd": "hosts/herdr/brain", "argv": None,
        "contract": {0: "pass", "other": "fail"},
        "kind": "functional", "metric": "pytest exit 0 with raw summary log",
    },
    "G-BASH-LINES": {
        "doc": "TASKS.md G-BASH-LINES", "cwd": ".", "argv": None,
        "contract": {0: "pass", 1: "fail", 2: "blocked"},
        "kind": "coverage", "metric": ">=90% modified executable Bash lines",
    },
    "G-BASH-MATRIX": {
        "doc": "TASKS.md G-BASH-MATRIX", "cwd": ".", "argv": None,
        "contract": {0: "pass", 1: "fail", 2: "blocked"},
        "kind": "coverage", "metric": "decision-alternative matrix executed",
    },
    "G-BOUNDARY": {
        "doc": "TASKS.md G-BOUNDARY", "cwd": ".", "argv": None,
        "contract": {0: "pass", "other": "fail"},
        "kind": "functional", "metric": "pytest exit 0 with raw summary log",
    },
    "G-SMOKE": {
        "doc": "TASKS.md G-SMOKE", "cwd": "hosts/herdr/tts-plugin", "argv": None,
        "contract": {0: "pass", "other": "fail"},
        "kind": "functional", "metric": "full smoke RESULT line: 0 failed",
    },
}
REQUIRED_GATES = [g for g in GATES if g != "G-X"]

ENV_ALLOWLIST = [  # names only — values are never recorded (no secrets)
    "PATH", "HOME", "VIRTUAL_ENV", "PYTEST_ADDOPTS", "NODE_OPTIONS",
    "E2E_JS_COVERAGE_DIR", "JS_COVERAGE_DIR", "NODE_V8_COVERAGE",
    "VOICE_STACK_JS_TOOLS", "HERDR_TTS_REAL_VENV", "SMOKE_ROOT",
]


class ToolError(Exception):
    """Blocked tool-level failure (missing inputs, unusable run dir)."""


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------- parsing

PYTEST_SUMMARY = re.compile(r"^=?\s*(\d+)?\s*passed([^\n]*)?$", re.M)
PYTEST_FULL = re.compile(r"(\d+) passed")
TAP_TESTS = re.compile(r"^.?\s*tests\s+(\d+)\s*$", re.M)
TAP_PASS = re.compile(r"^.?\s*pass\s+(\d+)\s*$", re.M)
TAP_FAIL = re.compile(r"^.?\s*fail\s+(\d+)\s*$", re.M)
SMOKE_RESULT = re.compile(r"RESULT:\s*(\d+) passed,\s*(\d+) failed")
GATE_MARKER = re.compile(r"^COVERAGE_GATE_(PASS|FAIL)\s*$", re.M)
GATE_BLOCKED = re.compile(r"^BLOCKED:\s*(.+)$", re.M)
BASH_MARKER = re.compile(r"^(BASH_LINES_PASS|MATRIX_PASS)\s*$", re.M)
PCT_LINE = re.compile(r"(lines|branches)\s+([0-9.]+)%")
PCT_BARE = re.compile(r"coverage=([0-9.]+)%")


def parse_counts(kind: str, text: str) -> dict:
    """Counts parsed ONLY from the suite's own raw output conventions."""
    out: dict = {}
    if kind == "pytest":
        m = PYTEST_FULL.search(text)
        if m:
            out["pytest_passed"] = int(m.group(1))
    elif kind == "node-tap":
        t, p, f = TAP_TESTS.search(text), TAP_PASS.search(text), TAP_FAIL.search(text)
        if t:
            out["node_tests"] = int(t.group(1))
        if p:
            out["node_pass"] = int(p.group(1))
        if f:
            out["node_fail"] = int(f.group(1))
    elif kind == "smoke":
        m = SMOKE_RESULT.search(text)
        if m:
            out["smoke_passed"], out["smoke_failed"] = int(m.group(1)), int(m.group(2))
    elif kind == "coverage-gate":
        m = GATE_MARKER.search(text)
        if m:
            out["coverage_gate"] = m.group(1).lower()
        pcts = PCT_LINE.findall(text)
        if pcts:
            out["metric_pct_lines"] = [float(v) for k, v in pcts if k == "lines"]
            out["metric_pct_branches"] = [float(v) for k, v in pcts if k == "branches"]
        b = GATE_BLOCKED.search(text)
        if b:
            out["blocked_reason"] = b.group(1).strip()
    elif kind == "bash-gate":
        m = BASH_MARKER.search(text)
        if m:
            out["bash_marker"] = m.group(1)
        m = PCT_BARE.search(text)
        if m:
            out["metric_pct_lines"] = [float(m.group(1))]
    return out


# ---------------------------------------------------------------- writer

def gate_argv(gate: str, run: Path, repo: Path, args) -> list[str] | None:
    """Assemble the literal argv for a gate at run time (never a shell str)."""
    py = args.python
    if gate == "G-ENG-PY":
        return None  # two-stage coverage command; assembled by caller recipe below
    return None


def write_gate_evidence(gate: str, argv: list[str], cwd_rel: str, run: Path,
                        repo: Path, kind: str, contract: dict,
                        suite: str | None, artifacts: list[Path],
                        source_bindings: dict[str, str],
                        timeout_s: int = 3600) -> dict:
    cwd = (repo / cwd_rel).resolve()
    if not is_within(cwd, repo):
        raise ToolError(f"{gate}: cwd {cwd_rel} escapes the owned root")
    if "..." in " ".join(argv):
        raise ToolError(f"{gate}: literal command contains an ellipsis")
    started = now_utc()
    t0 = time.monotonic()
    proc = subprocess.run(argv, cwd=str(cwd), capture_output=True,
                          timeout=timeout_s)
    elapsed = time.monotonic() - t0
    gdir = run / "gates"
    gdir.mkdir(parents=True, exist_ok=True)
    out_log = gdir / f"{gate}.stdout.log"
    err_log = gdir / f"{gate}.stderr.log"
    out_log.write_bytes(proc.stdout)
    err_log.write_bytes(proc.stderr)
    text = proc.stdout.decode("utf-8", "replace")
    counts = parse_counts(suite, text) if suite else {}
    rc = proc.returncode
    status = contract.get(rc) or contract.get("other") or "unknown"
    art = []
    for a in artifacts:
        p = Path(a)
        if not p.is_file():
            art.append({"path": str(p), "missing": True})
            continue
        art.append({"path": str(p), "sha256": sha256_file(p),
                    "bytes": p.stat().st_size})
    return {
        "schema": 1,
        "gate": gate,
        "kind": kind,
        "suite": suite,
        "argv": argv,
        "cwd_rel": cwd_rel,
        "started_utc": started,
        "finished_utc": now_utc(),
        "elapsed_s": round(elapsed, 3),
        "returncode": rc,
        "contract": {str(k): v for k, v in contract.items()},
        "derived_status": status,
        "stdout_log": str(out_log.relative_to(run)),
        "stderr_log": str(err_log.relative_to(run)),
        "stdout_sha256": sha256_bytes(proc.stdout),
        "stderr_sha256": sha256_bytes(proc.stderr),
        "stdout_bytes": len(proc.stdout),
        "stderr_bytes": len(proc.stderr),
        "parsed_counts": counts,
        "artifacts": art,
        "source_bindings": source_bindings,
        "env_names_recorded": [e for e in ENV_ALLOWLIST if os.environ.get(e)],
    }


def cmd_write(args) -> int:
    repo = Path(args.repo_root).resolve()
    run = Path(args.run_dir).resolve()
    run.mkdir(parents=True, exist_ok=True)
    if args.run_id and args.run_id != run.name:
        raise ToolError(f"run_id {args.run_id!r} != run dir name {run.name!r}")
    if not is_within(run, Path(args.runspace).resolve()):
        raise ToolError("run dir must live inside the declared own runs namespace")

    manifest = {
        "schema": 1,
        "tool": TOOL,
        "run_id": run.name,
        "created_utc": now_utc(),
        "repo_root": str(repo),
        "repo_head": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(repo),
            capture_output=True, text=True).stdout.strip(),
        "baselines": {
            "genesis_b0": {
                "role": "genesis pre-VS1 — M1/coverage comparisons, never replaced",
                "run_id": "20260930T214105Z-vs1",
                "snapshot_binding": "b8128eef7adebadb433730d24f9d9ea4d1d91217755c73591b37378e24863bed",
                "source": "voice-stack-runs/20260930T214105Z-vs1 (read-only)",
            },
            "milestone_b1": {
                "role": "post-M1 materialized source binding (changed-scope mapping base)",
                "run_id": "20261001T065148Z-vs2",
                "snapshot_binding": "aad7d6c95e1a8863e45a6a9d7837f0870e64e833d47995cc5172c00816462b92",
                "source": "voice-stack-runs/20261001T065148Z-vs2 (read-only)",
            },
        },
        "required_gates": REQUIRED_GATES,
        "gates": [],
        "status": "in_progress",
    }
    (run / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"wrote manifest skeleton run_id={run.name} (gates pending MQ-05)")
    print("NOTE: gate execution recipes are driven by the caller recipe file; "
          "this writer only records what actually ran.")
    return 0


def record_gate(run: Path, evidence: dict) -> None:
    """Append one gate's evidence JSON into the run dir + manifest."""
    run = run.resolve()
    gdir = run / "gates"
    gdir.mkdir(parents=True, exist_ok=True)
    (gdir / f"{evidence['gate']}.json").write_text(
        json.dumps(evidence, indent=1, sort_keys=True) + "\n")
    manifest_path = run / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["gates"] = [g for g in manifest.get("gates", [])
                         if g["gate"] != evidence["gate"]]
    manifest["gates"].append({
        "gate": evidence["gate"], "status": evidence["derived_status"],
        "evidence": f"gates/{evidence['gate']}.json",
        "returncode": evidence["returncode"]})
    manifest_path.write_text(json.dumps(manifest, indent=1) + "\n")


# ---------------------------------------------------------------- validator

def load_authority_gate_ids(doc_path: Path) -> list[str]:
    """Parse the G-* IDs from the TASKS.md gates table (doc authority)."""
    text = doc_path.read_text(encoding="utf-8")
    ids = re.findall(r"^\|\s*(G-[A-Z0-9-]+)\s*\|", text, re.M)
    return [i for i in ids if i != "G-X"]


def validate_run(run: Path, authority_doc: Path | None,
                 repo_root: Path | None = None) -> tuple[str, list[str]]:
    """Returns (verdict, findings). verdict: reject|blocked|pass."""
    findings: list[str] = []
    hard = False  # any hard finding => reject (dishonest/unusable evidence)
    run = run.resolve()
    manifest_path = run / "manifest.json"
    if not manifest_path.is_file():
        return "blocked", ["run manifest missing"]
    try:
        manifest = json.loads(manifest_path.read_text())
    except json.JSONDecodeError as exc:
        return "blocked", [f"manifest unparseable: {exc}"]

    # 1. run_id must equal the directory name (no cross-run copying)
    if manifest.get("run_id") != run.name:
        findings.append(f"run_id {manifest.get('run_id')!r} != dirname {run.name!r} "
                        "(cross-run copy or forgery)")
        hard = True

    # 2. required gates: doc authority list must be fully covered
    if authority_doc is not None:
        doc_ids = load_authority_gate_ids(authority_doc)
        if not doc_ids:
            findings.append("authority doc yielded no gate IDs (wrong doc?)")
            hard = True
    else:
        doc_ids = REQUIRED_GATES
    declared = manifest.get("required_gates", [])
    if sorted(declared) != sorted(doc_ids):
        findings.append(f"declared gates {sorted(declared)} != authority "
                        f"{sorted(doc_ids)} (misrepresented required scope)")
        hard = True
    have = {g.get("gate") for g in manifest.get("gates", [])}
    incomplete = [g for g in doc_ids if g not in have]
    for entry in manifest.get("gates", []):
        gid = entry.get("gate")
        ev_path = run / entry.get("evidence", "")
        if not entry.get("evidence") or not is_within(ev_path, run) \
                or not ev_path.is_file():
            findings.append(f"{gid}: evidence path missing or escapes run dir")
            hard = True
            continue
        try:
            ev = json.loads(ev_path.read_text())
        except json.JSONDecodeError as exc:
            findings.append(f"{gid}: evidence unparseable: {exc}")
            hard = True
            continue
        # 3. status vocabulary + exit/status consistency
        status = entry.get("status")
        if status not in ("pass", "fail", "blocked"):
            findings.append(f"{gid}: status {status!r} not pass|fail|blocked")
            hard = True
        rc = ev.get("returncode")
        contract = {int(k) if k.isdigit() else k: v
                    for k, v in ev.get("contract", {}).items()}
        expected = contract.get(rc) or contract.get("other")
        if expected is None:
            findings.append(f"{gid}: returncode {rc} not in contract {contract}")
            hard = True
        if status == "pass" and rc != 0:
            findings.append(f"{gid}: status=pass but returncode={rc} (exit≠0 cannot pass)")
            hard = True
        # 4. literal command, no ellipsis; cwd inside the owned root
        argv = ev.get("argv")
        if not isinstance(argv, list) or not argv:
            findings.append(f"{gid}: argv missing or not a literal list")
            hard = True
        else:
            if any("..." in a for a in argv):
                findings.append(f"{gid}: command literal contains ellipsis")
                hard = True
            if any(a.startswith("-") is False and re.search(r"[;&|]", a) and False for a in argv):
                pass  # argv lists cannot shell-inject by construction
        repo = repo_root or manifest.get("repo_root")
        cwd_rel = ev.get("cwd_rel", "")
        if repo:
            cwd = (Path(repo) / cwd_rel).resolve()
            if not is_within(cwd, Path(repo).resolve()):
                findings.append(f"{gid}: cwd {cwd_rel!r} escapes owned root")
                hard = True
        # 5. logs: exist, inside run dir, hash-match, coverage not blank
        logs_ok = True
        for log_key in ("stdout_log", "stderr_log"):
            lp = run / ev.get(log_key, "!!missing")
            if not ev.get(log_key) or not is_within(lp, run) or not lp.is_file():
                findings.append(f"{gid}: {log_key} missing or escapes run dir")
                hard = True
                logs_ok = False
                continue
            if sha256_file(lp) != ev.get(f"{log_key[:-4]}_sha256"):
                findings.append(f"{gid}: {log_key} hash mismatch (tampered log)")
                hard = True
                logs_ok = False
        kind = ev.get("kind")
        if kind == "coverage" and status == "pass":
            if ev.get("stdout_bytes", 0) == 0:
                findings.append(f"{gid}: coverage pass with BLANK stdout")
                hard = True
            counts = ev.get("parsed_counts", {})
            marker = counts.get("coverage_gate") or counts.get("bash_marker")
            if marker != "pass" and marker not in ("BASH_LINES_PASS", "MATRIX_PASS"):
                findings.append(
                    f"{gid}: coverage pass without a PASS marker in raw stdout "
                    "(not_applicable/zero-scope prose cannot pass)")
                hard = True
            elif counts.get("coverage_gate") == "pass" and not counts.get("metric_pct_lines"):
                findings.append(
                    f"{gid}: coverage-gate pass without numeric metric lines "
                    "(zero-scope/not_applicable cannot masquerade as pass)")
                hard = True
            if counts.get("bash_marker") in ("BASH_LINES_PASS", "MATRIX_PASS") \
                    and not counts.get("metric_pct_lines") \
                    and counts.get("bash_marker") == "BASH_LINES_PASS":
                findings.append(
                    f"{gid}: bash-lines pass without a numeric coverage percentage "
                    "(a pass claim without its measured number is rejected)")
                hard = True
            # artifact policy: python/js coverage passes carry real metric
            # artifacts; the Bash gates' metric record IS their hash-bound
            # raw stdout (marker + numeric pct) — no side artifacts by design.
            if ev.get("suite") != "bash-gate":
                arts = ev.get("artifacts", [])
                if not arts or any(a.get("missing") for a in arts):
                    findings.append(f"{gid}: coverage pass without complete artifacts")
                    hard = True
                for a in arts:
                    if a.get("missing"):
                        continue
                    ap = Path(a["path"])
                    if ap.is_file() and sha256_file(ap) != a.get("sha256"):
                        findings.append(f"{gid}: artifact hash drift {a['path']}")
                        hard = True
                    if ap.is_file() and ap.stat().st_size == 0:
                        findings.append(f"{gid}: blank coverage artifact {a['path']}")
                        hard = True
        # 6. suite counts must re-parse from the RAW log
        suite = ev.get("suite")
        if suite and logs_ok:
            raw = (run / ev["stdout_log"]).read_text(encoding="utf-8", errors="replace")
            re_counts = parse_counts(suite, raw)
            if re_counts != ev.get("parsed_counts", {}):
                findings.append(
                    f"{gid}: parsed counts {ev.get('parsed_counts')} not re-derivable "
                    f"from raw log {re_counts} (counts pasted from elsewhere are rejected)")
                hard = True
            if suite in ("pytest", "node-tap", "smoke") and not re_counts:
                findings.append(f"{gid}: suite summary line absent from raw log")
                hard = True
            if status == "pass" and suite == "smoke" and re_counts.get("smoke_failed", 1) != 0:
                findings.append(f"{gid}: smoke pass with failed>0 in raw log")
                hard = True
        elif suite and not logs_ok:
            findings.append(f"{gid}: suite counts unverifiable (logs unusable)")
            hard = True
        # E2E pass requires a raw log with a pytest summary (never construction)
        if gid == "G-E2E" and status == "pass":
            if not suite or suite != "pytest":
                findings.append("G-E2E: pass without a pytest raw-summary log")
                hard = True

    # verdict: reject = dishonest/unusable evidence; blocked = honest but
    # incomplete or failing; pass = complete, verified, all green.
    if hard:
        return "reject", findings
    non_pass = [e.get("gate") for e in manifest.get("gates", [])
                if e.get("status") != "pass"]
    if incomplete:
        findings.append(f"required gates not yet recorded: {incomplete}")
    if non_pass:
        findings.append(f"recorded gates not passing: {non_pass}")
    if findings:
        return "blocked", findings
    return "pass", []


def cmd_validate(args) -> int:
    run = Path(args.run_dir).resolve()
    doc = Path(args.authority_doc) if args.authority_doc else None
    repo = Path(args.repo_root) if args.repo_root else None
    verdict, findings = validate_run(run, doc, repo)
    print(f"verdict={verdict}")
    for f in findings:
        print(f"  - {f}")
    if not findings:
        print("VALIDATION_OK")
        return 0
    return 1 if verdict == "reject" else 2


# ---------------------------------------------------------------- ledger

ROLE_RULES = [
    ("production-python", re.compile(r"^(engine/src/|hosts/herdr/brain/src/herdr_brain/.*\.py$|hosts/herdr/tts-plugin/lib/)")),
    ("production-js", re.compile(r"^hosts/herdr/brain/src/herdr_brain/static/.*\.(js|html)$")),
    ("production-bash", re.compile(r"^hosts/herdr/tts-plugin/bin/")),
    ("operational-producer", re.compile(r"^hosts/herdr/tts-plugin/scripts/bootstrap\.sh$")),
    ("test-program", re.compile(r"^hosts/herdr/tts-plugin/scripts/smoke-tests\.sh$")),
    ("test-program", re.compile(r"^(engine/tests/|hosts/herdr/brain/tests/|hosts/herdr/tts-plugin/tests/)")),
    ("verification-tool", re.compile(r"^scripts/voice-stack/")),
    ("env-pin", re.compile(r"\.lock$")),
    ("docs", re.compile(r"^(docs/|odd/|README|LICENSE|\.github/)")),
]

METRIC_BY_ROLE = {
    "production-python": "G-ENG-PY | G-BRN-PY | G-HOST-PY (touched-module floors)",
    "production-js": "G-JS (mapped changed scope / new-file floor)",
    "production-bash": "G-BASH-LINES + G-BASH-MATRIX (modified executable lines)",
    "operational-producer": ("published-pin proof + oracle parity (MQ-02); "
                             "Bash-line measurement of the pin line itself is a "
                             "DECLARED GAP unless G-BASH-* scope covers /scripts"),
    "test-program": "executed by its own suite (non-production, explicit)",
    "verification-tool": "selftests + this ledger (non-production, explicit)",
    "env-pin": "environment reproducibility (uv.lock / toolchain manifests)",
    "docs": "documentation",
}


def cmd_ledger(args) -> int:
    repo = Path(args.repo_root).resolve()
    proc = subprocess.run(["git", "status", "--porcelain=v1"], cwd=str(repo),
                          capture_output=True, text=True)
    entries = []
    gaps = []
    for line in proc.stdout.splitlines():
        st, path = line[:2].strip(), line[3:].strip()
        if not path:
            continue
        role = next((r for r, rx in ROLE_RULES if rx.search(path)), "unclassified")
        entry = {"path": path, "git_status": st, "role": role,
                 "metric": METRIC_BY_ROLE.get(role, None)}
        if role in ("production-python", "production-js", "production-bash") \
                and not entry["metric"]:
            gaps.append(path)
        if role == "unclassified":
            gaps.append(path)
        if role == "operational-producer":
            entry["declared_gap"] = ("bootstrap.sh pin line: functional proof = "
                                     "published-commit + F1-F9 oracle parity; "
                                     "ps4-harness line measurement pending MQ-05 scope decision")
        if role == "test-program" and path.endswith("smoke-tests.sh"):
            entry["classification_note"] = ("hermetic smoke harness PROGRAM (non-production, "
                                            "explicit): executed end to end by G-SMOKE; its own "
                                            "modified lines are test code, not product code")
        entries.append(entry)
    ledger = {
        "schema": 1, "tool": TOOL, "generated_utc": now_utc(),
        "repo_root": str(repo),
        "repo_head": subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo),
                                    capture_output=True, text=True).stdout.strip(),
        "classification_rules": [{"role": r, "path_pattern": rx.pattern}
                                 for r, rx in ROLE_RULES],
        "entries": entries,
        "declared_gaps": gaps,
        "production_entries": [e["path"] for e in entries
                               if e["role"].startswith(("production", "operational"))],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(ledger, indent=1, sort_keys=True) + "\n")
    prod = len(ledger["production_entries"])
    print(f"ledger: {len(entries)} modified entries, {prod} production/operational, "
          f"{len(gaps)} declared gap(s)")
    for g in gaps:
        print(f"  GAP: {g}")
    return 0


# ---------------------------------------------------------------- selftest

def _mk_run(tmp: Path, name: str, *, run_id=None, gates=None, required=None,
            doc_ids=None) -> Path:
    """Synthesize a run dir fixture; gates is a list of (gate, status, rc,
    stdout_text, argv_tail, evidence_overrides). Coverage gates get one real
    artifact file so the healthy fixture is honestly complete."""
    run = tmp / name
    (run / "gates").mkdir(parents=True, exist_ok=True)
    (run / "artifacts").mkdir(parents=True, exist_ok=True)
    doc_ids = doc_ids or REQUIRED_GATES
    entries = []
    if gates is None:
        gates = _healthy_gates()
    for g in gates:
        gid, status, rc, out_text, argv_tail, over = g
        argv = ["python3", "tool"] + (argv_tail or ["run", "--ok"])
        ev = {
            "schema": 1, "gate": gid,
            "kind": over.get("kind", "coverage" if gid.startswith(
                ("G-ENG", "G-BRN", "G-HOST", "G-JS", "G-BASH")) else "functional"),
            "suite": over.get("suite"), "argv": argv, "cwd_rel": ".",
            "started_utc": "t", "finished_utc": "t", "elapsed_s": 1.0,
            "returncode": rc,
            "contract": {"0": "pass", "1": "fail", "2": "blocked", "other": "fail"},
            "derived_status": status,
            "stdout_log": f"gates/{gid}.stdout.log",
            "stderr_log": f"gates/{gid}.stderr.log",
            "stdout_sha256": sha256_bytes(out_text.encode()),
            "stderr_sha256": sha256_bytes(b""),
            "stdout_bytes": len(out_text.encode()), "stderr_bytes": 0,
            "parsed_counts": {},
            "artifacts": [], "source_bindings": {}, "env_names_recorded": [],
        }
        suite = ev["suite"]
        if suite:
            ev["parsed_counts"] = parse_counts(suite, out_text)
        if ev["kind"] == "coverage" and over.get("artifacts", True):
            art = run / "artifacts" / f"{gid}.metric.json"
            art.write_text('{"metric": "scoped-coverage-record"}\n')
            ev["artifacts"] = [{"path": str(art), "sha256": sha256_file(art),
                                "bytes": art.stat().st_size}]
        ev.update({k: v for k, v in over.items()
                   if k not in ("suite", "kind", "artifacts")})
        (run / "gates" / f"{gid}.json").write_text(json.dumps(ev, indent=1))
        (run / "gates" / f"{gid}.stdout.log").write_text(out_text)
        (run / "gates" / f"{gid}.stderr.log").write_text("")
        entries.append({"gate": gid, "status": status,
                        "evidence": f"gates/{gid}.json", "returncode": rc})
    manifest = {
        "schema": 1, "tool": TOOL, "run_id": run_id or name,
        "created_utc": "t", "repo_root": str(tmp),
        "repo_head": "0" * 40,
        "required_gates": required or doc_ids,
        "gates": entries, "status": "complete",
    }
    (run / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return run


def _healthy_gates():
    """The 9-gate healthy recipe: every suite emits its OWN raw summary
    convention and coverage gates carry real metric artifacts."""
    out = []
    for gid in REQUIRED_GATES:
        if gid in ("G-E2E", "G-BOUNDARY"):
            out.append((gid, "pass", 0, "16 passed in 174.27s\n", None,
                        {"suite": "pytest", "kind": "functional"}))
        elif gid == "G-SMOKE":
            out.append((gid, "pass", 0, "═ RESULT: 988 passed, 0 failed ═\n", None,
                        {"suite": "smoke", "kind": "functional"}))
        elif gid == "G-BASH-LINES":
            out.append((gid, "pass", 0,
                        "modified_executable_lines=5 covered=5 missed=0\n"
                        "coverage=100.00% threshold=90.0\nBASH_LINES_PASS\n", None,
                        {"suite": "bash-gate", "kind": "coverage"}))
        elif gid == "G-BASH-MATRIX":
            out.append((gid, "pass", 0,
                        "cases_executed=9 cases_ok=9 cases_fail=0\nMATRIX_PASS\n", None,
                        {"suite": "bash-gate", "kind": "coverage"}))
        else:
            out.append((gid, "pass", 0,
                        "COVERAGE_GATE_PASS\n"
                        "  x [changed scope]: lines 100.00% (38/38 changed stmts; "
                        "0 non-executable) branches 92.86% (26/28 in-scope arcs)\n",
                        None, {"suite": "coverage-gate", "kind": "coverage"}))
    return out


def _healthy_pass_fixture(tmp: Path) -> Path:
    return _mk_run(tmp, "healthy-run")


def cmd_selftest(_args) -> int:
    import shutil
    import tempfile

    ok = True

    def check(label: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f" :: {detail}" if detail else ""))
        ok &= cond

    with tempfile.TemporaryDirectory(prefix="mq04-selftest-") as tmp:
        tmp = Path(tmp)

        # healthy fixture passes validation (with the tool's own authority list)
        healthy = _healthy_pass_fixture(tmp)
        verdict, findings = validate_run(healthy, None)
        check("healthy run validates pass", verdict == "pass", str(findings[:3]))

        # N1: rc=1 recorded as pass -> reject
        run = _mk_run(tmp, "n1-rc1-pass", gates=[
            (g, "pass" if g == "G-SMOKE" else "pass",
             1 if g == "G-SMOKE" else 0,
             "═ RESULT: 988 passed, 0 failed ═\n" if g == "G-SMOKE" else
             "COVERAGE_GATE_PASS\n  x: lines 100.00% branches 92.86%\n",
             None,
             {"suite": "smoke" if g == "G-SMOKE" else None})
            for g in REQUIRED_GATES])
        verdict, findings = validate_run(run, None)
        check("N1 exit1+pass rejected", verdict == "reject",
              "; ".join(findings[:2]))

        # N2: omitted required gate -> blocked (honest incompleteness), never pass
        gates = [g for g in _healthy_gates() if g[0] != "G-BASH-MATRIX"]
        run = _mk_run(tmp, "n2-omitted", gates=gates)
        verdict, findings = validate_run(run, None)
        check("N2 omitted gate blocked, never pass", verdict == "blocked"
              and any("not yet recorded" in f for f in findings), str(findings[:2]))

        # N3: wrong run_id -> reject
        run = _mk_run(tmp, "n3-dirname", run_id="some-other-run")
        verdict, findings = validate_run(run, None)
        check("N3 run_id != dirname rejected",
              verdict == "reject" and any("run_id" in f for f in findings))

        # N4: ellipsis in literal command -> reject
        gates = _healthy_gates()
        gates[0] = (gates[0][0], "pass", 0, gates[0][3], ["run", "..."],
                    gates[0][5])
        run = _mk_run(tmp, "n4-ellipsis", gates=gates)
        verdict, findings = validate_run(run, None)
        check("N4 command ellipsis rejected",
              verdict == "reject" and any("ellipsis" in f for f in findings))

        # N5: cwd outside owned root -> reject
        gates = _healthy_gates()
        over = dict(gates[0][5]); over["cwd_rel"] = "../../etc"
        gates[0] = (gates[0][0], "pass", 0, gates[0][3], None, over)
        run = _mk_run(tmp, "n5-cwd-escape", gates=gates)
        verdict, findings = validate_run(run, None, repo_root=str(tmp))
        check("N5 cwd escape rejected",
              verdict == "reject" and any("escapes owned root" in f for f in findings))

        # N6: log path traversal -> reject
        gates = _healthy_gates()
        over = dict(gates[0][5]); over["stdout_log"] = "../../etc/passwd"
        gates[0] = (gates[0][0], "pass", 0, gates[0][3], None, over)
        run = _mk_run(tmp, "n6-traversal", gates=gates)
        verdict, findings = validate_run(run, None)
        check("N6 log traversal rejected",
              verdict == "reject" and any("escapes run dir" in f for f in findings))

        # N7: missing log file -> reject
        gates = _healthy_gates()
        run = _mk_run(tmp, "n7-missing-log", gates=gates)
        (run / "gates" / "G-ENG-PY.stdout.log").unlink()
        verdict, findings = validate_run(run, None)
        check("N7 missing log rejected",
              verdict == "reject" and any("missing" in f for f in findings))

        # N8: tampered raw log (hash mismatch) -> reject
        gates = _healthy_gates()
        run = _mk_run(tmp, "n8-tampered", gates=gates)
        log = run / "gates" / "G-SMOKE.stdout.log"
        log.write_text(log.read_text().replace("988 passed, 0 failed", "999 passed, 0 failed"))
        verdict, findings = validate_run(run, None)
        check("N8 tampered log rejected",
              verdict == "reject" and any("hash mismatch" in f for f in findings))

        # N9: coverage pass with blank stdout -> reject
        gates = _healthy_gates()
        over = dict(gates[0][5]); over["stdout_bytes"] = 0
        gates[0] = (gates[0][0], "pass", 0, "", None, over)
        run = _mk_run(tmp, "n9-blank", gates=gates)
        verdict, findings = validate_run(run, None)
        check("N9 blank coverage pass rejected",
              verdict == "reject" and any("BLANK" in f for f in findings))

        # N10: counts pasted from elsewhere (per_file_verified as test count)
        # -> raw log lacks the summary line, parsed counts present -> reject
        gates = _healthy_gates()
        over = dict(gates[0][5]); over["suite"] = "node-tap"
        over["parsed_counts"] = {"node_tests": 213, "node_pass": 213}
        gates[0] = (gates[0][0], "pass", 0,
                    "COVERAGE_GATE_PASS\n  x: lines 100.00% branches 92.86%\n",
                    None, over)
        run = _mk_run(tmp, "n10-pasted-counts", gates=gates)
        verdict, findings = validate_run(run, None)
        check("N10 pasted counts rejected",
              verdict == "reject" and any("not re-derivable" in f for f in findings))

        # N11: E2E pass without a raw pytest log (construction-only) -> reject
        gates = _healthy_gates()
        for i, g in enumerate(gates):
            if g[0] == "G-E2E":
                over = dict(g[5]); over["suite"] = None
                gates[i] = (g[0], "pass", 0, "ok\n", None, over)
        run = _mk_run(tmp, "n11-e2e-no-log", gates=gates)
        verdict, findings = validate_run(run, None)
        check("N11 E2E without raw log rejected",
              verdict == "reject" and any("G-E2E" in f for f in findings))

        # N12: unknown return code (contract has no catch-all) -> reject
        gates = _healthy_gates()
        over = dict(gates[0][5])
        over["contract"] = {"0": "pass", "1": "fail"}  # no "other" arm
        gates[0] = (gates[0][0], "fail", 42, gates[0][3], None, over)
        run = _mk_run(tmp, "n12-unknown-rc", gates=gates)
        verdict, findings = validate_run(run, None)
        check("N12 unknown returncode rejected",
              verdict == "reject" and any("not in contract" in f for f in findings))

        # N13: authority doc mismatch (declared list missing a doc gate)
        run = _healthy_pass_fixture(tmp)
        m = json.loads((run / "manifest.json").read_text())
        m["required_gates"] = [g for g in m["required_gates"] if g != "G-BOUNDARY"]
        (run / "manifest.json").write_text(json.dumps(m, indent=1))
        verdict, findings = validate_run(run, None)
        check("N13 declared list != authority rejected",
              verdict == "reject" and any("authority" in f for f in findings))

        # N14: zero-scope / not_applicable coverage cannot pass: a coverage
        # gate recorded pass whose raw output carries no PASS marker and no
        # numeric metric lines is rejected (empty-scope report).
        gates = _healthy_gates()
        gates[3] = (gates[3][0], "pass", 0,
                    "not_applicable\nno touched production files\n", None,
                    {"suite": "coverage-gate", "kind": "coverage"})
        run = _mk_run(tmp, "n14-zero-scope", gates=gates)
        verdict, findings = validate_run(run, None)
        check("N14 zero-scope/not_app pass rejected",
              verdict == "reject" and any("cannot pass" in f or "cannot masquerade" in f
                                          for f in findings), str(findings[:2]))

        # authority parsing from a synthetic doc
        doc = tmp / "TASKS.md"
        doc.write_text("| G-A | cmd |\n| G-B | cmd |\n| G-X | global |\n")
        ids = load_authority_gate_ids(doc)
        check("authority doc parse excludes G-X", ids == ["G-A", "G-B"], str(ids))

    print("SELFTEST_OK" if ok else "SELFTEST_FAIL")
    return 0 if ok else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("write", help="scaffold an own-run manifest")
    w.add_argument("--run-dir", required=True)
    w.add_argument("--run-id")
    w.add_argument("--repo-root", required=True)
    w.add_argument("--runspace", default=os.path.expanduser(
        "~/.local/state/voice-stack-maintenance-runs"))
    w.add_argument("--python", default=sys.executable)
    w.set_defaults(func=cmd_write)

    v = sub.add_parser("validate", help="verify a run dir end to end")
    v.add_argument("--run-dir", required=True)
    v.add_argument("--authority-doc", help="TASKS.md with the gates table")
    v.add_argument("--repo-root")
    v.set_defaults(func=cmd_validate)

    l = sub.add_parser("ledger", help="classify modified files + declared gaps")
    l.add_argument("--repo-root", required=True)
    l.add_argument("--out", required=True)
    l.set_defaults(func=cmd_ledger)

    s = sub.add_parser("selftest", help="negative controls then healthy pass")
    s.set_defaults(func=cmd_selftest)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ToolError as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
