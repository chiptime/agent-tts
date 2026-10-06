#!/usr/bin/env bash
# Critical launcher regressions: same named CASE protocol and CASES filter as
# host_cli_cases.sh. Every launcher process gets a fresh, entirely sandboxed
# environment; engine, herdr and tmux are stubs. No real daemon is dispatched.
#
# Existing behavior is expected GREEN. RED_PROOFS=1 runs six syntax-valid
# mutations in disposable launcher copies, checks an observed CASE FAIL for
# each, then runs the identical case against the unmodified launcher.
# HERDR_TTS_TEST_LAUNCHER selects a copy without changing product dispatch.
# Snippets expand in the sandbox child; mutation needles are literal source.
# shellcheck disable=SC2016
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_HOST_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LAUNCHER="${HERDR_TTS_TEST_LAUNCHER:-$REPO_HOST_DIR/bin/herdr-tts}"
SANDBOX_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/herdr-tts-critical.XXXXXX")"
# Only this invocation's private mktemp root is eligible for cleanup.
trap 'rm -rf "$SANDBOX_ROOT"' EXIT

sandbox_setup() {
  SANDBOX="$SANDBOX_ROOT/$1"
  mkdir -p "$SANDBOX"/{home,config,state,cache,run,tmp,bin} \
    "$SANDBOX/data/herdr-tts/venv/bin" "$SANDBOX/config/herdr" || return 1
  STUB_LOG="$SANDBOX/stub.log"
  local tool
  for tool in python herdr tmux; do
    cat > "$SANDBOX/bin/$tool" <<'STUB' || return 1
#!/usr/bin/env bash
printf '%s %s\n' "${0##*/}" "$*" >> "$STUB_LOG"
[[ "${0##*/}" == python ]] && exit 0
[[ "${0##*/}" == herdr && "$*" == 'config check' ]] && exit 0
echo "Unexpected external call: ${0##*/} $*" >&2
exit 97
STUB
    chmod +x "$SANDBOX/bin/$tool" || return 1
  done
  cp "$SANDBOX/bin/python" "$SANDBOX/data/herdr-tts/venv/bin/python"
}

host_env() {
  env -i PATH="$SANDBOX/bin:/usr/bin:/bin" LANG=C.UTF-8 TZ=UTC \
    HOME="$SANDBOX/home" XDG_CONFIG_HOME="$SANDBOX/config" \
    XDG_DATA_HOME="$SANDBOX/data" XDG_STATE_HOME="$SANDBOX/state" \
    XDG_CACHE_HOME="$SANDBOX/cache" XDG_RUNTIME_DIR="$SANDBOX/run" \
    TMPDIR="$SANDBOX/tmp" PYTHONDONTWRITEBYTECODE=1 \
    HERDR_PLUGIN_ROOT="$REPO_HOST_DIR" HERDR_TTS_LANG=en \
    HERDR_TTS_CONFIG_FILE="$SANDBOX/config/herdr-tts/config.env" \
    HERDR_CONFIG_DIR="$SANDBOX/config" \
    HERDR_TTS_KEYMAP_FILE="$SANDBOX/keymap.json" \
    HERDR_TTS_SNOOZE_FILE="$SANDBOX/snooze.json" \
    HERDR_TTS_VOICES_FILE="$SANDBOX/voices.json" \
    HERDR_TTS_DAEMON_PID_FILE="$SANDBOX/daemon.pid" \
    HERDR_TTS_DAEMON_LOG="$SANDBOX/daemon.log" \
    HERDR_TTS_SUPERVISOR_STOP_FILE="$SANDBOX/supervisor.stop" \
    HERDR_TTS_SUPERVISOR_BACKOFF=0 HERDR_TTS_SCRIPT="$SANDBOX/never-run" \
    HERDR_TTS_LOCK_FILE="$SANDBOX/play.lock" \
    HERDR_TTS_PID_FILE="$SANDBOX/play.pid" HERDR_TTS_IPC_SOCKET="$SANDBOX/play.sock" \
    HERDR_TTS_HISTORY_FILE="$SANDBOX/history.log" HERDR_TTS_SMOKE=1 \
    TTS_DEBOUNCE_SECONDS=3600 STUB_LOG="$STUB_LOG" SB="$SANDBOX" \
    ${SHELLOPTS:+SHELLOPTS="$SHELLOPTS"} ${PS4:+PS4="$PS4"} "$@"
}

# Stash snippet arguments before sourcing, exactly as in host_cli_cases.sh.
# Safety envelope: a hard kill-after bounds a stuck shell. Cleanup signals ONLY
# explicitly registered identities — direct children (register_owned) or
# fixture descendants that announced themselves while provably alive
# (register_handshake_owned) — and every signal re-validates pid + /proc
# starttime AT the boundary. No /proc tree discovery, no host sweeps. Platform
# limit, acknowledged: between the /proc read and kill(2) there remains an
# inherent TOCTOU window that pid-based signalling (without pidfd) cannot
# close; f3-* fixture names and short self-limiting lifetimes bound exposure.
# finish_owned never signals: a lifecycle oracle fails loudly if the product
# operation left a survivor — rescue cleanup can never mask it.
in_host() {
  local snippet="$1" rc=0 err
  shift
  host_env timeout --kill-after=5 15 bash -c '
    HOST="$1"; shift; ARGS=("$@"); set --
    source "$HOST"
    set -- "${ARGS[@]}"
    for resolved in "$SNOOZE_STATE_FILE" "$DAEMON_PID_FILE" "$DAEMON_LOG" \
      "$SUPERVISOR_STOP_FLAG" "$VOICE_MAP_FILE" "$KEYMAP_FILE" "$CONFIG_FILE" \
      "$HERDR_CONFIG_DIR" "$LOCK_FILE" "$PID_FILE" "$IPC_SOCKET" "$HISTORY_FILE" \
      "$HOME" "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_STATE_HOME" \
      "$XDG_CACHE_HOME" "$XDG_RUNTIME_DIR" "$TMPDIR"; do
      [[ "$resolved" == "$SB/"* ]] || { echo "Uncontained launcher path: $resolved" >&2; exit 97; }
    done
    OWNED_PIDS=()
    declare -A OWNED_BIRTH=()
    declare -A OWNED_RC=()
    proc_snapshot() { # /proc/<pid>/stat → PROC_STATE / PROC_PARENT / PROC_BIRTH
      local stat_line
      local -a fields=()
      PROC_STATE=""; PROC_PARENT=""; PROC_BIRTH=""
      [[ "$1" =~ ^[1-9][0-9]*$ ]] || return 1
      { IFS= read -r stat_line < "/proc/$1/stat"; } 2>/dev/null || return 1
      read -r -a fields <<< "${stat_line##*) }"
      [[ ${#fields[@]} -ge 20 ]] || return 1
      PROC_STATE=${fields[0]}; PROC_PARENT=${fields[1]}; PROC_BIRTH=${fields[19]}
    }
    register_owned() { # only direct children of this shell are registrable
      local pid="$1"
      if ! proc_snapshot "$pid"; then
        wait "$pid" 2>/dev/null || true
        return 1
      fi
      [[ "$PROC_PARENT" == "$BASHPID" ]] || return 1
      OWNED_PIDS+=("$pid"); OWNED_BIRTH[$pid]=$PROC_BIRTH
    }
    register_handshake_owned() { # <file>: "pid birth", written by our fixture
      local pid birth
      { read -r pid birth; } < "$1" 2>/dev/null || return 1
      [[ "$pid" =~ ^[1-9][0-9]*$ && "$birth" =~ ^[0-9]+$ ]] || return 1
      proc_snapshot "$pid" || return 0   # already exited: nothing to own or reap
      if [[ "$PROC_BIRTH" != "$birth" ]]; then
        echo "handshake identity stale (pid $pid reused); refusing to register" >&2
        return 1
      fi
      OWNED_PIDS+=("$pid"); OWNED_BIRTH[$pid]=$birth
    }
    owned_live() { # same pid AND same start time AND not a dead zombie
      proc_snapshot "$1" && [[ "$PROC_BIRTH" == "$2" && "$PROC_STATE" != Z && "$PROC_STATE" != X ]]
    }
    wait_owned_gone() { # bounded ~0.5s; no signaling here
      local i
      for i in {1..50}; do
        owned_live "$1" "$2" || return 0
        command sleep 0.01
      done
      return 1
    }
    signal_owned() { # <pid> <birth> <sig>: identity revalidated AT the boundary
      if ! proc_snapshot "$1"; then
        printf "signal refused: pid %s gone before %s\n" "$1" "$3" >> "$SB/signal.log"
        return 0
      fi
      if [[ "$PROC_BIRTH" != "$2" || "$PROC_STATE" == Z || "$PROC_STATE" == X ]]; then
        printf "signal refused: pid %s state=%s birth=%s expected=%s before %s\n" \
          "$1" "$PROC_STATE" "$PROC_BIRTH" "$2" "$3" >> "$SB/signal.log"
        return 0
      fi
      builtin kill -"$3" "$1" 2>/dev/null || true
    }
    stop_owned() { # <pid> <birth>: TERM, bounded wait, KILL escalation
      signal_owned "$1" "$2" TERM
      wait_owned_gone "$1" "$2" || true
      owned_live "$1" "$2" || return 0
      signal_owned "$1" "$2" KILL
      wait_owned_gone "$1" "$2" || return 1
      return 0
    }
    cleanup_owned() { # TERM ALL registered identities, then a bounded KILL
      # phase, then reap. KILL walks the registry in REVERSE: fixtures register
      # dependents after their spawning parent, so a parent blocked in
      # wait(child) is freed by the child death first and can exit on its
      # own TERM handling instead of being KILL-escalated out from under the
      # exit-code oracle of the case.
      local pid fail=0 idx
      for pid in "${OWNED_PIDS[@]}"; do
        signal_owned "$pid" "${OWNED_BIRTH[$pid]}" TERM
      done
      for (( idx = ${#OWNED_PIDS[@]} - 1; idx >= 0; idx-- )); do
        pid=${OWNED_PIDS[idx]}
        wait_owned_gone "$pid" "${OWNED_BIRTH[$pid]}" || true
        owned_live "$pid" "${OWNED_BIRTH[$pid]}" || continue
        signal_owned "$pid" "${OWNED_BIRTH[$pid]}" KILL
        wait_owned_gone "$pid" "${OWNED_BIRTH[$pid]}" || fail=1
      done
      for pid in "${OWNED_PIDS[@]}"; do
        finish_owned "$pid" || fail=1
      done
      return "$fail"
    }
    trap cleanup_owned EXIT
    finish_owned() { # reaps a process someone ELSE should have ended; signals nothing
      local pid="$1" i rc=0
      local -a keep=()
      [[ -n "${OWNED_BIRTH[$pid]:-}" ]] || return 1
      wait_owned_gone "$pid" "${OWNED_BIRTH[$pid]}" || return 1
      wait "$pid" 2>/dev/null || rc=$?
      OWNED_RC[$pid]=$rc
      # Rebuild compactly: unsetting by index would leave sparse gaps that the
      # reverse-index cleanup loop cannot walk under set -u.
      for i in "${!OWNED_PIDS[@]}"; do
        [[ "${OWNED_PIDS[i]}" != "$pid" ]] && keep+=("${OWNED_PIDS[i]}")
      done
      OWNED_PIDS=()
      (( ${#keep[@]} )) && OWNED_PIDS=("${keep[@]}")
      unset "OWNED_BIRTH[$pid]"
    }
    start_dummy() {
      bash -c '\''exec -a "$1" sleep 60'\'' _ "$1" &
      DUMMY_PID=$!; register_owned "$DUMMY_PID" || return 1
      local i comm
      for i in {1..200}; do
        if IFS= read -r comm < "/proc/$DUMMY_PID/comm" && [[ "$comm" == sleep ]]; then
          return 0
        fi
        command sleep 0.01
      done
      return 1
    }
    notify_herdr() { printf "%s\n" "$*" >> "$SB/notify.out"; }
    get_focused_pane() { printf "a\n"; }
  '"$snippet" _ "$LAUNCHER" "$@" || rc=$?
  # Captured diagnostics also contain xtrace when the changed-lines gate runs.
  for err in "$SANDBOX"/*.err; do
    [[ ! -f "$err" ]] || cat "$err" >&2
  done
  return "$rc"
}

in_cli() { host_env timeout --kill-after=5 15 bash "$LAUNCHER" "$@"; }

gate_check() { # expected rc (0=gated, 1=allowed), pane, status
  local expected="$1" rc=0
  shift
  in_cli --gate-check "$@" > "$SANDBOX/gate.out" || rc=$?
  [[ "$rc" -eq "$expected" ]] || {
    printf 'Expected gate rc=%s; observed rc=%s\n' "$expected" "$rc" >&2
    cat "$SANDBOX/gate.out" >&2
    return 1
  }
}

# --- Gating: exercise the real diagnostic dispatch, not a watcher stub. ------
case__critical_gate_mute() {
  printf '%s\n' '{"panes":{"a":{"muted":true}}}' > "$SANDBOX/snooze.json" || return 1
  cp "$SANDBOX/snooze.json" "$SANDBOX/before.json" || return 1
  gate_check 0 a 'done' || return 1
  grep -q 'muted' "$SANDBOX/gate.out" || return 1
  cmp -s "$SANDBOX/before.json" "$SANDBOX/snooze.json"
}

case__critical_gate_pane_snooze() {
  printf '%s\n' '{"panes":{"a":{"snooze_until":4102444800}}}' > "$SANDBOX/snooze.json" || return 1
  gate_check 0 a 'done' || return 1
  jq -e 'has("debounce") | not' "$SANDBOX/snooze.json" >/dev/null
}

case__critical_gate_expired_snooze() {
  printf '%s\n' '{"panes":{"a":{"snooze_until":1}},"global_snooze_until":1}' > "$SANDBOX/snooze.json" || return 1
  gate_check 1 a 'done' || return 1
  jq -e '.debounce.a.done > 1' "$SANDBOX/snooze.json" >/dev/null
}

case__critical_gate_global_snooze() {
  printf '%s\n' '{"global_snooze_until":4102444800}' > "$SANDBOX/snooze.json" || return 1
  gate_check 0 a 'done' || return 1
  gate_check 0 b blocked || return 1
  jq -e 'has("debounce") | not' "$SANDBOX/snooze.json" >/dev/null
}

case__critical_gate_debounce_window() {
  local now
  now="$(date +%s)"
  printf '{"debounce":{"a":{"done":%s}}}\n' "$now" > "$SANDBOX/snooze.json" || return 1
  cp "$SANDBOX/snooze.json" "$SANDBOX/before.json" || return 1
  gate_check 0 a 'done' || return 1
  cmp -s "$SANDBOX/before.json" "$SANDBOX/snooze.json" || return 1
  printf '{"debounce":{"a":{"done":%s}}}\n' "$((now - 86400))" > "$SANDBOX/snooze.json" || return 1
  gate_check 1 a 'done'
}

case__critical_gate_first_allow_records() {
  local before after
  before="$(date +%s)"
  gate_check 1 a blocked || return 1
  after="$(date +%s)"
  jq -e --argjson low "$((before - 60))" --argjson high "$((after + 60))" \
    '.debounce.a.blocked >= $low and .debounce.a.blocked <= $high
     and (.debounce.a | keys) == ["blocked"]' "$SANDBOX/snooze.json" >/dev/null || return 1
  ! compgen -G "$SANDBOX/snooze.json.*" >/dev/null
}

case__critical_gate_missing_corrupt_fail_open() {
  in_host '[[ "$(load_snooze_state)" == "{}" ]]' || return 1
  gate_check 1 a 'done' || return 1
  printf 'invalid JSON {\n' > "$SANDBOX/snooze.json" || return 1
  in_host '[[ "$(load_snooze_state)" == "{}" ]]' || return 1
  gate_check 1 a 'done' || return 1
  jq -e '.debounce.a.done > 0' "$SANDBOX/snooze.json" >/dev/null
}

case__critical_gate_status_pane_independence() {
  gate_check 1 a blocked || return 1
  gate_check 1 a 'done' || return 1
  gate_check 1 b 'done' || return 1
  gate_check 0 a blocked || return 1
  gate_check 0 a 'done' || return 1
  jq -e '(.debounce.a | keys) == ["blocked","done"] and .debounce.b.done > 0' \
    "$SANDBOX/snooze.json" >/dev/null
}

case__critical_gate_write_failure_allows() {
  in_host '
    SNOOZE_STATE_FILE="$SB/missing-parent/snooze.json"
    rc=0; gate_agent_event a done || rc=$?
    [[ $rc -eq 1 && ! -e "$SNOOZE_STATE_FILE" ]] || exit 1
    echo caller-continues
  '
}

# --- Mute/snooze toggles: stub notification/focus after sourcing. ------------
case__critical_mute_focus_roundtrip() {
  in_host '
    printf "%s\n" "{\"panes\":{\"a\":{\"snooze_until\":123},\"b\":{\"muted\":true}},\"global_snooze_until\":456}" > "$SNOOZE_STATE_FILE"
    toggle_pane_mute > "$SB/mute.out" || exit 1
    jq -e ".panes.a.muted == true and .panes.b.muted == true and .panes.a.snooze_until == 123" "$SNOOZE_STATE_FILE" >/dev/null || exit 1
    toggle_pane_mute >> "$SB/mute.out" || exit 1
    jq -e ".panes.a.muted == false and .panes.b.muted == true and .global_snooze_until == 456" "$SNOOZE_STATE_FILE" >/dev/null || exit 1
    [[ $(wc -l < "$SB/notify.out") -eq 2 ]]
  '
}

case__critical_mute_explicit_and_no_focus() {
  in_host '
    get_focused_pane() { echo unexpected-focus >&2; return 99; }
    toggle_pane_mute b > "$SB/mute.out" || exit 1
    jq -e ".panes.b.muted == true and (.panes | has(\"a\") | not)" "$SNOOZE_STATE_FILE" >/dev/null || exit 1
    cp "$SNOOZE_STATE_FILE" "$SB/before.json"
    get_focused_pane() { :; }
    rc=0; toggle_pane_mute > "$SB/mute.out" 2> "$SB/mute.err" || rc=$?
    [[ $rc -eq 1 ]] || exit 1
    cmp -s "$SB/before.json" "$SNOOZE_STATE_FILE"
  '
}

cycle_sequence() {
  in_host '
    scope="$1"; path="$2"
    printf "%s\n" "{\"panes\":{\"a\":{\"muted\":true}},\"debounce\":{\"b\":{\"done\":123}}}" > "$SNOOZE_STATE_FILE"
    for spec in 1:300 2:1800 3:7200 0:0; do
      stage=${spec%:*}; duration=${spec#*:}; before=$(date +%s)
      cycle_snooze "$scope" > "$SB/cycle.out" || exit 1
      after=$(date +%s)
      got_stage=$(jq -r "$path.snooze_stage // .global_snooze_stage" "$SNOOZE_STATE_FILE")
      until=$(jq -r "$path.snooze_until // .global_snooze_until" "$SNOOZE_STATE_FILE")
      [[ "$got_stage" -eq "$stage" ]] || exit 1
      if (( stage )); then
        (( until >= before + duration - 60 && until <= after + duration + 60 )) || exit 1
      else
        [[ "$until" -eq 0 ]] || exit 1
      fi
      jq -e ".panes.a.muted == true and .debounce.b.done == 123" "$SNOOZE_STATE_FILE" >/dev/null || exit 1
    done
    [[ $(wc -l < "$SB/notify.out") -eq 4 ]]
  ' "$1" "$2"
}

case__critical_snooze_pane_cycle() { cycle_sequence pane '.panes.a'; }
case__critical_snooze_global_cycle() { cycle_sequence global ''; }

case__critical_snooze_expired_and_no_focus() {
  in_host '
    printf "%s\n" "{\"panes\":{\"b\":{\"snooze_stage\":3,\"snooze_until\":1}},\"global_snooze_stage\":3,\"global_snooze_until\":1}" > "$SNOOZE_STATE_FILE"
    get_focused_pane() { echo unexpected-focus >&2; return 99; }
    cycle_snooze pane b > "$SB/cycle.out" || exit 1
    cycle_snooze global >> "$SB/cycle.out" || exit 1
    jq -e ".panes.b.snooze_stage == 1 and .global_snooze_stage == 1" "$SNOOZE_STATE_FILE" >/dev/null || exit 1
    cp "$SNOOZE_STATE_FILE" "$SB/before.json"
    get_focused_pane() { :; }
    rc=0; cycle_snooze pane > "$SB/cycle.out" 2> "$SB/cycle.err" || rc=$?
    [[ $rc -eq 1 ]] || exit 1
    cmp -s "$SB/before.json" "$SNOOZE_STATE_FILE"
  '
}

# --- Voice identity: cache, precedence, fail-open, catalog rotation, CLI. ----
case__critical_voice_precedence() {
  printf '%s\n' '{"pane":{"a":"pane-voice"},"agent":{"claude":"agent-voice"}}' > "$SANDBOX/voices.json" || return 1
  in_host '
    [[ "$(resolve_voice a claude)" == pane-voice ]] || exit 1
    [[ "$(resolve_voice b claude)" == agent-voice ]] || exit 1
    [[ "$(resolve_voice b other)" == elvira ]]
  '
}

case__critical_voice_corrupt_logs_once() {
  printf 'not JSON\n' > "$SANDBOX/voices.json" || return 1
  in_host '
    resolve_voice a claude > "$SB/voice.out" || exit 1
    resolve_voice b claude >> "$SB/voice.out" || exit 1
    [[ $(cat "$SB/voice.out") == $'"'"'elvira\nelvira'"'"' ]] || exit 1
    [[ $(wc -l < "$DAEMON_LOG") -eq 1 ]] || exit 1
    grep -q "invalid voices.json" "$DAEMON_LOG"
  '
}

case__critical_voice_auto_assign_and_fallback() {
  printf '%s\n' '{"auto_assign":true}' > "$SANDBOX/voices.json" || return 1
  in_host '
    ENGINE_CAT_PROVIDERS="edge openai"
    ENGINE_CAT_VOICES[edge]="alpha beta gamma delta"
    [[ "$(resolve_voice pane-1 a)" == beta ]] || exit 1
    [[ "$(resolve_voice pane-2 a)" == beta ]] || exit 1
    [[ "$(resolve_voice pane-1 b)" == gamma ]] || exit 1
    TTS_PROVIDER=piper
    [[ "$(resolve_voice pane-1 a)" == elvira ]] || exit 1
    TTS_PROVIDER=edge; ENGINE_CAT_PROVIDERS=""; ENGINE_CAT_VOICES[edge]=""
    [[ "$(resolve_voice pane-1 a)" == elvira ]]
  '
}

case__critical_voice_rule_set_and_cli_off() {
  in_host '
    voice_map_set pane a pane-voice || exit 1
    voice_map_set agent claude agent-voice || exit 1
    [[ "$(resolve_voice a claude)" == pane-voice ]]
  ' || return 1
  in_cli --voice-for pane a off > "$SANDBOX/voice.out" || return 1
  jq -e '(.pane | has("a") | not) and .agent.claude == "agent-voice"' \
    "$SANDBOX/voices.json" >/dev/null || return 1
  in_host '[[ "$(resolve_voice a claude)" == agent-voice ]]' || return 1
  local rc=0
  in_cli --voice-for invalid a off > "$SANDBOX/voice.out" 2> "$SANDBOX/voice.err" || rc=$?
  cat "$SANDBOX/voice.err" >&2
  [[ $rc -eq 1 ]]
}

case__critical_voice_prefix_flag() {
  in_cli --voice-prefix on > "$SANDBOX/prefix.out" || return 1
  jq -e '.prefix == true' "$SANDBOX/voices.json" >/dev/null || return 1
  in_host '
    [[ "$(spoken_prefix claude)" == "claude: " ]] || exit 1
    rc=0; spoken_prefix "" > "$SB/prefix.out" || rc=$?
    [[ $rc -eq 1 && ! -s "$SB/prefix.out" ]]
  ' || return 1
  in_cli --voice-prefix off > "$SANDBOX/prefix.out" || return 1
  in_host '
    rc=0; spoken_prefix claude > "$SB/prefix.out" || rc=$?
    [[ $rc -eq 1 && ! -s "$SB/prefix.out" ]]
  '
}

case__critical_voice_refresh_on_mtime() {
  in_host '
    printf "%s\n" "{\"pane\":{\"a\":\"first\"}}" > "$VOICE_MAP_FILE"
    touch -d @1000000000 "$VOICE_MAP_FILE"
    resolve_voice a > "$SB/first.out" || exit 1
    printf "%s\n" "{\"pane\":{\"a\":\"second\"}}" > "$VOICE_MAP_FILE"
    touch -d @1000000100 "$VOICE_MAP_FILE"
    resolve_voice a > "$SB/second.out" || exit 1
    [[ $(cat "$SB/first.out") == first && $(cat "$SB/second.out") == second ]] || exit 1
    VOICE_MAP_FILE="$SB/absent-voices.json"
    [[ "$(resolve_voice a)" == elvira ]]
  '
}

# --- Keymap writes and rollback: no real Herdr config is ever consulted. -----
keymap_fixture() {
  printf '%s\n' '{"style":"direct","bindings":{"play":"ctrl+alt+r","tldr":"ctrl+alt+l"}}' > "$SANDBOX/keymap.json" || return 1
  printf '# user settings\ntheme = "dark"\n' > "$SANDBOX/config/herdr/config.toml"
}

case__critical_keymap_preserves_user_bytes() {
  keymap_fixture || return 1
  in_host '
    cfg="$HERDR_CONFIG_DIR/herdr/config.toml"
    pre=$'"'"'# user ✓\ntheme = "dark"\n\n'"'"'
    post=$'"'"'\n\n# footer\n[font]\nsize = 11.0\n\n'"'"'
    printf "%s%s\nstale managed content\n%s%s" "$pre" "$KEYMAP_BLOCK_START" "$KEYMAP_BLOCK_END" "$post" > "$cfg"
    keymap_apply "" 0 > "$SB/apply.out" || exit 1
    content=$(cat "$cfg"; printf x); content=${content%x}
    [[ "$content" == "$pre$KEYMAP_BLOCK_START"* && "$content" == *"$KEYMAP_BLOCK_END$post" ]] || exit 1
    [[ $(grep -cF "$KEYMAP_BLOCK_START" "$cfg") -eq 1 ]] || exit 1
    grep -qF "herdr-tts --toggle-play" "$cfg"
  '
}

case__critical_keymap_idempotent_no_backup() {
  keymap_fixture || return 1
  in_host '
    cfg="$HERDR_CONFIG_DIR/herdr/config.toml"
    keymap_apply "" 0 > "$SB/apply.out" || exit 1
    cp "$cfg" "$SB/before.toml"
    before=$(stat -c "%i:%y:%s" "$cfg")
    shopt -s nullglob; baks=("$cfg".bak-*); [[ ${#baks[@]} -eq 1 ]] || exit 1
    keymap_apply "" 0 > "$SB/apply.out" || exit 1
    baks=("$cfg".bak-*)
    [[ ${#baks[@]} -eq 1 && "$before" == "$(stat -c "%i:%y:%s" "$cfg")" ]] || exit 1
    cmp -s "$cfg" "$SB/before.toml" || exit 1
    grep -q "already up to date" "$SB/apply.out"
  '
}

case__critical_keymap_null_removes_binding() {
  keymap_fixture || return 1
  in_host '
    cfg="$HERDR_CONFIG_DIR/herdr/config.toml"
    keymap_apply "" 0 > "$SB/apply.out" || exit 1
    printf "\n# manual footer\n" >> "$cfg"
    printf "%s\n" "{\"bindings\":{\"play\":\"ctrl+alt+r\",\"tldr\":null}}" > "$KEYMAP_FILE"
    keymap_apply "" 0 > "$SB/apply.out" || exit 1
    [[ $(grep -c "^\[\[keys.command\]\]" "$cfg") -eq 1 ]] || exit 1
    ! grep -qF "herdr-tts --tldr" "$cfg" || exit 1
    grep -q "^# manual footer$" "$cfg"
  '
}

case__critical_keymap_prune_three_backups() {
  keymap_fixture || return 1
  in_host '
    cfg="$HERDR_CONFIG_DIR/herdr/config.toml"
    for n in 1 2 3 4 5; do printf "%s\n" "$n" > "$cfg.bak-20200101-00000$n"; done
    printf keep > "$SB/unrelated.bak-20200101-000001"
    keymap_prune_backups "$cfg" || exit 1
    shopt -s nullglob; baks=("$cfg".bak-*)
    [[ ${#baks[@]} -eq 3 && ! -e "$cfg.bak-20200101-000001" && ! -e "$cfg.bak-20200101-000002" ]] || exit 1
    [[ $(cat "${baks[0]}") == 3 && $(cat "${baks[1]}") == 4 && $(cat "${baks[2]}") == 5 ]] || exit 1
    [[ $(cat "$SB/unrelated.bak-20200101-000001") == keep ]]
  '
}

case__critical_keymap_dry_run_no_writes() {
  keymap_fixture || return 1
  in_host '
    cfg="$HERDR_CONFIG_DIR/herdr/config.toml"
    cp "$cfg" "$SB/before.toml"
    keymap_apply "" 1 > "$SB/apply.out" || exit 1
    cmp -s "$cfg" "$SB/before.toml" || exit 1
    grep -q "dry-run: no files were modified" "$SB/apply.out" || exit 1
    keymap_apply "$SB/uncreated/herdr/config.toml" 1 > "$SB/apply.out" || exit 1
    [[ ! -e "$SB/uncreated" ]] || exit 1
    shopt -s nullglob; writes=("$cfg".bak-* "$TMPDIR"/*)
    [[ ${#writes[@]} -eq 0 ]]
  '
}

case__critical_keymap_hard_errors_refuse() {
  keymap_fixture || return 1
  in_host '
    cfg="$HERDR_CONFIG_DIR/herdr/config.toml"; cp "$cfg" "$SB/before.toml"
    for json in \
      "{\"bindings\":{\"unknown\":\"prefix+q\"}}" \
      "{\"bindings\":{\"play\":\"alt+x\"}}" \
      "{\"bindings\":{\"play\":\"prefix+a+b\"}}" \
      "{\"bindings\":{\"play\":\"prefix+u\",\"stop\":\"prefix+u\"}}" \
      "not JSON" "{\"unexpected\":true}" "{\"bindings\":[]}"; do
      printf "%s\n" "$json" > "$KEYMAP_FILE"
      rc=0; keymap_check "" > "$SB/check.out" 2> "$SB/check.err" || rc=$?
      [[ $rc -eq 1 ]] || exit 1
      rc=0; keymap_apply "" 0 > "$SB/apply.out" 2> "$SB/apply.err" || rc=$?
      [[ $rc -eq 1 ]] || exit 1
      cmp -s "$cfg" "$SB/before.toml" || exit 1
      ! compgen -G "$cfg.bak-*" >/dev/null || exit 1
    done
  '
}

case__critical_keymap_failed_check_rolls_back() {
  keymap_fixture || return 1
  in_host '
    cfg="$HERDR_CONFIG_DIR/herdr/config.toml"; cp "$cfg" "$SB/before.toml"
    herdr() { printf "%s\n" "$*" >> "$SB/herdr.out"; echo rejected-config >&2; return 1; }
    rc=0; keymap_apply "" 0 > "$SB/apply.out" 2> "$SB/apply.err" || rc=$?
    [[ $rc -eq 1 ]] || exit 1
    cmp -s "$cfg" "$SB/before.toml" || exit 1
    grep -q "rolled back" "$SB/apply.err" || exit 1
    grep -q "rejected-config" "$SB/apply.err" || exit 1
    rc=0; keymap_apply "$SB/new.toml" 0 > "$SB/new.out" 2> "$SB/new.err" || rc=$?
    [[ $rc -eq 1 && ! -e "$SB/new.toml" ]] || exit 1
    [[ $(cat "$SB/herdr.out") == $'"'"'config check\nconfig check'"'"' ]]
  '
}

case__critical_keymap_without_herdr_note() {
  keymap_fixture || return 1
  in_host '
    # Model absence at the discovery seam, irrespective of the CI image.
    command() {
      [[ "$*" != "-v herdr" ]] || return 1
      builtin command "$@"
    }
    keymap_apply "" 0 > "$SB/apply.out" || exit 1
    grep -q "skipped post-write config validation" "$SB/apply.out" || exit 1
    [[ ! -e "$STUB_LOG" ]]
  '
}

case__critical_keymap_adopt_protects_edits() {
  keymap_fixture || return 1
  in_host '
    cp "$KEYMAP_FILE" "$SB/before.json"
    rc=0; keymap_adopt menu 0 > "$SB/adopt.out" || rc=$?
    [[ $rc -eq 1 ]] || exit 1
    cmp -s "$KEYMAP_FILE" "$SB/before.json" || exit 1
    ! compgen -G "$KEYMAP_FILE.bak-*" >/dev/null || exit 1
    keymap_adopt menu 1 > "$SB/adopt.out" || exit 1
    jq -e ".style == \"menu\" and .bindings.menu == \"prefix+u\" and ([.bindings[] | select(. != null)] | length) == 1" "$KEYMAP_FILE" >/dev/null || exit 1
    shopt -s nullglob; baks=("$KEYMAP_FILE".bak-*); [[ ${#baks[@]} -eq 1 ]] || exit 1
    before=$(stat -c "%i:%y:%s" "$KEYMAP_FILE")
    keymap_adopt menu 0 > "$SB/adopt.out" || exit 1
    baks=("$KEYMAP_FILE".bak-*)
    [[ ${#baks[@]} -eq 1 && "$before" == "$(stat -c "%i:%y:%s" "$KEYMAP_FILE")" ]] || exit 1
    KEYMAP_FILE="$SB/missing-keymap.json"
    keymap_adopt ctrlalt 0 > "$SB/adopt.out" || exit 1
    jq -e ".style == \"ctrlalt\" and .bindings.play == \"ctrl+alt+r\" and .bindings.paragraph_next == null" "$KEYMAP_FILE" >/dev/null
  '
}

case__critical_keymap_autostart_corrupt_nonfatal() {
  keymap_fixture || return 1
  printf 'not JSON\n' > "$SANDBOX/keymap.json" || return 1
  in_host '
    cfg="$HERDR_CONFIG_DIR/herdr/config.toml"; cp "$cfg" "$SB/before.toml"
    rc=0; daemon_keymap_autostart > "$SB/autostart.out" 2> "$SB/autostart.err" || rc=$?
    [[ $rc -eq 0 ]] || exit 1
    cmp -s "$cfg" "$SB/before.toml" || exit 1
    grep -q "keymap apply failed" "$SB/autostart.err" || exit 1
    KEYMAP_FILE="$SB/absent-keymap.json"
    daemon_keymap_autostart > "$SB/autostart.out" || exit 1
    [[ ! -s "$SB/autostart.out" ]]
  '
}

# --- Daemon lifecycle: only pids spawned by this test; NEVER a live sweep. ----
case__critical_daemon_pid_identity_guard() {
  in_host '
    start_dummy herdr-tts-f3-owned || exit 1; matched=$DUMMY_PID
    printf "%s\n" "$matched" > "$DAEMON_PID_FILE"
    [[ "$(daemon_pid)" == "$matched" ]] || exit 1
    start_dummy f3-unrelated || exit 1; unrelated=$DUMMY_PID
    printf "%s\n" "$unrelated" > "$DAEMON_PID_FILE"
    [[ -z "$(daemon_pid)" ]] || exit 1
    kill -0 "$unrelated" || exit 1
    printf not-a-pid > "$DAEMON_PID_FILE"
    [[ -z "$(daemon_pid)" ]] || exit 1
    DAEMON_PID_FILE="$SB/missing.pid"
    [[ -z "$(daemon_pid)" ]]
  '
}

case__critical_daemon_takeover_owned_child() {
  in_host '
    start_dummy herdr-tts-f3-owned || exit 1; matched=$DUMMY_PID
    start_dummy f3-unrelated || exit 1; unrelated=$DUMMY_PID
    kill() {
      if [[ $# -eq 1 && "$1" == "$matched" ]]; then
        [[ ! -f "$SUPERVISOR_STOP_FLAG" ]] || echo armed-before-kill > "$SB/order.out"
      fi
      builtin kill "$@"
    }
    printf "%s\n" "$matched" > "$DAEMON_PID_FILE"
    daemon_takeover || exit 1
    finish_owned "$matched" || exit 1   # takeover must have ended it; no rescue kill
    [[ $(cat "$SB/order.out") == armed-before-kill ]] || exit 1
    [[ $(cat "$DAEMON_PID_FILE") == "$$" && -f "$SUPERVISOR_STOP_FLAG" ]] || exit 1
    ! kill -0 "$matched" 2>/dev/null || exit 1
    SUPERVISOR_STOP_FLAG="$SB/unused.stop"
    printf "%s\n" "$unrelated" > "$DAEMON_PID_FILE"
    daemon_takeover || exit 1
    kill -0 "$unrelated" || exit 1
    [[ $(cat "$DAEMON_PID_FILE") == "$$" && ! -e "$SUPERVISOR_STOP_FLAG" ]]
  '
}

case__critical_daemon_stop_pidfile_fail_open() {
  in_host '
    # The fallback is out of scope: its smoke marker is not unique ownership.
    daemon_sweep_legacy() { printf -v "$1" none; echo stubbed-sweep >> "$SB/sweep.out"; return 1; }
    start_dummy herdr-tts-f3-owned || exit 1; matched=$DUMMY_PID
    kill() {
      if [[ $# -eq 1 && "$1" == "$matched" ]]; then
        [[ ! -f "$SUPERVISOR_STOP_FLAG" ]] || echo armed-before-kill > "$SB/order.out"
      fi
      builtin kill "$@"
    }
    printf "%s\n" "$matched" > "$DAEMON_PID_FILE"
    daemon_stop_running how || exit 1
    finish_owned "$matched" || exit 1   # the stop must have ended it; no rescue kill
    [[ $(cat "$SB/order.out") == armed-before-kill ]] || exit 1
    [[ "$how" == pidfile && -f "$SUPERVISOR_STOP_FLAG" ]] || exit 1
    [[ ! -e "$SB/sweep.out" ]] || exit 1
    ! kill -0 "$matched" 2>/dev/null || exit 1
    printf not-a-pid > "$DAEMON_PID_FILE"
    daemon_stop_running how || exit 1
    [[ "$how" == none && $(cat "$SB/sweep.out") == stubbed-sweep ]]
  '
}

case__critical_daemon_supervisor_stop_flag() {
  in_host '
    : > "$SUPERVISOR_STOP_FLAG"
    rc=0; daemon_supervisor_should_relaunch 3 || rc=$?
    [[ $rc -eq 1 && ! -e "$SUPERVISOR_STOP_FLAG" ]] || exit 1
    daemon_supervisor_should_relaunch 0 || exit 1
    daemon_supervisor_should_relaunch 3
  '
}

case__critical_daemon_supervisor_relaunch() {
  in_host '
    SCRIPT="$SB/dying.sh"
    cat > "$SCRIPT" <<'"'"'CHILD'"'"'
#!/usr/bin/env bash
n=0; [[ ! -f "$STUB_LOG.count" ]] || read -r n < "$STUB_LOG.count"
n=$((n + 1)); printf "%s\n" "$n" > "$STUB_LOG.count"
printf "%s\n" "$*" >> "$STUB_LOG"
if (( n >= 2 )); then : > "$HERDR_TTS_SUPERVISOR_STOP_FILE"; fi
exit 3
CHILD
    chmod +x "$SCRIPT"
    : > "$SUPERVISOR_STOP_FLAG" # stale flag must not stop the first crash
    (run_daemon_supervised) || exit 1
    [[ $(cat "$STUB_LOG.count") == 2 && $(cat "$STUB_LOG") == $'"'"'_daemon\n_daemon'"'"' ]] || exit 1
    [[ ! -e "$SUPERVISOR_STOP_FLAG" ]] || exit 1
    grep -q "daemon died (rc=3)" "$DAEMON_LOG" || exit 1
    grep -q "deliberate daemon stop (rc=3)" "$DAEMON_LOG"
  '
}

case__critical_daemon_supervisor_forwards_term() {
  in_host '
    SCRIPT="$SB/blocking.sh"
    cat > "$SCRIPT" <<'"'"'CHILD'"'"'
#!/usr/bin/env bash
trap '\''printf "TERM\n" > "$STUB_LOG.term"; exit 0'\'' TERM
printf "%s\n" "$$" > "$STUB_LOG.child"
printf "%s\n" "$*" >> "$STUB_LOG"
while :; do sleep 0.05; done
CHILD
    chmod +x "$SCRIPT"
    (run_daemon_supervised) & supervisor=$!; register_owned "$supervisor" || exit 1
    for i in {1..200}; do [[ ! -f "$STUB_LOG.child" ]] || break; command sleep 0.01; done
    [[ -s "$STUB_LOG.child" ]] || exit 1
    read -r child < "$STUB_LOG.child"
    builtin kill -TERM "$supervisor" || exit 1
    finish_owned "$supervisor" || exit 1   # TERM handshake must end it; no rescue kill
    rc=${OWNED_RC[$supervisor]}
    [[ $rc -eq 0 && $(cat "$STUB_LOG.term") == TERM ]] || exit 1
    ! kill -0 "$child" 2>/dev/null || exit 1
    [[ $(cat "$STUB_LOG") == _daemon ]] || exit 1
    grep -q "TERM received" "$DAEMON_LOG"
  '
}

# --- Harness safety: refuse to spawn stubborn jobs without bounded cleanup. ---
bounded_host_available() {
  [[ "$(declare -f in_host)" == *'--kill-after='* ]] || {
    echo 'Missing hard timeout escalation; stubborn-process probe not started' >&2
    return 1
  }
}

case__critical_harness_reaps_term_ignoring_child() {
  bounded_host_available || return 1
  in_host '
    bash -c '\''trap "" TERM; : > "$1"; exec -a f3-term-ignoring-owned sleep 60'\'' _ "$SB/ready" &
    pid=$!; register_owned "$pid" || exit 1
    for i in {1..200}; do [[ ! -f "$SB/ready" ]] || break; sleep 0.01; done
    [[ -f "$SB/ready" ]] || exit 1
    birth=${OWNED_BIRTH[$pid]}
    builtin kill -TERM "$pid" || exit 1
    owned_live "$pid" "$birth" || exit 1     # TERM is ignored: still alive
    cleanup_owned || exit 1                  # must escalate, not hang
    [[ ${#OWNED_PIDS[@]} -eq 0 && "${OWNED_RC[$pid]}" == 137 ]] || exit 1
    ! owned_live "$pid" "$birth"
  '
}

case__critical_harness_reaps_stubborn_supervisor_child() {
  bounded_host_available || return 1
  in_host '
    SCRIPT="$SB/stubborn.sh"
    cat > "$SCRIPT" <<'"'"'CHILD'"'"'
#!/usr/bin/env bash
trap "" TERM
# Fork-free self-identity: $(cat /proc/self/stat) would read the FORKED cat
# process (whose starttime differs whenever the fork lands in a later clock
# tick), so builtin read + redirection keeps the true pid+starttime pair.
IFS= read -r stat_line < /proc/self/stat
stat_rest=${stat_line##*) }
read -r -a hf <<< "$stat_rest"
printf "%s %s\n" "$$" "${hf[19]}" > "$STUB_LOG.child"
exec -a f3-supervisor-child-owned sleep 60
CHILD
    chmod +x "$SCRIPT"
    (run_daemon_supervised) & supervisor=$!; register_owned "$supervisor" || exit 1
    local_i=0
    while [[ ! -s "$STUB_LOG.child" && $local_i -lt 200 ]]; do command sleep 0.01; local_i=$((local_i+1)); done
    [[ -s "$STUB_LOG.child" ]] || exit 1
    # Fail fast if the announced identity is not the live child (e.g. an
    # instant respawn raced the handshake read): registration is strict.
    read -r child child_birth < "$STUB_LOG.child"
    owned_live "$child" "$child_birth" || exit 1
    # Explicit identity: the child registered itself while provably alive, so
    # cleanup owns it directly — no /proc discovery, even if the supervisor
    # has already exited by cleanup time.
    register_handshake_owned "$STUB_LOG.child" || exit 1
    cleanup_owned || exit 1
    [[ ${#OWNED_PIDS[@]} -eq 0 ]] || exit 1
    [[ "${OWNED_RC[$supervisor]}" == 0 ]] || exit 1
    ! owned_live "$child" "$child_birth" || exit 1
    grep -q "TERM received" "$DAEMON_LOG"
  '
}

case__critical_harness_stale_identity_refuses_signal() {
  bounded_host_available || return 1
  in_host '
    # (a) registry entry whose process already exited: stop no-ops, never signals
    bash -c '\''exec -a f3-short-owned sleep 0.4'\'' & short=$!
    register_owned "$short" || exit 1
    short_birth=${OWNED_BIRTH[$short]}
    command sleep 0.7                        # natural exit; the entry goes stale
    stop_owned "$short" "$short_birth" || exit 1
    finish_owned "$short" || exit 1
    [[ "${OWNED_RC[$short]}" != 137 ]] || exit 1   # reaped naturally, not KILLed
    # (b) live owned process + WRONG birth: boundary check refuses the signal
    bash -c '\''exec -a f3-live-owned sleep 60'\'' & live=$!
    register_owned "$live" || exit 1
    live_birth=${OWNED_BIRTH[$live]}
    stop_owned "$live" 1 || exit 1
    owned_live "$live" "$live_birth" || exit 1      # mismatched identity NOT signaled
    # (c) the correct identity still stops cleanly afterwards
    stop_owned "$live" "$live_birth" || exit 1
    finish_owned "$live" || exit 1
    ! owned_live "$live" "$live_birth"
  '
}

case__critical_harness_cleanup_reaps_orphan_after_parent_exit() {
  bounded_host_available || return 1
  in_host '
    SCRIPT="$SB/orphan.sh"
    cat > "$SCRIPT" <<'"'"'PARENT'"'"'
#!/usr/bin/env bash
# Fixture parent: spawns a child that announces its own identity (pid +
# /proc starttime) and outlives the parent; parent exits at once. The child
# self-limits (sleep 5) so a failed cleanup can never linger. Fork-free
# self-read: a substituted `cat` would report the fork process starttime.
bash -c '\''IFS= read -r stat_line < /proc/self/stat; stat_rest=${stat_line##*) }; read -r -a hf <<< "$stat_rest"; printf "%s %s\n" "$$" "${hf[19]}" > "$1"; exec -a f3-orphan-owned sleep 5'\'' _ "$STUB_LOG.child" &
exit 0
PARENT
    chmod +x "$SCRIPT"
    bash "$SCRIPT" || exit 1
    local_i=0
    while [[ ! -s "$STUB_LOG.child" && $local_i -lt 200 ]]; do command sleep 0.01; local_i=$((local_i+1)); done
    read -r child child_birth < "$STUB_LOG.child"
    [[ "$child" =~ ^[1-9][0-9]*$ ]] || exit 1
    owned_live "$child" "$child_birth" || exit 1
    # Explicit ownership: the child announced its identity while alive, so it
    # is registered directly — cleanup reaps it even though its parent (which
    # was never ours to track) has already exited.
    register_handshake_owned "$STUB_LOG.child" || exit 1
    cleanup_owned || exit 1
    ! owned_live "$child" "$child_birth"
  '
}

# --- Managed config writer: preservation, admission, backup, permissions. ----
case__critical_config_upsert_dedupes() {
  in_host '
    printf "%s\n" "# untouched comment" "export TTS_PROVIDER=old" "UNMANAGED=keep" "$CONFIG_SETTINGS_BLOCK_START" "TTS_PROVIDER=duplicate" "$CONFIG_SETTINGS_BLOCK_END" > "$CONFIG_FILE"
    config_set TTS_PROVIDER edge || exit 1
    config_set TTS_PROVIDER openai || exit 1
    config_set TTS_PLAYBACK local || exit 1
    printf "%s\n" "# untouched comment" "TTS_PROVIDER=\"openai\"" "UNMANAGED=keep" "$CONFIG_SETTINGS_BLOCK_START" "TTS_PLAYBACK=\"local\"" "$CONFIG_SETTINGS_BLOCK_END" > "$SB/expected.env"
    cmp -s "$CONFIG_FILE" "$SB/expected.env" || exit 1
    bash -n "$CONFIG_FILE" || exit 1
    source "$CONFIG_FILE"
    [[ "$TTS_PROVIDER" == openai && "$TTS_PLAYBACK" == local && "$UNMANAGED" == keep ]]
  '
}

case__critical_config_rejects_unmanaged_values() {
  in_host '
    printf "# untouched\n" > "$CONFIG_FILE"; cp "$CONFIG_FILE" "$SB/before.env"
    rc=0; config_set NOT_MANAGED value 2> "$SB/unmanaged.err" || rc=$?
    [[ $rc -eq 1 ]] || exit 1
    rc=0; config_set TTS_PROVIDER "bad\"quote" 2> "$SB/quote.err" || rc=$?
    [[ $rc -eq 1 && ! -e "$CONFIG_FILE.bak" ]] || exit 1
    cmp -s "$CONFIG_FILE" "$SB/before.env" || exit 1
    grep -q "unmanaged key" "$SB/unmanaged.err" || exit 1
    grep -q "must not contain double quotes" "$SB/quote.err"
  '
}

case__critical_config_backup_first_write() {
  in_host '
    printf "# original\nTTS_PROVIDER=old\n" > "$CONFIG_FILE"; cp "$CONFIG_FILE" "$SB/before.env"
    config_set TTS_PROVIDER edge || exit 1
    cmp -s "$CONFIG_FILE.bak" "$SB/before.env" || exit 1
    config_set TTS_PLAYBACK local || exit 1
    cmp -s "$CONFIG_FILE.bak" "$SB/before.env"
  '
}

case__critical_config_unwritable_fail_open() {
  in_host '
    printf "# original\n" > "$CONFIG_FILE"; cp "$CONFIG_FILE" "$SB/before.env"
    dir=${CONFIG_FILE%/*}; chmod 500 "$dir"
    if [[ -w "$dir" ]]; then chmod 700 "$dir"; echo "Permission test requires an unprivileged uid" >&2; exit 1; fi
    rc=0; config_set TTS_PROVIDER edge 2> "$SB/readonly.err" || rc=$?
    chmod 700 "$dir"
    [[ $rc -eq 1 && ! -e "$CONFIG_FILE.bak" ]] || exit 1
    cmp -s "$CONFIG_FILE" "$SB/before.env" || exit 1
    grep -q "not writable" "$SB/readonly.err" || exit 1
    echo caller-continues
  '
}

red_proofs() {
  local original mutated copy output family name needle replacement suffix rc
  original="$(cat "$REPO_HOST_DIR/bin/herdr-tts")"
  local red_dir="${TMPDIR:-/tmp}/red"
  mkdir -p "$red_dir" || return 1
  local -a families=(gate toggle voice keymap daemon config)
  local -a cases=(critical_gate_mute critical_mute_focus_roundtrip critical_voice_precedence
    critical_keymap_failed_check_rolls_back critical_daemon_supervisor_stop_flag critical_config_upsert_dedupes)
  local -a needles=(
    '  if [[ "$muted" == "true" ]]; then
    echo "[$(date +%H:%M:%S)] $(tt gate.muted "$agent_name" "$pane_id" "$status")'
    '.panes[$p].muted = false'
    'local pane="$1" agent="${2:-}" i'
    'if mv -f "$backup" "$target"; then'
    'if [[ -f "$SUPERVISOR_STOP_FLAG" ]]; then'
    '          if (( ! replaced )); then')
  local -a replacements=(
    '  if [[ "$muted" == "false" ]]; then
    echo "[$(date +%H:%M:%S)] $(tt gate.muted "$agent_name" "$pane_id" "$status")'
    '.panes[$p].muted = true'
    'local pane="f3-missing-pane" agent="${2:-}" i'
    'if :; then'
    'if [[ ! -f "$SUPERVISOR_STOP_FLAG" ]]; then'
    '          if (( 1 )); then')
  local i
  for i in "${!families[@]}"; do
    family=${families[i]}; name=${cases[i]}; needle=${needles[i]}; replacement=${replacements[i]}
    [[ "$original" == *"$needle"* ]] || { echo "Missing mutation target: $family" >&2; return 1; }
    suffix=${original#*"$needle"}
    [[ "$suffix" != *"$needle"* ]] || { echo "Ambiguous mutation target: $family" >&2; return 1; }
    mutated=${original/"$needle"/"$replacement"}
    copy="$red_dir/$family-herdr-tts"; output="$red_dir/$family.out"
    printf '%s\n' "$mutated" > "$copy" || return 1
    bash -n "$copy" || return 1
    rc=0
    RED_PROOFS=0 CASES="$name" HERDR_TTS_TEST_LAUNCHER="$copy" \
      bash "$SCRIPT_DIR/critical_cases.sh" > "$output" 2>&1 || rc=$?
    cat "$output"
    [[ $rc -eq 1 ]] && grep -qx "CASE $name FAIL" "$output" || return 1
    printf 'RED %s: syntax-valid mutation produced the expected CASE FAIL\n' "$family"
    RED_PROOFS=0 CASES="$name" HERDR_TTS_TEST_LAUNCHER="$REPO_HOST_DIR/bin/herdr-tts" \
      bash "$SCRIPT_DIR/critical_cases.sh" || return 1
    printf 'GREEN %s: unmodified launcher passed the same case\n' "$family"
  done
}

main() {
  local all="" fn name fail=0
  while IFS= read -r fn; do all="$all ${fn#case__}"; done \
    < <(declare -F | awk '{print $3}' | grep '^case__' | sort)
  for name in ${CASES:-$all}; do
    printf 'CASE %s START\n' "$name"
    if declare -F "case__$name" >/dev/null && (sandbox_setup "$name" && "case__$name"); then
      printf 'CASE %s OK\n' "$name"
    else
      printf 'CASE %s FAIL\n' "$name"
      fail=1
    fi
  done
  return "$fail"
}

if [[ "${RED_PROOFS:-0}" == 1 ]]; then red_proofs; else main; fi
