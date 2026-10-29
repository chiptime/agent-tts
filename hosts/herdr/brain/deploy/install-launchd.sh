#!/usr/bin/env bash
# herdr-brain — macOS launchd installer (S8, doc 11 fase 7).
#
# Renders deploy/herdr-brain.plist.template with the values discovered
# in THIS shell (the installer's full environment, mirroring the
# systemd installer's discipline) and loads it with launchctl. The
# brain never runs launchctl itself; only this operator-run script does.
#
# Usage: deploy/install-launchd.sh   (from the brain checkout root)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$(cd "$HERE/.." && pwd)"
PYTHON="$INSTALL_DIR/.venv/bin/python"
PLIST_DEST="${XDG_STATE_HOME:-$HOME/.local/state}/herdr-brain"
LABEL="dev.herdr.brain"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"

[[ -x "$PYTHON" ]] || { echo "ERROR: no venv interpreter at $PYTHON (run the bootstrap first)." >&2; exit 1; }

# The six tokens the template carries. HERDR_BIN/HERDR_TTS_HOME come
# from the environment the installer is run in (the systemd unit bakes
# in the same two); ENV_FILE is the operator's private 600 env file.
HERDR_BIN="${HERDR_BIN:-$(command -v herdr || echo herdr)}"
HERDR_TTS_HOME="${HERDR_TTS_HOME:-$(cd "$INSTALL_DIR/.." && pwd)/tts-plugin}"
PORT="${HERDR_BRAIN_PORT:-8741}"
ENV_FILE="${HERDR_BRAIN_ENV_FILE:-$HOME/.config/herdr-brain/env}"

mkdir -p "$HOME/Library/LaunchAgents" "$PLIST_DEST"

render() {
  local src="$HERE/herdr-brain.plist.template" out="$DEST"
  sed \
    -e "s|@INSTALL_DIR@|$INSTALL_DIR|g" \
    -e "s|@PYTHON@|$PYTHON|g" \
    -e "s|@PORT@|$PORT|g" \
    -e "s|@HERDR_BIN@|$HERDR_BIN|g" \
    -e "s|@TTS_HOME@|$HERDR_TTS_HOME|g" \
    -e "s|@ENV_FILE@|$ENV_FILE|g" \
    -e "s|@STATE_DIR@|$PLIST_DEST|g" \
    "$src" > "$out"
  echo "wrote $out"
}

render
echo "load it yourself (the repo never runs launchctl):"
echo "  launchctl bootstrap gui/\$(id -u) $DEST"
echo "  launchctl enable gui/\$(id -u)/$LABEL"
echo "the brain starts at login and restarts on failure (KeepAlive)."
