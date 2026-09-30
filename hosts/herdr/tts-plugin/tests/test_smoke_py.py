"""Minimal host smoke suite (voice-stack VS0.3).

Proves the host test runner wiring works under coverage with an explicit
source scope. Real behavioral coverage for lib/ arrives with the voice-stack
milestones; this file intentionally stays a smoke check.
"""

from __future__ import annotations

import pathlib
import py_compile

LIB = pathlib.Path(__file__).resolve().parent.parent / "lib"


def test_lib_modules_compile() -> None:
    """Every Python module under lib/ must byte-compile."""
    modules = sorted(LIB.glob("*.py"))
    assert modules, "expected at least one module under lib/"
    for module in modules:
        py_compile.compile(str(module), doraise=True)


def test_bin_entrypoint_present() -> None:
    """The host CLI entrypoint must exist and be executable."""
    entry = LIB.parent / "bin" / "herdr-tts"
    assert entry.is_file()
    assert entry.stat().st_mode & 0o111, "bin/herdr-tts must stay executable"
