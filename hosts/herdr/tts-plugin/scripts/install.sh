#!/usr/bin/env bash
# ╔═════════════════════════════════════════════════════════════════════╗
# ║  herdr-tts installer — curl|sh fallback (non-registry, monorepo)    ║
# ╚═════════════════════════════════════════════════════════════════════╝
# Source: the agent-tts monorepo (github.com/chiptime/agent-tts). The
# checkout placed at TARGET is the WHOLE monorepo; the plugin lives at
# hosts/herdr/tts-plugin inside it (PLUGIN_ROOT below).
# Stages: preflight → obtain → bootstrap → expose CLI → keymap → daemon
# verify → uninstall print. Zero prompts; strictly no mutation before every
# preflight check passes; all git calls use the absolute TARGET via
# `git -C` (never the caller's cwd — even when that cwd is a foreign git
# repo). English output only.
#
# env:  HERDR_TTS_REF (default v0.16.0, a monorepo tag) · HERDR_CONFIG_DIR ·
#       HERDR_TTS_KEYMAP_FILE · XDG_DATA_HOME / XDG_CONFIG_HOME /
#       XDG_STATE_HOME
# argv: --no-keymap
# CLI:  bin/herdr-tts is exposed as ~/.local/bin/herdr-tts (a link into the
#       managed checkout; directory created when missing, PATH gap warned
#       about, an unmanaged herdr-tts or a non-directory ~/.local/bin is
#       refused in preflight, a managed link is refreshed on every re-run).
# exit: 0 installed/upgraded · 1 preflight, linked-checkout,
#       remote-mismatch, relative-target, CLI-exposure or bootstrap failure
set -euo pipefail

HERDR_TTS_REF="${HERDR_TTS_REF:-v0.16.0}"
CANONICAL_URL="https://github.com/chiptime/agent-tts.git"
DATA_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}"
TARGET="${DATA_ROOT}/herdr-tts/plugin"
PLUGIN_SUBDIR="hosts/herdr/tts-plugin"
PLUGIN_ROOT="${TARGET}/${PLUGIN_SUBDIR}"
KEYMAP_FILE="${HERDR_TTS_KEYMAP_FILE:-${XDG_CONFIG_HOME:-$HOME/.config}/herdr-tts/keymap.json}"
PIDFILE="${XDG_STATE_HOME:-$HOME/.local/state}/herdr-tts/daemon.pid"
CONFIG_ROOT="${XDG_CONFIG_HOME:-$HOME/.config}"
FIRST_RUN_MARKER="${CONFIG_ROOT}/herdr-tts/first-run.done"
BIN_DIR="${HOME}/.local/bin"
BIN_LINK="${BIN_DIR}/herdr-tts"

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
# A relative DATA_ROOT would make every subsequent git -C / clone run
# cwd-relative — refuse it before anything is touched.
[[ "$TARGET" == /* ]] || \
  fail "refusing a relative install target ('${TARGET}'): XDG_DATA_HOME ('${DATA_ROOT}') must resolve to an absolute path"
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

# CLI exposure preflight: the exposed artifact is only ever created or
# replaced when it is ours — a symlink into the managed checkout (this
# layout or the pre-monorepo one). Anything else is the user's and is
# refused here, before any mutation.
if [[ -e "$BIN_DIR" && ! -d "$BIN_DIR" ]]; then
  fail "${BIN_DIR} exists but is not a directory, so herdr-tts cannot be exposed there. Move it aside (or remove it), then re-run this installer"
fi
if [[ -e "$BIN_LINK" || -L "$BIN_LINK" ]]; then
  LINK_TARGET=""
  [[ -L "$BIN_LINK" ]] && LINK_TARGET="$(readlink "$BIN_LINK" 2>/dev/null || true)"
  if [[ "$LINK_TARGET" != "${DATA_ROOT}/herdr-tts/"* ]]; then
    fail "${BIN_LINK} already exists and is not managed by this installer (it does not link into ${DATA_ROOT}/herdr-tts). Move or remove it, then re-run this installer"
  fi
fi

# ─── 2. OBTAIN (fresh monorepo clone, or guarded in-place upgrade) ───────
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
  note "cloning the agent-tts monorepo (${HERDR_TTS_REF}) into ${TARGET}"
  git clone --branch "$HERDR_TTS_REF" "$CANONICAL_URL" "$TARGET" \
    || fail "clone failed for ref ${HERDR_TTS_REF}"
else
  note "existing monorepo checkout at ${TARGET} matches ${CANONICAL_URL} — upgrading"
  git -C "$TARGET" fetch origin "$HERDR_TTS_REF" \
    || fail "fetch failed for ref ${HERDR_TTS_REF}"
  git -C "$TARGET" checkout "$HERDR_TTS_REF" \
    || fail "checkout failed for ref ${HERDR_TTS_REF}"
fi

# ─── 3. BOOTSTRAP (venv + pinned agent-tts via the plugin's own script) ──
if [[ "$MODE" == "upgrade" ]]; then
  note "upgrading the Python environment (agent-tts refresh)"
  HERDR_TTS_UPGRADE=1 bash "${PLUGIN_ROOT}/scripts/bootstrap.sh" \
    || fail "bootstrap failed during upgrade"
else
  note "building the Python environment"
  bash "${PLUGIN_ROOT}/scripts/bootstrap.sh" || fail "bootstrap failed"
fi

# ─── 4. EXPOSE THE CLI (~/.local/bin/herdr-tts → the checkout's launcher) ─
# The launcher resolves its own root through the link (readlink -f), so no
# wrapper script or manual symlink step is ever needed. The link is
# refreshed on every run so it always tracks the upgraded checkout.
mkdir -p "$BIN_DIR"
ln -sfn "${PLUGIN_ROOT}/bin/herdr-tts" "$BIN_LINK"
note "exposed herdr-tts at ${BIN_LINK}"
ON_PATH=0
IFS=':' read -r -a PATH_ENTRIES <<< "${PATH:-}"
for entry in "${PATH_ENTRIES[@]}"; do
  [[ "${entry%/}" == "${BIN_DIR}" ]] && ON_PATH=1
done
if [[ "$ON_PATH" -eq 0 ]]; then
  echo "! ${BIN_DIR} is not on your PATH, so 'herdr-tts' will not be found by name."
  echo "  Add it to your shell profile:  export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

# ─── 5. KEYMAP (never overwrite; --no-keymap leaves zero artifacts) ──────
if [[ "$NO_KEYMAP" -eq 1 ]]; then
  note "--no-keymap: skipping keymap setup entirely"
elif [[ -e "$KEYMAP_FILE" ]]; then
  note "existing keymap at ${KEYMAP_FILE} — leaving it untouched"
else
  note "adopting the collision-free 'menu' keymap style"
  if bash "${PLUGIN_ROOT}/bin/herdr-tts" keymap adopt --style menu \
     && bash "${PLUGIN_ROOT}/bin/herdr-tts" keymap apply; then
    if herdr server reload-config; then
      note "herdr config reloaded with the new bindings"
    else
      echo "! 'herdr server reload-config' failed — run it manually later"
    fi
  else
    echo "! keymap setup failed — finish it later with: herdr-tts keymap init"
  fi
fi

# ─── 6. DAEMON VERIFY (warn-only; it starts with the host session) ───────
if [[ -r "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
  note "daemon is running (pid $(cat "$PIDFILE"))"
else
  echo "! daemon not detected yet — it starts with your next herdr session"
fi

# ─── 7. POST-INSTALL PRINT ───────────────────────────────────────────────
echo
echo "✓ herdr-tts ${HERDR_TTS_REF} installed at ${PLUGIN_ROOT}"
echo "  (agent-tts monorepo checkout: ${TARGET})"
echo "  See state any time:  herdr-tts --status"
echo
echo "To uninstall (manual steps):"
echo "  1. herdr plugin uninstall herdr.tts   (or: herdr plugin unlink for a linked checkout)"
echo "  2. stop the daemon (herdr-tts --stop, or the pid recorded in ${PIDFILE})"
echo "  3. rm -rf ${DATA_ROOT}/herdr-tts   # removes the monorepo checkout and the venv"
echo "  4. remove the managed keymap block (the lines between the herdr-tts markers) from your herdr config"
echo "  5. rm ${BIN_LINK}   # the managed CLI artifact"
echo "  6. first-run completion marker: ${FIRST_RUN_MARKER}"
echo "     (delete it to run onboarding again after a reinstall; keep it to skip)"
