"""``python -m herdr_brain`` — the launcher's generic verb dispatch.

``bin/herdr-brain`` execs this module for verbs the bash entrypoint has
no case for (its ``*)`` arm), after loading the environment and
resolving the venv. Verbs:

* ``hook-permission`` — the Claude Code PreToolUse hook protocol on
  stdin/stdout (parity-f7e S3). This is the exact command the
  ``hooks install`` entry runs, minus the pinned env prefix.
* ``hooks`` — ``install|uninstall|status`` of that hook in a Claude
  settings file.
"""

from __future__ import annotations

import sys
from typing import List, Optional

USAGE = """usage: python -m herdr_brain <verb>

  hook-permission   the Claude PreToolUse hook: reads the request JSON on
                    stdin, holds on the brain socket, prints the decision
  hooks             {install|uninstall|status} the held-permission hook in
                    a Claude Code settings file (see herdr-brain hooks --help)
"""

_VERBS = ("hook-permission", "hooks")


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0 if args else 2
    verb, rest = args[0], args[1:]
    if verb == "hook-permission":
        from .hook_permissions import hook_main

        return hook_main(rest)
    if verb == "hooks":
        from .hooks.cli import main as hooks_main

        return hooks_main(rest)
    print(f"unknown verb {verb!r}\n\n{USAGE}", file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover - manual entrypoint
    raise SystemExit(main())
