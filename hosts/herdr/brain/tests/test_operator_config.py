"""Behavior tests for the operator TOML config (doc 06, S3).

Every test drives the public surface only — the pure ``validate_*``
functions and the :class:`OperatorConfig` store — the same surface the
server routes will use. Files live in a per-test config directory and
every write bumps the mtime explicitly so reload behaviour is
deterministic, not a race against timestamp granularity.
"""

from __future__ import annotations

import os
import shlex
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from herdr_brain import operator_config as oc
from herdr_brain.herdr import MAX_SEND_KEYS, SEND_KEY_ALLOWLIST


# ── helpers ──────────────────────────────────────────────────────────────


@pytest.fixture()
def writer(tmp_path, monkeypatch):
    """Writes operator files into a per-test config dir (env override),
    each write with a strictly increasing mtime so reloads fire."""
    monkeypatch.setenv("HERDR_BRAIN_CONFIG_DIR", str(tmp_path))
    state = {"tick": 0}

    def _write(name: str, text: str) -> Path:
        state["tick"] += 1
        path = tmp_path / name
        # Atomic save (tmp + replace), the same discipline as the brain's
        # own cache writes: a concurrent reader never sees a truncated file.
        scratch = tmp_path / f".{name}.write-{state['tick']}"
        scratch.write_text(text, encoding="utf-8")
        os.replace(scratch, path)
        stamp = state["tick"] * 10**9
        os.utime(path, ns=(stamp, stamp))
        return path

    return _write


def issues_for(cfg: oc.OperatorConfig, file_name: str):
    report = next(r for r in cfg.check() if r.file == file_name)
    return list(report.issues)


# ── row-by-row validation per file ───────────────────────────────────────


def test_quick_replies_valid_rows_survive_invalid_ones(writer):
    writer(
        "quick-replies.toml",
        """
[[replies]]
label = "Yes"
text = "looks good, yes"

[[replies]]
text = "missing label"

[[replies]]
label = "No text"

[[replies]]
label = "Scoped"
text = "hola"
scope = "claude"
""",
    )
    cfg = oc.OperatorConfig()
    rows = {row.label: row for row in cfg.quick_replies()}
    assert set(rows) == {"Yes", "Scoped"}
    assert rows["Yes"].text == "looks good, yes"
    assert rows["Yes"].scope == ""
    assert rows["Scoped"].scope == "claude"
    issues = issues_for(cfg, "quick-replies.toml")
    assert [(i.row, i.reason) for i in issues] == [
        (2, "missing label"),
        (3, "missing text"),
    ]
    assert all(i.file == "quick-replies.toml" for i in issues)


def test_commands_need_a_slash_and_keep_argument_hints(writer):
    writer(
        "commands.toml",
        """
[[commands]]
label = "Compact"
command = "/compact"

[[commands]]
label = "No slash"
command = "compact"

[[commands]]
label = "Hint"
command = "/review "
""",
    )
    cfg = oc.OperatorConfig()
    commands = cfg.commands()
    assert [(c.id, c.command) for c in commands] == [
        ("compact", "/compact"),
        ("hint", "/review "),
    ]
    issues = issues_for(cfg, "commands.toml")
    assert [i.row for i in issues] == [2]
    assert issues[0].reason == "command must start with '/'"


def test_key_presets_bounded_by_the_existing_allowlist(writer):
    too_many = ", ".join(f'"{key}"' for key in SEND_KEY_ALLOWLIST[: MAX_SEND_KEYS + 1])
    writer(
        "keys.toml",
        f"""
[[keys]]
label = "Named outside"
keys = ["PageUp"]

[[keys]]
label = "Ctrl chord"
keys = ["ctrl+c"]

[[keys]]
label = "Too many"
keys = [{too_many}]

[[keys]]
label = "Not a list"
keys = "enter"

[[keys]]
label = "Valid"
keys = ["down", "enter"]
""",
    )
    cfg = oc.OperatorConfig()
    presets = cfg.key_presets()
    assert [(p.id, list(p.keys)) for p in presets] == [("valid", ["down", "enter"])]
    reasons = {i.row: i.reason for i in issues_for(cfg, "keys.toml")}
    assert reasons[1] == "key 'PageUp' is outside the send-key allowlist"
    assert reasons[2] == "key 'ctrl+c' is outside the send-key allowlist"
    assert reasons[3] == f"keys must use at most {MAX_SEND_KEYS} keys"
    assert reasons[4] == "keys must be a non-empty array of key names"


def test_control_characters_reject_rows_never_sanitize(writer):
    writer(
        "quick-replies.toml",
        """
[[replies]]
label = "Clean"
text = "fine"

[[replies]]
label = "Newline text"
text = "two\\rlines"
""",
    )
    writer(
        "commands.toml",
        """
[[commands]]
label = "Tab label\\t"
command = "/ok"
""",
    )
    writer(
        "keys.toml",
        """
[[keys]]
label = "Ctrl label\\u0000"
keys = ["enter"]
""",
    )
    writer(
        "launchers.toml",
        """
[[launchers]]
label = "Clean launch"
argv = ["ls"]

[[launchers]]
label = "Injected"
argv = ["echo", "a\\nb"]
""",
    )
    cfg = oc.OperatorConfig()
    assert [r.label for r in cfg.quick_replies()] == ["Clean"]
    assert cfg.commands() == ()
    assert cfg.key_presets() == ()
    assert [row.label for row in cfg.launchers()] == ["Clean launch"]
    for file_name, row in (
        ("quick-replies.toml", 2),
        ("commands.toml", 1),
        ("keys.toml", 1),
        ("launchers.toml", 2),
    ):
        issues = issues_for(cfg, file_name)
        assert len(issues) == 1 and issues[0].row == row
        assert "control" in issues[0].reason, (file_name, issues)


def test_launchers_accept_argv_only_never_a_shell_string(writer):
    writer(
        "launchers.toml",
        """
[[launchers]]
label = "String command"
command = "cargo build"

[[launchers]]
label = "Empty argv"
argv = []

[[launchers]]
label = "Non string part"
argv = ["ls", 3]

[[launchers]]
label = "Valid"
argv = ["cargo", "build"]
""",
    )
    cfg = oc.OperatorConfig()
    rows = cfg.launchers()
    assert [(r.id, list(r.argv)) for r in rows] == [("valid", ["cargo", "build"])]
    reasons = {i.row: i.reason for i in issues_for(cfg, "launchers.toml")}
    # A shell command line is not a launcher field at all: the unknown
    # field fires first, naming the mistake in the operator's file.
    assert reasons[1] == "unknown field 'command'"
    assert reasons[2] == "argv must be a non-empty array of strings (never a shell command line)"
    assert reasons[3] == "argv must contain only non-empty strings"


def test_launcher_cwd_rules_absolute_at_load_existing_at_launch(writer, tmp_path):
    missing = tmp_path / "definitely" / "not" / "here"
    writer(
        "launchers.toml",
        f"""
[[launchers]]
label = "Relative"
argv = ["ls"]
cwd = "some/where"

[[launchers]]
label = "Non string"
argv = ["ls"]
cwd = 3

[[launchers]]
label = "Absolute missing"
argv = ["ls"]
cwd = "{missing}"

[[launchers]]
label = "Absolute exists"
argv = ["ls"]
cwd = "{tmp_path}"
""",
    )
    cfg = oc.OperatorConfig()
    rows = {row.label: row for row in cfg.launchers()}
    # A missing-but-absolute cwd is VALID at load time: existence is a
    # launch-time refusal, not a parse-time one.
    assert set(rows) == {"Absolute missing", "Absolute exists"}
    assert rows["Absolute exists"].cwd == str(tmp_path)
    reasons = {i.row: i.reason for i in issues_for(cfg, "launchers.toml")}
    assert reasons[1] == "cwd must be an absolute path"
    assert reasons[2] == "cwd must be a string"


def test_no_prompts_must_be_a_boolean(writer):
    writer(
        "launchers.toml",
        """
[[launchers]]
label = "String bool"
argv = ["ls"]
no_prompts = "true"

[[launchers]]
label = "Real bool"
argv = ["ls"]
no_prompts = true
""",
    )
    cfg = oc.OperatorConfig()
    rows = {row.label: row for row in cfg.launchers()}
    assert set(rows) == {"Real bool"}
    assert rows["Real bool"].no_prompts is True
    assert issues_for(cfg, "launchers.toml")[0].reason == "no_prompts must be a boolean"


def test_unknown_fields_are_rejected(writer):
    writer(
        "launchers.toml",
        """
[[launchers]]
label = "Typo field"
argv = ["ls"]
foo = 1
""",
    )
    cfg = oc.OperatorConfig()
    assert cfg.launchers() == ()
    assert issues_for(cfg, "launchers.toml")[0].reason == "unknown field 'foo'"


# ── scope resolution: replace, never merge ───────────────────────────────


def test_scoped_rows_replace_the_global_catalog(writer):
    writer(
        "quick-replies.toml",
        """
[[replies]]
label = "Global yes"
text = "yes"

[[replies]]
label = "Claude only"
text = "oye"
scope = "Claude"

[[replies]]
label = "Shell only"
text = "si"
scope = "shell"
""",
    )
    writer(
        "commands.toml",
        """
[[commands]]
label = "Global cmd"
command = "/g"

[[commands]]
label = "Claude cmd"
command = "/c"
scope = "claude"
""",
    )
    writer(
        "keys.toml",
        """
[[keys]]
label = "Global keys"
keys = ["up"]

[[keys]]
label = "Claude keys"
keys = ["down"]
scope = "claude"
""",
    )
    cfg = oc.OperatorConfig()

    claude = cfg.for_pane("claude")
    assert [r.label for r in claude.quick_replies] == ["Claude only"]
    assert [c.label for c in claude.commands] == ["Claude cmd"]
    assert [p.label for p in claude.key_presets] == ["Claude keys"]

    shell = cfg.for_pane("SHELL")  # family matching is case-insensitive
    assert [r.label for r in shell.quick_replies] == ["Shell only"]

    codex = cfg.for_pane("codex")  # no scoped rows: the global catalog
    assert [r.label for r in codex.quick_replies] == ["Global yes"]
    assert [c.label for c in codex.commands] == ["Global cmd"]
    assert [p.label for p in codex.key_presets] == ["Global keys"]


# ── reload by mtime, broken file isolation ───────────────────────────────


def test_mtime_reload_with_cheap_stat_cache(writer):
    writer("commands.toml", '[[commands]]\nlabel = "One"\ncommand = "/one"\n')
    cfg = oc.OperatorConfig()
    assert len(cfg.commands()) == 1
    assert cfg.reload_counts()["commands"] == 1
    # Unchanged file: the stat matches, no reload happens.
    assert len(cfg.commands()) == 1
    assert cfg.reload_counts()["commands"] == 1
    # A rewrite (new mtime) is served on the very next request.
    writer(
        "commands.toml",
        '[[commands]]\nlabel = "One"\ncommand = "/one"\n\n'
        '[[commands]]\nlabel = "Two"\ncommand = "/two"\n',
    )
    assert [c.label for c in cfg.commands()] == ["One", "Two"]
    assert cfg.reload_counts()["commands"] == 2


def test_broken_file_is_isolated_from_its_siblings(writer):
    writer(
        "quick-replies.toml",
        '[[replies]]\nlabel = "Yes"\ntext = "yes"\n',
    )
    writer("keys.toml", "[[keys\nlabel = broken")  # invalid TOML syntax
    cfg = oc.OperatorConfig()
    pane = cfg.for_pane("claude")
    assert [r.label for r in pane.quick_replies] == ["Yes"]  # sibling still serves
    assert pane.key_presets == ()  # the broken file is treated as empty
    keys_issues = issues_for(cfg, "keys.toml")
    assert len(keys_issues) == 1
    assert keys_issues[0].row == 0
    assert keys_issues[0].reason.startswith("invalid TOML:")
    # The invalid rows of a repaired file come back on the next request.
    writer("keys.toml", '[[keys]]\nlabel = "Back"\nkeys = ["enter"]\n')
    assert [p.label for p in cfg.key_presets()] == ["Back"]


def test_missing_file_is_an_empty_list_without_issues(tmp_path, monkeypatch):
    monkeypatch.setenv("HERDR_BRAIN_CONFIG_DIR", str(tmp_path))
    cfg = oc.OperatorConfig()
    pane = cfg.for_pane("claude")
    assert pane.quick_replies == () and pane.commands == () and pane.key_presets == ()
    assert cfg.launchers() == ()
    assert all(report.issues == () for report in cfg.check())
    assert all(report.present is False for report in cfg.check())


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permission bits")
def test_unreadable_file_is_reported_as_error(tmp_path, monkeypatch):
    monkeypatch.setenv("HERDR_BRAIN_CONFIG_DIR", str(tmp_path))
    path = tmp_path / "keys.toml"
    path.write_text('[[keys]]\nlabel = "X"\nkeys = ["enter"]\n', encoding="utf-8")
    path.chmod(0o000)
    cfg = oc.OperatorConfig()
    assert cfg.key_presets() == ()
    issue = issues_for(cfg, "keys.toml")[0]
    assert issue.row == 0
    assert issue.reason.startswith("cannot read the file:")
    # And the sibling surface is untouched.
    assert cfg.quick_replies() == ()
    assert issues_for(cfg, "quick-replies.toml") == []


# ── launch resolution ────────────────────────────────────────────────────


def test_launch_plan_refusals_and_exact_plan(writer, tmp_path):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    writer(
        "launchers.toml",
        f"""
[[launchers]]
id = "editor"
label = "Editor"
argv = ["nvim", "notes md.txt"]
cwd = "{real_dir}"

[[launchers]]
id = "risky"
label = "Risky"
argv = ["make", "deploy"]
no_prompts = true

[[launchers]]
id = "gone"
label = "Gone cwd"
argv = ["ls"]
cwd = "/nonexistent-dir-for-brain-tests"
""",
    )
    cfg = oc.OperatorConfig()
    plan = cfg.launch_plan("editor")
    assert plan.row_id == "editor"
    assert plan.argv == ("nvim", "notes md.txt")
    assert plan.cwd == str(real_dir)
    assert plan.command_line == shlex.join(["nvim", "notes md.txt"])
    assert plan.no_prompts is False

    with pytest.raises(oc.UnknownRowError):
        cfg.launch_plan("nope")
    with pytest.raises(oc.LaunchRefusedError) as refused:
        cfg.launch_plan("risky")
    assert refused.value.reason == "confirmation_required"
    assert cfg.launch_plan("risky", confirm=True).argv == ("make", "deploy")
    with pytest.raises(oc.LaunchRefusedError) as gone:
        cfg.launch_plan("gone")
    assert gone.value.reason == "cwd_missing"
    # No confirmation is ever demanded for a plain row.
    assert cfg.launch_plan("editor", confirm=False).row_id == "editor"


def test_launcher_lookup_by_id(writer):
    writer(
        "launchers.toml",
        """
[[launchers]]
id = "editor"
label = "Editor"
argv = ["nvim"]
""",
    )
    cfg = oc.OperatorConfig()
    row = cfg.launcher("editor")
    assert row.label == "Editor" and row.cwd is None and row.no_prompts is False
    with pytest.raises(oc.UnknownRowError):
        cfg.launcher("ghost")


# ── row identity ─────────────────────────────────────────────────────────


def test_row_ids_explicit_derived_and_unique(writer):
    writer(
        "quick-replies.toml",
        """
[[replies]]
label = "Daily standup"
text = "standup"

[[replies]]
id = "explicit"
label = "Explicit"
text = "x"

[[replies]]
label = "Daily standup"
text = "standup two"

[[replies]]
id = "explicit"
label = "Duplicate"
text = "y"

[[replies]]
label = "\\u2713"
text = "z"
""",
    )
    cfg = oc.OperatorConfig()
    rows = cfg.quick_replies()
    ids = [row.id for row in rows]
    # Derived from the label slug, explicit kept verbatim, collisions
    # disambiguated deterministically, duplicates rejected, an
    # unsaluggable label falls back to its ordinal.
    assert ids == [
        "daily-standup",
        "explicit",
        "daily-standup-2",
        "row-5",
    ]
    duplicate = issues_for(cfg, "quick-replies.toml")[0]
    assert duplicate.row == 4
    assert duplicate.reason == "duplicate id 'explicit'"
    # The same content reloaded yields the same ids.
    content = (Path(cfg.config_dir) / "quick-replies.toml").read_text(encoding="utf-8")
    writer("quick-replies.toml", content)
    assert [row.id for row in cfg.quick_replies()] == ids


# ── config dir resolution ────────────────────────────────────────────────


def test_config_dir_env_override_and_xdg_fallback(tmp_path, monkeypatch):
    custom = tmp_path / "custom"
    custom.mkdir()
    (custom / "quick-replies.toml").write_text(
        '[[replies]]\nlabel = "Env"\ntext = "e"\n', encoding="utf-8"
    )
    monkeypatch.setenv("HERDR_BRAIN_CONFIG_DIR", str(custom))
    cfg = oc.OperatorConfig()
    assert [r.label for r in cfg.for_pane("x").quick_replies] == ["Env"]

    # An explicit directory wins over the environment.
    empty = tmp_path / "empty"
    empty.mkdir()
    assert oc.OperatorConfig(config_dir=empty).quick_replies() == ()

    # An empty override falls back to the XDG config home.
    monkeypatch.setenv("HERDR_BRAIN_CONFIG_DIR", "")
    xdg = tmp_path / "xdg" / "herdr-brain"
    xdg.mkdir(parents=True)
    (xdg / "commands.toml").write_text(
        '[[commands]]\nlabel = "X"\ncommand = "/x"\n', encoding="utf-8"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert [c.label for c in oc.OperatorConfig().commands()] == ["X"]


# ── the config-check report ──────────────────────────────────────────────


def test_check_reports_every_file_with_counts_and_issues(writer):
    writer(
        "quick-replies.toml",
        '[[replies]]\nlabel = "A"\ntext = "a"\n\n'
        '[[replies]]\nlabel = "B"\ntext = "b"\n',
    )
    writer("keys.toml", '[[keys]]\nlabel = "Ok"\nkeys = ["enter"]\n\n[[keys]]\nkeys = ["nope"]\n')
    cfg = oc.OperatorConfig()
    reports = {r.file: r for r in cfg.check()}
    assert set(reports) == {
        "quick-replies.toml",
        "commands.toml",
        "keys.toml",
        "launchers.toml",
    }
    assert reports["quick-replies.toml"].present is True
    assert reports["quick-replies.toml"].row_count == 2
    assert reports["quick-replies.toml"].issues == ()
    assert reports["keys.toml"].row_count == 1
    assert reports["keys.toml"].issues[0].row == 2
    assert reports["commands.toml"].present is False
    assert reports["commands.toml"].row_count == 0
    assert reports["launchers.toml"].issues == ()


# ── concurrency ──────────────────────────────────────────────────────────


def test_threaded_reads_never_see_a_torn_catalog(writer):
    writer("quick-replies.toml", '[[replies]]\nlabel = "A"\ntext = "a"\n')
    cfg = oc.OperatorConfig()
    stop = threading.Event()
    torn: list = []

    def hammer() -> None:
        while not stop.is_set():
            labels = tuple(r.label for r in cfg.for_pane("x").quick_replies)
            if labels not in (("A",), ("B",)):
                torn.append(labels)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(hammer) for _ in range(4)]
        for i in range(20):
            label = "A" if i % 2 == 0 else "B"
            writer("quick-replies.toml", f'[[replies]]\nlabel = "{label}"\ntext = "t"\n')
            time.sleep(0.003)
        stop.set()
        for future in futures:
            future.result()
    assert torn == []
