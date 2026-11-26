"""Read-only workspace explorer for agent panes (W3-P2, doc 13 §1).

Every pane carries a cwd (``AgentInfo.cwd``): this module inspects THAT
directory strictly read-only — a bounded file tree, a size-capped file
read, and the pane workspace's git status + diff. Three invariants hold
everywhere:

1. **Never escape the pane cwd.** Every path a caller names is resolved
   against the pane root and refused unless the REAL (symlink-followed)
   location stays inside it; ``..`` components are rejected before any
   filesystem access, absolute paths too, and symlinked directories are
   listed but never descended into.
2. **Reads are bounded.** The tree walk caps entries and depth, the file
   read caps bytes (64 KB), and the git diff caps output — a huge repo or
   a huge diff can never balloon the payload the PWA or the voice LLM
   receives.
3. **Never raises.** Every function returns a payload dict with
   ``available``/``error`` fields; a missing cwd, an unreadable file, a
   git failure or a timeout are ordinary answers, so the REST layer and
   the voice tool can pass the same shape through without mapping
   exceptions.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional

#: Hard cap for a single file read (S1): 64 KB, with a truncation flag.
MAX_FILE_BYTES = 64 * 1024

#: Hard caps for the tree walk: a pane workspace can be huge (node_modules
#: in a monorepo), so the walk stops at these bounds and says so.
MAX_TREE_ENTRIES = 2000
MAX_TREE_DEPTH = 12

#: Hard cap for git diff output (S2): a dirty workspace with a rewritten
#: file can produce megabytes of diff; 256 KB is plenty to review changes.
DIFF_MAX_BYTES = 256 * 1024

#: Subprocess budget for the git reads (S2). A hung git is an ordinary
#: unavailable answer, never a hung endpoint.
GIT_TIMEOUT_S = 10.0

#: Directories never descended into: VCS internals, package caches and
#: build outputs are noise for "what is in my workspace" and the usual
#: source of the huge-tree problem the caps exist for.
IGNORED_DIRS = frozenset({
    ".git", ".hg", ".svn",
    "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".venv", "venv", ".tox", ".eggs",
    "dist", "build", ".next", ".turbo", ".cache",
    ".idea", ".vscode", ".gradle", "target",
})


def _runner() -> Callable:
    return subprocess.run


def resolve_pane_root(cwd: str) -> Optional[Path]:
    """The pane cwd as a real, existing directory — or None.

    A pane whose cwd is empty, missing or no longer a directory has no
    explorable workspace; the caller answers ``available: False`` instead
    of guessing a folder (the resume_session rule: guessing is never an
    option).
    """
    if not cwd or not cwd.strip():
        return None
    try:
        root = Path(cwd).expanduser().resolve()
    except OSError:
        return None
    if not root.is_dir():
        return None
    return root


def _containment_error(root: Path, rel_path: str) -> Optional[str]:
    """Why ``rel_path`` is refused, or None when it is acceptable shape.

    Pure shape checks BEFORE any filesystem access: non-empty, relative,
    no ``..`` component, no NUL byte. Symlink escapes are caught later by
    the realpath containment check in :func:`read_file` — a name that
    passes here can still resolve outside the root through a symlink.
    """
    if not rel_path or not rel_path.strip():
        return "empty path"
    if "\x00" in rel_path:
        return "invalid path"
    candidate = Path(rel_path)
    if candidate.is_absolute() or rel_path.startswith("/"):
        return "absolute paths are not allowed"
    parts = candidate.parts
    if any(part == ".." for part in parts):
        return "path traversal is not allowed"
    return None


def list_tree(cwd: str) -> dict:
    """Bounded file tree of the pane cwd, size and mtime included (S1).

    Returns ``{cwd, available, entries, truncated, error?}``. Entries are
    ``{path, type, size, mtime}`` with workspace-relative posix paths,
    directories first then files, alphabetical inside each group.
    Symlinked directories are listed (type ``dir``) but never descended
    into — a symlink to anywhere could otherwise carry the walk outside
    the pane cwd. Hidden files ARE listed (dotfiles are workspace files
    too); only :data:`IGNORED_DIRS` names are skipped.
    """
    root = resolve_pane_root(cwd)
    if root is None:
        return {
            "cwd": cwd or "",
            "available": False,
            "entries": [],
            "truncated": False,
            "error": "the pane has no readable working directory",
        }
    entries: List[Dict] = []
    truncated = False
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = os.path.relpath(dirpath, root)
            depth = 0 if rel_dir == "." else rel_dir.count(os.sep) + 1
            if depth >= MAX_TREE_DEPTH:
                dirnames[:] = []
                truncated = True
                continue
            # Never descend into ignored names or symlinked dirs; symlink
            # dirs are still LISTED (see below) so the tree shows them.
            symlinked_dirs = [
                name for name in sorted(dirnames)
                if name not in IGNORED_DIRS
                and os.path.islink(os.path.join(dirpath, name))
            ]
            dirnames[:] = sorted(
                name for name in dirnames
                if name not in IGNORED_DIRS
                and not os.path.islink(os.path.join(dirpath, name))
            )
            if len(entries) >= MAX_TREE_ENTRIES:
                truncated = True
                break
            prefix = "" if rel_dir == "." else rel_dir.replace(os.sep, "/") + "/"
            for name in symlinked_dirs + sorted(dirnames):
                if len(entries) >= MAX_TREE_ENTRIES:
                    truncated = True
                    break
                full = os.path.join(dirpath, name)
                try:
                    stat = os.lstat(full)
                except OSError:
                    continue
                entries.append({
                    "path": prefix + name,
                    "type": "dir",
                    "size": 0,
                    "mtime": stat.st_mtime,
                })
            for name in sorted(filenames):
                if len(entries) >= MAX_TREE_ENTRIES:
                    truncated = True
                    break
                if name in IGNORED_DIRS:
                    continue
                full = os.path.join(dirpath, name)
                try:
                    stat = os.lstat(full)
                except OSError:
                    continue
                entries.append({
                    "path": prefix + name,
                    "type": "file",
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                })
        return {
            "cwd": str(root),
            "available": True,
            "entries": entries,
            "truncated": truncated,
            "error": None,
        }
    except OSError as exc:
        return {
            "cwd": str(root),
            "available": False,
            "entries": [],
            "truncated": False,
            "error": f"workspace tree unavailable: {exc}",
        }


def read_file(cwd: str, rel_path: str, cap: int = MAX_FILE_BYTES) -> dict:
    """Size-capped, traversal-safe read of ONE workspace file (S1).

    ``rel_path`` is workspace-relative. The guard is two-layered: shape
    checks (``..``, absolute, empty, NUL) refuse BEFORE the filesystem,
    then the realpath containment check refuses anything that resolves
    outside the pane root — which is exactly what a symlink pointing out
    of the workspace does. A symlink that resolves INSIDE the root is an
    ordinary file. Text is decoded UTF-8 with replacement; a NUL sniff in
    the head marks the file binary and its bytes are never served.

    Returns ``{path, size, mtime, truncated, binary, content, error?}``;
    ``content`` is capped at ``cap`` bytes with ``truncated`` saying so.
    """
    root = resolve_pane_root(cwd)
    if root is None:
        return {
            "path": rel_path or "",
            "available": False,
            "error": "the pane has no readable working directory",
        }
    shape_error = _containment_error(root, rel_path)
    if shape_error is not None:
        return {"path": rel_path, "available": False, "error": shape_error}

    candidate = (root / rel_path).resolve()
    try:
        root_real = root.resolve()
    except OSError:
        return {"path": rel_path, "available": False, "error": "unresolvable workspace root"}
    inside = candidate == root_real or root_real in candidate.parents
    if not inside:
        return {
            "path": rel_path,
            "available": False,
            "error": "path escapes the workspace (symlink or traversal)",
        }
    if not candidate.is_file():
        return {"path": rel_path, "available": False, "error": "not a readable file"}
    try:
        stat = candidate.stat()
        with open(candidate, "rb") as handle:
            head = handle.read(8192)
            if b"\x00" in head:
                return {
                    "path": rel_path,
                    "available": True,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                    "binary": True,
                    "truncated": False,
                    "content": None,
                    "error": "binary file: content not served",
                }
            handle.seek(0)
            raw = handle.read(cap + 1)
    except OSError as exc:
        return {"path": rel_path, "available": False, "error": f"read failed: {exc}"}
    truncated = len(raw) > cap
    if truncated:
        raw = raw[:cap]
    return {
        "path": rel_path,
        "available": True,
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "binary": False,
        "truncated": truncated,
        "content": raw.decode("utf-8", errors="replace"),
        "error": None,
    }


def _parse_status_line(line: str) -> Optional[Dict[str, str]]:
    """One ``git status --porcelain`` line → ``{status, path}``.

    The first two columns are the index/worktree codes; the rest is the
    path (rename lines carry ``old -> new`` and report the NEW path — the
    one that exists in the worktree now).
    """
    if not line or len(line) < 4:
        return None
    status = line[:2]
    path = line[3:]
    if " -> " in path:
        path = path.rsplit(" -> ", 1)[1]
    path = path.strip().strip('"')
    if not path:
        return None
    return {"status": status.strip() or "?", "path": path}


def git_changes(cwd: str, runner: Optional[Callable] = None) -> dict:
    """``git status`` + ``git diff`` inside the pane cwd (S1/S2).

    Runs the git CLI read-only in the pane's workspace: ``--porcelain``
    status entries, then the diff of tracked changes — against HEAD when
    the repository has commits, working-tree-only otherwise (a fresh
    ``git init`` has no HEAD). Output is capped at
    :data:`DIFF_MAX_BYTES` with a ``truncated`` flag; every git failure,
    a non-repo workspace and a timeout are ordinary payloads:
    ``{cwd, available, is_git_repo, entries, diff, truncated, error?}``.
    """
    root = resolve_pane_root(cwd)
    if root is None:
        return {
            "cwd": cwd or "",
            "available": False,
            "is_git_repo": False,
            "entries": [],
            "diff": None,
            "truncated": False,
            "error": "the pane has no readable working directory",
        }
    run = runner or _runner()

    def _git(*args: str) -> subprocess.CompletedProcess:
        return run(
            ["git", "-c", "core.quotepath=false", *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
            cwd=str(root),
        )

    try:
        probe = _git("rev-parse", "--is-inside-work-tree")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _git_failure(root, exc)
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        return {
            "cwd": str(root),
            "available": True,
            "is_git_repo": False,
            "entries": [],
            "diff": None,
            "truncated": False,
            "error": None,
        }
    try:
        status = _git("status", "--porcelain")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _git_failure(root, exc)
    entries: List[Dict[str, str]] = []
    for line in status.stdout.splitlines():
        parsed = _parse_status_line(line)
        if parsed is not None:
            entries.append(parsed)

    diff_text = ""
    truncated = False
    try:
        head = _git("rev-parse", "--verify", "--quiet", "HEAD")
        diff = _git("diff", "HEAD") if head.returncode == 0 else _git("diff")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _git_failure(root, exc, entries=entries)
    if diff.returncode == 0:
        if len(diff.stdout) > DIFF_MAX_BYTES:
            diff_text = diff.stdout[:DIFF_MAX_BYTES]
            truncated = True
        else:
            diff_text = diff.stdout
    return {
        "cwd": str(root),
        "available": True,
        "is_git_repo": True,
        "entries": entries,
        "diff": diff_text,
        "truncated": truncated,
        "error": None,
    }


def _git_failure(root: Path, exc: Exception, entries: Optional[List] = None) -> dict:
    return {
        "cwd": str(root),
        "available": False,
        "is_git_repo": None,
        "entries": entries or [],
        "diff": None,
        "truncated": False,
        "error": f"git unavailable: {exc}",
    }


__all__ = [
    "DIFF_MAX_BYTES",
    "GIT_TIMEOUT_S",
    "MAX_FILE_BYTES",
    "MAX_TREE_DEPTH",
    "MAX_TREE_ENTRIES",
    "git_changes",
    "list_tree",
    "read_file",
    "resolve_pane_root",
]
