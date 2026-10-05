#!/usr/bin/env bash
# voice-stack — named-case execution harness for the Bash decision matrix
# (scripts/voice-stack/bash_matrix.py) and the changed-lines gate.
#
# Protocol (parsed by the checkers):
#   CASE <name> START
#   CASE <name> OK | CASE <name> FAIL
#
# A case is a function named case__<name> (hyphens become underscores).
# Run all cases:      bash tests/host_cli_cases.sh
# Run a subset:       CASES="name1 name2" bash tests/host_cli_cases.sh
# Exit status is non-zero iff any executed case failed; a FAIL never stops
# the run (the matrix needs the full outcome record).
#
# Cases run inside `if`, where `set -e` is ignored: every assertion is an
# explicit `|| return 1` and the last command's status is the case verdict.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_HOST_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"   # hosts/herdr/tts-plugin

SANDBOX_ROOT="$(mktemp -d)"
trap 'rm -rf "$SANDBOX_ROOT"' EXIT

# Hermetic sandbox for bin/herdr-tts: XDG dirs live in a temp tree and the
# "venv python" is a stub that records its argv (STUB_LOG) and answers with
# STUB_OUT / STUB_EXIT. Nothing here touches the real /tmp lock, pid or
# socket, and no audio engine ever runs.
sandbox_setup() {
  SANDBOX="$SANDBOX_ROOT/$1"
  mkdir -p "$SANDBOX/data/herdr-tts/venv/bin" "$SANDBOX/config" "$SANDBOX/state"
  STUB_LOG="$SANDBOX/stub.log"
  cat > "$SANDBOX/data/herdr-tts/venv/bin/python" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STUB_LOG"
if [[ -n "${STUB_OUT:-}" ]]; then echo "$STUB_OUT"; fi
exit "${STUB_EXIT:-0}"
STUB
  chmod +x "$SANDBOX/data/herdr-tts/venv/bin/python"
}

# in_host <snippet> [args...]: runs the snippet in a fresh bash with
# bin/herdr-tts SOURCED (functions only, no dispatch) inside the sandbox.
# The args become $1.. of the snippet; they are stashed before sourcing so
# the sourced file never sees them.
in_host() {
  local snippet="$1"
  shift
  XDG_DATA_HOME="$SANDBOX/data" XDG_CONFIG_HOME="$SANDBOX/config" \
  XDG_STATE_HOME="$SANDBOX/state" STUB_LOG="$STUB_LOG" \
  STUB_OUT="${STUB_OUT:-}" STUB_EXIT="${STUB_EXIT:-0}" \
  bash -c 'HOST="$1"; shift; ARGS=("$@"); set --; source "$HOST/bin/herdr-tts"; set -- "${ARGS[@]}"; '"$snippet" \
    _ "$REPO_HOST_DIR" "$@"
}

case__host_cli_present() {
  local entry="$REPO_HOST_DIR/bin/herdr-tts"
  [[ -f "$entry" ]] || return 1
  [[ -x "$entry" ]]
}

# --- VS1.6: identified playback + targeted cancel passthrough -----------------

case__identified_playback_enqueues_with_id() {
  sandbox_setup identified_ok
  echo audio > "$SANDBOX/a.mp3"
  STUB_OUT="ok=true item=1 queue_len=0" \
    in_host 'play_audio_file "$1" "$2"' "$SANDBOX/a.mp3" ann-000000001 > "$SANDBOX/out" || return 1
  [[ "$(cat "$SANDBOX/out")" == "ok=true item=1 queue_len=0" ]] || return 1
  [[ "$(cat "$STUB_LOG")" == "$REPO_HOST_DIR/lib/pending_queue.py enqueue-file $SANDBOX/a.mp3 --id ann-000000001" ]]
}

case__identified_playback_failure_propagates() {
  sandbox_setup identified_fail
  echo audio > "$SANDBOX/a.mp3"
  local rc=0
  STUB_EXIT=1 in_host 'play_audio_file "$1" "$2"' "$SANDBOX/a.mp3" ann-000000001 2>"$SANDBOX/err" || rc=$?
  cat "$SANDBOX/err" >&2   # replay: captured stderr also carries the xtrace stream
  [[ $rc -ne 0 ]] || return 1
  [[ -s "$STUB_LOG" ]]   # the helper WAS reached: the failure is its own, not a guard's
}

case__legacy_playback_without_id_refuses_missing_file() {
  sandbox_setup legacy_missing
  local rc=0
  in_host 'play_audio_file "$1"' "$SANDBOX/missing.mp3" 2>"$SANDBOX/err" || rc=$?
  cat "$SANDBOX/err" >&2
  [[ $rc -ne 0 ]] || return 1
  [[ -s "$SANDBOX/err" ]] || return 1   # the guard's message reached stderr
  [[ ! -e "$STUB_LOG" ]]                # no engine run, no queue contact
}

case__cancel_speech_without_ids_prints_usage() {
  sandbox_setup cancel_usage
  local rc=0
  in_host 'cancel_speech' 2>"$SANDBOX/err" || rc=$?
  cat "$SANDBOX/err" >&2
  [[ $rc -eq 2 ]] || return 1
  grep -q '^usage: herdr-tts --cancel-speech' "$SANDBOX/err" || return 1
  [[ ! -e "$STUB_LOG" ]]
}

case__cancel_speech_passes_each_id() {
  sandbox_setup cancel_ids
  STUB_OUT="ok=true removed=1 active_stopped=0" \
    in_host 'cancel_speech "$@"' ann-000000001 ann-000000002 > "$SANDBOX/out" || return 1
  [[ "$(cat "$SANDBOX/out")" == "ok=true removed=1 active_stopped=0" ]] || return 1
  [[ "$(cat "$STUB_LOG")" == "$REPO_HOST_DIR/lib/pending_queue.py cancel --id ann-000000001 --id ann-000000002" ]]
}

case__cancel_speech_flag_dispatches_through_cli() {
  sandbox_setup cancel_cli
  XDG_DATA_HOME="$SANDBOX/data" XDG_CONFIG_HOME="$SANDBOX/config" \
  XDG_STATE_HOME="$SANDBOX/state" STUB_LOG="$STUB_LOG" \
  STUB_OUT="ok=true removed=0 active_stopped=0" \
    bash "$REPO_HOST_DIR/bin/herdr-tts" --cancel-speech ann-000000001 > "$SANDBOX/out" || return 1
  [[ "$(cat "$SANDBOX/out")" == "ok=true removed=0 active_stopped=0" ]] || return 1
  [[ "$(cat "$STUB_LOG")" == "$REPO_HOST_DIR/lib/pending_queue.py cancel --id ann-000000001" ]]
}

# --- MQ-02/MQ-05: machine-global playback state is env-overridable ------------
# Initialization inspection ONLY: the defaults and the override plumbing are
# asserted right after sourcing. Nothing here reads, writes or kills live
# /tmp player state — runtime isolation is proven by the smoke suite.

case__playback_state_paths_default_to_machine_global() {
  sandbox_setup trio_defaults
  in_host 'printf "L=%s|P=%s|S=%s\n" "$LOCK_FILE" "$PID_FILE" "$IPC_SOCKET"' \
    > "$SANDBOX/out" || return 1
  [[ "$(cat "$SANDBOX/out")" == \
     "L=/tmp/herdr-tts-playing.lock|P=/tmp/herdr-tts-current.pid|S=/tmp/herdr-tts-player.sock" ]]
}

case__playback_state_paths_env_override_wins() {
  sandbox_setup trio_override
  AGENT_TTS_LOCK_FILE="$SANDBOX/custom.lock" \
  AGENT_TTS_PID_FILE="$SANDBOX/custom.pid" \
  AGENT_TTS_SOCKET="$SANDBOX/custom.sock" \
    in_host 'printf "L=%s|P=%s|S=%s\n" "$LOCK_FILE" "$PID_FILE" "$IPC_SOCKET"' \
    > "$SANDBOX/out" || return 1
  [[ "$(cat "$SANDBOX/out")" == \
     "L=$SANDBOX/custom.lock|P=$SANDBOX/custom.pid|S=$SANDBOX/custom.sock" ]]
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
