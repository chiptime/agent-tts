#!/usr/bin/env python3
"""Coverage comparator gate (voice-stack VS0.9, contract D4/T12.1).

Compares a candidate coverage report (coverage.py JSON or Node LCOV) against
the immutable baseline on TOPPED production modules:

  - scope selection: FULL production modules touched per baseline-snapshot vs
    candidate-snapshot diff => >= 90% lines AND >= 90% branches per module;
  - plus TOTAL no-regression of the component against its baseline;
  - component with no touched production files => explicit not_applicable
    (never a fake 100%);
  - --check-env validates tool compatibility (blocked, never silent degrade).

Exit codes: 0 pass · 1 FAIL · 2 blocked.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

THRESHOLD = 90.0

COMPONENT_SCOPE = {
    ("python", "engine"): "G-ENG-PY",
    ("python", "brain"): "G-BRN-PY",
    ("python", "host"): "G-HOST-PY",
    ("js", "pwa"): "G-JS",
}


def die_blocked(reason: str) -> int:
    print(f"BLOCKED: {reason}")
    return 2


# -- inputs --------------------------------------------------------------------


def load_scope_paths(snapshot_path: str, gate: str) -> dict[str, str]:
    """{repo-relative path: sha256} for one gate scope of a snapshot."""
    try:
        with open(snapshot_path, "r", encoding="utf-8") as fh:
            snap = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(die_blocked(f"unreadable snapshot {snapshot_path}: {exc}"))
    return {e["path"]: e.get("sha256", "")
            for e in snap.get("scopes", {}).get(gate, {}).get("files", [])}


DEFAULT_EXCLUSIONS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "coverage-exclusions.json")


def load_exclusions(path: str | None, component: str) -> dict[str, str]:
    """{repo path: reason} for ONE component. Explicit per-file inventory only
    (T12.1): a missing reason is a blocked result, never a silent skip."""
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            entries = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(die_blocked(f"unreadable exclusions {path}: {exc}"))
    out: dict[str, str] = {}
    for entry in entries:
        if entry.get("component") != component:
            continue
        reason = str(entry.get("reason") or "").strip()
        if not entry.get("path") or not reason:
            raise SystemExit(die_blocked(
                f"exclusion without path/reason in {path}: {entry!r}"))
        out[entry["path"]] = reason
    return out


def touched_production_files(baseline_path: str, candidate_path: str, gate: str,
                             lang: str) -> list[str]:
    base = load_scope_paths(baseline_path, gate)
    cand = load_scope_paths(candidate_path, gate)
    if lang == "python":
        is_prod = lambda p: p.endswith(".py") and "/tests/" not in f"/{p}" and (
            "/src/" in f"/{p}" or "/lib/" in f"/{p}")
    else:
        is_prod = lambda p: "/static/" in p and p.endswith(".js")
    return sorted(p for p in cand
                  if is_prod(p) and base.get(p) != cand[p])


# -- coverage loading -----------------------------------------------------------


def load_coverage_py(path: str) -> dict[str, dict[str, float]]:
    """coverage.py JSON -> {file: {lines_pct, branches_pct}}."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(die_blocked(f"unreadable coverage json {path}: {exc}"))
    out: dict[str, dict[str, float]] = {}
    for fpath, finfo in data.get("files", {}).items():
        summary = finfo.get("summary", {})
        num_stmt = summary.get("num_statements", 0)
        covered = summary.get("covered_lines", 0)
        lines_pct = 100.0 * covered / num_stmt if num_stmt else 0.0
        num_br = summary.get("num_branches", 0)
        cov_br = summary.get("covered_branches", 0)
        branches_pct = 100.0 * cov_br / num_br if num_br else 0.0
        out[fpath] = {"lines_pct": lines_pct, "branches_pct": branches_pct,
                      "lines": (covered, num_stmt), "branches": (cov_br, num_br)}
    return out


def load_coverage_lcov(path: str) -> dict[str, dict[str, float]]:
    """lcov .info -> {file: {lines_pct, branches_pct}} (DA/LH/LF, BRDA/BRF/BRH)."""
    out: dict[str, dict[str, float]] = {}
    cur: str | None = None
    da: dict[int, int] = {}
    brda: dict[tuple, int] = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("SF:"):
                    if cur is not None:
                        out[cur] = _lcov_entry(da, brda)
                    cur, da, brda = line[3:].strip(), {}, {}
                elif line == "end_of_record":
                    if cur is not None:
                        out[cur] = _lcov_entry(da, brda)
                    cur = None
                elif line.startswith("DA:") and cur:
                    lineno, hits, *_ = line[3:].split(",")
                    da[int(lineno)] = int(hits)
                elif line.startswith("BRDA:") and cur:
                    parts = line[5:].split(",")
                    if len(parts) >= 4:
                        taken = parts[3]
                        brda[(parts[0], parts[1], parts[2])] = \
                            0 if taken in ("-", "0") else max(1, int(taken) if taken.isdigit() else 1)
    except OSError as exc:
        raise SystemExit(die_blocked(f"unreadable lcov {path}: {exc}"))
    return out


def _lcov_entry(da: dict[int, int], brda: dict[tuple, int]) -> dict[str, float]:
    total = len(da)
    hit = sum(1 for v in da.values() if v > 0)
    lines_pct = 100.0 * hit / total if total else 0.0
    br_total = len(brda)
    br_hit = sum(1 for v in brda.values() if v > 0)
    branches_pct = 100.0 * br_hit / br_total if br_total else 0.0
    return {"lines_pct": lines_pct, "branches_pct": branches_pct,
            "lines": (hit, total), "branches": (br_hit, br_total)}


def match_file(coverage: dict, repo_path: str) -> str | None:
    if repo_path in coverage:
        return repo_path
    for cov_path in coverage:
        if cov_path.endswith(repo_path) or repo_path.endswith(cov_path):
            return cov_path
    return None


# -- totals --------------------------------------------------------------------


def totals(coverage: dict) -> tuple[int, int, int, int]:
    lc = ls = bc = bt = 0
    for entry in coverage.values():
        c, t = entry["lines"]
        lc += c
        ls += t
        c, t = entry["branches"]
        bc += c
        bt += t
    return lc, ls, bc, bt


# -- check mode -----------------------------------------------------------------


def cmd_check(args) -> int:
    gate = COMPONENT_SCOPE.get((args.lang, args.component))
    if gate is None:
        return die_blocked(f"unknown lang/component pair: {args.lang}/{args.component}")
    if args.check_env:
        return cmd_check_env()

    touched = touched_production_files(args.baseline_snapshot, args.candidate_snapshot,
                                       gate, args.lang)
    if not touched:
        print("not_applicable")
        print(f"no touched production files for {args.component} ({args.lang}) — "
              "diff confirms zero changes; this is NOT a pass and NOT 100%")
        return 0

    exclusions = load_exclusions(args.exclusions, args.component)
    excluded = [p for p in touched if p in exclusions]
    touched = [p for p in touched if p not in exclusions]
    for repo_path in excluded:
        print(f"EXCLUDED {repo_path}: {exclusions[repo_path]}")
    if not touched:
        print("excluded_only")
        print("every touched production file is an explicit, justified glue "
              "exclusion — NOT a coverage pass; it needs its other evidence")
        return 0

    candidate = (load_coverage_py(args.coverage_json) if args.lang == "python"
                 else load_coverage_lcov(args.coverage_json))
    baseline = (load_coverage_py(args.baseline_coverage) if args.lang == "python"
                else load_coverage_lcov(args.baseline_coverage))

    problems: list[str] = []
    for repo_path in touched:
        cov_path = match_file(candidate, repo_path)
        if cov_path is None:
            problems.append(f"BLOCKED: no candidate coverage record for {repo_path}")
            continue
        entry = candidate[cov_path]
        if entry["lines"][1] == 0:
            problems.append(f"BLOCKED: zero statements measured for {repo_path}")
            continue
        if entry["lines_pct"] < THRESHOLD:
            problems.append(
                f"FAIL lines {entry['lines_pct']:.2f}% < {THRESHOLD} for {repo_path}")
        if entry["branches"][1] == 0:
            problems.append(
                f"BLOCKED: no branch data for {repo_path} (branch measurement "
                "unavailable — module-floor policy requires explicit decision)")
        elif entry["branches_pct"] < THRESHOLD:
            problems.append(
                f"FAIL branches {entry['branches_pct']:.2f}% < {THRESHOLD} for {repo_path}")

    cl, cs, cb, cbt = totals(candidate)
    bl, bs, bb, bbt = totals(baseline)
    cand_total = 100.0 * cl / cs if cs else 0.0
    base_total = 100.0 * bl / bs if bs else 0.0
    print(f"touched_production_files={len(touched)}")
    for repo_path in touched:
        cov_path = match_file(candidate, repo_path)
        if cov_path:
            e = candidate[cov_path]
            print(f"  {repo_path}: lines {e['lines_pct']:.2f}% "
                  f"branches {e['branches_pct']:.2f}%")
    print(f"total candidate {cand_total:.2f}% vs baseline {base_total:.2f}%")
    if cand_total < base_total - 1e-9:
        problems.append(
            f"FAIL total regression: candidate {cand_total:.2f}% < baseline {base_total:.2f}%")

    hard = [p for p in problems if p.startswith("BLOCKED:")]
    if hard:
        return die_blocked("; ".join(p[8:] for p in hard))
    if problems:
        for p in problems:
            print(p)
        print("COVERAGE_GATE_FAIL")
        return 1
    print("COVERAGE_GATE_PASS")
    return 0


def cmd_check_env() -> int:
    problems = []
    try:
        node = subprocess.run(["node", "--version"], capture_output=True,
                              text=True, timeout=15)
        version = node.stdout.strip().lstrip("v")
        major = int(version.split(".")[0]) if version else 0
        if major < 20:
            problems.append(f"node {version or '?'} < 20 (no LCOV reporter guarantee)")
        print(f"node={version or '?'}")
    except (OSError, subprocess.SubprocessError):
        print("node=absent")
        problems.append("node not found")
    print(f"python={sys.version.split()[0]}")
    if problems:
        return die_blocked("; ".join(problems))
    print("ENV_OK")
    return 0


# -- selftest (numeric honesty) ---------------------------------------------------


def _synthetic_coverage(tmp: str, name: str, lines_pct: float, branches_pct: float,
                        stmts: int = 10000, branches: int = 10) -> str:
    """coverage.py-style JSON for ONE module (engine/src/fake/mod.py) at exact
    percentages. stmts=10000 so 89.99% and 90.00% are exactly representable."""
    covered = round(stmts * lines_pct / 100.0)
    cov_br = round(branches * branches_pct / 100.0)
    data = {"meta": {"format": 3}, "files": {
        "engine/src/fake/mod.py": {
            "executed_lines": list(range(1, covered + 1)),
            "missing_lines": list(range(covered + 1, stmts + 1)),
            "summary": {
                "covered_lines": covered, "num_statements": stmts,
                "percent_covered": lines_pct,
                "covered_branches": cov_br, "num_branches": branches,
                "missing_branches": branches - cov_br}}}}
    path = os.path.join(tmp, f"{name}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return path


def _synthetic_snapshot(tmp: str, name: str, files: list[dict]) -> str:
    snap = {"schema": "1", "ts": "selftest", "repo": {}, "package": {"files": []},
            "scopes": {"G-ENG-PY": {"files": files}}, "env": {}, "binding": "s"}
    path = os.path.join(tmp, f"{name}-snapshot.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(snap, fh)
    return path


def cmd_selftest(_args) -> int:
    ok = True
    with tempfile.TemporaryDirectory(prefix="vs09-selftest-") as tmp:
        base_snap = _synthetic_snapshot(tmp, "base", [])
        # touched production module (src/... .py), changed vs baseline
        cand_snap = _synthetic_snapshot(tmp, "cand", [
            {"path": "engine/src/fake/mod.py", "sha256": "aaa", "mode": "0o644"}])

        # 89.99% lines => FAIL (exit 1) — the honesty fixture.
        cov_8999 = _synthetic_coverage(tmp, "m8999", 89.99, 100.0)
        base_cov = _synthetic_coverage(tmp, "base", 50.0, 50.0)
        rc = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--lang", "python",
             "--component", "engine", "--baseline-snapshot", base_snap,
             "--candidate-snapshot", cand_snap, "--coverage-json", cov_8999,
             "--baseline-coverage", base_cov],
            capture_output=True, text=True).returncode
        print(f"selftest 89.99% lines rc={rc} (expect 1)")
        ok &= rc == 1

        # exactly 90.0% lines AND 90.0% branches => PASS (exit 0).
        cov_90 = _synthetic_coverage(tmp, "m90", 90.0, 90.0)
        rc = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--lang", "python",
             "--component", "engine", "--baseline-snapshot", base_snap,
             "--candidate-snapshot", cand_snap, "--coverage-json", cov_90,
             "--baseline-coverage", base_cov],
            capture_output=True, text=True).returncode
        print(f"selftest 90.0% lines+branches rc={rc} (expect 0)")
        ok &= rc == 0

        # no touched production files => not_applicable (exit 0, explicit).
        rc = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--lang", "python",
             "--component", "engine", "--baseline-snapshot", base_snap,
             "--candidate-snapshot", base_snap, "--coverage-json", cov_90,
             "--baseline-coverage", base_cov],
            capture_output=True, text=True)
        out = rc.stdout
        print(f"selftest not_applicable rc={rc.returncode} out_has_marker="
              f"{'not_applicable' in out} (expect 0/True)")
        ok &= rc.returncode == 0 and "not_applicable" in out

        # total regression => FAIL even with module percentages fine.
        base_high = _synthetic_coverage(tmp, "basehigh", 99.0, 99.0)
        rc = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--lang", "python",
             "--component", "engine", "--baseline-snapshot", base_snap,
             "--candidate-snapshot", cand_snap, "--coverage-json", cov_90,
             "--baseline-coverage", base_high],
            capture_output=True, text=True).returncode
        print(f"selftest total-regression rc={rc} (expect 1)")
        ok &= rc == 1

        # host component: a touched module under hosts/.../lib/ is PRODUCTION
        # (regression: it was once reported not_applicable, a false pass).
        host_snap = os.path.join(tmp, "host-snapshot.json")
        with open(host_snap, "w", encoding="utf-8") as fh:
            json.dump({"schema": "1", "scopes": {"G-HOST-PY": {"files": [
                {"path": "hosts/herdr/tts-plugin/lib/mod.py", "sha256": "aaa", "mode": "0o644"}]}}}, fh)
        empty_host = os.path.join(tmp, "host-empty.json")
        with open(empty_host, "w", encoding="utf-8") as fh:
            json.dump({"schema": "1", "scopes": {"G-HOST-PY": {"files": []}}}, fh)
        host_cov = os.path.join(tmp, "host-cov.json")
        with open(host_cov, "w", encoding="utf-8") as fh:
            json.dump({"files": {"lib/mod.py": {"summary": {
                "covered_lines": 8999, "num_statements": 10000,
                "covered_branches": 10, "num_branches": 10}}}}, fh)
        rc = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--lang", "python",
             "--component", "host", "--baseline-snapshot", empty_host,
             "--candidate-snapshot", host_snap, "--coverage-json", host_cov,
             "--baseline-coverage", host_cov],
            capture_output=True, text=True).returncode
        print(f"selftest host lib module touched, 89.99% rc={rc} (expect 1, never not_applicable)")
        ok &= rc == 1

        # explicit glue exclusion: skipped + printed; the rest is still judged.
        pwa_base = os.path.join(tmp, "pwa-base.json")
        with open(pwa_base, "w", encoding="utf-8") as fh:
            json.dump({"schema": "1", "scopes": {"G-JS": {"files": []}}}, fh)
        pwa_snap = os.path.join(tmp, "pwa-snap.json")
        with open(pwa_snap, "w", encoding="utf-8") as fh:
            json.dump({"schema": "1", "scopes": {"G-JS": {"files": [
                {"path": "x/static/app.js", "sha256": "a", "mode": "0o644"}]}}}, fh)
        lcov = os.path.join(tmp, "pwa.lcov")
        with open(lcov, "w", encoding="utf-8") as fh:
            fh.write("SF:x/static/other.js\nDA:1,1\nend_of_record\n")
        good_inv = os.path.join(tmp, "inv-good.json")
        with open(good_inv, "w", encoding="utf-8") as fh:
            json.dump([{"component": "pwa", "path": "x/static/app.js", "reason": "browser IIFE"}], fh)
        bad_inv = os.path.join(tmp, "inv-bad.json")
        with open(bad_inv, "w", encoding="utf-8") as fh:
            json.dump([{"component": "pwa", "path": "x/static/app.js", "reason": " "}], fh)
        pwa_cmd = [sys.executable, os.path.abspath(__file__), "--lang", "js",
                   "--component", "pwa", "--baseline-snapshot", pwa_base,
                   "--candidate-snapshot", pwa_snap, "--coverage-json", lcov,
                   "--baseline-coverage", lcov]
        res = subprocess.run(pwa_cmd + ["--exclusions", good_inv], capture_output=True, text=True)
        print(f"selftest explicit exclusion rc={res.returncode} printed="
              f"{'EXCLUDED x/static/app.js' in res.stdout} (expect 0/True)")
        ok &= res.returncode == 0 and "EXCLUDED x/static/app.js: browser IIFE" in res.stdout
        res = subprocess.run(pwa_cmd + ["--exclusions", bad_inv], capture_output=True, text=True)
        print(f"selftest exclusion without reason rc={res.returncode} (expect 2)")
        ok &= res.returncode == 2
        res = subprocess.run(pwa_cmd + ["--exclusions", os.path.join(tmp, "absent.json")],
                             capture_output=True, text=True)
        print(f"selftest no inventory: untestable touched file rc={res.returncode} (expect 2)")
        ok &= res.returncode == 2

        # env check runs (informational pass/fail depending on machine).
        rc = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--check-env"],
            capture_output=True, text=True).returncode
        print(f"selftest check-env rc={rc} (expect 0 on this machine)")

    if ok:
        print("SELFTEST_OK")
        return 0
    print("SELFTEST_FAIL")
    return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lang", choices=["python", "js"])
    parser.add_argument("--component", choices=["engine", "brain", "host", "pwa"])
    parser.add_argument("--baseline-snapshot")
    parser.add_argument("--candidate-snapshot")
    parser.add_argument("--coverage-json")
    parser.add_argument("--baseline-coverage")
    parser.add_argument("--exclusions", default=DEFAULT_EXCLUSIONS,
                        help="explicit per-file glue exclusion inventory (JSON)")
    parser.add_argument("--check-env", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return cmd_selftest(args)
    if args.check_env:
        return cmd_check_env()
    if not (args.lang and args.component and args.baseline_snapshot
            and args.candidate_snapshot and args.coverage_json
            and args.baseline_coverage):
        return die_blocked("check mode requires --lang, --component, "
                           "--baseline-snapshot, --candidate-snapshot, "
                           "--coverage-json and --baseline-coverage")
    return cmd_check(args)


if __name__ == "__main__":
    sys.exit(main())
