#!/usr/bin/env bash
# voice-stack MQ-05 — voice identity and map test harness
# Protocol: CASE <name> START / OK | FAIL
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_HOST_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"   # hosts/herdr/tts-plugin

SANDBOX_ROOT="$(mktemp -d)"
trap 'rm -rf "$SANDBOX_ROOT"' EXIT

sandbox_setup() {
  SANDBOX="$SANDBOX_ROOT/$1"
  mkdir -p "$SANDBOX/data/herdr-tts/venv/bin" "$SANDBOX/config/herdr-tts" "$SANDBOX/state/herdr-tts" "$SANDBOX/cache/herdr-tts"
  STUB_LOG="$SANDBOX/stub.log"
  cat > "$SANDBOX/data/herdr-tts/venv/bin/python" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STUB_LOG"
prev=""
for arg in "$@"; do
  if [[ "$prev" == "--output" ]]; then
    mkdir -p "$(dirname "$arg")"
    echo "fake-audio" > "$arg"
  fi
  prev="$arg"
done
if [[ -n "${STUB_OUT:-}" ]]; then echo "$STUB_OUT"; fi
exit "${STUB_EXIT:-0}"
STUB
  chmod +x "$SANDBOX/data/herdr-tts/venv/bin/python"
}

in_host() {
  local snippet="$1"
  shift
  XDG_DATA_HOME="$SANDBOX/data" XDG_CONFIG_HOME="$SANDBOX/config" \
  XDG_STATE_HOME="$SANDBOX/state" XDG_CACHE_HOME="$SANDBOX/cache" STUB_LOG="$STUB_LOG" \
  STUB_OUT="${STUB_OUT:-}" STUB_EXIT="${STUB_EXIT:-0}" \
  HERDR_TTS_CONFIG_FILE="$SANDBOX/config/herdr-tts/config.env" \
  HERDR_TTS_VOICES_FILE="$SANDBOX/config/herdr-tts/voices.json" \
  bash -c 'HOST="$1"; shift; ARGS=("$@"); set --; source "$HOST/bin/herdr-tts"; set -- "${ARGS[@]}"; '"$snippet" \
    _ "$REPO_HOST_DIR" "$@"
}

case__voice_resolution_pane_over_agent_over_global() {
  sandbox_setup resolution
  cat > "$SANDBOX/config/herdr-tts/voices.json" <<'EOF'
{
  "pane": {
    "10": "voice_pane_specific"
  },
  "agent": {
    "claude": "voice_agent_claude",
    "opencode": "voice_agent_opencode"
  }
}
EOF
  # 1. pane over agent: pane 10 with agent claude must resolve to voice_pane_specific
  local v_pane
  v_pane="$(in_host 'TTS_VOICE=global_default resolve_voice "$1" "$2"' 10 claude)" || return 1
  [[ "$v_pane" == "voice_pane_specific" ]] || return 1

  # 2. agent over global: pane 20 (not in pane map) with agent claude must resolve to voice_agent_claude
  local v_agent
  v_agent="$(in_host 'TTS_VOICE=global_default resolve_voice "$1" "$2"' 20 claude)" || return 1
  [[ "$v_agent" == "voice_agent_claude" ]] || return 1

  # 3. global fallback: pane 20 with unknown agent must resolve to global_default
  local v_fallback
  v_fallback="$(in_host 'TTS_VOICE=global_default resolve_voice "$1" "$2"' 20 unknown_agent)" || return 1
  [[ "$v_fallback" == "global_default" ]] || return 1

  # 4. global fallback without agent argument
  local v_noagent
  v_noagent="$(in_host 'TTS_VOICE=global_default resolve_voice "$1"' 30)" || return 1
  [[ "$v_noagent" == "global_default" ]] || return 1
}

case__voice_map_malformed_fail_open() {
  sandbox_setup malformed

  # Corrupted JSON syntax
  echo "{ invalid json : 42" > "$SANDBOX/config/herdr-tts/voices.json"
  local v1
  v1="$(in_host 'TTS_VOICE=global_safe resolve_voice "$1" "$2"' 10 claude)" || return 1
  [[ "$v1" == "global_safe" ]] || return 1

  # Non-object JSON
  echo '"not an object"' > "$SANDBOX/config/herdr-tts/voices.json"
  local v2
  v2="$(in_host 'TTS_VOICE=global_safe resolve_voice "$1" "$2"' 10 claude)" || return 1
  [[ "$v2" == "global_safe" ]] || return 1
}

case__voice_for_set_and_clear() {
  sandbox_setup set_clear
  local vfile="$SANDBOX/config/herdr-tts/voices.json"

  # Assign voice for agent
  in_host 'voice_map_set agent claude elvira' || return 1
  [[ "$(jq -r '.agent.claude' "$vfile")" == "elvira" ]] || return 1

  # Assign voice for pane
  in_host 'voice_map_set pane 42 jorge' || return 1
  [[ "$(jq -r '.pane["42"]' "$vfile")" == "jorge" ]] || return 1
  [[ "$(jq -r '.agent.claude' "$vfile")" == "elvira" ]] || return 1

  # Clear voice for agent with "off"
  in_host 'voice_map_set agent claude off' || return 1
  [[ "$(jq -r '.agent.claude // "none"' "$vfile")" == "none" ]] || return 1
  [[ "$(jq -r '.pane["42"]' "$vfile")" == "jorge" ]] || return 1

  # Clear voice for pane with "off"
  in_host 'voice_map_set pane 42 off' || return 1
  [[ "$(jq -r '.pane["42"] // "none"' "$vfile")" == "none" ]] || return 1

  # Also test through CLI dispatch (--voice-for)
  XDG_DATA_HOME="$SANDBOX/data" XDG_CONFIG_HOME="$SANDBOX/config" \
  XDG_STATE_HOME="$SANDBOX/state" HERDR_TTS_VOICES_FILE="$vfile" \
  HERDR_TTS_CONFIG_FILE="$SANDBOX/config/herdr-tts/config.env" \
    bash "$REPO_HOST_DIR/bin/herdr-tts" --voice-for agent codex custom_voice >/dev/null 2>&1 || return 1
  [[ "$(jq -r '.agent.codex' "$vfile")" == "custom_voice" ]] || return 1

  XDG_DATA_HOME="$SANDBOX/data" XDG_CONFIG_HOME="$SANDBOX/config" \
  XDG_STATE_HOME="$SANDBOX/state" HERDR_TTS_VOICES_FILE="$vfile" \
  HERDR_TTS_CONFIG_FILE="$SANDBOX/config/herdr-tts/config.env" \
    bash "$REPO_HOST_DIR/bin/herdr-tts" --voice-for agent codex off >/dev/null 2>&1 || return 1
  [[ "$(jq -r '.agent.codex // "none"' "$vfile")" == "none" ]] || return 1
}

case__voice_auto_assign_deterministic() {
  sandbox_setup auto_assign
  local vfile="$SANDBOX/config/herdr-tts/voices.json"
  echo '{"auto_assign": true}' > "$vfile"

  # Stub returns known palette for provider "edge"
  STUB_OUT='{"providers":["edge"],"voices":{"edge":["alpha","beta","gamma","delta"]}}' \
  in_host '
    TTS_PROVIDER=edge
    a1=$(voice_auto_assign claude)
    a2=$(voice_auto_assign claude)
    [[ "$a1" == "$a2" ]] || exit 1
    [[ -n "$a1" ]] || exit 1

    b1=$(voice_auto_assign opencode)
    b2=$(voice_auto_assign opencode)
    [[ "$b1" == "$b2" ]] || exit 1
    [[ -n "$b1" ]] || exit 1

    # resolve_voice with auto_assign: true delegates to voice_auto_assign
    r1=$(resolve_voice 99 claude)
    r2=$(resolve_voice 99 claude)
    [[ "$r1" == "$a1" ]] || exit 1
    [[ "$r2" == "$a1" ]] || exit 1
  ' || return 1
}

case__voice_preview_creates_cached_sample() {
  sandbox_setup preview_cache
  local expected_sample="$SANDBOX/cache/herdr-tts/voice-previews/edge/ximena.mp3"

  # 1. Synthesize preview for voice "ximena"
  in_host 'TTS_PROVIDER=edge voice_preview ximena' || return 1

  # Assert cached MP3 file was created and is non-empty
  [[ -s "$expected_sample" ]] || return 1

  # Assert stub python was invoked with required synthesis flags
  grep -q -- "--voice ximena" "$STUB_LOG" || return 1
  grep -q -- "--output $expected_sample" "$STUB_LOG" || return 1
  grep -q -- "--no-play" "$STUB_LOG" || return 1

  # 2. Re-invoking preview on already cached voice should play immediately without re-synthesizing
  local log_len_before log_len_after
  log_len_before=$(wc -l < "$STUB_LOG")
  in_host 'TTS_PROVIDER=edge voice_preview ximena' || return 1
  log_len_after=$(wc -l < "$STUB_LOG")

  # Playback invokes play-file, but no new --output synthesis line should appear
  local diff_log
  diff_log=$(tail -n $(( log_len_after - log_len_before )) "$STUB_LOG")
  ! grep -q -- "--no-play" <<< "$diff_log" || return 1

  # 3. Preview "(global)" resolves to $TTS_VOICE
  in_host 'TTS_PROVIDER=edge TTS_VOICE=elvira voice_preview "(global)"' || return 1
  [[ -s "$SANDBOX/cache/herdr-tts/voice-previews/edge/elvira.mp3" ]] || return 1

  # 4. Preview via CLI flag --preview-voice
  XDG_DATA_HOME="$SANDBOX/data" XDG_CONFIG_HOME="$SANDBOX/config" \
  XDG_STATE_HOME="$SANDBOX/state" XDG_CACHE_HOME="$SANDBOX/cache" \
  HERDR_TTS_CONFIG_FILE="$SANDBOX/config/herdr-tts/config.env" \
  HERDR_TTS_VOICES_FILE="$SANDBOX/config/herdr-tts/voices.json" \
    bash "$REPO_HOST_DIR/bin/herdr-tts" --preview-voice dalia >/dev/null 2>&1 || return 1
  [[ -s "$SANDBOX/cache/herdr-tts/voice-previews/edge/dalia.mp3" ]] || return 1
}

case__voice_cache_lru_purges_over_limit() {
  sandbox_setup lru_purge
  local preview_dir="$SANDBOX/cache/herdr-tts/voice-previews/edge"
  mkdir -p "$preview_dir"

  # Create 3 dummy preview files of 8 MB each (total 24 MB > 20 MB threshold = 20971520 bytes)
  truncate -s 8M "$preview_dir/v1.mp3"
  touch -d "15 minutes ago" "$preview_dir/v1.mp3"

  truncate -s 8M "$preview_dir/v2.mp3"
  touch -d "10 minutes ago" "$preview_dir/v2.mp3"

  truncate -s 8M "$preview_dir/v3.mp3"
  touch -d "5 minutes ago" "$preview_dir/v3.mp3"

  # Verify initial files exist
  [[ -f "$preview_dir/v1.mp3" ]] || return 1
  [[ -f "$preview_dir/v2.mp3" ]] || return 1
  [[ -f "$preview_dir/v3.mp3" ]] || return 1

  # Run LRU purge in host
  in_host 'voice_cache_purge_lru' || return 1

  # Oldest file (v1.mp3) must be purged to bring cache under 20 MB (16 MB remains)
  [[ ! -f "$preview_dir/v1.mp3" ]] || return 1
  [[ -f "$preview_dir/v2.mp3" ]] || return 1
  [[ -f "$preview_dir/v3.mp3" ]] || return 1

  # Total size must now be <= 20971520 bytes
  local total_bytes
  total_bytes=$(find "$SANDBOX/cache/herdr-tts/voice-previews" -type f -exec stat -c %s {} + | awk '{s+=$1} END {print s}')
  (( total_bytes <= 20971520 )) || return 1
}

case__voice_audition_assigns_global() {
  sandbox_setup audition_assign
  local cfg="$SANDBOX/config/herdr-tts/config.env"
  cat > "$cfg" <<'EOF'
TTS_PROVIDER="edge"
TTS_VOICE="elvira"
EOF

  # 1. Direct call to set_global_voice
  in_host 'set_global_voice ximena' || return 1
  grep -q '^TTS_VOICE="ximena"$' "$cfg" || return 1

  # 2. Call via CLI flag --set-global-voice
  XDG_DATA_HOME="$SANDBOX/data" XDG_CONFIG_HOME="$SANDBOX/config" \
  XDG_STATE_HOME="$SANDBOX/state" XDG_CACHE_HOME="$SANDBOX/cache" \
  HERDR_TTS_CONFIG_FILE="$cfg" \
  HERDR_TTS_VOICES_FILE="$SANDBOX/config/herdr-tts/voices.json" \
    bash "$REPO_HOST_DIR/bin/herdr-tts" --set-global-voice jorge >/dev/null 2>&1 || return 1
  grep -q '^TTS_VOICE="jorge"$' "$cfg" || return 1

  # 3. Assigning "(global)" or empty keeps existing voice safely
  in_host 'set_global_voice "(global)"' || return 1
  grep -q '^TTS_VOICE="jorge"$' "$cfg" || return 1
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
