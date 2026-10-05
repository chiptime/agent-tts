#!/usr/bin/env python3
"""Coverage comparator gate (voice-stack VS0.9, contract D4/T12.1).

Compares a candidate coverage report (coverage.py JSON or Node LCOV) against
the immutable baseline on TOPPED production modules:

  - scope selection: FULL production modules touched per baseline-snapshot vs
    candidate-snapshot diff => >= 90% lines AND >= 90% branches per module;
  - plus TOTAL no-regression of the component against its baseline;
  - component with no touched production files => explicit not_applicable
    (never a fake 100%);
  - --check-env validates tool compatibility (blocked, never silent degrade);
  - --changed-scope-map (MQ-03, D4 changed-production metric): for modules
    whose changed-line+branch scope is machine-mapped (see
    scripts/voice-stack/js-coverage/map-changed-scope.py), the >=90/90 floor
    applies to the changed statements and the in-scope branch arcs, with
    hard bindings: the gate re-hashes the candidate file on disk, re-checks
    the detail maps against the map digests, refuses empty denominators,
    and REJECTS exclusions for mapped (touched) files — no exceptions.

Exit codes: 0 pass · 1 FAIL · 2 blocked.
"""

from __future__ import annotations

import argparse
import hashlib
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


def touched_production_files(baseline_path: str, candidate_path: str, gate: str,
                             lang: str) -> list[str]:
    base = load_scope_paths(baseline_path, gate)
    cand = load_scope_paths(candidate_path, gate)
    if lang == "python":
        # lib/ counts as production BOTH bare ("lib/foo.py", component cwd)
        # and repo-relative ("hosts/herdr/tts-plugin/lib/foo.py", snapshot
        # paths) — the bare-prefix form silently produced false
        # not_applicable for the host component (blessed VS1.6 tooling fix).
        is_prod = lambda p: ("/src/" in p or "/lib/" in p or p.startswith("lib/")) \
            and p.endswith(".py") and "/tests/" not in p
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


# -- changed-scope mode (MQ-03, D4 changed-production metric) -------------------


def _digest_normalized(obj) -> str:
    """Canonical digest matching map-changed-scope.py's normalization
    (null-valued keys dropped recursively, keys sorted)."""
    def norm(o):
        if isinstance(o, dict):
            return {k: norm(v) for k, v in sorted(o.items()) if v is not None}
        if isinstance(o, list):
            return [norm(v) for v in o]
        return o
    return hashlib.sha256(
        json.dumps(norm(obj), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _load_changed_maps(paths: list[str]) -> list[dict]:
    maps = []
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8") as fh:
                maps.append(json.load(fh))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(die_blocked(f"unreadable changed-scope map {p}: {exc}"))
    if not maps:
        raise SystemExit(die_blocked("no changed-scope maps supplied"))
    return maps


def _load_detail(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(die_blocked(f"unreadable coverage detail {path}: {exc}"))
    return data.get("files", data)


def _load_exclusions(path: str | None) -> list[dict]:
    if not path:
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(die_blocked(f"unreadable exclusions {path}: {exc}"))
    return data if isinstance(data, list) else []


def cmd_check_changed(args) -> int:
    maps = _load_changed_maps(args.changed_scope_map)
    detail = _load_detail(args.coverage_detail)
    repo_root = os.path.abspath(args.repo_root or ".")
    exclusions = _load_exclusions(args.exclusions)

    problems: list[str] = []
    for m in maps:
        fname = m.get("file") or os.path.basename(m.get("current_path", ""))
        # binding: the candidate file on disk must be the mapped source
        disk = os.path.join(repo_root, m["current_path"])
        try:
            with open(disk, "rb") as fh:
                actual = hashlib.sha256(fh.read()).hexdigest()
        except OSError:
            problems.append(f"BLOCKED: mapped candidate missing on disk: {m['current_path']}")
            continue
        if actual != m["current_sha256"]:
            problems.append(
                f"BLOCKED: source hash drift for {fname}: on-disk {actual[:12]} != "
                f"mapped {m['current_sha256'][:12]} (metric no longer binds)")
            continue
        # exclusions are REJECTED for touched/mapped files (user: NO EXCEPTIONS)
        for ex in exclusions:
            if ex.get("path", "").endswith("/" + fname) or ex.get("path", "").endswith(fname):
                print(f"  exclusion REJECTED for {fname}: {ex.get('reason', '')[:60]} "
                      "(touched production is never excludable)")
        # detail record must exist with the exact instrumented maps
        if fname not in detail:
            problems.append(f"BLOCKED: no coverage record for {fname} in detail")
            continue
        fc = detail[fname]
        if _digest_normalized(fc.get("statementMap", {})) != m["maps_digest"]["statement_map_sha256"] \
                or _digest_normalized(fc.get("branchMap", {})) != m["maps_digest"]["branch_map_sha256"]:
            problems.append(
                f"BLOCKED: detail maps != mapped instrumentation for {fname} "
                "(original-vs-instrumented mismatch)")
            continue
        stmts = m["executable_changed_statements"]
        if not stmts:
            problems.append(
                f"BLOCKED: zero executable changed statements for {fname} "
                "(changed scope unmappable — full module floor applies, not 100%)")
            continue
        s = fc.get("s", {})
        hit = sum(1 for sid in stmts if s.get(sid, 0) > 0)
        lines_pct = 100.0 * hit / len(stmts)
        in_scope = list(m["in_scope_branches"].keys())
        if not in_scope:
            problems.append(f"BLOCKED: zero in-scope branches for {fname}")
            continue
        b = fc.get("b", {})
        arcs_hit = arcs_total = 0
        for bid in in_scope:
            for v in b.get(bid, []):
                arcs_total += 1
                arcs_hit += 1 if v > 0 else 0
        branches_pct = 100.0 * arcs_hit / arcs_total
        nonexec = len(m.get("non_executable_changed_lines", []))
        print(
            f"  {fname} [changed scope]: lines {lines_pct:.2f}% "
            f"({hit}/{len(stmts)} changed stmts; {nonexec} changed lines "
            f"non-executable) branches {branches_pct:.2f}% "
            f"({arcs_hit}/{arcs_total} in-scope arcs of "
            f"{m['total_branches']} branches)"
        )
        if lines_pct < THRESHOLD:
            problems.append(f"FAIL lines {lines_pct:.2f}% < {THRESHOLD} for {fname} (changed scope)")
        if branches_pct < THRESHOLD:
            problems.append(f"FAIL branches {branches_pct:.2f}% < {THRESHOLD} for {fname} (changed scope)")

    # component-total non-regression on the COMPARABLE (common) file set:
    # new files widen the denominator without being comparable to baseline.
    if args.baseline_coverage:
        cand = load_coverage_lcov(args.coverage_json)
        base = load_coverage_lcov(args.baseline_coverage)
        common = [k for k in cand if any(
            bk.endswith(k) or k.endswith(bk) for bk in base)]
        cl = cs = bl = bs = 0
        for k in common:
            c, t = cand[k]["lines"]; cl += c; cs += t
            for bk in base:
                if bk.endswith(k) or k.endswith(bk):
                    c, t = base[bk]["lines"]; bl += c; bs += t
                    break
        cand_total = 100.0 * cl / cs if cs else 0.0
        base_total = 100.0 * bl / bs if bs else 0.0
        full_cl = sum(v["lines"][0] for v in cand.values())
        full_cs = sum(v["lines"][1] for v in cand.values())
        print(f"common-set total ({len(common)} files): candidate "
              f"{cand_total:.2f}% vs baseline {base_total:.2f}% "
              f"(full candidate set {len(cand)} files: "
              f"{100.0 * full_cl / full_cs if full_cs else 0:.2f}%)")
        if cand_total < base_total - 1e-9:
            problems.append(
                f"FAIL total regression (common set): candidate "
                f"{cand_total:.2f}% < baseline {base_total:.2f}%")

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


def _cs_fixture(tmp: str, name: str, uncovered_stmts: list[str],
                uncovered_arcs: dict[str, list[int]], maps_override=None,
                sha_override: str | None = None) -> tuple[str, str]:
    """Synthetic changed-scope fixture: a 10-statement/2-branch module where
    statements '0'..'9' and branch arcs are explicitly controlled.
    Returns (map_path, detail_path)."""
    import hashlib as _h

    src = os.path.join(tmp, f"repo-{name}", "static", "mod.js")
    os.makedirs(os.path.dirname(src), exist_ok=True)
    content = "var a=1;\n" * 10
    with open(src, "w", encoding="utf-8") as fh:
        fh.write(content)
    sha = sha_override or _h.sha256(content.encode()).hexdigest()
    stmt_map = {str(i): {"start": {"line": i + 1, "column": 0},
                         "end": {"line": i + 1, "column": 6}} for i in range(10)}
    branch_map = {
        "0": {"loc": {"start": {"line": 1, "column": 0}, "end": {"line": 1, "column": 6}},
              "type": "if", "locations": [
                  {"start": {"line": 1, "column": 0}, "end": {"line": 1, "column": 6}},
                  {"start": {"line": 2, "column": 0}, "end": {"line": 2, "column": 6}}]},
        "1": {"loc": {"start": {"line": 3, "column": 0}, "end": {"line": 3, "column": 6}},
              "type": "if", "locations": [
                  {"start": {"line": 3, "column": 0}, "end": {"line": 3, "column": 6}},
                  {"start": {"line": 4, "column": 0}, "end": {"line": 4, "column": 6}}]},
    }
    if maps_override == "tamper-detail":
        stmt_map_detail = dict(stmt_map)
        stmt_map_detail["0"] = {"start": {"line": 99, "column": 0},
                                "end": {"line": 99, "column": 6}}
    else:
        stmt_map_detail = stmt_map
    detail = {"files": {"mod.js": {
        "path": "mod.js", "s": {str(i): 0 if str(i) in uncovered_stmts else 1
                                for i in range(10)},
        "b": {bid: [0 if k in uncovered_arcs.get(bid, []) else 1 for k in range(2)]
              for bid in ("0", "1")},
        "statementMap": stmt_map_detail, "branchMap": branch_map,
    }}}
    detail_path = os.path.join(tmp, f"detail-{name}.json")
    with open(detail_path, "w", encoding="utf-8") as fh:
        json.dump(detail, fh)
    changed = {"schema": 1, "file": "mod.js",
               "current_path": os.path.relpath(src, tmp),
               "new_file": False, "current_sha256": sha,
               "baseline_sha256": "f" * 64, "instrumenter": "selftest",
               "maps_digest": {
                   "statement_map_sha256": _digest_normalized(stmt_map),
                   "branch_map_sha256": _digest_normalized(branch_map)},
               "changed_added_lines": list(range(1, 11)),
               "changed_added_line_count": 10,
               "executable_changed_statements": [str(i) for i in range(10)],
               "non_executable_changed_lines": [],
               "in_scope_branches": {"0": {"line": 1, "reason": "selftest"},
                                     "1": {"line": 3, "reason": "selftest"}},
               "total_statements": 10, "total_branches": 2, "total_branch_arcs": 4}
    map_path = os.path.join(tmp, f"map-{name}.json")
    with open(map_path, "w", encoding="utf-8") as fh:
        json.dump(changed, fh)
    return map_path, detail_path


def cmd_selftest_changed(_args) -> int:
    """Negative controls for the changed-scope mode (MQ-03)."""
    ok = True

    def run(*extra):
        return subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--changed-scope-map",
             *extra],
            capture_output=True, text=True)

    def gate(map_path, detail_path, repo, exclusions=None):
        argv = ["--coverage-detail", detail_path, "--repo-root", repo]
        if exclusions:
            argv += ["--exclusions", exclusions]
        return run(map_path, *argv)

    with tempfile.TemporaryDirectory(prefix="mq03-gate-selftest-") as tmp:
        # healthy fixture: 10/10 stmts, 4/4 arcs => PASS, even though an
        # exclusions file asks to skip the module (former-exclusion control)
        m, d = _cs_fixture(tmp, "ok", [], {})
        excl = os.path.join(tmp, "exclusions.json")
        with open(excl, "w", encoding="utf-8") as fh:
            json.dump([{"component": "pwa", "path": "static/mod.js",
                        "reason": "former blanket exclusion must not skip"}], fh)
        r = gate(m, d, tmp, excl)
        print(f"selftest changed-scope ok+exclusion-rejected rc={r.returncode} "
              f"rejection_shown={'exclusion REJECTED' in r.stdout} (expect 0/True)")
        ok &= r.returncode == 0 and "exclusion REJECTED" in r.stdout

        # intentional uncovered changed lines (8/10 = 80% < 90) => FAIL
        m, d = _cs_fixture(tmp, "uncovered", ["5", "6"], {})
        r = gate(m, d, tmp)
        print(f"selftest changed-scope uncovered-lines rc={r.returncode} (expect 1)")
        ok &= r.returncode == 1

        # intentional uncovered arc (1 of 4 = 75% < 90) => FAIL
        m, d = _cs_fixture(tmp, "uncovered-arc", [], {"0": [1]})
        r = gate(m, d, tmp)
        print(f"selftest changed-scope uncovered-arc rc={r.returncode} (expect 1)")
        ok &= r.returncode == 1

        # stale source hash (candidate drifted) => BLOCKED (exit 2)
        m, d = _cs_fixture(tmp, "stale-sha", [], {}, sha_override="0" * 64)
        r = gate(m, d, tmp)
        print(f"selftest changed-scope stale-source-hash rc={r.returncode} (expect 2)")
        ok &= r.returncode == 2

        # tampered detail maps (instrumented-vs-original mismatch) => BLOCKED
        m, d = _cs_fixture(tmp, "tamper", [], {}, maps_override="tamper-detail")
        r = gate(m, d, tmp)
        print(f"selftest changed-scope tampered-maps rc={r.returncode} (expect 2)")
        ok &= r.returncode == 2

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
    parser.add_argument("--changed-scope-map", action="append",
                        help="MQ-03 changed-scope map JSON (repeatable); "
                        "switches the gate to the D4 changed-production metric")
    parser.add_argument("--coverage-detail",
                        help="merged istanbul detail JSON for changed-scope mode")
    parser.add_argument("--repo-root")
    parser.add_argument("--exclusions",
                        help="coverage-exclusions.json; entries touching "
                             "mapped files are REJECTED, never honored")
    parser.add_argument("--selftest-changed", action="store_true",
                        help="run the changed-scope negative controls")
    parser.add_argument("--check-env", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return cmd_selftest(args)
    if args.selftest_changed:
        return cmd_selftest_changed(args)
    if args.check_env:
        return cmd_check_env()
    if args.changed_scope_map:
        if not (args.coverage_detail and args.repo_root):
            return die_blocked("changed-scope mode requires --coverage-detail "
                               "and --repo-root")
        return cmd_check_changed(args)
    if not (args.lang and args.component and args.baseline_snapshot
            and args.candidate_snapshot and args.coverage_json
            and args.baseline_coverage):
        return die_blocked("check mode requires --lang, --component, "
                           "--baseline-snapshot, --candidate-snapshot, "
                           "--coverage-json and --baseline-coverage")
    return cmd_check(args)


if __name__ == "__main__":
    sys.exit(main())
