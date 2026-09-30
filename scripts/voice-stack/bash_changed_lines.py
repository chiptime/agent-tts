#!/usr/bin/env python3
"""Bash changed-lines coverage gate (voice-stack VS0.8/VS0.7, contract D9/T12.2).

Denominator = MODIFIED EXECUTABLE Bash lines (baseline-snapshot vs
candidate-snapshot, compared through blob bytes, never git diffs).
Numerator = those lines reported covered by the collector.

Collector (owner decision 2026-09-30): native bash xtrace. kcov 42 (pinned
prebuilt) and kcov 43 (brew) both fail to trace bash 5.2 on Ubuntu 24.04
(their execve-redirector injection layer silently stops rewriting child
execs), while bash itself traces perfectly. The gate therefore runs the
harness under ``SHELLOPTS=xtrace PS4='+${LINENO}@${BASH_SOURCE}@'`` —
xtrace propagates to child bash scripts — and parses the stderr event
stream ``+<lineno>@<abspath>@``. Coverage evidence is the set of executed
lines observed during the harness run; absence of a modified line from the
stream means it was not executed (never "unknown").

Pass iff coverage >= threshold (default 90) or denominator == 0 (explicit
not_applicable — never a fake 100%).

Exit codes: 0 pass · 1 FAIL · 2 blocked.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys

SHEBANG_RE = re.compile(r"^#!")
PS4_EVENT_RE = re.compile(r"^\++(\d+)@([^@]+)@")
PS4 = "+${LINENO}@${BASH_SOURCE}@"

# Structural-only lines never fire xtrace (they contain no simple command);
# counting them would poison the denominator forever.
STRUCTURAL_ONLY = {"else", "fi", "esac", "done", ";;", "then", "do", "{", "}",
                   "}", "!", "in)", "&", ")"}


def die_blocked(reason: str) -> int:
    print(f"BLOCKED: {reason}")
    return 2


# -- snapshot plumbing (shared shape with bash_matrix.py) ---------------------


def load_scope(snapshot_path: str, gate: str) -> dict[str, dict]:
    try:
        with open(snapshot_path, "r", encoding="utf-8") as fh:
            snap = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(die_blocked(f"unreadable snapshot {snapshot_path}: {exc}"))
    files = snap.get("scopes", {}).get(gate, {}).get("files", [])
    return {entry["path"]: entry for entry in files}


def read_blob(snapshot_path: str, entry: dict) -> bytes:
    blob_dir = os.path.join(os.path.dirname(os.path.abspath(snapshot_path)), "blobs")
    ref = entry.get("blob_ref")
    if ref and os.path.isfile(os.path.join(blob_dir, ref)):
        with open(os.path.join(blob_dir, ref), "rb") as fh:
            return fh.read()
    with open(entry["path"], "rb") as fh:
        return fh.read()


def is_executable_line(text: str, is_first_line: bool) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.startswith("#"):
        return False
    if is_first_line and SHEBANG_RE.match(stripped):
        return False
    if stripped in STRUCTURAL_ONLY:
        return False
    return True


def modified_executable_lines(baseline_path: str, candidate_path: str, gate: str):
    """Yield (repo_path, lineno) for every added/changed executable line."""
    base = load_scope(baseline_path, gate)
    cand = load_scope(candidate_path, gate)
    for path, entry in sorted(cand.items()):
        old = base.get(path)
        if old is not None and old.get("sha256") == entry.get("sha256"):
            continue
        new_lines = read_blob(candidate_path, entry).decode("utf-8", "replace").splitlines()
        old_lines = (read_blob(baseline_path, old).decode("utf-8", "replace").splitlines()
                     if old else [])
        matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
        for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
            if tag in ("insert", "replace"):
                for lineno in range(j1 + 1, j2 + 1):
                    if is_executable_line(new_lines[lineno - 1], lineno == 1):
                        yield path, lineno


# -- PS4 xtrace collector -------------------------------------------------------


def ps4_collect(command_argv: list[str], cwd: str) -> dict[str, set[int]]:
    """Run a harness under native xtrace; return {abs-or-relative path: lines}.

    SHELLOPTS=xtrace propagates tracing into child bash scripts. The event
    stream lives on stderr; the harness's own protocol output (stdout) is
    untouched.
    """
    env = dict(os.environ)
    env["SHELLOPTS"] = "xtrace"
    env["PS4"] = PS4
    proc = subprocess.run(command_argv, cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=3600)
    covered: dict[str, set[int]] = {}
    for line in proc.stderr.splitlines():
        match = PS4_EVENT_RE.match(line)
        if match:
            lineno, path = int(match.group(1)), match.group(2)
            if path:
                covered.setdefault(path, set()).add(lineno)
    return covered


def _normalize_path(path: str, repo_root: str) -> str:
    absolute = os.path.abspath(os.path.join(repo_root, path))
    return os.path.relpath(absolute, repo_root)


def _match_collected(collected: dict[str, set[int]], repo_path: str,
                     repo_root: str) -> tuple[str, set[int]] | None:
    """Find the collected entry matching a repo-relative scope path."""
    normalized = repo_path
    candidates = {normalized, os.path.join(repo_root, normalized),
                  os.path.abspath(os.path.join(repo_root, normalized))}
    for collected_path in list(collected):
        absolute = os.path.abspath(collected_path)
        for candidate in candidates:
            if absolute == os.path.abspath(candidate) or \
               absolute.endswith("/" + repo_path) or collected_path.endswith(repo_path):
                return collected_path, collected[collected_path]
    return None


# -- legacy kcov / lcov parsing (compat; superseded by the PS4 collector) ------


def parse_coverage_dir(coverage_dir: str) -> dict[str, dict[int, bool]]:
    per_file: dict[str, dict[int, bool]] = {}
    if not os.path.isdir(coverage_dir):
        raise SystemExit(die_blocked(f"coverage dir not found: {coverage_dir}"))
    for root, _dirs, files in os.walk(coverage_dir):
        for fname in files:
            fpath = os.path.join(root, fname)
            if fname.endswith(".json"):
                try:
                    with open(fpath, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                except (OSError, json.JSONDecodeError):
                    continue
                for fentry in data.get("files", []):
                    cov = per_file.setdefault(fentry.get("file", "?"), {})
                    for ln in fentry.get("covered_lines", []):
                        cov[int(ln)] = True
                    for ln in fentry.get("uncovered_lines", []):
                        cov.setdefault(int(ln), False)
            elif fname.endswith(".info"):
                cur: str | None = None
                try:
                    with open(fpath, "r", encoding="utf-8") as fh:
                        for line in fh:
                            if line.startswith("SF:"):
                                cur = line[3:].strip()
                                per_file.setdefault(cur, {})
                            elif line.startswith("DA:") and cur:
                                lineno, hits, *_ = line[3:].strip().split(",")
                                per_file[cur][int(lineno)] = int(hits) > 0
                except OSError:
                    continue
    return per_file


# -- check ----------------------------------------------------------------------


def cmd_check(args) -> int:
    lines = list(modified_executable_lines(args.baseline_snapshot,
                                           args.candidate_snapshot, "G-BASH-LINES"))
    if not lines:
        print("not_applicable")
        print("modified executable lines: 0 — nothing to gate")
        return 0

    repo_root = os.path.abspath(args.repo_root) if args.repo_root else os.getcwd()

    if args.ps4_harness:
        harness = args.ps4_harness
        collected = ps4_collect(["bash", harness], repo_root)
        covered, missed, unseen_files = [], [], set()
        for path, lineno in lines:
            match = _match_collected(collected, path, repo_root)
            if match is None:
                unseen_files.add(path)
                missed.append(f"{path}:{lineno}")
            else:
                (_cp, line_set) = match
                (covered if lineno in line_set else missed).append(f"{path}:{lineno}")
    else:
        per_file = parse_coverage_dir(args.coverage_dir or "")
        covered, missed, unmatched = [], [], []
        for path, lineno in lines:
            cov_path = next((c for c in per_file
                             if c == path or c.endswith(path) or path.endswith(c)), None)
            if cov_path is None:
                unmatched.append(f"{path}:{lineno}")
                continue
            (covered if per_file[cov_path].get(lineno, False) else missed).append(
                f"{path}:{lineno}")
        if unmatched:
            return die_blocked(
                f"no coverage trace for {len(unmatched)} modified lines "
                f"(first: {unmatched[0]}) — coverage incomplete, refusing to score")
        unseen_files = set()

    pct = 100.0 * len(covered) / len(lines)
    print(f"modified_executable_lines={len(lines)} covered={len(covered)} missed={len(missed)}")
    print(f"collector={'ps4-xtrace' if args.ps4_harness else 'kcov-dir'}")
    if unseen_files:
        for f in sorted(unseen_files)[:10]:
            print(f"UNSEEN_FILE {f} (never executed during the harness run)")
    print(f"coverage={pct:.2f}% threshold={args.threshold}")
    for miss in missed[:50]:
        print(f"MISSED {miss}")
    if missed and len(missed) > 50:
        print(f"... and {len(missed) - 50} more missed")
    if pct >= args.threshold:
        print("BASH_LINES_PASS")
        return 0
    print("BASH_LINES_FAIL")
    return 1


# -- selftest (VS0.7 gate: honesty check via the PS4 collector) ------------------


def cmd_selftest(args) -> int:
    run_dir = os.path.join(args.run_dir, "bash-lines-selftest")
    tree = os.path.join(run_dir, "tree")
    os.makedirs(tree, exist_ok=True)
    script = os.path.join(tree, "subject.sh")

    def write_subject() -> None:
        # Branches on SEPARATE lines: an uncovered branch must show up as a
        # missed modified executable line (a one-line if/else would always
        # count as covered and prove nothing).
        body = ["#!/usr/bin/env bash", "set -euo pipefail",
                'echo always-1', 'echo always-2', 'echo always-3', 'echo always-4',
                'echo always-5', 'echo always-6', 'echo always-7', 'echo always-8',
                'if [[ "${1:-}" == "--flag" ]]; then',
                '  echo flagged-a',
                '  echo flagged-b',
                'else',
                '  echo unflagged',
                'fi']
        with open(script, "w") as fh:
            fh.write("\n".join(body) + "\n")
        os.chmod(script, 0o755)

    def mini_snapshot(path: str, files: list[dict]) -> None:
        snap = {"schema": "1", "ts": "selftest", "repo": {}, "package": {"files": []},
                "scopes": {"G-BASH-LINES": {"files": files}}, "env": {}, "binding": "s"}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(snap, fh, indent=1)

    def entry_for(relpath: str) -> dict:
        blob_dir = os.path.join(run_dir, "blobs")
        os.makedirs(blob_dir, exist_ok=True)
        data = open(os.path.join(run_dir, "tree", relpath), "rb").read()
        sha = hashlib.sha256(data).hexdigest()
        with open(os.path.join(blob_dir, sha), "wb") as fh:
            fh.write(data)
        return {"path": relpath, "sha256": sha, "mode": "0o755", "blob_ref": sha}

    write_subject()
    base_snap = os.path.join(run_dir, "baseline.json")
    cand_snap = os.path.join(run_dir, "candidate.json")
    mini_snapshot(base_snap, [])
    mini_snapshot(cand_snap, [entry_for("subject.sh")])

    def check_with_harness(harness_relpath: str) -> int:
        return subprocess.run(
            [sys.executable, os.path.abspath(__file__),
             "--baseline-snapshot", base_snap, "--candidate-snapshot", cand_snap,
             "--ps4-harness", os.path.join(run_dir, harness_relpath),
             "--repo-root", run_dir],
            capture_output=True, text=True).returncode

    # Phase 1: harness runs the subject WITHOUT the flag: the else-branch
    # modified line stays uncovered -> the checker MUST reject (exit 1).
    with open(os.path.join(run_dir, "harness1.sh"), "w") as fh:
        fh.write('#!/usr/bin/env bash\n"%s"\n' % script)
    os.chmod(os.path.join(run_dir, "harness1.sh"), 0o755)
    rc_bad = check_with_harness("harness1.sh")
    print(f"selftest phase1 (uncovered modified line must FAIL) rc={rc_bad} (expect 1)")
    if rc_bad != 1:
        print("SELFTEST_FAIL: uncovered modified line was not rejected")
        return 1

    # Phase 2: harness exercises BOTH branches -> every modified executable
    # line is covered -> the checker MUST pass (exit 0).
    with open(os.path.join(run_dir, "harness2.sh"), "w") as fh:
        fh.write('#!/usr/bin/env bash\n"%s" --flag\n"%s"\n' % (script, script))
    os.chmod(os.path.join(run_dir, "harness2.sh"), 0o755)
    rc_good = check_with_harness("harness2.sh")
    print(f"selftest phase2 (all modified lines covered must PASS) rc={rc_good} (expect 0)")
    if rc_good != 0:
        print("SELFTEST_FAIL: fully covered subject did not pass")
        return 1
    print("SELFTEST_OK")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-snapshot")
    parser.add_argument("--candidate-snapshot")
    parser.add_argument("--coverage-dir")
    parser.add_argument("--ps4-harness",
                        help="run this bash harness under native xtrace and collect")
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--threshold", type=float, default=90.0)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--kcov", default="kcov",
                        help="ignored: kept for CLI compat; collector is PS4 xtrace")
    parser.add_argument("--run-dir")
    args = parser.parse_args(argv)
    if args.selftest:
        if not args.run_dir:
            return die_blocked("--selftest requires --run-dir")
        return cmd_selftest(args)
    if not (args.baseline_snapshot and args.candidate_snapshot
            and (args.ps4_harness or args.coverage_dir)):
        return die_blocked("check mode requires --baseline-snapshot, "
                           "--candidate-snapshot and (--ps4-harness | --coverage-dir)")
    return cmd_check(args)


if __name__ == "__main__":
    sys.exit(main())
