"""Step registry for the first-run wizard (AT-11 tasks 3.1+).

A step is any object carrying ``name``, ``roles`` and ``run(ctx)``.
The wizard runs the steps registered for the requested role, in order;
each step records its preferences on ``ctx.preferences`` (merged into
the completion marker) and may raise ``MissingAnswer`` (non-interactive
gap), ``StepAbort`` (user declined) or fail outright — the wizard maps
those onto the exit contract and never writes the marker on a non-zero
path.

Task 3.1 shipped the registry empty; task 3.2 registers the credentials
step (brain role only).
"""

from __future__ import annotations

from typing import List, Protocol


class Step(Protocol):
    """The structural contract every wizard step satisfies."""

    name: str
    roles: tuple

    def run(self, ctx) -> None:  # pragma: no cover — protocol shape only
        ...


def steps_for_role(role: str) -> List[Step]:
    """Registered steps applicable to ``role``, in execution order.

    Task 3.2 registers the credentials step for the brain role only — a
    plugin-only run has no credentials step and therefore completes
    keyless by construction.  Later tasks (3.3, 3.4) append their steps.
    """
    # Imported lazily: step modules import the wizard's exception types,
    # and the wizard imports this registry.
    from herdr_onboarding.steps.credentials import CredentialsStep

    registry: List[Step] = [CredentialsStep()]
    return [step for step in registry if role in step.roles]
