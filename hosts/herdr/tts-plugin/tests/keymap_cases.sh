#!/usr/bin/env bash
# voice-stack MQ-05 — keymap validation, apply, and rollback test harness
# Protocol: CASE <name> START / OK | FAIL
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_HOST_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"   # hosts/herdr/tts-plugin

SANDBOX_ROOT="$(mktemp -d)"
trap 'rm -rf "$SANDBOX_ROOT"' EXIT

sandbox_setup() {
  SANDBOX="$SANDBOX_ROOT/$1"
  mkdir -p "$SANDBOX/bin" "$SANDBOX/data/herdr-tts/venv/bin" "$SANDBOX/config/herdr-tts" "$SANDBOX/state/herdr-tts"
  STUB_LOG="$SANDBOX/stub.log"
  cat > "$SANDBOX/data/herdr-tts/venv/bin/python" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STUB_LOG"
if [[ -n "${STUB_OUT:-}" ]]; then echo "$STUB_OUT"; fi
exit "${STUB_EXIT:-0}"
STUB
  chmod +x "$SANDBOX/data/herdr-tts/venv/bin/python"
}

in_host() {
  local snippet="$1"
  shift
  PATH="$SANDBOX/bin:$PATH" \
  XDG_DATA_HOME="$SANDBOX/data" XDG_CONFIG_HOME="$SANDBOX/config" \
  XDG_STATE_HOME="$SANDBOX/state" STUB_LOG="$STUB_LOG" \
  STUB_OUT="${STUB_OUT:-}" STUB_EXIT="${STUB_EXIT:-0}" \
  HERDR_TTS_SMOKE=1 \
  HERDR_TTS_CONFIG_FILE="$SANDBOX/config/herdr-tts/config.env" \
  HERDR_TTS_KEYMAP_FILE="$SANDBOX/config/herdr-tts/keymap.json" \
  HERDR_TTS_VOICES_FILE="$SANDBOX/config/herdr-tts/voices.json" \
  HERDR_TTS_DAEMON_PID_FILE="$SANDBOX/daemon.pid" \
  HERDR_TTS_SUPERVISOR_STOP_FILE="$SANDBOX/state/herdr-tts/daemon-supervisor.stop" \
  HERDR_TTS_DAEMON_LOG="$SANDBOX/state/herdr-tts/daemon.log" \
  bash -c 'HOST="$1"; shift; ARGS=("$@"); set --; source "$HOST/bin/herdr-tts"; set -- "${ARGS[@]}"; '"$snippet" \
    _ "$REPO_HOST_DIR" "$@"
}

case__keymap_check_shadowing_detection() {
  sandbox_setup shadow
  local km="$SANDBOX/config/herdr-tts/keymap.json"
  cat > "$km" <<'EOF'
{
  "style": "direct",
  "bindings": {
    "play": "prefix+z"
  }
}
EOF

  # 1. Human check output must report core shadow warning
  in_host 'keymap_check' > "$SANDBOX/out" 2>&1 || return 1
  grep -q 'SHADOWS CORE (zoom pane): play = prefix+z' "$SANDBOX/out" || return 1
  grep -q '1 shadow warning(s)' "$SANDBOX/out" || return 1

  # 2. JSON check output must indicate ok=true, warning_count=1, and status=warn
  in_host 'keymap_check --json' > "$SANDBOX/json.out" || return 1
  jq -e '.ok == true and .warning_count == 1 and .error_count == 0' "$SANDBOX/json.out" >/dev/null || return 1
  jq -e '.bindings[0].status == "warn" and .bindings[0].core == "zoom pane"' "$SANDBOX/json.out" >/dev/null || return 1
}

case__keymap_apply_managed_block_and_backup() {
  sandbox_setup apply_backup
  local km="$SANDBOX/config/herdr-tts/keymap.json"
  cat > "$km" <<'EOF'
{
  "style": "direct",
  "bindings": {
    "play": "prefix+p",
    "stop": "prefix+s"
  }
}
EOF

  local target="$SANDBOX/config.toml"
  cat > "$target" <<'EOF'
# User custom configuration
[general]
theme = "nord"
EOF
  local orig_content
  orig_content="$(cat "$target")"

  # Stub herdr binary to approve config check
  cat > "$SANDBOX/bin/herdr" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  chmod +x "$SANDBOX/bin/herdr"

  in_host 'keymap_apply "$1"' "$target" > "$SANDBOX/apply.out" || return 1

  # Managed block markers present
  grep -q '>>> herdr-tts keymap (managed; edits inside are overwritten) >>>' "$target" || return 1
  grep -q '<<< herdr-tts keymap <<<' "$target" || return 1

  # Original configuration content preserved
  grep -q '# User custom configuration' "$target" || return 1
  grep -q 'theme = "nord"' "$target" || return 1

  # Backup file was created with original content
  local bak
  bak="$(ls "$target".bak-* 2>/dev/null | head -1 || true)"
  [[ -n "$bak" && -f "$bak" ]] || return 1
  [[ "$(cat "$bak")" == "$orig_content" ]] || return 1
}

case__keymap_apply_rollback_on_failure() {
  sandbox_setup apply_rollback
  local km="$SANDBOX/config/herdr-tts/keymap.json"
  cat > "$km" <<'EOF'
{
  "style": "direct",
  "bindings": {
    "play": "prefix+p"
  }
}
EOF

  local target="$SANDBOX/config.toml"
  cat > "$target" <<'EOF'
# Clean configuration before rollback
safety_flag = true
EOF
  local orig_content
  orig_content="$(cat "$target")"

  # Stub herdr binary to FAIL on config check
  cat > "$SANDBOX/bin/herdr" <<'EOF'
#!/usr/bin/env bash
if [[ "$*" == *"config check"* ]]; then
  echo "Error: syntax error in config" >&2
  exit 1
fi
exit 0
EOF
  chmod +x "$SANDBOX/bin/herdr"

  local rc=0
  in_host 'keymap_apply "$1"' "$target" > "$SANDBOX/apply.out" 2> "$SANDBOX/apply.err" || rc=$?
  [[ $rc -eq 1 ]] || return 1

  # Content must be rolled back to exact original content
  [[ "$(cat "$target")" == "$orig_content" ]] || return 1

  # Managed block markers must NOT linger in target
  ! grep -q 'herdr-tts keymap' "$target" || return 1
}

main() {
  local all=""
  local fn
  while IFS= read -r fn; do
    all="$all ${fn#case__}"
  done < <(declare -F | awk '{print $3}' | grep '^case__' | sort)

  local wanted="${CASES:-$all}"
  local fail=0
  local name
  for name in $wanted; do
    echo "CASE $name START"
    if "case__$name"; then
      echo "CASE $name OK"
    else
      echo "CASE $name FAIL"
      fail=1
    fi
  done
  exit $fail
}

main "$@"
