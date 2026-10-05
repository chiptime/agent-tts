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

# A reserved word only starts a construct at a COMMAND START: head of the
# logical line or right after a list/pipe/subshell/group separator. A word in
# argument position is data, not a loop: the jq option argument in
# ``--argjson until "$new_until"`` must not block. Boundaries mirror _IF_RE's,
# plus pipe/subshell/group openers.
_UNSUPPORTED_RE = re.compile(
    r"(?:^|&&|\|\||\||;|\(|\{)\s*("
    + "|".join(kw.strip() for kw in UNSUPPORTED_KEYWORDS)
    + r")(\s|$)"
)

_HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")
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


def diff_modified(baseline_path: str, candidate_path: str, gate: str):
    """Yield (path, entry) for candidate files that are new or changed vs baseline."""
    base = load_scope(baseline_path, gate)
    cand = load_scope(candidate_path, gate)
    for path, entry in sorted(cand.items()):
        old = base.get(path)
        if old is None or old.get("sha256") != entry.get("sha256"):
            yield path, entry


# -- bash decision extraction (subset, fail-closed) ---------------------------


def _join_continuations(lines: list[str]) -> list[tuple[int, str]]:
    """Join backslash-continued lines into logical lines tagged with first
    physical line number."""
    out: list[tuple[int, str]] = []
    buf, start = "", None
    for idx, raw in enumerate(lines, start=1):
        if start is None:
            start = idx
        if raw.endswith("\\"):
            buf += raw[:-1] + " "
            continue
        out.append((start, buf + raw))
        buf, start = "", None
    if buf:
        out.append((start or 1, buf))
    return out


def _blank_strings_and_comment(line: str) -> str:
    """The line with quoted strings blanked and a trailing comment removed.

    Decision scanning must not read string LITERALS: a message like
    ``"active until %s"`` is not an ``until`` loop, and a comment is not
    code. Keeping the quotes themselves preserves nothing important for the
    construct regexes used here.
    """
    out = []
    quote = None
    for ch in line:
        if quote:
            if ch == quote:
                quote = None
            out.append(" " if ch != "\\" else " ")
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            continue
        out.append(ch)
    text = "".join(out)
    # trailing comment (unquoted # at top level is already unquoted here)
    if "#" in text:
        depth_quote = None
        for i, ch in enumerate(text):
            if depth_quote:
                if ch == depth_quote:
                    depth_quote = None
            elif ch in ("'", '"'):
                depth_quote = ch
            elif ch == "#":
                return text[:i]
    return text


def extract_decisions(path: str, blob: bytes, only_lines: set[int] | None = None) -> list[dict]:
    """Enumerate decision alternatives for one Bash file.

    Supported: if/elif/else/fi, case arms, && and || lists. Heredoc bodies
    are skipped. Anything unparseable raises Blocked (caller exits 2).
    With ``only_lines`` (the lines a change added or modified, per the
    snapshot blob diff) the enumeration is scoped to CHANGED code: the
    table only owes entries for decisions the change actually touched —
    unchanged legacy constructs are not this change's alternatives to
    prove. ``None`` keeps whole-file semantics (compat).
    """
    text = blob.decode("utf-8", "replace")
    lines = text.splitlines()
    heredoc_skip = _heredoc_spans(lines)
    logical = _join_continuations(lines)
    decisions: list[dict] = []

    class Blocked(Exception):
        pass

    stack: list[str] = []  # entries: "if" | "case"
    for lineno, line in logical:
        if any(lo <= lineno <= hi for lo, hi in heredoc_skip):
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        scanned = _blank_strings_and_comment(line)
        unsupported = _UNSUPPORTED_RE.search(scanned)
        if unsupported:
            raise Blocked(
                f"{path}:{lineno}: unsupported construct '{unsupported.group(1)}'"
            )
        if _CASE_RE.search(scanned):
            stack.append("case")
            continue
        if stack and stack[-1] == "case":
            if _ESAC_RE.match(stripped):
                stack.pop()
                continue
            arm = _CASE_ARM_RE.match(line)
            if arm:
                label = arm.group(1).strip()
                if only_lines is None or lineno in only_lines:
                    decisions.append(
                        {
                            "id": f"{path}:{lineno}:case-arm",
                            "file": path,
                            "line": lineno,
                            "construct": "case-arm",
                            "alternatives": [{"label": label, "cases": []}],
                        }
                    )
                continue
            # inside a case arm body: fall through to if/&& handling
        if_match = _IF_RE.search(scanned)
        if if_match:
            construct = "elif" if if_match.group(1) else "if"
            if construct == "elif":
                # `elif` is one more branch of the ENCLOSING if frame: the
                # frame's single `fi` closes the original `if`, so an elif
                # never pushes a frame of its own. An elif with no open if
                # frame is unparseable here: fail closed.
                if not (stack and stack[-1] == "if"):
                    raise Blocked(f"{path}:{lineno}: 'elif' without an open if")
            if only_lines is None or lineno in only_lines:
                decisions.append(
                    {
                        "id": f"{path}:{lineno}:{construct}",
                        "file": path,
                        "line": lineno,
                        "construct": construct,
                        "alternatives": [
                            {"label": "cond-true", "cases": []},
                            {"label": "cond-false", "cases": []},
                        ],
                        "_needs_nomatch": False,
                    }
                )
            if construct == "if":
                stack.append("if")
            continue
        if stack and stack[-1] == "if" and _FI_RE.match(stripped):
            stack.pop()
            continue
        if stack and stack[-1] == "if" and _ELSE_RE.match(stripped):
            continue
        andor = _ANDOR_RE.search(scanned)
        if andor and not _IF_RE.search(scanned) and not _CASE_RE.search(scanned):
            if only_lines is None or lineno in only_lines:
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
                        "_needs_nomatch": False,
                    }
                )
    if stack:
        raise Blocked(f"{path}: unbalanced {stack} (cannot enumerate reliably)")

    # Post-process contiguous case-arm runs: a "no-match" alternative exists
    # on the last arm of a run ONLY when the run has no '*' arm.
    result: list[dict] = []
    i = 0
    while i < len(decisions):
        if decisions[i]["construct"] == "case-arm":
            j = i
            while j < len(decisions) and decisions[j]["construct"] == "case-arm":
                j += 1
            run = decisions[i:j]
            if not any(a["label"] == "*" for d in run for a in d["alternatives"]):
                run[-1]["alternatives"].append({"label": "no-match", "cases": []})
            result.extend(run)
            i = j
        else:
            result.append(decisions[i])
            i += 1
    return result


def _heredoc_open_word(line: str) -> str | None:
    """Delimiter word of a real heredoc opener in ``line``, else None.

    Applies the exact string/comment-blanking rule of
    ``_blank_strings_and_comment`` to decide where CODE is: a ``<<`` inside a
    string literal or a comment (e.g. the ``'# <<< herdr-tts settings <<<'``
    sentinels) is not a heredoc. The blanked copy is length-aligned with
    ``line`` up to the comment cut and keeps code bytes verbatim, so a regex
    hit whose first ``<`` is blanked — or falls inside the cut comment tail —
    is rejected. The delimiter word itself is read from the raw line, because
    a quoted ``<<'EOF'`` delimiter is operator syntax, not a string literal,
    and blanking would erase it.
    """
    masked = _blank_strings_and_comment(line)
    for match in _HEREDOC_RE.finditer(line):
        if match.start() < len(masked) and masked[match.start()] == "<":
            return match.group(2)
    return None


def _heredoc_spans(lines: list[str]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    open_word: str | None = None
    start = 0
    for idx, line in enumerate(lines, start=1):
        if open_word is None:
            word = _heredoc_open_word(line)
            if word:
                open_word, start = word, idx + 1
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


def changed_lines_between(baseline_path: str, candidate_path: str, gate: str,
                          path: str, blob: bytes) -> set[int]:
    """Lines (1-based) added or modified in one file vs the baseline snapshot
    (insert/replace opcodes of the blob diff). Empty set = unchanged."""
    base = load_scope(baseline_path, gate)
    old = base.get(path)
    new_lines = blob.decode("utf-8", "replace").splitlines()
    old_lines = (read_blob(baseline_path, old).decode("utf-8", "replace").splitlines()
                 if old else [])
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    changed: set[int] = set()
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag in ("insert", "replace"):
            changed.update(range(j1 + 1, j2 + 1))
    return changed


def cmd_check(args) -> int:
    extracted: list[dict] = []
    bash_modified = 0
    harness_excluded = 0
    base_scope = load_scope(args.baseline_snapshot, "G-BASH-MATRIX")
    for path, entry in diff_modified(args.baseline_snapshot, args.candidate_snapshot, "G-BASH-MATRIX"):
        blob = read_blob(args.candidate_snapshot, entry)
        if not (
            path.endswith(".sh")
            or blob.split(b"\n", 1)[0].decode("utf-8", "replace").strip()
            .startswith("#!")
        ):
            continue  # same Bash-file rule as the changed-lines gate (json/py)
        bash_modified += 1
        # INSTRUMENT, not product: the case-runner harness whose own protocol
        # ("CASE <name> OK|FAIL") this gate consumes cannot simultaneously be
        # the measured surface — its assertion guards (`x || return 1`) fail
        # exactly when a case fails, so their failure arms are unmeasurable
        # by construction. The harness's modified lines are measured by the
        # G-BASH-LINES gate (they execute with the run); decision
        # alternatives are owed for PRODUCT Bash only.
        if path.endswith("tests/host_cli_cases.sh"):
            harness_excluded += 1
            continue
        only = changed_lines_between(args.baseline_snapshot, args.candidate_snapshot,
                                     "G-BASH-MATRIX", path, blob)
        try:
            extracted.extend(extract_decisions(path, blob, only_lines=only))
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

    print(f"modified_files={bash_modified} (harness-excluded-as-instrument={harness_excluded})")
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
    _mini_snapshot(cand_snap, "G-BASH-MATRIX", [entry])

    # (e) MQ-05 scope check: a modified file whose UNCHANGED region holds
    # decisions owes NO table entries for them — only changed-line decisions
    # count. subject_v2 changes only the else-arm body line of decisions.sh:
    # the if/case decisions themselves stay out of the extracted set.
    with open(os.path.join(tree, "subject_v2_base.sh"), "w") as fh:
        fh.write(DECISIONS_SH)
    with open(os.path.join(tree, "subject_v2.sh"), "w") as fh:
        fh.write(DECISIONS_SH.replace("  echo stop\n", "  echo stop-two\n"))
    import hashlib as _h
    _bdir = os.path.join(run_dir, "blobs")
    _orig = open(os.path.join(tree, "subject_v2_base.sh"), "rb").read()
    _osha = _h.sha256(_orig).hexdigest()
    with open(os.path.join(_bdir, _osha), "wb") as fh:
        fh.write(_orig)
    v2_entry = _mini_entry(run_dir, "subject_v2.sh")
    base2 = os.path.join(run_dir, "baseline2.json")
    _mini_snapshot(base2, "G-BASH-MATRIX",
                   [{"path": "subject_v2.sh", "sha256": _osha, "mode": "0o755",
                     "blob_ref": _osha}])
    _mini_snapshot(cand_snap, "G-BASH-MATRIX", [v2_entry])
    blob2 = open(os.path.join(tree, "subject_v2.sh"), "rb").read()
    only2 = changed_lines_between(base2, cand_snap, "G-BASH-MATRIX", "subject_v2.sh", blob2)
    extracted2 = extract_decisions("subject_v2.sh", blob2, only_lines=only2)
    print(f"selftest e (diff scope) changed_lines={sorted(only2)} "
          f"extracted={len(extracted2)} (expect 0 decisions)")
    ok &= len(extracted2) == 0

    # (f) string literals are not constructs: 'until' inside a message must
    # NOT block; a real `until` loop still must.
    with open(os.path.join(tree, "until_string.sh"), "w") as fh:
        fh.write('#!/usr/bin/env bash\nMSG="snooze active until %s"\necho "$MSG"\n')
    dec = extract_decisions("until_string.sh",
                            open(os.path.join(tree, "until_string.sh"), "rb").read())
    print(f"selftest f1 (string 'until' not a construct) extracted={len(dec)} (expect 0)")
    ok &= len(dec) == 0
    try:
        with open(os.path.join(tree, "until_real.sh"), "w") as fh:
            fh.write('#!/usr/bin/env bash\nuntil false; do echo x; done\n')
        extract_decisions("until_real.sh",
                          open(os.path.join(tree, "until_real.sh"), "rb").read())
        print("selftest f2 (real until still blocks) FAILED")
        ok &= False
    except Exception:
        print("selftest f2 (real until still blocks) ok")

    # (g) the case-runner harness is the INSTRUMENT: its own assertion guards
    # are never the measured decision surface, even when modified.
    harness_name = "tests/host_cli_cases.sh"
    with open(os.path.join(tree, "host_cli_cases.sh"), "w") as fh:
        fh.write('#!/usr/bin/env bash\ntrue || return 1\n')
    h_entry = _mini_entry(run_dir, "host_cli_cases.sh")
    # rename the blob entry to the harness path shape
    h_entry = {"path": harness_name, "sha256": h_entry["sha256"],
               "mode": "0o755", "blob_ref": h_entry["blob_ref"]}
    _mini_snapshot(cand_snap, "G-BASH-MATRIX", [entry, h_entry])
    proc_rc = run_checker(table_a, None)
    print(f"selftest g (harness excluded as instrument) rc={proc_rc} (expect 0)")
    ok &= proc_rc == 0
    _mini_snapshot(cand_snap, "G-BASH-MATRIX", [entry])

    # (h) heredoc openers live in CODE only: a `<<` inside a string literal
    # or a comment (the '# <<< herdr-tts settings <<<' sentinels) must not
    # open a skip span that swallows real decisions; real heredoc bodies
    # (quoted and bare delimiters) are skipped, and decisions after the
    # terminator still count. A bogus span here would hide the trailing if
    # (or worse, un-hide the body's `select`).
    heredoc_sh = (
        "#!/usr/bin/env bash\n"
        "SENTINEL='# <<< fake settings <<<'\n"
        "# comment mentioning <<NOTAHEREDOC\n"
        "cat <<'EOF'\n"
        "if body-then; then :; fi\n"
        "EOF\n"
        "cat <<PLAIN\n"
        "select x in a; do :; done\n"
        "PLAIN\n"
        "if [[ -n \"$SENTINEL\" ]]; then\n"
        "  echo kept\n"
        "fi\n"
    )
    try:
        dec = extract_decisions("heredoc.sh", heredoc_sh.encode())
        print(f"selftest h (heredoc spans) extracted={len(dec)} (expect 1)")
        ok &= len(dec) == 1 and dec[0]["construct"] == "if"
    except Exception as exc:
        print(f"selftest h (heredoc spans) BLOCKED unexpectedly: {exc}")
        ok &= False

    # (i) argument-position `until` (the jq --argjson option, as in
    # herdr-tts) is data, not a loop; the same word at a command start
    # (here after ';') still blocks.
    jq_sh = (
        "#!/usr/bin/env bash\n"
        "state=$(jq -c --argjson stage \"$new_stage\" --argjson until \"$new_until\" \\\n"
        "  '.x = ((.x // {}) + {until: $until})' <<<\"$state\")\n"
    )
    try:
        dec = extract_decisions("jq_arg.sh", jq_sh.encode())
        print(f"selftest i1 (jq --argjson until) extracted={len(dec)} (expect 0)")
        ok &= len(dec) == 0
    except Exception as exc:
        print(f"selftest i1 (jq --argjson until) BLOCKED unexpectedly: {exc}")
        ok &= False
    try:
        with open(os.path.join(tree, "until_cmd.sh"), "w") as fh:
            fh.write("#!/usr/bin/env bash\nx=1; until false; do echo x; done\n")
        extract_decisions("until_cmd.sh",
                          open(os.path.join(tree, "until_cmd.sh"), "rb").read())
        print("selftest i2 (real until after ';') FAILED")
        ok &= False
    except Exception:
        print("selftest i2 (real until after ';') ok")

    # (j) `elif` is a branch of its ENCLOSING if frame: no extra frame is
    # pushed, the file stays balanced, the elif is its own decision, and a
    # dangling elif still fails closed.
    elif_sh = (
        "#!/usr/bin/env bash\n"
        "f() {\n"
        "  if [[ \"$1\" == a ]]; then\n"
        "    echo a\n"
        "  elif [[ \"$1\" == b ]]; then\n"
        "    echo b\n"
        "  else\n"
        "    echo c\n"
        "  fi\n"
        "  case \"$1\" in\n"
        "    a) echo arm-a ;;\n"
        "    *) echo arm-any ;;\n"
        "  esac\n"
        "}\n"
    )
    try:
        dec = extract_decisions("elif.sh", elif_sh.encode())
        constructs = [d["construct"] for d in dec]
        want = ["if", "elif", "case-arm", "case-arm"]
        print(f"selftest j1 (if/elif balance) constructs={constructs} (expect {want})")
        ok &= constructs == want
    except Exception as exc:
        print(f"selftest j1 (if/elif balance) BLOCKED unexpectedly: {exc}")
        ok &= False
    try:
        extract_decisions("bad_elif.sh",
                          b"#!/usr/bin/env bash\nelif true; then :; fi\n")
        print("selftest j2 (dangling elif must block) FAILED")
        ok &= False
    except Exception:
        print("selftest j2 (dangling elif must block) ok")

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
            return die_blocked("--selftest requires --run-dir")
        return cmd_selftest(args)
    if not (args.baseline_snapshot and args.candidate_snapshot and args.table):
        return die_blocked("check mode requires --baseline-snapshot, "
                           "--candidate-snapshot and --table")
    return cmd_check(args)


if __name__ == "__main__":
    sys.exit(main())
