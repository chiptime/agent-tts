#!/usr/bin/env bash
set -euo pipefail

# Release publisher for agent-tts v0.3.1
# Pushes the pre-created evening commit and tag to GitHub.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> Verifying tests before release..."
/home/bruno/.local/share/herdr-tts/venv/bin/pytest engine/tests/test_edge_compat.py engine/tests/test_fallback_chain.py -q

echo "==> Pushing main and tag v0.3.1 to origin..."
git push origin main
git push origin v0.3.1

echo "==> Successfully released agent-tts v0.3.1!"
