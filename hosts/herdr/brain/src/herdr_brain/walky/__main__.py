"""``python -m herdr_brain.walky`` — the entry the launcher's ``msg``
case execs (bin/herdr-brain passes the verbs after ``msg``)."""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
