#!/usr/bin/env bash
# ╔═══════════════════════════════════════════════════════════╗
# ║  herdr-tts bootstrap — Python environment setup (build hook)
# ╚═══════════════════════════════════════════════════════════╝
# Called by: the herdr host [[build]], bin/herdr-tts auto-bootstrap and
# scripts/install.sh. No arguments required; no prompts; idempotent.
set -euo pipefail

# Immutable agent-tts pin: tag preferred, full 40-char commit SHA while
# agent-tts publishes no tags. Bare `main` and short SHAs are prohibited.
# HERDR_AGENT_TTS_REF overrides for testing/dev only.
AGENT_TTS_REF="${HERDR_AGENT_TTS_REF:-19ad6b469751ddca4cab9c7ebb471ab01ee732ce}"
AGENT_TTS_SRC="git+https://github.com/chiptime/agent-tts.git@${AGENT_TTS_REF}"

DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/herdr-tts"
VENV_DIR="${DATA_DIR}/venv"
VENV_PY="${VENV_DIR}/bin/python"

# Upgrade mode: refresh the pinned ref even on a healthy venv. Reachable
# from every caller (env var; --upgrade argv synonym for humans/scripts).
if [[ "${1:-}" == "--upgrade" ]]; then
  HERDR_TTS_UPGRADE=1
fi

# Default fast path: a healthy venv is left untouched (never auto-upgrade).
if [[ -z "${HERDR_TTS_UPGRADE:-}" ]] && [[ -x "$VENV_PY" ]] \
   && "$VENV_PY" -c "import agent_tts" >/dev/null 2>&1; then
  exit 0
fi

# All installs go through one indirection: `uv pip` owns installs into
# uv-created (pip-less) venvs; `python -m pip` survives venvs without a
# bin/pip console script. The venv's bin/pip is never invoked.
py_install() { # "$@" = pip args
  if command -v uv >/dev/null 2>&1; then
    uv pip install --python "$VENV_PY" "$@"
  else
    "$VENV_PY" -m pip install "$@"
  fi
}

# Tooling check BEFORE any mkdir: a machine without python3 and uv must
# abort with nothing created.
if [[ ! -x "$VENV_PY" ]]; then
  if command -v uv >/dev/null 2>&1; then
    PY_TOOL=uv
  elif command -v python3 >/dev/null 2>&1; then
    PY_TOOL=python3
  else
    echo "Error: python3 or uv is required to build the herdr-tts environment." >&2
    echo "  Install uv (https://docs.astral.sh/uv/) or python3 (on Debian/Ubuntu: python3-venv)." >&2
    exit 1
  fi
fi

echo "==> setting up the herdr-tts Python environment at ${VENV_DIR}"
mkdir -p "$DATA_DIR"

if [[ ! -x "$VENV_PY" ]]; then
  if [[ "$PY_TOOL" == uv ]]; then
    echo "==> creating venv with uv"
    uv venv "$VENV_DIR"
  else
    echo "==> creating venv with python3 -m venv"
    python3 -m venv "$VENV_DIR"
  fi
fi

# Dev shortcut: editable install only on explicit opt-in. A checkout that
# exists without the flag is ignored with a warning — public installs must
# be environment-independent.
LOCAL_AGENT_TTS="${HOME}/Code/personal/agent-tts"
if [[ -n "${HERDR_TTS_UPGRADE:-}" ]]; then
  echo "==> upgrade: refreshing agent-tts to ${AGENT_TTS_REF}"
  py_install --quiet --upgrade "$AGENT_TTS_SRC"
elif [[ -n "${HERDR_TTS_DEV:-}" && -d "$LOCAL_AGENT_TTS" ]]; then
  echo "==> HERDR_TTS_DEV=1: installing agent-tts editable from ${LOCAL_AGENT_TTS}"
  py_install --quiet -e "$LOCAL_AGENT_TTS"
else
  if [[ -d "$LOCAL_AGENT_TTS" ]]; then
    echo "! ignoring dev checkout at ${LOCAL_AGENT_TTS} (set HERDR_TTS_DEV=1 to use it)"
  fi
  echo "==> installing agent-tts from the pinned ref ${AGENT_TTS_REF}"
  py_install --quiet "$AGENT_TTS_SRC"
fi

echo "✓ TTS environment ready."
