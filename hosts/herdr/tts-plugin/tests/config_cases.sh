#!/usr/bin/env bash
# voice-stack MQ-05 — config_set behavior and safety test harness
# Protocol: CASE <name> START / OK | FAIL
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_HOST_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"   # hosts/herdr/tts-plugin

SANDBOX_ROOT="$(mktemp -d)"
trap 'rm -rf "$SANDBOX_ROOT"' EXIT

sandbox_setup() {
  SANDBOX="$SANDBOX_ROOT/$1"
  mkdir -p "$SANDBOX/data/herdr-tts/venv/bin" "$SANDBOX/config/herdr-tts" "$SANDBOX/state/herdr-tts"
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
  XDG_DATA_HOME="$SANDBOX/data" XDG_CONFIG_HOME="$SANDBOX/config" \
  XDG_STATE_HOME="$SANDBOX/state" STUB_LOG="$STUB_LOG" \
  STUB_OUT="${STUB_OUT:-}" STUB_EXIT="${STUB_EXIT:-0}" \
  HERDR_TTS_CONFIG_FILE="$SANDBOX/config/herdr-tts/config.env" \
  HERDR_TTS_VOICES_FILE="$SANDBOX/config/herdr-tts/voices.json" \
  bash -c 'HOST="$1"; shift; ARGS=("$@"); set --; source "$HOST/bin/herdr-tts"; set -- "${ARGS[@]}"; '"$snippet" \
    _ "$REPO_HOST_DIR" "$@"
}

case__config_set_in_place_replace() {
  sandbox_setup inplace
  local cfg="$SANDBOX/config/herdr-tts/config.env"
  cat > "$cfg" <<'EOF'
# Header comment
CUSTOM_KEY="preserve"
TTS_PROVIDER="edge"
# Mid comment
TTS_VOICE="elvira"
# Footer comment
EOF

  in_host 'config_set TTS_VOICE "ximena"' || return 1

  # Assert TTS_VOICE="ximena" is present
  grep -q '^TTS_VOICE="ximena"$' "$cfg" || return 1
  # Assert old value is gone
  ! grep -q 'elvira' "$cfg" || return 1

  # Check exact line order
  local expected
  expected="$(printf '# Header comment\nCUSTOM_KEY="preserve"\nTTS_PROVIDER="edge"\n# Mid comment\nTTS_VOICE="ximena"\n# Footer comment')"
  [[ "$(cat "$cfg")" == "$expected" ]] || return 1
}

case__config_set_upsert_dedupe() {
  sandbox_setup dedupe
  local cfg="$SANDBOX/config/herdr-tts/config.env"
  cat > "$cfg" <<'EOF'
TTS_VOICE="v1"
OTHER_KEY="keep"
TTS_VOICE="v2"
TTS_VOICE="v3"
EOF

  in_host 'config_set TTS_VOICE "v_single"' || return 1

  # There must be exactly 1 TTS_VOICE line
  [[ "$(grep -c '^TTS_VOICE=' "$cfg")" -eq 1 ]] || return 1
  grep -q '^TTS_VOICE="v_single"$' "$cfg" || return 1
  grep -q '^OTHER_KEY="keep"$' "$cfg" || return 1
}

case__config_set_preserves_comments_and_custom_keys() {
  sandbox_setup preserves
  local cfg="$SANDBOX/config/herdr-tts/config.env"
  cat > "$cfg" <<'EOF'
# Important operator comment
UNMANAGED_VAR="secret"
# Another note
ANOTHER_CUSTOM=123
EOF

  in_host 'config_set TTS_THEME "dark"' || return 1

  grep -q '^# Important operator comment$' "$cfg" || return 1
  grep -q '^UNMANAGED_VAR="secret"$' "$cfg" || return 1
  grep -q '^# Another note$' "$cfg" || return 1
  grep -q '^ANOTHER_CUSTOM=123$' "$cfg" || return 1
  grep -q '^TTS_THEME="dark"$' "$cfg" || return 1
}

case__config_set_rejects_malicious_quotes_and_injection() {
  sandbox_setup injection
  local cfg="$SANDBOX/config/herdr-tts/config.env"
  cat > "$cfg" <<'EOF'
TTS_VOICE="safe"
EOF
  local before; before="$(cat "$cfg")"

  # Double quotes rejection
  local rc=0
  in_host 'config_set TTS_VOICE "foo\"bar"' 2>/dev/null || rc=$?
  [[ $rc -ne 0 ]] || return 1

  # Shell injection attempt with semicolon and quote
  rc=0
  in_host 'config_set TTS_VOICE "foo\"; rm -rf /tmp; echo \""' 2>/dev/null || rc=$?
  [[ $rc -ne 0 ]] || return 1

  # Value validation rejection for TTS_THEME
  rc=0
  in_host 'config_set TTS_THEME "blue"' 2>/dev/null || rc=$?
  [[ $rc -ne 0 ]] || return 1

  # Value validation rejection for TTS_READER_AUTO
  rc=0
  in_host 'config_set TTS_READER_AUTO "invalid"' 2>/dev/null || rc=$?
  [[ $rc -ne 0 ]] || return 1

  # Unmanaged key rejection
  rc=0
  in_host 'config_set DANGEROUS_KEY "val"' 2>/dev/null || rc=$?
  [[ $rc -ne 0 ]] || return 1

  # File must remain untouched
  [[ "$(cat "$cfg")" == "$before" ]] || return 1
}

case__config_set_creates_backup() {
  sandbox_setup backup
  local cfg="$SANDBOX/config/herdr-tts/config.env"
  cat > "$cfg" <<'EOF'
TTS_VOICE="initial_voice"
EOF
  [[ ! -f "${cfg}.bak" ]] || return 1

  in_host 'config_set TTS_VOICE "updated_voice"' || return 1

  [[ -f "${cfg}.bak" ]] || return 1
  grep -q '^TTS_VOICE="initial_voice"$' "${cfg}.bak" || return 1
  grep -q '^TTS_VOICE="updated_voice"$' "$cfg" || return 1
}

reminder_values_stored() {
  local key="$1" value
  shift
  sandbox_setup "$key-valid"
  local cfg="$SANDBOX/config/herdr-tts/config.env"
  for value in "$@"; do
    in_host 'config_set "$1" "$2"' "$key" "$value" || return 1
    grep -Fxq "${key}=\"${value}\"" "$cfg" || return 1
    in_host '[[ "${!1}" == "$2" ]]' "$key" "$value" || return 1
  done
}

reminder_values_refused() {
  local key="$1" value rc
  shift
  sandbox_setup "$key-invalid"
  local cfg="$SANDBOX/config/herdr-tts/config.env"
  local err="$SANDBOX/stderr"
  printf '# Preserve config bytes\nTTS_VOICE="safe"\n\n' > "$cfg"
  cp "$cfg" "$SANDBOX/before"
  printf 'Preserve backup bytes\n\n' > "${cfg}.bak"
  cp "${cfg}.bak" "$SANDBOX/backup-before"
  for value in "$@"; do
    rc=0
    in_host 'config_set "$1" "$2"' "$key" "$value" 2>"$err" || rc=$?
    [[ $rc -eq 1 && -s "$err" ]] || return 1
    cmp -s "$cfg" "$SANDBOX/before" || return 1
    cmp -s "${cfg}.bak" "$SANDBOX/backup-before" || return 1
    ! compgen -G "${cfg}.tmp.*" >/dev/null || return 1

    # Rejection must not even create a missing config directory.
    rc=0
    in_host 'CONFIG_FILE="$3"; config_set "$1" "$2"' \
      "$key" "$value" "$SANDBOX/not-created/config.env" 2>"$err" || rc=$?
    [[ $rc -eq 1 && -s "$err" && ! -e "$SANDBOX/not-created" ]] || return 1
  done
}

case__config_set_reminders_valid() {
  reminder_values_stored TTS_REMINDERS on off
}

case__config_set_reminders_invalid() {
  reminder_values_refused TTS_REMINDERS '' ON true 1 'on ' '"on"' $'on\noff'
}

case__config_set_reminder_start_min_valid() {
  reminder_values_stored TTS_REMINDER_START_MIN 1 5 08 999999999999999999999999999999
}

case__config_set_reminder_start_min_invalid() {
  reminder_values_refused TTS_REMINDER_START_MIN '' 0 000 -1 +1 1.5 1e3 abc ' 1' '1 ' '"1"' $'1\n2'
}

case__config_set_reminder_backoff_valid() {
  reminder_values_stored TTS_REMINDER_BACKOFF 1 2,5,15 01,08,15 999999999999999999999999999999,1
}

case__config_set_reminder_backoff_invalid() {
  reminder_values_refused TTS_REMINDER_BACKOFF '' 0 000 2,0,15 -1,2 +1,2 1.5,2 \
    1e3,2 abc ,2 2, 2,,5 '2, 5' ' 2,5' '2,5 ' '"2,5"' '2;5' $'2,5\n15'
}

case__config_set_reminder_max_valid() {
  reminder_values_stored TTS_REMINDER_MAX 1 3 08 999999999999999999999999999999
}

case__config_set_reminder_max_invalid() {
  reminder_values_refused TTS_REMINDER_MAX '' 0 000 -1 +1 1.5 1e3 abc ' 1' '1 ' '"1"' $'1\n2'
}

case__config_reminder_defaults() {
  sandbox_setup reminder-defaults
  in_host '[[ "$TTS_REMINDERS" == off && "$TTS_REMINDER_START_MIN" == 5 && "$TTS_REMINDER_BACKOFF" == 2,5,15 && "$TTS_REMINDER_MAX" == 3 ]]'
}

case__config_set_reminder_keys_in_help() {
  sandbox_setup reminder-help
  local rc=0 key err="$SANDBOX/stderr"
  in_host 'config_set UNKNOWN_REMINDER_KEY 1' 2>"$err" || rc=$?
  [[ $rc -eq 1 ]] || return 1
  for key in TTS_REMINDERS TTS_REMINDER_START_MIN TTS_REMINDER_BACKOFF TTS_REMINDER_MAX; do
    grep -q "$key" "$err" || return 1
  done
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
