#!/usr/bin/env bash
# HT-03 — radio mode (voice triage bulletin) test harness
# Protocol: CASE <name> START / OK | FAIL
#
# Everything runs against fakes: the pinned engine venv python is a stub
# that records argv-by-argv (RENDER / ENQ / CANCEL / PLAY / IPC lines in
# STUB_LOG) and writes the spoken text into the "audio" file it renders, so
# the queue contents are provable from the log alone; `herdr` on PATH is a
# fake that answers `agent list` from agents.json and `pane read` with a
# per-pane body. No microphone, no audio device, no network, no models.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_HOST_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"   # hosts/herdr/tts-plugin

SANDBOX_ROOT="$(mktemp -d)"
trap 'rm -rf "$SANDBOX_ROOT"' EXIT

sandbox_setup() {
  # Cases tune the stubs through exported knobs; reset them so no case
  # inherits another's failure injection (they run in sort order).
  unset ENQUEUE_RC RENDER_SLEEP SLOW_TEXT TTS_RADIO_MAX_CHATS \
    TTS_RADIO_DEBOUNCE_SECONDS HERDR_TTS_LANG
  SANDBOX="$SANDBOX_ROOT/$1"
  mkdir -p "$SANDBOX/bin" "$SANDBOX/data/herdr-tts/venv/bin" \
    "$SANDBOX/config/herdr-tts" "$SANDBOX/state/herdr-tts" "$SANDBOX/tmp"
  STUB_LOG="$SANDBOX/engine.log"
  : > "$STUB_LOG"
  cat > "$SANDBOX/data/herdr-tts/venv/bin/python" <<'STUB'
#!/usr/bin/env bash
# Inline python (dashboard history scan) runs for real; every other call is
# a recorded stand-in for the engine / pending_queue helper.
if [[ "$1" == "-c" ]]; then exec python3 "$@"; fi
script="$1"; shift
case "$script" in
  */pending_queue.py)
    sub="$1"; shift
    case "$sub" in
      enqueue-file)
        file="$1"; shift
        prio="" id=""
        while [[ $# -gt 0 ]]; do
          case "$1" in
            --priority) prio="$2"; shift 2 ;;
            --id) id="$2"; shift 2 ;;
            *) shift ;;
          esac
        done
        printf 'ENQ prio=%s id=%s text=%s\n' "$prio" "$id" "$(cat "$file" 2>/dev/null)" >> "$STUB_LOG"
        if [[ -n "${ENQUEUE_RC:-}" && "$ENQUEUE_RC" != 0 ]]; then
          echo "ok=false error=daemon unreachable"
          exit "$ENQUEUE_RC"
        fi
        echo "ok=true item=1 queue_len=0"
        exit 0
        ;;
      cancel)
        printf 'CANCEL %s\n' "$*" >> "$STUB_LOG"
        echo "ok=true removed=1 active_stopped=0"
        exit 0
        ;;
    esac
    ;;
  *)
    # tts_engine.py: --ipc-cmd <cmd> | --play-file <file> | <text> ... --output <file>
    text="$1"
    if [[ "$text" == "--ipc-cmd" ]]; then
      printf 'IPC %s\n' "$*" >> "$STUB_LOG"
      exit 0
    fi
    if [[ "$text" == "--play-file" ]]; then
      printf 'PLAY text=%s\n' "$(cat "$2" 2>/dev/null)" >> "$STUB_LOG"
      exit 0
    fi
    shift
    out="" voice="" tldr=0 raw=0 agent="" session=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --output) out="$2"; shift 2 ;;
        --voice) voice="$2"; shift 2 ;;
        --agent) agent="$2"; shift 2 ;;
        --session-id) session="$2"; shift 2 ;;
        --tldr) tldr=1; shift ;;
        --raw) raw=1; shift ;;
        *) shift ;;
      esac
    done
    if [[ -n "${SLOW_TEXT:-}" && "$text" == *"$SLOW_TEXT"* ]]; then
      sleep "${RENDER_SLEEP:-5}"
    fi
    printf 'RENDER voice=%s tldr=%s raw=%s agent=%s session=%s text=%s\n' \
      "$voice" "$tldr" "$raw" "$agent" "$session" "$text" >> "$STUB_LOG"
    [[ -n "$out" ]] && printf '%s' "$text" > "$out"
    exit 0
    ;;
esac
exit 3
STUB
  chmod +x "$SANDBOX/data/herdr-tts/venv/bin/python"
  cat > "$SANDBOX/bin/herdr" <<'EOF'
#!/usr/bin/env bash
case "$1 $2" in
  "agent list") cat "$AGENTS_JSON" 2>/dev/null; exit 0 ;;
  "pane read")  printf 'Body of %s. It finished the requested change.\n' "$3"; exit 0 ;;
  "agent read") printf 'Body of %s. It finished the requested change.\n' "$3"; exit 0 ;;
  "api snapshot") printf '{"result":{"snapshot":{}}}'; exit 0 ;;
esac
exit 0
EOF
  chmod +x "$SANDBOX/bin/herdr"
  AGENTS_JSON="$SANDBOX/agents.json"
  SNOOZE_JSON="$SANDBOX/snooze.json"
  printf '{}' > "$SNOOZE_JSON"
}

# set_agents "pane|status|agent|title" ... → agents.json in `herdr agent list` shape
set_agents() {
  local out="[]" line p s a t
  for line in "$@"; do
    IFS='|' read -r p s a t <<<"$line"
    out="$(jq -c --arg p "$p" --arg s "$s" --arg a "$a" --arg t "$t" \
      '. + [{pane_id:$p, agent:$a, agent_status:$s, terminal_title_stripped:$t,
             agent_session:{agent:$a, value:("sess-" + $p)}}]' <<<"$out")"
  done
  jq -c '{result:{agents:.}}' <<<"$out" > "$AGENTS_JSON"
}

# Default fleet: two done (p3 has the MOST recent audio), one blocked, two quiet.
seed_default_roster() {
  set_agents \
    "p1|done|claude|Fix login bug" \
    "p2|blocked|opencode|Migrate database schema" \
    "p3|done|codex|Refactor billing" \
    "p4|working|claude|Long running task" \
    "p5|idle|pi|Scratch pad"
  # Audio-history ledger: later line = more recent audio.
  printf '2026-10-08T09:00:00\tp1\tclaude\t3.0\told turn\n2026-10-08T09:30:00\tp3\tcodex\t3.0\tnew turn\n' \
    > "$SANDBOX/state/herdr-tts/history.log"
}

in_host() {
  local snippet="$1"
  shift
  PATH="$SANDBOX/bin:$PATH" \
  XDG_DATA_HOME="$SANDBOX/data" XDG_CONFIG_HOME="$SANDBOX/config" \
  XDG_STATE_HOME="$SANDBOX/state" STUB_LOG="$STUB_LOG" AGENTS_JSON="$AGENTS_JSON" \
  TMPDIR="$SANDBOX/tmp" \
  ENQUEUE_RC="${ENQUEUE_RC:-}" RENDER_SLEEP="${RENDER_SLEEP:-}" SLOW_TEXT="${SLOW_TEXT:-}" \
  HERDR_TTS_SMOKE=1 \
  HERDR_TTS_LOCK_FILE="$SANDBOX/playing.lock" HERDR_TTS_PID_FILE="$SANDBOX/current.pid" \
  HERDR_TTS_IPC_SOCKET="$SANDBOX/player.sock" \
  HERDR_TTS_SNOOZE_FILE="$SNOOZE_JSON" \
  HERDR_TTS_CONFIG_FILE="$SANDBOX/config/herdr-tts/config.env" \
  HERDR_TTS_KEYMAP_FILE="$SANDBOX/config/herdr-tts/keymap.json" \
  HERDR_TTS_VOICES_FILE="$SANDBOX/config/herdr-tts/voices.json" \
  HERDR_TTS_DAEMON_PID_FILE="$SANDBOX/daemon.pid" \
  HERDR_TTS_SUPERVISOR_STOP_FILE="$SANDBOX/state/herdr-tts/daemon-supervisor.stop" \
  HERDR_TTS_DAEMON_LOG="$SANDBOX/state/herdr-tts/daemon.log" \
  bash -c 'HOST="$1"; shift; ARGS=("$@"); set --; source "$HOST/bin/herdr-tts"; set -- "${ARGS[@]}"; '"$snippet" \
    _ "$REPO_HOST_DIR" "$@"
}

# Queue contents as "<prio>|<text>" lines, in enqueue order.
enq_lines() { sed -n 's/^ENQ prio=\([a-z]*\) id=[^ ]* text=/\1|/p' "$STUB_LOG"; }
enq_count() { grep -c '^ENQ ' "$STUB_LOG" || true; }
render_count() { grep -c '^RENDER ' "$STUB_LOG" || true; }

# ── RF-HT-03-2/3/5: blocked first, then done by recency, priority labels ──
case__radio_orders_blocked_before_done() {
  sandbox_setup orders
  seed_default_roster
  local out rc=0
  out="$(in_host 'run_radio; wait' 2>&1)" || rc=$?
  [[ $rc -eq 0 ]] || return 1
  local -a q=()
  mapfile -t q < <(enq_lines)
  # blocked header + body, done p3 (newest audio) header + body, done p1
  # header + body, then the closing phrase at the lowest priority.
  [[ "${#q[@]}" -eq 7 ]] || return 1
  [[ "${q[0]}" == "blocked|Blocked: Migrate database schema."* ]] || return 1
  [[ "${q[1]}" == "blocked|Body of p2."* ]] || return 1
  [[ "${q[2]}" == "done|Done: Refactor billing."* ]] || return 1
  [[ "${q[3]}" == "done|Body of p3."* ]] || return 1
  [[ "${q[4]}" == "done|Done: Fix login bug."* ]] || return 1
  [[ "${q[5]}" == "done|Body of p1."* ]] || return 1
  [[ "${q[6]}" == "working|"* ]] || return 1
  # The body goes through the engine --tldr pipeline with the connector ids.
  grep -q '^RENDER .* tldr=1 raw=0 agent=opencode session=sess-p2 text=Body of p2\.' "$STUB_LOG" || return 1
  # Working / idle chats are never read out.
  ! grep -q 'Long running task\|Scratch pad\|Body of p4\|Body of p5' "$STUB_LOG" || return 1
}

# ── RF-HT-03-2: muted and snoozed chats are excluded ──
case__radio_respects_mute_and_snooze() {
  sandbox_setup mute_snooze
  seed_default_roster
  local now past future
  now="$(date +%s)"; past=$(( now - 30 )); future=$(( now + 600 ))
  # p2 muted, p3 snoozed (active), p1 snooze EXPIRED (must still be read).
  printf '{"panes":{"p2":{"muted":true},"p3":{"snooze_until":%s},"p1":{"snooze_until":%s}}}' \
    "$future" "$past" > "$SNOOZE_JSON"
  in_host 'run_radio; wait' >/dev/null 2>&1 || return 1
  grep -q 'Fix login bug' "$STUB_LOG" || return 1
  ! grep -q 'Migrate database schema\|Body of p2' "$STUB_LOG" || return 1
  ! grep -q 'Refactor billing\|Body of p3' "$STUB_LOG" || return 1

  # A GLOBAL snooze silences the whole bulletin: nothing rendered or queued.
  sandbox_setup global_snooze
  seed_default_roster
  now="$(date +%s)"
  printf '{"global_snooze_until":%s}' "$(( now + 600 ))" > "$SNOOZE_JSON"
  local out rc=0
  out="$(in_host 'run_radio; wait' 2>&1)" || rc=$?
  [[ $rc -eq 0 ]] || return 1
  [[ "$(render_count)" -eq 0 && "$(enq_count)" -eq 0 ]] || return 1
  [[ "$out" == *"snooze"* ]] || return 1
}

# ── RF-HT-03-6: stop cuts the bulletin (< 0.3 s) and kills the worker ──
case__radio_stop_cancels_playback() {
  sandbox_setup stop
  seed_default_roster
  # The p3 body render is slow, so the worker is mid-bulletin when stop lands.
  SLOW_TEXT="Body of p3" RENDER_SLEEP=5 in_host '
    run_radio
    sleep 1
    [[ -n "$(radio_dash_label)" ]] || exit 11     # RNF-HT-03-3 marker while live
    t0=$EPOCHREALTIME
    stop_audio >/dev/null
    t1=$EPOCHREALTIME
    printf "%s %s\n" "$t0" "$t1" > "$1"
    sleep 0.3
    [[ -z "$(radio_dash_label)" ]] || exit 12     # ... and gone after stop
    wait
  ' "$SANDBOX/times" || return 1
  local before
  before="$(enq_count)"
  # Items already queued (blocked pair + the p3 header) were cancelled by id.
  [[ "$before" -ge 3 ]] || return 1
  grep -q '^CANCEL --id radio-' "$STUB_LOG" || return 1
  local enq_id cancel_line
  enq_id="$(sed -n 's/^ENQ prio=[a-z]* id=\(radio-[^ ]*\) .*/\1/p' "$STUB_LOG" | head -n 1)"
  cancel_line="$(grep '^CANCEL ' "$STUB_LOG" | head -n 1)"
  [[ "$cancel_line" == *"--id $enq_id"* ]] || return 1
  # The worker is dead: the slow body never reaches the queue.
  sleep 0.5
  ! grep -q '^ENQ .*Body of p3' "$STUB_LOG" || return 1
  [[ "$(enq_count)" -eq "$before" ]] || return 1
  # < 0.3 s from stop_audio entry to return.
  local t0 t1
  read -r t0 t1 < "$SANDBOX/times"
  awk -v a="$t0" -v b="$t1" 'BEGIN{exit !((b - a) < 0.3)}' || return 1
}

# ── RF-HT-03-4: closing phrase and omitted-count formula ──
case__radio_omitted_count_formula() {
  sandbox_setup omitted
  seed_default_roster
  # PRD wording (Spanish), default cap 6: 3 attention chats read, 2 omitted.
  HERDR_TTS_LANG=es in_host 'run_radio; wait' >/dev/null 2>&1 || return 1
  [[ "$(enq_lines | tail -n 1)" == "working|2 chats más silenciosos omitidos"* ]] || return 1

  # Cap 2: the attention chats beyond the cap are counted as omitted too.
  sandbox_setup omitted_cap
  seed_default_roster
  HERDR_TTS_LANG=es TTS_RADIO_MAX_CHATS=2 in_host 'run_radio; wait' >/dev/null 2>&1 || return 1
  [[ "$(enq_count)" -eq 5 ]] || return 1          # 2 chats x (header + body) + closing
  [[ "$(enq_lines | tail -n 1)" == "working|3 chats más silenciosos omitidos"* ]] || return 1
  ! grep -q 'Fix login bug' "$STUB_LOG" || return 1   # third attention chat dropped

  # Singular grammar.
  sandbox_setup omitted_one
  set_agents "p1|done|claude|Only one" "p2|working|claude|Busy"
  HERDR_TTS_LANG=es in_host 'run_radio; wait' >/dev/null 2>&1 || return 1
  [[ "$(enq_lines | tail -n 1)" == "working|1 chat más silencioso omitido"* ]] || return 1

  # English default.
  sandbox_setup omitted_en
  set_agents "p1|done|claude|Only one" "p2|working|claude|Busy" "p3|idle|claude|Idle"
  in_host 'run_radio; wait' >/dev/null 2>&1 || return 1
  [[ "$(enq_lines | tail -n 1)" == "working|2 quieter chats omitted"* ]] || return 1

  # Nothing omitted => no closing phrase at all.
  sandbox_setup omitted_zero
  set_agents "p1|done|claude|Everything" "p2|blocked|claude|Needs you"
  in_host 'run_radio; wait' >/dev/null 2>&1 || return 1
  [[ "$(enq_count)" -eq 4 ]] || return 1
  ! enq_lines | grep -q '^working|' || return 1

  # A junk cap falls back to the default of 6.
  sandbox_setup omitted_junk_cap
  seed_default_roster
  TTS_RADIO_MAX_CHATS=banana in_host 'run_radio; wait' >/dev/null 2>&1 || return 1
  [[ "$(enq_count)" -eq 7 ]] || return 1
}

# ── RF-HT-03-7 / RF-HT-03-6: debounce, and re-press restarts with fresh data ──
case__radio_debounce_and_restart() {
  sandbox_setup debounce
  seed_default_roster
  in_host 'run_radio; wait' >/dev/null 2>&1 || return 1
  local first
  first="$(enq_count)"
  [[ "$first" -eq 7 ]] || return 1

  # Second press inside the 10 s window: ignored, with a visible notice.
  local out rc=0
  out="$(in_host 'run_radio; wait' 2>&1)" || rc=$?
  [[ $rc -eq 0 ]] || return 1
  [[ "$(enq_count)" -eq "$first" ]] || return 1
  [[ "$out" == *"10"* ]] || return 1
  ! grep -q '^CANCEL ' "$STUB_LOG" || return 1

  # Past the window the press cancels the previous bulletin (by id) and
  # re-triages with FRESH data: the blocked chat is gone now.
  set_agents "p1|done|claude|Fix login bug" "p3|done|codex|Refactor billing"
  TTS_RADIO_DEBOUNCE_SECONDS=0 in_host 'run_radio; wait' >/dev/null 2>&1 || return 1
  grep -q '^CANCEL --id radio-' "$STUB_LOG" || return 1
  [[ "$(enq_count)" -eq $(( first + 4 )) ]] || return 1   # 2 done chats x 2, nothing omitted
  [[ "$(enq_lines | sed -n "$(( first + 1 ))p")" == "done|Done: Refactor billing."* ]] || return 1
  local old_ids new_ids
  old_ids="$(sed -n 's/^ENQ prio=[a-z]* id=\(radio-[0-9a-z]*\)-[0-9]* .*/\1/p' "$STUB_LOG" | sort -u | wc -l)"
  [[ "$old_ids" -eq 2 ]] || return 1                       # two distinct run ids
}

# ── RF-HT-03-5: queue unavailable → sequential playback under the mutex ──
case__radio_sequential_fallback_without_queue() {
  sandbox_setup fallback
  seed_default_roster
  ENQUEUE_RC=1 in_host 'run_radio; wait' >/dev/null 2>&1 || return 1
  # One failed enqueue probe, then everything plays one after the other.
  [[ "$(enq_count)" -eq 1 ]] || return 1
  local -a plays=()
  mapfile -t plays < <(sed -n 's/^PLAY text=//p' "$STUB_LOG")
  [[ "${#plays[@]}" -eq 7 ]] || return 1
  [[ "${plays[0]}" == "Blocked: Migrate database schema."* ]] || return 1
  [[ "${plays[1]}" == "Body of p2."* ]] || return 1
  [[ "${plays[2]}" == "Done: Refactor billing."* ]] || return 1
  [[ "${plays[6]}" == *"quieter chats omitted"* ]] || return 1
  # The mutex is released when the bulletin ends.
  [[ ! -e "$SANDBOX/playing.lock" ]] || return 1
}

# ── expired state: no phantom cancel, leftovers swept, foreign dirs untouched ──
case__radio_expired_state_is_swept_without_cancel() {
  sandbox_setup stale
  local own="$SANDBOX/tmp/herdr-tts-radio.stale01" foreign="$SANDBOX/tmp/someone-elses-dir"
  mkdir -p "$own" "$foreign"
  printf 'RUNID=abc123\nPID=999999\nSTARTED=%s\nDIR=%s\nITEMS=3\n' \
    "$(( $(date +%s) - 7200 ))" "$own" > "$SANDBOX/state/herdr-tts/radio.state"
  local rc=0
  in_host 'radio_cancel' >/dev/null 2>&1 || rc=$?
  [[ $rc -eq 1 ]] || return 1                       # nothing live was cancelled
  [[ ! -e "$own" && ! -e "$SANDBOX/state/herdr-tts/radio.state" ]] || return 1
  ! grep -q '^CANCEL ' "$STUB_LOG" || return 1      # ids long gone: no daemon contact
  # A state file that points at a foreign directory never deletes it.
  printf 'RUNID=abc123\nPID=999999\nSTARTED=%s\nDIR=%s\nITEMS=3\n' \
    "$(date +%s)" "$foreign" > "$SANDBOX/state/herdr-tts/radio.state"
  in_host 'radio_cancel' >/dev/null 2>&1 || true
  [[ -d "$foreign" ]] || return 1
  grep -q '^CANCEL --id radio-abc123-1 --id radio-abc123-2 --id radio-abc123-3$' "$STUB_LOG" || return 1
}

# ── nothing to say ──
case__radio_without_attention_chats_is_silent() {
  sandbox_setup empty
  set_agents "p1|working|claude|Busy" "p2|idle|claude|Idle"
  local out rc=0
  out="$(in_host 'run_radio; wait' 2>&1)" || rc=$?
  [[ $rc -eq 0 ]] || return 1
  [[ "$(render_count)" -eq 0 && "$(enq_count)" -eq 0 ]] || return 1
  [[ "$out" == *"Radio"* ]] || return 1
  # No agents at all (herdr unreachable) fails open the same way.
  : > "$AGENTS_JSON"
  rc=0
  in_host 'run_radio; wait' >/dev/null 2>&1 || rc=$?
  [[ $rc -eq 0 ]] || return 1
  [[ "$(enq_count)" -eq 0 ]] || return 1
}

# ── RF-HT-03-3 / RF-HT-03-8: 40-char title, agent name + voice under HT-02 ──
case__radio_header_title_truncation_and_ht02_identity() {
  sandbox_setup header
  set_agents \
    "p1|done|claude|This chat title is considerably longer than forty characters" \
    "p2|done|codex|Short title"
  printf '2026-10-08T09:00:00\tp1\tclaude\t3.0\tx\n2026-10-08T09:30:00\tp2\tcodex\t3.0\ty\n' \
    > "$SANDBOX/state/herdr-tts/history.log"
  # HT-02 inactive (no voices.json): title only, global voice.
  in_host 'run_radio; wait' >/dev/null 2>&1 || return 1
  local header
  header="$(enq_lines | sed -n '3p')"
  [[ "$header" == "done|Done: This chat title is considerably longer …." ]] || return 1
  ! enq_lines | grep -q 'codex' || return 1

  # HT-02 active: agent name leads the header and the assigned voice is used.
  sandbox_setup header_ht02
  set_agents \
    "p1|done|claude|This chat title is considerably longer than forty characters" \
    "p2|done|codex|Short title"
  printf '2026-10-08T09:00:00\tp1\tclaude\t3.0\tx\n2026-10-08T09:30:00\tp2\tcodex\t3.0\ty\n' \
    > "$SANDBOX/state/herdr-tts/history.log"
  printf '{"agent":{"codex":"jorge"},"prefix":false}' > "$SANDBOX/config/herdr-tts/voices.json"
  in_host 'run_radio; wait' >/dev/null 2>&1 || return 1
  [[ "$(enq_lines | sed -n '1p')" == "done|Done: codex, Short title."* ]] || return 1
  [[ "$(enq_lines | sed -n '3p')" == "done|Done: claude, This chat title is considerably longer …."* ]] || return 1
  grep -q '^RENDER voice=jorge .*text=Done: codex, Short title\.' "$STUB_LOG" || return 1
  grep -q '^RENDER voice=jorge .*agent=codex session=sess-p2 text=Body of p2' "$STUB_LOG" || return 1
  ! grep -q '^RENDER voice=jorge .*Done: claude' "$STUB_LOG" || return 1
}

# ── RF-HT-03-1: keymap command id, no default chord ──
case__radio_keymap_command_id() {
  sandbox_setup keymap
  local km="$SANDBOX/config/herdr-tts/keymap.json"
  in_host 'keymap_is_known_id radio' || return 1
  [[ "$(in_host 'keymap_cmd_for radio')" == "herdr-tts --radio" ]] || return 1
  in_host 'keymap_default_json' > "$km" || return 1
  jq -e '.bindings | has("radio") and .radio == null' "$km" >/dev/null || return 1
  in_host 'keymap_check --json' > "$SANDBOX/check.json" || return 1
  jq -e '.ok == true' "$SANDBOX/check.json" >/dev/null || return 1
  # Bound like any sibling id, emit renders the CLI invocation.
  jq '.bindings.radio = "prefix+o"' "$km" > "$km.tmp" && mv "$km.tmp" "$km"
  in_host 'keymap_check --json' > "$SANDBOX/check.json" || return 1
  jq -e '.ok == true and .error_count == 0' "$SANDBOX/check.json" >/dev/null || return 1
  in_host 'keymap_emit direct' > "$SANDBOX/emit.out" 2>&1 || return 1
  grep -q 'herdr-tts --radio' "$SANDBOX/emit.out" || return 1
}

# ── CLI wiring: `herdr-tts --radio` (real dispatch, detached worker) ──
case__radio_cli_flag_dispatch() {
  sandbox_setup cli
  seed_default_roster
  local rc=0 i
  PATH="$SANDBOX/bin:$PATH" \
  XDG_DATA_HOME="$SANDBOX/data" XDG_CONFIG_HOME="$SANDBOX/config" \
  XDG_STATE_HOME="$SANDBOX/state" STUB_LOG="$STUB_LOG" AGENTS_JSON="$AGENTS_JSON" \
  TMPDIR="$SANDBOX/tmp" HERDR_TTS_SMOKE=1 \
  HERDR_TTS_LOCK_FILE="$SANDBOX/playing.lock" HERDR_TTS_PID_FILE="$SANDBOX/current.pid" \
  HERDR_TTS_IPC_SOCKET="$SANDBOX/player.sock" HERDR_TTS_SNOOZE_FILE="$SNOOZE_JSON" \
  HERDR_TTS_CONFIG_FILE="$SANDBOX/config/herdr-tts/config.env" \
  HERDR_TTS_VOICES_FILE="$SANDBOX/config/herdr-tts/voices.json" \
  HERDR_TTS_DAEMON_LOG="$SANDBOX/state/herdr-tts/daemon.log" \
    timeout 10 bash "$REPO_HOST_DIR/bin/herdr-tts" --radio >/dev/null 2>&1 || rc=$?
  [[ $rc -eq 0 ]] || return 1
  # The key press returns at once; the detached worker finishes on its own.
  for i in $(seq 1 100); do
    [[ "$(enq_count)" -ge 7 ]] && break
    sleep 0.1
  done
  [[ "$(enq_count)" -eq 7 ]] || return 1
  [[ "$(enq_lines | sed -n '1p')" == "blocked|Blocked: Migrate database schema."* ]] || return 1
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
