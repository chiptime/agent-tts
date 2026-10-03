"""Host test bootstrap: make ``lib/`` importable the way the shell host does."""

import pathlib
import sys

LIB = pathlib.Path(__file__).resolve().parent.parent / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))
