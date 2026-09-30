#!/usr/bin/env python3
"""Deterministic repository source snapshot for the voice-stack verification loop.

Emits a normalized JSON snapshot of the repository source identity. The
``binding`` digest is computed over canonical JSON of ``{"schema", "package",
"scopes", "env"}`` only: the timestamp (``ts``) and the repository block
(``repo``) are deliberately excluded so that two runs over an unchanged tree
produce an identical binding, while any byte, path, or mode change inside a
tracked scope changes it.

Package scope: every file under ``docs/voice-stack/``. Gate scopes: see
``SCOPE_TABLE`` below (overlaps between gates are intentional; each gate gets
its own view).

Stdlib only. Python 3.11+ (developed against 3.14).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "1"
PACKAGE_ROOT = "docs/voice-stack"
RUNS_DIRNAME = "voice-stack-runs"

# Directory names pruned while walking any root.
PRUNE_DIRNAMES = frozenset({
    ".git", ".venv", "__pycache__", "node_modules", "dist",
    ".pytest_cache", "private", "secrets", "credentials",
})
# File names skipped while walking any root.
SKIP_FILENAMES = frozenset({".coverage", "coverage.json", "lcov.info"})

# Gate name -> tuple of (path, prune_subdirs). prune_subdirs removes those
# directory names during that scope's walk. A listed path that does not exist
# yields files: [].
ScopeEntry = tuple[str, tuple[str, ...]]
SCOPE_TABLE: dict[str, tuple[ScopeEntry, ...]] = {
    "G-ENG-PY": (
        ("engine/src/agent_tts", ()),
        ("engine/tests", ()),
        ("engine/pyproject.toml", ()),
        ("engine/uv.lock", ()),
    ),
    "G-BRN-PY": (
        ("hosts/herdr/brain/src/herdr_brain", ()),
        ("hosts/herdr/brain/tests", ("js", "e2e")),
        ("hosts/herdr/brain/pyproject.toml", ()),
        ("hosts/herdr/brain/uv.lock", ()),
    ),
    "G-HOST-PY": (
        ("hosts/herdr/tts-plugin/lib", ()),
        ("hosts/herdr/tts-plugin/tests", ("matrix",)),
        ("hosts/herdr/tts-plugin/scripts/bootstrap.sh", ()),
        ("hosts/herdr/tts-plugin/pyproject.toml", ()),
    ),
    "G-JS": (
        ("hosts/herdr/brain/src/herdr_brain/static", ()),
        ("hosts/herdr/brain/tests/js", ()),
    ),
    "G-E2E": (
        ("hosts/herdr/brain/tests/e2e", ()),
    ),
    "G-BASH-LINES": (
        ("hosts/herdr/tts-plugin/bin", ()),
        ("hosts/herdr/tts-plugin/lib", ()),
        ("hosts/herdr/tts-plugin/tests", ()),
    ),
    "G-BASH-MATRIX": (
        ("hosts/herdr/tts-plugin/bin", ()),
        ("hosts/herdr/tts-plugin/tests/matrix", ()),
        ("hosts/herdr/tts-plugin/tests/host_cli_cases.sh", ()),
    ),
    "G-BOUNDARY": (
        ("engine/tests/test_monorepo_boundaries.py", ()),
    ),
    "G-SMOKE": (
        ("hosts/herdr/tts-plugin/scripts/smoke-tests.sh", ()),
    ),
    # Global regression view: union of all paths above, with the FULL tests
    # directories and no js/e2e/matrix pruning. Plain-file roots listed after
    # their parent directory are deduped by path (first occurrence wins).
    "G-X": (
        ("engine/src/agent_tts", ()),
        ("engine/tests", ()),
        ("engine/pyproject.toml", ()),
        ("engine/uv.lock", ()),
        ("hosts/herdr/brain/src/herdr_brain", ()),
        ("hosts/herdr/brain/tests", ()),
        ("hosts/herdr/brain/pyproject.toml", ()),
        ("hosts/herdr/brain/uv.lock", ()),
        ("hosts/herdr/tts-plugin/lib", ()),
        ("hosts/herdr/tts-plugin/tests", ()),
        ("hosts/herdr/tts-plugin/scripts/bootstrap.sh", ()),
        ("hosts/herdr/tts-plugin/pyproject.toml", ()),
        ("hosts/herdr/tts-plugin/bin", ()),
        ("hosts/herdr/tts-plugin/tests/matrix", ()),
        ("hosts/herdr/tts-plugin/tests/host_cli_cases.sh", ()),
        ("engine/tests/test_monorepo_boundaries.py", ()),
        ("hosts/herdr/tts-plugin/scripts/smoke-tests.sh", ()),
    ),
}


class SnapshotError(Exception):
    """Hard error: git failure, unreadable path, or unwritable output."""


def find_repo_root() -> Path:
    """First directory at or above the script's location containing .git."""
    here = Path(__file__).resolve().parent
    for candidate in (here, *here.parents):
        if (candidate / ".git").exists():
            return candidate
    return Path.cwd()


def _skip_filename(name: str) -> bool:
    return (
        name in SKIP_FILENAMES
        or name.startswith(".env")
        or name.startswith(".coverage")
        or name.endswith((".pem", ".key", ".p12"))
    )


def list_files_under(repo_root: Path, root_rel: str,
                     prune_subdirs: tuple[str, ...]) -> list[str]:
    """Sorted repo-relative forward-slash paths under root_rel ([] if absent)."""
    base = repo_root / root_rel
    if not base.exists():
        return []
    if base.is_file():
        return [] if _skip_filename(base.name) else [root_rel]
    if not base.is_dir():
        return []
    prune = PRUNE_DIRNAMES | frozenset(prune_subdirs)
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(base, topdown=True):
        dirnames[:] = sorted(
            d for d in dirnames if d not in prune and d != RUNS_DIRNAME
        )
        for name in sorted(filenames):
            if _skip_filename(name):
                continue
            full = Path(dirpath) / name
            if RUNS_DIRNAME in full.relative_to(repo_root).parts:
                continue
            if not stat.S_ISREG(full.stat().st_mode):
                continue
            found.append(full.relative_to(repo_root).as_posix())
    return sorted(found)


def write_blob(blob_dir: Path, digest: str, data: bytes) -> None:
    """Content-addressed copy at <blob-dir>/<sha256>; write-then-os.replace."""
    blob_dir.mkdir(parents=True, exist_ok=True)
    target = blob_dir / digest
    if target.exists():
        return
    tmp = blob_dir / f".{digest}.tmp-{os.getpid()}"
    tmp.write_bytes(data)
    os.replace(tmp, target)


def build_file_entry(repo_root: Path, rel: str,
                     blob_dir: Path | None) -> dict:
    full = repo_root / rel
    st = full.stat()
    data = full.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    entry = {
        "path": rel,
        "sha256": digest,
        "mode": oct(st.st_mode & 0o777),
    }
    if blob_dir is None:
        entry["blob_ref"] = None
    else:
        write_blob(blob_dir, digest, data)
        entry["blob_ref"] = digest
    return entry


def build_package(repo_root: Path) -> dict:
    """All files under the package docs root; no blob_ref key (no blobs)."""
    entries = []
    for rel in list_files_under(repo_root, PACKAGE_ROOT, ()):
        full = repo_root / rel
        st = full.stat()
        entries.append({
            "path": rel,
            "sha256": hashlib.sha256(full.read_bytes()).hexdigest(),
            "mode": oct(st.st_mode & 0o777),
        })
    entries.sort(key=lambda e: e["path"])
    return {"files": entries}


def build_scopes(repo_root: Path, blob_dir: Path | None) -> dict:
    scopes: dict[str, dict] = {}
    for gate, roots in SCOPE_TABLE.items():
        seen: set[str] = set()
        entries: list[dict] = []
        for root_rel, prune_subdirs in roots:
            for rel in list_files_under(repo_root, root_rel, prune_subdirs):
                if rel in seen:
                    continue  # dedup by path, first occurrence wins
                seen.add(rel)
                entries.append(build_file_entry(repo_root, rel, blob_dir))
        entries.sort(key=lambda e: e["path"])
        scopes[gate] = {"files": entries}
    return scopes


def run_git(repo_root: Path, args: list[str]) -> bytes:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(repo_root), capture_output=True
        )
    except OSError as exc:
        raise SnapshotError(f"git failed to run: {exc}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise SnapshotError(f"git {' '.join(args)} failed: {detail}")
    return proc.stdout


def _distribution_version(name: str) -> str | None:
    try:
        return importlib.metadata.distribution(name).version
    except Exception:
        pass
    try:  # accept normalized import name as a fallback
        return importlib.metadata.distribution(name.replace("-", "_")).version
    except Exception:
        return None


def _command_output(argv: list[str]) -> str | None:
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    out = proc.stdout.decode("utf-8", "replace").strip()
    return out or None


def detect_env() -> dict:
    """Best-effort environment fingerprint; must never crash the snapshot."""
    try:
        os_name = platform.platform()
    except Exception:
        os_name = sys.platform
    node = None
    if shutil.which("node") is not None:
        node = _command_output(["node", "--version"])
    kcov = None
    kcov_path = shutil.which("kcov")
    if kcov_path is not None:
        version = _command_output([kcov_path, "--version"])
        if version is not None:
            kcov = version.splitlines()[0]
    return {
        "os": os_name,
        "python": sys.version.split()[0],
        "node": node,
        "coverage_tools": {
            "coverage": _distribution_version("coverage"),
            "pytest_playwright": _distribution_version("pytest-playwright"),
            "kcov": kcov,
        },
    }


def compute_binding(snapshot: dict) -> str:
    """sha256 over canonical JSON of schema+package+scopes+env (normalization:
    ts, repo, binding itself, and blob_ref pointers are excluded — blob_ref
    depends on whether --blob-dir was passed, never on tree identity)."""
    scopes = {}
    for gate, scope in snapshot["scopes"].items():
        files = [
            {k: v for k, v in entry.items() if k != "blob_ref"}
            for entry in scope["files"]
        ]
        scopes[gate] = {"files": files}
    payload = {
        "schema": snapshot["schema"],
        "package": snapshot["package"],
        "scopes": scopes,
        "env": snapshot["env"],
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="snapshot.py",
        description=(
            "Emit a deterministic, normalized snapshot of the repository "
            "source identity for the voice-stack verification loop. Prints a "
            "summary line 'binding=<64hex> files=<int>' to stdout on success."
        ),
    )
    parser.add_argument(
        "--out", metavar="PATH",
        help="write the snapshot JSON to PATH (required unless --print-binding)",
    )
    parser.add_argument(
        "--blob-dir", metavar="DIR",
        help=(
            "for every scope file (not package docs), write an immutable "
            "content-addressed copy <DIR>/<sha256> if absent and set blob_ref"
        ),
    )
    parser.add_argument(
        "--print-binding", action="store_true",
        help="also print the bare binding hex digest as its own stdout line",
    )
    args = parser.parse_args(argv)
    if not args.out and not args.print_binding:
        parser.error("--out is required unless --print-binding is given")

    try:
        repo_root = find_repo_root()
        head_sha = run_git(repo_root, ["rev-parse", "HEAD"]).decode(
            "utf-8", "replace"
        ).strip()
        status_bytes = run_git(repo_root, ["status", "--porcelain=v1"])
        blob_dir = Path(args.blob_dir) if args.blob_dir else None

        package = build_package(repo_root)
        scopes = build_scopes(repo_root, blob_dir)
        env = detect_env()

        snapshot = {
            "schema": SCHEMA_VERSION,
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "repo": {
                "head_sha": head_sha,
                "status_porcelain_sha256": hashlib.sha256(status_bytes).hexdigest(),
            },
            "package": package,
            "scopes": scopes,
            "env": env,
        }
        binding = compute_binding(snapshot)
        snapshot["binding"] = binding
        file_total = len(package["files"]) + sum(
            len(scope["files"]) for scope in scopes.values()
        )

        if args.out:
            try:
                with open(args.out, "w", encoding="utf-8") as fh:
                    fh.write(json.dumps(snapshot, indent=1, ensure_ascii=False))
                    fh.write("\n")
            except OSError as exc:
                print(f"error: cannot write --out {args.out}: {exc}",
                      file=sys.stderr)
                return 2

        if args.print_binding:
            print(binding)
        print(f"binding={binding} files={file_total}")
        return 0
    except SnapshotError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
