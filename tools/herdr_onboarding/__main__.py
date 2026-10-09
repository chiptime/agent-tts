"""``python -m herdr_onboarding`` entry point (AT-11 task 3.1).

Entry points reach this module without installing anything::

    PYTHONPATH="<root>/tools" "$VENV_PY" -m herdr_onboarding --role plugin "$@"
"""

import sys

from herdr_onboarding.cli import main

if __name__ == "__main__":
    sys.exit(main())
