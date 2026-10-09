"""Non-argv secret intake for the first-run wizard (AT-11 task 3.2).

Three ranked channels (design Decision 2); argv is excluded at every
level — no flag in this package ever carries a secret value:

=====  ==========================================  ==========================
Rank  Channel                                     Guarantee
=====  ==========================================  ==========================
1     ``HERDR_ONBOARDING_SECRET_FD=<n>``          primary unattended: read
       (inherited file descriptor *n*)             once, close; invisible to
                                                   ``ps`` and to
                                                   ``/proc/<pid>/environ``
2     ``HERDR_ONBOARDING_SECRET_FILE=<path>``     fallback: mode-600 regular
       (opt-out unlink with                         file, read once; optional
       ``HERDR_ONBOARDING_SECRET_UNLINK=1``)       unlink after the read
3     ``getpass.getpass()``                       interactive only: no
                                                   terminal echo, never
                                                   re-displayed
=====  ==========================================  ==========================

An ambient ``GLM_API_KEY`` in the process environment is deliberately
NOT a channel: it is readable from ``/proc/<pid>/environ`` by any
same-user process and inherited by every child.  Non-secret answers
(voice provider, keymap style, STT size, consents) travel via flags and
environment variables — that is what they are for.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Callable, Mapping, Optional

FD_ENV = "HERDR_ONBOARDING_SECRET_FD"
FILE_ENV = "HERDR_ONBOARDING_SECRET_FILE"
UNLINK_ENV = "HERDR_ONBOARDING_SECRET_UNLINK"

_UNLINK_TRUE = {"1", "true", "yes"}


class SecretIntakeError(RuntimeError):
    """A secret channel is misconfigured.

    The message is actionable and value-free: it names the channel and
    the remediation, never the secret.
    """


def _strip_trailing_newlines(value: str) -> str:
    return value.rstrip("\r\n")


def _read_from_fd(env: Mapping[str, str]) -> Optional[str]:
    raw = env.get(FD_ENV, "").strip()
    if not raw:
        return None
    try:
        fd = int(raw)
    except ValueError:
        raise SecretIntakeError(
            f"{FD_ENV} must be an integer file descriptor number, not some "
            "other value; pass the secret on an inherited descriptor"
        ) from None
    try:
        # Read once, then close: the descriptor never survives the step.
        with os.fdopen(fd, "r", encoding="utf-8", closefd=True) as handle:
            return _strip_trailing_newlines(handle.read())
    except OSError as exc:
        raise SecretIntakeError(
            f"cannot read the secret from {FD_ENV}: {exc}; pass an "
            "inheritable descriptor that carries the secret"
        ) from None


def _read_from_file(env: Mapping[str, str]) -> Optional[str]:
    raw = env.get(FILE_ENV, "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    try:
        info = os.stat(path)
    except OSError as exc:
        raise SecretIntakeError(
            f"cannot read {FILE_ENV}: {exc}; point it at an existing "
            "mode-600 file carrying the secret"
        ) from None
    if not stat.S_ISREG(info.st_mode):
        raise SecretIntakeError(
            f"{FILE_ENV} must be a regular file (mode 600), not a "
            f"directory, fifo or socket: {path}"
        )
    mode = stat.S_IMODE(info.st_mode)
    if mode != 0o600:
        raise SecretIntakeError(
            f"{FILE_ENV} must have mode 600 (currently {mode:o}); run: "
            f"chmod 600 {path}"
        )
    # Read once.
    value = _strip_trailing_newlines(path.read_text(encoding="utf-8"))
    if env.get(UNLINK_ENV, "").strip().lower() in _UNLINK_TRUE:
        try:
            path.unlink()
        except OSError as exc:
            raise SecretIntakeError(
                f"read the secret but could not unlink {FILE_ENV}: {exc}; "
                f"remove the file yourself: rm {path}"
            ) from None
    return value


def read_secret(
    label: str,
    *,
    env: Mapping[str, str],
    interactive: bool,
    getpass_fn: Callable[[str], str] = None,
) -> Optional[str]:
    """The secret for ``label`` through the ranked channels, or ``None``.

    ``None`` means "no channel supplied a value" — the caller decides
    whether that is a :class:`~herdr_onboarding.wizard.MissingAnswer`
    (non-interactive) or a
    :class:`~herdr_onboarding.wizard.StepAbort` (the user submitted
    nothing interactively).  A misconfigured channel raises
    :class:`SecretIntakeError` with a value-free, actionable message.
    """
    value = _read_from_fd(env)
    if value is None:
        value = _read_from_file(env)
    if value is not None:
        return value
    if interactive:
        if getpass_fn is None:
            import getpass

            getpass_fn = getpass.getpass
        return _strip_trailing_newlines(getpass_fn(f"{label} (hidden, no echo): "))
    return None
