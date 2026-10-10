"""herdr_onboarding — shared first-run onboarding and portable resolution.

AT-11 task 2.1 (design slice 9) delivered :mod:`herdr_onboarding.resolve`;
task 3.1 (slice 15) adds the wizard skeleton — :mod:`herdr_onboarding.cli`
(``python -m herdr_onboarding``), :mod:`herdr_onboarding.wizard` (exit
contract, completion marker, health-gate seam) and the
:mod:`herdr_onboarding.steps` registry.  Later milestone-3 tasks register
the concrete steps.  Standard library only (design Decision 1): importing
this package adds no runtime dependency to any venv — entry points reach
it through ``PYTHONPATH``-style invocation.
"""
