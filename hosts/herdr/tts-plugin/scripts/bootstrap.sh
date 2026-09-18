#!/usr/bin/env bash
# ╔═══════════════════════════════════════════════════════════╗
# ║  herdr-tts bootstrap — auto-setup Python environment      ║
# ╚═══════════════════════════════════════════════════════════╝
set -euo pipefail

DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/herdr-tts"
VENV_DIR="${DATA_DIR}/venv"

if [[ -x "${VENV_DIR}/bin/python" ]] && "${VENV_DIR}/bin/python" -c "import agent_tts" >/dev/null 2>&1; then
  exit 0
fi

echo "📦 Configurando entorno Python para Herdr Neural TTS en ${VENV_DIR}..."
mkdir -p "$DATA_DIR"

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  if command -v uv >/dev/null 2>&1; then
    echo "  Usando uv para crear venv..."
    uv venv "$VENV_DIR"
  elif command -v python3 >/dev/null 2>&1; then
    echo "  Usando python3 -m venv..."
    python3 -m venv "$VENV_DIR"
  else
    echo "Error: Se requiere Python 3 (python3) o uv para ejecutar Herdr Neural TTS." >&2
    exit 1
  fi
fi

LOCAL_AGENT_TTS="${HOME}/Code/personal/agent-tts"
if [[ -d "$LOCAL_AGENT_TTS" ]]; then
  echo "  Instalando agent-tts desde ${LOCAL_AGENT_TTS}..."
  "${VENV_DIR}/bin/pip" install --quiet -e "$LOCAL_AGENT_TTS"
else
  echo "  Instalando agent-tts desde GitHub..."
  "${VENV_DIR}/bin/pip" install --quiet git+https://github.com/chiptime/agent-tts.git
fi

echo "✓ Entorno TTS listo."
