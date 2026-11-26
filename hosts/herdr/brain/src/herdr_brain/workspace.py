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
MAX_TREE_ENTRIES = 5000
MAX_TREE_DEPTH = 16

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
    ".runtime", ".agy-diagnostics", ".agy-attachments", ".codegraph",
    ".atl", ".coverage", ".opencode", ".herdr",
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


def list_tree(cwd: str, subpath: str = "", depth: Optional[int] = None) -> dict:
    """Bounded file tree of the pane cwd, size and mtime included (S1).

    Returns ``{cwd, available, entries, truncated, error?}``. Entries are
    ``{path, type, size, mtime}`` with workspace-relative posix paths,
    directories first then files, alphabetical inside each group.
    Symlinked directories are listed (type ``dir``) but never descended
    into — a symlink to anywhere could otherwise carry the walk outside
    the pane cwd. Hidden files ARE listed (dotfiles are workspace files
    too); only :data:`IGNORED_DIRS` names are skipped.

    If ``subpath`` is given, only descendants of that directory are listed.
    If ``depth`` is given, traversal stops at that relative depth limit.
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

    start_rel = ""
    if subpath and subpath.strip():
        shape_err = _containment_error(root, subpath)
        if shape_err is not None:
            return {
                "cwd": str(root),
                "available": False,
                "entries": [],
                "truncated": False,
                "error": shape_err,
            }
        clean_sub = subpath.strip("/\\")
        candidate = (root / clean_sub).resolve()
        if not candidate.is_dir() or not (candidate == root or root in candidate.parents):
            return {
                "cwd": str(root),
                "available": False,
                "entries": [],
                "truncated": False,
                "error": "directory not found or outside workspace",
            }
        start_rel = clean_sub

    entries: List[Dict] = []
    truncated = False
    max_depth = depth if (depth is not None and depth > 0) else MAX_TREE_DEPTH
    try:
        from collections import deque
        queue = deque([(start_rel, 0)])  # (rel_dir, cur_depth)
        while queue:
            rel_dir, cur_depth = queue.popleft()
            dirpath = os.path.join(root, rel_dir) if rel_dir else str(root)
            try:
                scandir_items = list(os.scandir(dirpath))
            except OSError:
                continue

            subdirs = []
            files = []
            for item in scandir_items:
                if item.name in IGNORED_DIRS:
                    continue
                try:
                    is_symlink = item.is_symlink()
                    is_dir = item.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if is_dir:
                    subdirs.append((item.name, is_symlink))
                else:
                    files.append(item.name)

            subdirs.sort(key=lambda x: x[0])
            files.sort()
            prefix = "" if not rel_dir else rel_dir + "/"

            for name, is_symlink in subdirs:
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
                if not is_symlink:
                    if (cur_depth + 1) < max_depth:
                        queue.append((prefix + name, cur_depth + 1))
                    elif depth is None:
                        truncated = True

            if truncated:
                break

            for name in files:
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
                    "type": "file",
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                })

            if truncated:
                break

        def _sort_key(entry: dict) -> list:
            parts = entry["path"].split("/")
            is_dir = entry["type"] == "dir"
            key = []
            for part in parts[:-1]:
                key.append((0, part))
            key.append((0 if is_dir else 1, parts[-1]))
            return key

        entries.sort(key=_sort_key)
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
            "content": None,
            "error": "the pane has no readable working directory",
        }
    shape_error = _containment_error(root, rel_path)
    if shape_error is not None:
        return {
            "path": rel_path,
            "available": False,
            "content": None,
            "error": shape_error,
        }

    candidate = (root / rel_path).resolve()
    try:
        root_real = root.resolve()
    except OSError:
        return {
            "path": rel_path,
            "available": False,
            "content": None,
            "error": "unresolvable workspace root",
        }
    inside = candidate == root_real or root_real in candidate.parents
    if not inside:
        return {
            "path": rel_path,
            "available": False,
            "content": None,
            "error": "path escapes the workspace (symlink or traversal)",
        }
    if not candidate.is_file():
        return {
            "path": rel_path,
            "available": False,
            "content": None,
            "error": "not a readable file",
        }
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
        return {
            "path": rel_path,
            "available": False,
            "content": None,
            "error": f"read failed: {exc}",
        }
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


def _is_safe_ref(ref: str) -> bool:
    """True if ``ref`` looks like a plausible git commit SHA, tag or branch.

    Refuses anything starting with a dash (option injection) and any
    character outside the standard git ref set.
    """
    if not ref or len(ref) > 100 or ref.startswith("-"):
        return False
    import re
    return bool(re.match(r"^[a-zA-Z0-9_.~^/@{}-]+$", ref))


def git_changes(
    cwd: str,
    runner: Optional[Callable] = None,
    ref: Optional[str] = None,
) -> dict:
    """``git status`` + ``git diff`` inside the pane cwd (S1/S2).

    Runs the git CLI read-only in the pane's workspace: ``--porcelain``
    status entries, then the diff of tracked changes (against HEAD, or
    for a specific commit if ``ref`` is passed), plus recent commits for
    quick inspection when the worktree is clean. Output is capped at
    :data:`DIFF_MAX_BYTES` with a ``truncated`` flag; every git failure,
    a non-repo workspace and a timeout are ordinary payloads:
    ``{cwd, available, is_git_repo, entries, commits, diff, truncated, ref, error?}``.
    """
    root = resolve_pane_root(cwd)
    if root is None:
        return {
            "cwd": cwd or "",
            "available": False,
            "is_git_repo": False,
            "entries": [],
            "commits": [],
            "diff": None,
            "truncated": False,
            "ref": None,
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
            "commits": [],
            "diff": None,
            "truncated": False,
            "ref": None,
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

    commits: List[Dict[str, str]] = []
    try:
        log = _git("log", "-n", "8", "--format=%h|%an|%cr|%s")
        if log.returncode == 0:
            for line in log.stdout.splitlines():
                parts = line.split("|", 3)
                if len(parts) == 4:
                    commits.append({
                        "sha": parts[0].strip(),
                        "author": parts[1].strip(),
                        "time": parts[2].strip(),
                        "message": parts[3].strip(),
                    })
    except (OSError, subprocess.TimeoutExpired, AssertionError):
        pass

    diff_text = ""
    truncated = False
    target_ref = ref if (ref and _is_safe_ref(ref)) else None
    try:
        if target_ref is not None:
            diff = _git("show", "-m", "--first-parent", target_ref)
        else:
            head = _git("rev-parse", "--verify", "--quiet", "HEAD")
            diff = _git("diff", "HEAD") if head.returncode == 0 else _git("diff")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _git_failure(root, exc, entries=entries, commits=commits)
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
        "commits": commits,
        "diff": diff_text,
        "truncated": truncated,
        "ref": target_ref,
        "error": None,
    }


def _git_failure(
    root: Path,
    exc: Exception,
    entries: Optional[List] = None,
    commits: Optional[List] = None,
) -> dict:
    return {
        "cwd": str(root),
        "available": False,
        "is_git_repo": None,
        "entries": entries or [],
        "commits": commits or [],
        "diff": None,
        "truncated": False,
        "ref": None,
        "error": f"git unavailable: {exc}",
    }


def search_workspace(
    cwd: str,
    query: str,
    mode: str = "filename",
    limit: int = 50,
    runner: Optional[Callable] = None,
) -> dict:
    """Search files by name or text content in the workspace (VS Code style).

    Modes:
      - 'filename': match query against relative file paths and basenames.
      - 'content': match query against text lines inside workspace files (git grep).
    """
    root = resolve_pane_root(cwd)
    if root is None:
        return {
            "cwd": cwd or "",
            "available": False,
            "query": query,
            "mode": mode,
            "results": [],
            "truncated": False,
            "error": "the pane has no readable working directory",
        }

    q = (query or "").strip()
    if not q:
        return {
            "cwd": str(root),
            "available": True,
            "query": "",
            "mode": mode,
            "results": [],
            "truncated": False,
            "error": None,
        }

    run = runner or _runner()

    def _run_git(*args: str) -> subprocess.CompletedProcess:
        return run(
            ["git", "-c", "core.quotepath=false", *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
            cwd=str(root),
        )

    results: List[Dict] = []
    truncated = False
    cap = max(1, min(limit, 200))

    try:
        is_git = False
        try:
            probe = _run_git("rev-parse", "--is-inside-work-tree")
            is_git = (probe.returncode == 0 and probe.stdout.strip() == "true")
        except (subprocess.SubprocessError, OSError):
            is_git = False

        if mode == "content":
            if is_git:
                res = _run_git("grep", "--untracked", "-n", "-I", "-i", "-m", "10", "-e", q, "--")
                if res.returncode in (0, 1):
                    for line in res.stdout.splitlines():
                        if len(results) >= cap:
                            truncated = True
                            break
                        parts = line.split(":", 2)
                        if len(parts) >= 3:
                            rel_path, lineno_str, snippet = parts[0], parts[1], parts[2]
                            if any(part in IGNORED_DIRS for part in rel_path.split("/")):
                                continue
                            try:
                                lineno = int(lineno_str)
                            except ValueError:
                                lineno = 1
                            results.append({
                                "path": rel_path,
                                "name": os.path.basename(rel_path),
                                "line": lineno,
                                "snippet": snippet.strip()[:140],
                            })
            else:
                tree = list_tree(str(root))
                for entry in tree.get("entries", []):
                    if entry["type"] != "file":
                        continue
                    if len(results) >= cap:
                        truncated = True
                        break
                    full_p = root / entry["path"]
                    try:
                        with open(full_p, "r", encoding="utf-8", errors="replace") as f:
                            for idx, f_line in enumerate(f, 1):
                                if q.lower() in f_line.lower():
                                    results.append({
                                        "path": entry["path"],
                                        "name": os.path.basename(entry["path"]),
                                        "line": idx,
                                        "snippet": f_line.strip()[:140],
                                    })
                                    if len(results) >= cap:
                                        truncated = True
                                        break
                    except OSError:
                        continue
        else:
            q_lower = q.lower()
            all_files: List[str] = []
            if is_git:
                res = _run_git("ls-files", "--cached", "--others", "--exclude-standard")
                if res.returncode == 0:
                    all_files = res.stdout.splitlines()
            if not all_files:
                tree = list_tree(str(root))
                all_files = [e["path"] for e in tree.get("entries", []) if e["type"] == "file"]

            for rel_path in all_files:
                if len(results) >= cap:
                    truncated = True
                    break
                if any(part in IGNORED_DIRS for part in rel_path.split("/")):
                    continue
                basename = os.path.basename(rel_path)
                if q_lower in basename.lower() or q_lower in rel_path.lower():
                    full_p = root / rel_path
                    size = 0
                    mtime = 0.0
                    try:
                        st = full_p.stat()
                        size = st.st_size
                        mtime = st.st_mtime
                    except OSError:
                        pass
                    results.append({
                        "path": rel_path,
                        "name": basename,
                        "size": size,
                        "mtime": mtime,
                    })

        return {
            "cwd": str(root),
            "available": True,
            "query": q,
            "mode": mode,
            "results": results,
            "truncated": truncated,
            "error": None,
        }
    except Exception as exc:
        return {
            "cwd": str(root),
            "available": False,
            "query": q,
            "mode": mode,
            "results": [],
            "truncated": False,
            "error": f"search failed: {exc}",
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
    "search_workspace",
]
