"""``bin/herdr-brain doctor`` runs BOTH stages (parity F7a S1).

The launcher runs under ``set -euo pipefail``. The onboarding stage's exit
code must be captured without errexit aborting the script, so the ops stage
(ports, root 443, VAPID, push store, crew roster, operator config) still runs
and the first non-zero exit code wins.

The interpreter is a stub: ``-m herdr_onboarding`` exits with
``STUB_ONBOARDING_RC`` and prints a JSON line; ``-m herdr_brain.ops_cli``
prints a marker and exits with ``STUB_OPS_RC``.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

BRAIN_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = BRAIN_ROOT / "bin" / "herdr-brain"

STUB_PYTHON = """#!/usr/bin/env bash
module=""
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "-m" ]]; then module="$2"; break; fi
  shift
done
case "$module" in
  herdr_onboarding)
    echo '{"stage": "onboarding"}'
    exit "${STUB_ONBOARDING_RC:-0}"
    ;;
  herdr_brain.ops_cli)
    echo "OPS-STAGE-RAN"
    exit "${STUB_OPS_RC:-0}"
    ;;
esac
exit 99
"""


def _fake_root(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "root"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "herdr-brain").write_text("")
    venv_bin = root / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    python = venv_bin / "python"
    python.write_text(STUB_PYTHON)
    python.chmod(0o755)
    onboarding = tmp_path / "onboarding"
    (onboarding / "herdr_onboarding").mkdir(parents=True)
    (onboarding / "herdr_onboarding" / "__main__.py").write_text("")
    return root, onboarding


def _run(tmp_path: Path, *args: str, onboarding_rc: int, ops_rc: int = 0):
    root, onboarding = _fake_root(tmp_path)
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "XDG_CONFIG_HOME": str(tmp_path / "home" / ".config"),
        "HERDR_PLUGIN_ROOT": str(root),
        "HERDR_ONBOARDING_HOME": str(onboarding),
        "STUB_ONBOARDING_RC": str(onboarding_rc),
        "STUB_OPS_RC": str(ops_rc),
    }
    return subprocess.run(
        ["bash", str(LAUNCHER), "doctor", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_failing_onboarding_stage_still_runs_ops_stage(tmp_path):
    result = _run(tmp_path, onboarding_rc=30)
    assert "OPS-STAGE-RAN" in result.stdout
    assert result.returncode == 30


def test_both_stages_green_exit_zero_with_both_outputs(tmp_path):
    result = _run(tmp_path, onboarding_rc=0)
    assert result.returncode == 0
    assert '{"stage": "onboarding"}' in result.stdout
    assert "─── brain ops doctor ───" in result.stdout
    assert "OPS-STAGE-RAN" in result.stdout


def test_ops_failure_alone_exits_one(tmp_path):
    result = _run(tmp_path, onboarding_rc=0, ops_rc=1)
    assert result.returncode == 1


@pytest.mark.parametrize("onboarding_rc", [0, 30])
def test_json_mode_keeps_stdout_parseable(tmp_path, onboarding_rc):
    result = _run(tmp_path, "--json", onboarding_rc=onboarding_rc)
    assert json.loads(result.stdout) == {"stage": "onboarding"}
    assert "OPS-STAGE-RAN" in result.stderr
    assert result.returncode == onboarding_rc
