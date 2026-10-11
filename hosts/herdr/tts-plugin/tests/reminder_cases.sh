#!/usr/bin/env bash
# HT-05 sweep/delivery contract. CASE protocol; no engine or network needed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_HOST_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SANDBOX_ROOT="$(mktemp -d)"
trap 'rm -rf "$SANDBOX_ROOT"' EXIT

sandbox_setup() {
  SANDBOX="$SANDBOX_ROOT/$1"
  mkdir -p "$SANDBOX/bin" "$SANDBOX/data/herdr-tts/venv/bin" \
    "$SANDBOX/config" "$SANDBOX/state" "$SANDBOX/home" "$SANDBOX/tmp"
  printf '1000\n' > "$SANDBOX/now"
  printf 'other\n' > "$SANDBOX/focus"
  printf '%s\n' '{"result":{"agents":[{"pane_id":"pane:7","agent":"claude","agent_status":"done"}]}}' > "$SANDBOX/agents.json"
  cat > "$SANDBOX/data/herdr-tts/venv/bin/python" <<'STUB'
#!/usr/bin/env bash
if [[ "$1 ${2:-}" == '-m agent_tts.stt.cli' ]]; then
  printf '%s\n' "$*" >> "$SB/stt.calls"
  case "$3" in
    capture) exit 0 ;;
    transcribe) printf '%s\n' '{"ok":true,"text":"answer supplied"}'; exit 0 ;;
  esac
  exit 97
fi
if [[ "$1" == */pending_queue.py ]]; then
  printf '%s\n' "$*" >> "$SB/queue.calls"
  if [[ "${CORE_FAIL:-0}" == 1 ]]; then
    echo 'forced reminder failure' >&2
    exit 1
  fi
  if [[ "${CORE_BAD_JSON:-0}" == 1 ]]; then
    printf '%s\n' 'not-json'
    exit 0
  fi
  if [[ -s "$SB/core.jsonl" ]]; then cat "$SB/core.jsonl"; exit 0; fi
  # Stdlib-only Python deliberately cannot import the engine: reminders
  # must still fire with the core's fail-closed, snippet-free text.
  exec python3 -I -S "$@"
fi
if [[ "$1" == */tts_engine.py ]]; then
  printf '%s\n' "$2" >> "$SB/voice.calls"
  printf '%s\n' "$*" >> "$SB/engine.calls"
  exit 0
fi
echo 'unexpected python delegation' >&2
exit 97
STUB
  cat > "$SANDBOX/bin/herdr" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$SB/herdr.calls"
case "$1 $2" in
  'agent list') cat "$SB/agents.json" ;;
  'api snapshot') jq -n --arg pane "$(cat "$SB/focus")" '{result:{snapshot:{focused_pane_id:$pane}}}' ;;
  'pane send-text') exit "${INJECT_FAIL:-0}" ;;
  'pane send-keys'|'notification show') exit 0 ;;
  *) exit 97 ;;
esac
STUB
  chmod +x "$SANDBOX/data/herdr-tts/venv/bin/python" "$SANDBOX/bin/herdr"
}

in_host() {
  local snippet="$1"
  SB="$SANDBOX" HOME="$SANDBOX/home" TMPDIR="$SANDBOX/tmp" \
  PATH="$SANDBOX/bin:$PATH" PYTHONPATH="" PYTHONDONTWRITEBYTECODE=1 \
  XDG_DATA_HOME="$SANDBOX/data" XDG_CONFIG_HOME="$SANDBOX/config" \
  XDG_STATE_HOME="$SANDBOX/state" XDG_CACHE_HOME="$SANDBOX/cache" \
  HERDR_PLUGIN_ROOT="$REPO_HOST_DIR" \
  HERDR_TTS_CONFIG_FILE="$SANDBOX/config/config.env" \
  HERDR_TTS_SNOOZE_FILE="$SANDBOX/snooze.json" \
  HERDR_TTS_DAEMON_PID_FILE="$SANDBOX/daemon.pid" \
  HERDR_TTS_DAEMON_LOG="$SANDBOX/daemon.log" \
  HERDR_TTS_SUPERVISOR_STOP_FILE="$SANDBOX/supervisor.stop" \
  HERDR_TTS_LOCK_FILE="$SANDBOX/audio.lock" HERDR_TTS_PID_FILE="$SANDBOX/audio.pid" \
  HERDR_TTS_IPC_SOCKET="$SANDBOX/audio.sock" \
  bash -c '
    source "$1/bin/herdr-tts"
    date() {
      if [[ "$*" == "+%s" ]]; then cat "$SB/now"; else command date "$@"; fi
    }
    # Only stop/scratch seams are replaced; real speak_text reaches the
    # pinned engine stub without touching global /tmp audio/log files.
    stop_audio() { printf "stop\n" >> "$SB/stop.calls"; }
    mktemp() {
      case "${1:-}" in
        /tmp/herdr-tts-err.XXXXXX) command mktemp "$SB/tmp/voice-err.XXXXXX" ;;
        "$SB"/*) command mktemp "$@" ;;
        *) return 97 ;;
      esac
    }
    is_playing() { [[ "${BUSY:-0}" == 1 ]]; }
    curl() {
      printf "%s\n" curl >> "$SB/push.count"
      printf "[%s]" "$@" >> "$SB/push.calls"
      printf "\n" >> "$SB/push.calls"
    }
    TTS_AUTO_SCOPE=all; TTS_REMINDERS=on; TTS_REMINDER_START_MIN=1
    TTS_REMINDER_BACKOFF="1,1"; TTS_REMINDER_MAX=3; TTS_DEBOUNCE_SECONDS=20
    TTS_READER_AUTO=off; NTFY_TOPIC=reminders; NTFY_SERVER=https://ntfy.invalid
    NTFY_APPROVAL_URL=""; NTFY_APPROVAL_TOKEN=""; WEB_URL=""
    sweep() { daemon_reminders "$(cat "$SB/agents.json")"; wait; }
    now() { printf "%s\n" "$1" > "$SB/now"; }
    focus() { printf "%s\n" "$1" > "$SB/focus"; }
    expect_state() {
      python3 -I -S - "$HOME/.local/state/herdr-tts/pending.db" "$1" "$2" <<PY
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as conn:
    row = conn.execute("SELECT reminder_count FROM reminder_state WHERE pane_id = ?", (sys.argv[2],)).fetchone()
actual = row[0] if row else -1
assert actual == int(sys.argv[3]), (sys.argv[2], actual, sys.argv[3])
PY
    }
    '"$snippet" _ "$REPO_HOST_DIR"
}

case__reminders_off_and_unset_inert() {
  sandbox_setup off
  in_host 'TTS_REMINDERS=off; sweep; unset TTS_REMINDERS; sweep' || return 1
  [[ ! -e "$SANDBOX/queue.calls" && ! -e "$SANDBOX/voice.calls" \
     && ! -e "$SANDBOX/push.calls" && ! -e "$SANDBOX/snooze.json" ]]
}

case__reminder_due_speaks_once() {
  sandbox_setup due
  in_host 'sweep; now 1059; sweep; now 1060; sweep; now 1064; sweep' || return 1
  [[ "$(<"$SANDBOX/voice.calls")" == 'Recordatorio: el chat sigue esperando' ]] || return 1
  [[ "$(wc -l < "$SANDBOX/voice.calls")" == 1 ]] || return 1
  [[ ! -e "$SANDBOX/push.calls" ]] || return 1
  grep -q -- 'reminders --now 1000 --start-min 1 --backoff 1,1 --max 3 --pane pane:7:done' "$SANDBOX/queue.calls" || return 1
  grep -q -- 'reminders --now 1064.*--suppressed pane:7' "$SANDBOX/queue.calls" || return 1
  ! grep -qE -- ' --agent | --session-id | --tldr|pending_queue.py admit' "$SANDBOX/engine.calls" "$SANDBOX/queue.calls"
}

case__reminder_final_high_push_and_debounce_progression() {
  sandbox_setup final
  in_host 'sweep; now 1060; sweep; now 1064; sweep; now 1120; sweep;
    now 1124; sweep; now 1180; sweep; now 1184; sweep; now 1300; sweep' || return 1
  [[ "$(wc -l < "$SANDBOX/voice.calls")" == 3 ]] || return 1
  [[ "$(wc -l < "$SANDBOX/push.count")" == 1 ]] || return 1
  grep -qF '[Priority: high]' "$SANDBOX/push.calls" || return 1
  grep -qF '[-d][Recordatorio: el chat sigue esperando]' "$SANDBOX/push.calls" || return 1
  [[ "$(grep -c -- '--suppressed pane:7' "$SANDBOX/queue.calls")" == 3 ]] || return 1
  # Query through the same engine-free pinned CLI: reminders never admit.
  in_host '"$VENV_PYTHON" "$PLUGIN_ROOT/lib/pending_queue.py" status' > "$SANDBOX/status" || return 1
  grep -q 'active=0/256' "$SANDBOX/status"
}

case__reminder_candidate_probe_does_not_refresh_debounce() {
  sandbox_setup probe
  in_host 'sweep; now 1004; sweep; [[ ! -e "$SNOOZE_STATE_FILE" ]]' || return 1
  [[ ! -e "$SANDBOX/voice.calls" && ! -e "$SANDBOX/push.calls" ]]
}

case__reminder_jsonl_text_delivered_verbatim() {
  sandbox_setup verbatim
  cat > "$SANDBOX/core.jsonl" <<'JSONL'
not-json
{"pane_id":"unknown","index":3,"text":"must not speak"}
{"pane_id":"pane:7","index":4,"text":"over max"}
{"pane_id":"pane:7","index":3,"text":"Reminder: choose \"A\" or B.\nNext step: $(printf unsafe) `id`"}
JSONL
  in_host 'sweep' || return 1
  [[ "$(<"$SANDBOX/voice.calls")" == $'Reminder: choose "A" or B.\nNext step: $(printf unsafe) `id`' ]] || return 1
  [[ "$(wc -l < "$SANDBOX/push.count")" == 1 ]] || return 1
  grep -qF '[Priority: high]' "$SANDBOX/push.calls" || return 1
  grep -qF 'Next step: $(printf unsafe) `id`' "$SANDBOX/push.calls"
}

case__reminder_simultaneous_due_does_not_overlap() {
  sandbox_setup simultaneous
  printf '%s\n' '{"result":{"agents":[{"pane_id":"pane:7","agent_status":"done"},{"pane_id":"pane:8","agent_status":"blocked"}]}}' > "$SANDBOX/agents.json"
  in_host '
    # The first real speak_text publishes its busy lock. Hold that busy
    # observation for this sweep so a second due item cannot preempt it.
    is_playing() { [[ -s "$LOCK_FILE" ]]; }
    sweep; now 1060; sweep
  ' || return 1
  [[ "$(wc -l < "$SANDBOX/voice.calls")" == 1 ]] || return 1
  [[ "$(wc -l < "$SANDBOX/stop.calls")" == 1 ]] || return 1
  [[ "$(wc -l < "$SANDBOX/push.count")" == 1 ]] || return 1
  grep -qF '[Priority: high]' "$SANDBOX/push.calls"
}

case__reminder_mute_and_snooze_excluded() {
  sandbox_setup gates
  in_host '
    raw="{\"result\":{\"agents\":[
      {\"pane_id\":\"muted\",\"agent_status\":\"done\"},
      {\"pane_id\":\"snoozed\",\"agent_status\":\"blocked\"},
      {\"pane_id\":\"held\",\"agent_status\":\"blocked\"},
      {\"pane_id\":\"working\",\"agent_status\":\"working\"}]}}"
    save_snooze_state "{\"panes\":{\"muted\":{\"muted\":true},\"snoozed\":{\"snooze_until\":2000}},\"debounce\":{\"held\":{\"blocked\":995}}}"
    daemon_reminders "$raw"
    save_snooze_state "{\"global_snooze_until\":2000}"
    sweep
  ' || return 1
  grep -q -- '--suppressed held' "$SANDBOX/queue.calls" || return 1
  ! grep -q -- '--pane ' "$SANDBOX/queue.calls" || return 1
  [[ ! -e "$SANDBOX/voice.calls" && ! -e "$SANDBOX/push.calls" ]]
}

case__reminder_local_gates_push_without_interrupting_audio() {
  local mode
  for mode in busy auto_muted background; do
    sandbox_setup "$mode"
    in_host 'sweep; now 1060
      case "'"$mode"'" in
        busy) BUSY=1 ;;
        auto_muted) touch "$AUTO_MUTE_FILE" ;;
        background) TTS_AUTO_SCOPE=focused; get_focused_pane() { echo other; } ;;
      esac
      sweep' || return 1
    [[ ! -e "$SANDBOX/voice.calls" && ! -e "$SANDBOX/stop.calls" ]] || return 1
    [[ "$(wc -l < "$SANDBOX/push.count")" == 1 ]] || return 1
    grep -qF '[-d][Recordatorio: el chat sigue esperando]' "$SANDBOX/push.calls" || return 1
  done
}

case__reminder_core_failure_and_bad_json_fail_open() {
  sandbox_setup failures
  in_host 'export CORE_FAIL=1; sweep; export CORE_FAIL=0 CORE_BAD_JSON=1; sweep;
    echo normal-audio-continues' > "$SANDBOX/out" || return 1
  grep -q normal-audio-continues "$SANDBOX/out" || return 1
  [[ ! -e "$SANDBOX/voice.calls" && ! -e "$SANDBOX/push.calls" ]]
}

case__reminder_sweep_wiring_and_failure_survives() {
  sandbox_setup wiring
  in_host '
    # Contain startup, watcher discovery and daemon cleanup. The real
    # sweep body still exercises prune -> title -> reminder -> retention.
    daemon_takeover() { :; }; daemon_keymap_autostart() { :; }; show_status() { :; }
    trap() { :; }
    prune_snooze_state() { echo prune >> "$SB/order"; }
    daemon_title_sync() { echo title >> "$SB/order"; }
    audio_store_prune() { echo retention >> "$SB/order"; }
    jq() {
      if [[ "$*" == *"\\(.pane_id)\\t\\(.agent)"* ]]; then cat >/dev/null; return 0; fi
      command jq "$@"
    }
    sleep() { exit 42; }
    export CORE_FAIL=1
    rc=0; ( run_daemon ) || rc=$?
    [[ "$rc" == 42 ]]
  ' || return 1
  [[ "$(<"$SANDBOX/order")" == $'prune\ntitle\nretention' ]] || return 1
  grep -q -- 'pending_queue.py reminders ' "$SANDBOX/queue.calls" || return 1
  [[ "$(<"$SANDBOX/herdr.calls")" == $'agent list\napi snapshot' ]]
}

case__reminder_disabled_sweep_never_calls_core() {
  sandbox_setup disabled_sweep
  in_host '
    daemon_takeover() { :; }; daemon_keymap_autostart() { :; }; show_status() { :; }
    trap() { :; }; prune_snooze_state() { :; }; daemon_title_sync() { :; }
    audio_store_prune() { :; }
    daemon_reminders() { echo unexpected >> "$SB/reminder.calls"; return 97; }
    jq() {
      if [[ "$*" == *"\\(.pane_id)\\t\\(.agent)"* ]]; then cat >/dev/null; return 0; fi
      command jq "$@"
    }
    sleep() { exit 42; }
    for setting in off unset; do
      TTS_REMINDERS=off
      [[ "$setting" == unset ]] && unset TTS_REMINDERS
      rc=0; ( run_daemon ) || rc=$?
      [[ "$rc" == 42 ]] || exit 1
    done
  ' || return 1
  [[ ! -e "$SANDBOX/reminder.calls" && ! -e "$SANDBOX/queue.calls" \
     && ! -e "$SANDBOX/voice.calls" && ! -e "$SANDBOX/push.calls" ]]
}

case__reminder_focus_over_two_seconds_resets_before_due() {
  sandbox_setup focus_reset
  in_host 'sweep; expect_state pane:7 0
    now 1057; focus pane:7; sweep; expect_state pane:7 0
    now 1059; sweep; expect_state pane:7 0
    now 1060; sweep; expect_state pane:7 -1
    now 1200; sweep; expect_state pane:7 -1' || return 1
  [[ ! -e "$SANDBOX/voice.calls" && ! -e "$SANDBOX/push.calls" ]] || return 1
  ! grep -q -- 'reminder-reset' "$SANDBOX/queue.calls"
}

case__reminder_brief_focus_does_not_reset() {
  sandbox_setup brief_focus
  in_host 'sweep; now 1058; focus pane:7; sweep; expect_state pane:7 0
    now 1059; sweep; expect_state pane:7 0
    now 1060; focus other; sweep; expect_state pane:7 1' || return 1
  [[ "$(wc -l < "$SANDBOX/voice.calls")" == 1 && ! -e "$SANDBOX/push.calls" ]]
}

case__reminder_focus_switch_restarts_timer() {
  sandbox_setup focus_switch
  in_host 'sweep; now 1057; focus pane:7; sweep
    now 1058; focus other; sweep; now 1059; focus pane:7; sweep
    now 1060; sweep; expect_state pane:7 1' || return 1
  [[ "$(wc -l < "$SANDBOX/voice.calls")" == 1 ]]
}

case__reminder_focus_resets_debounce_suppressed_pane() {
  sandbox_setup focus_suppressed
  in_host 'sweep; now 1060; sweep; expect_state pane:7 1
    now 1061; focus pane:7; sweep; expect_state pane:7 1
    now 1064; sweep; expect_state pane:7 -1' || return 1
  [[ "$(wc -l < "$SANDBOX/voice.calls")" == 1 && ! -e "$SANDBOX/push.calls" ]] || return 1
  grep -q -- 'reminders --now 1061.*--suppressed pane:7' "$SANDBOX/queue.calls" || return 1
  grep -q -- 'reminders --now 1064 --start-min 1 --backoff 1,1 --max 3$' "$SANDBOX/queue.calls"
}

case__reminder_ptt_injection_resets() {
  sandbox_setup ptt_reset
  in_host 'sweep; now 1060; sweep; expect_state pane:7 1
    focus pane:7; TTS_PTT=on; TTS_STT=on; TTS_PTT_ENTER=always
    printf "\n" | run_ptt; expect_state pane:7 -1
    now 1064; sweep; expect_state pane:7 -1' || return 1
  grep -q -- 'pane send-text pane:7 answer supplied' "$SANDBOX/herdr.calls" || return 1
  grep -q -- 'pane send-keys pane:7 Enter' "$SANDBOX/herdr.calls" || return 1
  [[ "$(grep -c -- 'reminder-reset --pane pane:7' "$SANDBOX/queue.calls")" == 1 ]] || return 1
  [[ "$(wc -l < "$SANDBOX/voice.calls")" == 1 && ! -e "$SANDBOX/push.calls" ]]
}

case__reminder_ptt_reset_is_pane_specific() {
  sandbox_setup ptt_target
  in_host '"$VENV_PYTHON" "$PLUGIN_ROOT/lib/pending_queue.py" reminders \
      --now 1000 --start-min 1 --backoff 1,1 --max 3 --pane pane:7:done --pane pane:8:blocked
    focus pane:7; TTS_PTT=on; TTS_STT=on; TTS_PTT_ENTER=never
    printf "\n" | run_ptt
    expect_state pane:7 -1; expect_state pane:8 0' || return 1
  [[ ! -e "$SANDBOX/voice.calls" && ! -e "$SANDBOX/push.calls" ]] || return 1
  grep -q -- 'reminder-reset --pane pane:7' "$SANDBOX/queue.calls"
}

case__reminder_ptt_reset_failure_is_fail_open() {
  sandbox_setup ptt_reset_fail
  in_host 'sweep; focus pane:7; TTS_PTT=on; TTS_STT=on; TTS_PTT_ENTER=always
    export CORE_FAIL=1
    printf "\n" | run_ptt; expect_state pane:7 0' || return 1
  grep -q -- 'pane send-keys pane:7 Enter' "$SANDBOX/herdr.calls" || return 1
  grep -q -- 'reminders: reset failed for pane pane:7' "$SANDBOX/daemon.log"
}

case__reminder_ptt_failure_does_not_reset() {
  sandbox_setup ptt_fail
  in_host 'sweep; focus pane:7; TTS_PTT=on; TTS_STT=on; TTS_PTT_ENTER=always
    export INJECT_FAIL=1
    rc=0; printf "\n" | run_ptt || rc=$?
    [[ "$rc" == 1 ]] || exit 1
    expect_state pane:7 0' || return 1
  ! grep -q -- 'reminder-reset' "$SANDBOX/queue.calls" || return 1
  ! grep -q -- 'pane send-keys' "$SANDBOX/herdr.calls"
}

case__reminder_ptt_off_is_inert() {
  sandbox_setup ptt_off
  in_host 'sweep; focus pane:7; TTS_PTT=on; TTS_STT=on; TTS_PTT_ENTER=never
    TTS_REMINDERS=off; printf "\n" | run_ptt; expect_state pane:7 0' || return 1
  ! grep -q -- 'reminder-reset' "$SANDBOX/queue.calls"
}

case__reminder_mute_and_snooze_suppress_and_reset() {
  local mode
  for mode in mute pane_snooze global_snooze; do
    sandbox_setup "reset_$mode"
    in_host 'sweep; expect_state pane:7 0; now 1060
      case "'"$mode"'" in
        mute) toggle_pane_mute pane:7 ;;
        pane_snooze) cycle_snooze pane pane:7 ;;
        global_snooze) cycle_snooze global ;;
      esac
      sweep; expect_state pane:7 -1; now 1120; sweep; expect_state pane:7 -1' || return 1
    [[ ! -e "$SANDBOX/voice.calls" && ! -e "$SANDBOX/push.calls" ]] || return 1
    ! grep -q -- 'reminder-reset' "$SANDBOX/queue.calls" || return 1
  done
}

case__reminder_working_state_resets() {
  sandbox_setup working_reset
  in_host 'sweep; now 1060
    printf "%s\n" "{\"result\":{\"agents\":[{\"pane_id\":\"pane:7\",\"agent_status\":\"working\"}]}}" > "$SB/agents.json"
    sweep; expect_state pane:7 -1
    now 1120; sweep; expect_state pane:7 -1' || return 1
  [[ ! -e "$SANDBOX/voice.calls" && ! -e "$SANDBOX/push.calls" ]] || return 1
  ! grep -q -- 'reminder-reset' "$SANDBOX/queue.calls"
}

main() {
  local all="" fn name fail=0
  while IFS= read -r fn; do all+=" ${fn#case__}"; done \
    < <(declare -F | awk '{print $3}' | grep '^case__' | sort)
  for name in ${CASES:-$all}; do
    echo "CASE $name START"
    if "case__$name"; then echo "CASE $name OK"; else echo "CASE $name FAIL"; fail=1; fi
  done
  exit "$fail"
}
main "$@"
