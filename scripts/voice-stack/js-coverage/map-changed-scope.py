#!/usr/bin/env python3
"""Changed-scope mapper for the voice-stack JS coverage gate (MQ-03).

Builds the D4-sanctioned changed-production metric input: which executable
statements and which branches of a CURRENT production file are in the
changed scope, derived mechanically from the immutable baseline bytes and
the current bytes — never hand-listed.

Scope rules (conservative, all machine-checked):
  * changed added lines: unified diff (difflib, context 0) of baseline vs
    current, new-side lines inside + hunks. Line MOVES count (they are
    re-authored at a new position); pure deletions do not (no current line).
  * executable changed lines: a changed line covered by at least one istanbul
    statement range (multiline-safe: start..end). Changed lines with no
    covering statement are reported as non-executable (comments, blanks,
    braces) — full accounting, never silently dropped.
  * in-scope branches: any branch whose decision location OR any of its
    path locations intersects an executable changed line. This pulls in new
    conditionals, modified arms, enclosing guards around inserted bodies,
    and logical-operator legs — but NOT untouched legacy branches elsewhere
    in the file.
  * new file (no baseline): every statement and every branch is in scope.

Bindings validated before anything is emitted:
  * sha256(baseline bytes) == --baseline-sha256 (ties the diff to the
    immutable B0/genesis artifact);
  * sha256(current bytes) == --current-sha256 (ties the metric to the exact
    candidate under test);
  * the detail JSON's statementMap/branchMap are byte-equal to a freshly
    instrumented map of the CURRENT file (--maps), so collected counters
    are proven to belong to this source (original-vs-instrumented mismatch
    control). The gate re-checks digests at gate time.

Selftest (--selftest): synthetic mini-files exercise shifted lines, a body
added inside an old conditional, and a multiline statement; the mapper must
select exactly the expected scope.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import sys
from pathlib import Path


class MapperError(Exception):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalize(obj):
    """Canonical form for istanbul map structures: drop null-valued keys so
    an empty loc `{}` and its round-tripped `{line: null, column: null}`
    representation compare equal (istanbul-lib-coverage normalizes empty
    locations during merge). Any coordinate change still differs."""
    if isinstance(obj, dict):
        return {k: _normalize(v) for k, v in sorted(obj.items()) if v is not None}
    if isinstance(obj, list):
        return [_normalize(v) for v in obj]
    return obj


def sha256_json(obj) -> str:
    return hashlib.sha256(
        json.dumps(_normalize(obj), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def changed_added_lines(baseline_text: str, current_text: str) -> list[int]:
    """New-side line numbers (1-based) that were added vs baseline."""
    diff = difflib.unified_diff(
        baseline_text.splitlines(keepends=True),
        current_text.splitlines(keepends=True),
        n=0,
    )
    added: list[int] = []
    newline = 0
    in_hunk = False
    for raw in diff:
        if raw.startswith("@@"):
            # @@ -a,b +c,d @@ -> current position restarts at c
            head = raw.split("+", 1)[1].split(",", 1)[0].split(" ")[0]
            newline = int(head) - 1
            in_hunk = True
            continue
        if not in_hunk or raw.startswith("---") or raw.startswith("+++"):
            continue
        if raw.startswith("+"):
            newline += 1
            added.append(newline)
        elif raw.startswith("-"):
            pass  # deletion: no current-side line
        else:
            newline += 1
    return sorted(set(added))


def _range_hits_line(loc: dict, line: int) -> bool:
    start = loc.get("start", {}).get("line", -1)
    end = loc.get("end", {}).get("line", -1)
    return start <= line <= end


def map_file(baseline_path: Path | None, current_path: Path, detail: dict,
             maps: dict, current_sha: str, baseline_sha: str | None) -> dict:
    current_bytes = current_path.read_bytes()
    if sha256_bytes(current_bytes) != current_sha:
        raise MapperError(
            f"current file hash drift: {current_path} is not the candidate "
            f"{current_sha[:12]} the metric was built for"
        )
    baseline_bytes = (baseline_path.read_bytes() if baseline_path else b"")
    new_file = baseline_path is None
    if not new_file and sha256_bytes(baseline_bytes) != baseline_sha:
        raise MapperError(
            f"baseline file hash drift: expected genesis {baseline_sha[:12]}"
        )

    # detail binding: maps in the collected detail must equal the fresh maps
    fresh_stmt = maps["statementMap"]
    fresh_branch = maps["branchMap"]
    if sha256_json(detail.get("statementMap", {})) != sha256_json(fresh_stmt) \
            or sha256_json(detail.get("branchMap", {})) != sha256_json(fresh_branch):
        raise MapperError(
            "detail maps != fresh instrumentation maps of the current file "
            "(original-vs-instrumented mismatch: counters do not belong to "
            "this source)"
        )

    current_text = current_bytes.decode("utf-8", "replace")
    baseline_text = baseline_bytes.decode("utf-8", "replace") if baseline_path else ""
    added = changed_added_lines(baseline_text, current_text) if baseline_path else []

    # statements covering changed lines (or all statements for a new file)
    statement_map = fresh_stmt
    exec_changed_stmts: list[str] = []
    non_exec: list[int] = []
    if new_file:
        exec_changed_stmts = sorted(statement_map.keys(), key=int)
    else:
        covering: dict[str, list[int]] = {}
        for sid, loc in statement_map.items():
            for line in added:
                if _range_hits_line(loc, line):
                    covering.setdefault(sid, []).append(line)
        for line in added:
            if not any(line in hits for hits in covering.values()):
                non_exec.append(line)
        exec_changed_stmts = sorted(covering.keys(), key=int)

    # branches whose decision/path ranges intersect changed lines
    branch_map = fresh_branch
    in_scope: dict[str, dict] = {}
    changed_line_set = set(added)
    for bid, meta in branch_map.items():
        if new_file:
            in_scope[bid] = {
                "line": meta.get("loc", {}).get("start", {}).get("line"),
                "type": meta.get("type"),
                "reason": "new-file",
            }
            continue
        # istanbul branchMap: meta["loc"] is the decision; meta["locations"]
        # are BARE loc entries (one per arc/path)
        locs = [meta.get("loc", {})] + list(meta.get("locations", []))
        hit_lines = sorted(
            {line for loc in locs for line in changed_line_set if _range_hits_line(loc, line)}
        )
        if hit_lines:
            in_scope[bid] = {
                "line": meta.get("loc", {}).get("start", {}).get("line"),
                "type": meta.get("type"),
                "reason": "changed-line-in-decision-or-path",
                "hit_lines": hit_lines,
            }

    return {
        "schema": 1,
        "file": maps.get("path") or current_path.name,
        "current_path": str(current_path),
        "new_file": new_file,
        "current_sha256": current_sha,
        "baseline_sha256": baseline_sha,
        "instrumenter": maps.get("instrumenter"),
        "maps_digest": {
            "statement_map_sha256": sha256_json(fresh_stmt),
            "branch_map_sha256": sha256_json(fresh_branch),
        },
        "changed_added_lines": added,
        "changed_added_line_count": len(added),
        "executable_changed_statements": exec_changed_stmts,
        "non_executable_changed_lines": non_exec,
        "in_scope_branches": in_scope,
        "total_statements": len(statement_map),
        "total_branches": len(branch_map),
        "total_branch_arcs": sum(len(m.get("locations", [])) for m in branch_map.values()),
    }


# --------------------------------------------------------------- selftest

SELFTEST_BASELINE = """\
function a(x) {
  if (x) {
    return 1;
  }
  return 2;
}
function b() {
  return 3;
}
"""

# case 1: body added inside the old conditional (lines shift below it)
SELFTEST_CURRENT_1 = """\
function a(x) {
  if (x) {
    log("new");
    return 1;
  }
  return 2;
}
function b() {
  return 3;
}
"""

# case 2: multiline statement added (each physical line must count)
SELFTEST_CURRENT_2 = """\
function a(x) {
  if (x) {
    return 1;
  }
  send({
    a: 1,
    b: 2
  });
  return 2;
}
function b() {
  return 3;
}
"""


def _selftest_instrument_like(babel_free_source: str) -> dict:
    """Minimal statement/branch maps for the synthetic sources: statements
    per top-level function line and one if-branch. The mapper's contract is
    map-shape based, so exact istanbul output is not needed for the
    synthetic selftest — only ranges consistent with the synthetic text."""
    lines = babel_free_source.splitlines()

    def find(prefix: str) -> int:
        for i, l in enumerate(lines, 1):
            if l.startswith(prefix):
                return i
        raise AssertionError(prefix)

    if_line = find("  if (x) {")
    return {
        "statementMap": {
            "0": {"start": {"line": 1, "column": 0}, "end": {"line": len(lines), "column": 1}},
            "1": {"start": {"line": if_line, "column": 2}, "end": {"line": if_line, "column": 12}},
            "2": {"start": {"line": if_line + 1, "column": 4}, "end": {"line": if_line + 2, "column": 14}},
        },
        "branchMap": {
            # real istanbul models an if-branch with the decision as loc and
            # the consequent/alternative BLOCK ranges as locations
            "0": {
                "loc": {"start": {"line": if_line, "column": 2}, "end": {"line": if_line, "column": 12}},
                "type": "if",
                "locations": [
                    {"start": {"line": if_line + 1, "column": 4}, "end": {"line": if_line + 2, "column": 14}},
                    {"start": {"line": if_line + 4, "column": 2}, "end": {"line": if_line + 4, "column": 11}},
                ],
            }
        },
    }


def cmd_selftest() -> int:
    ok = True

    def check(label: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f" :: {detail}" if detail else ""))
        ok &= cond

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        base = tmp / "base.js"
        cur1 = tmp / "cur1.js"
        cur2 = tmp / "cur2.js"
        base.write_text(SELFTEST_BASELINE)
        cur1.write_text(SELFTEST_CURRENT_1)
        cur2.write_text(SELFTEST_CURRENT_2)

        maps1 = _selftest_instrument_like(SELFTEST_CURRENT_1)
        detail1 = {"statementMap": maps1["statementMap"], "branchMap": maps1["branchMap"]}
        out = map_file(
            base, cur1, detail1, {"path": "cur1.js", "instrumenter": "selftest", **maps1},
            sha256_bytes(cur1.read_bytes()), sha256_bytes(base.read_bytes()),
        )
        # added lines: log("new"); -> line 3; enclosing if-branch in scope
        check("case1 changed lines == [3]", out["changed_added_lines"] == [3],
              str(out["changed_added_lines"]))
        check("case1 if-branch in scope", "0" in out["in_scope_branches"],
              str(sorted(out["in_scope_branches"])))
        check("case1 statement 2 (return 1) is the covering stmt",
              "2" in out["executable_changed_statements"],
              str(out["executable_changed_statements"]))
        # shifted function b() is NOT changed: no +line beyond 3
        check("case1 no phantom changed lines below the hunk",
              out["changed_added_line_count"] == 1, str(out["changed_added_lines"]))

        maps2 = _selftest_instrument_like(SELFTEST_CURRENT_2)
        detail2 = {"statementMap": maps2["statementMap"], "branchMap": maps2["branchMap"]}
        out2 = map_file(
            base, cur2, detail2, {"path": "cur2.js", "instrumenter": "selftest", **maps2},
            sha256_bytes(cur2.read_bytes()), sha256_bytes(base.read_bytes()),
        )
        # multiline send({...}) spans lines 5-8: every line must be accounted
        check("case2 changed lines == [5,6,7,8]",
              out2["changed_added_lines"] == [5, 6, 7, 8],
              str(out2["changed_added_lines"]))
        covered = set(out2["executable_changed_statements"]) | set()
        check("case2 covering statement counted",
              len(out2["executable_changed_statements"]) >= 1,
              str(out2["executable_changed_statements"]))
        check("case2 non-executable changed lines reported",
              isinstance(out2["non_executable_changed_lines"], list))

        # binding controls
        try:
            map_file(base, cur1, {"statementMap": {}, "branchMap": {}},
                     {"path": "cur1.js", "instrumenter": "s", **maps1},
                     sha256_bytes(cur1.read_bytes()), sha256_bytes(base.read_bytes()))
            check("tampered detail maps rejected", False)
        except MapperError:
            check("tampered detail maps rejected", True)
        try:
            map_file(base, cur1, detail1,
                     {"path": "cur1.js", "instrumenter": "s", **maps1},
                     "0" * 64, sha256_bytes(base.read_bytes()))
            check("wrong current sha rejected", False)
        except MapperError:
            check("wrong current sha rejected", True)
        try:
            map_file(base, cur1, detail1,
                     {"path": "cur1.js", "instrumenter": "s", **maps1},
                     sha256_bytes(cur1.read_bytes()), "0" * 64)
            check("wrong baseline sha rejected", False)
        except MapperError:
            check("wrong baseline sha rejected", True)

    print("SELFTEST_OK" if ok else "SELFTEST_FAIL")
    return 0 if ok else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-file")
    parser.add_argument("--baseline-sha256")
    parser.add_argument("--current-file")
    parser.add_argument("--current-sha256")
    parser.add_argument("--detail",
                        help="merged istanbul detail JSON (full coverage data)")
    parser.add_argument("--detail-key",
                        help="file key inside the detail (default: basename)")
    parser.add_argument("--maps",
                        help="fresh instrument-file.js maps JSON of the current file")
    parser.add_argument("--out")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(None if argv is None else argv)
    if args.selftest:
        return cmd_selftest()
    missing = [name for name in ("current_file", "current_sha256", "detail", "maps", "out")
               if not getattr(args, name)]
    if missing:
        parser.error(f"the following arguments are required: {', '.join('--' + m.replace('_', '-') for m in missing)}")

    try:
        detail_all = json.loads(Path(args.detail).read_text(encoding="utf-8"))
        files = detail_all.get("files", detail_all)
        key = args.detail_key or Path(args.current_file).name
        if key not in files:
            raise MapperError(f"detail has no record for {key!r} (keys: {sorted(files)})")
        detail = files[key]
        maps = json.loads(Path(args.maps).read_text(encoding="utf-8"))
        baseline = Path(args.baseline_file) if args.baseline_file else None
        out = map_file(baseline, Path(args.current_file), detail, maps,
                       args.current_sha256, args.baseline_sha256)
        Path(args.out).write_text(json.dumps(out, indent=1, sort_keys=True) + "\n",
                                  encoding="utf-8")
        print(
            f"mapped {out['file']}: changed_lines={out['changed_added_line_count']} "
            f"executable_stmts={len(out['executable_changed_statements'])} "
            f"in_scope_branches={len(out['in_scope_branches'])} "
            f"(of {out['total_branches']})"
        )
        return 0
    except MapperError as exc:
        print(f"MAP_BLOCKED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
