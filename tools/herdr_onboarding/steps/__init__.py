"""Step registry for the first-run wizard (AT-11 tasks 3.1+).

A step is any object carrying ``name``, ``roles`` and ``run(ctx)``.
The wizard runs the steps registered for the requested role, in order;
each step records its preferences on ``ctx.preferences`` (merged into
the completion marker) and may raise ``MissingAnswer`` (non-interactive
gap), ``StepAbort`` (user declined) or fail outright — the wizard maps
those onto the exit contract and never writes the marker on a non-zero
path.

Task 3.1 ships the registry empty: the wizard skeleton runs, the exit
contract and the marker lifecycle are unit-tested, and no answers are
needed yet.
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

    Task 3.1 ships no steps: the wizard skeleton runs, the exit contract
    and the marker lifecycle are unit-tested, and no answers are needed
    yet.  Task 3.2 registers the credentials step here (brain role only
    — a plugin-only run completes keyless by construction).
    """
    return []
