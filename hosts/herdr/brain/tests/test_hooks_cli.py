"""`herdr-brain hooks install|uninstall|status` (parity-f7e S3).

The install edits a Claude Code settings JSON file explicitly and
reversibly: it adds exactly ONE clearly-marked PreToolUse hook entry
(idempotent, never duplicating, preserving every other key and hook as
JSON), and the uninstall removes exactly that entry. NEVER run against
the operator's real ~/.claude/settings.json — every test uses tmp_path.
"""

from __future__ import annotations

import json

import pytest

from herdr_brain.hooks.cli import main as hooks_main
from herdr_brain.hooks.settings_edit import (
    HOOK_MARKER,
    build_hook_command,
    install_hook,
    uninstall_hook,
)

OTHER_SETTINGS = {
    "model": "claude-opus-4-5",
    "permissions": {"allow": ["Bash(echo:*)"]},
    "hooks": {
        "PreToolUse": [
            {
                "matcher": "WebFetch",
                "hooks": [{"type": "command", "command": "/usr/local/bin/other-hook.sh"}],
            }
        ],
        "PostToolUse": [
            {"hooks": [{"type": "command", "command": "echo done"}]}
        ],
    },
}


def write_settings(path, payload):
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def settings_path(tmp_path):
    return write_settings(tmp_path / "settings.json", OTHER_SETTINGS)


def marked_entries(payload):
    return [
        entry
        for entry in payload.get("hooks", {}).get("PreToolUse", [])
        if any(HOOK_MARKER in h.get("command", "") for h in entry.get("hooks", []))
    ]


# -- pure editor ---------------------------------------------------------------


def test_install_adds_one_marked_entry():
    payload = install_hook(
        dict(OTHER_SETTINGS), command="python -m herdr_brain.hook_permissions", matcher="Bash", timeout_s=120
    )
    entries = marked_entries(payload)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["matcher"] == "Bash"
    command = entry["hooks"][0]
    assert command["type"] == "command"
    assert HOOK_MARKER in command["command"]
    # Claude's kill timeout must exceed the hook's own deny deadline so
    # the deny JSON always lands before Claude would time the hook out
    # (a non-0/non-2 exit without JSON is NON-BLOCKING there).
    assert command["timeout"] > 120


def test_install_preserves_every_other_key_and_hook():
    payload = install_hook(
        dict(OTHER_SETTINGS), command="python -m herdr_brain.hook_permissions", matcher="Bash", timeout_s=120
    )
    assert payload["model"] == OTHER_SETTINGS["model"]
    assert payload["permissions"] == OTHER_SETTINGS["permissions"]
    pre = payload["hooks"]["PreToolUse"]
    assert OTHER_SETTINGS["hooks"]["PreToolUse"][0] in pre
    assert payload["hooks"]["PostToolUse"] == OTHER_SETTINGS["hooks"]["PostToolUse"]


def test_install_is_idempotent_never_duplicates():
    once = install_hook(dict(OTHER_SETTINGS), command="cmd-a " + HOOK_MARKER, matcher="Bash", timeout_s=120)
    twice = install_hook(once, command="cmd-a " + HOOK_MARKER, matcher="Bash", timeout_s=120)
    assert len(marked_entries(twice)) == 1


def test_install_replaces_a_diverged_marked_entry():
    once = install_hook(dict(OTHER_SETTINGS), command="old " + HOOK_MARKER, matcher="Bash", timeout_s=60)
    updated = install_hook(once, command="new " + HOOK_MARKER, matcher="Edit|Write", timeout_s=90)
    entries = marked_entries(updated)
    assert len(entries) == 1
    assert entries[0]["matcher"] == "Edit|Write"
    assert entries[0]["hooks"][0]["command"] == "new " + HOOK_MARKER


def test_uninstall_removes_exactly_the_marked_entry():
    installed = install_hook(
        dict(OTHER_SETTINGS), command="cmd " + HOOK_MARKER, matcher="Bash", timeout_s=120
    )
    payload, removed = uninstall_hook(installed)
    assert removed == 1
    assert marked_entries(payload) == []
    assert payload["hooks"]["PreToolUse"] == OTHER_SETTINGS["hooks"]["PreToolUse"]
    assert payload["hooks"]["PostToolUse"] == OTHER_SETTINGS["hooks"]["PostToolUse"]
    assert payload["model"] == OTHER_SETTINGS["model"]


def test_uninstall_without_the_entry_is_a_noop():
    payload, removed = uninstall_hook(dict(OTHER_SETTINGS))
    assert removed == 0
    assert payload == OTHER_SETTINGS


def test_install_on_empty_settings_creates_the_hooks_tree():
    payload = install_hook({}, command="cmd " + HOOK_MARKER, matcher="Bash", timeout_s=120)
    assert len(marked_entries(payload)) == 1


def test_build_hook_command_pins_absolute_paths(tmp_path):
    command = build_hook_command(
        python="/opt/venv/bin/python",
        src="/opt/src",
        state_dir=str(tmp_path),
    )
    assert command.startswith("PYTHONPATH=")
    assert "/opt/venv/bin/python" in command
    assert "-m herdr_brain.hook_permissions" in command
    assert str(tmp_path) in command  # the state dir (socket location) is pinned


# -- the CLI against tmp settings files ----------------------------------------


def test_cli_install_then_uninstall_restores_the_original(settings_path):
    rc = hooks_main(["install", "--settings", str(settings_path)])
    assert rc == 0
    on_disk = json.loads(settings_path.read_text())
    assert len(marked_entries(on_disk)) == 1
    # everything else survived the JSON round-trip
    assert on_disk["model"] == OTHER_SETTINGS["model"]
    assert on_disk["hooks"]["PostToolUse"] == OTHER_SETTINGS["hooks"]["PostToolUse"]

    rc = hooks_main(["uninstall", "--settings", str(settings_path)])
    assert rc == 0
    restored = json.loads(settings_path.read_text())
    assert marked_entries(restored) == []
    assert restored["hooks"]["PreToolUse"] == OTHER_SETTINGS["hooks"]["PreToolUse"]


def test_cli_install_is_idempotent_on_disk(settings_path):
    hooks_main(["install", "--settings", str(settings_path)])
    before = settings_path.read_text()
    rc = hooks_main(["install", "--settings", str(settings_path)])
    assert rc == 0
    after = json.loads(settings_path.read_text())
    assert len(marked_entries(after)) == 1
    assert before == settings_path.read_text()  # byte-stable second run


def test_cli_install_creates_a_missing_file(tmp_path):
    target = tmp_path / "nested" / "settings.json"
    rc = hooks_main(["install", "--settings", str(target)])
    assert rc == 0
    payload = json.loads(target.read_text())
    assert len(marked_entries(payload)) == 1


def test_cli_install_refuses_corrupt_json_without_writing(tmp_path):
    target = tmp_path / "settings.json"
    target.write_text("{ this is not json", encoding="utf-8")
    before = target.read_text()
    rc = hooks_main(["install", "--settings", str(target)])
    assert rc == 1
    assert target.read_text() == before


def test_cli_uninstall_when_absent_is_ok(settings_path):
    rc = hooks_main(["uninstall", "--settings", str(settings_path)])
    assert rc == 0
    assert json.loads(settings_path.read_text()) == OTHER_SETTINGS


def test_cli_status_is_read_only(settings_path):
    hooks_main(["install", "--settings", str(settings_path)])
    before = settings_path.read_text()
    rc = hooks_main(["status", "--settings", str(settings_path)])
    assert rc == 0
    assert settings_path.read_text() == before


def test_cli_usage_error_is_two(capsys):
    assert hooks_main([]) == 2
    assert hooks_main(["frobnicate"]) == 2


def test_cli_custom_matcher_and_timeout_land_in_the_entry(tmp_path):
    target = tmp_path / "settings.json"
    rc = hooks_main(
        [
            "install",
            "--settings",
            str(target),
            "--matcher",
            "Bash|mcp__.*",
            "--timeout-s",
            "45",
        ]
    )
    assert rc == 0
    entry = marked_entries(json.loads(target.read_text()))[0]
    assert entry["matcher"] == "Bash|mcp__.*"
    assert entry["hooks"][0]["timeout"] > 45
