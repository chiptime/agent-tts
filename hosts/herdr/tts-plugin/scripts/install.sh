#!/usr/bin/env bash
# ╔═══════════════════════════════════════════════════════════╗
# ║  herdr-tts installer — curl|sh fallback (non-registry)     ║
# ╚═══════════════════════════════════════════════════════════╝
# Stages: preflight → obtain → bootstrap → keymap → daemon verify →
# uninstall print. Zero prompts; strictly no mutation before every
# preflight check passes; all git calls use the absolute TARGET via
# `git -C` (never the caller's cwd). English output only.
#
# env:  HERDR_TTS_REF (default v0.16.0) · HERDR_CONFIG_DIR ·
#       HERDR_TTS_KEYMAP_FILE · XDG_DATA_HOME / XDG_CONFIG_HOME /
#       XDG_STATE_HOME
# argv: --no-keymap
# exit: 0 installed/upgraded · 1 preflight, linked-checkout,
#       remote-mismatch or bootstrap failure
set -euo pipefail

HERDR_TTS_REF="${HERDR_TTS_REF:-v0.16.0}"
CANONICAL_URL="https://github.com/chiptime/herdr-tts.git"
DATA_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}"
TARGET="${DATA_ROOT}/herdr-tts/plugin"
KEYMAP_FILE="${HERDR_TTS_KEYMAP_FILE:-${XDG_CONFIG_HOME:-$HOME/.config}/herdr-tts/keymap.json}"
PIDFILE="${XDG_STATE_HOME:-$HOME/.local/state}/herdr-tts/daemon.pid"

NO_KEYMAP=0
for arg in "$@"; do
  case "$arg" in
    --no-keymap) NO_KEYMAP=1 ;;
    *) echo "Error: unknown option: ${arg} (this installer accepts --no-keymap)" >&2; exit 1 ;;
  esac
done

fail() { echo "Error: $*" >&2; exit 1; }
note() { echo "==> $*"; }

# ─── 1. PREFLIGHT (read-only; zero mutation) ─────────────────────────────
[[ -n "${BASH_VERSION:-}" ]] || fail "bash is required to run this installer"
for tool in git jq herdr; do
  command -v "$tool" >/dev/null 2>&1 || fail "${tool} is required but was not found in PATH"
done
if ! command -v uv >/dev/null 2>&1 && ! command -v python3 >/dev/null 2>&1; then
  fail "python3 or uv is required but neither was found in PATH"
fi

# Refuse to install over a linked dev checkout (dev → public migration:
# unlink or uninstall first). jq is already proven by the loop above.
LINKED="$(herdr plugin list --json 2>/dev/null \
  | jq -r '.result.plugins[]? | select(.plugin_id=="herdr.tts") | .source.kind' 2>/dev/null || true)"
if [[ "$LINKED" == "local" ]]; then
  fail "herdr.tts is linked to a local checkout. Run 'herdr plugin unlink herdr.tts' (or 'herdr plugin uninstall herdr.tts') first, then re-run this installer"
fi

# ─── 2. OBTAIN (fresh clone, or guarded in-place upgrade) ────────────────
MODE=fresh
if [[ -d "$TARGET" ]]; then
  CURRENT_URL="$(git -C "$TARGET" remote get-url origin 2>/dev/null || true)"
  if [[ "$CURRENT_URL" != "$CANONICAL_URL" ]]; then
    fail "refusing to touch ${TARGET}: its origin remote is '${CURRENT_URL:-none}' but this installer manages ${CANONICAL_URL}"
  fi
  MODE=upgrade
fi

mkdir -p "${DATA_ROOT}/herdr-tts"
if [[ "$MODE" == "fresh" ]]; then
  note "cloning herdr-tts ${HERDR_TTS_REF} into ${TARGET}"
  git clone --branch "$HERDR_TTS_REF" "$CANONICAL_URL" "$TARGET" \
    || fail "clone failed for ref ${HERDR_TTS_REF}"
else
  note "existing checkout at ${TARGET} matches ${CANONICAL_URL} — upgrading"
  git -C "$TARGET" fetch origin "$HERDR_TTS_REF" \
    || fail "fetch failed for ref ${HERDR_TTS_REF}"
  git -C "$TARGET" checkout "$HERDR_TTS_REF" \
    || fail "checkout failed for ref ${HERDR_TTS_REF}"
fi

# ─── 3. BOOTSTRAP (venv + pinned agent-tts via the plugin's own script) ──
if [[ "$MODE" == "upgrade" ]]; then
  note "upgrading the Python environment (agent-tts refresh)"
  HERDR_TTS_UPGRADE=1 bash "$TARGET/scripts/bootstrap.sh" \
    || fail "bootstrap failed during upgrade"
else
  note "building the Python environment"
  bash "$TARGET/scripts/bootstrap.sh" || fail "bootstrap failed"
fi

# ─── 4. KEYMAP (never overwrite; --no-keymap leaves zero artifacts) ──────
if [[ "$NO_KEYMAP" -eq 1 ]]; then
  note "--no-keymap: skipping keymap setup entirely"
elif [[ -e "$KEYMAP_FILE" ]]; then
  note "existing keymap at ${KEYMAP_FILE} — leaving it untouched"
else
  note "adopting the collision-free 'menu' keymap style"
  if bash "$TARGET/bin/herdr-tts" keymap adopt --style menu \
     && bash "$TARGET/bin/herdr-tts" keymap apply; then
    if herdr server reload-config; then
      note "herdr config reloaded with the new bindings"
    else
      echo "! 'herdr server reload-config' failed — run it manually later"
    fi
  else
    echo "! keymap setup failed — finish it later with: herdr-tts keymap init"
  fi
fi

# ─── 5. DAEMON VERIFY (warn-only; it starts with the host session) ───────
if [[ -r "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
  note "daemon is running (pid $(cat "$PIDFILE"))"
else
  echo "! daemon not detected yet — it starts with your next herdr session"
fi

# ─── 6. POST-INSTALL PRINT ───────────────────────────────────────────────
echo
echo "✓ herdr-tts ${HERDR_TTS_REF} installed at ${TARGET}"
echo "  See state any time:  herdr-tts --status"
echo
echo "To uninstall (manual steps):"
echo "  1. herdr plugin uninstall herdr.tts   (or: herdr plugin unlink for a linked checkout)"
echo "  2. stop the daemon (herdr-tts --stop, or the pid recorded in ${PIDFILE})"
echo "  3. rm -rf ${DATA_ROOT}/herdr-tts   # removes the checkout and the venv"
echo "  4. remove the managed keymap block (the lines between the herdr-tts markers) from your herdr config"
