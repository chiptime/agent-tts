"""Credentials step: capture the GLM API key for the brain role
(AT-11 task 3.2, design slice 16; validator note B).

Registered for the ``brain`` role ONLY — a plugin-only run has no
credentials step at all and therefore completes keyless by construction
(spec scenario "GLM key optional without the brain").

Behaviour:

- a value supplied on an unattended channel (FD, then FILE) is always
  used: that is a deliberate rotation of the persisted key;
- otherwise an already-persisted, non-empty ``GLM_API_KEY`` in the env
  file satisfies the step: nothing is re-asked (not even by getpass) and
  the file is left byte-identical (the re-run / reinstall-idempotence
  contract);
- otherwise, when interactive, the value comes from ``getpass`` — via
  :func:`herdr_onboarding.prompts.read_secret` (FD → FILE → getpass;
  never argv, never an ambient ``GLM_API_KEY`` in the process
  environment);
- the captured value is registered on the context BEFORE anything else
  can fail, so every later diagnostic passes the ``redact()`` boundary;
- the value is persisted through the atomic mode-600 merge writer;
- a missing value maps to ``MissingAnswer`` (exit 20) when
  non-interactive and ``StepAbort`` (exit 40) when interactive.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from herdr_onboarding import prompts, secrets
from herdr_onboarding.wizard import MissingAnswer, StepAbort

if TYPE_CHECKING:  # pragma: no cover — import-cycle-free typing only
    from herdr_onboarding.wizard import RunContext

GLM_KEY_NAME = "GLM_API_KEY"
LABEL = "GLM API key"


class CredentialsStep:
    name = "credentials"
    roles = ("brain",)

    def run(self, ctx: "RunContext") -> None:
        env_file = secrets.brain_env_path(ctx.env)
        existing = _read_existing(env_file)
        # Unattended channels (FD, then FILE) are consulted first and
        # explicitly: a value supplied there is a deliberate rotation.
        value = prompts.read_secret(LABEL, env=ctx.env, interactive=False)
        if not value and existing:
            # Already configured and nothing new supplied: preserve the
            # file byte-identical and never re-ask.
            return
        if value is None and ctx.interactive:
            value = prompts.read_secret(
                LABEL, env=ctx.env, interactive=True, getpass_fn=_getpass()
            )
        if not value:
            if ctx.interactive:
                raise StepAbort(f"no {LABEL} provided")
            raise MissingAnswer(
                f"the {LABEL} (required for the brain role); supply it via "
                f"{prompts.FD_ENV}=<fd> or {prompts.FILE_ENV}=<mode-600 "
                "file>, or answer the interactive prompt"
            )
        ctx.register_secret(value)
        secrets.merge_env_file(env_file, {GLM_KEY_NAME: value})


def _getpass():
    """Resolved at call time so the terminal prompt stays a single seam."""
    import getpass

    return getpass.getpass


def _read_existing(env_file) -> str:
    from herdr_onboarding import resolve as _resolve

    try:
        return _resolve._read_env_key(env_file, GLM_KEY_NAME) or ""
    except OSError:
        return ""
