#!/usr/bin/env bash
# Bootstrap the herdr-brain virtualenv and install dev dependencies.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
echo "==> Creating .venv with $PYTHON"
"$PYTHON" -m venv .venv

echo "==> Installing herdr-brain (editable) + dev deps"
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -e ".[dev]"

echo "==> Running unit tests"
.venv/bin/python -m pytest -q

echo "Done. Activate with: source $ROOT/.venv/bin/activate"
