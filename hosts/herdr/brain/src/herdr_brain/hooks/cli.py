"""``herdr-brain hooks install|uninstall|status`` (S3, parity-f7e).

The management half of the Claude hook-held permissions: an explicit,
reversible edit of a Claude Code settings JSON file. Default target is
the user's ``~/.claude/settings.json``; ``--settings <path>`` overrides
it (tests and scratch runs always pass a tmp path — this verb must
NEVER be pointed at the operator's real config unintentionally, and
refuses to overwrite a file it cannot parse).

Exit contract mirrors the other brain CLIs: 0 did the job, 1 failure,
2 usage.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Optional

from .settings_edit import (
    CLAUDE_TIMEOUT_MARGIN_S,
    HOOK_MARKER,
    build_hook_command,
    install_hook,
    uninstall_hook,
)

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

#: Tools the held gate answers for by default. Claude's own permission
#: prompts center on the writing trio; the matcher is a ``--matcher``
#: override away for stricter or wider installs.
DEFAULT_MATCHER = "Bash|Edit|Write"

#: The hook's own deny deadline (seconds). The brain socket also holds
#: this long, so both sides deny at the same moment.
DEFAULT_TIMEOUT_S = 120

USAGE = f"""usage: herdr-brain hooks <verb> [args]

  install [--settings PATH] [--matcher RE] [--timeout-s N]
                     add the ONE marked PreToolUse hook entry that holds
                     Claude permissions on the brain socket (idempotent;
                     preserves every other setting). Default settings
                     file: ~/.claude/settings.json
  uninstall [--settings PATH]
                     remove exactly that entry (safe when absent)
  status [--settings PATH]
                     read-only: is the hook installed, and as what

The hook command pins the venv python, the package src and the brain
state dir; it carries no credential. On timeout, brain-down or any
error the hook DENIES the tool call (audit 02 §5: reject by default).
Installed marker: {HOOK_MARKER}
"""


def _default_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _read_settings(path: Path) -> Optional[dict]:
    """Parses the settings JSON; ``{}`` for a missing file.

    Returns None (and says why on stderr) for anything unreadable or
    unparsable — a corrupt file is never overwritten with a guess.
    """
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"hooks: cannot read {path}: {exc}", file=sys.stderr)
        return None
    try:
        payload = json.loads(text) if text.strip() else {}
    except ValueError as exc:
        print(
            f"hooks: {path} is not valid JSON ({exc}); refusing to touch it",
            file=sys.stderr,
        )
        return None
    if not isinstance(payload, dict):
        print(f"hooks: {path} is not a JSON object; refusing to touch it", file=sys.stderr)
        return None
    return payload


def _write_settings(path: Path, payload: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"hooks: cannot write {path}: {exc}", file=sys.stderr)
        return False
    return True


def _find_marked(payload: dict) -> Optional[dict]:
    for entry in payload.get("hooks", {}).get("PreToolUse", []) or []:
        if isinstance(entry, dict) and any(
            isinstance(hook, dict) and HOOK_MARKER in str(hook.get("command", ""))
            for hook in entry.get("hooks", []) or []
        ):
            return entry
    return None


def _print(text: str) -> None:
    print(text)


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        _print(USAGE)
        return EXIT_USAGE
    verb, rest = args[0], args[1:]
    if verb in ("-h", "--help", "help"):
        _print(USAGE)
        return EXIT_OK
    if verb not in ("install", "uninstall", "status"):
        _print(f"hooks: unknown verb {verb!r}\n")
        _print(USAGE)
        return EXIT_USAGE

    settings_arg: Optional[str] = None
    matcher = DEFAULT_MATCHER
    timeout_s = DEFAULT_TIMEOUT_S
    it = iter(rest)
    for flag in it:
        if flag == "--settings":
            settings_arg = next(it, None)
            if settings_arg is None:
                _print("hooks: --settings needs a path")
                return EXIT_USAGE
        elif flag == "--matcher":
            matcher = next(it, None)
            if not matcher:
                _print("hooks: --matcher needs a non-empty pattern")
                return EXIT_USAGE
        elif flag == "--timeout-s":
            raw = next(it, None)
            try:
                timeout_s = int(raw) if raw is not None else 0
            except ValueError:
                timeout_s = 0
            if timeout_s < 10 or timeout_s > 3600:
                _print("hooks: --timeout-s must be between 10 and 3600")
                return EXIT_USAGE
        else:
            _print(f"hooks: unknown flag {flag!r} for {verb}\n")
            _print(USAGE)
            return EXIT_USAGE

    path = Path(settings_arg) if settings_arg else _default_settings_path()
    payload = _read_settings(path)
    if payload is None:
        return EXIT_FAIL

    if verb == "status":
        entry = _find_marked(payload)
        if entry is None:
            _print(f"hooks: NOT installed in {path}")
            return EXIT_OK
        hook = next(
            h for h in entry.get("hooks", []) if HOOK_MARKER in str(h.get("command", ""))
        )
        _print(f"hooks: installed in {path}")
        _print(f"  matcher: {entry.get('matcher', '')!r}")
        _print(f"  command: {hook.get('command', '')}")
        _print(
            f"  claude timeout: {hook.get('timeout', '?')}s "
            f"(hook denies at {max(0, int(hook.get('timeout', 0)) - CLAUDE_TIMEOUT_MARGIN_S)}s)"
        )
        return EXIT_OK

    if verb == "uninstall":
        payload, removed = uninstall_hook(payload)
        if not _write_settings(path, payload):
            return EXIT_FAIL
        _print(
            f"hooks: removed {removed} hook entr{'y' if removed == 1 else 'ies'} from {path}"
            if removed
            else f"hooks: nothing to remove in {path}"
        )
        return EXIT_OK

    # install
    command = build_hook_command(
        python=sys.executable,
        src=str(Path(__file__).resolve().parents[2]),
        state_dir=str(_resolved_state_dir()),
    )
    install_hook(payload, command=command, matcher=matcher, timeout_s=timeout_s)
    if not _write_settings(path, payload):
        return EXIT_FAIL
    _print(f"hooks: installed the held-permission hook in {path}")
    _print(f"  matcher: {matcher!r}  (deny after {timeout_s}s without an answer)")
    _print("  remove it any time with: herdr-brain hooks uninstall")
    return EXIT_OK


def _resolved_state_dir() -> Path:
    """The brain state dir the hook command pins (same rule as config)."""
    import os

    return Path(
        os.getenv("HERDR_BRAIN_STATE_DIR")
        or (Path(os.getenv("XDG_STATE_HOME") or Path.home() / ".local" / "state"))
        / "herdr-brain"
    ).expanduser()


if __name__ == "__main__":  # pragma: no cover - manual entrypoint
    raise SystemExit(main())
