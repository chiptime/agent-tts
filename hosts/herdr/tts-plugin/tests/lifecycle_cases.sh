#!/usr/bin/env bash
# voice-stack MQ-05 — daemon lifecycle and supervision test harness
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
  HERDR_TTS_SMOKE=1 \
  HERDR_TTS_CONFIG_FILE="$SANDBOX/config/herdr-tts/config.env" \
  HERDR_TTS_VOICES_FILE="$SANDBOX/config/herdr-tts/voices.json" \
  HERDR_TTS_DAEMON_PID_FILE="$SANDBOX/daemon.pid" \
  HERDR_TTS_SUPERVISOR_STOP_FILE="$SANDBOX/state/herdr-tts/daemon-supervisor.stop" \
  HERDR_TTS_DAEMON_LOG="$SANDBOX/state/herdr-tts/daemon.log" \
  HERDR_TTS_AUDIO_RETENTION_DAYS="${HERDR_TTS_AUDIO_RETENTION_DAYS:-0}" \
  bash -c 'HOST="$1"; shift; ARGS=("$@"); set --; source "$HOST/bin/herdr-tts"; set -- "${ARGS[@]}"; '"$snippet" \
    _ "$REPO_HOST_DIR" "$@"
}

case__daemon_takeover_spares_unrelated_process() {
  sandbox_setup takeover_spares
  sleep 60 &
  local unrelated_pid=$!
  echo "$unrelated_pid" > "$SANDBOX/daemon.pid"

  in_host 'daemon_takeover' || {
    kill "$unrelated_pid" 2>/dev/null || true
    return 1
  }

  local is_alive=0
  kill -0 "$unrelated_pid" 2>/dev/null && is_alive=1
  kill "$unrelated_pid" 2>/dev/null || true

  # The unrelated process MUST have survived daemon_takeover
  [[ $is_alive -eq 1 ]] || return 1

  # The pidfile must now hold a new PID
  local new_pid
  new_pid="$(cat "$SANDBOX/daemon.pid" 2>/dev/null || true)"
  [[ -n "$new_pid" && "$new_pid" != "$unrelated_pid" ]] || return 1
}

case__daemon_stop_clean() {
  sandbox_setup stop_clean

  # 1. When no pidfile exists
  local out
  out="$(in_host 'res=""; daemon_stop_running res; echo "res=$res"')" || return 1
  [[ "$out" == "res=none" ]] || return 1

  # 2. When pidfile has a dead PID
  echo 99999999 > "$SANDBOX/daemon.pid"
  out="$(in_host 'res=""; daemon_stop_running res; echo "res=$res"')" || return 1
  [[ "$out" == "res=none" ]] || return 1

  # Supervisor stop flag was armed during deliberate stop
  [[ -f "$SANDBOX/state/herdr-tts/daemon-supervisor.stop" ]] || return 1
}

case__daemon_supervisor_respects_stop_flag() {
  sandbox_setup supervisor_flag
  local flag="$SANDBOX/state/herdr-tts/daemon-supervisor.stop"

  # 1. No flag -> should relaunch (rc 0)
  rm -f "$flag"
  in_host 'daemon_supervisor_should_relaunch 0' || return 1

  # 2. Flag present -> should NOT relaunch (rc 1)
  touch "$flag"
  local rc=0
  in_host 'daemon_supervisor_should_relaunch 0' || rc=$?
  [[ $rc -eq 1 ]] || return 1

  # Flag was consumed (one-shot)
  [[ ! -f "$flag" ]] || return 1

  # 3. Subsequent call -> back to relaunch (rc 0)
  in_host 'daemon_supervisor_should_relaunch 0' || return 1
}

case__daemon_audio_prune_throttled() {
  sandbox_setup audio_prune
  local stamp="$SANDBOX/state/herdr-tts/last-audio-prune"
  rm -f "$stamp"

  # 1. Retention active (7 days) -> first call performs prune and touches stamp
  HERDR_TTS_AUDIO_RETENTION_DAYS=7 in_host 'audio_store_prune' || return 1
  [[ -f "$stamp" ]] || return 1
  grep -q 'audio_store.prune_expired' "$STUB_LOG" || return 1
  local count1
  count1="$(grep -c 'audio_store.prune_expired' "$STUB_LOG")"
  [[ "$count1" -eq 1 ]] || return 1

  # 2. Immediate second call -> throttled by hourly gate, no new prune spawn
  HERDR_TTS_AUDIO_RETENTION_DAYS=7 in_host 'audio_store_prune' || return 1
  local count2
  count2="$(grep -c 'audio_store.prune_expired' "$STUB_LOG")"
  [[ "$count2" -eq 1 ]] || return 1

  # 3. Stamp mtime aged past 3600 seconds -> prune runs again
  touch -d '2 hours ago' "$stamp"
  HERDR_TTS_AUDIO_RETENTION_DAYS=7 in_host 'audio_store_prune' || return 1
  local count3
  count3="$(grep -c 'audio_store.prune_expired' "$STUB_LOG")"
  [[ "$count3" -eq 2 ]] || return 1
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
