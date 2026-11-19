"""Pure edits of a Claude Code settings JSON for the held hook (S3).

The editor is deliberately pure — dicts in, dicts out — so the CLI owns
every byte of file I/O and the policy is testable without a disk. One
hard invariant: ``install_hook`` leaves EXACTLY ONE entry whose command
carries :data:`HOOK_MARKER`, and ``uninstall_hook`` removes exactly the
entries carrying it, leaving every other key and hook untouched.
"""

from __future__ import annotations

from typing import Tuple

#: The unique marker of the brain's hook entry inside a Claude settings
#: file. Matched against the ``command`` string, so an entry stays
#: recognizable no matter which python/paths an install pinned.
HOOK_MARKER = "herdr_brain.hook_permissions"

#: The Claude Code event this hook answers (the only one the brain
#: installs): fired before each tool call, with the permission decision
#: contract on stdout.
HOOK_EVENT = "PreToolUse"

#: Claude's kill timeout for the hook command, added on top of the
#: hook's own deny deadline. A ``command`` hook that dies without valid
#: JSON is a NON-BLOCKING error in Claude Code (the tool would run), so
#: the brain's own deny must ALWAYS land first; this margin is what
#: guarantees that ordering.
CLAUDE_TIMEOUT_MARGIN_S = 60


def build_hook_command(python: str, src: str, state_dir: str) -> str:
    """The one-line shell command the installed hook entry runs.

    Everything is absolute — the hook must resolve identically from any
    cwd, any PATH, with the user's shell environment: the venv python,
    the package ``src`` directory on ``PYTHONPATH``, and the brain state
    dir (where the socket lives) are all pinned. No credential ever
    appears here; the socket is same-user only.
    """
    return (
        f'PYTHONPATH="{src}" HERDR_BRAIN_STATE_DIR="{state_dir}" '
        f'"{python}" -m {HOOK_MARKER}'
    )


def _is_marked(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    hooks = entry.get("hooks")
    if not isinstance(hooks, list):
        return False
    return any(
        isinstance(hook, dict) and HOOK_MARKER in str(hook.get("command", ""))
        for hook in hooks
    )


def install_hook(
    settings: dict,
    command: str,
    matcher: str,
    timeout_s: int,
) -> dict:
    """Adds (or replaces) the ONE marked PreToolUse entry, in place.

    Re-install replaces a previously marked entry instead of adding a
    second one — the entry stays single no matter how many times the
    operator runs install, and a changed matcher/timeout lands cleanly.
    Every other key, and every other hook entry, is preserved verbatim
    as JSON.
    """
    hooks_tree = settings.get("hooks")
    if not isinstance(hooks_tree, dict):
        hooks_tree = {}
        settings["hooks"] = hooks_tree
    entries = hooks_tree.get(HOOK_EVENT)
    if not isinstance(entries, list):
        entries = []
        hooks_tree[HOOK_EVENT] = entries

    entry = {
        "matcher": matcher,
        "hooks": [
            {
                "type": "command",
                "command": command,
                "timeout": int(timeout_s) + CLAUDE_TIMEOUT_MARGIN_S,
            }
        ],
    }
    kept = [item for item in entries if not _is_marked(item)]
    kept.append(entry)
    hooks_tree[HOOK_EVENT] = kept
    return settings


def uninstall_hook(settings: dict) -> Tuple[dict, int]:
    """Removes every marked PreToolUse entry; returns (settings, count).

    Uninstall is safe when nothing is installed (count 0) and never
    drops a hook it did not install."""
    hooks_tree = settings.get("hooks")
    if not isinstance(hooks_tree, dict):
        return settings, 0
    entries = hooks_tree.get(HOOK_EVENT)
    if not isinstance(entries, list):
        return settings, 0
    kept = [item for item in entries if not _is_marked(item)]
    removed = len(entries) - len(kept)
    if removed:
        hooks_tree[HOOK_EVENT] = kept
    return settings, removed
