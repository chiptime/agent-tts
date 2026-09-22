#!/usr/bin/env bash
# herdr-brain installer — systemd user unit + stable environment file.
#
# Idempotent: safe to re-run. On re-run it only rewrites the env file when
# the key changed (and restarts the unit in that case), and never touches a
# herdr-brain process owned by systemd.
#
# SECRETS DISCIPLINE: the GLM_API_KEY is read from ~/.dotfiles/shell/
# private-env.sh and written to ~/.config/herdr-brain/env (outside this
# repo). The value is NEVER echoed, printed or logged — not even masked
# fragments. Do not add `set -x` here.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_NAME="herdr-brain.service"
UNIT_SRC="$REPO_DIR/deploy/herdr-brain.service"
UNIT_DST="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/$UNIT_NAME"
ENV_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/herdr-brain"
ENV_FILE="$ENV_DIR/env"
PRIVATE_ENV="$HOME/.dotfiles/shell/private-env.sh"
PORT=8741
HEALTH_URL="http://127.0.0.1:$PORT/health"

log() { printf '[install] %s\n' "$*"; }
die() { printf '[install] ERROR: %s\n' "$*" >&2; exit 1; }

[[ -f "$UNIT_SRC" ]] || die "no existe $UNIT_SRC (¿ejecutaste desde el repo?)"
[[ -x "$REPO_DIR/.venv/bin/python" ]] || die "falta $REPO_DIR/.venv — corre scripts/bootstrap.sh primero"

# ---------------------------------------------------------------- env file
# Extract the GLM_API_KEY value from the dotfiles export line. Handles
# double-quoted, single-quoted and bare values; never prints it.
extract_key() {
  local line raw
  line="$(grep -E '^[[:space:]]*export[[:space:]]+GLM_API_KEY=' "$PRIVATE_ENV" 2>/dev/null | tail -n 1 || true)"
  [[ -n "$line" ]] || return 1
  raw="${line#*GLM_API_KEY=}"
  raw="${raw%\"}"; raw="${raw#\"}"   # strip double quotes
  raw="${raw%\'}"; raw="${raw#\'}"   # strip single quotes
  raw="${raw%%#*}"                    # strip trailing comment
  raw="$(printf '%s' "$raw" | tr -d '\r\n' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  [[ -n "$raw" ]] || return 1
  printf '%s' "$raw"
}

if [[ ! -f "$PRIVATE_ENV" ]]; then
  die "no encuentro $PRIVATE_ENV — clona los dotfiles primero."
fi

log "Extrayendo GLM_API_KEY de ~/.dotfiles/shell/private-env.sh…"
if ! KEY="$(extract_key)"; then
  die "GLM_API_KEY no está definido en private-env.sh (se esperaba: export GLM_API_KEY=\"…\"). Añádelo y vuelve a ejecutar."
fi
log "Clave encontrada (el valor no se muestra)."

mkdir -p "$ENV_DIR"
chmod 700 "$ENV_DIR"
env_changed=0
if [[ -f "$ENV_FILE" ]] && grep -qxF "GLM_API_KEY=$KEY" "$ENV_FILE"; then
  log "$ENV_FILE ya está al día."
else
  # Write via temp file + mv so a crashed write never leaves a partial env.
  tmp_env="$(mktemp "$ENV_DIR/env.XXXXXX")"
  trap 'rm -f "$tmp_env"' EXIT
  {
    printf '# Generado por herdr-brain/deploy/install.sh — NO subir al repo.\n'
    printf '# Regenerar: %s/deploy/install.sh\n' "$REPO_DIR"
    printf 'GLM_API_KEY=%s\n' "$KEY"
  } > "$tmp_env"
  chmod 600 "$tmp_env"
  mv -f "$tmp_env" "$ENV_FILE"
  trap - EXIT
  env_changed=1
  log "Escrito $ENV_FILE (modo 600)."
fi

# ------------------------------------------------- stop stray manual runs
# A manual `nohup` server on :8741 would block the unit's bind. Only stop
# listeners that are NOT the systemd unit's MainPID.
unit_main_pid="$(systemctl --user show -P MainPID --value "$UNIT_NAME" 2>/dev/null || true)"
[[ -n "$unit_main_pid" ]] || unit_main_pid=0
listener_pid="$(ss -tlnp 2>/dev/null | awk -v port=":$PORT " '$4 ~ port {print}' | grep -oE 'pid=[0-9]+' | head -n1 | cut -d= -f2 || true)"

if [[ -n "${listener_pid:-}" && "$listener_pid" != "0" && "$listener_pid" != "$unit_main_pid" ]]; then
  log "Deteniendo instancia manual suelta (PID $listener_pid) en el puerto $PORT…"
  kill -TERM "$listener_pid" 2>/dev/null || true
  for _ in $(seq 1 20); do
    kill -0 "$listener_pid" 2>/dev/null || break
    sleep 0.25
  done
  if kill -0 "$listener_pid" 2>/dev/null; then
    log "No respondió a TERM — enviando KILL."
    kill -KILL "$listener_pid" 2>/dev/null || true
    sleep 1
  fi
fi

# ------------------------------------------------------------ unit install
mkdir -p "$(dirname "$UNIT_DST")"
if [[ ! -f "$UNIT_DST" ]] || ! cmp -s "$UNIT_SRC" "$UNIT_DST"; then
  cp "$UNIT_SRC" "$UNIT_DST"
  log "Unidad instalada en $UNIT_DST."
else
  log "La unidad ya está instalada y al día."
fi

systemctl --user daemon-reload
loginctl enable-linger "$USER" 2>/dev/null || true   # idempotent: survives WSL reloads with no login session
systemctl --user enable --now "$UNIT_NAME" >/dev/null
# A changed key must reach the running process.
if [[ "$env_changed" == 1 ]] && systemctl --user is-active --quiet "$UNIT_NAME"; then
  log "La clave cambió — reiniciando la unidad para que la recoja."
  systemctl --user restart "$UNIT_NAME"
fi
log "Unidad habilitada (auto-arranque + linger)."

# ------------------------------------------------------------ health gate
log "Comprobando $HEALTH_URL (hasta 10s)…"
healthy=0
for _ in $(seq 1 20); do
  if curl -fsS -m 2 "$HEALTH_URL" >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 0.5
done

if [[ "$healthy" != 1 ]]; then
  log "FALLO: el servicio no respondió a /health en 10s. Últimas líneas del journal:"
  journalctl --user -u herdr-brain -n 30 --no-pager || true
  exit 1
fi

log "Salud: $(curl -fsS -m 2 "$HEALTH_URL")"
log "Listo: systemctl --user status herdr-brain"
