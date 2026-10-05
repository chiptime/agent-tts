#!/usr/bin/env bash
# clean-install.sh — AT-11 V2 acceptance harness (design Decision 3, slice 1).
#
# Runs installation scenarios inside a clean-room sandbox and reports a
# four-state result per scenario. Built complete as milestone-1 task 1.1,
# before any production change; later tasks add scenario files under
# scenarios/ and extend this harness only where their task says so.
#
# Usage: scripts/acceptance/clean-install.sh [OPTIONS]
#   --milestone N     Run at milestone N (default: highest milestone recorded
#                     as closed in the baseline journal, else 1).
#   --record          Copy journal.json + summary.md to --record-dir
#                     (default <repo>/metrics/acceptance/) as the durable
#                     closure artifact and regression baseline.
#   --record-dir DIR  Target directory for --record.
#   --baseline FILE   Journal compared against by the regression guard
#                     (default: newest clean-install-*.json in record-dir).
#   --keep            Keep the sandbox after the run (path printed).
#   --src PATH        Repository under test (default: this checkout).
#   --drill MODE      Self-drill exit semantics: fail | blocked | regress |
#                     origin (positive drill: the origin-shim must refuse a
#                     non-allowlisted host with BLOCKED-ORIGIN). Drill runs
#                     are tagged "drill": true in the journal and never count
#                     as milestone closure.
#   --list            Print the scenario registry and exit.
#   -h, --help        Print help.
#
# Scenario states (there is deliberately NO SKIP state):
#   PASS               activated and green
#   FAIL               activated and red                                (red)
#   BLOCKED            activated, prerequisite or authorization missing  (red)
#   NOT-YET-ACTIVATED  file not authored or milestone not reached    (neutral)
#
# Exit codes:
#   0  every activated scenario PASS, no regression vs the baseline
#   1  at least one activated scenario FAIL
#   2  at least one activated scenario BLOCKED
#   3  a previously-PASS scenario regressed (precedence: 3 > 1 > 2)
#   4  harness self-check failed (sandbox could not be built)
#  64  usage error
#
# Sandbox: the repo under test is copied (git archive HEAD) to a non-standard
# path under $(mktemp -d), outside $HOME; HOME is created empty; XDG dirs live
# under $SANDBOX; scenarios execute under env -i with a PATH rebuilt from an
# allowlist (bash git jq curl python3 uv + sandbox stubs). Tools missing from
# the host are recorded in the journal, never silently assumed. The harness
# never runs sudo and never writes outside $SANDBOX (--record writes only the
# journal/summary copies named above).
#
# Scenario contract: a scenario file runs under env -i with HOME, XDG_*,
# CHECKOUT (repo-under-test copy), SCEN_DIR (its evidence dir), PATH (the
# allowlist above), HERDR_SANDBOX=1, and this file pre-sourced for its
# assertion vocabulary (ok/bad/ok_stubbed/block/step/assert_grep).
#
# Honesty notes: network access is governed by the documented-origin policy
# shim (slice 2): `curl` and `git` on the sandbox PATH are origin-shim copies
# that refuse non-allowlisted hosts with BLOCKED-ORIGIN. This is a POLICY
# boundary at the tool call, not a kernel namespace — no network-isolation
# claim is made. Evidence lives in $SANDBOX/evidence and is deleted with the
# sandbox unless --keep; --record persists journal.json + summary.md outside
# it. Commands are logged argv-redacted (cmd.log); assertions riding stubs
# rather than real installed components are tagged "stubbed": true in the
# journal. An empty run (zero activated scenarios) exits 0 but is never
# recorded as closing a milestone.
set -uo pipefail

# ---------------------------------------------------------------------------
# Scenario API — the vocabulary scenarios execute with (sourced under env -i
# via HERDR_ACCEPTANCE_API=1). Keep it PURE BASH: the sandbox PATH holds only
# the allowlisted tools, so these helpers must not call grep/sed/tee/date.
# Final states are derived by the harness from assert.log / blocked.reason.
# ---------------------------------------------------------------------------
ok()  { echo "  ok   $1" >> "$SCEN_DIR/assert.log"; echo "  ok   $1"; }
bad() { echo "  FAIL $1" >> "$SCEN_DIR/assert.log"; echo "  FAIL $1"; }

# Assertion that rides a STUB instead of a real installed component: counts as
# a pass but tags the scenario "stubbed": true in the journal (spec rule:
# stubbed V1-style checks stay distinct from real installation evidence).
ok_stubbed() {
  echo "  ok   [stubbed] $1" >> "$SCEN_DIR/assert.log"
  echo "  ok   [stubbed] $1"
}

# Declare the scenario BLOCKED, naming the missing prerequisite. Never a skip.
block() { printf '%s\n' "$1" > "$SCEN_DIR/blocked.reason"; echo "BLOCKED: $1"; exit 0; }

# Run a command with argv-redacted logging; returns the command's exit code.
step() {
  redact_argv "$@" >> "$SCEN_DIR/cmd.log"
  "$@" >> "$SCEN_DIR/stdout.log" 2>&1
}

# Emit one log-safe line of the given argv (bash-only redaction discipline).
# Secret-looking values are replaced by a literal <redacted> marker that stays
# greppable; every other argument is %q-quoted so word boundaries survive.
redact_argv() {
  local a prev="" out="" val
  local -r _marker_re='^[A-Za-z0-9_./-]*=<redacted>$'
  for a in "$@"; do
    if [[ $prev =~ ^--(glm-key|api-key|token|secret|password)$ ]]; then
      out+="<redacted> " # value of a secret flag: never shown, never quoted
      prev=$a; continue
    fi
    val=$a
    if [[ $a =~ ^(--(glm-key|api-key|token|secret|password)|[A-Za-z_]*(KEY|TOKEN|SECRET)[A-Za-z_]*)= ]]; then
      val="${a%%=*}=<redacted>" # name=value form: keep the name, drop the value
    fi
    prev=$a
    if [[ $val =~ $_marker_re ]]; then
      out+="$val "
    else
      out+="$(printf '%q ' "$val")"
    fi
  done
  printf '%s\n' "${out% }"
}

# smoke-tests.sh vocabulary (pure-bash port: [[ =~ ]] replaces grep -E,
# substring match replaces grep -F): assert_grep desc pattern file [-F]
assert_grep() {
  local data; data=$(<"$3")
  if [[ "${4:-}" == "-F" ]]; then
    [[ $data == *"$2"* ]] && ok "$1" || bad "$1 (missing: $2)"
  else
    [[ $data =~ $2 ]] && ok "$1" || bad "$1 (no match: $2)"
  fi
}
assert_no_grep() {
  local data; data=$(<"$3")
  [[ $data =~ $2 ]] && bad "$1 (unexpected: $2)" || ok "$1"
}

# --- id= doc-block extraction (slice 2) -------------------------------------
# The harness executes installation commands from the documentation BY ID,
# from this pinned file allowlist only. It never scans a document and runs
# whatever it finds (threat matrix: documentation-like paths). A V1 test
# (engine/tests/test_versioned_tree_hygiene.py) asserts every id referenced
# by the harness or its scenarios exists exactly once in its expected file.
DOC_BLOCK_ALLOWLIST=(
  "README.md"
  "hosts/herdr/brain/README.md"
  "hosts/herdr/tts-plugin/README.md"
)

# doc_block <repo-relative-file> <id> — print the body of the fenced block
# tagged `id=<id>` in that file. Fails (non-zero, nothing executed) when the
# file is not on the pinned allowlist, is missing, the id is unknown, or the
# id is duplicated in the file.
doc_block() {
  local f=$1 id=$2 rel ok=0 count=0 in_block=0 line body=""
  for rel in "${DOC_BLOCK_ALLOWLIST[@]}"; do
    [[ $rel == "$f" ]] && { ok=1; break; }
  done
  if (( ! ok )); then
    echo "doc-block: refusing '${f}': not on the pinned file allowlist" >&2
    return 1
  fi
  if [[ ! -f "$CHECKOUT/$f" ]]; then
    echo "doc-block: '${f}' not found in the checkout under test" >&2
    return 1
  fi
  local -r open_re='^[[:space:]]*`{3,}[[:space:]]*[a-z]*[[:space:]]*id=("|'"'"')?([A-Za-z0-9_.-]+)("|'"'"')?[[:space:]]*$'
  local -r close_re='^[[:space:]]*`{3,}[[:space:]]*$'
  while IFS= read -r line || [[ -n $line ]]; do
    if (( in_block )); then
      if [[ $line =~ $close_re ]]; then in_block=0; continue; fi
      body+="$line"$'\n'
    elif [[ $line =~ $open_re ]] && [[ ${BASH_REMATCH[2]} == "$id" ]]; then
      (( count++ )); in_block=1
    fi
  done < "$CHECKOUT/$f"
  if (( count == 0 )); then
    echo "doc-block: id '${id}' not found in '${f}'" >&2
    return 1
  fi
  if (( count > 1 )); then
    echo "doc-block: id '${id}' appears ${count} times in '${f}' (must be exactly once)" >&2
    return 1
  fi
  printf '%s' "$body"
}

# run_doc_block <repo-relative-file> <id> — execute that block's body with
# the sandbox bash, logged into cmd.log/stdout.log like any other step.
run_doc_block() {
  local f=$1 id=$2 body
  body=$(doc_block "$f" "$id") || return 1
  redact_argv bash -c "<doc block ${f}#${id}>" >> "$SCEN_DIR/cmd.log"
  printf '%s\n' "$body" >> "$SCEN_DIR/cmd.log"
  bash -c "$body" >> "$SCEN_DIR/stdout.log" 2>&1
}

# ---------------------------------------------------------------------------
# Harness-side helpers (run in the operator's full environment).
# ---------------------------------------------------------------------------
die() { # die <message> [exit-code]
  echo "clean-install.sh: $1" >&2
  exit "${2:-1}"
}

json_escape() { # emit a JSON-safe copy of $1
  local s=$1
  s=${s//\\/\\\\}; s=${s//\"/\\\"}
  s=${s//$'\t'/ }; s=${s//$'\n'/ }; s=${s//$'\r'/ }
  printf '%s' "$s"
}

json_str_list() { # emit ["a", "b"] from argv (possibly empty)
  local e out=""
  for e in "$@"; do out+="\"$(json_escape "$e")\", "; done
  if [[ -n $out ]]; then printf '[%s]' "${out%, }"; else printf '[]'; fi
}

count_lines() { # count_lines <file> <pattern> — grep -c that yields 0
  local n
  n=$(grep -c -- "$2" "$1" 2>/dev/null) || n=0
  printf '%s' "${n:-0}"
}

latest_baseline() { # newest clean-install-*.json in dir $1 (or empty)
  [[ -d ${1:-} ]] || return 0
  ls -1t "$1"/clean-install-*.json 2>/dev/null | head -n 1
}

default_milestone() { # milestone default: highest closed milestone in journal $1
  local best=1 runline re='\"milestone\": ([0-9]+), \"closed\": (true|false)'
  [[ -n ${1:-} && -f $1 ]] || { printf '1'; return; }
  runline=$(grep -m1 '"run": {' "$1")
  [[ $runline =~ $re ]] || { printf '1'; return; }
  [[ ${BASH_REMATCH[2]} == true ]] && best=${BASH_REMATCH[1]}
  printf '%s' "$best"
}

regression_guard() { # fail when a baseline-PASS scenario is no longer PASS
  local pairs line id st cur reg=0
  pairs=$(grep -oE '"id": "[^"]*", "state": "[^"]*"' "$1" 2>/dev/null || true)
  while IFS= read -r line; do
    [[ -z $line ]] && continue
    id=${line#\"id\": \"}; id=${id%%\", \"state\"*}
    st=${line##*\"state\": \"}; st=${st%\"}
    if [[ $st == PASS ]]; then
      cur=${STATES[$id]:-ABSENT}
      if [[ $cur != PASS ]]; then
        echo "  REGRESSION  $id: baseline PASS -> $cur" >&2
        reg=1
      fi
    fi
  done <<< "$pairs"
  return "$reg"
}

# ---------------------------------------------------------------------------
# Sandbox construction and self-check (exit 4 on any failure).
# ---------------------------------------------------------------------------
cleanup() {
  if [[ ${KEEP:-0} == 1 ]]; then
    echo "sandbox kept: $SANDBOX (evidence: $SANDBOX/evidence)"
  else
    rm -rf "$SANDBOX"
  fi
}

build_sandbox() {
  SANDBOX=$(mktemp -d "${TMPDIR:-/tmp}/at11-clean-install.XXXXXX") \
    || die "self-check: mktemp failed" 4
  trap cleanup EXIT
  mkdir -p "$SANDBOX/home" "$SANDBOX/xdg/config" "$SANDBOX/xdg/data" \
    "$SANDBOX/xdg/state" "$SANDBOX/xdg/cache" "$SANDBOX/bin" \
    "$SANDBOX/checkout" "$SANDBOX/evidence" \
    || die "self-check: cannot create sandbox dirs" 4
  EVIDENCE="$SANDBOX/evidence"
  CHECKOUT="$SANDBOX/checkout/checkout-$RANDOM"
  mkdir -p "$CHECKOUT"
  if ! git -C "$SRC" archive HEAD | tar -x -C "$CHECKOUT"; then
    die "self-check: cannot copy the repo under test from '$SRC' into the sandbox" 4
  fi
  # Documented-origin policy shim: sandbox `curl` and `git` are origin-shim
  # COPIES (first on PATH). Tool-call policy boundary, not a namespace.
  cp "$HARNESS_DIR/origin-shim" "$SANDBOX/bin/curl"
  cp "$HARNESS_DIR/origin-shim" "$SANDBOX/bin/git"
  chmod 0755 "$SANDBOX/bin/curl" "$SANDBOX/bin/git"
  cp "$HARNESS_DIR/allowed-origins.txt" "$SANDBOX/allowed-origins.txt" \
    || die "self-check: allowed-origins.txt missing next to the harness" 4
  # Rebuild PATH from the allowlist; the stubs dir always wins.
  local t p d
  declare -A _seen=()
  SANDBOX_PATH="$SANDBOX/bin"
  for t in "${ALLOWED_TOOLS[@]}"; do
    if p=$(command -v "$t" 2>/dev/null); then
      d=$(dirname "$p")
      if [[ -z ${_seen[$d]:-} ]]; then SANDBOX_PATH+=":$d"; _seen[$d]=1; fi
      TOOLS_AVAILABLE+=("$t")
    else
      TOOLS_MISSING+=("$t")
    fi
  done
  # Single source of truth for the env -i scenario contract (SCEN_DIR is
  # appended per invocation by run_scenario / self_check).
  SANDBOX_ENV_BASE=(
    HOME="$SANDBOX/home" PATH="$SANDBOX_PATH"
    XDG_CONFIG_HOME="$SANDBOX/xdg/config" XDG_DATA_HOME="$SANDBOX/xdg/data"
    XDG_STATE_HOME="$SANDBOX/xdg/state" XDG_CACHE_HOME="$SANDBOX/xdg/cache"
    TERM=dumb LANG=C.UTF-8 LC_ALL=C.UTF-8 HERDR_SANDBOX=1
    CHECKOUT="$CHECKOUT"
    HERDR_ALLOWED_ORIGINS="$SANDBOX/allowed-origins.txt"
    HERDR_ACCEPTANCE_API=1 HERDR_ACCEPTANCE_LIB="$HARNESS_FILE"
  )
  self_check
}

self_check() {
  local leaked d
  [[ -n $(ls -A "$CHECKOUT" 2>/dev/null) ]] || die "self-check: checkout copy is empty" 4
  [[ -z $(ls -A "$SANDBOX/home") ]] || die "self-check: sandbox HOME is not empty" 4
  for d in config data state cache; do
    [[ -d "$SANDBOX/xdg/$d" ]] || die "self-check: XDG $d dir missing" 4
  done
  # The env -i base must not leak host session variables into the sandbox.
  # SANDBOX_ENV_BASE is the single source of truth for the scenario contract;
  # SCEN_DIR is appended per invocation.
  leaked=$(env -i "${SANDBOX_ENV_BASE[@]}" SCEN_DIR="$EVIDENCE/selfcheck" \
    bash -c 'compgen -e' 2>/dev/null \
    | grep -E '^(USER|SHELL|DISPLAY|DBUS_SESSION_BUS_ADDRESS|SSH_AUTH_SOCK|XAUTHORITY|HISTFILE|OLDPWD)$' || true)
  [[ -z $leaked ]] || die "self-check: host environment leaked into sandbox base: $leaked" 4
  env -i PATH="$SANDBOX_PATH" bash -c 'command -v bash >/dev/null && command -v git >/dev/null' \
    || die "self-check: bash/git not reachable on the sandbox PATH" 4
  [[ -x "$SANDBOX/bin/curl" && -x "$SANDBOX/bin/git" && -f "$SANDBOX/allowed-origins.txt" ]] \
    || die "self-check: origin policy shim or allowlist not installed on the sandbox PATH" 4
}

# ---------------------------------------------------------------------------
# Scenario execution. Sets RC_STATE RC_REASON RC_PASS RC_FAIL RC_STUB RC_DUR.
# ---------------------------------------------------------------------------
run_scenario() { # run_scenario <id> <script-path>
  local id=$1 sf=$2 sd rc start
  sd="$EVIDENCE/$1"; mkdir -p "$sd"
  start=$(date +%s)
  env -i "${SANDBOX_ENV_BASE[@]}" SCEN_DIR="$sd" \
    bash -c 'source "$HERDR_ACCEPTANCE_LIB"; source "$1"' _ "$sf" \
    > "$sd/stdout.log" 2>&1
  rc=$?
  RC_DUR=$(( $(date +%s) - start ))
  if (( rc != 0 )); then
    echo "  FAIL scenario script exited with rc=$rc" >> "$sd/assert.log"
    echo "  FAIL $id: scenario script exited with rc=$rc"
  fi
  if [[ -f $sd/blocked.reason ]]; then
    RC_STATE=BLOCKED; RC_REASON=$(<"$sd/blocked.reason")
    RC_PASS=0; RC_FAIL=0; RC_STUB=0
  else
    local total stub
    total=$(count_lines "$sd/assert.log" '^  ok')
    stub=$(count_lines "$sd/assert.log" '^  ok   \[stubbed\]')
    RC_STUB=$stub; RC_PASS=$((total - stub))
    RC_FAIL=$(count_lines "$sd/assert.log" '^  FAIL')
    if (( RC_FAIL > 0 )); then RC_STATE=FAIL; else RC_STATE=PASS; fi
    RC_REASON=""
  fi
}

scenario_json() { # one-line journal object for a scenario
  local id=$1 level=$2 ms=$3 state=$4 reason=$5 pass=$6 fail=$7 stub=$8 dur=$9 r st=false
  if [[ -n $reason ]]; then r="\"$(json_escape "$reason")\""; else r=null; fi
  (( stub > 0 )) && st=true
  printf '{"id": "%s", "state": "%s", "level": "%s", "activates_at_milestone": %s, "assertions": {"pass": %s, "fail": %s, "stubbed": %s}, "stubbed": %s, "duration_s": %s, "reason": %s}' \
    "$(json_escape "$id")" "$state" "$(json_escape "$level")" "$ms" \
    "$pass" "$fail" "$stub" "$st" "$dur" "$r"
}

baseline_json() {
  if [[ -n ${GUARD_BASE:-} ]]; then printf '"%s"' "$(json_escape "$GUARD_BASE")"; else printf 'null'; fi
}

write_journal() { # $1 = closed (true|false)
  local ts k n=${#JROWS[@]}
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  {
    printf '{\n'
    printf '  "run": { "timestamp": "%s", "commit": "%s", "milestone": %s, "closed": %s, "drill": %s, "exit_code": %s, "activated": %s, "sandbox": "%s", "sandbox_kept": %s, "baseline": %s, "source": "%s" },\n' \
      "$ts" "$SHA" "$MILESTONE" "$1" "$DRILL_R" "$EXIT" "$NACT" \
      "$(json_escape "$SANDBOX")" "$KEEP_R" "$(baseline_json)" "$(json_escape "$SRC")"
    printf '  "tools": { "allowed": %s, "available": %s, "missing": %s, "path": "%s" },\n' \
      "$(json_str_list "${ALLOWED_TOOLS[@]}")" \
      "$(json_str_list "${TOOLS_AVAILABLE[@]}")" \
      "$(json_str_list "${TOOLS_MISSING[@]}")" \
      "$(json_escape "$SANDBOX_PATH")"
    printf '  "scenarios": [\n'
    for (( k = 0; k < n; k++ )); do
      if (( k == n - 1 )); then printf '    %s\n' "${JROWS[k]}"
      else printf '    %s,\n' "${JROWS[k]}"; fi
    done
    printf '  ]\n}\n'
  } > "$EVIDENCE/journal.json"
}

exit_meaning() {
  case $1 in
    0) echo "every activated scenario PASS; no regression against the journal" ;;
    1) echo "at least one activated scenario FAIL" ;;
    2) echo "at least one activated scenario BLOCKED (missing prerequisite or authorization)" ;;
    3) echo "regression: a previously-PASS scenario is no longer PASS" ;;
  esac
}

write_summary() {
  local i id state reason asserts dur msrc="none"
  [[ -n ${GUARD_BASE:-} ]] && msrc="$GUARD_BASE"
  {
    echo "# Clean-install acceptance run (AT-11)"
    echo
    echo "- Commit: \`$SHA\`"
    echo "- Timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    if (( DEFAULTED )); then
      echo "- Milestone: $MILESTONE (journal-based default; baseline: $msrc)"
    else
      echo "- Milestone: $MILESTONE (explicit)"
    fi
    echo "- Source under test: \`$SRC\` (sandbox checkout: \`$CHECKOUT\`)"
    echo "- Sandbox: \`$SANDBOX\` ($([[ $KEEP_R == true ]] && echo kept || echo removed on exit))"
    [[ $DRILL_R == true ]] && echo "- DRILL RUN — synthetic evidence; never counts as milestone closure"
    echo
    echo "| # | Scenario | Level | State | Assertions | Duration |"
    echo "|---|----------|-------|-------|------------:|---------:|"
    for i in "${!IDS[@]}"; do
      id=${IDS[i]}; state=${STATES[$id]}; reason=${REASONS[i]}
      asserts="-"; dur="-"
      if [[ $state != NOT-YET-ACTIVATED ]]; then
        asserts="${RP[i]} ok / ${RF[i]} fail / ${RS[i]} stubbed"; dur="${RD[i]}s"
      fi
      echo "| $((i + 1)) | \`$id\` | ${LEVELS[i]} | $state${reason:+ ($reason)} | $asserts | $dur |"
    done
    echo
    echo "## Result"
    echo
    echo "$NPASS PASS · $NFAIL FAIL · $NBLOCK BLOCKED · $NNYA NOT-YET-ACTIVATED (activated: $NACT)"
    echo
    echo "Exit code $EXIT — $(exit_meaning "$EXIT")"
  } > "$EVIDENCE/summary.md"
  cat "$EVIDENCE/summary.md"
}

# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------
usage() {
  cat <<'EOF'
Usage: scripts/acceptance/clean-install.sh [OPTIONS]
  --milestone N   --record  --record-dir DIR  --baseline FILE  --keep
  --src PATH  --drill {fail|blocked|regress|origin}  --list  -h | --help
Exit codes: 0 all-activated PASS · 1 FAIL · 2 BLOCKED · 3 regression ·
4 harness self-check failed · 64 usage error. See the file header for details.
EOF
}

print_registry() {
  local i
  echo "Scenario registry ($SCENARIOS_DIR/registry.conf):"
  for i in "${!IDS[@]}"; do
    printf '  %2d  %-24s M%s  %-8s  %s\n' \
      $((i + 1)) "${IDS[i]}" "${MS[i]}" "${LEVELS[i]}" "${DESCS[i]}"
  done
}

read_registry() {
  local f="$SCENARIOS_DIR/registry.conf" id ms file level desc
  [[ -f $f ]] || die "self-check: scenario registry missing: $f" 4
  while IFS=$'\t' read -r id ms file level desc; do
    [[ ${id:-} == \#* || -z ${id:-} ]] && continue
    IDS+=("$id"); MS+=("$ms"); FILES+=("$file")
    LEVELS+=("$level"); DESCS+=("$desc")
  done < "$f"
}

main() {
  local milestone=0 milestone_given=0 record=0 keep=0 list=0 drill="" src="" record_dir="" baseline=""
  while [[ $# -gt 0 ]]; do
    case $1 in
      --milestone)  [[ $# -ge 2 ]] || die "missing value for $1" 64; milestone=$2; milestone_given=1; shift 2 ;;
      --record)     record=1; shift ;;
      --keep)       keep=1; shift ;;
      --list)       list=1; shift ;;
      --src)        [[ $# -ge 2 ]] || die "missing value for $1" 64; src=$2; shift 2 ;;
      --record-dir) [[ $# -ge 2 ]] || die "missing value for $1" 64; record_dir=$2; shift 2 ;;
      --baseline)   [[ $# -ge 2 ]] || die "missing value for $1" 64; baseline=$2; shift 2 ;;
      --drill)      [[ $# -ge 2 ]] || die "missing value for $1" 64; drill=$2; shift 2 ;;
      -h|--help)    usage; exit 0 ;;
      *)            usage >&2; die "unknown option: $1" 64 ;;
    esac
  done
  if (( milestone_given )); then
    [[ $milestone =~ ^[0-9]+$ ]] && (( milestone >= 1 )) || die "--milestone must be a positive integer" 64
  fi
  [[ $drill =~ ^(fail|blocked|regress|origin)?$ ]] || die "--drill must be fail|blocked|regress|origin" 64

  HARNESS_FILE=$(readlink -f "${BASH_SOURCE[0]}")
  HARNESS_DIR=$(dirname "$HARNESS_FILE")
  SCENARIOS_DIR="$HARNESS_DIR/scenarios"
  ALLOWED_TOOLS=(bash git jq curl python3 uv)
  TOOLS_AVAILABLE=(); TOOLS_MISSING=()
  IDS=(); MS=(); FILES=(); LEVELS=(); DESCS=()

  if [[ -n $src ]]; then
    SRC=$src
  else
    SRC=$(git -C "$HARNESS_DIR" rev-parse --show-toplevel 2>/dev/null) \
      || die "self-check: cannot resolve the repository under test" 4
  fi
  SHA=$(git -C "$SRC" rev-parse --short HEAD 2>/dev/null) \
    || die "self-check: cannot resolve HEAD of '$SRC'" 4
  RECORD_DIR=${record_dir:-"$SRC/metrics/acceptance"}

  read_registry
  if (( list )); then print_registry; exit 0; fi

  KEEP=$keep
  [[ $keep == 1 ]] && KEEP_R=true || KEEP_R=false
  DRILL_R=false; [[ -n $drill ]] && DRILL_R=true
  build_sandbox

  # Milestone resolution: explicit wins; default comes from the baseline
  # journal (highest milestone recorded as closed), else 1.
  GUARD_BASE=""
  [[ -n $baseline ]] && GUARD_BASE=$baseline
  if (( milestone == 0 )); then
    [[ -z $GUARD_BASE ]] && GUARD_BASE=$(latest_baseline "$RECORD_DIR")
    MILESTONE=$(default_milestone "$GUARD_BASE")
    DEFAULTED=1
  else
    MILESTONE=$milestone; DEFAULTED=0
    if [[ -z $GUARD_BASE ]]; then GUARD_BASE=$(latest_baseline "$RECORD_DIR"); fi
  fi
  echo "AT-11 clean-install harness — commit $SHA, milestone $MILESTONE"
  echo "sandbox: $SANDBOX (env -i base, allowlisted PATH)"
  echo "network policy: documented-origin shim active on curl/git — a tool-call policy boundary, NOT a network namespace"

  # Self-drills: synthetic scenarios proving the exit-code contract.
  if [[ -n $drill ]]; then
    local df="$SANDBOX/drill-$drill.sh"
    case $drill in
      fail)    printf 'bad "forced failure drill (exit 1 semantics proof)"\n' > "$df" ;;
      blocked) printf 'block "drill: forced missing prerequisite (exit 2 semantics proof)"\n' > "$df" ;;
      regress) printf 'bad "regression drill: PASS in baseline, FAIL now (exit 3 semantics proof)"\n' > "$df" ;;
      origin)
        # Positive drill: proves the documented-origin policy shim refuses a
        # non-allowlisted host with BLOCKED-ORIGIN (no network is contacted —
        # the shim rejects before any transfer). Slice 2 task 1.2 verify.
        {
          echo 'out=$(curl -fsSL --max-time 10 https://origin-policy-drill.invalid/install.sh 2>&1); rc=$?'
          echo 'if (( rc != 0 )) && [[ $out == *"BLOCKED-ORIGIN:"*"origin-policy-drill.invalid"* ]]; then'
          echo '  ok "origin drill: non-allowlisted host refused with BLOCKED-ORIGIN (no transfer)"'
          echo 'else'
          echo '  bad "origin drill: shim failed to block origin-policy-drill.invalid (rc=$rc out=$out)"'
          echo 'fi'
        } > "$df"
        ;;
    esac
    IDS+=("_drill-$drill"); MS+=(1); FILES+=("$df")
    LEVELS+=("V2"); DESCS+=("self-drill: $drill exit-code semantics")
    if [[ $drill == regress ]]; then
      GUARD_BASE="$SANDBOX/drill-baseline.json"
      {
        echo '{'
        echo '  "run": { "milestone": 1, "closed": true, "drill": true },'
        echo '  "scenarios": ['
        echo '    {"id": "_drill-regress", "state": "PASS", "activates_at_milestone": 1}'
        echo '  ]'
        echo '}'
      } > "$GUARD_BASE"
    fi
  fi

  # Run every registry scenario through the four-state activation model.
  declare -A STATES=()
  local -a JROWS=() RP=() RF=() RS=() RD=() REASONS=()
  local i id ms sf state reason
  NPASS=0; NFAIL=0; NBLOCK=0; NNYA=0; NACT=0
  for i in "${!IDS[@]}"; do
    id=${IDS[i]}; ms=${MS[i]}
    if [[ ${FILES[i]} == /* ]]; then sf=${FILES[i]}; else sf="$SCENARIOS_DIR/${FILES[i]}"; fi
    reason=""
    if [[ ! -f $sf ]]; then
      state=NOT-YET-ACTIVATED; reason="not authored"
    elif (( ms > MILESTONE )); then
      state=NOT-YET-ACTIVATED; reason="activates at milestone $ms"
    else
      (( NACT++ ))
      run_scenario "$id" "$sf"
      state=$RC_STATE; reason=$RC_REASON
    fi
    STATES[$id]=$state
    case $state in
      PASS)             (( NPASS++ )); echo "  PASS               $id" ;;
      FAIL)             (( NFAIL++ )); echo "  FAIL               $id" ;;
      BLOCKED)          (( NBLOCK++ )); echo "  BLOCKED            $id — $reason" ;;
      NOT-YET-ACTIVATED) (( NNYA++ )); echo "  NOT-YET-ACTIVATED  $id — $reason" ;;
    esac
    if [[ $state == NOT-YET-ACTIVATED ]]; then
      RP+=(0); RF+=(0); RS+=(0); RD+=(0); REASONS+=("$reason")
    else
      RP+=("$RC_PASS"); RF+=("$RC_FAIL"); RS+=("$RC_STUB"); RD+=("$RC_DUR"); REASONS+=("$reason")
    fi
    JROWS+=("$(scenario_json "$id" "${LEVELS[i]}" "$ms" "$state" "$reason" \
      "${RP[i]}" "${RF[i]}" "${RS[i]}" "${RD[i]}")")
  done

  # Regression guard against the journal: any baseline-PASS scenario that is
  # no longer PASS fails the run at any milestone (exit 3).
  local regress=0
  if [[ -n $GUARD_BASE && -f $GUARD_BASE ]] && ! regression_guard "$GUARD_BASE"; then
    regress=1
  fi

  if (( regress )); then EXIT=3
  elif (( NFAIL > 0 )); then EXIT=1
  elif (( NBLOCK > 0 )); then EXIT=2
  else EXIT=0; fi
  local closed=false
  (( EXIT == 0 && NACT > 0 )) && [[ $DRILL_R == false ]] && closed=true

  write_journal "$closed"
  write_summary
  if (( record )); then
    mkdir -p "$RECORD_DIR"
    cp "$EVIDENCE/journal.json" "$RECORD_DIR/clean-install-$SHA.json"
    cp "$EVIDENCE/summary.md" "$RECORD_DIR/clean-install-$SHA.summary.md"
    echo "recorded: $RECORD_DIR/clean-install-$SHA.json"
  fi
  exit "$EXIT"
}

# Scenario API guard: when HERDR_ACCEPTANCE_API=1 this file is sourced inside
# the sandbox purely for its assertion vocabulary; main() must not run there.
if [[ "${HERDR_ACCEPTANCE_API:-}" != "1" ]]; then
  main "$@"
fi
