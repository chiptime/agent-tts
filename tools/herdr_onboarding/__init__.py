"""herdr_onboarding — shared first-run onboarding and portable resolution.

AT-11 task 2.1 (design slice 9) delivers :mod:`herdr_onboarding.resolve`;
the wizard CLI (``__main__``) and the step modules arrive with milestone 3.
Standard library only (design Decision 1): importing this package adds no
runtime dependency to any venv — entry points reach it through
``PYTHONPATH``-style invocation.
"""
