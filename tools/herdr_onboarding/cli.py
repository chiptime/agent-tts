"""CLI surface for ``python -m herdr_onboarding`` (AT-11 task 3.1).

Argument parsing is hand-rolled on purpose: argparse usage errors echo
the offending token (``unrecognized arguments: <flag> <value>``), and
this CLI must never reflect a secret-carrying argv token in any output
byte.  Unknown arguments are refused with a fixed, echo-free message.
No flag in this surface ever carries a secret value.
"""

from __future__ import annotations

import os
import sys
from typing import Callable, Iterable, List, Mapping, Optional, TextIO, Tuple

from herdr_onboarding.health import make_health_gate
from herdr_onboarding.wizard import Wizard, WizardOptions

USAGE = (
    "usage: python -m herdr_onboarding --role {plugin,brain} "
    "[--no-first-run] [--non-interactive] [--json] [--doctor]\n"
    "       doctor [--fix-credentials] [--non-interactive] [--json]\n"
    "       [--voice-provider {edge,openai,elevenlabs,piper}] [--voice NAME]\n"
    "       [--keymap-style {menu,direct,ctrlalt,none}] [--replace-keymap]\n"
    "       [--stt {tiny,base,small,none}]"
)

ROLES = ("plugin", "brain")
_FLAGS = {
    "--no-first-run": "no_first_run",
    "--non-interactive": "non_interactive",
    "--json": "json_output",
    "--replace-keymap": "replace_keymap",
}
# Non-secret preference flags: ``flag -> (option field, allowed values)``;
# ``None`` allowed values = free-form (a voice name is provider-specific).
_VALUE_FLAGS = {
    "--voice-provider": ("voice_provider", ("edge", "openai", "elevenlabs", "piper")),
    "--voice": ("voice", None),
    "--keymap-style": ("keymap_style", ("menu", "direct", "ctrlalt", "none")),
    "--stt": ("stt", ("tiny", "base", "small", "none")),
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
        flag, sep, inline = token.partition("=")
        if flag in _VALUE_FLAGS:
            field, allowed = _VALUE_FLAGS[flag]
            if sep:
                value, step = inline, 1
            elif i + 1 < len(argv):
                value, step = argv[i + 1], 2
            else:
                return None, f"{flag} requires a value"
            value = value.strip()
            if allowed is not None:
                value = value.lower()
                if value not in allowed:
                    return None, f"{flag} must be one of: {', '.join(allowed)}"
            elif not value or '"' in value or "\n" in value or "\r" in value:
                return None, f"{flag} requires a single-line value without double quotes"
            settings[field] = value
            i += step
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

    A usage error returns ``2``.  ``health_gate`` is the marker gate:
    ``None`` (the default, and what ``python -m herdr_onboarding`` uses)
    selects the real gate — ``/health`` reports ``tts: ok`` AND ``herdr
    plugin list`` emits no warning (task 3.5, ``herdr_onboarding.health``).
    Tests inject a ``Callable[[RunContext], bool]`` instead.
    """
    tokens = list(sys.argv[1:] if argv is None else argv)
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    if "doctor" in tokens or "--doctor" in tokens:
        from herdr_onboarding import doctor
        from herdr_onboarding.wizard import MissingAnswer
        subcommand = "doctor" in tokens
        fix = "--fix-credentials" in tokens
        filtered = [t for t in tokens if t not in ("doctor", "--doctor", "--fix-credentials")]
        options, error = _parse(filtered)
        allowed = {"--role", "plugin", "brain", "--role=plugin", "--role=brain", "--non-interactive", "--json"}
        if error or any(t not in allowed for t in filtered) or (fix and (
            not subcommand or "--doctor" in tokens or options.role != "brain"
        )):
            err.write("herdr-onboarding: doctor accepts checks only; credential capture requires brain doctor --fix-credentials\n")
            return 2
        context = Wizard(options, env=env, stdin=stdin, stdout=out, stderr=err, isatty=isatty, steps=[]).ctx
        if fix:
            try:
                doctor.capture_credentials(context)
            except MissingAnswer:
                context.diagnostic("credential capture needs HERDR_ONBOARDING_SECRET_FD or HERDR_ONBOARDING_SECRET_FILE")
                return 20
            except (Exception, KeyboardInterrupt):
                context.diagnostic("credential capture aborted; no automatic repair performed")
                return 40
        return doctor.report(doctor.Doctor(options.role, dict(os.environ if env is None else env)).checks(),
                             out, json_output=options.json_output)
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
        health_gate=health_gate if health_gate is not None else make_health_gate(),
        clock=clock,
    )
    return wizard.run()
