"""First-run wizard orchestration (AT-11 task 3.1, design slice 15).

The :class:`Wizard` owns the exit contract, the completion-marker
lifecycle and the diagnostic boundary; step modules plug into it through
:data:`herdr_onboarding.steps.steps_for_role`.

Exit contract (design "Wizard CLI"):

=====  =====================================================
Code   Meaning
=====  =====================================================
0      completed; the completion marker was written
10     completed with no marker (health gate not wired yet)
20     an answer is missing in non-interactive mode
30     health gate failed (retryable; no marker)
40     user aborted or a step failed (no partial state)
=====  =====================================================

Health-gate seam for task 3.5
-----------------------------
``Wizard(..., health_gate=callable)`` decides whether the completion
marker may be written.  Until 3.5 wires the real gate (``/health``
reports ``tts: ok`` and ``herdr plugin list`` emits no manifest
warnings), the default is ``None``: the run completes its steps, writes
no marker and exits ``10`` ("completed with no marker").  Tests and the
3.5 wiring inject a ``Callable[[RunContext], bool]``; ``True`` → marker
+ exit 0, ``False`` or a raised exception → exit 30 and no marker.

Diagnostic boundary
-------------------
Every byte the wizard prints — human lines and ``--json`` summaries —
goes through :func:`_scrub`, the single redaction seam.  Task 3.2
replaces its body with ``herdr_onboarding.secrets.redact`` so every
diagnostic path passes the one ``redact()`` boundary.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional, TextIO, Union

from herdr_onboarding import resolve as _resolve

EXIT_COMPLETED = 0
EXIT_COMPLETED_NO_MARKER = 10
EXIT_MISSING_ANSWER = 20
EXIT_HEALTH_GATE_FAILED = 30
EXIT_ABORTED = 40

MARKER_DIRNAME = "herdr-tts"
MARKER_FILENAME = "first-run.done"
MARKER_VERSION = 1
MARKER_MODE = 0o644

ONBOARDING_HOME_ENV = "HERDR_ONBOARDING_HOME"
ONBOARDING_LIB_SUBDIR = "tools"
ONBOARD_HOME_MSG = (
    f"{ONBOARDING_HOME_ENV} to the directory containing herdr_onboarding/ "
    "(the Homebrew keg route stages only the plugin subdirectory and "
    "honestly does not include the wizard)"
)

SecretGate = Callable[["RunContext"], bool]


class MissingAnswer(Exception):
    """A required answer is absent in non-interactive mode."""

    def __init__(self, answer: str):
        self.answer = answer
        super().__init__(f"missing required answer: {answer}")


class StepAbort(Exception):
    """The user declined to continue; no partial state may remain."""


def _scrub(text: str, secret_values: Iterable[str]) -> str:
    """The single diagnostic redaction boundary.

    Placeholder body until task 3.2 delivers
    :func:`herdr_onboarding.secrets.redact`; the wizard routes every
    diagnostic through this one seam so the swap is a single line.
    """
    return text


def resolve_onboarding_lib(
    env: Optional[Mapping[str, str]] = None,
    start: Optional[Union[str, Path]] = None,
) -> Path:
    """The ``PYTHONPATH`` directory that exposes ``herdr_onboarding``.

    Resolution order (design Decision 1):

    1. ``HERDR_ONBOARDING_HOME`` — the directory that directly contains
       ``herdr_onboarding/`` (explicit override for tests and packagers).
    2. Ascend from ``start`` (by default this module's own location), at
       most :data:`herdr_onboarding.resolve.ROOT_ASCEND_MAX` levels,
       taking the first directory ``D`` where
       ``D/tools/herdr_onboarding/__main__.py`` exists; return ``D/tools``.
       This predicate reaches the module in every supported layout —
       source checkout, curl-route full clone and subdirectory managed
       install all place the entry point three levels below a monorepo
       root that carries ``tools/``.
    3. Fail with an actionable English message naming step 1.  The
       Homebrew keg route stages only the plugin subdirectory and
       honestly does not include the wizard.
    """
    environ = os.environ if env is None else env
    override = environ.get(ONBOARDING_HOME_ENV, "").strip()
    if override:
        candidate = Path(override).expanduser()
        if (candidate / "herdr_onboarding" / "__main__.py").is_file():
            return candidate
        raise _resolve.ResolutionError(
            f"{ONBOARDING_HOME_ENV}={override} does not contain "
            "herdr_onboarding/__main__.py; point it at the directory that "
            "directly contains herdr_onboarding/"
        )
    current = Path(start).resolve() if start is not None else Path(__file__).resolve().parent
    for _ in range(_resolve.ROOT_ASCEND_MAX):
        lib = current / ONBOARDING_LIB_SUBDIR
        if (lib / "herdr_onboarding" / "__main__.py").is_file():
            return lib
        if current.parent == current:
            break
        current = current.parent
    raise _resolve.ResolutionError(
        "cannot resolve the onboarding library: no "
        f"{ONBOARDING_LIB_SUBDIR}/herdr_onboarding/__main__.py within "
        f"{_resolve.ROOT_ASCEND_MAX} levels of {current}; set "
        f"{ONBOARD_HOME_MSG}"
    )


def marker_path(env: Optional[Mapping[str, str]] = None) -> Path:
    """``<XDG_CONFIG_HOME or ~/.config>/herdr-tts/first-run.done``."""
    environ = os.environ if env is None else env
    directory = _resolve._config_dir(dict(environ))
    if directory is None:
        raise _resolve.ResolutionError(
            "cannot resolve the config directory for the completion marker: "
            "set HOME or XDG_CONFIG_HOME"
        )
    return directory / MARKER_DIRNAME / MARKER_FILENAME


def read_marker(env: Optional[Mapping[str, str]] = None) -> Optional[dict]:
    """The parsed marker payload, or ``None`` when absent or corrupt."""
    path = marker_path(env)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def write_marker(
    env: Optional[Mapping[str, str]], payload: Mapping[str, object]
) -> Path:
    """Writes the completion marker (mode 644, design contract).

    Callers are responsible for the "only after the health gate" rule;
    :class:`Wizard` enforces it via the gate seam.
    """
    path = marker_path(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload)) + "\n", encoding="utf-8")
    path.chmod(MARKER_MODE)
    return path


@dataclass
class WizardOptions:
    """Parsed CLI answers (no secret value ever appears here)."""

    role: str
    no_first_run: bool = False
    non_interactive: bool = False
    json_output: bool = False


@dataclass
class RunContext:
    """What a step may touch: environment, streams, preferences, secrets."""

    options: WizardOptions
    env: Dict[str, str]
    stdin: TextIO
    stdout: TextIO
    stderr: TextIO
    interactive: bool
    secret_values: List[str] = field(default_factory=list)
    preferences: Dict[str, object] = field(default_factory=dict)

    def register_secret(self, value: Optional[str]) -> None:
        """Records a secret value for the diagnostic redaction boundary."""
        if value and len(value) >= 4 and value not in self.secret_values:
            self.secret_values.append(value)

    def prompt(self, message: str) -> str:
        """Interactive text answer (plain stdin line; never a secret)."""
        if not self.interactive:
            raise MissingAnswer(message)
        self.stdout.write(f"{message}: ")
        self.stdout.flush()
        line = self.stdin.readline()
        if line == "":
            raise StepAbort("standard input closed before the answer arrived")
        return line.rstrip("\r\n")

    def diagnostic(self, text: str) -> None:
        self.stderr.write(_scrub(text, self.secret_values) + "\n")
        self.stderr.flush()

    def emit(self, text: str) -> None:
        self.stdout.write(_scrub(text, self.secret_values) + "\n")
        self.stdout.flush()


class Wizard:
    """Runs the registered steps for the role and owns the exit contract."""

    def __init__(
        self,
        options: WizardOptions,
        *,
        env: Optional[Mapping[str, str]] = None,
        stdin: Optional[TextIO] = None,
        stdout: Optional[TextIO] = None,
        stderr: Optional[TextIO] = None,
        isatty: Optional[Callable[[], bool]] = None,
        steps: Optional[Iterable] = None,
        health_gate: Optional[SecretGate] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ):
        self.options = options
        self._isatty = isatty or (lambda: bool(sys.stdin.isatty()))
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._health_gate = health_gate
        if steps is None:
            from herdr_onboarding.steps import steps_for_role

            steps = steps_for_role(options.role)
        self.steps = list(steps)
        interactive = not options.non_interactive and bool(self._isatty())
        self.ctx = RunContext(
            options=options,
            env=dict(os.environ if env is None else env),
            stdin=stdin if stdin is not None else sys.stdin,
            stdout=stdout if stdout is not None else sys.stdout,
            stderr=stderr if stderr is not None else sys.stderr,
            interactive=interactive,
        )

    def run(self) -> int:
        opts = self.options
        if opts.no_first_run:
            self._finish(EXIT_COMPLETED, "skipped", note="first-run skipped")
            return EXIT_COMPLETED
        if read_marker(self.ctx.env) is not None:
            self._finish(
                EXIT_COMPLETED, "already-completed", note="marker present"
            )
            return EXIT_COMPLETED
        if not opts.non_interactive and not self.ctx.interactive:
            self.ctx.diagnostic(
                "no TTY detected; running non-interactively — provide "
                "answers via flags or environment variables, or skip "
                "onboarding with --no-first-run"
            )
        for step in self.steps:
            try:
                step.run(self.ctx)
            except MissingAnswer as exc:
                self.ctx.diagnostic(
                    f"non-interactive run: {exc}; provide the answer via "
                    "its flag or environment variable, or skip onboarding "
                    "with --no-first-run"
                )
                self._finish(EXIT_MISSING_ANSWER, "missing-answer")
                return EXIT_MISSING_ANSWER
            except (StepAbort, KeyboardInterrupt):
                self.ctx.diagnostic(
                    "onboarding aborted: no completion marker was written"
                )
                self._finish(EXIT_ABORTED, "aborted")
                return EXIT_ABORTED
            except Exception as exc:  # single boundary; redacted, no traceback
                self.ctx.diagnostic(
                    f"onboarding step failed: {type(exc).__name__}: {exc}; "
                    "no completion marker was written"
                )
                self._finish(EXIT_ABORTED, "aborted")
                return EXIT_ABORTED
        return self._finish_gate()

    def _finish_gate(self) -> int:
        if self._health_gate is None:
            self.ctx.diagnostic(
                "health gate not wired yet (task 3.5): completing without "
                "the completion marker"
            )
            self._finish(EXIT_COMPLETED_NO_MARKER, "completed-no-marker")
            return EXIT_COMPLETED_NO_MARKER
        try:
            passed = bool(self._health_gate(self.ctx))
        except Exception as exc:  # noqa: BLE001 — a crashing gate is a failed gate
            self.ctx.diagnostic(
                f"health gate raised {type(exc).__name__}: {exc}; no "
                "completion marker was written"
            )
            self._finish(EXIT_HEALTH_GATE_FAILED, "health-gate-failed")
            return EXIT_HEALTH_GATE_FAILED
        if not passed:
            self.ctx.diagnostic(
                "health gate failed: no completion marker was written; the "
                "next launch will retry onboarding"
            )
            self._finish(EXIT_HEALTH_GATE_FAILED, "health-gate-failed")
            return EXIT_HEALTH_GATE_FAILED
        completed_at = self._clock().astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        payload: Dict[str, object] = {
            "version": MARKER_VERSION,
            "completed_at": completed_at,
            "role": self.options.role,
        }
        payload.update(self.ctx.preferences)
        write_marker(self.ctx.env, payload)
        self._finish(EXIT_COMPLETED, "completed", marker_written=True)
        return EXIT_COMPLETED

    def _finish(
        self,
        code: int,
        status: str,
        *,
        marker_written: bool = False,
        note: Optional[str] = None,
    ) -> None:
        if self.options.json_output:
            record = {
                "status": status,
                "exit": code,
                "role": self.options.role,
                "marker_written": marker_written,
                "marker": str(marker_path(self.ctx.env)),
            }
            self.ctx.emit(json.dumps(record, sort_keys=True))
        else:
            line = f"herdr-onboarding: {status} (exit {code})"
            if note:
                line += f" — {note}"
            self.ctx.diagnostic(line)
