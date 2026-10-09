"""CLI surface for ``python -m herdr_onboarding`` (AT-11 task 3.1).

Argument parsing is hand-rolled on purpose: argparse usage errors echo
the offending token (``unrecognized arguments: <flag> <value>``), and
this CLI must never reflect a secret-carrying argv token in any output
byte.  Unknown arguments are refused with a fixed, echo-free message.
No flag in this surface ever carries a secret value.
"""

from __future__ import annotations

import sys
from typing import Callable, Iterable, List, Mapping, Optional, TextIO, Tuple

from herdr_onboarding.wizard import Wizard, WizardOptions

USAGE = (
    "usage: python -m herdr_onboarding --role {plugin,brain} "
    "[--no-first-run] [--non-interactive] [--json]"
)

ROLES = ("plugin", "brain")
_FLAGS = {
    "--no-first-run": "no_first_run",
    "--non-interactive": "non_interactive",
    "--json": "json_output",
}


def _parse(argv: List[str]) -> Tuple[Optional[WizardOptions], Optional[str]]:
    """Echo-free parser.

    Returns ``(options, None)`` on success or ``(None, message)`` on a
    usage error.  The message never contains the raw offending token, so
    an accidental secret-valued flag cannot leak through it.
    """
    role: Optional[str] = None
    settings: dict = {}
    i = 0
    while i < len(argv):
        token = argv[i]
        if token in ("-h", "--help"):
            return None, "HELP"
        if token == "--role":
            if i + 1 >= len(argv):
                return None, "--role requires a value (plugin or brain)"
            role = argv[i + 1]
            i += 2
            continue
        if token.startswith("--role="):
            role = token[len("--role="):]
            i += 1
            continue
        if token in _FLAGS:
            settings[_FLAGS[token]] = True
            i += 1
            continue
        return None, (
            "unrecognized argument present; this CLI never accepts secret "
            "values on argv — run with --help to see the supported flags"
        )
    if role is None:
        return None, "--role is required (plugin or brain)"
    if role not in ROLES:
        return None, "--role must be plugin or brain"
    return WizardOptions(role=role, **settings), None


def main(
    argv: Optional[Iterable[str]] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
    isatty: Optional[Callable[[], bool]] = None,
    steps: Optional[Iterable] = None,
    health_gate=None,
    clock=None,
) -> int:
    """Runs the wizard and returns the exit contract code (0/10/20/30/40).

    A usage error returns ``2``.  ``health_gate`` is the task-3.5 seam:
    ``None`` (default) completes without the marker and exits ``10``;
    inject a ``Callable[[RunContext], bool]`` to gate the marker write.
    """
    tokens = list(sys.argv[1:] if argv is None else argv)
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    options, error = _parse(tokens)
    if error is not None:
        if error == "HELP":
            out.write(USAGE + "\n")
            return 0
        err.write(f"herdr-onboarding: {error}\n{USAGE}\n")
        return 2
    wizard = Wizard(
        options,
        env=env,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        isatty=isatty,
        steps=steps,
        health_gate=health_gate,
        clock=clock,
    )
    return wizard.run()
