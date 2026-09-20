#!/usr/bin/env bash
# Hermetic smoke tests for herdr-tts.
# Stubs: herdr CLI (fixture JSON, optionally stateful across pane renames),
# venv python, isolated XDG dirs, date/tput stubs for the render pipeline.
#
# Scenarios
#   0-10  dashboard v3.1 regressions (roster, overlays, history grouping,
#         controls, overflow, fail-open, frame budget, width/height clamps)
#   11    ambient title glyphs: lifecycle (prefix → idempotent → state clears
#         → exact restore → integration adoption → multi-glyph → cache purge
#         on close) + TTS_TITLE_GLYPHS=0 zero-spawn + piggyback (no extra
#         `herdr agent list`)
#   12    palette entry building: hidden fields parse, legacy 4-field rows,
#         newest-first order, zero-audio chats, title truncation
#   13    palette preview: chat header, gating, turn rows (legacy shows "-"),
#         empty history, no-herdr fail-open, fzf-missing actionable error
#   14    history snippet: sanitize + 5-field append + legacy fallback
#   15    dashboard renders mixed 4/5-field history rows
#   16    voice menu: dispatch map (stub-verified, one key = one existing
#         function), single-write frame, quit/Esc/unknown/EOF paths,
#         width+height clamp reuse, manifest/README wiring
#   17    keymap: init (template, no-overwrite, --force), check (core
#         shadow warnings, --json), emit (direct/ctrlalt/menu TOML),
#         invalid ids/chords/duplicates rejected, missing file actionable
#   18    keymap apply / adopt: managed block into a fixture config.toml
#         (user content byte-identical, in-place replace, lockstep with
#         emit), idempotent re-apply, null-binding removal, backups
#         created + pruned to 3, dry-run zero writes, invalid keymap
#         refuses, herdr-check failure → rollback, herdr missing → skip
#         note, adopt (require --style, idempotent, refuses modified
#         without --force, ctrlalt/menu maps, seeds missing file)
set -uo pipefail

REPO="${REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SCRIPT="$REPO/bin/herdr-tts"
ROOT="${SMOKE_ROOT:-/tmp/opencode/herdr-tts-smoke}"
rm -rf "$ROOT"; mkdir -p "$ROOT"
PASS=0; FAIL=0

ok()  { PASS=$((PASS+1)); echo "  ok   $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL $1"; }
assert_grep() { # desc pattern file [-F]
  if [[ "${4:-}" == "-F" ]]; then grep -qF -- "$2" "$3" && ok "$1" || bad "$1 (missing: $2)";
  else grep -qE -- "$2" "$3" && ok "$1" || bad "$1 (no match: $2)"; fi
}
assert_no_grep() { # desc pattern file
  grep -qE -- "$2" "$3" && bad "$1 (unexpected: $2)" || ok "$1";
}
assert_no_grep_f() { # desc fixed-string file
  grep -qF -- "$2" "$3" && bad "$1 (unexpected: $2)" || ok "$1";
}
# ANSI-free visible length stats of a raw dashboard capture.
visible_stats() { # $1 = out file -> prints "max_lines rows"
  python3 - "$1" <<'PY'
import re, sys
data = open(sys.argv[1]).read()
lines = [l for l in data.split('\n') if re.sub(r'\x1b\[[0-9;?]*[a-zA-Z]', '', l)]
mx = max((len(re.sub(r'\x1b\[[0-9;?]*[a-zA-Z]', '', l)) for l in lines), default=0)
print(f"{mx} {len(lines)}")
PY
}
esc_count() { # $1 = out file, $2 = escape seq -> occurrence count
  python3 - "$1" "$2" <<'PY'
import sys
print(open(sys.argv[1]).read().count(sys.argv[2]))
PY
}

new_env() { # $1 = scenario dir name
  T="$ROOT/$1"
  rm -rf "$T"; mkdir -p "$T/bin" "$T/data/herdr-tts/venv/bin" "$T/conf" "$T/state"
  # venv python stub: delegates to the real python3 (the history scan and
  # the palette need a working interpreter); engine IPC probes fail open.
  printf '#!/usr/bin/env bash\nexec python3 "$@"\n' > "$T/data/herdr-tts/venv/bin/python"
  chmod +x "$T/data/herdr-tts/venv/bin/python"
  export XDG_CONFIG_HOME="$T/conf" XDG_DATA_HOME="$T/data" XDG_STATE_HOME="$T/state"
  export HERDR_TTS_SNOOZE_FILE="$T/snooze.json"
  export HERDR_TTS_HISTORY_FILE="$T/history.log"
  export LINES=40 COLUMNS=110
  export PATH="$T/bin:$PATH" # stubbed herdr CLI wins over the real one
  : > "$T/err.log"
}

write_herdr_stub() { # $1 fixture file
  cat > "$T/bin/herdr" <<EOF
#!/usr/bin/env bash
if [[ "\${1:-}" == "agent" && "\${2:-}" == "list" ]]; then
  cat "$1"
  exit 0
fi
exit 0
EOF
  chmod +x "$T/bin/herdr"
}

# Stateful herdr stub: `pane rename` updates the served fixture (so later
# `agent list` output reflects the rename, like the real multiplexer) and
# EVERY invocation is logged, which makes spawn counts assertable.
write_stateful_herdr_stub() { # $1 fixture file, $2 log file
  cat > "$T/bin/herdr" <<EOF
#!/usr/bin/env bash
printf '%s\n' "herdr \$*" >> "$2"
# Reality (verified live): pane rename sets the pane LABEL; agent list never
# reflects it. The stub keeps labels in a JSONL sidecar, last write wins.
if [[ "\${1:-}" == "pane" && "\${2:-}" == "rename" && -n "\${3:-}" ]]; then
  jq -n --arg p "\${3:-}" --arg l "\${4:-}" '{pane_id:\$p,label:\$l}' >> "$1.labels"
  exit 0
fi
if [[ "\${1:-}" == "pane" && "\${2:-}" == "list" ]]; then
  jq -s --slurpfile fx "$1" '
    (reduce .[] as \$u ({}; .[\$u.pane_id] = \$u.label)) as \$labels
    | {result:{panes:[\$fx[0].result.agents[]
        | .pane_id as \$p | . + {label:(\$labels[\$p] // .terminal_title_stripped)}]}}' \
      "$1.labels" 2>/dev/null \
    || jq '{result:{panes:[.result.agents[] | . + {label:.terminal_title_stripped}]}}' "$1"
  exit 0
fi
if [[ "\${1:-}" == "agent" && "\${2:-}" == "list" ]]; then
  cat "$1"
  exit 0
fi
exit 0
EOF
  chmod +x "$T/bin/herdr"
}


echo "── 20. daemon_takeover (single-instance guard)"
new_env s20
export HERDR_CONFIG_DIR="$T/conf"
export HERDR_TTS_DAEMON_PID_FILE="$T/daemon.pid"
# 20a. unrelated process (no herdr-tts in cmdline) is spared
sleep 60 & UNRELATED=$!
printf '%s\n' "$UNRELATED" > "$HERDR_TTS_DAEMON_PID_FILE"
lib_run 'daemon_takeover'
kill -0 "$UNRELATED" 2>/dev/null && ok "20a unrelated process spared" || bad "20a killed an unrelated process"
kill "$UNRELATED" 2>/dev/null; wait "$UNRELATED" 2>/dev/null
# 20b. stale herdr-tts daemon (cmdline matches) is taken over
bash -c 'exec -a "herdr-tts-daemon-stale" sleep 60' & STALE=$!
printf '%s\n' "$STALE" > "$HERDR_TTS_DAEMON_PID_FILE"
lib_run 'daemon_takeover'
sleep 0.5
wait "$STALE" 2>/dev/null || true   # reap the killed child or kill -0 sees a zombie
! kill -0 "$STALE" 2>/dev/null && ok "20b stale daemon taken over (killed)" || { bad "20b stale daemon survived"; kill -9 "$STALE" 2>/dev/null; }
# 20c. takeover writes its own pid
grep -qE '^[0-9]+$' "$HERDR_TTS_DAEMON_PID_FILE" && ok "20c pid file written" || bad "20c pid file missing/invalid"

# Library-mode harness: source the plugin script (functions only, no CLI
# dispatch) and eval a snippet, all inside one isolated bash process.
LIBRUN="$ROOT/librun.sh"
cat > "$LIBRUN" <<'EOF'
#!/bin/bash
SCRIPT_PATH="$1"; shift
source "$SCRIPT_PATH"
eval "$1"
EOF
chmod +x "$LIBRUN"
export T FX # snippets reference the current scenario dir / fixture
lib_run() { # $1 snippet — call after new_env so the env is hermetic
  /bin/bash "$LIBRUN" "$SCRIPT" "$1" 2>>"$T/err.log"
}

# Tool-only PATH used to prove behavior when herdr/fzf are absent.
make_nobin() { # $1 dir
  local d="$1" f
  rm -rf "$d"; mkdir -p "$d"
  for f in /usr/bin/* /bin/*; do
    ln -s "$f" "$d/" 2>/dev/null || true
  done
  rm -f "$d/herdr" "$d/fzf" "$d/fzf-tmux"
}

# Fixture mirroring the real `herdr agent list` shape (verified live).
make_fixture() { # $1 out file
  cat > "$1" <<'EOF'
{"result":{"agents":[
 {"agent":"opencode","agent_session":{"agent":"opencode","kind":"id","source":"herdr:opencode","value":"ses_a"},"agent_status":"done","cwd":"/x","focused":false,"pane_id":"w4:p1","revision":4,"state_change_seq":9,"tab_id":"w4:t1","terminal_id":"t1","terminal_title":"OC | Priorización features herdr-tts y siguientes pasos del roadmap","terminal_title_stripped":"OC | Priorización features herdr-tts y siguientes pasos del roadmap","workspace_id":"w4"},
 {"agent":"agy","agent_session":{"agent":"agy","kind":"id","source":"herdr:agy","value":"ses_b"},"agent_status":"working","cwd":"/x","focused":false,"pane_id":"w4:p2","revision":2,"state_change_seq":3,"tab_id":"w4:t1","terminal_id":"t2","terminal_title":"AGY | Refactor del watcher daemon","terminal_title_stripped":"AGY | Refactor del watcher daemon","workspace_id":"w4"},
 {"agent":"opencode","agent_session":{"agent":"opencode","kind":"id","source":"herdr:opencode","value":"ses_c"},"agent_status":"idle","cwd":"/x","focused":false,"pane_id":"w4:p3","revision":7,"state_change_seq":11,"tab_id":"w4:t2","terminal_id":"t3","terminal_title":"OC | Repositorio limpio y tests en verde","terminal_title_stripped":"OC | Repositorio limpio y tests en verde","workspace_id":"w4"},
 {"agent":"gemini","agent_session":{"agent":"gemini","kind":"id","source":"herdr:gemini","value":"ses_d"},"agent_status":"working","cwd":"/x","focused":true,"pane_id":"w4:p4","revision":1,"state_change_seq":1,"tab_id":"w4:t3","terminal_id":"t4","terminal_title":"Gem | Revisar PR 42","terminal_title_stripped":"Gem | Revisar PR 42","workspace_id":"w4"}
]}}
EOF
}

make_history() { # $1 out file (timestamps relative to now for age checks)
  {
    printf '%s\tw4:p3\topencode\t12.5\n' "$(date -d '-95 minutes' +%Y-%m-%dT%H:%M:%S)"
    printf '%s\tw4:p3\topencode\t8.0\n'  "$(date -d '-70 minutes' +%Y-%m-%dT%H:%M:%S)"
    printf '%s\tw4:p3\topencode\t30.2\n' "$(date -d '-45 minutes' +%Y-%m-%dT%H:%M:%S)"
    printf '%s\tw4:p3\topencode\t5.0\n'  "$(date -d '-40 minutes' +%Y-%m-%dT%H:%M:%S)"
    printf '%s\tw9:p9\topencode\t45.0\n' "$(date -d '-25 minutes' +%Y-%m-%dT%H:%M:%S)"
    printf '%s\tw4:p1\topencode\t21.3\n' "$(date -d '-12 minutes' +%Y-%m-%dT%H:%M:%S)"
    printf '%s\tw4:p1\topencode\t60.0\n' "$(date -d '-2 minutes'  +%Y-%m-%dT%H:%M:%S)"
  } > "$1"
}

make_gate_state() { # mute w4:p3, snooze w4:p2 (+275s), debounce on w4:p1 (done 5s ago)
  jq -n --argjson now "$(date +%s)" '{
    panes: { "w4:p3": {muted: true}, "w4:p2": {snooze_until: ($now + 275)} },
    debounce: { "w4:p1": {done: ($now - 5)} }
  }' > "$HERDR_TTS_SNOOZE_FILE"
}

capture() { # $1 keys (printf %b), $2 out file
  printf '%b' "$1" | timeout 30 "$SCRIPT" --dashboard > "$2" 2>>"$T/err.log"
  local rc=$?
  [[ $rc -ne 0 ]] && bad "dashboard exited rc=$rc (see $T/err.log)"
  return 0
}

echo "── 0. bash -n"
bash -n "$SCRIPT" && ok "bash -n clean" || bad "bash -n failed"

echo "── 1. roster merge + sort + overlays + history grouping (regression)"
new_env s1
FX="$T/fixture.json"; make_fixture "$FX"
write_herdr_stub "$FX"
make_history "$HERDR_TTS_HISTORY_FILE"
make_gate_state
capture 'q\n' "$T/out.txt"

T1="OC | Priorización features herdr-tts y siguientes pasos del roadmap"
assert_grep "done chat rendered with truncated title" "${T1:0:39}…" "$T/out.txt" -F
assert_grep "done line shows agent kind" '✔ .*opencode' "$T/out.txt"
assert_grep "working icon present" '▶ ' "$T/out.txt"
assert_grep "idle icon present" '· ' "$T/out.txt"
d=$(grep -n 'Priorización' "$T/out.txt" | head -1 | cut -d: -f1)
w=$(grep -n 'Refactor del watcher' "$T/out.txt" | head -1 | cut -d: -f1)
i=$(grep -n 'Repositorio limpio' "$T/out.txt" | head -1 | cut -d: -f1)
[[ -n "$d" && -n "$w" && -n "$i" && "$d" -lt "$w" && "$w" -lt "$i" ]] \
  && ok "sort: done($d) < working($w) < idle($i)" || bad "sort order wrong (done=$d working=$w idle=$i)"
assert_grep "mute overlay 🔇 on w4:p3" 'Repositorio limpio.*🔇' "$T/out.txt"
assert_grep "snooze overlay 😴 mm:ss on w4:p2" 'Refactor del watcher.*😴 04:3[0-9]' "$T/out.txt"
assert_grep "debounce overlay ⏱Ns on w4:p1" 'Priorización.*⏱[0-9]+s' "$T/out.txt"
assert_grep "history section header" 'Historial por chat' "$T/out.txt"
assert_grep "group header counts p1 (2 audios)" '· 2 audios · último hace' "$T/out.txt"
assert_grep "group header counts p3 (4 audios)" '· 4 audios · último hace' "$T/out.txt"
assert_grep "closed pane group labeled by pane id" '── w9:p9 ' "$T/out.txt"
h1=$(grep -n '· 2 audios' "$T/out.txt" | head -1 | cut -d: -f1)
h9=$(grep -n 'w9:p9' "$T/out.txt" | head -1 | cut -d: -f1)
h3=$(grep -n '· 4 audios' "$T/out.txt" | head -1 | cut -d: -f1)
[[ -n "$h1" && -n "$h9" && -n "$h3" && "$h1" -lt "$h9" && "$h9" -lt "$h3" ]] \
  && ok "history groups sorted by last-audio recency (p1 < p9 < p3)" || bad "history group order wrong ($h1/$h9/$h3)"
na=$(grep -cE '^║    · [0-9]{2}:[0-9]{2} · hace [0-9]+[smh]$' "$T/out.txt")
[[ "$na" -eq 6 ]] && ok "audio rows: 2+1+3 = 6 (max 3 per group)" || bad "audio rows = $na (want 6)"
assert_grep "duration+age: 01:00 two minutes ago" '· 01:00 · hace 2m' "$T/out.txt"
assert_grep "duration+age: 00:21 twelve minutes ago" '· 00:21 · hace 12m' "$T/out.txt"
assert_grep "duration+age: 00:45 twentyfive minutes ago (closed pane)" '· 00:45 · hace 25m' "$T/out.txt"
assert_grep "p1 header age: último hace 2m" '── .* · 2 audios · último hace 2m' "$T/out.txt"
assert_grep "engine fallback line intact (v2 regression)" 'motor: no responde' "$T/out.txt"
assert_grep "global state line intact" 'snooze global off' "$T/out.txt"
assert_grep "config line intact" 'proveedor edge' "$T/out.txt"
# v3.1 single-tick frame: exactly one frame emit; full-clear only from cleanup
hv=$(esc_count "$T/out.txt" $'\033[H')
jv=$(esc_count "$T/out.txt" $'\033[J')
cj=$(esc_count "$T/out.txt" $'\033[2J')
[[ "$hv" -eq 2 ]] && ok "single tick → 1 frame home (H-moves=2 incl. cleanup) ($hv)" || bad "H-moves=$hv (want 2)"
[[ "$jv" -eq 1 ]] && ok "erase-below present once ($jv)" || bad "erase-below=$jv (want 1)"
[[ "$cj" -eq 1 ]] && ok "no full-clear in render path (2J only from cleanup) ($cj)" || bad "2J=$cj (want 1: cleanup)"

echo "── 2. controls: j/m/z/Z/+/- regressions"
new_env s2
FX="$T/fixture.json"; make_fixture "$FX"
write_herdr_stub "$FX"
capture 'jmzZ+-q\n' "$T/out.txt"
jq -e '.panes["w4:p2"].muted == true' "$HERDR_TTS_SNOOZE_FILE" >/dev/null \
  && ok "m muted the selected chat w4:p2 (2nd after j)" || bad "m did not mute w4:p2"
jq -e --argjson now "$(date +%s)" '.panes["w4:p2"].snooze_until > $now' "$HERDR_TTS_SNOOZE_FILE" >/dev/null \
  && ok "z cycled snooze on selected chat" || bad "z did not snooze w4:p2"
jq -e --argjson now "$(date +%s)" '.global_snooze_until > $now' "$HERDR_TTS_SNOOZE_FILE" >/dev/null \
  && ok "Z cycled GLOBAL snooze" || bad "Z did not set global snooze"
grep -qE '^TTS_RATE="\+20%"$' "$T/conf/herdr-tts/config.env" \
  && ok "+/- adjusted rate (net +20% after + then -)" || bad "rate +/- not persisted as expected"
frames=$(grep -c 'Panel de voz' "$T/out.txt")
[[ "$frames" -ge 2 ]] && ok "multiple frames rendered ($frames)" || bad "only $frames frame(s)"
last_frame=$(awk '/Panel de voz/{buf=""} {buf=buf $0 "\n"} END{printf "%s", buf}' "$T/out.txt")
printf '%s' "$last_frame" > "$T/last_frame.txt"
m1=$(grep -n '▸ ' "$T/last_frame.txt" | head -1 | cut -d: -f1)
c2=$(grep -n 'Refactor del watcher' "$T/last_frame.txt" | head -1 | cut -d: -f1)
[[ -n "$m1" && -n "$c2" && "$m1" -eq "$c2" ]] \
  && ok "j moved cursor to 2nd roster line (working chat)" || bad "cursor mismatch (marker=$m1 line2=$c2)"
assert_grep "snoozed chat shows 😴 overlay after z" 'Refactor del watcher.*😴' "$T/last_frame.txt"
assert_grep "mute msg surfaced in panel" 'silenciado' "$T/out.txt"
read stats < <(visible_stats "$T/last_frame.txt"); sl=${stats%% *}
[[ "$sl" -le 110 ]] && ok "last frame max visible width ≤ 110 ($sl)" || bad "last frame max width $sl"

echo "── 3. overflow cap (30 idle + 2 done, never hide needs-attention)"
new_env s3
{ printf '{"result":{"agents":['
  printf '{"agent":"opencode","agent_status":"done","pane_id":"w1:p1","terminal_title_stripped":"DONEONE revisión crítica","agent_session":{"value":"s1"}},'
  printf '{"agent":"opencode","agent_status":"done","pane_id":"w1:p2","terminal_title_stripped":"DONETWO revisión crítica","agent_session":{"value":"s2"}}'
  for k in $(seq 1 60); do
    printf ',{"agent":"agy","agent_status":"idle","pane_id":"w2:p%02d","terminal_title_stripped":"Chat idle numero %02d del listado largo","agent_session":{"value":"s%d"}}' "$k" "$k" "$k"
  done
  printf ']}}'
} > "$T/fixture.json"
rm -f "$HERDR_TTS_HISTORY_FILE"
write_herdr_stub "$T/fixture.json"
FX="$T/fixture.json"
capture 'q\n' "$T/out.txt"
assert_grep "DONEONE visible" 'DONEONE' "$T/out.txt"
assert_grep "DONETWO visible" 'DONETWO' "$T/out.txt"
assert_grep "overflow notice present" 'y [0-9]+ chats más' "$T/out.txt"
read stats < <(visible_stats "$T/out.txt"); sl=${stats#* }
[[ "$sl" -le 39 ]] && ok "total frame rows ≤ LINES-1 = 39 ($sl)" || bad "frame overflow: $sl rows"
assert_no_grep "history section hidden without ledger" 'Historial por chat' "$T/out.txt"

echo "── 4. fail-open: no herdr CLI"
new_env s4
make_history "$HERDR_TTS_HISTORY_FILE"
env PATH="/usr/bin:/bin" HOME="$HOME" LINES=40 COLUMNS=110 timeout 30 "$SCRIPT" --dashboard < /dev/null > "$T/out.txt" 2>>"$T/err.log" || true
assert_grep "roster shows sin datos de herdr" 'sin datos de herdr' "$T/out.txt"
assert_grep "history still renders from ledger" 'Historial por chat' "$T/out.txt"
assert_grep "closed-pane labels survive without roster" '── w4:p3 ' "$T/out.txt"

echo "── 5. empty history → section hidden"
new_env s5
FX="$T/fixture.json"; make_fixture "$FX"
write_herdr_stub "$FX"
rm -f "$HERDR_TTS_HISTORY_FILE"
capture 'q\n' "$T/out.txt"
assert_no_grep "history section hidden" 'Historial por chat' "$T/out.txt"
assert_grep "roster still renders" 'Chats \(j/k' "$T/out.txt"

echo "── 6. CLI regressions (v2 flags)"
new_env s6
FX="$T/fixture.json"; make_fixture "$FX"
write_herdr_stub "$FX"
"$SCRIPT" --help 2>/dev/null | grep -q 'j/k chat' && ok "--help mentions chat controls" || bad "--help dashboard blurb stale"
"$SCRIPT" --help 2>/dev/null | grep -q -- '--voice-palette' && ok "--help mentions voice palette" || bad "--help missing --voice-palette"
timeout 10 "$SCRIPT" --status >/dev/null 2>&1 && ok "--status runs" || bad "--status failed"

echo "── 7. single-write per tick + change-detection (fifo, date stubs)"
new_env s7
FX="$T/fixture.json"; make_fixture "$FX"
write_herdr_stub "$FX"
make_history "$HERDR_TTS_HISTORY_FILE"
# 7a. LIVE clock stub: +%H:%M:%S increments per call (per tick), +%s real.
CALLS="$T/date.calls"; : > "$CALLS"
cat > "$T/bin/date" <<EOF
#!/usr/bin/env bash
if [[ "\${1:-}" == "+%H:%M:%S" ]]; then
  echo x >> "$CALLS"
  n=\$(cat "$T/cnt" 2>/dev/null || echo 0); n=\$((n+1)); echo "\$n" > "$T/cnt"
  printf '12:00:%02d' "\$n"
else
  exec /bin/date "\$@"
fi
EOF
chmod +x "$T/bin/date"
mkfifo "$T/in"
timeout 30 "$SCRIPT" --dashboard < "$T/in" > "$T/out.txt" 2>>"$T/err.log" &
DPID=$!
exec 3>"$T/in" # hold the fifo open so reads block on timeout instead of EOF
sleep 0.3
sleep 2.6      # ≥ two 1s read-timeouts → ≥3 ticks total
printf 'q' >&3
exec 3>&-
wait "$DPID"; rc=$?
[[ $rc -eq 0 ]] && ok "fifo session exited clean (rc=0)" || bad "fifo session rc=$rc"
ticks=$(wc -l < "$CALLS")
[[ "$ticks" -ge 3 ]] && ok "clock stub proves ≥3 ticks ran ($ticks)" || bad "only $ticks clock call(s)"
hv=$(esc_count "$T/out.txt" $'\033[H')
[[ "$hv" -ge 4 ]] && ok "changing clock → every tick written (H-moves=$hv ≥ 3 frames + cleanup)" || bad "H-moves=$hv with changing clock (want ≥4)"
# 7b. FROZEN clock stub: identical frames → change-detection must skip writes.
new_env s7b
FX="$T/fixture.json"; make_fixture "$FX"
write_herdr_stub "$FX"
make_history "$HERDR_TTS_HISTORY_FILE"
CALLS="$T/date.calls"; : > "$CALLS"
cat > "$T/bin/date" <<EOF
#!/usr/bin/env bash
if [[ "\${1:-}" == "+%H:%M:%S" ]]; then
  echo x >> "$CALLS"
  printf '12:00:00'
else
  exec /bin/date "\$@"
fi
EOF
chmod +x "$T/bin/date"
mkfifo "$T/in"
timeout 30 "$SCRIPT" --dashboard < "$T/in" > "$T/out.txt" 2>>"$T/err.log" &
DPID=$!
exec 3>"$T/in"
sleep 0.3
sleep 2.6
printf 'q' >&3
exec 3>&-
wait "$DPID"; rc=$?
[[ $rc -eq 0 ]] && ok "frozen session exited clean (rc=0)" || bad "frozen session rc=$rc"
ticks=$(wc -l < "$CALLS")
[[ "$ticks" -ge 3 ]] && ok "frozen clock: ≥3 ticks ran ($ticks)" || bad "only $ticks clock call(s)"
hv=$(esc_count "$T/out.txt" $'\033[H')
[[ "$hv" -eq 2 ]] && ok "identical ticks → change-detection: exactly 1 physical frame write (H=2 incl cleanup)" || bad "H-moves=$hv for identical frames (want 2)"
frames=$(grep -c 'Panel de voz' "$T/out.txt")
[[ "$frames" -eq 1 ]] && ok "exactly 1 frame of content in output ($frames)" || bad "$frames frame(s) in output (want 1)"

echo "── 8. width clamp: COLUMNS=90 → no line exceeds 90 visible chars"
new_env s8
export COLUMNS=90
LONG="OC | Título larguisimo de más de noventa columnas para forzar el wrap del popup y reventar el layout entero"
{ printf '{"result":{"agents":['
  printf '{"agent":"opencode","agent_status":"done","pane_id":"w8:p1","terminal_title_stripped":"%s","agent_session":{"value":"a"}},' "$LONG"
  printf '{"agent":"agy","agent_status":"working","pane_id":"w8:p2","terminal_title_stripped":"AGY | Refactor del watcher daemon","agent_session":{"value":"b"}},'
  printf '{"agent":"opencode","agent_status":"idle","pane_id":"w8:p3","terminal_title_stripped":"OC | Repositorio limpio y tests en verde","agent_session":{"value":"c"}}'
  printf ']}}'
} > "$T/fixture.json"
write_herdr_stub "$T/fixture.json"
FX="$T/fixture.json"
make_history "$HERDR_TTS_HISTORY_FILE"
make_gate_state
capture 'q\n' "$T/out.txt"
read stats < <(visible_stats "$T/out.txt"); sl=${stats%% *}; nl=${stats#* }
[[ "$sl" -le 90 ]] && ok "no line exceeds 90 visible chars (max=$sl, lines=$nl)" || bad "max visible width $sl > 90"
LONGT32="${LONG:0:31}…"
if grep -qF -- "$LONGT32" "$T/out.txt"; then
  ok "roster title truncated to dynamic width 32 (90 cols < 110 base)"
else
  bad "roster title not truncated to 32 (got: $(grep -oF "${LONG:0:20}" "$T/out.txt" | head -1)…)"
fi
LONG40="${LONG:0:39}…"
assert_no_grep_f "old fixed width 40 no longer used at 90 cols" "$LONG40" "$T/out.txt"
echo "  → measured max line length at COLUMNS=90: $sl visible chars"

echo "── 9. height clamp: LINES=30 → history gone, idle dropped, done kept"
new_env s9
export LINES=30
{ printf '{"result":{"agents":['
  printf '{"agent":"opencode","agent_status":"done","pane_id":"w9:p1","terminal_title_stripped":"DONEONE revisión crítica","agent_session":{"value":"s1"}},'
  printf '{"agent":"opencode","agent_status":"done","pane_id":"w9:p2","terminal_title_stripped":"DONETWO revisión crítica","agent_session":{"value":"s2"}}'
  for k in $(seq 1 30); do
    printf ',{"agent":"agy","agent_status":"idle","pane_id":"w9:i%02d","terminal_title_stripped":"Chat idle numero %02d del panel","agent_session":{"value":"i%d"}}' "$k" "$k" "$k"
  done
  printf ']}}'
} > "$T/fixture.json"
write_herdr_stub "$T/fixture.json"
FX="$T/fixture.json"
make_history "$HERDR_TTS_HISTORY_FILE"
capture 'q\n' "$T/out.txt"
read stats < <(visible_stats "$T/out.txt"); nl=${stats#* }
[[ "$nl" -le 29 ]] && ok "frame rows ≤ LINES-1 = 29 ($nl)" || bad "frame rows $nl > 29"
assert_no_grep "history shrunk away first (budget 24 → 0)" 'Historial por chat' "$T/out.txt"
assert_grep "done chat DONEONE kept" 'DONEONE' "$T/out.txt"
assert_grep "done chat DONETWO kept" 'DONETWO' "$T/out.txt"
assert_grep "idle chats dropped with notice" 'y [0-9]+ chats más' "$T/out.txt"
echo "── 9b. minimum fit: LINES=10 → fixed lines + 2 chats + notice = 9 rows"
new_env s9b
export LINES=10
cp "$ROOT/s9/fixture.json" "$T/fixture.json"
write_herdr_stub "$T/fixture.json"
FX="$T/fixture.json"
make_history "$HERDR_TTS_HISTORY_FILE"
capture 'q\n' "$T/out.txt"
read stats < <(visible_stats "$T/out.txt"); nl=${stats#* }
[[ "$nl" -eq 9 ]] && ok "minimum frame: exactly 9 rows (LINES-1) ($nl)" || bad "minimum frame rows = $nl (want 9)"
assert_grep "footer survives the minimum fit" 'q salir' "$T/out.txt"
assert_grep "done chats survive the minimum fit" 'DONEONE' "$T/out.txt"
assert_grep "'+N chats' notice rendered" 'y 30 chats más' "$T/out.txt"

echo "── 10. size fallback chain: env unset + failing tput → 40x100"
new_env s10
unset LINES COLUMNS
printf '#!/usr/bin/env bash\nexit 1\n' > "$T/bin/tput"; chmod +x "$T/bin/tput"
LONG="OC | Título larguisimo de más de noventa columnas para forzar el wrap del popup y reventar el layout entero"
{ printf '{"result":{"agents":['
  printf '{"agent":"opencode","agent_status":"done","pane_id":"wA:p1","terminal_title_stripped":"%s","agent_session":{"value":"a"}}' "$LONG"
  printf ']}}'
} > "$T/fixture.json"
write_herdr_stub "$T/fixture.json"
FX="$T/fixture.json"
rm -f "$HERDR_TTS_HISTORY_FILE"
capture 'q\n' "$T/out.txt"
LONG36="${LONG:0:35}…"
grep -qF -- "$LONG36" "$T/out.txt" \
  && ok "fallback cols=100 → roster title width 36 (100*40/110)" || bad "title not at fallback width 36"
LONG40="${LONG:0:39}…"
assert_no_grep_f "fallback did not use full width 40" "$LONG40" "$T/out.txt"
read stats < <(visible_stats "$T/out.txt"); nl=${stats#* }
[[ "$nl" -le 39 ]] && ok "fallback lines=40 → frame rows ≤ 39 ($nl)" || bad "frame rows $nl > 39"

echo "── 11. title glyphs: lifecycle + opt-out + piggyback"
new_env s11
FX="$T/fixture.json"
cat > "$FX" <<'EOF'
{"result":{"agents":[
 {"agent":"opencode","agent_status":"done","pane_id":"w4:p1","terminal_title":"OC | Chat Original","terminal_title_stripped":"OC | Chat Original"},
 {"agent":"agy","agent_status":"working","pane_id":"w4:p2","terminal_title":"AGY | Refactor del watcher","terminal_title_stripped":"AGY | Refactor del watcher"},
 {"agent":"opencode","agent_status":"idle","pane_id":"w4:p3","terminal_title":"OC | Repositorio limpio","terminal_title_stripped":"OC | Repositorio limpio"}
]}}
EOF
write_stateful_herdr_stub "$FX" "$T/herdr.log"
: > "$T/herdr.log"

# 11a. prefix: done pane gets ✔, original cached, others untouched
lib_run 'sync_pane_titles "$(cat "$FX")"'
assert_grep "11a rename to ✔-prefixed title" 'herdr pane rename w4:p1 ✔ \| OC \| Chat Original$' "$T/herdr.log"
[[ "$(jq -r '.panes["w4:p1"].title_original' "$HERDR_TTS_SNOOZE_FILE" 2>/dev/null)" == "OC | Chat Original" ]] \
  && ok "11a original title cached in snooze state" || bad "11a title_original missing/wrong"
grep -q 'rename w4:p2' "$T/herdr.log" && bad "11a working pane renamed (must not)" || ok "11a working pane untouched"
grep -q 'rename w4:p3' "$T/herdr.log" && bad "11a idle pane renamed (must not)" || ok "11a idle pane untouched"
[[ "$(jq -r '.result.agents[] | select(.pane_id=="w4:p1") | .terminal_title_stripped' "$FX")" == "OC | Chat Original" ]] \
  && ok "11a rename surface is the LABEL (fixture/terminal_title untouched)" || bad "11a rename leaked into fixture"
[[ "$("$T/bin/herdr" pane list | jq -r '.result.panes[] | select(.pane_id=="w4:p1") | .label')" == "✔ | OC | Chat Original" ]] \
  && ok "11a stateful stub applied the rename to the live label" || bad "11a stub did not apply label rename"

# 11b. idempotent: second sweep renames nothing
: > "$T/herdr.log"
lib_run 'sync_pane_titles "$(cat "$FX")"'
[[ "$(grep -c 'pane rename' "$T/herdr.log")" -eq 0 ]] \
  && ok "11b rename-only-on-change: 2nd sweep renamed nothing" || bad "11b 2nd sweep renamed: $(grep 'pane rename' "$T/herdr.log")"
grep -q 'pane list' "$T/herdr.log" && ok "11b sync consults pane labels (pane list)" || bad "11b no pane list consult"
grep -q 'agent list' "$T/herdr.log" && bad "11b sync spawned herdr agent list" || ok "11b piggyback: sync itself never spawns agent list"

# 11c. state clears → exact restore + cache dropped
jq '(.result.agents[] | select(.pane_id == "w4:p1")).agent_status = "working"' "$FX" > "$T/f2" && mv "$T/f2" "$FX"
: > "$T/herdr.log"
lib_run 'sync_pane_titles "$(cat "$FX")"'
assert_grep "11c restore renames to original" 'herdr pane rename w4:p1 OC \| Chat Original$' "$T/herdr.log"
[[ "$(jq -r '.panes["w4:p1"].title_original // "NO-CACHE"' "$HERDR_TTS_SNOOZE_FILE")" == "NO-CACHE" ]] \
  && ok "11c title_original dropped after restore" || bad "11c cache not dropped"

# 11d. integration rename mid-session → adopt as new original
lib_run 'toggle_pane_mute w4:p1 >/dev/null' # 🔇 glyph via the ledger
"$T/bin/herdr" pane rename w4:p1 "INTEGRATION | Renamed by tool" # foreign rename
: > "$T/herdr.log"
lib_run 'sync_pane_titles "$(cat "$FX")"'
assert_grep "11d muted+renamed pane gets 🔇 prefix on ADOPTED title" 'herdr pane rename w4:p1 🔇 \| INTEGRATION \| Renamed by tool$' "$T/herdr.log"
[[ "$(jq -r '.panes["w4:p1"].title_original' "$HERDR_TTS_SNOOZE_FILE")" == "INTEGRATION | Renamed by tool" ]] \
  && ok "11d integration title adopted as new original" || bad "11d adoption failed"

# 11e. multi-glyph stacking in fixed order (✔ done, 🔇 mute, 😴 snooze)
lib_run 'cycle_snooze pane w4:p1 >/dev/null'
jq '(.result.agents[] | select(.pane_id == "w4:p1")).agent_status = "done"' "$FX" > "$T/f2" && mv "$T/f2" "$FX"
: > "$T/herdr.log"
lib_run 'sync_pane_titles "$(cat "$FX")"'
assert_grep "11e done+mute+snooze stacks ✔🔇😴" 'herdr pane rename w4:p1 ✔🔇😴 \| INTEGRATION \| Renamed by tool$' "$T/herdr.log"

# 11f. glyphs vanish → exact restore of the adopted original
jq 'del(.panes["w4:p1"].snooze_until)' "$HERDR_TTS_SNOOZE_FILE" > "$T/s2" && mv "$T/s2" "$HERDR_TTS_SNOOZE_FILE"
lib_run 'toggle_pane_mute w4:p1 >/dev/null' # unmute
jq '(.result.agents[] | select(.pane_id == "w4:p1")).agent_status = "working"' "$FX" > "$T/f2" && mv "$T/f2" "$FX"
: > "$T/herdr.log"
lib_run 'sync_pane_titles "$(cat "$FX")"'
assert_grep "11f exact restore after glyphs vanish" 'herdr pane rename w4:p1 INTEGRATION \| Renamed by tool$' "$T/herdr.log"
[[ "$(jq -r '.panes["w4:p1"].title_original // "NO-CACHE"' "$HERDR_TTS_SNOOZE_FILE")" == "NO-CACHE" ]] \
  && ok "11f cache dropped again" || bad "11f cache not dropped"

# 11g. panes that close purge their cache in the prune sweep
jq -c '.panes = ((.panes // {}) + {"w4:p1": {title_original: "Cached Title", muted: true}})' \
  "$HERDR_TTS_SNOOZE_FILE" > "$T/s2" && mv "$T/s2" "$HERDR_TTS_SNOOZE_FILE"
jq 'del(.result.agents[] | select(.pane_id == "w4:p1"))' "$FX" > "$T/f2" && mv "$T/f2" "$FX"
lib_run 'prune_snooze_state "$(cat "$FX")"'
[[ "$(jq -r '.panes["w4:p1"].title_original // null' "$HERDR_TTS_SNOOZE_FILE")" == "null" ]] \
  && ok "11g closed pane purged title cache in prune" || bad "11g cache survived pane close"

# 11h. TTS_TITLE_GLYPHS=0 → daemon_title_sync spawns NOTHING
: > "$T/herdr.log"
lib_run 'TTS_TITLE_GLYPHS=0 daemon_title_sync "$(cat "$FX")"'
[[ "$(wc -l < "$T/herdr.log")" -eq 0 ]] \
  && ok "11h TTS_TITLE_GLYPHS=0 → zero spawns" || bad "11h spawned with glyphs off: $(cat "$T/herdr.log")"

echo "── 12. palette entries: hidden fields, legacy rows, zero-audio chats"
new_env s12
FX="$T/fixture.json"
cat > "$FX" <<'EOF'
{"result":{"agents":[
 {"agent":"opencode","agent_status":"done","pane_id":"w4:p1","terminal_title":"OC | Chat Uno","terminal_title_stripped":"OC | Chat Uno"},
 {"agent":"agy","agent_status":"working","pane_id":"w4:p2","terminal_title":"AGY | Chat Dos","terminal_title_stripped":"AGY | Chat Dos"},
 {"agent":"gemini","agent_status":"idle","pane_id":"w4:p3","terminal_title":"Gem | Sin audios todavia","terminal_title_stripped":"Gem | Sin audios todavia"}
]}}
EOF
write_herdr_stub "$FX"
# chronological order (append-only ledger): p1 snippet row, p1 legacy row, p2 row
{
  printf '%s\tw4:p1\topencode\t21.3\tPrimera vuelta del chat uno\n' "$(date -d '-12 minutes' +%Y-%m-%dT%H:%M:%S)"
  printf '%s\tw4:p1\topencode\t60.0\n' "$(date -d '-2 minutes' +%Y-%m-%dT%H:%M:%S)"
  printf '%s\tw4:p2\tagy\t8.0\tRespuesta mas reciente de todas\n' "$(date -d 'now' +%Y-%m-%dT%H:%M:%S)"
} > "$HERDR_TTS_HISTORY_FILE"
lib_run 'palette_build_entries' > "$T/out.txt"
[[ "$(wc -l < "$T/out.txt")" -eq 4 ]] \
  && ok "12 4 entries: 3 audios + 1 zero-audio chat" || bad "12 entry count = $(wc -l < "$T/out.txt") (want 4)"
nfields=$(awk -F'\t' '{print NF}' "$T/out.txt" | sort -u | tr '\n' ' ')
[[ "$nfields" == "3 " ]] && ok "12 every entry has 3 tab-delimited fields" || bad "12 field counts: $nfields"
mapfile -t plines < "$T/out.txt"
l1="${plines[0]:-}"; l2="${plines[1]:-}"; l3="${plines[2]:-}"; l4="${plines[3]:-}"
[[ "${l1%%$'\t'*}" == *"· 00:08 · Respuesta mas reciente de todas" ]] \
  && ok "12 newest audio first (p2 row leads)" || bad "12 first entry not newest: ${l1%%$'\t'*}"
[[ "${l2%%$'\t'*}" == *"· 01:00 · -" ]] \
  && ok "12 legacy 4-field row renders '-' snippet" || bad "12 legacy row wrong: ${l2%%$'\t'*}"
[[ "$(printf '%s' "$l2" | cut -f2)" == "w4:p1" && "$(printf '%s' "$l2" | cut -f3)" =~ ^[0-9]+$ ]] \
  && ok "12 hidden fields parse: pane_id + numeric epoch" || bad "12 hidden fields wrong: $l2"
[[ "$l4" == "(sin audios) · Gem | Sin audios todavia"$'\t'"w4:p3"$'\t'"chat" ]] \
  && ok "12 zero-audio chat entry format exact" || bad "12 zero-audio entry: $l4"

echo "── 13. palette preview: header, gating, turns, fail-open, no-fzf"
new_env s13
FX="$T/fixture.json"
cat > "$FX" <<'EOF'
{"result":{"agents":[
 {"agent":"opencode","agent_status":"done","pane_id":"w4:p1","terminal_title":"OC | Chat Uno","terminal_title_stripped":"OC | Chat Uno"}
]}}
EOF
write_herdr_stub "$FX"
make_gate_state # w4:p3 muted/debounce entries don't apply; w4:p1 clean
{
  printf '%s\tw4:p1\topencode\t21.3\tPrimera vuelta del chat uno\n' "$(date -d '-12 minutes' +%Y-%m-%dT%H:%M:%S)"
  printf '%s\tw4:p1\topencode\t60.0\n' "$(date -d '-2 minutes' +%Y-%m-%dT%H:%M:%S)"
} > "$HERDR_TTS_HISTORY_FILE"
lib_run 'palette_preview w4:p1' > "$T/out.txt"
assert_grep "13 header shows chat title" '^📌 OC \| Chat Uno$' "$T/out.txt"
assert_grep "13 header shows agent+status" 'agente: opencode · estado: done' "$T/out.txt"
assert_grep "13 gating shows the active debounce hold" 'gating: .*⏱ debounce activo \([0-9]+s\)' "$T/out.txt"
assert_grep "13 newest turn first with legacy '-' snippet" '· 01:00 · -$' "$T/out.txt"
assert_grep "13 older turn shows snippet" '· 00:21 · Primera vuelta del chat uno$' "$T/out.txt"
t1=$(grep -n '· 01:00 · -' "$T/out.txt" | head -1 | cut -d: -f1)
t2=$(grep -n '· 00:21 · Primera' "$T/out.txt" | head -1 | cut -d: -f1)
[[ -n "$t1" && -n "$t2" && "$t1" -lt "$t2" ]] \
  && ok "13 turns sorted newest first" || bad "13 turn order wrong"
lib_run 'palette_preview w9:zz' > "$T/out2.txt"
assert_grep "13 empty history → sin turnos" '^sin turnos$' "$T/out2.txt"
make_nobin "$T/nobin"
env PATH="$T/nobin" HOME="$HOME" XDG_CONFIG_HOME="$T/conf" XDG_DATA_HOME="$T/data" \
  XDG_STATE_HOME="$T/state" HERDR_TTS_SNOOZE_FILE="$HERDR_TTS_SNOOZE_FILE" HERDR_TTS_HISTORY_FILE="$HERDR_TTS_HISTORY_FILE" \
  /bin/bash "$LIBRUN" "$SCRIPT" 'palette_preview w4:p1' > "$T/out3.txt" 2>/dev/null
assert_grep "13 no herdr CLI → explicit note" 'herdr CLI no disponible' "$T/out3.txt"
assert_grep "13 no-herdr preview still lists turns" '· 00:21 · Primera' "$T/out3.txt"
env PATH="$T/nobin" HOME="$HOME" XDG_CONFIG_HOME="$T/conf" XDG_DATA_HOME="$T/data" \
  XDG_STATE_HOME="$T/state" HERDR_TTS_SNOOZE_FILE="$HERDR_TTS_SNOOZE_FILE" HERDR_TTS_HISTORY_FILE="$HERDR_TTS_HISTORY_FILE" \
  /bin/bash "$LIBRUN" "$SCRIPT" 'run_voice_palette' > "$T/out4.txt" 2>&1
assert_grep "13 no fzf → actionable Spanish error" 'fzf no está instalado' "$T/out4.txt"
assert_grep "13 no-fzf error suggests install command" 'apt install fzf|brew install fzf' "$T/out4.txt"

echo "── 14. history snippet: sanitize, 5-field append, legacy fallback"
new_env s14
FX="$T/fixture.json"; make_fixture "$FX"
write_herdr_stub "$FX"
lib_run '
  s=""
  history_snippet s "$(printf "Hola\r\nmundo\t  con   \x1b[31mANSI\x1b[0m colorea  y\nsigue ")"
  echo "[$s]"
  append_audio_history w4:p1 opencode 12.5 "texto limpio de prueba"
  append_audio_history w4:p2 agy 3.0 ""
' > "$T/out.txt"
assert_grep "14 snippet single-line + ANSI stripped + whitespace collapsed" '^\[Hola mundo con ANSI colorea y sigue\]$' "$T/out.txt"
[[ "$(awk -F'\t' 'NR==1{print NF}' "$HERDR_TTS_HISTORY_FILE")" -eq 5 ]] \
  && ok "14 non-empty snippet → 5-field row" || bad "14 first row not 5-field"
grep -qE $'^[^\t]+\tw4:p1\topencode\t12\.5\ttexto limpio de prueba$' "$HERDR_TTS_HISTORY_FILE" \
  && ok "14 row schema ts/pane/agent/duration/snippet" || bad "14 row schema wrong: $(head -1 "$HERDR_TTS_HISTORY_FILE" | cat -A)"
[[ "$(awk -F'\t' 'NR==2{print NF}' "$HERDR_TTS_HISTORY_FILE")" -eq 4 ]] \
  && ok "14 empty snippet → legacy 4-field row (no empty tail)" || bad "14 second row not 4-field"
lib_run '
  big="$(printf "pad %.0s" $(seq 1 200))"
  s=""
  history_snippet s "$big"
  echo "${#s}"
' > "$T/out2.txt"
[[ "$(cat "$T/out2.txt")" -le 120 ]] \
  && ok "14 snippet capped at ~120 chars ($(cat "$T/out2.txt"))" || bad "14 snippet too long: $(cat "$T/out2.txt")"

echo "── 15. dashboard renders mixed 4/5-field history rows"
new_env s15
FX="$T/fixture.json"; make_fixture "$FX"
write_herdr_stub "$FX"
{
  printf '%s\tw4:p3\topencode\t12.5\n' "$(date -d '-95 minutes' +%Y-%m-%dT%H:%M:%S)"
  printf '%s\tw4:p3\topencode\t8.0\tRevision con snippet nuevo\n' "$(date -d '-70 minutes' +%Y-%m-%dT%H:%M:%S)"
  printf '%s\tw4:p3\topencode\t30.2\n' "$(date -d '-45 minutes' +%Y-%m-%dT%H:%M:%S)"
  printf '%s\tw9:p9\topencode\t45.0\tOtro snippet de chat cerrado\n' "$(date -d '-25 minutes' +%Y-%m-%dT%H:%M:%S)"
  printf '%s\tw4:p1\topencode\t21.3\n' "$(date -d '-12 minutes' +%Y-%m-%dT%H:%M:%S)"
  printf '%s\tw4:p1\topencode\t60.0\tCierre con detalle final\n' "$(date -d '-2 minutes'  +%Y-%m-%dT%H:%M:%S)"
} > "$HERDR_TTS_HISTORY_FILE"
capture 'q\n' "$T/out.txt"
assert_grep "15 history section renders with mixed rows" 'Historial por chat' "$T/out.txt"
assert_grep "15 group header counts p3 (3 audios)" '· 3 audios · último hace' "$T/out.txt"
na=$(grep -cE '^║    · [0-9]{2}:[0-9]{2} · hace [0-9]+[smh]$' "$T/out.txt")
[[ "$na" -eq 6 ]] && ok "15 audio rows intact (max 3/group): $na" || bad "15 audio rows = $na (want 6)"
assert_grep "15 legacy row renders (00:12)" '· 00:12 · hace 1h' "$T/out.txt"
assert_grep "15 5-field row renders (01:00)" '· 01:00 · hace 2m' "$T/out.txt"
if grep -q $'\t' <(sed 's/\x1b\[[0-9;?]*[a-zA-Z]//g' "$T/out.txt"); then
  bad "15 snippets leaked raw tabs into the frame"
else
  ok "15 snippets never leak raw tabs into the frame"
fi

echo "── 16. voice menu: dispatch map, single write, quit/unknown/EOF, clamps"

# 16a. Dispatch map: every key fires the EXISTING implementation function
#      (stub-verified via overrides) and nothing else.
new_env s16
FX="$T/fixture.json"; make_fixture "$FX"
write_herdr_stub "$FX"
lib_run '
  toggle_play()        { echo "STUB toggle-play"; }
  toggle_pause_audio() { echo "STUB toggle-pause"; }
  stop_audio()         { echo "STUB stop"; }
  read_current_pane()  { echo "STUB tldr:${2:-}"; }
  toggle_auto()        { echo "STUB toggle-auto"; }
  seek_audio()         { echo "STUB seek:${1:-}"; }
  next_sentence_audio(){ echo "STUB next-sentence"; }
  prev_sentence_audio(){ echo "STUB prev-sentence"; }
  toggle_pane_mute()   { echo "STUB mute:${1:-}"; }
  cycle_snooze()       { echo "STUB snooze:${1:-}:${2:-}"; }
  adjust_rate()        { echo "STUB rate:${1:-}"; }
  herdr()              { printf '%s\n' "$*" >> "$T/menu-herdr.log"; }
  for k in r p s t v "[" "]" n N m z Z + - = _ d o; do
    if menu_dispatch "$k" >/dev/null; then
      printf "%s→%s\n" "$k" "$MENU_RESULT"
    else
      printf "%s→ERROR\n" "$k"
    fi
  done
  if menu_dispatch "@" >/dev/null 2>&1; then echo "@→accepted"; else echo "@→rejected"; fi
' > "$T/out.txt"
assert_grep "16a r → toggle_play"              '^r→STUB toggle-play$' "$T/out.txt"
assert_grep "16a p → toggle_pause_audio"       '^p→STUB toggle-pause$' "$T/out.txt"
assert_grep "16a s → stop_audio"               '^s→STUB stop$' "$T/out.txt"
assert_grep "16a t → read_current_pane --tldr" '^t→STUB tldr:--tldr$' "$T/out.txt"
assert_grep "16a v → toggle_auto"              '^v→STUB toggle-auto$' "$T/out.txt"
assert_grep "16a [ → seek -10"                 '^\[→STUB seek:-10$' "$T/out.txt"
assert_grep "16a ] → seek +10"                 '^\]→STUB seek:\+10$' "$T/out.txt"
assert_grep "16a n → next_sentence_audio"      '^n→STUB next-sentence$' "$T/out.txt"
assert_grep "16a N → prev_sentence_audio"      '^N→STUB prev-sentence$' "$T/out.txt"
assert_grep "16a m → toggle_pane_mute (focused default)" '^m→STUB mute:$' "$T/out.txt"
assert_grep "16a z → cycle_snooze pane (focused default)" '^z→STUB snooze:pane:$' "$T/out.txt"
assert_grep "16a Z → cycle_snooze global"      '^Z→STUB snooze:global:$' "$T/out.txt"
assert_grep "16a + → adjust_rate +10"          '^\+→STUB rate:10$' "$T/out.txt"
assert_grep "16a = → adjust_rate +10"          '^=→STUB rate:10$' "$T/out.txt"
assert_grep "16a - → adjust_rate -10"          '^-→STUB rate:-10$' "$T/out.txt"
assert_grep "16a _ → adjust_rate -10"          '^_→STUB rate:-10$' "$T/out.txt"
assert_grep "16a d → confirmation label"       '^d→.*Abriendo el dashboard' "$T/out.txt"
assert_grep "16a d → opens tts-dashboard entrypoint" 'plugin pane open --plugin herdr.tts --entrypoint tts-dashboard' "$T/menu-herdr.log"
assert_grep "16a o → confirmation label"       '^o→.*Abriendo la paleta' "$T/out.txt"
assert_grep "16a o → opens tts-palette entrypoint" 'plugin pane open --plugin herdr.tts --entrypoint tts-palette' "$T/menu-herdr.log"
assert_grep "16a unknown key rejected"         '^@→rejected$' "$T/out.txt"

# 16b. End-to-end 'm': ONE physical frame write + one-line confirmation +
#      the action actually applied through the focused-pane default.
new_env s16b
FX="$T/fixture.json"; make_fixture "$FX"
cat > "$T/bin/herdr" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "pane" && "${2:-}" == "current" ]]; then
  echo '{"result":{"pane":{"pane_id":"w4:p4"}}}'
  exit 0
fi
exit 0
EOF
chmod +x "$T/bin/herdr"
export HERDR_TTS_MENU_CONFIRM_SECS=0.2
printf 'm' | timeout 10 "$SCRIPT" --voice-menu > "$T/out.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "16b menu exited rc=0" || bad "16b menu rc!=0"
hv=$(esc_count "$T/out.txt" $'\033[H')
[[ "$hv" -eq 1 ]] && ok "16b exactly one frame home-move (single write) ($hv)" || bad "16b H-moves=$hv (want 1)"
jv=$(esc_count "$T/out.txt" $'\033[J')
[[ "$jv" -eq 1 ]] && ok "16b erase-below present once ($jv)" || bad "16b J=$jv (want 1)"
cj=$(esc_count "$T/out.txt" $'\033[2J')
[[ "$cj" -eq 0 ]] && ok "16b no full-clear in menu path ($cj)" || bad "16b 2J=$cj (want 0)"
assert_grep "16b frame content rendered" 'Menú de voz' "$T/out.txt"
assert_grep "16b one-line Spanish confirmation" '^✓ .*(silenciado|reactivada)' "$T/out.txt"
jq -e '.panes["w4:p4"].muted == true' "$HERDR_TTS_SNOOZE_FILE" >/dev/null \
  && ok "16b m muted the FOCUSED pane (stub pane current → w4:p4)" || bad "16b focused-pane default not honored"

# 16c. q / Esc: frame renders, no confirmation, silent exit (no dwell).
printf 'q' | timeout 10 "$SCRIPT" --voice-menu > "$T/out.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "16c q exits rc=0" || bad "16c q rc!=0"
assert_grep "16c q still renders the frame" 'Menú de voz' "$T/out.txt"
assert_no_grep "16c q path is silent (no confirmation)" '✓|Tecla no reconocida' "$T/out.txt"
printf '\033' | timeout 10 "$SCRIPT" --voice-menu > "$T/out.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "16c Esc exits rc=0" || bad "16c Esc rc!=0"
assert_no_grep "16c Esc path is silent" '✓|Tecla no reconocida' "$T/out.txt"

# 16d. Unknown key → brief Spanish notice, rc 0.
printf '@' | timeout 10 "$SCRIPT" --voice-menu > "$T/out.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "16d unknown key exits rc=0" || bad "16d rc!=0"
assert_grep "16d unknown key notice" 'Tecla no reconocida \(@\)' "$T/out.txt"

# 16e. Fail-open: EOF (no tty / closed stdin) → rc 0, never any action.
timeout 10 "$SCRIPT" --voice-menu < /dev/null > "$T/out.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "16e EOF exits rc=0 (fail-open)" || bad "16e EOF rc!=0"
assert_no_grep "16e EOF fires no action" '✓|Tecla no reconocida' "$T/out.txt"

# 16f. Clamp reuse: COLUMNS=90 / LINES=10 → width ≤ 90, rows ≤ LINES-1.
new_env s16f
FX="$T/fixture.json"; make_fixture "$FX"
write_herdr_stub "$FX"
export COLUMNS=90 LINES=10
printf 'q' | timeout 10 "$SCRIPT" --voice-menu > "$T/out.txt" 2>>"$T/err.log"
read stats < <(visible_stats "$T/out.txt"); sl=${stats%% *}; nl=${stats#* }
[[ "$sl" -le 90 ]] && ok "16f no line exceeds 90 visible chars (max=$sl)" || bad "16f max width $sl > 90"
[[ "$nl" -le 9 ]] && ok "16f frame rows ≤ LINES-1 = 9 ($nl)" || bad "16f rows $nl > 9"
unset COLUMNS LINES # restore for the wiring greps below

# 16g. Manifest + README wiring shipped with the feature.
assert_grep "16g manifest declares tts-menu pane" 'id = "tts-menu"' "$REPO/herdr-plugin.toml" -F
assert_grep "16g tts-menu runs --voice-menu" 'command = \["bin/herdr-tts", "--voice-menu"\]' "$REPO/herdr-plugin.toml"
assert_grep "16g tts-menu popup is 60%x45%" 'width = "60%"' "$REPO/herdr-plugin.toml" -F
assert_grep "16g open-menu action wired" '"--entrypoint", "tts-menu"' "$REPO/herdr-plugin.toml" -F
assert_grep "16g version bumped to 0.14.1" 'version = "0.14.1"' "$REPO/herdr-plugin.toml" -F
assert_grep "16g README option 1 (menu, recommended)" '### Option 1 — Compact map \(recommended\)' "$REPO/README.md"
assert_grep "16g README option 2 (ctrl+alt family)" '### Option 2 — ctrl\+alt family' "$REPO/README.md"
assert_grep "16g README option 3 (direct map + conflicts)" '### Option 3 — Direct map \(power users\)' "$REPO/README.md"
assert_grep "16g README binds prefix+u to the menu" '"prefix+u"' "$REPO/README.md" -F
assert_grep "16g README documents the ctrl+alt+t caveat" 'ctrl\+alt\+t` launches a terminal' "$REPO/README.md"

echo "── 17. keymap: init / check / emit (declarative, conflict-checked)"
new_env s17
export HERDR_TTS_KEYMAP_FILE="$T/keymap.json"
km="$HERDR_TTS_KEYMAP_FILE"
run_km() { timeout 10 "$SCRIPT" keymap "$@" > "$T/out.txt" 2>&1; }

# 17a. init: template created; refuses silent overwrite; --force replaces.
run_km init
[[ $? -eq 0 ]] && ok "17a init exits rc=0" || bad "17a init rc!=0"
[[ -f "$km" ]] && ok "17a keymap.json created" || bad "17a no keymap file"
jq -e '.style == "direct" and (.bindings | length == 19)' "$km" >/dev/null \
  && ok "17a template: style=direct, 19 stable command ids" || bad "17a template shape"
jq -e '.bindings.play == "prefix+r" and .bindings.snooze_global == "prefix+Z" and .bindings.menu == null' "$km" >/dev/null \
  && ok "17a template prefilled with README direct map (menu=null)" || bad "17a template prefill"
cp "$km" "$T/km.bak"
run_km init
[[ $? -ne 0 ]] && ok "17a init refuses overwrite without --force (rc!=0)" || bad "17a silent overwrite allowed"
assert_grep "17a refusal is actionable (--force hint)" 'keymap init --force' "$T/out.txt" -F
cmp -s "$km" "$T/km.bak" && ok "17a refused init left file untouched" || bad "17a file mutated"
run_km init --force
[[ $? -eq 0 ]] && ok "17a init --force replaces the file" || bad "17a --force rc!=0"
cmp -s "$km" "$T/km.bak" && ok "17a --force rewrote the template" || bad "17a --force content mismatch"

# 17b. check on the default direct map: shadows r/v/z/n/p, warnings only, rc 0.
run_km check
[[ $? -eq 0 ]] && ok "17b check exits rc=0 with warnings only" || bad "17b check rc!=0"
assert_grep "17b prefix+r SHADOWS CORE (resize pane)"     'SHADOWS CORE \(resize pane\): play = prefix\+r' "$T/out.txt"
assert_grep "17b prefix+v SHADOWS CORE (split right)"     'SHADOWS CORE \(split right\): auto = prefix\+v' "$T/out.txt"
assert_grep "17b prefix+z SHADOWS CORE (zoom pane)"       'SHADOWS CORE \(zoom pane\): snooze = prefix\+z' "$T/out.txt"
assert_grep "17b prefix+n SHADOWS CORE (next tab)"        'SHADOWS CORE \(next tab\): sentence_next = prefix\+n' "$T/out.txt"
assert_grep "17b prefix+p SHADOWS CORE (previous tab)"    'SHADOWS CORE \(previous tab\): pause = prefix\+p' "$T/out.txt"
assert_grep "17b prefix+t stays OK (free letter)"          '✓ OK: tldr = prefix\+t' "$T/out.txt"
assert_grep "17b unassigned binding renders OK"            '✓ OK: menu = \(unassigned\)' "$T/out.txt"
assert_grep "17b summary line: zero errors"                '0 errors' "$T/out.txt"

# 17c. check --json: machine-readable, jq-friendly.
run_km check --json
[[ $? -eq 0 ]] && ok "17c check --json exits rc=0" || bad "17c check --json rc!=0"
jq -e '.ok == true and .error_count == 0 and .warning_count >= 5' "$T/out.txt" >/dev/null \
  && ok "17c json: ok=true, 0 errors, ≥5 warnings" || bad "17c json summary fields"
jq -e '(.bindings | length) == 19 and (.bindings[0].command == "play")' "$T/out.txt" >/dev/null \
  && ok "17c json: 19 bindings, file order preserved" || bad "17c json bindings array"
jq -e '.bindings[] | select(.command == "play" and .chord == "prefix+r" and .status == "warn" and .core == "resize pane")' "$T/out.txt" >/dev/null \
  && ok "17c json: play binding carries core=resize pane" || bad "17c json warn detail"
jq -e '.bindings[] | select(.command == "menu" and .chord == null and .status == "ok")' "$T/out.txt" >/dev/null \
  && ok "17c json: null chord serializes as null" || bad "17c json null chord"
jq -e '.bindings[] | select(.command == "sentence_prev" and .chord == "prefix+N" and .core == "new workspace")' "$T/out.txt" >/dev/null \
  && ok "17c json: uppercase key treated as shift (N → new workspace)" || bad "17c json shift expansion"

# 17d. emit (direct): ready-to-paste TOML, header carries file mtime.
run_km emit > /dev/null
cp "$T/out.txt" "$T/emit-direct.toml"
[[ $? -eq 0 ]] && ok "17d emit exits rc=0" || bad "17d emit rc!=0"
assert_grep "17d header names the source file"  "^# source: $km \(modified " "$T/emit-direct.toml"
python3 - "$T/emit-direct.toml" <<'PY' > "$T/py.out" 2>&1 \
  && ok "17d output parses as TOML with 14 shell blocks" || { bad "17d TOML invalid"; cat "$T/py.out"; }
import sys, tomllib
doc = tomllib.loads(open(sys.argv[1]).read())
blocks = doc["keys"]["command"]
assert len(blocks) == 14, f"want 14 blocks, got {len(blocks)}"
assert all(b["type"] == "shell" for b in blocks)
by_key = {b["key"]: b["command"] for b in blocks}
assert by_key["prefix+r"] == "herdr-tts --toggle-play", by_key["prefix+r"]
assert by_key["prefix+N"] == "herdr-tts --prev-sentence"
assert by_key["prefix+Z"] == "herdr-tts --snooze-global"
PY

# 17e. emit --style ctrlalt: suggested family, no ctrl+alt+t, valid TOML.
run_km emit --style ctrlalt > /dev/null
cp "$T/out.txt" "$T/emit-ctrlalt.toml"
[[ $? -eq 0 ]] && ok "17e emit --style ctrlalt exits rc=0" || bad "17e rc!=0"
assert_no_grep_f "17e ctrl+alt+t never suggested as a key" 'key = "ctrl+alt+t"' "$T/emit-ctrlalt.toml"
assert_grep "17e TL;DR lives on ctrl+alt+l"       'key = "ctrl\+alt\+l"' "$T/emit-ctrlalt.toml"
assert_grep "17e caveat documented in output"      'ctrl\+alt\+t.*terminal' "$T/emit-ctrlalt.toml"
python3 - "$T/emit-ctrlalt.toml" <<'PY' > "$T/py.out" 2>&1 \
  && ok "17e ctrlalt TOML valid: 17 distinct chords" || { bad "17e TOML invalid"; cat "$T/py.out"; }
import sys, tomllib
doc = tomllib.loads(open(sys.argv[1]).read())
blocks = doc["keys"]["command"]
keys = [b["key"] for b in blocks]
assert len(blocks) == 17, f"want 17 blocks, got {len(blocks)}"
assert len(set(keys)) == len(keys), "duplicate chords in suggested family"
assert "ctrl+alt+shift+n" in keys
by_key = dict(zip(keys, (b["command"] for b in blocks)))
assert by_key["ctrl+alt+r"] == "herdr-tts --toggle-play"
assert by_key["ctrl+alt+d"] == "herdr plugin pane open --plugin herdr.tts --entrypoint tts-dashboard"
PY

# 17f. emit --style menu: one block, prefix+u → tts-menu popup.
run_km emit --style menu > /dev/null
cp "$T/out.txt" "$T/emit-menu.toml"
python3 - "$T/emit-menu.toml" <<'PY' > "$T/py.out" 2>&1 \
  && ok "17f menu TOML valid: exactly 1 block (prefix+u → tts-menu)" || { bad "17f TOML invalid"; cat "$T/py.out"; }
import sys, tomllib
doc = tomllib.loads(open(sys.argv[1]).read())
blocks = doc["keys"]["command"]
assert len(blocks) == 1, f"want 1 block, got {len(blocks)}"
assert blocks[0]["key"] == "prefix+u"
assert "tts-menu" in blocks[0]["command"]
PY

# 17g. unknown command id → hard error, rc 1, in both human and JSON modes.
jq '.bindings.volume = "prefix+q"' "$km" > "$T/km.tmp" && mv "$T/km.tmp" "$km"
run_km check
[[ $? -ne 0 ]] && ok "17g unknown id rejected (rc!=0)" || bad "17g unknown id accepted"
assert_grep "17g unknown id message" 'unknown command id.*volume' "$T/out.txt"
run_km check --json
[[ $? -ne 0 ]] && ok "17g unknown id rejected in --json (rc!=0)" || bad "17g json rc"
jq -e '.ok == false and (.bindings[] | select(.command == "volume" and .status == "error"))' "$T/out.txt" >/dev/null \
  && ok "17g json marks the unknown binding as error" || bad "17g json error detail"
run_km emit
[[ $? -ne 0 ]] && ok "17g emit refuses a file with unknown ids" || bad "17g emit accepted unknown id"

# 17h. duplicate chord → hard error on both rows.
run_km init --force >/dev/null
jq '.bindings.play = "prefix+u" | .bindings.stop = "prefix+u"' "$km" > "$T/km.tmp" && mv "$T/km.tmp" "$km"
run_km check
[[ $? -ne 0 ]] && ok "17h duplicate chord rejected (rc!=0)" || bad "17h duplicate accepted"
assert_grep "17h play flagged as duplicate"  "duplicate chord prefix\\+u \\(also bound by 'stop'\\)" "$T/out.txt"
assert_grep "17h stop flagged as duplicate"  "duplicate chord prefix\\+u \\(also bound by 'play'\\)" "$T/out.txt"

# 17i. invalid chord syntax → hard error.
run_km init --force >/dev/null
jq '.bindings.play = "alt+x"' "$km" > "$T/km.tmp" && mv "$T/km.tmp" "$km"
run_km check
[[ $? -ne 0 ]] && ok "17i invalid chord 'alt+x' rejected (rc!=0)" || bad "17i alt+x accepted"
assert_grep "17i invalid chord message" 'invalid chord syntax.*play = alt\+x' "$T/out.txt"
jq '.bindings.play = "prefix+a+b"' "$km" > "$T/km.tmp" && mv "$T/km.tmp" "$km"
run_km check
[[ $? -ne 0 ]] && ok "17i multi-key chord 'prefix+a+b' rejected" || bad "17i prefix+a+b accepted"

# 17j. missing keymap file → actionable fail-open (check and emit).
rm -f "$km"; export HERDR_TTS_KEYMAP_FILE="$T/absent.json"
run_km check
[[ $? -eq 1 ]] && ok "17j check exits rc=1 on missing file" || bad "17j check rc"
assert_grep "17j check suggests keymap init" 'keymap init' "$T/out.txt" -F
run_km emit
[[ $? -eq 1 ]] && ok "17j emit exits rc=1 on missing file" || bad "17j emit rc"
assert_grep "17j emit suggests keymap init" 'keymap init' "$T/out.txt" -F
run_km check --json
[[ $? -eq 1 ]] && ok "17j check --json missing file rc=1" || bad "17j json rc"
jq -e '.ok == false and .error' "$T/out.txt" >/dev/null \
  && ok "17j json missing-file payload is still valid JSON" || bad "17j json payload"

# 17k. help wiring: main --help, keymap --help, unknown subcommand.
"$SCRIPT" --help > "$T/out.txt" 2>&1
assert_grep "17k main --help documents keymap init"  'keymap init \[--force\]' "$T/out.txt"
assert_grep "17k main --help documents keymap check" 'keymap check \[--json\]' "$T/out.txt"
assert_grep "17k main --help documents keymap emit"  'keymap emit \[--style S\]' "$T/out.txt"
run_km --help
[[ $? -eq 0 ]] && ok "17k keymap --help exits rc=0" || bad "17k keymap --help rc"
assert_grep "17k keymap help documents init"  'keymap init \[--force\]' "$T/out.txt"
assert_grep "17k keymap help documents check" 'keymap check \[--json\]' "$T/out.txt"
assert_grep "17k keymap help documents emit"  'keymap emit \[--style S\]' "$T/out.txt"
run_km bogus
[[ $? -ne 0 ]] && ok "17k unknown subcommand rejected" || bad "17k bogus accepted"

echo "── 18. keymap apply / adopt (managed block, backups, rollback)"
new_env s18
export HERDR_CONFIG_DIR="$T/conf"
mkdir -p "$HERDR_CONFIG_DIR/herdr"
export HERDR_TTS_KEYMAP_FILE="$T/keymap.json"
km="$HERDR_TTS_KEYMAP_FILE"
cfg="$HERDR_CONFIG_DIR/herdr/config.toml"
run_km() { timeout 10 "$SCRIPT" keymap "$@" > "$T/out.txt" 2>&1; }
bak_count() { local f c=0; for f in "$cfg".bak-*; do if [[ -e "$f" ]]; then c=$((c + 1)); fi; done; printf '%s\n' "$c"; }
# Permissive herdr stub: `herdr config check` passes (rc 0). Every apply in
# this scenario runs against $cfg — never the real ~/.config/herdr config.
printf '#!/usr/bin/env bash\nexit 0\n' > "$T/bin/herdr"
chmod +x "$T/bin/herdr"

# 18a. apply without a keymap refuses; with one, it creates a missing target.
run_km apply
[[ $? -ne 0 ]] && ok "18a apply refuses without keymap file" || bad "18a apply rc"
assert_grep "18a refusal suggests keymap init" 'keymap init' "$T/out.txt" -F
[[ ! -e "$cfg" ]] && ok "18a no target touched on refused apply" || bad "18a target created"
run_km init >/dev/null
[[ $? -eq 0 ]] && ok "18a init seeds the keymap" || bad "18a init rc"
run_km apply
[[ $? -eq 0 ]] && ok "18a apply creates a missing target config" || bad "18a apply rc"
[[ -f "$cfg" ]] && ok "18a target config written" || bad "18a no target"
assert_grep "18a start marker present"  '^# >>> herdr-tts keymap \(managed; edits inside are overwritten\) >>>$' "$cfg"
assert_grep "18a end marker present"    '^# <<< herdr-tts keymap <<<$' "$cfg"
assert_grep "18a final hint: reload-config" 'herdr server reload-config' "$T/out.txt" -F
[[ $(grep -c '^\[\[keys.command\]\]' "$cfg") -eq 14 ]] \
  && ok "18a template apply renders 14 blocks" || bad "18a block count $(grep -c '^\[\[keys.command\]\]' "$cfg")"
run_km apply --config "$T/other-config.toml"
[[ $? -eq 0 ]] && ok "18a --config override honored" || bad "18a --config rc"
assert_grep "18a block written into override path" '^# >>> herdr-tts keymap' "$T/other-config.toml"

# 18b. user content: byte-identical, block appended at END, valid TOML.
cat > "$cfg" <<'EOF'
# bruno's theme
theme = "tokyonight"

[font]
size = 11.0
EOF
cp "$cfg" "$T/user-only.toml"
run_km apply
[[ $? -eq 0 ]] && ok "18b apply rc=0 over user content" || bad "18b apply rc"
nlines=$(wc -l < "$T/user-only.toml")
head -n "$nlines" "$cfg" > "$T/head.out"
cmp -s "$T/head.out" "$T/user-only.toml" \
  && ok "18b user lines byte-identical (block appended after)" || bad "18b user content mutated"
[[ $(grep -cF '# >>> herdr-tts keymap' "$cfg") -eq 1 ]] \
  && ok "18b exactly one managed block" || bad "18b duplicate markers"
python3 - "$cfg" <<'PY' > "$T/py.out" 2>&1 \
  && ok "18b result parses as TOML: user keys intact + 14 shell blocks" || { bad "18b TOML invalid"; cat "$T/py.out"; }
import sys, tomllib
doc = tomllib.loads(open(sys.argv[1]).read())
assert doc["theme"] == "tokyonight" and doc["font"]["size"] == 11.0
blocks = doc["keys"]["command"]
assert len(blocks) == 14 and all(b["type"] == "shell" for b in blocks)
by_key = {b["key"]: b["command"] for b in blocks}
assert by_key["prefix+r"] == "herdr-tts --toggle-play"
PY
assert_grep "18b shadow warnings printed (allowed, not blocking)" 'SHADOWS CORE \(resize pane\): play = prefix\+r' "$T/out.txt"
[[ $(bak_count) -eq 1 ]] && ok "18b one backup after the content-changing write" || bad "18b backups=$(bak_count)"

# 18c. lockstep: managed block body == `keymap emit` output (minus headers).
run_km emit
tail -n +4 "$T/out.txt" > "$T/emit-body.toml"
awk '/^# >>> herdr-tts keymap \(managed/{f=1;next} /^# <<< herdr-tts keymap <<<$/{f=0;next} f' "$cfg" \
  | grep -v '^# generated by:' > "$T/block-body.toml"
cmp -s "$T/emit-body.toml" "$T/block-body.toml" \
  && ok "18c managed block == emit output (lockstep)" || bad "18c emit/apply diverged"

# 18d. idempotent re-apply: no write, no backup.
cp "$cfg" "$T/before-idem.toml"
run_km apply
[[ $? -eq 0 ]] && ok "18d idempotent apply rc=0" || bad "18d rc"
assert_grep "18d already-up-to-date message" 'already up to date' "$T/out.txt" -F
cmp -s "$cfg" "$T/before-idem.toml" && ok "18d file byte-identical on re-apply" || bad "18d file mutated"
[[ $(bak_count) -eq 1 ]] && ok "18d no backup on a no-op apply" || bad "18d backups=$(bak_count)"

# 18e. null-binding removal + user content placed AFTER the block.
printf '\n# my manual footer\ninjected = true\n' >> "$cfg"
jq '.bindings.tldr = null' "$km" > "$T/km.tmp" && mv "$T/km.tmp" "$km"
run_km apply
[[ $? -eq 0 ]] && ok "18e apply after nulling tldr rc=0" || bad "18e rc"
[[ $(grep -c '^\[\[keys.command\]\]' "$cfg") -eq 13 ]] \
  && ok "18e tldr block removed (13 blocks)" || bad "18e block count"
assert_no_grep_f "18e --tldr command gone from config" 'herdr-tts --tldr' "$cfg"
assert_grep "18e user footer preserved" '^injected = true$' "$cfg"
eline=$(grep -nF '# <<< herdr-tts keymap' "$cfg" | cut -d: -f1)
fline=$(grep -n '^injected = true$' "$cfg" | cut -d: -f1)
[[ "$fline" -gt "$eline" ]] \
  && ok "18e user content after the block stays after (in-place replace)" || bad "18e footer moved"
[[ $(bak_count) -eq 2 ]] && ok "18e second backup created" || bad "18e backups=$(bak_count)"

# 18f. backups pruned to the last 3 across changing applies.
for c in q w e; do
  jq --arg c "prefix+$c" '.bindings.play = $c' "$km" > "$T/km.tmp" && mv "$T/km.tmp" "$km"
  run_km apply >/dev/null
  sleep 1 # distinct backup timestamps → deterministic prune order
done
[[ $? -eq 0 ]] && ok "18f three changing applies rc=0" || bad "18f rc"
[[ $(bak_count) -eq 3 ]] && ok "18f backups pruned to 3" || bad "18f backups=$(bak_count)"
assert_grep "18f newest state applied" '^key = "prefix\+e"$' "$cfg"
newest="$(ls -1 "$cfg".bak-* | sort | tail -1)"
grep -q '^key = "prefix+w"$' "$newest" \
  && ok "18f newest backup holds the previous chord (w)" || bad "18f newest backup content wrong"

# 18g. dry-run: prints the diff, writes nothing.
cp "$cfg" "$T/pre-dry.toml"
jq '.bindings.tldr = "prefix+t"' "$km" > "$T/km.tmp" && mv "$T/km.tmp" "$km"
run_km apply --dry-run
[[ $? -eq 0 ]] && ok "18g dry-run rc=0" || bad "18g rc"
assert_grep "18g diff shows the added block" '^\+\[\[keys.command\]\]' "$T/out.txt"
assert_grep "18g diff header present" '^--- ' "$T/out.txt"
cmp -s "$cfg" "$T/pre-dry.toml" && ok "18g config byte-identical after dry-run" || bad "18g config written"
[[ $(bak_count) -eq 3 ]] && ok "18g no backup from dry-run" || bad "18g backups=$(bak_count)"
run_km apply >/dev/null
run_km apply --dry-run
assert_grep "18g dry-run on unchanged state: up to date" 'already up to date' "$T/out.txt" -F

# 18h. hard-error keymap refuses to apply, writes nothing.
cp "$cfg" "$T/pre-err.toml"
jq '.bindings.play = "alt+bad"' "$km" > "$T/km.tmp" && mv "$T/km.tmp" "$km"
run_km apply
[[ $? -ne 0 ]] && ok "18h invalid keymap refuses apply" || bad "18h rc"
assert_grep "18h refusal is actionable" 'refusing to apply' "$T/out.txt"
cmp -s "$cfg" "$T/pre-err.toml" && ok "18h config untouched on refusal" || bad "18h config mutated"
[[ $(bak_count) -eq 3 ]] && ok "18h no backup on refusal" || bad "18h backups=$(bak_count)"

# 18i. herdr config check failure → automatic rollback.
run_km init --force >/dev/null # reset: 18h left an invalid chord on purpose
cat > "$T/bin/herdr" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "config" && "${2:-}" == "check" ]]; then
  echo "error: invalid key binding in config" >&2
  exit 1
fi
exit 0
EOF
chmod +x "$T/bin/herdr"
cp "$cfg" "$T/pre-rollback.toml"
jq '.bindings.snooze = "prefix+y"' "$km" > "$T/km.tmp" && mv "$T/km.tmp" "$km"
run_km apply
[[ $? -ne 0 ]] && ok "18i apply fails when herdr config check fails" || bad "18i rc"
cmp -s "$cfg" "$T/pre-rollback.toml" \
  && ok "18i config rolled back byte-identical" || bad "18i rollback mismatch"
assert_grep "18i rollback message" 'rolled back' "$T/out.txt"
assert_grep "18i herdr error surfaced" 'invalid key binding in config' "$T/out.txt" -F
printf '#!/usr/bin/env bash\nexit 0\n' > "$T/bin/herdr"; chmod +x "$T/bin/herdr"

# 18j. herdr missing from PATH → skip note, apply still succeeds.
OLDPATH="$PATH"
PATH="/usr/bin:/bin"
if command -v herdr >/dev/null 2>&1; then
  bad "18j test env: herdr unexpectedly present in /usr/bin:/bin"
else
  ok "18j test env: herdr hidden (PATH=/usr/bin:/bin)"
fi
jq '.bindings.snooze = "prefix+z"' "$km" > "$T/km.tmp" && mv "$T/km.tmp" "$km"
run_km apply
[[ $? -eq 0 ]] && ok "18j apply succeeds without herdr binary" || bad "18j rc"
assert_grep "18j skip note when herdr absent" "skipped post-write config validation" "$T/out.txt" -F
PATH="$OLDPATH"

# 18k. adopt: require --style, idempotent, refuses modified, --force, seeds.
run_km init --force >/dev/null
run_km adopt
[[ $? -ne 0 ]] && ok "18k adopt without --style rejected" || bad "18k rc"
assert_grep "18k usage hint names --style" 'requires --style' "$T/out.txt"
run_km adopt --style bogus
[[ $? -ne 0 ]] && ok "18k unknown style rejected" || bad "18k rc"
cp "$km" "$T/km-direct.bak"
run_km adopt --style direct
[[ $? -eq 0 ]] && ok "18k adopt direct on pristine template: already adopted" || bad "18k rc"
assert_grep "18k already-adopted message" 'already adopts' "$T/out.txt" -F
cmp -s "$km" "$T/km-direct.bak" && ok "18k already-adopted left file untouched" || bad "18k file mutated"
run_km adopt --style ctrlalt
[[ $? -eq 0 ]] && ok "18k adopt ctrlalt rc=0" || bad "18k rc"
jq -e '.style == "ctrlalt"' "$km" >/dev/null && ok "18k style field updated" || bad "18k style"
jq -e '[.bindings | to_entries[] | select(.value != null)] | length == 17' "$km" >/dev/null \
  && ok "18k 17 non-null ctrlalt bindings" || bad "18k binding count"
jq -e '.bindings.play == "ctrl+alt+r" and .bindings.tldr == "ctrl+alt+l" and .bindings.dashboard == "ctrl+alt+d"' "$km" >/dev/null \
  && ok "18k chords match the suggested family" || bad "18k chords"
jq -e '.bindings.paragraph_next == null and .bindings.paragraph_prev == null' "$km" >/dev/null \
  && ok "18k chords without a ctrlalt suggestion stay null" || bad "18k nulls"
assert_no_grep_f "18k ctrl+alt+t never written" '"ctrl+alt+t"' "$km"
[[ -n "$(ls "$km".bak-* 2>/dev/null)" ]] \
  && ok "18k keymap backup created on adopt" || bad "18k no keymap backup"
cp "$km" "$T/km-ctrlalt.bak"
run_km adopt --style ctrlalt
[[ $? -eq 0 ]] && ok "18k adopt ctrlalt idempotent" || bad "18k rc"
assert_grep "18k idempotent message" 'already adopts' "$T/out.txt" -F
cmp -s "$km" "$T/km-ctrlalt.bak" && ok "18k idempotent left file untouched" || bad "18k mutated"
jq '.bindings.play = "prefix+q"' "$km" > "$T/km.tmp" && mv "$T/km.tmp" "$km"
cp "$km" "$T/km-modified.bak"
run_km adopt --style menu
[[ $? -ne 0 ]] && ok "18k adopt refuses a modified keymap" || bad "18k rc"
assert_grep "18k refusal names --force" 'adopt --style menu --force' "$T/out.txt" -F
cmp -s "$km" "$T/km-modified.bak" && ok "18k refusal left file untouched" || bad "18k mutated"
run_km adopt --style menu --force
[[ $? -eq 0 ]] && ok "18k forced adopt rc=0" || bad "18k rc"
jq -e '.style == "menu" and .bindings.menu == "prefix+u"' "$km" >/dev/null \
  && ok "18k menu map: menu=prefix+u" || bad "18k menu chord"
jq -e '[.bindings | to_entries[] | select(.value != null)] | length == 1' "$km" >/dev/null \
  && ok "18k menu map: exactly one binding" || bad "18k menu count"
run_km adopt --style menu
[[ $? -eq 0 ]] && ok "18k adopt menu idempotent" || bad "18k rc"
assert_grep "18k menu idempotent message" 'already adopts' "$T/out.txt" -F
rm -f "$km"
run_km adopt --style ctrlalt
[[ $? -eq 0 ]] && ok "18k adopt seeds a missing keymap file" || bad "18k rc"
jq -e '.style == "ctrlalt" and .bindings.play == "ctrl+alt+r"' "$km" >/dev/null \
  && ok "18k seeded file holds the ctrlalt map" || bad "18k seed content"

# 18l. help wiring for the new subcommands.
"$SCRIPT" --help > "$T/out.txt" 2>&1
assert_grep "18l main --help documents keymap apply" 'keymap apply' "$T/out.txt"
assert_grep "18l main --help documents keymap adopt" 'keymap adopt --style' "$T/out.txt"
run_km --help
[[ $? -eq 0 ]] && ok "18l keymap --help rc=0" || bad "18l rc"
assert_grep "18l help documents apply" 'keymap apply \[--config P\] \[--dry-run\]' "$T/out.txt"
assert_grep "18l help documents adopt" 'keymap adopt --style S' "$T/out.txt"
run_km bogus
[[ $? -ne 0 ]] && ok "18l unknown subcommand rejected" || bad "18l rc"
assert_grep "18l unknown message lists the family" 'init, check, emit, apply or adopt' "$T/out.txt" -F

echo "── 19. daemon_keymap_autostart (startup auto-apply)"
new_env s19
export HERDR_CONFIG_DIR="$T/conf"
mkdir -p "$HERDR_CONFIG_DIR/herdr"
export HERDR_TTS_KEYMAP_FILE="$T/keymap19.json"
km19="$HERDR_TTS_KEYMAP_FILE"
cfg19="$HERDR_CONFIG_DIR/herdr/config.toml"
printf '#!/usr/bin/env bash\nexit 0\n' > "$T/bin/herdr"
chmod +x "$T/bin/herdr"

# 19a. no keymap file → silent no-op, target never created
lib_run 'daemon_keymap_autostart >"$T/out19.txt" 2>&1; echo $? > "$T/rc19"'
rc19=$(cat "$T/rc19"); out19=$(cat "$T/out19.txt")
[[ $rc19 -eq 0 && ! -e "$cfg19" && -z "$out19" ]] \
  && ok "19a missing keymap: silent no-op, no target" || bad "19a rc=$rc19 out='$out19' target=$([[ -e "$cfg19" ]] && echo yes)"

# 19b. valid keymap + user content → block applied once, user lines intact
HERDR_TTS_KEYMAP_FILE="$km19" timeout 10 "$SCRIPT" keymap init >/dev/null 2>&1
printf '# mi binding manual\n' > "$cfg19"
bak_before=$(ls "$cfg19".bak-* 2>/dev/null | wc -l)
lib_run 'daemon_keymap_autostart >"$T/out19.txt" 2>&1; echo $? > "$T/rc19"'
rc19=$(cat "$T/rc19"); out19=$(cat "$T/out19.txt")
grep -q '>>> herdr-tts keymap' "$cfg19" && ok "19b managed block written" || bad "19b no block"
grep -q 'mi binding manual' "$cfg19" && ok "19b user content preserved" || bad "19b user content lost"
grep -q 'Keymap aplicado' <<<"$out19" && ok "19b success notice shown" || bad "19b notice: $out19"
[[ $rc19 -eq 0 ]] && ok "19b rc 0" || bad "19b rc=$rc19"

# 19c. unchanged re-run → no write, no extra backup
bak_after=$(ls "$cfg19".bak-* 2>/dev/null | wc -l)
lib_run 'daemon_keymap_autostart >"$T/out19.txt" 2>&1'
bak_final=$(ls "$cfg19".bak-* 2>/dev/null | wc -l)
[[ $bak_before -eq 0 && $bak_after -eq 1 && $bak_final -eq 1 && ! -s "$T/out19.txt" ]] \
  && ok "19c idempotent: one backup total, silent re-run" || bad "19c backups $bak_before/$bak_after/$bak_final out='$(cat "$T/out19.txt")'"

# 19d. corrupt keymap → non-fatal warning, target untouched
printf 'not json {{{' > "$km19"
lib_run 'daemon_keymap_autostart >"$T/out19.txt" 2>&1; echo $? > "$T/rc19"'
rc19=$(cat "$T/rc19"); out19=$(cat "$T/out19.txt")
[[ $rc19 -eq 0 ]] && ok "19d corrupt keymap non-fatal rc" || bad "19d rc=$rc19"
grep -q 'keymap apply falló' <<<"$out19" && ok "19d warning surfaced" || bad "19d no warning: $out19"
grep -q '>>> herdr-tts keymap' "$cfg19" && ok "19d target untouched by failed apply" || bad "19d target modified"

echo
echo "═══ RESULT: $PASS passed, $FAIL failed ═══"
exit $(( FAIL > 0 ? 1 : 0 ))
