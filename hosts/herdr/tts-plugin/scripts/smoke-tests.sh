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
#   14    history snippet: sanitize + 4/5/6-field append matrix (stored
#         audio path, literal "-" mid-row when snippet empty)
#   15    dashboard renders mixed 4/5/6-field history rows; ▶ replay marker
#         only on rows whose stored file exists
#   16    voice menu: dispatch map (stub-verified, one key = one existing
#         function), single-write frame, quit/Esc/unknown/EOF paths,
#         width+height clamp reuse, manifest/README wiring, two views
#         (`a` opens the settings INDEX inside the menu, `v` enters the
#         Voz category, `q` backs out to the index and then the main
#         frame, provider cycle persists into a hermetic config.env via
#         HERDR_TTS_CONFIG_FILE), settings `v` (Lectura) toggles the
#         auto_muted marker both ways with inline notes, a/i/f/b cycles
#         persist scope/lang/podcast/debounce, t free-text topic (empty
#         clears), transient status row separated by a blank row
#   17    keymap: init (template, no-overwrite, --force), check (core
#         shadow warnings, --json), emit (direct/ctrlalt/menu TOML),
#         invalid ids/chords/duplicates rejected, missing file actionable
#         (settings ships UNASSIGNED: it lives inside the voice menu)
#   18    keymap apply / adopt: managed block into a fixture config.toml
#         (user content byte-identical, in-place replace, lockstep with
#         emit), idempotent re-apply, null-binding removal, backups
#         created + pruned to 3, dry-run zero writes, invalid keymap
#         refuses, herdr-check failure → rollback, herdr missing → skip
#         note, adopt (require --style, idempotent, refuses modified
#         without --force, ctrlalt/menu maps, seeds missing file)
#   21    palette ctrl-r replay bind: stubbed fzf argv records the
#         --play-file {4} bind and the header hint; entries carry the
#         stored path as the 4th hidden field
#   22    play_audio_file: mutex stop + engine --play-file spawn on a real
#         file, graceful rc=1 + no spawn on a missing/empty path
#   23    audio_retention_days knob precedence (HERDR_ > AGENT_ > TTS_ >
#         default 0 = opt-in, invalid → 0, positive days enables) + audio_store_dir
#   24    audio_store_prune from the daemon sweep: stateful stub records
#         the engine retention spawn; first call spawns + arms the hourly
#         stamp (silent on success), immediate second call is gated,
#         retention 0 skips without spawn or stamp, a stale stamp (touch
#         -d) re-arms the spawn, and a failing engine is logged once with
#         the daemon loop unharmed
#   25    settings popup: config_set managed-key writer (in-place replace,
#         managed-block append, byte-for-byte unknown lines, .bak backup,
#         unmanaged key + quote-in-value rejected, unwritable dir fail-open,
#         missing file created sourceable), settings_cycle_value full-cycle
#         wrap + unknown-current fallback per key, run_voice_settings with
#         piped keys (frame + persisted config.env + re-render), and the
#         --voice-settings dispatch smoke
#   26    daemon lifecycle: daemon_stop_running (pidfile kill + cmdline
#         guard, legacy pkill fallback without pidfile, nothing-running
#         "none" fail-open), daemon_restart success path through the
#         SCRIPT re-invocation (HERDR_TTS_SCRIPT points at a recorder stub
#         that logs its argv and writes the pidfile; the timeout warning
#         path is NOT exercised here), settings key R renders the
#         confirmation note inline, and the --restart-daemon dispatch smoke
#   27    watcher settle window: TTS_SETTLE_SECONDS sanitization +
#         config_set acceptance, an intermediate working→done→working
#         flicker inside the window is logged and dropped BEFORE the
#         pipeline, and a done that holds past the window reaches the
#         pipeline branch
#   28    settings popup web redirect: w free-text URL input (verbatim
#         {pane_id}, COLLIE_URL rides along, collie seeds WEB_LABEL,
#         empty input clears) and c click-directo on/off cycle, both
#         persisted to a hermetic config.env through the managed writer;
#         frame rows render Desactivada/the URL and the restart hint
#         line names the web redirect
#   29    daemon supervisor: stop-flag semantics (flag armed after start
#         → child death does NOT relaunch and the flag is consumed; no
#         flag → relaunch is logged), stale flag cleared on start,
#         TERM/INT forwarded to the child with a clean orphan-free exit,
#         dispatch + manifest wiring, run_daemon startup/exit-reason
#         logging through the daemon.log helper
#   30    config_set upsert dedupe: duplicate keys inside (and across)
#   31    voz por agente/chat (PRD HT-02): voices.json rules via
#         --voice-for (pane/agent, off deletes), precedence pane >
#         agent > global + spoken prefix, malformed file fail-open with
#         daemon.log line, deterministic auto_assign rotation, fzf
#         picker persists, palette ctrl-v routes --voice-for pane
#         the managed block collapse to exactly one line with the new
#         value, repeated writes never grow the block, unknown lines and
#         markers stay byte-identical, file stays bash-sourceable
#   32    ajustes two-level navigation: the index renders the 4
#         categories (v voice, a audio, n notifications, r reading —
#         English mnemonics; r is lowercase-only because R restarts)
#         plus the index-level l language row, and no raw knobs, each category view renders
#         exactly its own knobs, in-category cycles persist through the
#         writer, Esc from a category returns to the index, Esc/q from
#         the index exits, R restarts from the standalone index,
#         unknown keys warn with category-scoped hints
#   37    layer boundary audit (RF-HT-13): the provider cycle list comes
#         from the engine catalog (a catalog-only canary provider is
#         reachable) and wraps through the static legacy table when the
#         engine is down (fail-open, RNF-HT-13-2); static audit over the
#         host: no static voice catalog (RF-HT-13-4), no inline audio
#         decode (RF-HT-13-2), no internal engine module imports (public
#         `from agent_tts import` surface only)
#   38    tema claro (HT-14): TTS_THEME managed key (accept/reject, no
#         filesystem side effect on reject), theme_color sole-emitter
#         static gate + dark byte-identity via differential captures,
#         light map + compound-width parity, Apariencia category: t
#         opens/cycles the theme, persists across 10 cold starts, scoped
#         warnings, bilingual copy
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
strip_ansi() { # $1 = file -> ANSI-free copy on stdout (plain-text diffs)
  python3 - "$1" <<'PY'
import re, sys
sys.stdout.write(re.sub(r'\x1b\[[0-9;?]*[a-zA-Z]', '', open(sys.argv[1]).read()))
PY
}
# Clock/age-blanked frame copy (colors intact) for stable capture diffs:
# the dashboard embeds date +%H:%M:%S and per-second age labels, so raw
# captures can never diff byte-for-byte across runs.
norm_frame() { # $1 = file -> normalized copy on stdout
  sed -E -e 's/[0-9]{2}:[0-9]{2}:[0-9]{2}/HH:MM:SS/g' \
         -e 's/[0-9]+[smh] ago/Nm ago/g' "$1"
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
# Smoke-suite marker: the legacy-daemon sweep only kills processes that
# carry it too, so a REAL daemon running on this machine can never be
# caught in scenario 26b's live sweep.
export HERDR_TTS_SMOKE=1
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

make_history() { # $1 out file, $2 optional stored-audio path (6th field on every row)
  local audio_path="${2:-}" pfx=""
  [[ -n "$audio_path" ]] && pfx=$'\t'"$audio_path"
  {
    printf '%s\tw4:p3\topencode\t12.5%s\n' "$(date -d '-95 minutes' +%Y-%m-%dT%H:%M:%S)" "$pfx"
    printf '%s\tw4:p3\topencode\t8.0%s\n'  "$(date -d '-70 minutes' +%Y-%m-%dT%H:%M:%S)" "$pfx"
    printf '%s\tw4:p3\topencode\t30.2%s\n' "$(date -d '-45 minutes' +%Y-%m-%dT%H:%M:%S)" "$pfx"
    printf '%s\tw4:p3\topencode\t5.0%s\n'  "$(date -d '-40 minutes' +%Y-%m-%dT%H:%M:%S)" "$pfx"
    printf '%s\tw9:p9\topencode\t45.0%s\n' "$(date -d '-25 minutes' +%Y-%m-%dT%H:%M:%S)" "$pfx"
    printf '%s\tw4:p1\topencode\t21.3%s\n' "$(date -d '-12 minutes' +%Y-%m-%dT%H:%M:%S)" "$pfx"
    printf '%s\tw4:p1\topencode\t60.0%s\n' "$(date -d '-2 minutes'  +%Y-%m-%dT%H:%M:%S)" "$pfx"
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
assert_grep "history section header" 'History per chat' "$T/out.txt"
assert_grep "group header counts p1 (2 audios)" '· 2 audios · last' "$T/out.txt"
assert_grep "group header counts p3 (4 audios)" '· 4 audios · last' "$T/out.txt"
assert_grep "closed pane group labeled by pane id" '── w9:p9 ' "$T/out.txt"
h1=$(grep -n '· 2 audios' "$T/out.txt" | head -1 | cut -d: -f1)
h9=$(grep -n 'w9:p9' "$T/out.txt" | head -1 | cut -d: -f1)
h3=$(grep -n '· 4 audios' "$T/out.txt" | head -1 | cut -d: -f1)
[[ -n "$h1" && -n "$h9" && -n "$h3" && "$h1" -lt "$h9" && "$h9" -lt "$h3" ]] \
  && ok "history groups sorted by last-audio recency (p1 < p9 < p3)" || bad "history group order wrong ($h1/$h9/$h3)"
na=$(grep -cE '^║    · [0-9]{2}:[0-9]{2} · [0-9]+[smh] ago$' "$T/out.txt")
[[ "$na" -eq 6 ]] && ok "audio rows: 2+1+3 = 6 (max 3 per group)" || bad "audio rows = $na (want 6)"
assert_grep "duration+age: 01:00 two minutes ago" '· 01:00 · 2m ago' "$T/out.txt"
assert_grep "duration+age: 00:21 twelve minutes ago" '· 00:21 · 12m ago' "$T/out.txt"
assert_grep "duration+age: 00:45 twentyfive minutes ago (closed pane)" '· 00:45 · 25m ago' "$T/out.txt"
assert_grep "p1 header age: last 2m ago" '── .* · 2 audios · last 2m ago' "$T/out.txt"
assert_grep "engine fallback line intact (v2 regression)" 'engine: not responding' "$T/out.txt"
assert_grep "global state line intact" 'global snooze off' "$T/out.txt"
assert_grep "config line intact" 'provider edge' "$T/out.txt"
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
frames=$(grep -c 'Voice Panel' "$T/out.txt")
[[ "$frames" -ge 2 ]] && ok "multiple frames rendered ($frames)" || bad "only $frames frame(s)"
last_frame=$(awk '/Voice Panel/{buf=""} {buf=buf $0 "\n"} END{printf "%s", buf}' "$T/out.txt")
printf '%s' "$last_frame" > "$T/last_frame.txt"
m1=$(grep -n '▸ ' "$T/last_frame.txt" | head -1 | cut -d: -f1)
c2=$(grep -n 'Refactor del watcher' "$T/last_frame.txt" | head -1 | cut -d: -f1)
[[ -n "$m1" && -n "$c2" && "$m1" -eq "$c2" ]] \
  && ok "j moved cursor to 2nd roster line (working chat)" || bad "cursor mismatch (marker=$m1 line2=$c2)"
assert_grep "snoozed chat shows 😴 overlay after z" 'Refactor del watcher.*😴' "$T/last_frame.txt"
assert_grep "mute msg surfaced in panel" 'muted' "$T/out.txt"
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
assert_grep "overflow notice present" 'and [0-9]+ more chats' "$T/out.txt"
read stats < <(visible_stats "$T/out.txt"); sl=${stats#* }
[[ "$sl" -le 39 ]] && ok "total frame rows ≤ LINES-1 = 39 ($sl)" || bad "frame overflow: $sl rows"
assert_no_grep "history section hidden without ledger" 'History per chat' "$T/out.txt"

echo "── 4. fail-open: no herdr CLI"
new_env s4
make_history "$HERDR_TTS_HISTORY_FILE"
env PATH="/usr/bin:/bin" HOME="$HOME" LINES=40 COLUMNS=110 timeout 30 "$SCRIPT" --dashboard < /dev/null > "$T/out.txt" 2>>"$T/err.log" || true
assert_grep "roster shows no herdr data" 'no herdr data' "$T/out.txt"
assert_grep "history still renders from ledger" 'History per chat' "$T/out.txt"
assert_grep "closed-pane labels survive without roster" '── w4:p3 ' "$T/out.txt"

echo "── 5. empty history → section hidden"
new_env s5
FX="$T/fixture.json"; make_fixture "$FX"
write_herdr_stub "$FX"
rm -f "$HERDR_TTS_HISTORY_FILE"
capture 'q\n' "$T/out.txt"
assert_no_grep "history section hidden" 'History per chat' "$T/out.txt"
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
frames=$(grep -c 'Voice Panel' "$T/out.txt")
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
assert_no_grep "history shrunk away first (budget 24 → 0)" 'History per chat' "$T/out.txt"
assert_grep "done chat DONEONE kept" 'DONEONE' "$T/out.txt"
assert_grep "done chat DONETWO kept" 'DONETWO' "$T/out.txt"
assert_grep "idle chats dropped with notice" 'and [0-9]+ more chats' "$T/out.txt"
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
assert_grep "footer survives the minimum fit" 'q quit' "$T/out.txt"
assert_grep "done chats survive the minimum fit" 'DONEONE' "$T/out.txt"
assert_grep "'+N chats' notice rendered" 'and 30 more chats' "$T/out.txt"

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
# chronological order (append-only ledger): p1 snippet row, p1 legacy row,
# p2 row with a stored-audio path (6th ledger field)
STORE12="$T/store/p2-turn.mp3"
{
  printf '%s\tw4:p1\topencode\t21.3\tPrimera vuelta del chat uno\n' "$(date -d '-12 minutes' +%Y-%m-%dT%H:%M:%S)"
  printf '%s\tw4:p1\topencode\t60.0\n' "$(date -d '-2 minutes' +%Y-%m-%dT%H:%M:%S)"
  printf '%s\tw4:p2\tagy\t8.0\tRespuesta mas reciente de todas\t%s\n' "$(date -d 'now' +%Y-%m-%dT%H:%M:%S)" "$STORE12"
} > "$HERDR_TTS_HISTORY_FILE"
lib_run 'palette_build_entries' > "$T/out.txt"
[[ "$(wc -l < "$T/out.txt")" -eq 4 ]] \
  && ok "12 4 entries: 3 audios + 1 zero-audio chat" || bad "12 entry count = $(wc -l < "$T/out.txt") (want 4)"
nfields=$(awk -F'\t' '{print NF}' "$T/out.txt" | sort -u | tr '\n' ' ')
[[ "$nfields" == "4 " ]] && ok "12 every entry has 4 tab-delimited fields" || bad "12 field counts: $nfields"
mapfile -t plines < "$T/out.txt"
l1="${plines[0]:-}"; l2="${plines[1]:-}"; l3="${plines[2]:-}"; l4="${plines[3]:-}"
[[ "${l1%%$'\t'*}" == *"· 00:08 · Respuesta mas reciente de todas" ]] \
  && ok "12 newest audio first (p2 row leads)" || bad "12 first entry not newest: ${l1%%$'\t'*}"
[[ "$(printf '%s' "$l1" | cut -f4)" == "$STORE12" ]] \
  && ok "12 stored path rides as the 4th hidden field" || bad "12 4th field wrong: $(printf '%s' "$l1" | cut -f4)"
[[ "${l2%%$'\t'*}" == *"· 01:00 · -" ]] \
  && ok "12 legacy 4-field row renders '-' snippet" || bad "12 legacy row wrong: ${l2%%$'\t'*}"
[[ "$(printf '%s' "$l2" | cut -f2)" == "w4:p1" && "$(printf '%s' "$l2" | cut -f3)" =~ ^[0-9]+$ ]] \
  && ok "12 hidden fields parse: pane_id + numeric epoch" || bad "12 hidden fields wrong: $l2"
[[ "$l4" == "(no audios) · Gem | Sin audios todavia"$'\t'"w4:p3"$'\t'"chat"$'\t'"-" ]] \
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
assert_grep "13 header shows agent+status" 'agent: opencode · status: done' "$T/out.txt"
assert_grep "13 gating shows the active debounce hold" 'gating: .*⏱ debounce active \([0-9]+s\)' "$T/out.txt"
assert_grep "13 newest turn first with legacy '-' snippet" '· 01:00 · -$' "$T/out.txt"
assert_grep "13 older turn shows snippet" '· 00:21 · Primera vuelta del chat uno$' "$T/out.txt"
t1=$(grep -n '· 01:00 · -' "$T/out.txt" | head -1 | cut -d: -f1)
t2=$(grep -n '· 00:21 · Primera' "$T/out.txt" | head -1 | cut -d: -f1)
[[ -n "$t1" && -n "$t2" && "$t1" -lt "$t2" ]] \
  && ok "13 turns sorted newest first" || bad "13 turn order wrong"
lib_run 'palette_preview w9:zz' > "$T/out2.txt"
assert_grep "13 empty history → no turns" '^no turns$' "$T/out2.txt"
make_nobin "$T/nobin"
env PATH="$T/nobin" HOME="$HOME" XDG_CONFIG_HOME="$T/conf" XDG_DATA_HOME="$T/data" \
  XDG_STATE_HOME="$T/state" HERDR_TTS_SNOOZE_FILE="$HERDR_TTS_SNOOZE_FILE" HERDR_TTS_HISTORY_FILE="$HERDR_TTS_HISTORY_FILE" \
  /bin/bash "$LIBRUN" "$SCRIPT" 'palette_preview w4:p1' > "$T/out3.txt" 2>/dev/null
assert_grep "13 no herdr CLI → explicit note" 'herdr CLI unavailable' "$T/out3.txt"
assert_grep "13 no-herdr preview still lists turns" '· 00:21 · Primera' "$T/out3.txt"
env PATH="$T/nobin" HOME="$HOME" XDG_CONFIG_HOME="$T/conf" XDG_DATA_HOME="$T/data" \
  XDG_STATE_HOME="$T/state" HERDR_TTS_SNOOZE_FILE="$HERDR_TTS_SNOOZE_FILE" HERDR_TTS_HISTORY_FILE="$HERDR_TTS_HISTORY_FILE" \
  /bin/bash "$LIBRUN" "$SCRIPT" 'run_voice_palette' > "$T/out4.txt" 2>&1
assert_grep "13 no fzf → actionable error" 'fzf is not installed' "$T/out4.txt"
assert_grep "13 no-fzf error suggests install command" 'apt install fzf|brew install fzf' "$T/out4.txt"

echo "── 14. history snippet: sanitize, 4/5/6-field append matrix"
new_env s14
FX="$T/fixture.json"; make_fixture "$FX"
write_herdr_stub "$FX"
mkdir -p "$T/store"
lib_run '
  s=""
  history_snippet s "$(printf "Hola\r\nmundo\t  con   \x1b[31mANSI\x1b[0m colorea  y\nsigue ")"
  echo "[$s]"
  append_audio_history w4:p1 opencode 12.5 "texto limpio de prueba"
  append_audio_history w4:p2 agy 3.0 ""
  append_audio_history w4:p3 gemini 7.5 "" "'"$T"'/store/p3-turn.mp3"
  append_audio_history w4:p4 claude 9.9 "Con path y snippet" "'"$T"'/store/p4-turn.mp3"
' > "$T/out.txt"
assert_grep "14 snippet single-line + ANSI stripped + whitespace collapsed" '^\[Hola mundo con ANSI colorea y sigue\]$' "$T/out.txt"
[[ "$(awk -F'\t' 'NR==1{print NF}' "$HERDR_TTS_HISTORY_FILE")" -eq 5 ]] \
  && ok "14 non-empty snippet → 5-field row" || bad "14 first row not 5-field"
grep -qE $'^[^\t]+\tw4:p1\topencode\t12\.5\ttexto limpio de prueba$' "$HERDR_TTS_HISTORY_FILE" \
  && ok "14 row schema ts/pane/agent/duration/snippet" || bad "14 row schema wrong: $(head -1 "$HERDR_TTS_HISTORY_FILE" | cat -A)"
[[ "$(awk -F'\t' 'NR==2{print NF}' "$HERDR_TTS_HISTORY_FILE")" -eq 4 ]] \
  && ok "14 empty snippet → legacy 4-field row (no empty tail)" || bad "14 second row not 4-field"
row3=$(sed -n '3p' "$HERDR_TTS_HISTORY_FILE")
[[ "$(printf '%s' "$row3" | awk -F'\t' '{print NF}')" -eq 6 ]] \
  && ok "14 path + empty snippet → 6-field row" || bad "14 third row not 6-field: $row3"
[[ "$(printf '%s' "$row3" | cut -f5)" == "-" && "$(printf '%s' "$row3" | cut -f6)" == "$T/store/p3-turn.mp3" ]] \
  && ok "14 empty mid-row snippet becomes literal '-'" || bad "14 field5/6 wrong: $row3"
row4=$(sed -n '4p' "$HERDR_TTS_HISTORY_FILE")
[[ "$(printf '%s' "$row4" | awk -F'\t' '{print NF}')" -eq 6 \
   && "$(printf '%s' "$row4" | cut -f5)" == "Con path y snippet" \
   && "$(printf '%s' "$row4" | cut -f6)" == "$T/store/p4-turn.mp3" ]] \
  && ok "14 path + snippet → 6-field row, both columns intact" || bad "14 fourth row wrong: $row4"
lib_run '
  big="$(printf "pad %.0s" $(seq 1 200))"
  s=""
  history_snippet s "$big"
  echo "${#s}"
' > "$T/out2.txt"
[[ "$(cat "$T/out2.txt")" -le 120 ]] \
  && ok "14 snippet capped at ~120 chars ($(cat "$T/out2.txt"))" || bad "14 snippet too long: $(cat "$T/out2.txt")"

echo "── 15. dashboard renders mixed 4/5/6-field history rows (▶ = stored file exists)"
new_env s15
FX="$T/fixture.json"; make_fixture "$FX"
write_herdr_stub "$FX"
mkdir -p "$T/store"
STORE15="$T/store/p1-latest.mp3"
touch "$STORE15" # only this stored audio exists on disk
{
  printf '%s\tw4:p3\topencode\t12.5\n' "$(date -d '-95 minutes' +%Y-%m-%dT%H:%M:%S)"
  printf '%s\tw4:p3\topencode\t8.0\tRevision con snippet nuevo\t%s\n' "$(date -d '-70 minutes' +%Y-%m-%dT%H:%M:%S)" "$T/store/p3-gone.mp3"
  printf '%s\tw4:p3\topencode\t30.2\n' "$(date -d '-45 minutes' +%Y-%m-%dT%H:%M:%S)"
  printf '%s\tw9:p9\topencode\t45.0\tOtro snippet de chat cerrado\t-\n' "$(date -d '-25 minutes' +%Y-%m-%dT%H:%M:%S)"
  printf '%s\tw4:p1\topencode\t21.3\n' "$(date -d '-12 minutes' +%Y-%m-%dT%H:%M:%S)"
  printf '%s\tw4:p1\topencode\t60.0\tCierre con detalle final\t%s\n' "$(date -d '-2 minutes'  +%Y-%m-%dT%H:%M:%S)" "$STORE15"
} > "$HERDR_TTS_HISTORY_FILE"
capture 'q\n' "$T/out.txt"
assert_grep "15 history section renders with mixed rows" 'History per chat' "$T/out.txt"
assert_grep "15 group header counts p3 (3 audios)" '· 3 audios · last' "$T/out.txt"
na=$(grep -cE '^║    · [0-9]{2}:[0-9]{2} · [0-9]+[smh] ago$' "$T/out.txt")
[[ "$na" -eq 5 ]] && ok "15 unmarked audio rows = 5 (max 3/group): $na" || bad "15 unmarked rows = $na (want 5)"
nm=$(grep -cE '^║    · ▶ [0-9]{2}:[0-9]{2} · [0-9]+[smh] ago$' "$T/out.txt")
[[ "$nm" -eq 1 ]] && ok "15 exactly one ▶ marked row: $nm" || bad "15 marked rows = $nm (want 1)"
assert_grep "15 ▶ on the row whose stored file exists" '· ▶ 01:00 · 2m ago' "$T/out.txt"
assert_no_grep_f "15 no ▶ when the stored file is gone" '▶ 00:08' "$T/out.txt"
assert_no_grep_f "15 no ▶ for the literal '-' path column" '▶ 00:45' "$T/out.txt"
assert_grep "15 legacy row renders (00:12)" '· 00:12 · 1h ago' "$T/out.txt"
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
  # d/o open popups from a DETACHED session (setsid bash -c): only
  # exported functions are visible there, so export the stub too.
  export -f herdr
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
assert_grep "16a d → confirmation label"       '^d→.*opening the dashboard' "$T/out.txt"
assert_grep "16a o → confirmation label"       '^o→.*opening the voice palette' "$T/out.txt"
# The d/o opens run in a DETACHED session now (setsid) — wait for both
# invocations to land in the stub log before asserting.
for _ in $(seq 1 30); do
  grep -q 'entrypoint tts-palette' "$T/menu-herdr.log" 2>/dev/null \
    && grep -q 'entrypoint tts-dashboard' "$T/menu-herdr.log" 2>/dev/null && break
  sleep 0.1
done
assert_grep "16a d → opens tts-dashboard entrypoint" 'plugin pane open --plugin herdr.tts --entrypoint tts-dashboard' "$T/menu-herdr.log"
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
assert_grep "16b frame content rendered" 'Voice Menu' "$T/out.txt"
assert_grep "16b one-line confirmation" '^✓ .*(muted|re-enabled)' "$T/out.txt"
jq -e '.panes["w4:p4"].muted == true' "$HERDR_TTS_SNOOZE_FILE" >/dev/null \
  && ok "16b m muted the FOCUSED pane (stub pane current → w4:p4)" || bad "16b focused-pane default not honored"

# 16c. q / Esc: frame renders, no confirmation, silent exit (no dwell).
printf 'q' | timeout 10 "$SCRIPT" --voice-menu > "$T/out.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "16c q exits rc=0" || bad "16c q rc!=0"
assert_grep "16c q still renders the frame" 'Voice Menu' "$T/out.txt"
assert_no_grep "16c q path is silent (no confirmation)" '✓|Unrecognized key' "$T/out.txt"
printf '\033' | timeout 10 "$SCRIPT" --voice-menu > "$T/out.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "16c Esc exits rc=0" || bad "16c Esc rc!=0"
assert_no_grep "16c Esc path is silent" '✓|Unrecognized key' "$T/out.txt"

# 16d. Unknown key → brief Spanish notice, rc 0.
printf '@' | timeout 10 "$SCRIPT" --voice-menu > "$T/out.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "16d unknown key exits rc=0" || bad "16d rc!=0"
assert_grep "16d unknown key notice" 'Unrecognized key \(@\)' "$T/out.txt"

# 16e. Fail-open: EOF (no tty / closed stdin) → rc 0, never any action.
timeout 10 "$SCRIPT" --voice-menu < /dev/null > "$T/out.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "16e EOF exits rc=0 (fail-open)" || bad "16e EOF rc!=0"
assert_no_grep "16e EOF fires no action" '✓|Unrecognized key' "$T/out.txt"

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
assert_grep "16g tts-settings entrypoint wired" '"--voice-settings"' "$REPO/herdr-plugin.toml" -F
assert_grep "16g tts-settings popup id" 'id = "tts-settings"' "$REPO/herdr-plugin.toml" -F
assert_grep "16g open-menu action wired" '"--entrypoint", "tts-menu"' "$REPO/herdr-plugin.toml" -F
assert_grep "16g manifest declares voice-settings action" 'id = "voice-settings"' "$REPO/herdr-plugin.toml" -F
assert_grep "16g voice-settings runs --voice-settings" 'command = \["bin/herdr-tts", "--voice-settings"\]' "$REPO/herdr-plugin.toml"
assert_grep "16g voice-settings title in English" 'title = "Open TTS Settings (popup)"' "$REPO/herdr-plugin.toml" -F
assert_grep "16g version bumped to 0.16.0" 'version = "0.16.0"' "$REPO/herdr-plugin.toml" -F
assert_grep "16g README option 1 (menu, recommended)" '### Option 1 — Compact map \(recommended\)' "$REPO/README.md"
assert_grep "16g README option 2 (ctrl+alt family)" '### Option 2 — ctrl\+alt family' "$REPO/README.md"
assert_grep "16g README option 3 (direct map + conflicts)" '### Option 3 — Direct map \(power users\)' "$REPO/README.md"
assert_grep "16g README binds prefix+u to the menu" '"prefix+u"' "$REPO/README.md" -F
assert_grep "16g README documents the ctrl+alt+t caveat" 'ctrl\+alt\+t` launches a terminal' "$REPO/README.md"

# 16h. Two-view menu: `a` opens the settings INDEX inside the menu, `v`
#      enters the Voice category, `p` cycles the provider into a hermetic
#      config.env (HERDR_TTS_CONFIG_FILE override, same convention as
#      HERDR_TTS_KEYMAP_FILE), `q` backs out to the index and the second
#      `q` returns to the MAIN frame (re-render, NOT exit); EOF after the
#      last `q` (main view) exits rc 0.
new_env s16h
FX="$T/fixture.json"; make_fixture "$FX"
write_herdr_stub "$FX"
export HERDR_TTS_CONFIG_FILE="$T/config.env"
printf 'avpqq' | timeout 10 "$SCRIPT" --voice-menu > "$T/out.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "16h two-view menu exits rc=0 on the final q" || bad "16h rc!=0"
hv=$(esc_count "$T/out.txt" $'\033[H')
[[ "$hv" -eq 6 ]] && ok "16h 6 frame writes: main, index, voz, p re-render, index, main ($hv)" || bad "16h H-moves=$hv (want 6)"
[[ $(grep -cF '· Voice Menu' "$T/out.txt") -eq 2 ]] \
  && ok "16h final q returns to the MAIN frame (re-rendered, not exit)" || bad "16h main frame count $(grep -cF '· Voice Menu' "$T/out.txt")"
[[ $(grep -cF '· Voice & Audio Settings' "$T/out.txt") -eq 2 ]] \
  && ok "16h a opens the settings INDEX (+1 re-render after the category q)" || bad "16h index frame count $(grep -cF '· Voice & Audio Settings' "$T/out.txt")"
[[ $(grep -cF '· Settings · Voice' "$T/out.txt") -eq 2 ]] \
  && ok "16h v enters the Voice category (+1 re-render after p)" || bad "16h voice frame count $(grep -cF '· Settings · Voice' "$T/out.txt")"
grep -q 'TTS_PROVIDER="openai"' "$HERDR_TTS_CONFIG_FILE" \
  && ok "16h p cycled provider edge→openai into config.env" || bad "16h provider not persisted"
assert_no_grep "16h a/v/p/q path fires no action" '✓|Unrecognized key' "$T/out.txt"
unset HERDR_TTS_CONFIG_FILE # scenario 25 derives CONFIG_FILE from the XDG paths

# 16i. Settings `v` (inside reading/auto-read): toggles the auto_muted
#      marker both ways with inline notes. The marker lives in the
#      hermetic XDG conf dir (AUTO_MUTE_FILE derives from CONFIG_DIR),
#      NOT config.env — same source of truth as --toggle-auto /
#      --auto-on / --auto-off. One `v` per run (it is a toggle, not a
#      cycle). Entry key is `r` (English mnemonic; lowercase-only on the
#      index because uppercase R stays the daemon restart).
new_env s16i
FX="$T/fixture.json"; make_fixture "$FX"
write_herdr_stub "$FX"
export HERDR_TTS_CONFIG_FILE="$T/config.env"
printf 'rvq' | timeout 10 "$SCRIPT" --voice-settings > "$T/out.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "16i v toggle rc=0 (on→off)" || bad "16i rc!=0"
[[ -f "$T/conf/herdr-tts/auto_muted" ]] \
  && ok "16i v creates the auto_muted marker" || bad "16i auto_muted marker missing"
assert_grep "16i off note renders inline" 'Auto-read muted' "$T/out.txt"
printf 'rvq' | timeout 10 "$SCRIPT" --voice-settings > "$T/out.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "16i second v rc=0 (off→on)" || bad "16i second run rc!=0"
[[ ! -f "$T/conf/herdr-tts/auto_muted" ]] \
  && ok "16i second v clears the auto_muted marker" || bad "16i marker still present"
assert_grep "16i on note renders inline" 'Auto-read enabled' "$T/out.txt"
assert_grep "16i reading view lists the v row" ' v  Auto-read:' "$T/out.txt"
unset HERDR_TTS_CONFIG_FILE

# 16j. Category cycles persist into a hermetic config.env (fresh
#      defaults: scope=focused, lang=off, podcast=off, debounce=20):
#      r→reading (a scope), v→voice (i lang), n→notifications (f
#      podcast), r→reading again (b debounce), backing out to the index
#      between category views.
new_env s16j
FX="$T/fixture.json"; make_fixture "$FX"
write_herdr_stub "$FX"
export HERDR_TTS_CONFIG_FILE="$T/config.env"
printf 'raqviqnfqrbq' | timeout 10 "$SCRIPT" --voice-settings > "$T/out.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "16j category cycles rc=0" || bad "16j rc!=0"
grep -q 'TTS_AUTO_SCOPE="all"' "$HERDR_TTS_CONFIG_FILE" \
  && ok "16j a cycled scope focused→all" || bad "16j scope not persisted"
grep -q 'TTS_AUTO_LANG="on"' "$HERDR_TTS_CONFIG_FILE" \
  && ok "16j i cycled auto-lang off→on" || bad "16j lang not persisted"
grep -q 'PODCAST_ENABLED="on"' "$HERDR_TTS_CONFIG_FILE" \
  && ok "16j f cycled podcast off→on" || bad "16j podcast not persisted"
db=$(grep -oE 'TTS_DEBOUNCE_SECONDS="[0-9]+"' "$HERDR_TTS_CONFIG_FILE" | grep -oE '[0-9]+' | head -1)
[[ "$db" == "30" ]] && ok "16j b cycled debounce 20→30" || bad "16j debounce='$db' (want 30)"
assert_no_grep "16j cycles fire no warn" 'Unrecognized key|⚠️' "$T/out.txt"
unset HERDR_TTS_CONFIG_FILE

# 16k. Settings `t` (inside Notificaciones): free-text ntfy topic like
#      w; empty line clears the push (off), a written topic persists
#      verbatim, inline notes render.
new_env s16k
FX="$T/fixture.json"; make_fixture "$FX"
write_herdr_stub "$FX"
export HERDR_TTS_CONFIG_FILE="$T/config.env"
printf 'ntMiTopic\nq' | timeout 10 "$SCRIPT" --voice-settings > "$T/out.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "16k t write rc=0" || bad "16k write rc!=0"
grep -q 'NTFY_TOPIC="MiTopic"' "$HERDR_TTS_CONFIG_FILE" \
  && ok "16k t persists the topic verbatim" || bad "16k topic not persisted"
assert_grep "16k topic note renders inline" 'ntfy topic set' "$T/out.txt"
printf 'nt\nq' | timeout 10 "$SCRIPT" --voice-settings > "$T/out.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "16k t clear rc=0" || bad "16k clear rc!=0"
grep -q 'NTFY_TOPIC=""' "$HERDR_TTS_CONFIG_FILE" \
  && ok "16k empty line clears the topic" || bad "16k topic not cleared"
assert_grep "16k off note renders inline" 'ntfy mobile notifications disabled' "$T/out.txt"
unset HERDR_TTS_CONFIG_FILE

# 16l. Settings `g` (inside Voz): cycles the global voice and persists
#      TTS_VOICE. The venv stub answers the engine catalog query (RF-HT-13-4:
#      the cycle list IS the engine catalog, no static table anymore).
new_env s16l
FX="$T/fixture.json"; make_fixture "$FX"
write_herdr_stub "$FX"
cat > "$T/data/herdr-tts/venv/bin/python" <<'EOF'
#!/usr/bin/env bash
if [[ "${*}" == *"voice list --json" ]]; then
  printf '%s\n' '{"providers": ["edge", "openai", "elevenlabs", "piper", "kokoro"], "voices": {"edge": ["alvaro", "dalia", "elvira", "en", "jorge", "ximena"], "openai": ["alloy", "echo", "fable", "nova", "onyx", "shimmer"], "elevenlabs": ["adam", "rachel"], "piper": [], "kokoro": ["af_heart"]}}'
  exit 0
fi
exec python3 "$@"
EOF
chmod +x "$T/data/herdr-tts/venv/bin/python"
export HERDR_TTS_CONFIG_FILE="$T/config.env"
printf 'vgq' | timeout 10 "$SCRIPT" --voice-settings > "$T/out.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "16l g cycle rc=0" || bad "16l rc!=0"
grep -q 'TTS_VOICE="en"' "$HERDR_TTS_CONFIG_FILE" \
  && ok "16l g cycled voice elvira→en (engine catalog order)" || bad "16l voice not persisted"
unset HERDR_TTS_CONFIG_FILE

# 16m. Settings `n`/`u` (inside Voz): persist the voices.json flags
#      (prefix, auto_assign).
new_env s16m
FX="$T/fixture.json"; make_fixture "$FX"
write_herdr_stub "$FX"
export HERDR_TTS_CONFIG_FILE="$T/config.env"
printf 'vnuq' | timeout 10 "$SCRIPT" --voice-settings > "$T/out.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "16m n/u toggles rc=0" || bad "16m rc!=0"
jq -e '(.prefix == true) and (.auto_assign == true)' "$XDG_CONFIG_HOME/herdr-tts/voices.json" >/dev/null \
  && ok "16m n/u persist prefix + auto_assign" || bad "16m voices.json flags not persisted"
assert_grep "16m voz view lists the n row" ' n  Spoken prefix:' "$T/out.txt"
assert_grep "16m voz view lists the u row" ' u  Auto-assign:' "$T/out.txt"
unset HERDR_TTS_CONFIG_FILE

echo "── 17. keymap: init / check / emit (declarative, conflict-checked)"
new_env s17
export HERDR_TTS_KEYMAP_FILE="$T/keymap.json"
km="$HERDR_TTS_KEYMAP_FILE"
run_km() { timeout 10 "$SCRIPT" keymap "$@" > "$T/out.txt" 2>&1; }

# 17a. init: template created; refuses silent overwrite; --force replaces.
run_km init
[[ $? -eq 0 ]] && ok "17a init exits rc=0" || bad "17a init rc!=0"
[[ -f "$km" ]] && ok "17a keymap.json created" || bad "17a no keymap file"
jq -e '.style == "direct" and (.bindings | length == 20)' "$km" >/dev/null \
  && ok "17a template: style=direct, 20 stable command ids" || bad "17a template shape"
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
jq -e '(.bindings | length) == 20 and (.bindings[0].command == "play")' "$T/out.txt" >/dev/null \
  && ok "17c json: 20 bindings, file order preserved" || bad "17c json bindings array"
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
assert "prefix+u" not in by_key, "settings ships unassigned (lives inside the voice menu)"
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
assert "ctrl+alt+shift+u" not in keys, "settings has no suggested ctrl+alt chord anymore"
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
# settings is null in the default template since it moved inside the voice
# menu, so the collision below is already a clean two-way play/stop duplicate.
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
  && ok "18k 17 non-null ctrlalt bindings (settings has no suggested chord)" || bad "18k binding count"
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
grep -q 'Keymap applied' <<<"$out19" && ok "19b success notice shown" || bad "19b notice: $out19"
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
grep -q 'keymap apply failed' <<<"$out19" && ok "19d warning surfaced" || bad "19d no warning: $out19"
grep -q '>>> herdr-tts keymap' "$cfg19" && ok "19d target untouched by failed apply" || bad "19d target modified"

echo "── 21. palette ctrl-r replay bind (stubbed fzf records argv)"
new_env s21
FX="$T/fixture.json"
cat > "$FX" <<'EOF'
{"result":{"agents":[
 {"agent":"opencode","agent_status":"done","pane_id":"w4:p1","terminal_title":"OC | Chat Uno","terminal_title_stripped":"OC | Chat Uno"}
]}}
EOF
write_herdr_stub "$FX"
mkdir -p "$T/store"
STORE21="$T/store/2026-09-20-1-w4_p1.mp3"
touch "$STORE21"
{
  printf '%s\tw4:p1\topencode\t21.3\tVieja sin almacenar\n' "$(date -d '-12 minutes' +%Y-%m-%dT%H:%M:%S)"
  printf '%s\tw4:p1\topencode\t60.0\tCierre con audio persistido\t%s\n' "$(date -d '-2 minutes' +%Y-%m-%dT%H:%M:%S)" "$STORE21"
} > "$HERDR_TTS_HISTORY_FILE"
# 21a. entries carry the stored path as the 4th hidden field (fzf {4})
lib_run 'palette_build_entries' > "$T/out.txt"
nfields=$(awk -F'\t' '{print NF}' "$T/out.txt" | sort -u | tr '\n' ' ')
[[ "$nfields" == "4 " ]] && ok "21a every entry has 4 tab-delimited fields" || bad "21a field counts: $nfields"
grep -qF "$STORE21" "$T/out.txt" \
  && ok "21a newest entry carries the stored path" || bad "21a stored path missing from entries"
[[ "$(printf '%s\n' "$(tail -n 1 "$T/out.txt")" | cut -f4)" == "-" ]] \
  && ok "21a legacy row falls back to literal '-' path" || bad "21a legacy 4th field: $(tail -n 1 "$T/out.txt")"
# 21b. stub fzf: record argv, accept the first entry
cat > "$T/bin/fzf" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" > "$T/fzf.argv"
cat > /dev/null
head -n 1
exit 0
EOF
chmod +x "$T/bin/fzf"
timeout 20 "$SCRIPT" --voice-palette > "$T/out2.txt" 2>>"$T/err.log"
[[ -s "$T/fzf.argv" ]] && ok "21b fzf invoked by the palette" || bad "21b fzf argv file empty"
grep -qF "ctrl-r:execute-silent(" "$T/fzf.argv" \
  && ok "21b ctrl-r execute-silent bind present" || bad "21b no ctrl-r bind: $(cat "$T/fzf.argv")"
grep -qF "'$SCRIPT' --play-file {4}" "$T/fzf.argv" \
  && ok "21b bind replays {4} through --play-file" || bad "21b no --play-file {4} in: $(cat "$T/fzf.argv")"
grep -qF "+abort" "$T/fzf.argv" \
  && ok "21b bind aborts after replay (snapshot-per-open)" || bad "21b bind missing +abort"
grep -qF "ctrl-r: play audio" "$T/fzf.argv" \
  && ok "21c header advertises ctrl-r replay" || bad "21c header hint missing"

echo "── 22. play_audio_file: mutex stop + engine --play-file + missing-file guard"
new_env s22
ENGINE_CALLS="$T/engine.calls"; : > "$ENGINE_CALLS"
export ENGINE_CALLS
cat > "$T/bin/pystub" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "\$ENGINE_CALLS"
exit 0
EOF
chmod +x "$T/bin/pystub"
printf 'ID3x' > "$T/real.mp3" # non-empty: the -s guard must accept it
lib_run '
  VENV_PYTHON="$T/bin/pystub"; ENGINE_SCRIPT="$T/engine.py"
  PID_FILE="$T/pid"; LOCK_FILE="$T/lock"; IPC_SOCKET="$T/player.sock"
  play_audio_file "$T/real.mp3"
  rc=$?; echo "rc=$rc" > "$T/rc22"
  # reap the spawned engine so its argv line is flushed before asserts
  wait "$(cat "$T/pid" 2>/dev/null)" 2>/dev/null || true
'
grep -q 'rc=0' "$T/rc22" && ok "22a real file → rc 0" || bad "22a rc: $(cat "$T/rc22")"
grep -qF -- "--play-file $T/real.mp3" "$ENGINE_CALLS" \
  && ok "22a engine spawned with --play-file <file>" || bad "22a engine argv: $(cat "$ENGINE_CALLS")"
grep -qE '^[0-9]+$' "$T/pid" \
  && ok "22a mutex pid file written (player registered)" || bad "22a pid file missing/invalid"
[[ "$(wc -l < "$ENGINE_CALLS")" -eq 1 ]] \
  && ok "22a exactly one engine spawn" || bad "22a engine spawns: $(wc -l < "$ENGINE_CALLS")"
lib_run '
  VENV_PYTHON="$T/bin/pystub"; ENGINE_SCRIPT="$T/engine.py"
  PID_FILE="$T/pid"; LOCK_FILE="$T/lock"; IPC_SOCKET="$T/player.sock"
  rm -f "$PID_FILE" "$LOCK_FILE"
  rc=0
  play_audio_file "$T/missing.mp3" 2> "$T/miss.err" || rc=$?
  echo "rc=$rc" > "$T/rc22b"
'
grep -q 'rc=1' "$T/rc22b" && ok "22b missing path → rc 1" || bad "22b rc: $(cat "$T/rc22b")"
assert_grep "22b graceful missing-file message on stderr" 'Nothing to play' "$T/miss.err"
[[ "$(wc -l < "$ENGINE_CALLS")" -eq 1 ]] \
  && ok "22b no engine spawn for a missing path" || bad "22b engine spawned: $(cat "$ENGINE_CALLS")"

echo "── 23. audio_retention_days precedence + guards, audio_store_dir"
new_env s23
lib_run '
  unset HERDR_TTS_AUDIO_RETENTION_DAYS AGENT_TTS_AUDIO_RETENTION_DAYS TTS_AUDIO_RETENTION_DAYS AGENT_TTS_AUDIO_DIR
  echo "default:$(audio_retention_days)"
  HERDR_TTS_AUDIO_RETENTION_DAYS=3 AGENT_TTS_AUDIO_RETENTION_DAYS=9 TTS_AUDIO_RETENTION_DAYS=11
  echo "herdr:$(audio_retention_days)"
  unset HERDR_TTS_AUDIO_RETENTION_DAYS
  echo "agent:$(audio_retention_days)"
  unset AGENT_TTS_AUDIO_RETENTION_DAYS
  echo "tts:$(audio_retention_days)"
  unset TTS_AUDIO_RETENTION_DAYS
  HERDR_TTS_AUDIO_RETENTION_DAYS=nope
  echo "invalid:$(audio_retention_days)"
  HERDR_TTS_AUDIO_RETENTION_DAYS=0
  echo "zero:$(audio_retention_days)"
  HERDR_TTS_AUDIO_RETENTION_DAYS=-2
  echo "negative:$(audio_retention_days)"
  unset HERDR_TTS_AUDIO_RETENTION_DAYS
  echo "store_default:$(audio_store_dir)"
  AGENT_TTS_AUDIO_DIR=/tmp/custom-store
  echo "store_custom:$(audio_store_dir)"
' > "$T/out.txt"
assert_grep "23 no knobs → default 0 (opt-in)" '^default:0$' "$T/out.txt"
assert_grep "23 HERDR_TTS_ wins" '^herdr:3$' "$T/out.txt"
assert_grep "23 AGENT_TTS_ second" '^agent:9$' "$T/out.txt"
assert_grep "23 TTS_ third" '^tts:11$' "$T/out.txt"
assert_grep "23 non-integer → default 0" '^invalid:0$' "$T/out.txt"
assert_grep "23 zero disables retention" '^zero:0$' "$T/out.txt"
assert_grep "23 negative disables retention" '^negative:0$' "$T/out.txt"
assert_grep "23 store dir default" '^store_default:' "$T/out.txt"
assert_grep "23 store dir override" '^store_custom:/tmp/custom-store$' "$T/out.txt"

# 23h. HOT read: the watcher resolves retention from the LIVE config.env
# (tier 2) after exported env (tier 1); a startup file snapshot must never
# shadow a later file edit. Each case re-runs lib_run so the script is
# sourced fresh, mirroring a daemon start. new_env uses
# XDG_CONFIG_HOME="$T/conf" → CONFIG_FILE = "$T/conf/herdr-tts/config.env".
CFG23="$T/conf/herdr-tts/config.env"
# 23h-a. Value only in the file, written AFTER load → hot read picks it up.
mkdir -p "${CFG23%/*}"
lib_run '
  echo "HERDR_TTS_AUDIO_RETENTION_DAYS=\"5\"" >> "$CONFIG_FILE"
  echo "file-only:$(audio_retention_days)"
' > "$T/out23h.txt"
assert_grep "23h file-only value is read live" '^file-only:5$' "$T/out23h.txt"
# 23h-b. Stale startup snapshot must NOT shadow a later file edit: config
# says 9 at load, the menu (config_set) rewrites it to 5, function → 5.
mkdir -p "${CFG23%/*}" && printf 'HERDR_TTS_AUDIO_RETENTION_DAYS="9"\n' > "$CFG23"
lib_run '
  printf "HERDR_TTS_AUDIO_RETENTION_DAYS=\"5\"\n" > "$CONFIG_FILE"
  echo "hot:$(audio_retention_days)"
' > "$T/out23h.txt"
assert_grep "23h menu rewrite applies without restart (stale snapshot neutralized)" '^hot:5$' "$T/out23h.txt"
# 23h-c. Exported env (tier 1) beats the live file (tier 2).
printf 'HERDR_TTS_AUDIO_RETENTION_DAYS="5"\n' > "$CFG23"
lib_run '
  export HERDR_TTS_AUDIO_RETENTION_DAYS=9
  echo "env-wins:$(audio_retention_days)"
' > "$T/out23h.txt"
assert_grep "23h exported env beats live file" '^env-wins:9$' "$T/out23h.txt"
# 23h-d. Invalid value in the file → falls back to default 0.
printf 'HERDR_TTS_AUDIO_RETENTION_DAYS="soon"\n' > "$CFG23"
lib_run 'echo "invalid-file:$(audio_retention_days)"' > "$T/out23h.txt"
assert_grep "23h invalid file value → default 0" '^invalid-file:0$' "$T/out23h.txt"

echo "── 24. audio_store_prune: hourly retention prune from the daemon sweep"
new_env s24
PRUNE_CALLS="$T/prune.calls"; : > "$PRUNE_CALLS"
export PRUNE_CALLS
STAMP24="$T/state/herdr-tts/last-audio-prune" # STATE_DIR = $XDG_STATE_HOME/herdr-tts
write_prune_stub() { # $1 = exit code — stateful stub records engine argv
  cat > "$T/bin/pystub" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "\$PRUNE_CALLS"
exit $1
EOF
  chmod +x "$T/bin/pystub"
}
run_prune() { # $1 = optional snippet lines before the call (knob overrides)
  lib_run '
    VENV_PYTHON="$T/bin/pystub"
    '"${1:-}"'
    audio_store_prune
    echo "rc=$?" > "$T/rc24" # rc marker off stdout: out24 is the function output
  ' > "$T/out24.txt"
}
# 24-wiring. The daemon sweep must invoke the prune after its existing work
sed -n '/^run_daemon()/,/^}/p' "$SCRIPT" | grep -q 'audio_store_prune' \
  && ok "24w run_daemon sweep invokes audio_store_prune" || bad "24w no audio_store_prune inside run_daemon"
# 24a. first sweep: prune spawned, hourly stamp armed, stdout silent
write_prune_stub 0
run_prune 'HERDR_TTS_AUDIO_RETENTION_DAYS=7'
[[ "$(wc -l < "$PRUNE_CALLS")" -eq 1 ]] \
  && ok "24a first sweep spawns the retention prune" || bad "24a spawns: $(wc -l < "$PRUNE_CALLS")"
grep -qF 'prune_expired' "$PRUNE_CALLS" \
  && ok "24a spawn runs the engine retention sweep" || bad "24a argv: $(cat "$PRUNE_CALLS")"
[[ -f "$STAMP24" ]] && ok "24a hourly stamp written" || bad "24a stamp missing at $STAMP24"
grep -q '^rc=0$' "$T/rc24" && ok "24a rc 0 on success" || bad "24a rc: $(cat "$T/rc24")"
[[ ! -s "$T/out24.txt" ]] && ok "24a silent on success" || bad "24a unexpected stdout: $(cat "$T/out24.txt")"
# 24b. immediate second sweep: the hour gate blocks the spawn
run_prune 'HERDR_TTS_AUDIO_RETENTION_DAYS=7'
[[ "$(wc -l < "$PRUNE_CALLS")" -eq 1 ]] \
  && ok "24b hour gate blocks the immediate second spawn" || bad "24b spawns: $(wc -l < "$PRUNE_CALLS")"
# 24c. retention 0: no spawn at all and no timestamp file
rm -f "$STAMP24"
run_prune 'HERDR_TTS_AUDIO_RETENTION_DAYS=0'
[[ "$(wc -l < "$PRUNE_CALLS")" -eq 1 ]] \
  && ok "24c retention 0 → no spawn" || bad "24c spawns: $(wc -l < "$PRUNE_CALLS")"
[[ ! -e "$STAMP24" ]] \
  && ok "24c retention 0 → no stamp file" || bad "24c stamp exists"
# 24d. stale stamp (>1h via touch -d): the gate re-arms and it spawns again
touch -d '2 hours ago' "$STAMP24"
run_prune 'HERDR_TTS_AUDIO_RETENTION_DAYS=7'
[[ "$(wc -l < "$PRUNE_CALLS")" -eq 2 ]] \
  && ok "24d stale stamp (touch -d) triggers a fresh spawn" || bad "24d spawns: $(wc -l < "$PRUNE_CALLS")"
[[ -f "$STAMP24" ]] \
  && ok "24d stamp re-armed after the successful prune" || bad "24d stamp missing"
# 24e. failing engine (non-zero exit): daemon continues, failure logged
write_prune_stub 7
touch -d '2 hours ago' "$STAMP24"
run_prune 'HERDR_TTS_AUDIO_RETENTION_DAYS=7'
[[ "$(wc -l < "$PRUNE_CALLS")" -eq 3 ]] \
  && ok "24e failing engine attempted (still once per hour)" || bad "24e spawns: $(wc -l < "$PRUNE_CALLS")"
grep -q '^rc=0$' "$T/rc24" \
  && ok "24e failure never breaks the caller (rc 0, no crash)" || bad "24e rc: $(cat "$T/rc24")"
grep -qE '\[retention\].*prune failed' "$T/out24.txt" \
  && ok "24e failure logged (Spanish daemon-log line)" || bad "24e no failure log: $(cat "$T/out24.txt")"
[[ -f "$STAMP24" ]] \
  && ok "24e failure still arms the hour gate (no retry storm)" || bad "24e stamp missing after failure"

echo "── 25. settings popup: config_set writer, cycle tables, --voice-settings"
new_env s25
CONFIG_DIR_S25="$T/conf/herdr-tts"
CONFIG_FILE="$CONFIG_DIR_S25/config.env" # mirrors the script's XDG default
mkdir -p "$CONFIG_DIR_S25"

# 25a. config_set: managed-key writer with byte-preserving rewrite.
cat > "$CONFIG_FILE" <<'EOFX'
# mi config a mano
TTS_PROVIDER="openai"
# comentario suelto
TTS_PLAYBACK="wsl-ps"

OPENAI_API_KEY="sk-test"
EOFX
cp "$CONFIG_FILE" "$T/before25a.env"
lib_run '
  r1=0; config_set TTS_PROVIDER kokoro || r1=$?
  r2=0; config_set HERDR_TTS_AUDIO_RETENTION_DAYS 7 || r2=$?
  r3=0; config_set NOT_MANAGED x >/dev/null 2>&1 || r3=$?
  r4=0; config_set TTS_PROVIDER bad\"quote >/dev/null 2>&1 || r4=$?
  echo "r1=$r1 r2=$r2 r3=$r3 r4=$r4"
' > "$T/out.txt"
assert_grep "25a managed updates accepted, rejections rc 1" '^r1=0 r2=0 r3=1 r4=1$' "$T/out.txt"
cat > "$T/expected25a.env" <<'EOFX'
# mi config a mano
TTS_PROVIDER="kokoro"
# comentario suelto
TTS_PLAYBACK="wsl-ps"

OPENAI_API_KEY="sk-test"

# >>> herdr-tts settings (managed by the settings popup) >>>
HERDR_TTS_AUDIO_RETENTION_DAYS="7"
# <<< herdr-tts settings <<<
EOFX
cmp -s "$CONFIG_FILE" "$T/expected25a.env" \
  && ok "25a unknown lines/comments byte-identical, key replaced in place, block appended" \
  || bad "25a config.env drifted from the expected rewrite"
cmp -s "$CONFIG_FILE.bak" "$T/before25a.env" \
  && ok "25a .bak holds the previous version" || bad "25a .bak missing or wrong content"
bash -c 'source "$1" >/dev/null 2>&1 && printf "src:%s|%s\n" "$TTS_PROVIDER" "$HERDR_TTS_AUDIO_RETENTION_DAYS"' \
  _ "$CONFIG_FILE" > "$T/out.txt"
assert_grep "25a rewritten file stays bash-sourceable" '^src:kokoro|7$' "$T/out.txt"
# Fail-open: unwritable config directory → rc 1 + English stderr warning, no crash.
chmod 500 "$CONFIG_DIR_S25"
/bin/bash "$LIBRUN" "$SCRIPT" 'r=0; config_set TTS_PROVIDER edge 2>"$T/ro25.err" || r=$?; echo "ro_rc=$r"' > "$T/out.txt"
chmod 755 "$CONFIG_DIR_S25"
assert_grep "25a unwritable dir → rc 1 (fail-open)" '^ro_rc=1$' "$T/out.txt"
assert_grep "25a unwritable dir → English stderr warning" 'not writable' "$T/ro25.err"

# 25b. settings_cycle_value: full-cycle wrap + unknown-current fallback.
lib_run '
  k=edge; line="provider:$k"
  for i in 1 2 3 4 5 6; do k=$(settings_cycle_value provider "$k"); line+=">$k"; done
  echo "$line"
  echo "provider_unknown:$(settings_cycle_value provider weird)"
  k=local; line="target:$k"
  for i in 1 2 3 4 5 6; do k=$(settings_cycle_value target "$k"); line+=">$k"; done
  echo "$line"
  echo "target_unknown:$(settings_cycle_value target notamode)"
  k=0; line="retention:$k"
  for i in 1 2 3 4 5 6; do k=$(settings_cycle_value retention "$k"); line+=">$k"; done
  echo "$line"
  echo "retention_unknown:$(settings_cycle_value retention 9)"
  k=0; line="settle:$k"
  for i in 1 2 3 4 5 6; do k=$(settings_cycle_value settle "$k"); line+=">$k"; done
  echo "$line"
  echo "settle_unknown:$(settings_cycle_value settle 7)"
' > "$T/out.txt"
assert_grep "25b provider cycles with wrap" '^provider:edge>openai>elevenlabs>piper>kokoro>edge>openai$' "$T/out.txt"
assert_grep "25b unknown provider current → first element" '^provider_unknown:edge$' "$T/out.txt"
assert_grep "25b target cycles with wrap" '^target:local>winhost>wsl-ps>windows>auto>local>winhost$' "$T/out.txt"
assert_grep "25b unknown target current → first element" '^target_unknown:local$' "$T/out.txt"
assert_grep "25b retention cycles with wrap" '^retention:0>1>3>7>14>0>1$' "$T/out.txt"
assert_grep "25b unknown retention current → first element" '^retention_unknown:0$' "$T/out.txt"
assert_grep "25b settle cycles with wrap" '^settle:0>2>5>10>15>30>0$' "$T/out.txt"
assert_grep "25b unknown settle current → first element" '^settle_unknown:0$' "$T/out.txt"

# 25c. run_voice_settings with piped keys: index frame renders, values
#      cycle inside their categories (v→voice p, a→audio r), config.env
#      persists, every change re-renders (one H-move each).
printf 'vpqarqq' | timeout 10 "$SCRIPT" --voice-settings > "$T/out.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "25c settings popup exited rc=0" || bad "25c rc!=0"
assert_grep "25c index frame renders" 'Voice & Audio Settings' "$T/out.txt"
assert_grep "25c voice category frame renders" 'Settings · Voice' "$T/out.txt"
assert_grep "25c audio category frame renders" 'Settings · Audio' "$T/out.txt"
hv=$(esc_count "$T/out.txt" $'\033[H')
[[ "$hv" -eq 7 ]] && ok "25c index, voz, p re-render, index, audio, r re-render, index ($hv H-moves)" || bad "25c H-moves=$hv (want 7)"
grep -q 'TTS_PROVIDER="edge"' "$CONFIG_FILE" \
  && ok "25c p cycled provider kokoro→edge into config.env" || bad "25c provider not persisted"
grep -q 'HERDR_TTS_AUDIO_RETENTION_DAYS="14"' "$CONFIG_FILE" \
  && ok "25c r cycled retention 7→14 in place" || bad "25c retention not persisted"
grep -q 'TTS_PLAYBACK="wsl-ps"' "$CONFIG_FILE" \
  && ok "25c untouched knob survives popup writes" || bad "25c playback knob mutated"
# q-only run: one render, no re-render, file untouched.
printf 'q' | timeout 10 "$SCRIPT" --voice-settings > "$T/out2.txt" 2>>"$T/err.log"
hv=$(esc_count "$T/out2.txt" $'\033[H')
[[ "$hv" -eq 1 ]] && ok "25c q exits after a single render ($hv H-move)" || bad "25c q H-moves=$hv (want 1)"
# Unknown key → warning rendered inline on the re-render, then q exits.
printf '@q' | timeout 10 "$SCRIPT" --voice-settings > "$T/out3.txt" 2>>"$T/err.log"
assert_grep "25c unknown key shows the warning inline" 'Unrecognized key' "$T/out3.txt"

# 25d. --voice-settings dispatch smoke: the flag runs the popup (a daemon
#      start would hang and hit the timeout instead of exiting rc 0).
timeout 10 "$SCRIPT" --voice-settings </dev/null > "$T/out.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "25d --voice-settings dispatch exits rc=0 (EOF fail-open)" || bad "25d rc!=0"
assert_grep "25d dispatch renders the settings frame" 'Voice & Audio Settings' "$T/out.txt"
hv=$(esc_count "$T/out.txt" $'\033[H')
[[ "$hv" -eq 1 ]] && ok "25d EOF path renders exactly once ($hv H-move)" || bad "25d H-moves=$hv (want 1)"
sed -n '/--voice-settings)/,/;;/p' "$SCRIPT" | grep -q 'run_voice_settings' \
  && ok "25d argparse case wires --voice-settings → run_voice_settings" || bad "25d no dispatch wiring"

# 25e. config.env without the file → created and bash-sourceable.
rm -f "$CONFIG_FILE" "$CONFIG_FILE.bak"
lib_run '
  r=0; config_set TTS_PLAYBACK auto || r=$?
  echo "create_rc=$r"
  if bash -n "$CONFIG_FILE" 2>/dev/null; then echo "syntax-ok"; else echo "syntax-bad"; fi
' > "$T/out.txt"
assert_grep "25e config_set creates a missing config.env (rc 0)" '^create_rc=0$' "$T/out.txt"
assert_grep "25e created file passes bash -n" '^syntax-ok$' "$T/out.txt"
bash -c 'source "$1" >/dev/null 2>&1 && printf "src:%s\n" "$TTS_PLAYBACK"' _ "$CONFIG_FILE" > "$T/out.txt"
assert_grep "25e created file sources with the written value" '^src:auto$' "$T/out.txt"

echo "── 26. daemon stop/restart: pidfile kill, legacy fallback, settings R, --restart-daemon"
new_env s26
export HERDR_TTS_DAEMON_PID_FILE="$T/daemon.pid"

# 26a. pidfile path: a live fake daemon (cmdline guarded like the real one)
#      is killed through the pidfile; the out-var reports "pidfile".
bash -c 'exec -a "bin/herdr-tts _daemon" sleep 30' & FAKE26=$!
printf '%s\n' "$FAKE26" > "$HERDR_TTS_DAEMON_PID_FILE"
lib_run 'daemon_stop_running stop_how; rc=$?; echo "how=$stop_how rc=$rc"' > "$T/out.txt"
wait "$FAKE26" 2>/dev/null || true
! kill -0 "$FAKE26" 2>/dev/null && ok "26a pidfile kill removes the fake daemon" || { bad "26a fake daemon survived"; kill -9 "$FAKE26" 2>/dev/null || true; }
assert_grep "26a out-var says pidfile, rc 0" '^how=pidfile rc=0$' "$T/out.txt"

# 26b. legacy fallback: same fake process WITHOUT a pidfile → the cmdline
#      sweep kills it and the out-var reports "fallback". The fake mirrors
#      the real daemon's argv shape (adjacent <...herdr-tts> <_daemon>),
#      which is exactly what the strict adjacency sweep matches.
rm -f "$HERDR_TTS_DAEMON_PID_FILE"
bash -c 'while :; do sleep 0.5; done' herdr-tts _daemon & FAKE26B=$!
lib_run 'daemon_stop_running stop_how; echo "how=$stop_how"' > "$T/out.txt"
wait "$FAKE26B" 2>/dev/null || true
! kill -0 "$FAKE26B" 2>/dev/null && ok "26b legacy fallback kills a daemon without pidfile" || { bad "26b fake daemon survived"; kill -9 "$FAKE26B" 2>/dev/null || true; }
assert_grep "26b out-var says fallback" '^how=fallback$' "$T/out.txt"

# 26c. nothing running → "none", rc 0 (fail-open). The sweep matches only
#      the strict adjacent-argv daemon signature, so ambient processes that
#      merely mention the string in a larger argument are never touched.
lib_run 'daemon_stop_running stop_how; rc=$?; echo "how=$stop_how rc=$rc"' > "$T/out.txt"
assert_grep "26c nothing running → none, rc 0" '^how=none rc=0$' "$T/out.txt"

# 26c-2. smoke-safety: a REAL daemon on this machine (same argv shape, no
#        HERDR_TTS_SMOKE marker in its environ) must survive the suite's
#        sweep — the battery can never take down live voice feedback.
env -u HERDR_TTS_SMOKE bash -c 'while :; do sleep 0.5; done' herdr-tts _daemon & REAL26=$!
lib_run 'daemon_stop_running stop_how; echo "how=$stop_how"' > "$T/out.txt"
kill -0 "$REAL26" 2>/dev/null && ok "26c-2 unmarked real-shape daemon spared by the suite sweep" || bad "26c-2 sweep killed an unmarked daemon"
kill "$REAL26" 2>/dev/null || true; wait "$REAL26" 2>/dev/null || true
assert_grep "26c-2 sweep finds nothing to kill" '^how=none$' "$T/out.txt"

# 26d. daemon_restart SUCCESS path (documented choice over the timeout
#      path): SCRIPT is pointed at a recorder stub that logs its argv and
#      WRITES the pidfile itself, so the pidfile wait succeeds and the
#      Spanish confirmation fires with the new pid.
cat > "$T/recorder.sh" <<EOF
#!/usr/bin/env bash
printf 'args=%s\n' "\$*" >> "$T/restart.log"
printf '%s\n' "\$\$" > "\${HERDR_TTS_DAEMON_PID_FILE:?}"
EOF
chmod +x "$T/recorder.sh"
rm -f "$T/restart.log" "$HERDR_TTS_DAEMON_PID_FILE"
lib_run 'SCRIPT="$T/recorder.sh"; daemon_restart 2>"$T/restart.err"; rc=$?; echo "rc=$rc"' > "$T/out.txt"
assert_grep "26d daemon_restart exits rc 0" '^rc=0$' "$T/out.txt"
assert_grep "26d re-invocation reached the stub with _daemon-supervised" '^args=_daemon-supervised$' "$T/restart.log"
assert_grep "26d restart confirmation with the new pid" '^✓ Daemon restarted \(pid [0-9]+\)$' "$T/restart.err"

# 26e. Settings view key R: piped `aRq` through --voice-menu with the
#      hermetic SCRIPT override — R restarts via the stub and the
#      confirmation note renders inline on exactly one frame.
rm -f "$T/restart.log" "$HERDR_TTS_DAEMON_PID_FILE"
( export HERDR_TTS_SCRIPT="$T/recorder.sh"
  printf 'aRq' | timeout 10 "$SCRIPT" --voice-menu > "$T/out.txt" 2>>"$T/err.log" )
[[ $? -eq 0 ]] && ok "26e aRq menu path exits rc=0" || bad "26e rc!=0"
assert_grep "26e settings frame rendered" 'Voice & Audio Settings' "$T/out.txt"
[[ $(grep -cF '✓ Daemon restarted' "$T/out.txt") -eq 1 ]] \
  && ok "26e R renders the confirmation note on exactly the re-render" \
  || bad "26e note count $(grep -cF '✓ Daemon restarted' "$T/out.txt") (want 1)"
assert_grep "26e R hit the restart path (stub invoked)" '^args=_daemon-supervised$' "$T/restart.log"
hv=$(esc_count "$T/out.txt" $'\033[H')
[[ "$hv" -eq 4 ]] && ok "26e 4 frame writes: main, settings, R re-render, main ($hv)" || bad "26e H-moves=$hv (want 4)"

# 26f. --restart-daemon dispatch smoke (like 25d): the flag exits rc 0
#      with the confirmation on stderr and the case wiring calls
#      daemon_restart.
rm -f "$T/restart.log" "$HERDR_TTS_DAEMON_PID_FILE"
( export HERDR_TTS_SCRIPT="$T/recorder.sh"
  timeout 10 "$SCRIPT" --restart-daemon </dev/null > "$T/out.txt" 2>"$T/restart2.err" )
[[ $? -eq 0 ]] && ok "26f --restart-daemon exits rc=0" || bad "26f rc!=0"
assert_grep "26f confirmation on stderr" 'Daemon restarted' "$T/restart2.err"
assert_grep "26f dispatch spawned the supervised entrypoint" '^args=_daemon-supervised$' "$T/restart.log"
sed -n '/--restart-daemon)/,/;;/p' "$SCRIPT" | grep -q 'daemon_restart' \
  && ok "26f argparse case wires --restart-daemon → daemon_restart" || bad "26f no dispatch wiring"
unset HERDR_TTS_DAEMON_PID_FILE

echo "── 27. watcher settle window: intermediate done dropped, held done fires"
new_env s27
CONFIG_FILE="$T/conf/herdr-tts/config.env" # mirrors the script's XDG default
# Fake herdr CLI: agent wait succeeds once then blocks (the agent is back
# at work), agent get answers from the scenario files, pane/agent read
# return empty so a held done never reaches real synthesis in the test.
export SETTLE_SCENARIO_DIR="$T"
cat > "$T/bin/herdr" <<'STUB'
#!/usr/bin/env bash
d="$SETTLE_SCENARIO_DIR"
case "$1 $2" in
  "pane read"|"agent read") exit 0 ;;
  "agent wait")
    n=$(cat "$d/wait_n" 2>/dev/null || echo 0); printf '%s' $((n+1)) > "$d/wait_n"
    if (( n == 0 )); then exit 0; fi
    sleep 60 ;;
  "agent get")
    if [[ -f "$d/flip_working" ]]; then
      printf '%s' '{"result":{"agent":{"agent_status":"working"}}}'
    else
      printf '%s' '{"result":{"agent":{"agent_status":"done"}}}'
    fi ;;
  *) printf '%s' '{"result":{}}' ;;
esac
STUB
chmod +x "$T/bin/herdr"

# 27a. invalid env value sanitized to the default + config_set acceptance.
TTS_SETTLE_SECONDS=banana lib_run 'printf %s "$TTS_SETTLE_SECONDS"' > "$T/out.txt"
assert_grep "27a invalid env value sanitized to default 5" '^5$' "$T/out.txt"
lib_run 'r=0; config_set TTS_SETTLE_SECONDS 10 || r=$?; echo "rc=$r"' > "$T/out.txt"
assert_grep "27a config_set accepts the settle key" '^rc=0$' "$T/out.txt"
grep -q 'TTS_SETTLE_SECONDS="10"' "$CONFIG_FILE" \
  && ok "27a settle value persisted to config.env" || bad "27a settle not persisted"

# 27b. intermediate step: done reverts to working inside the window →
#      logged discard, the pipeline (synthesis + ntfy) never fires.
: > "$T/wait_n"
( timeout 8 /bin/bash "$LIBRUN" "$SCRIPT" 'TTS_SETTLE_SECONDS=2; watch_agent_pane w6:p1 opencode' > "$T/watchA.log" 2>>"$T/err.log" ) &
flipper=$!
sleep 1
touch "$T/flip_working" # the agent goes back to working DURING the window
wait $flipper
assert_grep "27b intermediate done discarded" 'intermediate done discarded' "$T/watchA.log"
grep -q 'working → done' "$T/watchA.log" \
  && bad "27b pipeline fired for an intermediate step" \
  || ok "27b pipeline never fired for the intermediate step"

# 27c. real end of turn: done still holds after the window → the watcher
#      proceeds to the pipeline branch (empty pane text skips synthesis).
rm -f "$T/flip_working"; : > "$T/wait_n"
timeout 8 /bin/bash "$LIBRUN" "$SCRIPT" 'TTS_SETTLE_SECONDS=1; watch_agent_pane w6:p2 opencode' > "$T/watchB.log" 2>>"$T/err.log"
assert_grep "27c held done reaches the pipeline branch" 'working → done' "$T/watchB.log"

# 27d. menu_open_after_exit: Herdr allows one popup at a time (ui_busy
#      while the menu itself is alive) → the deferred open retries until
#      the slot frees and lands on the right entrypoint.
: > "$T/open_n"; rm -f "$T/opened.log"
cat > "$T/bin/herdr" <<'STUB2'
#!/usr/bin/env bash
d="$SETTLE_SCENARIO_DIR"
case "$1 $2 $3" in
  "plugin pane open")
    n=$(cat "$d/open_n" 2>/dev/null || echo 0); printf '%s' $((n+1)) > "$d/open_n"
    if (( n < 2 )); then printf '%s' '{"error":{"code":"ui_busy"}}'; exit 1; fi
    printf 'opened:%s\n' "$7" >> "$d/opened.log"; exit 0 ;;
  *) exit 0 ;;
esac
STUB2
chmod +x "$T/bin/herdr"
HERDR_TTS_MENU_OPEN_RETRIES=20 lib_run 'menu_open_after_exit tts-dashboard; sleep 1.5; cat "$SETTLE_SCENARIO_DIR/opened.log" 2>/dev/null' > "$T/out.txt"
assert_grep "27d deferred open retries through ui_busy then opens" '^opened:tts-dashboard$' "$T/out.txt"

# 27e. exhausted retries land in the open log instead of being swallowed.
rm -f "$T/opened.log" "$T/menu.log"
cat > "$T/bin/herdr" <<'STUB3'
#!/usr/bin/env bash
printf '%s' '{"error":{"code":"ui_busy"}}'
exit 1
STUB3
chmod +x "$T/bin/herdr"
HERDR_TTS_MENU_OPEN_RETRIES=3 HERDR_TTS_MENU_OPEN_LOG="$T/menu.log" \
  lib_run 'menu_open_after_exit tts-palette; sleep 1' > "$T/out.txt"
assert_grep "27e exhausted retries are logged" 'open tts-palette failed after 3 attempts' "$T/menu.log"

echo "── 28. settings popup: web redirect URL input/clear + click directo on/off"
new_env s28
CONFIG_DIR_S28="$T/conf/herdr-tts"
CONFIG_FILE="$CONFIG_DIR_S28/config.env" # mirrors the script's XDG default
mkdir -p "$CONFIG_DIR_S28"

# 28a. empty URL: notif view renders "Desactivada", w with an empty line
#      keeps the redirect off (empty WEB_URL/COLLIE_URL written by the
#      writer); a pass through the Audio view shows the untouched c row.
printf 'nw\nqaq' | timeout 10 "$SCRIPT" --voice-settings > "$T/out.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "28a popup exits rc=0 on empty-URL clear" || bad "28a rc!=0"
assert_grep "28a web row renders Off with no URL" 'w  Web redirect: +Off' "$T/out.txt"
assert_grep "28a click row renders off" 'c  Click-through: +off ' "$T/out.txt"
grep -qF 'WEB_URL=""' "$CONFIG_FILE" \
  && ok "28a empty WEB_URL persisted to config.env" || bad "28a WEB_URL not persisted"
assert_grep "28a index keeps the restart hint line" 'R +restarts the daemon.*other settings' "$T/out.txt"

# 28b. w + collie URL with the {pane_id} placeholder: stored verbatim,
#      COLLIE_URL rides along, and a collie URL seeds WEB_LABEL="Collie"
#      (it was empty); the frame shows the configured URL.
URL28="https://collie.example.com/ui/pane/{pane_id}"
printf 'nw%s\nq' "$URL28" | timeout 10 "$SCRIPT" --voice-settings > "$T/out2.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "28b URL popup exits rc=0" || bad "28b rc!=0"
grep -qF "WEB_URL=\"$URL28\"" "$CONFIG_FILE" \
  && ok "28b WEB_URL persisted verbatim ({pane_id} intact)" || bad "28b WEB_URL not persisted"
grep -qF "COLLIE_URL=\"$URL28\"" "$CONFIG_FILE" \
  && ok "28b COLLIE_URL written alongside (CLI parity)" || bad "28b COLLIE_URL missing"
grep -qF 'WEB_LABEL="Collie"' "$CONFIG_FILE" \
  && ok "28b collie URL seeds WEB_LABEL=Collie when empty" || bad "28b WEB_LABEL not seeded"
assert_grep "28b frame shows the configured URL" "Web redirect: +https://collie.example.com/ui/pane/[{]pane_id" "$T/out2.txt"

# 28c. c cycles off → on (inside Audio): persisted + CLI-parity note.
printf 'acq' | timeout 10 "$SCRIPT" --voice-settings > "$T/out3.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "28c on-cycle popup exits rc=0" || bad "28c rc!=0"
grep -qF 'CLICK_REDIRECT="on"' "$CONFIG_FILE" \
  && ok "28c CLICK_REDIRECT=on persisted" || bad "28c on not persisted"
assert_grep "28c on note mirrors the CLI wording" 'Click-through enabled \(opens the browser' "$T/out3.txt"

# 28d. c again cycles on → off, wording included.
printf 'acq' | timeout 10 "$SCRIPT" --voice-settings > "$T/out4.txt" 2>>"$T/err.log"
grep -qF 'CLICK_REDIRECT="off"' "$CONFIG_FILE" \
  && ok "28d CLICK_REDIRECT=off persisted after the wrap" || bad "28d off not persisted"
assert_grep "28d off note mirrors the CLI wording" 'Click-through disabled' "$T/out4.txt"

echo "── 29. daemon supervisor: stop-flag semantics, relaunch decision, lifecycle logging"
new_env s29
export HERDR_TTS_DAEMON_PID_FILE="$T/daemon.pid"
export HERDR_TTS_SUPERVISOR_STOP_FILE="$T/supervisor.stop"
export HERDR_TTS_DAEMON_LOG="$T/daemon.log"
SUPERVISOR_STOP_FLAG="$T/supervisor.stop" # mirrors the env override

# 29a. decision fn, flag present → stop + one-shot consumption. The
#      harness shell runs under set -e, so the rc-1 outcome is captured
#      through an if (a bare call would abort the eval).
touch "$SUPERVISOR_STOP_FLAG"
lib_run 'if ! daemon_supervisor_should_relaunch 3; then rel=1; else rel=0; fi; echo "rel=$rel"; [[ -e "$SUPERVISOR_STOP_FLAG" ]] && echo flag-there || echo flag-consumed' > "$T/out.txt"
assert_grep "29a flag present → no relaunch (rc 1)" '^rel=1$' "$T/out.txt"
assert_grep "29a flag consumed by the decision (one-shot)" '^flag-consumed$' "$T/out.txt"

# 29b. decision fn, no flag → relaunch.
lib_run 'daemon_supervisor_should_relaunch 0; echo "rel=$?"' > "$T/out.txt"
assert_grep "29b no flag → relaunch (rc 0)" '^rel=0$' "$T/out.txt"

# 29c. end-to-end stop-flag semantics: the flag is armed AFTER supervisor
#      start (like a real daemon_stop_running kill) and the child dies.
#      The supervisor must consume it, exit rc 0 and NEVER respawn.
cat > "$T/stopped.sh" <<EOF
#!/usr/bin/env bash
echo "child \$\$ args=\$*" >> "$T/children.log"
: > "$SUPERVISOR_STOP_FLAG" # what daemon_stop_running arms before the kill
exit 3
EOF
chmod +x "$T/stopped.sh"
rm -f "$T/children.log" "$HERDR_TTS_DAEMON_LOG" "$SUPERVISOR_STOP_FLAG"
HERDR_TTS_SCRIPT="$T/stopped.sh" timeout 10 "$SCRIPT" _daemon-supervised </dev/null >"$T/out.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "29c flag armed → supervisor exits rc 0 without relaunching" || bad "29c rc!=0"
[[ $(wc -l < "$T/children.log") -eq 1 ]] \
  && ok "29c exactly one child spawn (death did NOT relaunch)" || bad "29c spawns=$(wc -l < "$T/children.log") (want 1)"
assert_grep "29c deliberate stop logged to daemon.log" 'deliberate daemon stop \(rc=3\)' "$HERDR_TTS_DAEMON_LOG"
[[ ! -e "$SUPERVISOR_STOP_FLAG" ]] && ok "29c flag consumed (removed)" || bad "29c flag left behind"

# 29d. relaunch path: no flag, child dies rc 3, short backoff override →
#      the supervisor relaunches (≥2 spawns) and logs every relaunch. A
#      stale flag preset BEFORE start must be cleared, or this would stop.
cat > "$T/dying.sh" <<EOF
#!/usr/bin/env bash
echo "child \$\$ args=\$*" >> "$T/children.log"
exit 3
EOF
chmod +x "$T/dying.sh"
rm -f "$T/children.log" "$HERDR_TTS_DAEMON_LOG"
touch "$SUPERVISOR_STOP_FLAG" # stale flag from a dead supervisor
HERDR_TTS_SCRIPT="$T/dying.sh" HERDR_TTS_SUPERVISOR_BACKOFF=0.2 \
  timeout 2 "$SCRIPT" _daemon-supervised </dev/null >"$T/out.txt" 2>>"$T/err.log"
rc29d=$?
[[ "$rc29d" -eq 124 ]] && ok "29d supervisor kept the loop until the timeout TERM (rc 124)" || bad "29d rc=$rc29d (want 124)"
spawns=$(wc -l < "$T/children.log")
[[ "$spawns" -ge 2 ]] && ok "29d child relaunched after unplanned death ($spawns spawns)" || bad "29d spawns=$spawns (want ≥2)"
assert_grep "29d relaunch logged to daemon.log" 'daemon died \(rc=3\) — relaunching in 0.2s' "$HERDR_TTS_DAEMON_LOG"
[[ ! -e "$SUPERVISOR_STOP_FLAG" ]] && ok "29d stale flag cleared on supervisor start" || bad "29d stale flag swallowed the relaunch"

# 29e. TERM forwarding + clean exit, no orphan child: a blocking child is
#      running when the supervisor gets TERM → child receives TERM and
#      dies, supervisor exits rc 0, nothing left behind.
cat > "$T/blocking.sh" <<EOF
#!/usr/bin/env bash
echo "child \$\$ args=\$*" >> "$T/children.log"
trap 'exit 0' TERM
while :; do sleep 0.2; done
EOF
chmod +x "$T/blocking.sh"
rm -f "$T/children.log" "$HERDR_TTS_DAEMON_LOG"
HERDR_TTS_SCRIPT="$T/blocking.sh" "$SCRIPT" _daemon-supervised </dev/null >"$T/out.txt" 2>>"$T/err.log" &
SUP29=$!
sleep 1
child29=$(cat "$T/children.log" | head -1 | sed 's/child \([0-9]*\).*/\1/')
kill -TERM "$SUP29" 2>/dev/null
wait "$SUP29" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "29e supervisor exits rc 0 on TERM" || bad "29e supervisor rc!=0"
sleep 0.5
! kill -0 "$child29" 2>/dev/null && ok "29e child got TERM and died (no orphan)" || { bad "29e child orphaned"; kill -9 "$child29" 2>/dev/null || true; }
assert_grep "29e forwarded-stop logged" 'TERM received — stopping with the daemon' "$HERDR_TTS_DAEMON_LOG"

# 29f. wiring: dispatch case, manifest startup, run_daemon observability,
#      and the daemon_log helper line format.
sed -n '/_daemon-supervised)/,/;;/p' "$SCRIPT" | grep -q 'run_daemon_supervised' \
  && ok "29f dispatch wires _daemon-supervised → run_daemon_supervised" || bad "29f no dispatch wiring"
grep -qF '"_daemon-supervised"' "$REPO/herdr-plugin.toml" \
  && ok "29f plugin manifest startup runs the supervised entrypoint" || bad "29f manifest still launches the bare daemon"
sed -n '/^run_daemon() {/,/^}/p' "$SCRIPT" | grep -q 'daemon_log' \
  && ok "29f run_daemon writes the startup line to daemon.log" || bad "29f run_daemon silent"
sed -n '/^run_daemon() {/,/^}/p' "$SCRIPT" | grep -q 'DAEMON_EXIT_REASON' \
  && ok "29f cleanup logs the exit reason (signal vs clean)" || bad "29f cleanup silent"
lib_run 'daemon_log "prueba de escritura"' > /dev/null
assert_grep "29f daemon_log appends the [HH:MM:SS] line" '^\[[0-9]{2}:[0-9]{2}:[0-9]{2}\] prueba de escritura$' "$HERDR_TTS_DAEMON_LOG"
unset HERDR_TTS_DAEMON_PID_FILE HERDR_TTS_SUPERVISOR_STOP_FILE HERDR_TTS_DAEMON_LOG

echo "── 30. config_set upsert: duplicate keys collapse to exactly one line"
new_env s30
CONFIG_DIR_S30="$T/conf/herdr-tts"
CONFIG_FILE="$CONFIG_DIR_S30/config.env" # mirrors the script's XDG default
mkdir -p "$CONFIG_DIR_S30"

# 30a. the real-world corruption: the key repeated INSIDE the managed
#      block (appends grew it) → one config_set collapses to one line.
cat > "$CONFIG_FILE" <<'EOFX'
TTS_PROVIDER="edge"

# >>> herdr-tts settings (managed by the settings popup) >>>
HERDR_TTS_AUDIO_RETENTION_DAYS="7"
HERDR_TTS_AUDIO_RETENTION_DAYS="7"
HERDR_TTS_AUDIO_RETENTION_DAYS="7"
# <<< herdr-tts settings <<<
EOFX
lib_run 'r=0; config_set HERDR_TTS_AUDIO_RETENTION_DAYS 14 || r=$?; echo "rc=$r"' > "$T/out.txt"
assert_grep "30a config_set rc 0" '^rc=0$' "$T/out.txt"
n=$(grep -c '^HERDR_TTS_AUDIO_RETENTION_DAYS=' "$CONFIG_FILE")
[[ "$n" -eq 1 ]] && ok "30a exactly one retention line remains (had 3)" || bad "30a $n retention lines (want 1)"
grep -qF 'HERDR_TTS_AUDIO_RETENTION_DAYS="14"' "$CONFIG_FILE" \
  && ok "30a value upserted" || bad "30a value not updated"
assert_no_grep_f "30a old duplicate values gone" 'HERDR_TTS_AUDIO_RETENTION_DAYS="7"' "$CONFIG_FILE"
cat > "$T/expected30a.env" <<'EOFX'
TTS_PROVIDER="edge"

# >>> herdr-tts settings (managed by the settings popup) >>>
HERDR_TTS_AUDIO_RETENTION_DAYS="14"
# <<< herdr-tts settings <<<
EOFX
cmp -s "$CONFIG_FILE" "$T/expected30a.env" \
  && ok "30a unknown lines and block markers byte-identical" || bad "30a file drifted"

# 30b. duplicate OUTSIDE + inside the block: first occurrence (top) is
#      replaced in place, the later one is dropped.
cat > "$CONFIG_FILE" <<'EOFX'
HERDR_TTS_AUDIO_RETENTION_DAYS="3"
# >>> herdr-tts settings (managed by the settings popup) >>>
HERDR_TTS_AUDIO_RETENTION_DAYS="7"
# <<< herdr-tts settings <<<
EOFX
lib_run 'r=0; config_set HERDR_TTS_AUDIO_RETENTION_DAYS 14 || r=$?; echo "rc=$r"' > "$T/out.txt"
assert_grep "30b config_set rc 0" '^rc=0$' "$T/out.txt"
n=$(grep -c '^HERDR_TTS_AUDIO_RETENTION_DAYS=' "$CONFIG_FILE")
[[ "$n" -eq 1 ]] && ok "30b exactly one line after cross-block dedupe" || bad "30b $n lines (want 1)"
[[ $(head -1 "$CONFIG_FILE") == 'HERDR_TTS_AUDIO_RETENTION_DAYS="14"' ]] \
  && ok "30b first occurrence replaced in place (position kept)" || bad "30b top line drifted"

# 30c. regression of the append-instead-of-replace growth: the same key
#      written twice in a row must NOT grow the block (old code: +1
#      duplicate per write when the key line followed the block start).
cat > "$CONFIG_FILE" <<'EOFX'
# >>> herdr-tts settings (managed by the settings popup) >>>
TTS_PROVIDER="edge"
# <<< herdr-tts settings <<<
EOFX
lib_run '
  r1=0; config_set HERDR_TTS_AUDIO_RETENTION_DAYS 7 || r1=$?
  r2=0; config_set HERDR_TTS_AUDIO_RETENTION_DAYS 14 || r2=$?
  echo "r1=$r1 r2=$r2"
' > "$T/out.txt"
assert_grep "30c both writes accepted" '^r1=0 r2=0$' "$T/out.txt"
n=$(grep -c '^HERDR_TTS_AUDIO_RETENTION_DAYS=' "$CONFIG_FILE")
[[ "$n" -eq 1 ]] && ok "30c repeated writes stay at exactly one line" || bad "30c $n lines (want 1, growth regression)"
grep -qF 'HERDR_TTS_AUDIO_RETENTION_DAYS="14"' "$CONFIG_FILE" \
  && ok "30c second write wins" || bad "30c value stale"
bash -c 'source "$1" >/dev/null 2>&1 && printf "src:%s\n" "$HERDR_TTS_AUDIO_RETENTION_DAYS"' _ "$CONFIG_FILE" > "$T/out.txt"
assert_grep "30c file stays bash-sourceable" '^src:14$' "$T/out.txt"

echo "── 31. voz por agente/chat (PRD HT-02): voices.json + --voice-for + paleta ctrl-v"
new_env s31
FX="$T/fixture.json"; make_fixture "$FX"
write_herdr_stub "$FX"
# 31a CLI writes: pane and agent rules land in voices.json; 'off' deletes (RF-HT-02-6)
timeout 10 "$SCRIPT" --voice-for pane w4:p4 alvaro > "$T/out.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "31a voice-for pane set rc=0" || bad "31a rc!=0"
jq -e '.pane["w4:p4"] == "alvaro"' "$XDG_CONFIG_HOME/herdr-tts/voices.json" >/dev/null \
  && ok "31a pane rule persisted" || bad "31a pane rule missing"
timeout 10 "$SCRIPT" --voice-for agent claude-code ximena > /dev/null 2>>"$T/err.log"
jq -e '.agent["claude-code"] == "ximena"' "$XDG_CONFIG_HOME/herdr-tts/voices.json" >/dev/null \
  && ok "31a agent rule persisted" || bad "31a agent rule missing"
timeout 10 "$SCRIPT" --voice-for pane w4:p4 off > /dev/null 2>>"$T/err.log"
jq -e '(.pane // {}) | has("w4:p4") | not' "$XDG_CONFIG_HOME/herdr-tts/voices.json" >/dev/null \
  && ok "31a off deletes the pane rule" || bad "31a clear failed"
# 31b precedence pane > agent > global + spoken prefix (RF-HT-02-2 / RF-HT-02-5)
export HERDR_TTS_VOICES_FILE="$XDG_CONFIG_HOME/herdr-tts/voices.json"
printf '{\n  "agent": {"claude-code": "ximena"},\n  "pane": {"w4:p4": "alvaro"},\n  "prefix": true\n}\n' > "$HERDR_TTS_VOICES_FILE"
lib_run 'v1=$(resolve_voice w4:p4 claude-code); v2=$(resolve_voice w9:p9 claude-code); v3=$(resolve_voice w9:p9 otro-agente); printf "%s %s %s" "$v1" "$v2" "$v3"' > "$T/out.txt"
assert_grep "31b pane beats agent beats global (RF-HT-02-2)" '^alvaro ximena elvira$' "$T/out.txt"
lib_run 'printf "%s" "$(spoken_prefix claude-code)"' > "$T/out.txt"
assert_grep "31b spoken prefix renders when prefix=true (RF-HT-02-5)" '^claude-code: $' "$T/out.txt"
# 31c invalid voices.json fails open to the global voice + daemon.log line (RF-HT-02-3)
printf 'no-soy-json' > "$HERDR_TTS_VOICES_FILE"
export HERDR_TTS_DAEMON_LOG="$T/daemon.log"
lib_run 'printf "%s" "$(resolve_voice w4:p4 claude-code)"' > "$T/out.txt"
assert_grep "31c malformed file fails open to global" '^elvira$' "$T/out.txt"
grep -q 'invalid voices.json' "$T/daemon.log" \
  && ok "31c fail-open logged to daemon.log" || bad "31c no daemon.log line"
# 31d auto_assign rotation: deterministic per agent_type, provider palette only (RF-HT-02-7)
printf '{"auto_assign": true}' > "$HERDR_TTS_VOICES_FILE"
lib_run 'a1=$(resolve_voice w1:p1 claude-code); a2=$(resolve_voice w1:p1 claude-code); a3=$(resolve_voice w1:p1 openai-code); printf "%s %s %s" "$a1" "$a2" "$a3"' > "$T/out.txt"
r1=$(cut -d' ' -f1 "$T/out.txt"); r2=$(cut -d' ' -f2 "$T/out.txt")
case "$r1" in elvira|alvaro|ximena|dalia) ok "31d rotation picks from the active-provider palette" ;; *) bad "31d '$r1' not in the edge rotation palette" ;; esac
[[ "$r1" == "$r2" ]] && ok "31d rotation is deterministic per agent_type" || bad "31d unstable rotation ($r1 vs $r2)"
# 31e picker: stub fzf returns "ximena" → pane rule persisted via --voice-for
cat > "$T/bin/fzf" <<'EOF'
#!/usr/bin/env bash
cat > /dev/null
printf '%s\n' "ximena"
exit 0
EOF
chmod +x "$T/bin/fzf"
timeout 10 "$SCRIPT" --voice-for pane w4:p4 > "$T/out.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "31e picker rc=0" || bad "31e rc!=0"
jq -e '.pane["w4:p4"] == "ximena"' "$HERDR_TTS_VOICES_FILE" >/dev/null \
  && ok "31e picker persists the chosen voice" || bad "31e picker did not persist"
# 31f palette advertises and routes ctrl-v through --voice-for pane {2}
cat > "$T/bin/fzf" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" > "$T/fzf31.argv"
cat > /dev/null
head -n 1
exit 0
EOF
chmod +x "$T/bin/fzf"
timeout 20 "$SCRIPT" --voice-palette > /dev/null 2>>"$T/err.log"
grep -qF "ctrl-v:execute-silent(" "$T/fzf31.argv" \
  && ok "31f ctrl-v voice bind present" || bad "31f no ctrl-v bind"
grep -qF -- "--voice-for pane {2}" "$T/fzf31.argv" \
  && ok "31f bind routes {2} through --voice-for pane" || bad "31f no --voice-for pane {2} in: $(cat "$T/fzf31.argv")"
grep -qF "ctrl-v: chat voice" "$T/fzf31.argv" \
  && ok "31f header advertises ctrl-v" || bad "31f header hint missing"

echo "── 32. ajustes two-level: category index, scoped knobs, Esc semantics"
new_env s32
FX="$T/fixture.json"; make_fixture "$FX"
write_herdr_stub "$FX"
export HERDR_TTS_CONFIG_FILE="$T/config.env"

# 32a. the index renders the four categories and no flat knob rows.
printf 'q' | timeout 10 "$SCRIPT" --voice-settings > "$T/out.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "32a index-only run exits rc=0" || bad "32a rc!=0"
assert_grep "32a index lists Voice"              ' v  🎙 Voice' "$T/out.txt" -F
assert_grep "32a index lists Audio"              ' a  🔊 Audio' "$T/out.txt" -F
assert_grep "32a index lists Notifications"      ' n  🔔 Notifications' "$T/out.txt" -F
assert_grep "32a index lists Auto-read" ' r  ⚙️  Auto-read' "$T/out.txt" -F
assert_grep "32a index lists the language row" ' l  🌐  Language' "$T/out.txt" -F
assert_grep "32a language row shows the native label" 'Language +English' "$T/out.txt"
assert_no_grep "32a index shows no raw knob rows" 'TTS provider:|Playback target:|ntfy topic:|Anti-spam debounce:' "$T/out.txt"
assert_grep "32a index keeps the R restart hint" 'R +restarts the daemon' "$T/out.txt"

# 32b. each category view renders EXACTLY its own knobs (scoped dispatch:
#      the key set of one category never leaks into another view).
while IFS='|' read -r cat key rows; do
  printf '%sq' "$key" | timeout 10 "$SCRIPT" --voice-settings > "$T/cat.txt" 2>>"$T/err.log"
  IFS='|' read -ra wanted <<< "$rows"
  for row in "${wanted[@]}"; do
    assert_grep "32b ${cat} renders '${row}'" "$row" "$T/cat.txt" -F
  done
done <<'EOF'
voice|v| p  TTS provider:| g  Global voice:| n  Spoken prefix:| u  Auto-assign:| i  Auto language:
audio|a| d  Playback target:| c  Click-through:| r  Audio retention:| s  Done settle:
notifications|n| t  ntfy topic:| f  Podcast feed:| w  Web redirect:
reading|r| v  Auto-read:| a  Auto-read scope:| b  Anti-spam debounce:
EOF
printf 'vq'  | timeout 10 "$SCRIPT" --voice-settings > "$T/voice.txt"  2>>"$T/err.log"
printf 'aq'  | timeout 10 "$SCRIPT" --voice-settings > "$T/audio.txt"  2>>"$T/err.log"
printf 'nq'  | timeout 10 "$SCRIPT" --voice-settings > "$T/notif.txt"  2>>"$T/err.log"
printf 'rq'  | timeout 10 "$SCRIPT" --voice-settings > "$T/read.txt"   2>>"$T/err.log"
assert_no_grep "32b voice view free of audio rows"     'Playback target:|Click-through:|Audio retention:' "$T/voice.txt"
assert_no_grep "32b voice view free of notif rows"     'ntfy topic:|Podcast feed:|Web redirect:' "$T/voice.txt"
assert_no_grep "32b audio view free of voice rows"     'TTS provider:|Global voice:|Auto language:' "$T/audio.txt"
assert_no_grep "32b notif view free of reading rows" 'Auto-read:|Anti-spam debounce:' "$T/notif.txt"
assert_no_grep "32b reading view free of notif rows" 'ntfy topic:|Web redirect:|Click-through:' "$T/read.txt"

# 32c. cycling a knob inside a category persists through the config writer.
printf 'vpq' | timeout 10 "$SCRIPT" --voice-settings > "$T/out.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "32c in-category cycle rc=0" || bad "32c rc!=0"
grep -q 'TTS_PROVIDER="openai"' "$HERDR_TTS_CONFIG_FILE" \
  && ok "32c p cycled provider edge→openai via config_set" || bad "32c provider not persisted"

# 32d. Esc from a category returns to the INDEX (re-render, not exit):
#      3 H-moves (index, voice, index) and the trailing q is consumed by
#      the index — a category-Esc exit would leave it unread at EOF.
printf 'v\033q' | timeout 10 "$SCRIPT" --voice-settings > "$T/out.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "32d Esc-from-category path exits rc=0" || bad "32d rc!=0"
hv=$(esc_count "$T/out.txt" $'\033[H')
[[ "$hv" -eq 3 ]] && ok "32d index → voz → index ($hv H-moves)" || bad "32d H-moves=$hv (want 3)"
[[ $(grep -cF '· Voice & Audio Settings' "$T/out.txt") -eq 2 ]] \
  && ok "32d Esc lands back on the index" || bad "32d index frame count $(grep -cF '· Voice & Audio Settings' "$T/out.txt")"

# 32e. Esc from the index exits the standalone popup after one render.
printf '\033' | timeout 10 "$SCRIPT" --voice-settings > "$T/out.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "32e Esc-from-index exits rc=0" || bad "32e rc!=0"
hv=$(esc_count "$T/out.txt" $'\033[H')
[[ "$hv" -eq 1 ]] && ok "32e exactly one render before the exit ($hv H-move)" || bad "32e H-moves=$hv (want 1)"

# 32f. R reachable from the standalone INDEX through the restart path
#      (26e covers the same key via the voice menu `a` entry).
cat > "$T/recorder.sh" <<EOF
#!/usr/bin/env bash
printf 'args=%s\n' "\$*" >> "$T/restart32.log"
printf '%s\n' "\$\$" > "\${HERDR_TTS_DAEMON_PID_FILE:?}"
EOF
chmod +x "$T/recorder.sh"
rm -f "$T/restart32.log" "$T/daemon32.pid"
( export HERDR_TTS_SCRIPT="$T/recorder.sh" HERDR_TTS_DAEMON_PID_FILE="$T/daemon32.pid"
  printf 'Rq' | timeout 10 "$SCRIPT" --voice-settings > "$T/out.txt" 2>>"$T/err.log" )
[[ $? -eq 0 ]] && ok "32f R-from-index exits rc=0" || bad "32f rc!=0"
assert_grep "32f R renders the confirmation inline" '✓ Daemon restarted' "$T/out.txt"
assert_grep "32f R hit the restart path (stub invoked)" '^args=_daemon-supervised$' "$T/restart32.log"

# 32g. unknown key inside a category warns with the category-scoped hint.
printf 'v@q' | timeout 10 "$SCRIPT" --voice-settings > "$T/out.txt" 2>>"$T/err.log"
assert_grep "32g unknown key in voice warns" 'Unrecognized key \(@\)' "$T/out.txt"
assert_grep "32g voice warning hints its own keys" 'p provider, g global voice' "$T/out.txt"

# 32h. write failure keeps the old value (Persistence on Cycle): a
#      read-only config dir makes config_set fail at its writability
#      guard — the cycle warns inline and the old value stays put.
mkdir "$T/roconf"
printf 'TTS_PROVIDER="edge"\n' > "$T/roconf/config.env"
export HERDR_TTS_CONFIG_FILE="$T/roconf/config.env"
chmod 555 "$T/roconf" # unwritable dir → config_set rc 1 (dir guard)
printf 'vpq' | timeout 10 "$SCRIPT" --voice-settings > "$T/out.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "32h failed-write run exits rc=0 (fail-open)" || bad "32h rc!=0"
assert_grep "32h failed save warns inline" '⚠️.*Could not save TTS_PROVIDER' "$T/out.txt"
assert_grep "32h config_set rejected the unwritable dir" 'config directory is not writable' "$T/err.log"
assert_grep "32h old provider value still renders" 'TTS provider: +edge' "$T/out.txt"
assert_no_grep "32h cycled value never renders" 'TTS provider: +openai' "$T/out.txt"
assert_grep "32h config keeps the old value" 'TTS_PROVIDER="edge"' "$HERDR_TTS_CONFIG_FILE" -F
assert_no_grep_f "32h failed write never persisted openai" 'TTS_PROVIDER="openai"' "$HERDR_TTS_CONFIG_FILE"
chmod 755 "$T/roconf" # restore: keep the suite's rm -rf temp cleanup working
unset HERDR_TTS_CONFIG_FILE

# ═════════════════════════════════════════════════════════════════════════
# 33. bootstrap.sh contract (PM-01): pip-free installs (uv pip / python -m
#     pip — the venv's bin/pip is NEVER invoked), immutable agent-tts pin
#     (tag or full 40-char SHA, never bare main), HERDR_TTS_DEV gate,
#     HERDR_TTS_UPGRADE/--upgrade refresh, checkout-location agnosticism.
#     Harness: PATH-level recorder stubs on a restricted PATH — tool
#     absence is a PATH fact, not a mock. uv venv deliberately creates a
#     pip-less venv (uv's Seed::Disabled default, verified in research).
# ═════════════════════════════════════════════════════════════════════════
bt_init() { # $1 sub-case name → fresh $BT with a restricted-stub bin dir
  BT="$T/bt-$1"; rm -rf "$BT"; mkdir -p "$BT/bin" "$BT/logs" "$BT/data" "$BT/home"
  # mkdir passthrough: bootstrap may mkdir before installing; coreutils
  # must stay reachable while uv/python3 stay OFF the PATH.
  printf '#!/bin/bash\nexec /bin/mkdir "$@"\n' > "$BT/bin/mkdir"; chmod +x "$BT/bin/mkdir"
}
bt_uv() { # recorder uv: `uv venv DIR` creates a pip-less venv
  cat > "$BT/bin/uv" <<'EOF'
#!/bin/bash
printf 'uv %s\n' "$*" >> "${UVLOG:?}"
if [[ "${1:-}" == venv && -n "${2:-}" ]]; then
  /bin/mkdir -p "$2/bin"
  printf '#!/bin/bash\nexit 0\n' > "$2/bin/python"
  /bin/chmod +x "$2/bin/python"
fi
exit 0
EOF
  chmod +x "$BT/bin/uv"
}
bt_py() { # recorder python3: `-m venv` plants a recorder venv python plus a
  # sentinel bin/pip — any invocation of venv bin/pip shows as `venv-pip`.
  # Only builtins and absolute coreutils: this stub runs on a restricted PATH.
  cat > "$BT/bin/python3" <<'EOF'
#!/bin/bash
printf 'python3 %s\n' "$*" >> "${PYLOG:?}"
if [[ "${1:-}" == -m && "${2:-}" == venv && -n "${3:-}" ]]; then
  /bin/mkdir -p "$3/bin"
  {
    printf '%s\n' '#!/bin/bash'
    printf '%s\n' 'printf "venv-python %s\n" "$*" >> "${PYLOG:?}"'
    printf '%s\n' 'exit 0'
  } > "$3/bin/python"
  /bin/chmod +x "$3/bin/python"
  {
    printf '%s\n' '#!/bin/bash'
    printf '%s\n' 'printf "venv-pip %s\n" "$*" >> "${PYLOG:?}"'
    printf '%s\n' 'exit 0'
  } > "$3/bin/pip"
  /bin/chmod +x "$3/bin/pip"
fi
exit 0
EOF
  chmod +x "$BT/bin/python3"
}
bt_run() { # KEY=VAL env pairs, then --, then bootstrap argv
  local -a e=( -u HERDR_TTS_DEV -u HERDR_TTS_UPGRADE -u HERDR_AGENT_TTS_REF )
  while [[ "$1" != -- ]]; do e+=( "$1" ); shift; done; shift
  env "${e[@]}" PATH="$BT/bin" HOME="$BT/home" XDG_DATA_HOME="$BT/data" \
    UVLOG="$BT/logs/uv.log" PYLOG="$BT/logs/py.log" \
    /bin/bash "$BT_SCRIPT" "$@" > "$BT/out.log" 2> "$BT/err.log"
}
BT_SCRIPT="$REPO/scripts/bootstrap.sh"
PIN_RE='git\+https://github\.com/chiptime/agent-tts\.git@[0-9a-f]{40}$'
PIN_PY_RE='git\+https://github\.com/chiptime/agent-tts\.git@[0-9a-f]{40}( |$)'

echo "── 33. bootstrap: pip-free installs, immutable pin, dev gate, upgrade"
new_env s33

# 33a. uv-only machine (python3 unavailable): installs route through
#      `uv pip install --python <venv>/bin/python`; venv stays pip-less.
bt_init uvonly; bt_uv
bt_run --
[[ $? -eq 0 ]] && ok "33a uv-only machine exits rc=0" || bad "33a rc!=0 (out: $(tail -1 "$BT/out.log" 2>/dev/null))"
grep -q '^uv venv ' "$BT/logs/uv.log" && ok "33a venv created via uv" || bad "33a no uv-venv call recorded"
assert_grep "33a uv pip owns the install (pinned source)" "$PIN_RE" "$BT/logs/uv.log"
[[ ! -e "$BT/data/herdr-tts/venv/bin/pip" ]] \
  && ok "33a venv stays pip-less (no bin/pip needed)" || bad "33a bootstrap created/used venv bin/pip"

# 33b. python3-only machine (uv unavailable): venv via python3 -m venv and
#      installs via `venv/bin/python -m pip`; the sentinel bin/pip that the
#      stub plants must never run.
bt_init pyonly; bt_py
bt_run --
[[ $? -eq 0 ]] && ok "33b python3-only machine exits rc=0" || bad "33b rc!=0 (out: $(tail -1 "$BT/out.log" 2>/dev/null))"
grep -q '^python3 -m venv ' "$BT/logs/py.log" && ok "33b venv created via python3 -m venv" || bad "33b no python3-venv call recorded"
assert_grep "33b python -m pip owns the install (pinned source)" "$PIN_PY_RE" "$BT/logs/py.log"
assert_no_grep "33b venv bin/pip never invoked" '^venv-pip ' "$BT/logs/py.log"
[[ ! -e "$BT/logs/uv.log" ]] && ok "33b uv never called (absent from PATH)" || bad "33b uv.log exists on a uv-less machine"

# 33c. no python tooling at all: abort in English BEFORE creating anything.
bt_init notool
bt_run --
[[ $? -ne 0 ]] && ok "33c no-tooling machine exits non-zero" || bad "33c rc==0 without python3/uv"
grep -qiE 'python3|uv' "$BT/err.log" && ok "33c error names the missing prerequisite" || bad "33c error does not name python3/uv"
assert_no_grep "33c failure output is English" 'Instalando|Configurando|Usando|Entorno|Se requiere' "$BT/err.log"
[[ ! -e "$BT/data/herdr-tts" ]] \
  && ok "33c aborts before mkdir (no partial state)" || bad "33c created state before aborting"

# 33d. decoy dev checkout in HOME is ignored unless HERDR_TTS_DEV=1.
bt_init decoy; bt_uv; mkdir -p "$BT/home/Code/personal/agent-tts"
bt_run --
[[ $? -eq 0 ]] && ok "33d public install with decoy HOME exits rc=0" || bad "33d rc!=0"
assert_grep "33d decoy HOME still installs the pinned remote source" "$PIN_RE" "$BT/logs/uv.log"
assert_no_grep_f "33d recorded install never references the decoy path" "$BT/home/Code/personal/agent-tts" "$BT/logs/uv.log"
assert_no_grep "33d progress output is English" 'Instalando|Configurando|Usando|Entorno' "$BT/out.log"

# 33e. explicit dev opt-in: editable install from the local checkout.
bt_init devopt; bt_uv; mkdir -p "$BT/home/Code/personal/agent-tts"
bt_run HERDR_TTS_DEV=1 --
[[ $? -eq 0 ]] && ok "33e HERDR_TTS_DEV=1 exits rc=0" || bad "33e rc!=0"
grep -qF -- "-e $BT/home/Code/personal/agent-tts" "$BT/logs/uv.log" \
  && ok "33e dev opt-in installs editable from the checkout" || bad "33e no editable install recorded"

# 33f. upgrade path: healthy venv short-circuits by default; the upgrade
#      mode (env var AND --upgrade argv) refreshes the pinned ref.
bt_init upg; bt_uv
mkdir -p "$BT/data/herdr-tts/venv/bin"
printf '#!/bin/bash\nexit 0\n' > "$BT/data/herdr-tts/venv/bin/python"
chmod +x "$BT/data/herdr-tts/venv/bin/python"
bt_run --
[[ $? -eq 0 ]] && ok "33f healthy venv re-run exits rc=0" || bad "33f re-run rc!=0"
[[ ! -s "$BT/logs/uv.log" ]] && ok "33f default re-run installs nothing" || bad "33f default re-run re-installed"
bt_run HERDR_TTS_UPGRADE=1 --
[[ $? -eq 0 ]] && ok "33f HERDR_TTS_UPGRADE=1 exits rc=0" || bad "33f upgrade rc!=0"
grep -qE -- '--upgrade.*agent-tts\.git@[0-9a-f]{40}' "$BT/logs/uv.log" \
  && ok "33f env upgrade refreshes the pinned ref" || bad "33f no --upgrade install recorded"
bt_run -- --upgrade
[[ $? -eq 0 ]] && ok "33f --upgrade argv exits rc=0" || bad "33f --upgrade rc!=0"
[[ $(grep -c -- '--upgrade' "$BT/logs/uv.log") -eq 2 ]] \
  && ok "33f --upgrade argv is a synonym (2 recorded upgrades)" || bad "33f upgrade count $(grep -c -- '--upgrade' "$BT/logs/uv.log") != 2"

# 33g. checkout-location agnostic: running from a non-canonical copy of
#      scripts/ behaves identically (no repo-relative or $HOME-relative
#      expectations beyond the dev-gated shortcut).
bt_init spot; bt_uv
mkdir -p "$BT/elsewhere"; cp -r "$REPO/scripts" "$BT/elsewhere/scripts"
BT_SCRIPT="$BT/elsewhere/scripts/bootstrap.sh"
bt_run --
[[ $? -eq 0 ]] && ok "33g arbitrary checkout location exits rc=0" || bad "33g rc!=0"
assert_grep "33g arbitrary location installs the pinned source" "$PIN_RE" "$BT/logs/uv.log"
BT_SCRIPT="$REPO/scripts/bootstrap.sh"
unset BT UVLOG PYLOG BT_SCRIPT PIN_RE PIN_PY_RE

echo "── 34. install.sh: preflight, fresh e2e, upgrade guard, keymap policy"
new_env s34

# ═════════════════════════════════════════════════════════════════════════
# 34. install.sh contract (PM-01): preflight before mutation (jq missing,
#     linked checkout refusal), fresh end-to-end install, upgrade vs
#     remote-mismatch, keymap adoption policy (default/never-overwrite/
#     --no-keymap), uninstall print, English output, tag-pinned default
#     (v0.16.0) with HERDR_TTS_REF escape hatch, absolute clone target
#     via `git -C` from an unrelated cwd.
# ═════════════════════════════════════════════════════════════════════════
INS_TEMPLATE="$T/repo-template"
mkdir -p "$INS_TEMPLATE"
cp -r "$REPO/bin" "$REPO/scripts" "$REPO/lib" "$REPO/herdr-plugin.toml" "$INS_TEMPLATE/" 2>/dev/null
export HERDR_CONFIG_DIR="$T/conf" # keymap apply target stays hermetic
ins_init() { # $1 case → $INS with stub bin/, isolated data/logs
  INS="$T/ins-$1"; rm -rf "$INS"; mkdir -p "$INS/bin" "$INS/logs" "$INS/data" "$INS/home"
  INS_PATH="$INS/bin:/usr/bin:/bin" # real jq reachable; stubs shadow git/uv/herdr
  cat > "$INS/bin/git" <<'EOF'
#!/bin/bash
printf 'git %s\n' "$*" >> "${INSLOG:?}/git.log"
if [[ "${1:-}" == clone ]]; then
  /bin/cp -r "${SRC_TEMPLATE:?}/." "${@: -1}"
elif [[ "${1:-}" == -C && "${3:-}" == remote && "${4:-}" == get-url ]]; then
  /bin/cat "${REMOTE_FIXTURE:?}"; exit 0
fi
exit 0
EOF
  chmod +x "$INS/bin/git"
  cat > "$INS/bin/herdr" <<'EOF'
#!/bin/bash
printf 'herdr %s\n' "$*" >> "${INSLOG:?}/herdr.log"
if [[ "${1:-}" == plugin && "${2:-}" == list ]]; then
  /bin/cat "${PLUGIN_FIXTURE:?}"; exit 0
fi
exit 0
EOF
  chmod +x "$INS/bin/herdr"
  cat > "$INS/bin/uv" <<'EOF'
#!/bin/bash
printf 'uv %s\n' "$*" >> "${UVLOG:?}"
if [[ "${1:-}" == venv && -n "${2:-}" ]]; then
  /bin/mkdir -p "$2/bin"
  printf '#!/bin/bash\nexit 0\n' > "$2/bin/python"
  /bin/chmod +x "$2/bin/python"
fi
exit 0
EOF
  chmod +x "$INS/bin/uv"
}
ins_plugins() { # $1 kind → fixture for `herdr plugin list --json`
  printf '{"result":{"plugins":[{"plugin_id":"herdr.tts","source":{"kind":"%s"},"plugin_root":"/x"}]}}\n' "$1" > "$INS/plugins.json"
}
ins_run() { # KEY=VAL env pairs, then --, then installer argv
  local -a e=( -u HERDR_TTS_REF -u HERDR_TTS_DEV -u HERDR_TTS_UPGRADE -u HERDR_TTS_KEYMAP_FILE )
  while [[ "$1" != -- ]]; do e+=( "$1" ); shift; done; shift
  env "${e[@]}" PATH="$INS_PATH" HOME="$INS/home" XDG_DATA_HOME="$INS/data" \
    INSLOG="$INS/logs" UVLOG="$INS/logs/uv.log" \
    SRC_TEMPLATE="$INS_TEMPLATE" REMOTE_FIXTURE="$INS/remote.txt" \
    PLUGIN_FIXTURE="$INS/plugins.json" \
    /bin/bash "$REPO/scripts/install.sh" "$@" > "$INS/out.log" 2> "$INS/err.log"
}

# 34a. jq missing: abort naming jq before any mutation (in-1, in-6).
ins_init nojq; ins_plugins github
printf 'https://github.com/chiptime/herdr-tts.git\n' > "$INS/remote.txt"
INS_PATH="$INS/bin" # restricted: no system jq anywhere
ins_run --
[[ $? -ne 0 ]] && ok "34a jq-missing exits non-zero" || bad "34a rc==0 without jq"
grep -q 'jq' "$INS/err.log" && ok "34a error names jq" || bad "34a jq not named"
[[ ! -e "$INS/data/herdr-tts" ]] && ok "34a no clone/venv artifacts on abort" || bad "34a artifacts created before abort"
[[ ! -e "$T/conf/herdr-tts/keymap.json" && ! -e "$T/conf/herdr/config.toml" ]] \
  && ok "34a no keymap artifacts on abort" || bad "34a keymap artifacts created"
assert_no_grep "34a failure output is English" 'Instalando|Configurando|Usando|Entorno|Se requiere' "$INS/err.log"

# 34b. linked dev checkout: refuse before mutating, guide migration (in-1).
ins_init linked; ins_plugins local
printf 'https://github.com/chiptime/herdr-tts.git\n' > "$INS/remote.txt"
ins_run --
[[ $? -ne 0 ]] && ok "34b linked checkout exits non-zero" || bad "34b rc==0 over a linked checkout"
grep -q 'plugin unlink' "$INS/err.log" && grep -q 'plugin uninstall' "$INS/err.log" \
  && ok "34b guides unlink/uninstall migration" || bad "34b migration guidance missing"
[[ ! -e "$INS/data/herdr-tts" ]] && ok "34b mutates nothing" || bad "34b wrote despite refusal"

# 34c. fresh end-to-end: clone (absolute TARGET, from an unrelated cwd),
#      bootstrap, keymap adopt+apply+reload, daemon verify, status pointer,
#      uninstall print, tag-pinned default (in-2, in-5, in-7).
ins_init fresh; ins_plugins github
printf 'https://github.com/chiptime/herdr-tts.git\n' > "$INS/remote.txt"
sleep 60 & DAEMON_PID=$!
mkdir -p "$T/state/herdr-tts"; printf '%s\n' "$DAEMON_PID" > "$T/state/herdr-tts/daemon.pid"
mkdir -p "$T/unrelated-cwd"
( cd "$T/unrelated-cwd" && ins_run -- )
[[ $? -eq 0 ]] && ok "34c fresh install exits rc=0" || bad "34c rc!=0 (err: $(tail -1 "$INS/err.log" 2>/dev/null))"
grep -qF -- "--branch v0.16.0 https://github.com/chiptime/herdr-tts.git $INS/data/herdr-tts/plugin" "$INS/logs/git.log" \
  && ok "34c clones the pinned tag to the absolute TARGET from an unrelated cwd" \
  || bad "34c clone argv wrong: $(grep clone "$INS/logs/git.log" 2>/dev/null)"
[[ -x "$INS/data/herdr-tts/plugin/bin/herdr-tts" ]] && ok "34c checkout materialized at TARGET" || bad "34c no checkout at TARGET"
grep -qE 'uv pip install .*agent-tts\.git@[0-9a-f]{40}' "$INS/logs/uv.log" \
  && ok "34c bootstrap ran inside the install (pinned agent-tts)" || bad "34c no pinned install recorded"
[[ -f "$T/conf/herdr-tts/keymap.json" ]] && ok "34c menu-style keymap adopted" || bad "34c keymap.json missing"
grep -q 'generated by: herdr-tts keymap apply' "$T/conf/herdr/config.toml" \
  && ok "34c managed keymap block landed in the resolved config" || bad "34c no managed block in config"
first_pl=$(grep -n 'plugin list' "$INS/logs/herdr.log" | head -1 | cut -d: -f1)
reload_ln=$(grep -n 'server reload-config' "$INS/logs/herdr.log" | head -1 | cut -d: -f1)
[[ -n "$reload_ln" && "$reload_ln" -gt "$first_pl" ]] \
  && ok "34c reload-config runs after preflight (stage order)" || bad "34c reload-config missing or out of order"
grep -q 'daemon is running' "$INS/out.log" && ok "34c daemon verification reports the live pid" || bad "34c no daemon-verify line"
grep -q -- '--status' "$INS/out.log" && ok "34c prints the --status pointer" || bad "34c no --status pointer"
grep -q -- 'rm -rf' "$INS/out.log" && grep -q 'daemon' "$INS/out.log" && grep -q 'keymap block' "$INS/out.log" \
  && ok "34c uninstall print: daemon stop, data removal, keymap block" || bad "34c uninstall steps incomplete"
kill "$DAEMON_PID" 2>/dev/null; wait "$DAEMON_PID" 2>/dev/null

# 34d. matching remote: re-run upgrades — fetch+checkout of the tag and an
#      agent-tts refresh past the never-upgrade gate (in-3).
ins_init upg; ins_plugins github
printf 'https://github.com/chiptime/herdr-tts.git\n' > "$INS/remote.txt"
ins_run --
ins_run --
[[ $? -eq 0 ]] && ok "34d matching-remote re-run exits rc=0" || bad "34d rc!=0"
grep -qF -- "-C $INS/data/herdr-tts/plugin fetch origin v0.16.0" "$INS/logs/git.log" \
  && ok "34d upgrade fetches the tag via git -C TARGET" || bad "34d no fetch recorded"
grep -qF -- "-C $INS/data/herdr-tts/plugin checkout v0.16.0" "$INS/logs/git.log" \
  && ok "34d upgrade checks out the tag" || bad "34d no checkout recorded"
grep -qE -- '--upgrade.*agent-tts\.git@[0-9a-f]{40}' "$INS/logs/uv.log" \
  && ok "34d agent-tts refreshed past the never-upgrade gate" || bad "34d no --upgrade install recorded"

# 34e. mismatched remote: abort naming the mismatch, write nothing (in-3).
ins_init mism; ins_plugins github
printf 'https://github.com/chiptime/herdr-tts.git\n' > "$INS/remote.txt"
ins_run --
before_tree=$(find "$INS/data/herdr-tts/plugin" -type f | sort | xargs md5sum | md5sum)
before_writes=$(grep -cE 'clone|fetch|checkout' "$INS/logs/git.log"); before_uv=$(wc -l < "$INS/logs/uv.log")
printf 'https://evil.example.com/other.git\n' > "$INS/remote.txt"
ins_run --
[[ $? -ne 0 ]] && ok "34e mismatched remote exits non-zero" || bad "34e rc==0"
grep -q 'remote' "$INS/err.log" && ok "34e error names the remote mismatch" || bad "34e mismatch not named"
[[ $(grep -cE 'clone|fetch|checkout' "$INS/logs/git.log") -eq "$before_writes" && $(wc -l < "$INS/logs/uv.log") -eq "$before_uv" ]] \
  && ok "34e zero write commands and zero installs after the mismatch" || bad "34e write/install ran after mismatch"
[[ $(find "$INS/data/herdr-tts/plugin" -type f | sort | xargs md5sum | md5sum) == "$before_tree" ]] \
  && ok "34e checkout tree byte-identical" || bad "34e checkout mutated"

# 34f. existing keymap.json: byte-identical, no adopt/apply/reload (in-4).
ins_init keep; ins_plugins github
printf 'https://github.com/chiptime/herdr-tts.git\n' > "$INS/remote.txt"
mkdir -p "$T/conf/herdr-tts"
printf '{\n  "style": "direct",\n  "bindings": { "play": "prefix+F9" }\n}\n' > "$T/conf/herdr-tts/keymap.json"
cp "$T/conf/herdr-tts/keymap.json" "$INS/expected-keymap.json"
ins_run --
[[ $? -eq 0 ]] && ok "34f re-run with existing keymap exits rc=0" || bad "34f rc!=0"
cmp -s "$T/conf/herdr-tts/keymap.json" "$INS/expected-keymap.json" \
  && ok "34f existing keymap byte-identical" || bad "34f keymap overwritten"
grep -q 'server reload-config' "$INS/logs/herdr.log" \
  && bad "34f reload ran despite existing keymap" || ok "34f no reload-config without adoption"
rm -f "$T/conf/herdr-tts/keymap.json" "$T/conf/herdr/config.toml"

# 34g. --no-keymap: zero keymap artifacts of any kind (in-4).
ins_init nokey; ins_plugins github
printf 'https://github.com/chiptime/herdr-tts.git\n' > "$INS/remote.txt"
ins_run -- --no-keymap
[[ $? -eq 0 ]] && ok "34g --no-keymap exits rc=0" || bad "34g rc!=0"
[[ ! -e "$T/conf/herdr-tts/keymap.json" && ! -e "$T/conf/herdr/config.toml" ]] \
  && ok "34g no keymap.json or managed block" || bad "34g keymap artifacts exist"
grep -q 'server reload-config' "$INS/logs/herdr.log" \
  && bad "34g reload-config ran" || ok "34g no reload-config invocation"

# 34h. HERDR_TTS_REF escape hatch: mutable ref only when explicit (in-7).
ins_init hatch; ins_plugins github
printf 'https://github.com/chiptime/herdr-tts.git\n' > "$INS/remote.txt"
ins_run HERDR_TTS_REF=main --
[[ $? -eq 0 ]] && ok "34h HERDR_TTS_REF=main exits rc=0" || bad "34h rc!=0"
grep -qF -- '--branch main' "$INS/logs/git.log" \
  && ok "34h explicit hatch clones main" || bad "34h main not used"
grep -qE 'uv pip install .*agent-tts\.git@[0-9a-f]{40}' "$INS/logs/uv.log" \
  && ok "34h agent-tts stays SHA-pinned regardless" || bad "34h agent-tts pin loosened"
unset INS INS_PATH INS_TEMPLATE
unset HERDR_CONFIG_DIR

# ═════════════════════════════════════════════════════════════════════════
# 35. agent skill distribution: skill install publishes the managed
#     SKILL.md into each agent's user-level directory (hermetic via
#     HERDR_TTS_SKILLS_HOME), idempotent re-run (identical content is a
#     no-op success), never-overwrite adoption policy (modified file
#     refuses without --force, --force replaces), marker-guarded
#     uninstall (idempotent, foreign content refused), list state,
#     unknown agent/option/subcommand rejection, dispatch + help wiring.
# ═════════════════════════════════════════════════════════════════════════
echo "── 35. skill: install, never-overwrite policy, uninstall, list"
new_env s35
export HERDR_TTS_SKILLS_HOME="$T/skills"
run_sk() { timeout 10 "$SCRIPT" skill "$@" > "$T/out.txt" 2>&1; }
sk_file() { # $1 = agent-relative subpath → SKILL.md path under the fake HOME
  printf '%s/%s\n' "$HERDR_TTS_SKILLS_HOME" "$1"
}

# 35a. install claude-code: documented path, frontmatter, agent ops, marker.
run_sk install claude-code
[[ $? -eq 0 ]] && ok "35a install claude-code exits rc=0" || bad "35a rc!=0"
CC="$(sk_file .claude/skills/herdr-tts/SKILL.md)"
[[ -f "$CC" ]] && ok "35a SKILL.md at the documented Claude Code path" || bad "35a missing $CC"
grep -q '^name: herdr-tts' "$CC" && ok "35a frontmatter names the skill" || bad "35a no name frontmatter"
grep -q '^description:' "$CC" && ok "35a frontmatter carries a description" || bad "35a no description"
assert_grep "35a teaches --status" 'herdr-tts --status' "$CC" -F
assert_grep "35a teaches mute" 'herdr-tts --mute-pane' "$CC" -F
assert_grep "35a teaches pane snooze" 'herdr-tts --snooze' "$CC" -F
assert_grep "35a teaches replay" 'herdr-tts --play' "$CC" -F
assert_grep "35a teaches keymap check" 'herdr-tts keymap check' "$CC" -F
assert_grep "35a carries the managed marker" 'managed-by: herdr-tts skill install' "$CC" -F

# 35b. idempotence: identical re-run succeeds and leaves the file untouched.
cp "$CC" "$T/sk.bak"
run_sk install claude-code
[[ $? -eq 0 ]] && ok "35b identical re-run exits rc=0 (idempotent)" || bad "35b rc!=0"
cmp -s "$CC" "$T/sk.bak" && ok "35b identical re-run left the file untouched" || bad "35b file mutated"

# 35c. never-overwrite: modified file refuses without --force, --force replaces.
printf '\n# my local tweak\n' >> "$CC"
cp "$CC" "$T/sk.mod"
run_sk install claude-code
[[ $? -ne 0 ]] && ok "35c modified file refuses without --force (rc!=0)" || bad "35c silent overwrite allowed"
assert_grep "35c refusal is actionable (--force hint)" 'skill install claude-code --force' "$T/out.txt" -F
cmp -s "$CC" "$T/sk.mod" && ok "35c refused install left the file untouched" || bad "35c file mutated"
run_sk install claude-code --force
[[ $? -eq 0 ]] && ok "35c --force replaces the file" || bad "35c --force rc!=0"
cmp -s "$CC" "$T/sk.bak" && ok "35c --force restored the managed content" || bad "35c --force content mismatch"

# 35d. uninstall: removes file + empty dir; second run is a no-op success.
run_sk uninstall claude-code
[[ $? -eq 0 ]] && ok "35d uninstall exits rc=0" || bad "35d rc!=0"
[[ ! -e "$CC" ]] && ok "35d SKILL.md removed" || bad "35d file still present"
[[ ! -d "$(dirname "$CC")" ]] && ok "35d empty skill dir pruned" || bad "35d dir left behind"
run_sk uninstall claude-code
[[ $? -eq 0 ]] && ok "35d uninstall is idempotent (nothing to do)" || bad "35d rc!=0"

# 35e. uninstall refuses a foreign SKILL.md (no managed marker).
mkdir -p "$(dirname "$CC")"
printf -- '---\nname: herdr-tts\ndescription: mine\n---\nmy own rules\n' > "$CC"
run_sk uninstall claude-code
[[ $? -ne 0 ]] && ok "35e foreign SKILL.md refuses deletion (rc!=0)" || bad "35e deleted a foreign file"
[[ -f "$CC" ]] && ok "35e foreign file untouched" || bad "35e file removed"
rm -f "$CC" # clean slate for the all-agents pass below

# 35f. list: installed vs not-installed rows for every supported agent.
run_sk install pi
run_sk list
[[ $? -eq 0 ]] && ok "35f list exits rc=0" || bad "35f rc!=0"
assert_grep "35f pi row installed" '✓ +pi ' "$T/out.txt"
assert_grep "35f claude-code row not installed" 'claude-code.*not installed' "$T/out.txt"
for a in opencode codex; do
  assert_grep "35f $a row present" "$a" "$T/out.txt"
done

# 35g. every supported agent lands in its documented user-level subpath.
for pair in "claude-code:.claude/skills" "opencode:.config/opencode/skills" \
            "codex:.codex/skills" "pi:.pi/agent/skills"; do
  a="${pair%%:*}"; sub="${pair#*:}"
  run_sk install "$a"
  [[ $? -eq 0 ]] && ok "35g install $a exits rc=0" || bad "35g $a rc!=0"
  [[ -f "$HERDR_TTS_SKILLS_HOME/$sub/herdr-tts/SKILL.md" ]] \
    && ok "35g $a SKILL.md at $sub/herdr-tts/" || bad "35g $a wrong path"
done

# 35h. argument hygiene + dispatch/help wiring.
run_sk install not-an-agent
[[ $? -ne 0 ]] && ok "35h unknown agent rejected" || bad "35h rc==0 for unknown agent"
assert_grep "35h error lists supported agents" 'claude-code, opencode, codex, pi' "$T/out.txt" -F
run_sk install claude-code --bogus
[[ $? -ne 0 ]] && ok "35h unknown option rejected" || bad "35h rc==0 for unknown option"
run_sk install
[[ $? -ne 0 ]] && ok "35h missing agent rejected" || bad "35h rc==0 without agent"
run_sk frobnicate
[[ $? -ne 0 ]] && ok "35h unknown subcommand rejected" || bad "35h rc==0 for unknown subcommand"
assert_grep "35h unknown subcommand hints usage" 'use install, uninstall or list' "$T/out.txt" -F
run_sk help
grep -q 'skill install' "$T/out.txt" && ok "35h skill help renders" || bad "35h no help output"
timeout 10 "$SCRIPT" --help 2>&1 | grep -q 'skill install <agent>' \
  && ok "35h top-level --help documents the skill family" || bad "35h missing from --help"

unset HERDR_TTS_SKILLS_HOME

echo "── 36. UI language (HERDR_TTS_LANG): EN default, es dictionary, invalid fallback"

# 36a. Default (no env var, no config key): a known runtime label renders
#      in English — zero Spanish reaches the terminal with default config.
rm -f "$T/lang.env"
( export HERDR_TTS_CONFIG_FILE="$T/lang.env"
  unset HERDR_TTS_LANG
  timeout 10 "$SCRIPT" --player-status > "$T/out36a.txt" 2>/dev/null ) || true
assert_grep "36a default renders the idle label in English" 'Idle \(no active playback\)' "$T/out36a.txt"
assert_no_grep "36a default leaks no Spanish" 'En reposo' "$T/out36a.txt"

# 36b. HERDR_TTS_LANG=es: the same label renders its Spanish equivalent.
( export HERDR_TTS_CONFIG_FILE="$T/lang.env" HERDR_TTS_LANG=es
  timeout 10 "$SCRIPT" --player-status > "$T/out36b.txt" 2>/dev/null ) || true
assert_grep "36b es renders the Spanish idle label" 'En reposo \(sin reproducción activa\)' "$T/out36b.txt"

# 36c. Invalid values fall back to English silently — the same
#      validation style as the other knobs (never break the run).
( export HERDR_TTS_CONFIG_FILE="$T/lang.env" HERDR_TTS_LANG=fr
  timeout 10 "$SCRIPT" --player-status > "$T/out36c.txt" 2>/dev/null ) || true
assert_grep "36c invalid lang falls back to English" 'Idle \(no active playback\)' "$T/out36c.txt"
assert_no_grep "36c invalid lang renders no Spanish" 'En reposo' "$T/out36c.txt"

# 36d. Settings index `l`: the interface-language row cycles en↔es,
#      persists through the managed writer (config_set HERDR_TTS_LANG)
#      and the popup re-renders in the new language on the very next
#      frame — the ES index documents the ENGLISH chords (r reading).
new_env s36d
unset HERDR_TTS_LANG # deterministic start: no env override, fresh config
export HERDR_TTS_CONFIG_FILE="$T/config.env"
printf 'lq' | timeout 10 "$SCRIPT" --voice-settings > "$T/out36d.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "36d language cycle run exits rc=0" || bad "36d rc!=0"
assert_grep "36d first frame renders the EN index" 'Voice & Audio Settings' "$T/out36d.txt"
grep -q 'HERDR_TTS_LANG="es"' "$HERDR_TTS_CONFIG_FILE" \
  && ok "36d l persisted HERDR_TTS_LANG en→es via config_set" || bad "36d HERDR_TTS_LANG not persisted"
assert_grep "36d re-render flips to the ES index" 'Ajustes de voz y audio' "$T/out36d.txt"
assert_grep "36d ES index documents the English reading chord" ' r  ⚙️  Lectura automática' "$T/out36d.txt" -F
assert_grep "36d ES note confirms the switch" 'Idioma de la interfaz: Español' "$T/out36d.txt" -F
printf 'lq' | timeout 10 "$SCRIPT" --voice-settings > "$T/out36d2.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "36d second run exits rc=0" || bad "36d second run rc!=0"
grep -q 'HERDR_TTS_LANG="en"' "$HERDR_TTS_CONFIG_FILE" \
  && ok "36d second l cycles back es→en" || bad "36d es→en round trip failed"
assert_grep "36d second run re-renders the EN index" 'Voice & Audio Settings' "$T/out36d2.txt"
unset HERDR_TTS_CONFIG_FILE

# 36e. config_set accepts HERDR_TTS_LANG (managed-key allowlist): rc 0,
#      value upserted into the hermetic config.env, file stays
#      bash-sourceable.
new_env s36e
mkdir -p "$T/conf/herdr-tts"
printf 'TTS_PROVIDER="edge"\n' > "$T/conf/herdr-tts/config.env"
lib_run '
  r=0; config_set HERDR_TTS_LANG es || r=$?
  echo "rc=$r"
' > "$T/out36e.txt"
assert_grep "36e config_set accepts HERDR_TTS_LANG (rc 0)" '^rc=0$' "$T/out36e.txt"
grep -q 'HERDR_TTS_LANG="es"' "$T/conf/herdr-tts/config.env" \
  && ok "36e HERDR_TTS_LANG=\"es\" persisted into config.env" || bad "36e HERDR_TTS_LANG not persisted"
bash -c 'source "$1" >/dev/null 2>&1 && printf "src:%s\n" "$HERDR_TTS_LANG"' \
  _ "$T/conf/herdr-tts/config.env" > "$T/out36e2.txt"
assert_grep "36e rewritten file stays bash-sourceable" '^src:es$' "$T/out36e2.txt"

echo "── 37. layer boundary audit (RF-HT-13): host consumes the engine, never re-implements it"

# 37a. The provider cycle list IS the engine catalog: a canary provider
#      that exists ONLY in the engine answer must be reachable from the
#      cycle (the static SETTINGS_PROVIDERS table would wrap to edge).
new_env s37a
FX="$T/fixture.json"; make_fixture "$FX"
write_herdr_stub "$FX"
cat > "$T/data/herdr-tts/venv/bin/python" <<'EOF'
#!/usr/bin/env bash
if [[ "${*}" == *"voice list --json" ]]; then
  printf '%s\n' '{"providers": ["edge", "openai", "elevenlabs", "piper", "kokoro", "futureprov"], "voices": {"edge": ["elvira"], "futureprov": ["canary"]}}'
  exit 0
fi
exec python3 "$@"
EOF
chmod +x "$T/data/herdr-tts/venv/bin/python"
lib_run 'settings_cycle_value provider kokoro' > "$T/out.txt"
grep -qx 'futureprov' "$T/out.txt" \
  && ok "37a provider cycle reaches the catalog canary (kokoro→futureprov)" || bad "37a cycle answered: $(cat "$T/out.txt")"

# 37b. Engine down → fail-open to the static legacy table (RNF-HT-13-2):
#      kokoro is last there, so the cycle wraps to edge.
new_env s37b
FX="$T/fixture.json"; make_fixture "$FX"
write_herdr_stub "$FX"
printf '#!/usr/bin/env bash\nif [[ "${*}" == *"voice list --json" ]]; then exit 1; fi\nexec python3 "$@"\n' > "$T/data/herdr-tts/venv/bin/python"
chmod +x "$T/data/herdr-tts/venv/bin/python"
lib_run 'settings_cycle_value provider kokoro' > "$T/out.txt"
grep -qx 'edge' "$T/out.txt" \
  && ok "37b engine down → legacy table wraps kokoro→edge (fail-open)" || bad "37b cycle answered: $(cat "$T/out.txt")"

# 37c. Static audit: patterns that would re-introduce engine semantics in
#      the host. Comment-only lines are stripped first (history notes
#      mention retired code on purpose).
grep -v '^[[:space:]]*#' "$SCRIPT" > "$T/code-only.sh"
assert_no_grep_f "37c no static voice catalog (RF-HT-13-4)" 'SETTINGS_VOICES' "$T/code-only.sh"
assert_no_grep "37c no inline audio decode in the host (RF-HT-13-2)" 'miniaudio' "$T/code-only.sh"
assert_no_grep "37c no internal engine module imports (public API only)" 'from agent_tts\.|import agent_tts\.' "$T/code-only.sh"

# ═══ 38. tema claro (HT-14): TTS_THEME, theme_color, Apariencia ═══
# Engine IPC stub shared by the capture sub-scenarios: one synthesizing
# status so the Motor line renders the accent color (the roster fixture
# already covers ok/warn/muted); everything else delegates to python3.
write_engine_status_stub() {
  cat > "$T/data/herdr-tts/venv/bin/python" <<'EOF'
#!/usr/bin/env bash
if [[ "${*}" == *"--ipc-cmd status"* ]]; then
  printf '%s\n' 'status=synthesizing pos=1.0 total=42.0 provider=edge voice=elvira text=prueba del tema'
  exit 0
fi
exec python3 "$@"
EOF
  chmod +x "$T/data/herdr-tts/venv/bin/python"
}

# 38a. Managed Theme Key: config_set accepts dark/light (rc 0, persisted,
#      markers + per-process .bak) and rejects anything else BEFORE any
#      filesystem side effect (rc 1, named values on stderr, stored config
#      byte-unchanged, no .bak from the rejected call). 36e pattern.
new_env s38a
mkdir -p "$T/conf/herdr-tts"
printf 'TTS_PROVIDER="edge"\n' > "$T/conf/herdr-tts/config.env"
cp "$T/conf/herdr-tts/config.env" "$T/config.pre"
lib_run '
  r=0; config_set TTS_THEME solarized 2>"$T/reject.err" || r=$?
  echo "rc=$r"
' > "$T/out38a.txt"
assert_grep "38a solarized rejected (rc 1)" '^rc=1$' "$T/out38a.txt"
assert_grep "38a rejection names the allowed values" "must be 'dark' or 'light' \(got 'solarized'\)" "$T/reject.err"
cmp -s "$T/config.pre" "$T/conf/herdr-tts/config.env" \
  && ok "38a rejected call leaves the stored config byte-unchanged" || bad "38a reject mutated config.env"
[[ ! -e "$T/conf/herdr-tts/config.env.bak" ]] \
  && ok "38a rejected call creates no .bak (gate fires pre-I/O)" || bad "38a reject left a .bak"
lib_run 'r=0; config_set TTS_THEME dark || r=$?; echo "rc=$r"' > "$T/out38a.txt"
assert_grep "38a config_set accepts dark (rc 0)" '^rc=0$' "$T/out38a.txt"
grep -q 'TTS_THEME="dark"' "$T/conf/herdr-tts/config.env" \
  && ok "38a dark persisted into config.env" || bad "38a dark not persisted"
assert_grep "38a managed block markers written" '>>> herdr-tts settings' "$T/conf/herdr-tts/config.env"
cmp -s "$T/config.pre" "$T/conf/herdr-tts/config.env.bak" \
  && ok "38a accepted write preserves the .bak snapshot" || bad "38a .bak missing or divergent"
lib_run 'r=0; config_set TTS_THEME light || r=$?; echo "rc=$r"' > "$T/out38a.txt"
assert_grep "38a config_set accepts light (rc 0)" '^rc=0$' "$T/out38a.txt"
bash -c 'source "$1" >/dev/null 2>&1 && printf "src:%s\n" "$TTS_THEME"' \
  _ "$T/conf/herdr-tts/config.env" > "$T/out38a2.txt"
assert_grep "38a rewritten file stays bash-sourceable" '^src:light$' "$T/out38a2.txt"

# 38b. Sole Color Emitter / Dark Byte-Identity / Born-Off Default:
#      differential captures from ONE hermetic run (no committed golden —
#      the frame embeds a wall clock, so identity = color byte pins +
#      normalized-structure diffs). Dark pins are the four shipped byte
#      sequences; unset ≡ dark; light differs ONLY in color bytes;
#      round-trip light→dark restores the baseline.
new_env s38b
FX="$T/fixture.json"; make_fixture "$FX"
write_herdr_stub "$FX"
make_history "$HERDR_TTS_HISTORY_FILE"
write_engine_status_stub
capture 'q\n' "$T/dark-unset.txt"
TTS_THEME=dark capture 'q\n' "$T/dark.txt"
TTS_THEME=light capture 'q\n' "$T/light.txt"
assert_grep "38b dark byte pin: ok green" $'\033[32m' "$T/dark.txt" -F
assert_grep "38b dark byte pin: warn yellow" $'\033[33m' "$T/dark.txt" -F
assert_grep "38b dark byte pin: accent cyan" $'\033[36m' "$T/dark.txt" -F
assert_grep "38b dark byte pin: muted bright-black" $'\033[90m' "$T/dark.txt" -F
sed -e $'s/\033\\[32m//g' -e $'s/\033\\[33m//g' -e $'s/\033\\[36m//g' -e $'s/\033\\[90m//g' \
  "$T/dark.txt" > "$T/dark-stripped.txt"
assert_no_grep "38b dark frame carries no other foreground color" $'\033\[[0-9;]*(3[0-7]|9[0-7])m' "$T/dark-stripped.txt"
diff <(norm_frame "$T/dark-unset.txt") <(norm_frame "$T/dark.txt") > /dev/null \
  && ok "38b unset ≡ dark (born-off default)" || bad "38b unset frame differs from dark"
diff <(strip_ansi "$T/dark.txt") <(strip_ansi "$T/light.txt") > /dev/null \
  && ok "38b plain text untouched (dark vs light differ only in color)" || bad "38b plain text drifted between themes"
# Round-trip + precedence: config.env is written light (captured), env dark
# must NOT override it, then config_set dark restores the dark baseline.
lib_run 'config_set TTS_THEME light' > /dev/null
capture 'q\n' "$T/rt-light.txt"
assert_grep "38b round-trip: config.env light drives the frame" $'\033[30m' "$T/rt-light.txt" -F
TTS_THEME=dark capture 'q\n' "$T/pref.txt"
assert_grep "38b precedence: config.env light outranks env dark" $'\033[30m' "$T/pref.txt" -F
lib_run 'config_set TTS_THEME dark' > /dev/null
capture 'q\n' "$T/rt-dark.txt"
diff <(norm_frame "$T/rt-dark.txt") <(norm_frame "$T/dark.txt") > /dev/null \
  && ok "38b round-trip restores the dark baseline" || bad "38b round-trip ≠ dark baseline"

# 38c. Appearance Category and Theme Knob + Two-Level Navigation:
#      index `t` opens Apariencia (fifth row, after reading), the in-view
#      `t` cycles dark↔light and re-renders the SAME popup (both labels in
#      one capture), persists through config_set (note on the post-cycle
#      frame, cleared on the next), a failing write keeps the old value
#      (32h read-only-dir technique), an unknown key warns scoped to the
#      theme knob, the exit hint stays on the grown index (no clamp), the
#      copy is bilingual, and the persisted value survives 10 cold starts.
new_env s38c
FX="$T/fixture.json"; make_fixture "$FX"
write_herdr_stub "$FX"
export HERDR_TTS_CONFIG_FILE="$T/config.env"

# unit: settings_cycle_value theme alternates and wraps to dark.
lib_run 'settings_cycle_value theme dark' > "$T/cyc1.txt"
grep -qx 'light' "$T/cyc1.txt" && ok "38c cycle unit: dark→light" || bad "38c dark→light answered: $(cat "$T/cyc1.txt")"
lib_run 'settings_cycle_value theme light' > "$T/cyc2.txt"
grep -qx 'dark' "$T/cyc2.txt" && ok "38c cycle unit: light→dark" || bad "38c light→dark answered: $(cat "$T/cyc2.txt")"
lib_run 'settings_cycle_value theme bogus' > "$T/cyc3.txt"
grep -qx 'dark' "$T/cyc3.txt" && ok "38c cycle unit: unknown current wraps to dark" || bad "38c wrap answered: $(cat "$T/cyc3.txt")"

printf 'tq' | timeout 10 "$SCRIPT" --voice-settings > "$T/t-open.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "38c t-open run exits rc=0" || bad "38c t-open rc!=0"
assert_grep "38c t opens the Appearance view" 'herdr-tts · Settings · Appearance' "$T/t-open.txt" -F
assert_grep "38c appearance row shows the theme knob" ' t  Theme:' "$T/t-open.txt" -F
assert_grep "38c dark is the default label" 'Theme:               Dark' "$T/t-open.txt" -F
assert_grep "38c view documents the roster adoption" 'chat roster adopts it too' "$T/t-open.txt"
r38=$(grep -nF ' r  ⚙️  Auto-read' "$T/t-open.txt" | head -1 | cut -d: -f1)
a38=$(grep -nF ' t  🎨 Appearance' "$T/t-open.txt" | head -1 | cut -d: -f1)
[[ -n "$r38" && -n "$a38" && "$r38" -lt "$a38" ]] \
  && ok "38c index lists Appearance after Auto-read" || bad "38c index row order wrong (reading=$r38 appearance=$a38)"
assert_grep "38c exit hint still on the grown index (no clamp)" 'q/Esc quit' "$T/t-open.txt" -F
hv=$(esc_count "$T/t-open.txt" $'\033[H')
[[ "$hv" -eq 3 ]] && ok "38c index → appearance → index ($hv H-moves)" || bad "38c t-open H-moves=$hv (want 3)"

# enriched index hint: settings.unknown_key.index now documents t.
printf '@q' | timeout 10 "$SCRIPT" --voice-settings > "$T/idx-hint.txt" 2>>"$T/err.log"
assert_grep "38c index unknown-key hint documents t appearance" 't appearance' "$T/idx-hint.txt"

# bilingual copy: the ES dictionary renders the same views (before any
# write, so the persisted theme is still the dark default).
( export HERDR_TTS_LANG=es
  printf 'tq' | timeout 10 "$SCRIPT" --voice-settings > "$T/t-es.txt" 2>>"$T/err.log" )
assert_grep "38c ES view title" 'Ajustes · Apariencia' "$T/t-es.txt" -F
assert_grep "38c ES knob row" ' t  Tema:' "$T/t-es.txt" -F
assert_grep "38c ES dark label" 'Tema:                 Oscuro' "$T/t-es.txt" -F

# cycle + persistence + same-popup re-render.
printf 'ttq' | timeout 10 "$SCRIPT" --voice-settings > "$T/t-cycle.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "38c cycle run exits rc=0" || bad "38c cycle rc!=0"
assert_grep "38c pre-cycle label renders" 'Theme:               Dark' "$T/t-cycle.txt" -F
assert_grep "38c same-popup re-render shows Light" 'Theme:               Light' "$T/t-cycle.txt" -F
hv=$(esc_count "$T/t-cycle.txt" $'\033[H')
[[ "$hv" -eq 4 ]] && ok "38c index → appearance(Dark) → appearance(Light) → index ($hv frames)" || bad "38c cycle H-moves=$hv (want 4)"
grep -q 'TTS_THEME="light"' "$HERDR_TTS_CONFIG_FILE" \
  && ok "38c t persisted TTS_THEME=light via config_set" || bad "38c TTS_THEME not persisted"
assert_grep "38c note on the post-cycle frame" '✓ Theme: Light' "$T/t-cycle.txt" -F
[[ $(grep -cF '✓ Theme:' "$T/t-cycle.txt") -eq 1 ]] \
  && ok "38c note is transient (shown once, cleared next frame)" || bad "38c note rendered $(grep -cF '✓ Theme:' "$T/t-cycle.txt") times"

# unknown key inside Apariencia warns scoped to the theme knob only.
printf 't@q' | timeout 10 "$SCRIPT" --voice-settings > "$T/t-unknown.txt" 2>>"$T/err.log"
grep -F 'Unrecognized key (@)' "$T/t-unknown.txt" | tail -1 > "$T/warn-line.txt"
assert_grep "38c unknown key warns scoped to the theme knob" 'Unrecognized key \(@\) — t theme, q back to the index' "$T/warn-line.txt"
assert_no_grep "38c scoped warning names no other category's keys" 'p provider|d playback|ntfy topic|v auto-read' "$T/warn-line.txt"

# write failure keeps the old value (32h technique: read-only config dir).
mkdir "$T/roconf"
printf 'TTS_THEME="dark"\n' > "$T/roconf/config.env"
export HERDR_TTS_CONFIG_FILE="$T/roconf/config.env"
chmod 555 "$T/roconf" # unwritable dir → config_set rc 1 (dir guard)
printf 'ttq' | timeout 10 "$SCRIPT" --voice-settings > "$T/t-ro.txt" 2>>"$T/err.log"
[[ $? -eq 0 ]] && ok "38c failed-write run exits rc=0 (fail-open)" || bad "38c failed-write rc!=0"
assert_grep "38c failed save warns inline" '⚠️.*Could not save TTS_THEME' "$T/t-ro.txt"
assert_grep "38c old theme still renders" 'Theme:               Dark' "$T/t-ro.txt" -F
assert_no_grep_f "38c cycled value never renders" 'Theme:               Light' "$T/t-ro.txt"
grep -q 'TTS_THEME="dark"' "$HERDR_TTS_CONFIG_FILE" \
  && ok "38c config keeps the old value" || bad "38c old value lost"
assert_no_grep_f "38c failed write never persisted light" 'TTS_THEME="light"' "$HERDR_TTS_CONFIG_FILE"
assert_grep "38c config_set rejected the unwritable dir" 'config directory is not writable' "$T/err.log"
chmod 755 "$T/roconf" # restore: keep the suite's rm -rf temp cleanup working
export HERDR_TTS_CONFIG_FILE="$T/config.env"

# 10 cold starts alternate dark↔light: each process re-sources config.env
# (restart persistence) and renders the value the previous run persisted.
# 'ttq' = open the view, cycle the knob, back to the index, EOF exit.
alt38=1; lights38=0; last38=""
for i in 1 2 3 4 5 6 7 8 9 10; do
  cur38=$(grep -o 'TTS_THEME="[a-z]*"' "$HERDR_TTS_CONFIG_FILE" | cut -d'"' -f2)
  lbl38="Dark"; [[ "$cur38" == "light" ]] && lbl38="Light"
  printf 'ttq' | timeout 10 "$SCRIPT" --voice-settings > "$T/cold-$i.txt" 2>>"$T/err.log"
  grep -qF "Theme:               $lbl38" "$T/cold-$i.txt" \
    || bad "38c cold start $i did not render the persisted $lbl38"
  new38=$(grep -o 'TTS_THEME="[a-z]*"' "$HERDR_TTS_CONFIG_FILE" | cut -d'"' -f2)
  [[ "$new38" == "$cur38" ]] && alt38=0
  [[ "$new38" == "light" ]] && lights38=$((lights38+1))
  last38="$new38"
done
[[ "$alt38" -eq 1 ]] && ok "38c 10 cold starts alternate dark↔light" || bad "38c cold starts stopped alternating"
[[ "$lights38" -eq 5 ]] && ok "38c alternation lands on light exactly 5/10 (last=$last38)" || bad "38c light count=$lights38 (want 5)"
unset HERDR_TTS_CONFIG_FILE

# 38d. Sole-emitter static gate (37c comment-strip pattern, no exemption
#      list): the maps hold numeric SGR parameters only and theme_color
#      composes the escape, so NO literal foreground color SGR may exist
#      anywhere in the host script, and no wide-gamut code may appear.
new_env s38d
grep -v '^[[:space:]]*#' "$SCRIPT" > "$T/code-only.sh"
assert_no_grep "38d no literal color SGR anywhere" '\\(033|e)\[[0-9;]*(3[0-7]|9[0-7])m' "$T/code-only.sh"
assert_no_grep "38d no 256-color/truecolor SGR" '(38|48);(5|2);' "$T/code-only.sh"

# 38e. Light Contrast Minimums + Surface Coverage: exact resolver bytes
#      for the 6 tokens × 2 themes (printf %q), unknown token → reset,
#      unknown theme value → the dark map (fail-safe), the light frame
#      carries the light map (32/30/34/1;33) and never the dark-only
#      codes, roster rows adopt it, and the compound 1;33 strips cleanly
#      (visible-width parity with dark).
new_env s38e
FX="$T/fixture.json"; make_fixture "$FX"
write_herdr_stub "$FX"
make_history "$HERDR_TTS_HISTORY_FILE"
write_engine_status_stub
# Expected lines are encoded with printf %q on BOTH sides (theme_color
# composes the raw bytes, the expected side re-composes the spec bytes and
# %q-encodes them) so the assertion stays byte-exact across bash versions
# (5.2 renders ESC as \E, older as \033).
check_theme_map() { # $1 label, $2 file, then token:param pairs
  local label="$1" file="$2" tok param want38
  shift 2
  while [[ $# -gt 0 ]]; do
    tok="$1"; param="$2"; shift 2
    printf -v want38 '\033[%sm' "$param"
    grep -qxF "${tok}=$(printf '%q' "$want38")" "$file" \
      && ok "38e ${label} resolver: ${tok} → ${param}" \
      || bad "38e ${label} resolver: ${tok} (want ${param})"
  done
}
TTS_THEME=dark lib_run 'for t in ok warn error accent muted title; do theme_color v "$t"; printf "%s=%q\n" "$t" "$v"; done' > "$T/res-dark.txt"
check_theme_map dark "$T/res-dark.txt" ok 32 warn 33 error 31 accent 36 muted 90 title 1
TTS_THEME=light lib_run 'for t in ok warn error accent muted title; do theme_color v "$t"; printf "%s=%q\n" "$t" "$v"; done' > "$T/res-light.txt"
check_theme_map light "$T/res-light.txt" ok 32 warn '1;33' error 31 accent 34 muted 30 title 1
TTS_THEME=solarized lib_run 'theme_color v accent; printf "accent=%q\n" "$v"' > "$T/res-fb.txt"
printf -v want38 '\033[36m'
grep -qxF "accent=$(printf '%q' "$want38")" "$T/res-fb.txt" \
  && ok "38e unknown theme value falls back to the dark map" || bad "38e unknown theme did not fall back to dark"
TTS_THEME=light lib_run 'theme_color v unexpected; printf "unknown=%q\n" "$v"' > "$T/res-unk.txt"
printf -v want38 '\033[0m'
grep -qxF "unknown=$(printf '%q' "$want38")" "$T/res-unk.txt" \
  && ok "38e unknown token resolves to reset" || bad "38e unknown token did not resolve to reset"
capture 'q\n' "$T/e-dark.txt"
TTS_THEME=light capture 'q\n' "$T/e-light.txt"
assert_grep "38e light frame: ok stays green" $'\033[32m' "$T/e-light.txt" -F
assert_grep "38e light frame: muted 30" $'\033[30m' "$T/e-light.txt" -F
assert_grep "38e light frame: accent 34" $'\033[34m' "$T/e-light.txt" -F
assert_grep "38e light frame: warn bold 1;33" $'\033[1;33m' "$T/e-light.txt" -F
assert_no_grep_f "38e light frame drops the dark muted 90" $'\033[90m' "$T/e-light.txt"
assert_no_grep_f "38e light frame drops the dark accent 36" $'\033[36m' "$T/e-light.txt"
assert_grep "38e roster working row keeps ok green" $'\033[32m▶' "$T/e-light.txt" -F
assert_grep "38e roster done row carries light warn" $'\033[1;33m✔' "$T/e-light.txt" -F
assert_grep "38e roster idle row carries light muted" $'\033[30m·' "$T/e-light.txt" -F
assert_grep "38e engine line carries light accent" $'\033[34m' "$T/e-light.txt" -F
[[ "$(visible_stats "$T/e-dark.txt")" == "$(visible_stats "$T/e-light.txt")" ]] \
  && ok "38e compound 1;33 strips cleanly (visible-width parity dark vs light)" \
  || bad "38e width parity broke: $(visible_stats "$T/e-dark.txt") vs $(visible_stats "$T/e-light.txt")"

# 39a. Live Theme Adoption (US-HT-14-2): the dashboard is a LONG-RUNNING
#      process whose TTS_THEME was snapshotted at startup; the settings
#      popup persists a new theme from a SEPARATE process. The render loop
#      must re-read the key from config.env per frame (same per-frame state
#      discipline as load_snooze_state / is_playing / is_auto_muted), so
#      the RUNNING dashboard adopts the write on its next frame. Fail-open
#      controls: missing/unreadable config and an absent key keep the
#      current value; a hand-edited solarized normalizes to dark; the
#      per-frame read adds no writes (H-move budget unchanged).
new_env s39a
FX="$T/fixture.json"; make_fixture "$FX"
write_herdr_stub "$FX"
make_history "$HERDR_TTS_HISTORY_FILE"
write_engine_status_stub
printf 'TTS_THEME="dark"\n' > "$T/conf/herdr-tts/config.env"
capture 'q\n' "$T/cap-dark.txt"
assert_grep "39a dark capture carries the dark muted byte" $'\033[90m' "$T/cap-dark.txt" -F

# Differential pair from ONE hermetic process: frame 1 in dark, then an
# external-style write — config_set touches the FILE only, the live var
# provably stays dark, which is exactly what the popup's write looks like
# to the running dashboard — then frame 2 in the SAME process.
lib_run '
  dashboard_refresh_roster
  DASH_LAST_FRAME=""
  dashboard_render "" > "$T/f1.txt"
  config_set TTS_THEME light
  printf "live=%s\n" "$TTS_THEME" > "$T/live.txt"
  DASH_LAST_FRAME=""
  dashboard_render "" > "$T/f2.txt"
'
assert_grep "39a frame 1 (before the write) is dark" $'\033[90m' "$T/f1.txt" -F
assert_grep "39a the file write leaves the dashboard live var untouched" '^live=dark$' "$T/live.txt"
assert_grep "39a frame 2 (after the write) adopts light" $'\033[30m' "$T/f2.txt" -F
assert_no_grep_f "39a frame 2 drops the dark muted byte" $'\033[90m' "$T/f2.txt"

# The persisted light also drives a FRESH dashboard process, and the
# frame budget is unchanged by the re-read.
capture 'q\n' "$T/cap-light.txt"
assert_grep "39a light capture carries the light muted byte" $'\033[30m' "$T/cap-light.txt" -F
h39d=$(esc_count "$T/cap-dark.txt" $'\033[H')
h39l=$(esc_count "$T/cap-light.txt" $'\033[H')
[[ "$h39d" -ge 1 && "$h39d" -eq "$h39l" ]] \
  && ok "39a frame budget unchanged (H-moves dark=$h39d light=$h39l)" \
  || bad "39a frame budget changed (H-moves dark=$h39d light=$h39l)"

# Fail-open controls, each a fresh process whose current value is light.
lib_run '
  TTS_THEME=light
  rm -f "$CONFIG_FILE"
  dashboard_refresh_roster
  DASH_LAST_FRAME=""
  dashboard_render "" > "$T/c-missing.txt"
'
assert_grep "39a missing config keeps rendering (fail open, light kept)" $'\033[30m' "$T/c-missing.txt" -F
printf 'TTS_VOICE="elvira"\n' > "$T/conf/herdr-tts/config.env"
lib_run '
  TTS_THEME=light
  chmod 000 "$CONFIG_FILE"
  dashboard_refresh_roster
  DASH_LAST_FRAME=""
  dashboard_render "" > "$T/c-unreadable.txt"
  chmod 644 "$CONFIG_FILE"
'
assert_grep "39a unreadable config keeps the current theme (light)" $'\033[30m' "$T/c-unreadable.txt" -F
lib_run '
  TTS_THEME=light
  dashboard_refresh_roster
  DASH_LAST_FRAME=""
  dashboard_render "" > "$T/c-absent.txt"
'
assert_grep "39a absent key keeps the current theme (light)" $'\033[30m' "$T/c-absent.txt" -F
printf 'TTS_THEME="solarized"\n' > "$T/conf/herdr-tts/config.env"
lib_run '
  TTS_THEME=light
  dashboard_refresh_roster
  DASH_LAST_FRAME=""
  dashboard_render "" > "$T/c-solarized.txt"
'
assert_grep "39a hand-edited solarized normalizes to dark" $'\033[90m' "$T/c-solarized.txt" -F
assert_no_grep_f "39a solarized never renders the light muted byte" $'\033[30m' "$T/c-solarized.txt"

# 39b. Theme-note copy: the appearance view discloses the instant
#      dashboard adoption in both languages (roster disclosure kept —
#      38c asserts that half on the rendered popup).
lib_run 'tt settings.row.theme_note' > "$T/note-en.txt"
assert_grep "39b EN note documents the instant dashboard adoption" 'instant on the running dashboard' "$T/note-en.txt"
( export HERDR_TTS_LANG=es
  lib_run 'tt settings.row.theme_note' > "$T/note-es.txt" )
assert_grep "39b ES note documents the instant dashboard adoption" 'el dashboard lo adopta al vuelo' "$T/note-es.txt"

echo
echo "═══ RESULT: $PASS passed, $FAIL failed ═══"
exit $(( FAIL > 0 ? 1 : 0 ))
