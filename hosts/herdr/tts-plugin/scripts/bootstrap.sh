#!/usr/bin/env bash
# ╔═══════════════════════════════════════════════════════════╗
# ║  herdr-tts bootstrap — auto-setup Python environment      ║
# ╚═══════════════════════════════════════════════════════════╝
set -euo pipefail

DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/herdr-tts"
VENV_DIR="${DATA_DIR}/venv"

if [[ -x "${VENV_DIR}/bin/python" ]]; then
  exit 0
fi

echo "📦 Configurando entorno Python para Herdr Neural TTS en ${VENV_DIR}..."
mkdir -p "$DATA_DIR"

if command -v uv >/dev/null 2>&1; then
  echo "  Usando uv para crear venv..."
  uv venv "$VENV_DIR"
  uv pip install --python "${VENV_DIR}/bin/python" edge-tts miniaudio
elif command -v python3 >/dev/null 2>&1; then
  echo "  Usando python3 -m venv..."
  python3 -m venv "$VENV_DIR"
  "${VENV_DIR}/bin/pip" install --quiet edge-tts miniaudio
else
  echo "Error: Se requiere Python 3 (python3) o uv para ejecutar Herdr Neural TTS." >&2
  exit 1
fi

echo "✓ Entorno TTS listo."
