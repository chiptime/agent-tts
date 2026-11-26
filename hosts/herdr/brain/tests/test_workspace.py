"""Security and behaviour tests for the read-only workspace module (W3-P2 S1/S5).

The traversal guard is the security core: every test that names an escape
(``..``, absolute paths, symlinks out of the root) must be refused BEFORE
any workspace byte leaves the pane cwd.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from herdr_brain import workspace
from herdr_brain.workspace import (
    DIFF_MAX_BYTES,
    MAX_FILE_BYTES,
    MAX_TREE_ENTRIES,
    git_changes,
    list_tree,
    read_file,
    resolve_pane_root,
)


@pytest.fixture
def ws(tmp_path):
    """A small workspace: two files, one subdir, one hidden file."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "readme.md").write_text("hello workspace\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (root / ".hidden").write_text("dot\n", encoding="utf-8")
    return root


def outside_file(tmp_path) -> Path:
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret\n", encoding="utf-8")
    return secret


# ── resolve_pane_root ─────────────────────────────────────────────────────────


class TestResolvePaneRoot:
    def test_existing_dir_resolves(self, ws):
        assert resolve_pane_root(str(ws)) == ws.resolve()

    def test_empty_cwd_is_none(self):
        assert resolve_pane_root("") is None
        assert resolve_pane_root("   ") is None

    def test_missing_dir_is_none(self, tmp_path):
        assert resolve_pane_root(str(tmp_path / "gone")) is None

    def test_file_is_not_a_root(self, ws):
        assert resolve_pane_root(str(ws / "readme.md")) is None


# ── list_tree ─────────────────────────────────────────────────────────────────


class TestListTree:
    def test_lists_files_with_size_and_mtime(self, ws):
        payload = list_tree(str(ws))
        assert payload["available"] is True
        assert payload["error"] is None
        paths = {entry["path"] for entry in payload["entries"]}
        assert {"readme.md", "src", "src/main.py", ".hidden"} <= paths
        readme = next(e for e in payload["entries"] if e["path"] == "readme.md")
        assert readme["type"] == "file"
        assert readme["size"] == len("hello workspace\n")
        assert isinstance(readme["mtime"], float)
        src = next(e for e in payload["entries"] if e["path"] == "src")
        assert src["type"] == "dir"

    def test_dirs_sort_before_files(self, ws):
        payload = list_tree(str(ws))
        types = [entry["type"] for entry in payload["entries"]]
        assert types == sorted(types, key=lambda t: 0 if t == "dir" else 1)

    def test_ignored_dirs_are_skipped(self, ws):
        (ws / "node_modules").mkdir()
        (ws / "node_modules" / "junk.js").write_text("x")
        (ws / ".git").mkdir()
        (ws / ".git" / "HEAD").write_text("ref\n")
        payload = list_tree(str(ws))
        paths = {entry["path"] for entry in payload["entries"]}
        assert "node_modules" not in paths
        assert "node_modules/junk.js" not in paths
        assert ".git/HEAD" not in paths

    def test_symlinked_dir_is_listed_but_not_descended(self, ws, tmp_path):
        outside = outside_file(tmp_path)
        link = ws / "escape"
        os.symlink(outside.parent, link)
        payload = list_tree(str(ws))
        paths = {entry["path"] for entry in payload["entries"]}
        assert "escape" in paths
        assert "escape/secret.txt" not in paths

    def test_entry_cap_sets_truncated(self, ws):
        # One-shot cap proof: walk a tiny fake tree by shrinking the cap.
        for i in range(5):
            (ws / f"f{i}.txt").write_text("x")
        payload = workspace.list_tree(str(ws))
        assert payload["truncated"] is False
        # Patch the module constant the function reads.
        import herdr_brain.workspace as mod

        original = mod.MAX_TREE_ENTRIES
        mod.MAX_TREE_ENTRIES = 3
        try:
            payload = list_tree(str(ws))
        finally:
            mod.MAX_TREE_ENTRIES = original
        assert payload["truncated"] is True
        assert len(payload["entries"]) == 3

    def test_missing_root_is_unavailable(self, tmp_path):
        payload = list_tree(str(tmp_path / "gone"))
        assert payload["available"] is False
        assert payload["entries"] == []
        assert payload["error"]


# ── read_file traversal guard (S1/S5 security core) ──────────────────────────


class TestReadFileTraversalGuard:
    def test_dotdot_is_refused(self, ws, tmp_path):
        outside_file(tmp_path)
        payload = read_file(str(ws), "../secret.txt")
        assert payload["available"] is False
        assert "traversal" in payload["error"]
        assert "content" not in payload or payload.get("content") is None

    def test_dotdot_hidden_inside_a_name_is_refused(self, ws):
        payload = read_file(str(ws), "src/../../etc/passwd")
        assert payload["available"] is False

    def test_absolute_path_is_refused(self, ws, tmp_path):
        secret = outside_file(tmp_path)
        payload = read_file(str(ws), str(secret))
        assert payload["available"] is False
        assert "absolute" in payload["error"]

    def test_symlink_pointing_outside_is_refused(self, ws, tmp_path):
        secret = outside_file(tmp_path)
        os.symlink(secret, ws / "steal.txt")
        payload = read_file(str(ws), "steal.txt")
        assert payload["available"] is False
        assert "escapes" in payload["error"]
        assert payload.get("content") is None

    def test_symlinked_dir_escape_is_refused(self, ws, tmp_path):
        outside_file(tmp_path)
        os.symlink(tmp_path, ws / "out")
        payload = read_file(str(ws), "out/secret.txt")
        assert payload["available"] is False

    def test_symlink_inside_the_root_is_allowed(self, ws):
        os.symlink(ws / "readme.md", ws / "alias.md")
        payload = read_file(str(ws), "alias.md")
        assert payload["available"] is True
        assert "hello workspace" in payload["content"]

    def test_empty_path_is_refused(self, ws):
        assert read_file(str(ws), "").get("available") is False

    def test_nul_byte_path_is_refused(self, ws):
        assert read_file(str(ws), "a\x00b").get("available") is False

    def test_missing_file_is_an_ordinary_error(self, ws):
        payload = read_file(str(ws), "nope.txt")
        assert payload["available"] is False
        assert "not a readable file" in payload["error"]

    def test_directory_is_not_a_file(self, ws):
        payload = read_file(str(ws), "src")
        assert payload["available"] is False


class TestReadFileBounds:
    def test_content_is_served_under_the_cap(self, ws):
        payload = read_file(str(ws), "readme.md")
        assert payload["available"] is True
        assert payload["content"] == "hello workspace\n"
        assert payload["truncated"] is False
        assert payload["binary"] is False
        assert payload["size"] == len("hello workspace\n")

    def test_oversized_file_is_truncated_at_64kb(self, ws):
        big = ws / "big.txt"
        big.write_text("a" * (MAX_FILE_BYTES + 100), encoding="utf-8")
        payload = read_file(str(ws), "big.txt")
        assert payload["truncated"] is True
        assert len(payload["content"].encode("utf-8")) == MAX_FILE_BYTES

    def test_binary_file_content_is_never_served(self, ws):
        (ws / "blob.bin").write_bytes(b"PNG\x00\x00data")
        payload = read_file(str(ws), "blob.bin")
        assert payload["available"] is True
        assert payload["binary"] is True
        assert payload["content"] is None

    def test_invalid_utf8_replaces_instead_of_raising(self, ws):
        (ws / "latin.txt").write_bytes(b"caf\xe9 lone \xff byte")
        payload = read_file(str(ws), "latin.txt")
        assert payload["available"] is True
        assert "caf" in payload["content"]

    def test_missing_root_is_unavailable(self, tmp_path):
        payload = read_file(str(tmp_path / "gone"), "x.txt")
        assert payload["available"] is False


# ── git_changes ───────────────────────────────────────────────────────────────


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    )


@pytest.fixture
def repo(ws):
    _git(ws, "init", "-q")
    _git(ws, "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    _git(ws, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    return ws


class _P:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _cp(returncode=0, stdout="", stderr=""):
    return _P(returncode=returncode, stdout=stdout, stderr=stderr)


class CappedRun:
    """subprocess.run double: canned git answers, huge diff, call log."""

    def __init__(self):
        self.calls: list = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        args = tuple(cmd[3:])
        if args == ("rev-parse", "--is-inside-work-tree"):
            return _cp(stdout="true\n")
        if args == ("status", "--porcelain"):
            return _cp(stdout=" M readme.md\n")
        if args == ("rev-parse", "--verify", "--quiet", "HEAD"):
            return _cp(returncode=0, stdout="abc123\n")
        if args == ("diff", "HEAD"):
            return _cp(stdout="x" * (DIFF_MAX_BYTES + 10))
        raise AssertionError(f"unexpected git call: {cmd}")


class TestGitChanges:
    def test_clean_repo_reports_no_changes(self, repo):
        payload = git_changes(str(repo))
        assert payload["available"] is True
        assert payload["is_git_repo"] is True
        assert payload["entries"] == []
        assert payload["diff"] == ""
        assert payload["truncated"] is False
        assert len(payload["commits"]) >= 1
        assert payload["commits"][0]["message"] == "init"

    def test_inspect_commit_ref_shows_diff(self, repo):
        payload = git_changes(str(repo), ref="HEAD")
        assert payload["available"] is True
        assert payload["ref"] == "HEAD"
        assert payload["diff"] != ""
        assert "hello workspace" in payload["diff"]

    def test_unsafe_ref_is_ignored_and_treated_as_default(self, repo):
        payload = git_changes(str(repo), ref="--dangerous-flag")
        assert payload["ref"] is None

    def test_modified_file_appears_in_status_and_diff(self, repo):
        (repo / "readme.md").write_text("changed!\n", encoding="utf-8")
        payload = git_changes(str(repo))
        statuses = {e["path"]: e["status"] for e in payload["entries"]}
        assert statuses.get("readme.md") == "M"
        assert "changed!" in payload["diff"]
        assert "hello workspace" in payload["diff"]  # unified diff shows both

    def test_untracked_file_appears_in_status(self, repo):
        (repo / "new.txt").write_text("brand new\n", encoding="utf-8")
        payload = git_changes(str(repo))
        statuses = {e["path"]: e["status"] for e in payload["entries"]}
        assert statuses.get("new.txt") == "??"
        # Untracked files produce no diff body.
        assert "brand new" not in payload["diff"]

    def test_repo_without_head_diffs_working_tree_only(self, ws):
        _git(ws, "init", "-q")
        (ws / "readme.md").write_text("staged later\n", encoding="utf-8")
        payload = git_changes(str(ws))
        assert payload["available"] is True
        assert payload["is_git_repo"] is True
        assert payload["entries"]  # untracked readme listed
        assert payload["error"] is None

    def test_non_repo_is_reported_not_failed(self, ws):
        payload = git_changes(str(ws))
        assert payload["available"] is True
        assert payload["is_git_repo"] is False
        assert payload["entries"] == []
        assert payload["diff"] is None

    def test_missing_root_is_unavailable(self, tmp_path):
        payload = git_changes(str(tmp_path / "gone"))
        assert payload["available"] is False
        assert payload["error"]

    def test_diff_output_is_capped_with_truncated_flag(self, repo):
        runner = CappedRun()
        payload = git_changes(str(repo), runner=runner)
        assert payload["truncated"] is True
        assert len(payload["diff"]) == DIFF_MAX_BYTES
        assert payload["entries"] == [{"status": "M", "path": "readme.md"}]

    def test_git_failure_is_an_ordinary_payload(self, repo):
        def boom(cmd, **kwargs):
            raise OSError("git vanished")

        payload = git_changes(str(repo), runner=boom)
        assert payload["available"] is False
        assert "git unavailable" in payload["error"]


# ── status parsing ────────────────────────────────────────────────────────────


class TestParseStatusLine:
    def test_modified_line(self):
        assert workspace._parse_status_line(" M src/a.py") == {
            "status": "M",
            "path": "src/a.py",
        }

    def test_rename_reports_the_new_path(self):
        parsed = workspace._parse_status_line("R  old.txt -> new.txt")
        assert parsed == {"status": "R", "path": "new.txt"}

    def test_untracked_line(self):
        assert workspace._parse_status_line("?? junk.log") == {
            "status": "??",
            "path": "junk.log",
        }

    def test_garbage_is_none(self):
        assert workspace._parse_status_line("") is None
        assert workspace._parse_status_line("ab") is None
