"""``python -m herdr_brain.crew`` — the operator's crew terminal."""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
