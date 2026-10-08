#!/usr/bin/env bash
# voice-stack F4 U3 — push-to-talk (ptt) plugin wiring test harness
# Protocol: CASE <name> START / OK | FAIL
#
# Everything runs against fakes: the pinned engine venv python is a stub
# that implements the `agent_tts.stt.cli capture|transcribe` contract, and
# `herdr` on PATH is a fake that answers `api snapshot` and logs pane
# verbs argv-by-argv. No microphone, no network, no models.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_HOST_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"   # hosts/herdr/tts-plugin

SANDBOX_ROOT="$(mktemp -d)"
trap 'rm -rf "$SANDBOX_ROOT"' EXIT

sandbox_setup() {
  # Cases tune the stubs via exported knobs; reset them here so no case
  # inherits another's failure injection (they run in sort order).
  unset CAPTURE_FAIL_MSG CAPTURE_EXIT TRANSCRIBE_OUT TRANSCRIBE_FAIL_MSG \
    TRANSCRIBE_EXIT STUB_MODULE_MISSING
  SANDBOX="$SANDBOX_ROOT/$1"
  mkdir -p "$SANDBOX/bin" "$SANDBOX/data/herdr-tts/venv/bin" \
    "$SANDBOX/config/herdr-tts" "$SANDBOX/state/herdr-tts" "$SANDBOX/tmp"
  STUB_LOG="$SANDBOX/engine.log"
  HERDR_LOG="$SANDBOX/herdr.log"
  # Engine stub: the plugin must reach the STT CLI through its pinned venv
  # (`$VENV_PYTHON -m agent_tts.stt.cli <subcmd> ...`). The subcommand sits
  # right after the module path; capture writes the --out file (unless
  # CAPTURE_FAIL_MSG is set), transcribe prints one JSON line (unless
  # TRANSCRIBE_FAIL_MSG is set).
  cat > "$SANDBOX/data/herdr-tts/venv/bin/python" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STUB_LOG"
subcmd=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  if [[ "${args[$i]}" == "agent_tts.stt.cli" ]]; then
    subcmd="${args[$((i + 1))]}"
  fi
done
case "$subcmd" in
  capture)
    if [[ -n "${STUB_MODULE_MISSING:-}" ]]; then
      echo "No module named 'agent_tts.stt'" >&2
      exit 1
    fi
    if [[ -n "${CAPTURE_FAIL_MSG:-}" ]]; then
      printf '%s\n' "$CAPTURE_FAIL_MSG"
      echo "agent-tts-stt: capture failed" >&2
      exit "${CAPTURE_EXIT:-1}"
    fi
    out=""
    for ((i = 0; i < ${#args[@]}; i++)); do
      if [[ "${args[$i]}" == "--out" ]]; then
        out="${args[$((i + 1))]}"
      fi
    done
    [[ -n "$out" ]] && printf 'RIFFstubwav-bytes' > "$out"
    exit 0
    ;;
  transcribe)
    if [[ -n "${TRANSCRIBE_FAIL_MSG:-}" ]]; then
      printf '%s\n' "$TRANSCRIBE_FAIL_MSG"
      echo "agent-tts-stt: transcribe failed" >&2
      exit "${TRANSCRIBE_EXIT:-5}"
    fi
    if [[ -n "${TRANSCRIBE_OUT:-}" ]]; then
      printf '%s\n' "$TRANSCRIBE_OUT"
    else
      printf '%s\n' '{"ok": true, "text": "hola mundo dictado"}'
    fi
    exit 0
    ;;
  *)
    echo "ptt stub: unexpected subcommand '$subcmd'" >&2
    exit 2
    ;;
esac
STUB
  chmod +x "$SANDBOX/data/herdr-tts/venv/bin/python"
  # Fake herdr: snapshot answers a focused pane; every other invocation is
  # logged argv-by-argv so the literal send-text payload is provable.
  cat > "$SANDBOX/bin/herdr" <<'EOF'
#!/usr/bin/env bash
if [[ "$1 $2" == "api snapshot" ]]; then
  printf '{"result":{"snapshot":{"focused_pane_id":"pane-7"}}}'
  exit 0
fi
for arg in "$@"; do
  printf '[%s]' "$arg"
done >> "$HERDR_LOG"
printf '\n' >> "$HERDR_LOG"
exit 0
EOF
  chmod +x "$SANDBOX/bin/herdr"
}

in_host() {
  local snippet="$1"
  shift
  PATH="$SANDBOX/bin:$PATH" \
  XDG_DATA_HOME="$SANDBOX/data" XDG_CONFIG_HOME="$SANDBOX/config" \
  XDG_STATE_HOME="$SANDBOX/state" STUB_LOG="$STUB_LOG" HERDR_LOG="$HERDR_LOG" \
  TMPDIR="$SANDBOX/tmp" \
  STUB_MODULE_MISSING="${STUB_MODULE_MISSING:-}" \
  CAPTURE_FAIL_MSG="${CAPTURE_FAIL_MSG:-}" CAPTURE_EXIT="${CAPTURE_EXIT:-0}" \
  TRANSCRIBE_OUT="${TRANSCRIBE_OUT:-}" \
  TRANSCRIBE_FAIL_MSG="${TRANSCRIBE_FAIL_MSG:-}" TRANSCRIBE_EXIT="${TRANSCRIBE_EXIT:-0}" \
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

engine_calls() { [[ -f "$STUB_LOG" ]] && cat "$STUB_LOG" || true; }
herdr_calls() { [[ -f "$HERDR_LOG" ]] && cat "$HERDR_LOG" || true; }
no_wav_left() { ! ls "$SANDBOX/tmp"/*.wav >/dev/null 2>&1; }
no_injection() { ! herdr_calls | grep -q 'send-text'; }

# ── F4.9: both toggles off (defaults) ⇒ inert, notice, nonzero ──
case__ptt_defaults_both_off_inert() {
  sandbox_setup both_off
  local out rc=0
  out="$(in_host 'run_ptt' 2>&1)" || rc=$?
  [[ $rc -ne 0 ]] || return 1
  [[ "$out" == *"TTS_PTT"* ]] || return 1
  [[ -z "$(engine_calls)" ]] || return 1   # no engine process at all
  no_injection || return 1
}

case__ptt_stt_off_alone_blocks() {
  sandbox_setup stt_off
  local out rc=0
  out="$(in_host 'TTS_PTT=on run_ptt')" || rc=$?
  [[ $rc -ne 0 ]] || return 1
  [[ "$out" == *"TTS_STT"* ]] || return 1  # names the off switch
  [[ -z "$(engine_calls)" ]] || return 1
  no_injection || return 1
}

case__ptt_ptt_off_alone_blocks() {
  sandbox_setup ptt_off
  local out rc=0
  out="$(in_host 'TTS_STT=on run_ptt')" || rc=$?
  [[ $rc -ne 0 ]] || return 1
  [[ "$out" == *"TTS_PTT"* ]] || return 1
  [[ -z "$(engine_calls)" ]] || return 1
  no_injection || return 1
}

# ── engine failure paths: visible message, nothing injected, nonzero ──
case__ptt_capture_engine_failure_notice() {
  sandbox_setup cap_fail
  export CAPTURE_FAIL_MSG='{"ok": false, "error": {"kind": "capture_unavailable", "message": "no microphone bridge"}}'
  export CAPTURE_EXIT=1
  local out rc=0
  out="$(in_host 'TTS_STT=on TTS_PTT=on run_ptt')" || rc=$?
  [[ $rc -ne 0 ]] || return 1
  [[ "$out" == *"no microphone bridge"* ]] || return 1
  no_injection || return 1
  no_wav_left || return 1                    # temp capture cleaned on failure
  [[ ! -f "$SANDBOX/state/herdr-tts/daemon.log" ]] \
    || ! grep -q '^.*ptt' "$SANDBOX/state/herdr-tts/daemon.log" || return 1
}

case__ptt_transcribe_worker_unavailable_notice() {
  sandbox_setup worker_down
  export TRANSCRIBE_FAIL_MSG='{"ok": false, "error": {"kind": "worker_unavailable", "message": "STT worker is not running"}}'
  export TRANSCRIBE_EXIT=5
  local out rc=0
  out="$(in_host 'TTS_STT=on TTS_PTT=on run_ptt')" || rc=$?
  [[ $rc -ne 0 ]] || return 1
  [[ "$out" == *"STT worker is not running"* ]] || return 1
  no_injection || return 1
  no_wav_left || return 1
}

case__ptt_missing_engine_notice() {
  sandbox_setup engine_gone
  local out rc=0
  out="$(in_host 'VENV_PYTHON="/nonexistent/venv-python" TTS_STT=on TTS_PTT=on run_ptt')" || rc=$?
  [[ $rc -ne 0 ]] || return 1
  [[ "$out" == *"engine"* ]] || return 1
  [[ -z "$(engine_calls)" ]] || return 1
  no_injection || return 1
}

case__ptt_engine_module_missing_notice() {
  sandbox_setup module_gone
  export STUB_MODULE_MISSING=1
  local out rc=0
  out="$(in_host 'TTS_STT=on TTS_PTT=on run_ptt')" || rc=$?
  [[ $rc -ne 0 ]] || return 1
  [[ "$out" == *"agent_tts.stt"* ]] || return 1  # engine's own message surfaces
  no_injection || return 1
  no_wav_left || return 1
}

# ── guards: empty / <2 visible chars ⇒ warning, no injection ──
case__ptt_guard_empty_and_short() {
  sandbox_setup guard
  export TRANSCRIBE_OUT='{"ok": true, "text": ""}'
  local out rc=0
  out="$(printf '\n' | in_host 'TTS_STT=on TTS_PTT=on run_ptt' 2>&1)" || rc=$?
  [[ $rc -ne 0 ]] || return 1
  [[ "$out" == *"under 2"* ]] || return 1    # the guard warning itself
  no_injection || return 1
  no_wav_left || return 1

  export TRANSCRIBE_OUT='{"ok": true, "text": " a "}'   # 1 visible char
  rc=0
  out="$(printf '\n' | in_host 'TTS_STT=on TTS_PTT=on run_ptt' 2>&1)" || rc=$?
  [[ $rc -ne 0 ]] || return 1
  [[ "$out" == *"under 2"* ]] || return 1
  no_injection || return 1
}

# ── injection: literal send-text, Enter per TTS_PTT_ENTER, log, cleanup ──
case__ptt_inject_literal_enter_never() {
  sandbox_setup never_mode
  local out rc=0
  out="$(printf '\n' | in_host 'TTS_STT=on TTS_PTT=on TTS_PTT_ENTER=never run_ptt')" || rc=$?
  [[ $rc -eq 0 ]] || return 1
  # Literal text as ONE argv, exactly `pane send-text <pane> <text>`
  herdr_calls | grep -qxF '[pane][send-text][pane-7][hola mundo dictado]' || return 1
  # never ⇒ no Enter key event
  ! herdr_calls | grep -q 'send-keys' || return 1
  no_wav_left || return 1
  # daemon.log: exactly one ptt line with duration_s, chars, pane — never the text
  local dlog="$SANDBOX/state/herdr-tts/daemon.log"
  [[ "$(grep -c 'ptt' "$dlog")" -eq 1 ]] || return 1
  grep -Eq 'duration_s=[0-9]+([.][0-9]+)? chars=[0-9]+ pane=pane-7' "$dlog" || return 1
  ! grep -q 'hola' "$dlog" || return 1
}

case__ptt_inject_enter_always() {
  sandbox_setup always_mode
  local rc=0
  printf '\n' | in_host 'TTS_STT=on TTS_PTT=on TTS_PTT_ENTER=always run_ptt' >/dev/null || rc=$?
  [[ $rc -eq 0 ]] || return 1
  herdr_calls | grep -qxF '[pane][send-text][pane-7][hola mundo dictado]' || return 1
  herdr_calls | grep -qxF '[pane][send-keys][pane-7][Enter]' || return 1
}

case__ptt_inject_enter_ask() {
  sandbox_setup ask_mode
  # y ⇒ Enter sent
  local rc=0
  printf '\ny' | in_host 'TTS_STT=on TTS_PTT=on TTS_PTT_ENTER=ask run_ptt' >/dev/null || rc=$?
  [[ $rc -eq 0 ]] || return 1
  herdr_calls | grep -qxF '[pane][send-keys][pane-7][Enter]' || return 1
  # any other key ⇒ no Enter
  rm -f "$HERDR_LOG"
  rc=0
  printf '\nn' | in_host 'TTS_STT=on TTS_PTT=on TTS_PTT_ENTER=ask run_ptt' >/dev/null || rc=$?
  [[ $rc -eq 0 ]] || return 1
  herdr_calls | grep -qxF '[pane][send-text][pane-7][hola mundo dictado]' || return 1
  ! herdr_calls | grep -q 'send-keys' || return 1
}

case__ptt_cancel_esc() {
  sandbox_setup cancel
  local out rc=0
  out="$(printf '\033' | in_host 'TTS_STT=on TTS_PTT=on run_ptt')" || rc=$?
  [[ $rc -eq 0 ]] || return 1          # user cancel is not an error
  [[ "$out" == *"cancel"* ]] || return 1
  no_injection || return 1
  no_wav_left || return 1
}

case__ptt_recapture_accept_second_attempt() {
  sandbox_setup recapture
  local rc=0
  printf 'r\n' | in_host 'TTS_STT=on TTS_PTT=on run_ptt' >/dev/null || rc=$?
  [[ $rc -eq 0 ]] || return 1
  # 'stt.cli capture' — a bare 'capture' also matches this sandbox's
  # directory name (recapture) inside every logged argv.
  [[ "$(engine_calls | grep -c 'stt.cli capture')" -eq 2 ]] || return 1
  herdr_calls | grep -qxF '[pane][send-text][pane-7][hola mundo dictado]' || return 1
}

case__ptt_recapture_gives_up_after_three() {
  sandbox_setup giveup
  local out rc=0
  out="$(printf 'rrr' | in_host 'TTS_STT=on TTS_PTT=on run_ptt')" || rc=$?
  [[ $rc -ne 0 ]] || return 1          # nothing injected: nonzero per guard paths
  [[ "$out" == *"3"* ]] || return 1    # notice names the attempt budget
  [[ "$(engine_calls | grep -c 'stt.cli capture')" -eq 3 ]] || return 1
  no_injection || return 1
  no_wav_left || return 1
}

# ── capture CLI pass-through of the configured seconds ──
case__ptt_passthrough_seconds() {
  sandbox_setup passthrough
  local rc=0
  printf '\n' | in_host 'TTS_STT=on TTS_PTT=on TTS_PTT_SILENCE_SECONDS=2.5 TTS_PTT_MAX_SECONDS=45 run_ptt' >/dev/null || rc=$?
  [[ $rc -eq 0 ]] || return 1
  engine_calls | grep -q -- '--silence-seconds 2.5' || return 1
  engine_calls | grep -q -- '--max-seconds 45' || return 1
  # defaults reach the CLI when nothing is configured
  rm -f "$STUB_LOG"
  rc=0
  printf '\n' | in_host 'TTS_STT=on TTS_PTT=on run_ptt' >/dev/null || rc=$?
  [[ $rc -eq 0 ]] || return 1
  engine_calls | grep -q -- '--silence-seconds 1.2' || return 1
  engine_calls | grep -q -- '--max-seconds 30' || return 1
}

# ── keymap: id `ptt` registered like its siblings, no default chord ──
case__ptt_keymap_id_registered() {
  sandbox_setup keymap
  in_host 'keymap_is_known_id ptt' || return 1
  [[ "$(in_host 'keymap_cmd_for ptt')" == "herdr-tts ptt" ]] || return 1
  in_host 'keymap_ctrlalt_for ptt' >/dev/null 2>&1 && return 1  # no suggested chord
  keymap_default_json_has_ptt_null() {
    in_host 'keymap_default_json' | jq -e '.bindings.ptt == null' >/dev/null
  }
  keymap_default_json_has_ptt_null || return 1

  cat > "$SANDBOX/config/herdr-tts/keymap.json" <<'EOF'
{
  "style": "direct",
  "bindings": {
    "ptt": "ctrl+alt+shift+p"
  }
}
EOF
  local emitted
  emitted="$(in_host 'keymap_emit')" || return 1
  [[ "$emitted" == *'command = "herdr-tts ptt"'* ]] || return 1
  [[ "$emitted" == *'key = "ctrl+alt+shift+p"'* ]] || return 1
}

# ── config: managed keys, admission, strict degradation ──
case__ptt_config_set_managed_strict() {
  sandbox_setup cfg
  local cfg_file="$SANDBOX/config/herdr-tts/config.env"
  in_host 'config_set TTS_STT on' || return 1
  in_host 'config_set TTS_PTT on' || return 1
  in_host 'config_set TTS_PTT_ENTER always' || return 1
  in_host 'config_set TTS_PTT_SILENCE_SECONDS 2.5' || return 1
  in_host 'config_set TTS_PTT_MAX_SECONDS 45' || return 1
  grep -q '^TTS_STT="on"$' "$cfg_file" || return 1
  grep -q '^TTS_PTT="on"$' "$cfg_file" || return 1
  grep -q '^TTS_PTT_ENTER="always"$' "$cfg_file" || return 1
  grep -q '^TTS_PTT_SILENCE_SECONDS="2.5"$' "$cfg_file" || return 1
  grep -q '^TTS_PTT_MAX_SECONDS="45"$' "$cfg_file" || return 1

  # junk values are refused and leave the file untouched
  local before; before="$(cat "$cfg_file")"
  local rc=0
  in_host 'config_set TTS_STT banana' 2>/dev/null || rc=$?
  [[ $rc -ne 0 ]] || return 1
  rc=0; in_host 'config_set TTS_PTT maybe' 2>/dev/null || rc=$?
  [[ $rc -ne 0 ]] || return 1
  rc=0; in_host 'config_set TTS_PTT_ENTER sometimes' 2>/dev/null || rc=$?
  [[ $rc -ne 0 ]] || return 1
  rc=0; in_host 'config_set TTS_PTT_SILENCE_SECONDS abc' 2>/dev/null || rc=$?
  [[ $rc -ne 0 ]] || return 1
  rc=0; in_host 'config_set TTS_PTT_SILENCE_SECONDS 0' 2>/dev/null || rc=$?
  [[ $rc -ne 0 ]] || return 1
  rc=0; in_host 'config_set TTS_PTT_MAX_SECONDS -3' 2>/dev/null || rc=$?
  [[ $rc -ne 0 ]] || return 1
  [[ "$(cat "$cfg_file")" == "$before" ]] || return 1
}

case__ptt_config_loader_strict_degrades() {
  sandbox_setup loader
  cat > "$SANDBOX/config/herdr-tts/config.env" <<'EOF'
TTS_STT="banana"
TTS_PTT="maybe"
TTS_PTT_ENTER="sometimes"
TTS_PTT_SILENCE_SECONDS="abc"
TTS_PTT_MAX_SECONDS="-3"
EOF
  in_host '[[ "$TTS_STT" == "off" ]]' || return 1
  in_host '[[ "$TTS_PTT" == "off" ]]' || return 1
  in_host '[[ "$TTS_PTT_ENTER" == "ask" ]]' || return 1
  in_host '[[ "$TTS_PTT_SILENCE_SECONDS" == "1.2" ]]' || return 1
  in_host '[[ "$TTS_PTT_MAX_SECONDS" == "30" ]]' || return 1
}

# ── settings popup: cycle tables + voice-category rows persist ──
case__ptt_settings_cycle_and_rows() {
  sandbox_setup settings
  [[ "$(in_host 'settings_cycle_value stt off')" == "on" ]] || return 1
  [[ "$(in_host 'settings_cycle_value stt on')" == "off" ]] || return 1
  [[ "$(in_host 'settings_cycle_value ptt off')" == "on" ]] || return 1
  in_host 'SETTINGS_CATEGORY=voice settings_handle_key s' || return 1
  grep -q '^TTS_STT="on"$' "$SANDBOX/config/herdr-tts/config.env" || return 1
  in_host 'SETTINGS_CATEGORY=voice settings_handle_key d' || return 1
  grep -q '^TTS_PTT="on"$' "$SANDBOX/config/herdr-tts/config.env" || return 1
  # rendered voice view carries one row per toggle
  local frame
  frame="$(in_host 'SETTINGS_CATEGORY=voice settings_render')" || return 1
  [[ "$frame" == *"STT engine:"*"Dictation (PTT):"* ]] || return 1
}

# ── triangulation: injection failure is visible; payload is literal ──
case__ptt_injection_failure_visible() {
  sandbox_setup inject_fail
  # This fake herdr rejects send-text: the failure must be visible and
  # no Enter may follow a failed injection.
  cat > "$SANDBOX/bin/herdr" <<'EOF'
#!/usr/bin/env bash
if [[ "$1 $2" == "api snapshot" ]]; then
  printf '{"result":{"snapshot":{"focused_pane_id":"pane-7"}}}'
  exit 0
fi
if [[ "$1 $2" == "pane send-text" ]]; then
  echo "herdr: pane is gone" >&2
  exit 1
fi
for arg in "$@"; do
  printf '[%s]' "$arg"
done >> "$HERDR_LOG"
printf '\n' >> "$HERDR_LOG"
exit 0
EOF
  chmod +x "$SANDBOX/bin/herdr"
  local out rc=0
  out="$(printf '\n' | in_host 'TTS_STT=on TTS_PTT=on TTS_PTT_ENTER=always run_ptt' 2>&1)" || rc=$?
  [[ $rc -ne 0 ]] || return 1
  [[ "$out" == *"pane-7"* ]] || return 1   # names the failed pane
  ! herdr_calls | grep -q 'send-keys' || return 1
  [[ ! -f "$SANDBOX/state/herdr-tts/daemon.log" ]] \
    || ! grep -q 'ptt' "$SANDBOX/state/herdr-tts/daemon.log" || return 1
}

case__ptt_payload_is_literal_never_evaluated() {
  sandbox_setup literal
  export TRANSCRIBE_OUT='{"ok": true, "text": "hola \"mundo\" $(rm -rf /tmp/nope) `id`"}'
  local rc=0
  printf '\n' | in_host 'TTS_STT=on TTS_PTT=on TTS_PTT_ENTER=never run_ptt' >/dev/null || rc=$?
  [[ $rc -eq 0 ]] || return 1
  herdr_calls | grep -qxF '[pane][send-text][pane-7][hola "mundo" $(rm -rf /tmp/nope) `id`]' || return 1
  [[ ! -e /tmp/nope ]] || return 1         # nothing was ever evaluated
  ! herdr_calls | grep -q 'send-keys' || return 1
}

# ── CLI verb: `herdr-tts ptt` dispatches the flow (executed, not sourced) ──
case__ptt_cli_verb_dispatch() {
  sandbox_setup cli_verb
  local out rc=0
  # `timeout` guards the RED phase: an unrecognized verb used to fall
  # through to the watcher daemon (infinite loop) instead of failing.
  out="$(PATH="$SANDBOX/bin:$PATH" \
    XDG_DATA_HOME="$SANDBOX/data" XDG_CONFIG_HOME="$SANDBOX/config" \
    XDG_STATE_HOME="$SANDBOX/state" TMPDIR="$SANDBOX/tmp" STUB_LOG="$STUB_LOG" \
    HERDR_LOG="$HERDR_LOG" HERDR_TTS_SMOKE=1 \
    HERDR_TTS_CONFIG_FILE="$SANDBOX/config/herdr-tts/config.env" \
    HERDR_TTS_DAEMON_LOG="$SANDBOX/state/herdr-tts/daemon.log" \
    timeout 10 bash "$REPO_HOST_DIR/bin/herdr-tts" ptt)" || rc=$?
  [[ $rc -ne 0 ]] || return 1                  # defaults: both off
  [[ "$out" == *"TTS_PTT"* ]] || return 1
  [[ -z "$(engine_calls)" ]] || return 1
  no_injection || return 1
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
