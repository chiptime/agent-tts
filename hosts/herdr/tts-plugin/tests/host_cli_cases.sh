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

case__render_segmented_delegates_to_lib() {
  sandbox_setup segmented
  mkdir -p "$SANDBOX/outdir"
  echo "hola mundo" > "$SANDBOX/in.txt"
  XDG_DATA_HOME="$SANDBOX/data" XDG_CONFIG_HOME="$SANDBOX/config" \
  XDG_STATE_HOME="$SANDBOX/state" STUB_LOG="$STUB_LOG" \
    bash "$REPO_HOST_DIR/bin/herdr-tts" --render-text-segmented \
      "$SANDBOX/outdir" "$SANDBOX/in.txt" --speech-request-id seg-0123456789 \
      > "$SANDBOX/out" || return 1
  [[ "$(cat "$STUB_LOG")" == "$REPO_HOST_DIR/lib/segmented_render.py $SANDBOX/outdir $SANDBOX/in.txt --speech-request-id seg-0123456789" ]]
}

case__contract_capabilities_strict_json() {
  sandbox_setup capabilities
  XDG_DATA_HOME="$SANDBOX/data" XDG_CONFIG_HOME="$SANDBOX/config" \
  XDG_STATE_HOME="$SANDBOX/state" STUB_LOG="$STUB_LOG" \
    bash "$REPO_HOST_DIR/bin/herdr-tts" --contract-capabilities > "$SANDBOX/out" || return 1
  [[ "$(cat "$SANDBOX/out")" == '{"supported_protocols":[1,2]}' ]]
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

# --- VS3.2 (T7): pending-queue wiring — wrappers + the three sites ----------------
#
# The watcher-loop cases drive ONE full announcement iteration of
# watch_agent_pane: externals and heavy helpers are stubbed AFTER the source
# (bash redefinition), the loop is terminated by the herdr stub (exit 42 at
# the tail wait), and rc=42 proves the loop SURVIVED the iteration. The
# venv-python stub records every delegation argv. Nothing here reaches
# stop_audio or any real /tmp path (retention is redirected into the sandbox).

# $1 sandbox name · $2 is_playing rc (0=busy→admit) · $3 play_audio_file rc
# · $4 stub exit (0=ok, 1=refusal/failure for every python call).
run_watcher_announce() {
  sandbox_setup "$1"
  STUB_EXIT="$4" in_host '
    SB="$1"; ISP="$2"; PLAYR="$3"
    herdr_n=0
    herdr() {
      herdr_n=$((herdr_n+1))
      case "$1 $2" in
        "agent wait") [[ "$herdr_n" -eq 1 ]] && return 0 ;;
        "agent get") printf %s "{\"result\":{\"agent\":{\"agent_status\":\"done\",\"agent_session\":{\"agent\":\"opencode\",\"value\":\"sess-1\"},\"workspace_id\":\"ws-1\",\"terminal_title_stripped\":\"title-1\"}}}" && return 0 ;;
      esac
      exit 42
    }
    sleep() { :; }
    gate_agent_event() { return 1; }
    is_auto_muted() { return 1; }
    is_playing() { return "$ISP"; }
    read_pane_text() { echo "texto del turno"; }
    resolve_voice() { echo elvira; }
    spoken_prefix() { echo ""; }
    render_text_audio() { printf x > "$1"; }
    audio_retention_days() { echo 7; }
    audio_duration_secs() { echo 0; }
    play_audio_file() { return "$PLAYR"; }
    NTFY_TOPIC=""; PODCAST_ENABLED="off"; TTS_AUTO_SCOPE="all"; TTS_SETTLE_SECONDS="0"
    AGENT_TTS_AUDIO_DIR="$SB/audio"
    rc=0
    ( watch_agent_pane 3 claude ) > "$SB/out" 2> "$SB/werr" || rc=$?
    echo "rc=$rc" > "$SB/rc"
    cat "$SB/werr" >&2   # replay: the xtrace stream must reach the harness
  ' "$SANDBOX" "$2" "$3"
}

case__pending_admit_passes_event_argv() {
  sandbox_setup admit_argv
  STUB_OUT="ok=true id=0123456789abcdef state=pending" \
    in_host 'pending_admit "$@"' 3 claude done title-1 "texto del turno" /x/a.mp3 > "$SANDBOX/out" || return 1
  [[ "$(cat "$SANDBOX/out")" == "ok=true id=0123456789abcdef state=pending" ]] || return 1
  [[ "$(cat "$STUB_LOG")" == "$REPO_HOST_DIR/lib/pending_queue.py admit --pane-id 3 --agent claude --status done --label title-1 --text texto del turno --audio-path /x/a.mp3" ]]
}

case__pending_admit_omits_audio_path_when_absent() {
  sandbox_setup admit_noaudio
  STUB_OUT="ok=true id=0123456789abcdef state=pending" \
    in_host 'pending_admit "$@"' 3 claude done title-1 "texto del turno" > "$SANDBOX/out" || return 1
  [[ "$(cat "$STUB_LOG")" == "$REPO_HOST_DIR/lib/pending_queue.py admit --pane-id 3 --agent claude --status done --label title-1 --text texto del turno" ]]
}

case__pending_admit_refusal_logs_visible_line() {
  sandbox_setup admit_refused
  STUB_EXIT=1 in_host '
    rc=0
    pending_admit "$@" || rc=$?
    echo "rc=$rc"
    pending_admit "$@" || true
    echo "caller-continues"
  ' 3 claude done title-1 "texto del turno" /x/a.mp3 > "$SANDBOX/out" || return 1
  grep -q "^rc=1$" "$SANDBOX/out" || return 1                       # subcommand exit relayed
  grep -q "^caller-continues$" "$SANDBOX/out" || return 1           # the guard keeps the caller alive
  grep -qE "^\[[0-9]{2}:[0-9]{2}:[0-9]{2}\] " "$SANDBOX/out"        # visible vigente-format log
}

case__pending_tick_delegates_to_lib() {
  sandbox_setup tick_ok
  STUB_OUT="ok=true dispatched=0 placeholder=dispatch-vs3.3" \
    in_host 'pending_tick' > "$SANDBOX/out" || return 1
  [[ "$(cat "$SANDBOX/out")" == "ok=true dispatched=0 placeholder=dispatch-vs3.3" ]] || return 1
  [[ "$(cat "$STUB_LOG")" == "$REPO_HOST_DIR/lib/pending_queue.py tick" ]]
}

case__pending_tick_failure_logs_visible_line() {
  sandbox_setup tick_fail
  STUB_EXIT=1 in_host '
    rc=0
    pending_tick || rc=$?
    echo "rc=$rc"
    pending_tick || true
    echo "caller-continues"
  ' > "$SANDBOX/out" || return 1
  grep -q "^rc=1$" "$SANDBOX/out" || return 1
  grep -q "^caller-continues$" "$SANDBOX/out" || return 1
  grep -qE "^\[[0-9]{2}:[0-9]{2}:[0-9]{2}\] " "$SANDBOX/out"
}

case__pending_completion_passes_played_outcome() {
  sandbox_setup completion_played
  STUB_OUT="ok=true id=ann-0123456789 outcome=played placeholder=mapping-vs3.3" \
    in_host 'pending_completion "$1" "$2"' ann-0123456789 played > "$SANDBOX/out" || return 1
  [[ "$(cat "$STUB_LOG")" == "$REPO_HOST_DIR/lib/pending_queue.py completion --id ann-0123456789 --outcome played" ]]
}

case__pending_completion_passes_failed_outcome() {
  sandbox_setup completion_failed_arg
  STUB_OUT="ok=true id=ann-0123456789 outcome=failed placeholder=mapping-vs3.3" \
    in_host 'pending_completion "$1" "$2"' ann-0123456789 failed > "$SANDBOX/out" || return 1
  [[ "$(cat "$STUB_LOG")" == "$REPO_HOST_DIR/lib/pending_queue.py completion --id ann-0123456789 --outcome failed" ]]
}

case__pending_completion_refusal_logs_visible_line() {
  sandbox_setup completion_refused
  STUB_EXIT=1 in_host '
    rc=0
    pending_completion "$1" "$2" || rc=$?
    echo "rc=$rc"
    pending_completion "$1" "$2" || true
    echo "caller-continues"
  ' ann-0123456789 played > "$SANDBOX/out" || return 1
  grep -q "^rc=1$" "$SANDBOX/out" || return 1
  grep -q "^caller-continues$" "$SANDBOX/out" || return 1
  grep -qE "^\[[0-9]{2}:[0-9]{2}:[0-9]{2}\] " "$SANDBOX/out"
}

case__pending_admit_busy_admits_event() {
  run_watcher_announce admit_busy 0 0 0
  [[ "$(cat "$SANDBOX/rc")" == "rc=42" ]] || return 1               # loop survived the iteration
  [[ "$(head -n1 "$STUB_LOG")" == "$REPO_HOST_DIR/lib/pending_queue.py tick" ]] || return 1
  grep -q -- "--pane-id 3 --agent claude --status done" "$STUB_LOG" || return 1
  grep -q -- "--label title-1 --text texto del turno" "$STUB_LOG" || return 1
  grep -q -- "--audio-path $SANDBOX/audio/" "$STUB_LOG" || return 1  # rendered mp3 stored as recovery
  ! grep -q "completion" "$STUB_LOG"                                  # silent this run: nothing played
}

case__pending_admit_busy_refusal_continues_loop() {
  run_watcher_announce admit_busy_refused 0 0 1
  [[ "$(cat "$SANDBOX/rc")" == "rc=42" ]] || return 1               # || true guard kept the loop alive
  grep -q -- "admit --pane-id 3" "$STUB_LOG" || return 1
  [[ "$(grep -cE "^\[[0-9]{2}:[0-9]{2}:[0-9]{2}\] " "$SANDBOX/out")" -ge 2 ]]  # tick + admit failure lines
}

case__pending_completion_announce_reports_played() {
  run_watcher_announce announce_played 1 0 0
  [[ "$(cat "$SANDBOX/rc")" == "rc=42" ]] || return 1
  grep -q -- "completion --id ann-" "$STUB_LOG" || return 1
  grep -q -- "--outcome played" "$STUB_LOG" || return 1
  ! grep -q "pending_queue.py admit" "$STUB_LOG"                     # not busy: no admission this run
}

case__pending_completion_announce_reports_failed() {
  run_watcher_announce announce_failed 1 1 0
  [[ "$(cat "$SANDBOX/rc")" == "rc=42" ]] || return 1
  grep -q -- "completion --id ann-" "$STUB_LOG" || return 1
  grep -q -- "--outcome failed" "$STUB_LOG"
}

case__pending_completion_announce_refusal_continues() {
  run_watcher_announce announce_play_refused 1 0 1
  [[ "$(cat "$SANDBOX/rc")" == "rc=42" ]] || return 1
  grep -q -- "--outcome played" "$STUB_LOG" || return 1
  [[ "$(grep -cE "^\[[0-9]{2}:[0-9]{2}:[0-9]{2}\] " "$SANDBOX/out")" -ge 2 ]]  # tick + completion failure lines
}

case__pending_completion_failure_refusal_continues() {
  run_watcher_announce announce_fail_refused 1 1 1
  [[ "$(cat "$SANDBOX/rc")" == "rc=42" ]] || return 1
  grep -q -- "--outcome failed" "$STUB_LOG" || return 1
  [[ "$(grep -cE "^\[[0-9]{2}:[0-9]{2}:[0-9]{2}\] " "$SANDBOX/out")" -ge 2 ]]
}

# --- VS3.5/VS3.7/VS3.8 (T8/T7): operator CLI through the REAL python surface -------
#
# The stub harness above proves argv delegation only; the operator
# subcommands (list/status/retry/resolve/regenerate) need REAL execution
# against a REAL ledger. Convention (documented here — no earlier case
# needed the real lib):
#   * VENV_PYTHON_REAL (default: python3) runs lib/pending_queue.py
#     directly; the stubbed sandbox venv python is never used here.
#   * The ledger path is redirected by env override: DEFAULT_DB_PATH
#     expands "~/.local/state/herdr-tts/pending.db" through $HOME (NOT
#     XDG_STATE_HOME), so each case pins HOME plus the XDG dirs into its
#     hermetic sandbox — nothing outside the sandbox is read or written.
#   * Cases exercise engine-free paths only (admit/list/status/retry/
#     resolve and regenerate REFUSALS): no tick, no daemon, no renderer,
#     no network. The regenerate happy path needs a real render chain and
#     is covered by the pytest suite with injected fake renderers.
#   * Uncertain/orphan rows are seeded through the library's own API
#     (admit + mark announcing; CLI construction then recovers them,
#     VS3.7), never by hand-writing SQL.

VENV_PYTHON_REAL="${VENV_PYTHON_REAL:-python3}"

case__pending_cli_list_status_real_ledger() {
  sandbox_setup cli_list_status
  mkdir -p "$SANDBOX/home"
  local pq="$REPO_HOST_DIR/lib/pending_queue.py"
  HOME="$SANDBOX/home" "$VENV_PYTHON_REAL" "$pq" admit --pane-id 3 --agent claude \
    --status done --label title-1 --text "task done" --pane-pid 42 > "$SANDBOX/admit.out" || return 1
  grep -q "^ok=true id=" "$SANDBOX/admit.out" || return 1
  # list: header + ONE compact record line carrying the required fields.
  HOME="$SANDBOX/home" "$VENV_PYTHON_REAL" "$pq" list > "$SANDBOX/list.out" || return 1
  [[ "$(wc -l < "$SANDBOX/list.out")" -eq 2 ]] || return 1
  head -n1 "$SANDBOX/list.out" | grep -q "^ok=true count=1$" || return 1
  tail -n1 "$SANDBOX/list.out" | grep -Eq \
    "^id=[0-9a-f-]{36} pane=3 status=done state=pending repeat_count=1 first_seen=[0-9.]+$" || return 1
  # status: per-state counts, ACTIVE bound usage, ledger bytes, overflow ok.
  HOME="$SANDBOX/home" "$VENV_PYTHON_REAL" "$pq" status > "$SANDBOX/status.out" || return 1
  grep -q "^ok=true pending=1 announcing=0 announced=0 uncertain=0 expired=0 evicted=0 cancelled=0" \
    "$SANDBOX/status.out" || return 1
  grep -q " active=1/256 " "$SANDBOX/status.out" || return 1
  grep -Eq " ledger_bytes=[1-9][0-9]* " "$SANDBOX/status.out" || return 1
  grep -q "overflow=ok overflow_total=0" "$SANDBOX/status.out"
}

case__pending_cli_retry_resolve_deliberate() {
  sandbox_setup cli_retry_resolve
  mkdir -p "$SANDBOX/home"
  local pq="$REPO_HOST_DIR/lib/pending_queue.py"
  # Seed a genuinely-claimed record through the library's own API; the CLI
  # construction below then recovers the orphan claim (VS3.7 crash path).
  HOME="$SANDBOX/home" PYTHONPATH="$REPO_HOST_DIR/lib" "$VENV_PYTHON_REAL" -c '
import pending_queue as pq
q = pq.PendingQueue()
rec = q.admit({"pane_id": "3", "agent": "claude", "status": "done",
               "label": "title-1", "text": "task done", "pane_pid": 42})
assert q.mark(rec["id"], "announcing")["ok"]
print(rec["id"])
' > "$SANDBOX/seed.out" || return 1
  local rid; rid="$(cat "$SANDBOX/seed.out")"
  # First CLI construction recovers the orphan claim: uncertain, visible.
  HOME="$SANDBOX/home" "$VENV_PYTHON_REAL" "$pq" status > "$SANDBOX/status1.out" || return 1
  grep -q "uncertain=1 " "$SANDBOX/status1.out" || return 1
  grep -q "pending=0 " "$SANDBOX/status1.out" || return 1
  # retry: the deliberate re-queue, attributed to the operator.
  HOME="$SANDBOX/home" "$VENV_PYTHON_REAL" "$pq" retry "$rid" --actor night-shift \
    > "$SANDBOX/retry.out" || return 1
  grep -q "^ok=true id=$rid from=uncertain to=pending" "$SANDBOX/retry.out" || return 1
  grep -q "resolved_by=night-shift" "$SANDBOX/retry.out" || return 1
  # retry again (now pending): typed refusal, exit 1, nothing mutated.
  local rc=0
  HOME="$SANDBOX/home" "$VENV_PYTHON_REAL" "$pq" retry "$rid" 2>"$SANDBOX/retry2.err" || rc=$?
  [[ $rc -eq 1 ]] || return 1
  grep -q "retry only resolves" "$SANDBOX/retry2.err" || return 1
  # resolve to a terminal verdict over a legal edge...
  HOME="$SANDBOX/home" "$VENV_PYTHON_REAL" "$pq" resolve "$rid" expired --actor op \
    > "$SANDBOX/resolve.out" || return 1
  grep -q "to=expired" "$SANDBOX/resolve.out" || return 1
  # ...then prove terminals never reopen: typed refusal, exit 1, and the
  # aggregate counts prove nothing mutated.
  rc=0
  HOME="$SANDBOX/home" "$VENV_PYTHON_REAL" "$pq" resolve "$rid" announced \
    2>"$SANDBOX/resolve2.err" || rc=$?
  [[ $rc -eq 1 ]] || return 1
  grep -q "never reopens" "$SANDBOX/resolve2.err" || return 1
  HOME="$SANDBOX/home" "$VENV_PYTHON_REAL" "$pq" status > "$SANDBOX/status2.out" || return 1
  grep -q "expired=1 " "$SANDBOX/status2.out" || return 1
  grep -q "pending=0 " "$SANDBOX/status2.out"
}

case__pending_cli_regenerate_refusals_real() {
  sandbox_setup cli_regenerate
  mkdir -p "$SANDBOX/home"
  local pq="$REPO_HOST_DIR/lib/pending_queue.py"
  # A record whose audio is STILL on disk: regeneration is refused typed
  # and no renderer is ever contacted (no engine, no network in the harness).
  echo audio > "$SANDBOX/home/a.mp3"
  HOME="$SANDBOX/home" "$VENV_PYTHON_REAL" "$pq" admit --pane-id 3 --agent claude \
    --status done --label title-1 --text "task done" --pane-pid 42 \
    --audio-path "$SANDBOX/home/a.mp3" > "$SANDBOX/admit.out" || return 1
  local rid; rid="$(sed -n 's/^ok=true id=\([^ ]*\) .*/\1/p' "$SANDBOX/admit.out")"
  [[ -n "$rid" ]] || return 1
  local rc=0
  HOME="$SANDBOX/home" "$VENV_PYTHON_REAL" "$pq" regenerate "$rid" \
    2>"$SANDBOX/regen.err" || rc=$?
  [[ $rc -eq 1 ]] || return 1
  grep -q "audio already present" "$SANDBOX/regen.err" || return 1
  # Unknown id: typed refusal, exit 1.
  rc=0
  HOME="$SANDBOX/home" "$VENV_PYTHON_REAL" "$pq" regenerate ann-000000009 \
    2>"$SANDBOX/regen2.err" || rc=$?
  [[ $rc -eq 1 ]] || return 1
  grep -q "unknown record id" "$SANDBOX/regen2.err" || return 1
  # Ledger unchanged: one pending record, nothing resolved by the refusals.
  HOME="$SANDBOX/home" "$VENV_PYTHON_REAL" "$pq" status > "$SANDBOX/status.out" || return 1
  grep -q "pending=1 " "$SANDBOX/status.out" || return 1
  grep -q "uncertain=0 " "$SANDBOX/status.out"
}

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
