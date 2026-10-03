"""Surface contract v2 (voice-stack VS2.1): additive negotiation.

The host CLI is exercised as a REAL subprocess with an isolated XDG home
(the venv auto-bootstrap finds the stub python and never runs). v1 must
stay byte-identical; v2 is one strict JSON line.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

HOST = Path(__file__).resolve().parent.parent / "bin" / "herdr-tts"


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    data = tmp_path / "data/herdr-tts/venv/bin"
    data.mkdir(parents=True)
    stub = data / "python"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    for var, sub in (("XDG_DATA_HOME", "data"), ("XDG_CONFIG_HOME", "config"), ("XDG_STATE_HOME", "state")):
        target = tmp_path / sub
        target.mkdir(exist_ok=True)
        monkeypatch.setenv(var, str(target))
    return tmp_path


def run_host(home, *args) -> subprocess.CompletedProcess:
    return subprocess.run([str(HOST), *args], capture_output=True, text=True, timeout=30)


def test_legacy_version1_probe_unchanged(home):
    proc = run_host(home, "--contract-version")
    assert proc.returncode == 0
    assert proc.stdout == "1\n"          # exactly the v1 answer, nothing else
    assert proc.stderr == ""


def test_capabilities_strict_json(home):
    proc = run_host(home, "--contract-capabilities")
    assert proc.returncode == 0
    assert proc.stderr == ""
    assert proc.stdout.count("\n") == 1  # one line, one newline
    payload = json.loads(proc.stdout)
    assert set(payload) == {"supported_protocols"}
    assert payload["supported_protocols"] == [1, 2]
    assert all(isinstance(p, int) for p in payload["supported_protocols"])


def test_unknown_protocol_failsoft(home):
    """A consumer asking for a protocol the host never claimed gets a clean
    'not supported' from the LIST itself — the flag never errors and never
    claims support it does not have (fail-soft is the consumer's decision)."""
    payload = json.loads(run_host(home, "--contract-capabilities").stdout)
    supported = payload["supported_protocols"]
    assert 99 not in supported and 1 in supported and 2 in supported
    # v1 consumers keep working with zero change:
    assert run_host(home, "--contract-version").stdout == "1\n"
