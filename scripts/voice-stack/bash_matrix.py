#!/usr/bin/env python3
"""Bash decision-alternative matrix checker (voice-stack VS0.8, contract D9/T12.2).

Proves that every DECISION ALTERNATIVE in MODIFIED Bash code has an EXECUTED
test case. The matrix is evidence of covered alternatives; it is NOT a numeric
branch-coverage percentage and never prints or computes one.

Exit codes: 0 pass · 1 FAIL · 2 blocked.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import tempfile

NOT_A_PERCENTAGE = (
    "matrix = evidence of alternatives covered; NOT a branch-coverage percentage"
)

# Keywords that start constructs this parser does NOT enumerate. Finding one in
# a MODIFIED file is a typed blocked result (fail-closed), never a silent skip.
UNSUPPORTED_KEYWORDS = ("select ", "until ")
_UNSUPPORTED_RE = re.compile(r"(?:^|&&|\|\||\||;|\(|\{)\s*(select|until)(\s|$)")

# A heredoc opener: `<<WORD` / `<<-WORD` / `<<'WORD'`. Not `<<<` (here-string,
# also what a quoted marker like '# <<< x <<<' looks like) and not an
# arithmetic shift inside (( )); the word must be an identifier.
_HEREDOC_RE = re.compile(r"(?<![<\d])<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")
_IF_RE = re.compile(r"(?:^|&&|\|\||;)\s*(el)?if\s+")
_CASE_RE = re.compile(r"(?:^|&&|\|\||;)\s*case\s+\S.*\bin\s*$")
_CASE_ARM_RE = re.compile(r"^\s*([^(|)]+(?:\|[^)|]*)*)\)\s*")
_FI_RE = re.compile(r"^\s*fi\b")
_ESAC_RE = re.compile(r"^\s*esac\b")
_ELSE_RE = re.compile(r"^\s*(else|elif\s)\b")
_ANDOR_RE = re.compile(r"(?:^|;)\s*[^\s#][^;]*?(&&|\|\|)\s*\S")


def die_blocked(reason: str) -> int:
    print(f"BLOCKED: {reason}")
    return 2


# -- snapshot / blob plumbing ------------------------------------------------


def load_scope(snapshot_path: str, gate: str) -> dict[str, dict]:
    try:
        with open(snapshot_path, "r", encoding="utf-8") as fh:
            snap = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(die_blocked(f"unreadable snapshot {snapshot_path}: {exc}"))
    files = snap.get("scopes", {}).get(gate, {}).get("files", [])
    return {entry["path"]: entry for entry in files}


def read_blob(snapshot_path: str, entry: dict) -> bytes:
    """Blob bytes for a snapshot entry; falls back to live file if blob dir
    is absent next to the snapshot (identity then equals the live tree)."""
    blob_dir = os.path.join(os.path.dirname(os.path.abspath(snapshot_path)), "blobs")
    ref = entry.get("blob_ref")
    if ref and os.path.isfile(os.path.join(blob_dir, ref)):
        with open(os.path.join(blob_dir, ref), "rb") as fh:
            return fh.read()
    with open(entry["path"], "rb") as fh:
        return fh.read()


def is_bash_file(path: str, blob: bytes) -> bool:
    """Bash sources only: *.sh, or an extensionless file with a bash/sh shebang.

    The gate scopes also hold docs, JSON tables and Python modules; parsing
    those as shell would block on prose or count non-shell lines.
    """
    if "/tests/" in f"/{path}":
        return False  # measuring instruments are never the subject (D9 = product Bash)
    if path.endswith(".sh"):
        return True
    first = blob.split(b"\n", 1)[0].decode("utf-8", "replace")
    return first.startswith("#!") and ("bash" in first or first.rstrip().endswith("sh"))


def diff_modified(baseline_path: str, candidate_path: str, gate: str):
    """Yield (path, entry) for candidate files that are new or changed vs baseline."""
    base = load_scope(baseline_path, gate)
    cand = load_scope(candidate_path, gate)
    for path, entry in sorted(cand.items()):
        old = base.get(path)
        if old is None or old.get("sha256") != entry.get("sha256"):
            yield path, entry


# -- bash decision extraction (subset, fail-closed) ---------------------------


def _join_continuations(lines: list[str]) -> list[tuple[int, int, str]]:
    """Join backslash-continued lines into logical lines.

    Each entry is ``(first physical line, last physical line, text)`` so a
    modified continuation line still maps to its logical line.
    """
    out: list[tuple[int, int, str]] = []
    buf, start = "", None
    for idx, raw in enumerate(lines, start=1):
        if start is None:
            start = idx
        if raw.endswith("\\"):
            buf += raw[:-1] + " "
            continue
        out.append((start, idx, buf + raw))
        buf, start = "", None
    if buf:
        out.append((start or 1, len(lines), buf))
    return out


_STRING_RE = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")
_ONE_LINE_FI_RE = re.compile(r"(^|;|\s)fi\s*(;|$)")


def _code_only(line: str) -> str:
    """The line with quoted strings blanked and a trailing comment removed.

    Keyword/operator detection runs on this view so prose inside messages
    ("wait until ready", "a && b") is never mistaken for shell syntax.
    """
    code = _STRING_RE.sub('""', line)
    return re.sub(r"(^|\s)#.*$", "", code)


def changed_line_set(old: bytes | None, new: bytes) -> set[int] | None:
    """1-based candidate lines added/changed vs the baseline; None = all lines
    (new file)."""
    if old is None:
        return None
    old_lines = old.decode("utf-8", "replace").splitlines()
    new_lines = new.decode("utf-8", "replace").splitlines()
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    changed: set[int] = set()
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag in ("insert", "replace"):
            changed.update(range(j1 + 1, j2 + 1))
    return changed


def extract_decisions(path: str, blob: bytes, only_lines: set[int] | None = None) -> list[dict]:
    """Enumerate decision alternatives for one Bash file.

    Supported: if/elif/else/fi, case arms, && and || lists. Heredoc bodies are
    skipped. With ``only_lines`` (the lines a change added or modified) the
    whole file is still walked so nesting context stays right, but decisions
    are emitted — and unsupported constructs block — ONLY on those lines: an
    unchanged 7000-line script is never re-audited for a two-line change.
    Anything unparseable on a checked line raises Blocked (caller exits 2).
    """
    text = blob.decode("utf-8", "replace")
    lines = text.splitlines()
    heredoc_skip = _heredoc_spans(lines)
    logical = _join_continuations(lines)
    decisions: list[dict] = []

    class Blocked(Exception):
        pass

    stack: list[str] = []  # entries: "if" | "case"
    for lineno, end_lineno, line in logical:
        if any(lo <= lineno <= hi for lo, hi in heredoc_skip):
            continue
        code = _code_only(line)
        stripped = code.strip()
        if not stripped:
            continue
        checked = only_lines is None or any(
            n in only_lines for n in range(lineno, end_lineno + 1)
        )
        if checked:
            unsupported = _UNSUPPORTED_RE.search(code)
            if unsupported:
                raise Blocked(f"{path}:{lineno}: unsupported construct '{unsupported.group(1)}'")
        if _CASE_RE.search(code):
            stack.append({"kind": "case", "has_star": False, "emitted": []})
            continue
        if stack and isinstance(stack[-1], dict):
            block = stack[-1]
            if _ESAC_RE.match(stripped):
                stack.pop()
                # "no-match" exists only when the WHOLE case has no '*' arm;
                # it rides on the last arm THIS change emitted for the block.
                if not block["has_star"] and block["emitted"]:
                    block["emitted"][-1]["alternatives"].append(
                        {"label": "no-match", "cases": []}
                    )
                continue
            arm = _CASE_ARM_RE.match(code)
            if arm:
                label = (_CASE_ARM_RE.match(line) or arm).group(1).strip()
                if label == "*":
                    block["has_star"] = True
                if checked:
                    decision = {
                        "id": f"{path}:{lineno}:case-arm",
                        "file": path,
                        "line": lineno,
                        "construct": "case-arm",
                        "alternatives": [{"label": label, "cases": []}],
                    }
                    decisions.append(decision)
                    block["emitted"].append(decision)
                continue
            # inside a case arm body: fall through to if/&& handling
        if_match = _IF_RE.search(code)
        if if_match:
            is_elif = if_match.group(1) == "el"
            if checked and is_elif and not (stack and stack[-1] == "if"):
                raise Blocked(f"{path}:{lineno}: 'elif' without an open if")
            if checked:
                decisions.append(
                    {
                        "id": f"{path}:{lineno}:{'elif' if is_elif else 'if'}",
                        "file": path,
                        "line": lineno,
                        "construct": "elif" if is_elif else "if",
                        "alternatives": [
                            {"label": "cond-true", "cases": []},
                            {"label": "cond-false", "cases": []},
                        ],
                    }
                )
            # elif continues the open chain; a one-line `if ...; fi` closes
            # itself: neither may leave an entry on the nesting stack.
            if not is_elif and not _ONE_LINE_FI_RE.search(code):
                stack.append("if")
            continue
        if stack and stack[-1] == "if" and _FI_RE.match(stripped):
            stack.pop()
            continue
        if stack and stack[-1] == "if" and _ELSE_RE.match(stripped):
            continue
        andor = _ANDOR_RE.search(code)
        if checked and andor and not _CASE_RE.search(code):
            op = andor.group(1)
            decisions.append(
                {
                    "id": f"{path}:{lineno}:{op}",
                    "file": path,
                    "line": lineno,
                    "construct": op,
                    "alternatives": [
                        {"label": "left-success", "cases": []},
                        {"label": "left-failure", "cases": []},
                    ],
                }
            )
    if stack and only_lines is None:
        raise Blocked(f"{path}: unbalanced {stack} (cannot enumerate reliably)")

    return decisions


def _heredoc_spans(lines: list[str]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    open_word: str | None = None
    start = 0
    for idx, line in enumerate(lines, start=1):
        if open_word is None:
            # Keep offsets to distinguish operators in code from quoted
            # markers, but read quoted delimiter words from the raw source.
            masked = _STRING_RE.sub(lambda m: " " * len(m.group()), line)
            masked = re.sub(r"(^|\s)#.*$", "", masked)
            match = None if "((" in masked else next(
                (m for m in _HEREDOC_RE.finditer(line)
                 if m.start() < len(masked) and masked[m.start()] == "<"), None)
            if match:
                open_word, start = match.group(2), idx + 1
        elif line.strip() == open_word:
            spans.append((start, idx - 1))
            open_word = None
    if open_word is not None:
        spans.append((start, len(lines)))
    return spans


# -- table + execution --------------------------------------------------------


def validate_table(table: list[dict], extracted: list[dict]) -> list[str]:
    """Return list of violation strings (empty = valid)."""
    problems: list[str] = []
    extracted_ids = {d["id"] for d in extracted}
    table_ids = [d.get("id") for d in table]
    if len(table_ids) != len(set(table_ids)):
        problems.append("table has duplicate ids")
    for tid in table_ids:
        if tid not in extracted_ids:
            problems.append(f"table id not present in modified code: {tid}")
    for d in extracted:
        if d["id"] not in set(table_ids):
            problems.append(f"missing table entry for {d['id']}")
    seen_pairs: set[tuple[str, str, str]] = set()
    for d in table:
        for alt in d.get("alternatives", []):
            if not alt.get("cases"):
                problems.append(f"{d['id']} alternative '{alt.get('label')}' has no case")
            for case in alt.get("cases", []):
                triple = (d["id"], str(alt.get("label")), case)
                if triple in seen_pairs:
                    problems.append(f"duplicate case '{case}' in {d['id']}/{alt.get('label')}")
                seen_pairs.add(triple)
    return problems


def run_harness(repo_root: str, cases_log: str | None) -> tuple[dict[str, str], int]:
    """Return ({case: OK|FAIL}, failures). Latest occurrence wins."""
    if cases_log:
        with open(cases_log, "r", encoding="utf-8") as fh:
            output = fh.read()
    else:
        proc = subprocess.run(
            ["bash", "hosts/herdr/tts-plugin/tests/host_cli_cases.sh"],
            cwd=repo_root, capture_output=True, text=True, timeout=600,
        )
        output = proc.stdout
    state: dict[str, str] = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "CASE":
            name, verdict = parts[1], parts[2]
            if verdict in ("START", "OK", "FAIL"):
                state[name] = verdict if verdict != "START" else state.get(name, "START")
    executed = {k: v for k, v in state.items() if v != "START"}
    fails = sum(1 for v in executed.values() if v == "FAIL")
    return executed, fails


def referenced_cases(table: list[dict]) -> set[str]:
    names: set[str] = set()
    for d in table:
        for alt in d.get("alternatives", []):
            names.update(alt.get("cases", []))
    return names


# -- kcov / lcov coverage parsing ---------------------------------------------


def parse_coverage_dir(coverage_dir: str) -> dict[str, dict[int, bool]]:
    """{path: {line: covered}} merged from kcov JSON and lcov .info files."""
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


def _match_coverage_path(per_file: dict, repo_path: str) -> str | None:
    if repo_path in per_file:
        return repo_path
    for cov_path in per_file:
        if cov_path.endswith(repo_path) or repo_path.endswith(cov_path):
            return cov_path
    return None


def coverage_linkage(extracted: list[dict], coverage_dir: str) -> list[str]:
    per_file = parse_coverage_dir(coverage_dir)
    problems: list[str] = []
    for d in extracted:
        cov_path = _match_coverage_path(per_file, d["file"])
        if cov_path is None:
            problems.append(f"blocked: no coverage trace for {d['file']}")
            continue
        cov = per_file[cov_path]
        if not cov.get(d["line"], False):
            problems.append(f"{d['id']} line {d['line']} not covered")
    return problems


# -- main paths ----------------------------------------------------------------


def cmd_check(args) -> int:
    extracted: list[dict] = []
    base_scope = load_scope(args.baseline_snapshot, "G-BASH-MATRIX")
    bash_modified = 0
    for path, entry in diff_modified(args.baseline_snapshot, args.candidate_snapshot, "G-BASH-MATRIX"):
        blob = read_blob(args.candidate_snapshot, entry)
        if not is_bash_file(path, blob):
            continue
        bash_modified += 1
        old = base_scope.get(path)
        old_blob = read_blob(args.baseline_snapshot, old) if old else None
        try:
            extracted.extend(extract_decisions(path, blob, changed_line_set(old_blob, blob)))
        except Exception as exc:  # Blocked or decode issues: fail closed
            return die_blocked(str(exc))
    try:
        with open(args.table, "r", encoding="utf-8") as fh:
            table = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return die_blocked(f"unreadable table {args.table}: {exc}")
    if not isinstance(table, list):
        return die_blocked("table must be a JSON array")

    problems = validate_table(table, extracted)
    executed, fails = run_harness(repo_root(os.path.abspath(args.table)), args.cases_log)
    refs = referenced_cases(table)
    for name in sorted(refs):
        if name not in executed:
            problems.append(f"referenced case not executed: {name}")
        elif executed[name] == "FAIL":
            problems.append(f"referenced case FAILED: {name}")
    if fails and not problems:
        pass  # unreferenced failures allowed but reported
    if args.coverage_dir:
        cov_problems = coverage_linkage(extracted, args.coverage_dir)
        for p in cov_problems:
            if p.startswith("blocked:"):
                return die_blocked(p[9:])
        problems.extend(cov_problems)

    print(f"modified_files={bash_modified}")
    print(f"decisions={len(extracted)} "
          f"alternatives={sum(len(d['alternatives']) for d in extracted)}")
    print(f"cases_executed={len(executed)} cases_ok={sum(1 for v in executed.values() if v == 'OK')} "
          f"cases_fail={sum(1 for v in executed.values() if v == 'FAIL')}")
    print(NOT_A_PERCENTAGE)
    if problems:
        for p in problems[:50]:
            print(f"FAIL: {p}")
        return 1
    print("MATRIX_PASS")
    return 0


def repo_root(from_path: str) -> str:
    cur = os.path.dirname(from_path)
    while cur != os.path.dirname(cur):
        if os.path.isdir(os.path.join(cur, ".git")):
            return cur
        cur = os.path.dirname(cur)
    return os.getcwd()


# -- selftest ------------------------------------------------------------------


def _mini_snapshot(path: str, gate: str, files: list[dict]) -> None:
    snap = {"schema": "1", "ts": "selftest", "repo": {}, "package": {"files": []},
            "scopes": {gate: {"files": files}}, "env": {}, "binding": "selftest"}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, indent=1)


def _mini_entry(rundir: str, relpath: str) -> dict:
    import hashlib
    blob_dir = os.path.join(rundir, "blobs")
    os.makedirs(blob_dir, exist_ok=True)
    with open(os.path.join(rundir, "tree", relpath), "rb") as fh:
        data = fh.read()
    sha = hashlib.sha256(data).hexdigest()
    with open(os.path.join(blob_dir, sha), "wb") as fh:
        fh.write(data)
    return {"path": relpath, "sha256": sha, "mode": "0o755", "blob_ref": sha}


DECISIONS_SH = """#!/usr/bin/env bash
set -euo pipefail
mode="$1"
if [[ "$mode" == "go" ]]; then
  echo go
else
  echo stop
fi
case "$mode" in
  go) echo arm-go ;;
  stop) echo arm-stop ;;
  *) echo arm-any ;;
esac
[[ -n "$mode" ]] && echo andok
[[ -z "$mode" ]] || echo orok
"""

SELECT_SH = """#!/usr/bin/env bash
select opt in a b; do
  echo "$opt"
  break
done
"""

SCOPED_OLD = """#!/usr/bin/env bash
echo "wait until ready && keep going"
if [[ -n "${X:-}" ]]; then
  echo legacy-unbalanced-on-purpose
"""

SCOPED_ADDED = """if [[ "${1:-}" == "go" ]]; then
  echo new-go
else
  echo new-other
fi
"""

HARNESS_SH = """#!/usr/bin/env bash
set -euo pipefail
case__mode_go() { echo "GO"; }
case__mode_stop() { echo "STOP"; }
case__andor_ok() { echo "ANDOR"; }
case__arm_any() { echo "ANY"; }
names=""
for fn in $(declare -F | awk '{print $3}' | grep '^case__'); do
  names="$names ${fn#case__}"
done
wanted="${CASES:-$names}"
fail=0
for name in $wanted; do
  echo "CASE $name START"
  if "case__$name"; then echo "CASE $name OK"; else echo "CASE $name FAIL"; fail=1; fi
done
exit $fail
"""


def cmd_selftest(args) -> int:
    run_dir = os.path.join(args.run_dir, "bash-matrix-selftest")
    tree = os.path.join(run_dir, "tree")
    os.makedirs(tree, exist_ok=True)
    with open(os.path.join(tree, "decisions.sh"), "w") as fh:
        fh.write(DECISIONS_SH)
    with open(os.path.join(tree, "select.sh"), "w") as fh:
        fh.write(SELECT_SH)
    with open(os.path.join(run_dir, "harness.sh"), "w") as fh:
        fh.write(HARNESS_SH)

    base_snap = os.path.join(run_dir, "baseline.json")
    cand_snap = os.path.join(run_dir, "candidate.json")
    _mini_snapshot(base_snap, "G-BASH-MATRIX", [])
    entry = _mini_entry(run_dir, "decisions.sh")
    _mini_snapshot(cand_snap, "G-BASH-MATRIX", [entry])

    extracted = extract_decisions("decisions.sh", DECISIONS_SH.encode())
    ids = [d["id"] for d in extracted]
    alts = {d["id"]: [a["label"] for a in d["alternatives"]] for d in extracted}
    ok = True

    def build_table(mode: str, case_for_every: bool, extra_case: str | None) -> list:
        table = []
        for d in extracted:
            row = {"id": d["id"], "file": d["file"], "line": d["line"],
                   "construct": d["construct"],
                   "alternatives": [{"label": a["label"],
                                     "cases": [case_for(a["label"])] if case_for_every else []}
                                    for a in d["alternatives"]]}
            table.append(row)
        if extra_case:
            first = table[0]["alternatives"][0]
            first["cases"] = [extra_case]
        return table

    def case_for(label: str) -> str:
        if label in ("go", "stop", "*", "no-match") or label.startswith("arm"):
            return "arm_any"
        if label in ("left-success", "left-failure"):
            return "andor_ok"
        return "mode_go"

    def run_checker(table: dict, cases_env: str | None) -> int:
        table_path = os.path.join(run_dir, "table.json")
        with open(table_path, "w") as fh:
            json.dump(table, fh)
        env = dict(os.environ)
        if cases_env is not None:
            env["CASES"] = cases_env
        else:
            env.pop("CASES", None)
        # execute harness ourselves honoring CASES, reuse via --cases-log
        proc = subprocess.run(["bash", os.path.join(run_dir, "harness.sh")],
                              capture_output=True, text=True, env=env)
        log_path = os.path.join(run_dir, "cases.log")
        with open(log_path, "w") as fh:
            fh.write(proc.stdout)
        checker = subprocess.run(
            [sys.executable, os.path.abspath(__file__),
             "--baseline-snapshot", base_snap, "--candidate-snapshot", cand_snap,
             "--table", table_path, "--cases-log", log_path],
            capture_output=True, text=True)
        return checker.returncode

    # (a) complete table + all executed => 0
    table_a = build_table("a", True, None)
    rc = run_checker(table_a, None)
    print(f"selftest a (complete+executed) rc={rc} (expect 0)")
    ok &= rc == 0
    # (b) incomplete table (first alternative without case) => 1
    table_b = build_table("b", True, None)
    table_b[0]["alternatives"][0]["cases"] = []
    rc = run_checker(table_b, None)
    print(f"selftest b (incomplete table) rc={rc} (expect 1)")
    ok &= rc == 1
    # (c) referenced case not executed (CASES subset omits mode_stop... ensure it is referenced)
    table_c = build_table("c", True, None)
    for row in table_c:
        if row["construct"] == "if":
            for alt in row["alternatives"]:
                alt["cases"] = ["mode_stop"]
    rc = run_checker(table_c, "mode_go andor_ok arm_any")
    print(f"selftest c (case not executed) rc={rc} (expect 1)")
    ok &= rc == 1
    # (d) unsupported construct (select) in candidate => 2
    sel_entry = _mini_entry(run_dir, "select.sh")
    _mini_snapshot(cand_snap, "G-BASH-MATRIX", [entry, sel_entry])
    rc = run_checker(table_a, None)
    print(f"selftest d (unsupported select) rc={rc} (expect 2)")
    ok &= rc == 2

    # (e) scope = CHANGED lines only: a modified file whose UNCHANGED region has
    # prose containing 'until' and an unbalanced `if` must not block, and only
    # the decision on the added lines needs a case.
    scoped_path = os.path.join(tree, "scoped.sh")
    with open(scoped_path, "w") as fh:
        fh.write(SCOPED_OLD)
    scoped_old_entry = _mini_entry(run_dir, "scoped.sh")
    with open(scoped_path, "w") as fh:
        fh.write(SCOPED_OLD + SCOPED_ADDED)
    scoped_new_entry = _mini_entry(run_dir, "scoped.sh")
    scoped_decisions = extract_decisions(
        "scoped.sh", (SCOPED_OLD + SCOPED_ADDED).encode(),
        changed_line_set(SCOPED_OLD.encode(), (SCOPED_OLD + SCOPED_ADDED).encode()))
    scoped_table = [{"id": d["id"], "file": d["file"], "line": d["line"],
                     "construct": d["construct"],
                     "alternatives": [{"label": a["label"], "cases": ["mode_go"]}
                                      for a in d["alternatives"]]}
                    for d in scoped_decisions]
    ok &= len(scoped_decisions) == 1
    _mini_snapshot(base_snap, "G-BASH-MATRIX", [scoped_old_entry])
    _mini_snapshot(cand_snap, "G-BASH-MATRIX", [scoped_new_entry])
    rc = run_checker(scoped_table, None)
    print(f"selftest e (only changed lines audited; {len(scoped_decisions)} decision) "
          f"rc={rc} (expect 0)")
    ok &= rc == 0

    # (f) a modified NON-bash file (docs/table/python) is never parsed as shell.
    notes_path = os.path.join(tree, "notes.md")
    with open(notes_path, "w") as fh:
        fh.write("wait until ready\n")
    notes_old = _mini_entry(run_dir, "notes.md")
    with open(notes_path, "w") as fh:
        fh.write("if unbalanced && prose until select\n")
    notes_new = _mini_entry(run_dir, "notes.md")
    _mini_snapshot(base_snap, "G-BASH-MATRIX", [scoped_old_entry, notes_old])
    _mini_snapshot(cand_snap, "G-BASH-MATRIX", [scoped_new_entry, notes_new])
    rc = run_checker(scoped_table, None)
    print(f"selftest f (modified non-bash file ignored) rc={rc} (expect 0)")
    ok &= rc == 0

    # (g) Bash under tests/ (the harness itself) is an instrument, not a subject.
    os.makedirs(os.path.join(tree, "tests"), exist_ok=True)
    inner_path = os.path.join(tree, "tests", "inner.sh")
    with open(inner_path, "w") as fh:
        fh.write("#!/usr/bin/env bash\necho old\n")
    inner_old = _mini_entry(run_dir, "tests/inner.sh")
    with open(inner_path, "w") as fh:
        fh.write(SELECT_SH)
    inner_new = _mini_entry(run_dir, "tests/inner.sh")
    _mini_snapshot(base_snap, "G-BASH-MATRIX", [scoped_old_entry, inner_old])
    _mini_snapshot(cand_snap, "G-BASH-MATRIX", [scoped_new_entry, inner_new])
    rc = run_checker(scoped_table, None)
    print(f"selftest g (modified Bash under tests/ ignored) rc={rc} (expect 0)")
    ok &= rc == 0

    _mini_snapshot(base_snap, "G-BASH-MATRIX", [])
    _mini_snapshot(cand_snap, "G-BASH-MATRIX", [entry])
    # Retain M1's parser controls alongside main's changed-line checks.
    heredoc_sh = (
        "#!/usr/bin/env bash\n"
        "SENTINEL='# <<< fake settings <<<'\n"
        "# comment mentioning <<NOTAHEREDOC\n"
        "cat <<'EOF'\n"
        "select body in x; do :; done\n"
        "EOF\n"
        "cat <<PLAIN\n"
        "select body in x; do :; done\n"
        "PLAIN\n"
        "if true; then\n  echo kept\nfi\n"
    )
    jq_sh = (
        "#!/usr/bin/env bash\n"
        'state=$(jq -c --argjson stage "$new_stage" --argjson until "$new_until" \\\n'
        "  '.x = {until: $until}' <<<\"$state\")\n"
    )
    elif_sh = (
        "#!/usr/bin/env bash\n"
        "if true; then\n  :\nelif false; then\n  :\nelse\n  :\nfi\n"
        "case x in\n  a) : ;;\n  *) : ;;\nesac\n"
    )
    for label, source, expected in (
        ("h heredoc spans", heredoc_sh, ["if"]),
        ("i jq argument until", jq_sh, []),
        ("j if/elif balance", elif_sh, ["if", "elif", "case-arm", "case-arm"]),
    ):
        try:
            constructs = [d["construct"] for d in extract_decisions(label, source.encode())]
            good = constructs == expected
            print(f"selftest {label}: constructs={constructs} expected={expected}")
        except Exception as exc:
            good = False
            print(f"selftest {label}: unexpectedly blocked: {exc}")
        ok &= good
    for source in ("until false; do :; done\n", "x=1; until false; do :; done\n",
                   "elif true; then :; fi\n"):
        try:
            extract_decisions("invalid.sh", source.encode())
            print(f"selftest unsupported/dangling construct NOT blocked: {source.strip()}")
            ok = False
        except Exception:
            print(f"selftest unsupported/dangling construct blocked: {source.strip()}")

    print(f"extracted ids={ids}")
    print(f"alternatives={alts}")
    if ok:
        print("SELFTEST_OK")
        return 0
    print("SELFTEST_FAIL")
    return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-snapshot")
    parser.add_argument("--candidate-snapshot")
    parser.add_argument("--table")
    parser.add_argument("--cases-log")
    parser.add_argument("--coverage-dir")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--run-dir")
    args = parser.parse_args(argv)
    if args.selftest:
        if not args.run_dir:
            with tempfile.TemporaryDirectory(prefix="bash-matrix-selftest-") as run_dir:
                args.run_dir = run_dir
                return cmd_selftest(args)
        return cmd_selftest(args)
    if not (args.baseline_snapshot and args.candidate_snapshot and args.table):
        return die_blocked("check mode requires --baseline-snapshot, "
                           "--candidate-snapshot and --table")
    return cmd_check(args)


if __name__ == "__main__":
    sys.exit(main())
