# Scenario 1: plugin-fresh-clone (AT-11 slice 8, task 1.8; level V2, M1).
#
# PRD scenario "Instalación del plugin desde clon fresco" — the documented
# non-registry fresh-clone route: the id=install-plugin-curl block executes
# LITERALLY from hosts/herdr/tts-plugin/README.md (curl | sh installer).
# The scenario also carries the two OQ-6 evidence legs (design Decision 9,
# task 1.8) that make the exact pin's PUBLIC retrievability mechanical:
#
#   B) exact-pin retrieval — a fresh clone of the documented HTTPS origin
#      fetches d66616bc… BY FULL SHA. The sandbox starts with no git cache
#      (empty HOME/XDG, non-standard checkout path), so those objects can
#      only arrive from the public origin during THIS run; the pinned tree
#      must materialize BOTH hosts/herdr/tts-plugin/ AND engine/.
#   C) the #subdirectory=engine install — the repo-under-test bootstrap
#      (corrected pin, task 1.11) builds a sandbox venv from
#      git+https://github.com/chiptime/agent-tts.git@d66616bc#subdirectory=engine
#      with NO overrides: the DEFAULT pin is what must be installable.
#      direct_url.json must record commit_id == the exact pin.
#
# Truthfulness rules (tasks.md 1.8, design Decision 9): PASS requires the
# literal documented block to complete in the clean sandbox. A documented
# origin/revision that is unavailable (today: tag v0.16.0 is not published —
# raw.githubusercontent.com answers HTTP 404) makes the scenario BLOCKED with
# that recorded reason while the OQ-6 legs still run and record evidence.
# Never a substitute SHA, a moving branch, a cache-only pass, or a silent
# fallback. An unexplained route failure is a FAIL, not a BLOCKED.
#
# Pure bash on purpose: the sandbox PATH allowlist has no grep/sed/date.

PIN=d66616bce3ad8193f11ae615bd58bb4508eb65be
ORIGIN_URL=https://github.com/chiptime/agent-tts.git

nbad=0
fail() { bad "$1"; nbad=$((nbad + 1)); }

# Deterministic non-interactive stdin for every child of this scenario: a
# documented command that can prompt must refuse visibly, never hang.
exec 0</dev/null

# Real-herdr discovery, derived never hardcoded: probe ONLY directories the
# harness itself already allowlisted onto the sandbox PATH (the documented
# installer preflights `herdr`). A missing herdr is a missing prerequisite —
# it is never stubbed, and ok_stubbed is never used in this scenario.
HERDR_BIN=""
IFS=: read -r -a _pdirs <<< "$PATH"
for _d in "${_pdirs[@]}"; do
  [[ -n $_d && -x "$_d/herdr" ]] && { HERDR_BIN="$_d/herdr"; break; }
done

ROUTE_BLOCKED_REASON=""
OQ6_BLOCKED_REASON=""

# --- Leg A: the literal documented fresh-clone route (id=install-plugin-curl).
# Note: the block is a `curl | bash` pipeline, so a failed fetch can exit 0
# (empty stdin into bash); the outcome is classified from the recorded
# output, never from the pipeline status alone.
rc_a=0
run_doc_block hosts/herdr/tts-plugin/README.md install-plugin-curl || rc_a=$?
log_a=$(<"$SCEN_DIR/stdout.log")
if [[ $log_a == *"BLOCKED-ORIGIN"* ]]; then
  ROUTE_BLOCKED_REASON="origin policy refused the documented route (BLOCKED-ORIGIN in the route leg; see stdout.log)"
elif [[ $log_a == *"returned error: 404"* || $log_a == *"404: Not Found"* ]]; then
  ROUTE_BLOCKED_REASON="documented installer artifact unavailable at the origin: the tag-pinned URL answers HTTP 404 (tag v0.16.0 is not published on github.com/chiptime/agent-tts)"
elif (( rc_a != 0 )); then
  if [[ -z $HERDR_BIN && $log_a == *herdr* ]]; then
    ROUTE_BLOCKED_REASON="host prerequisite unavailable: no real herdr binary found on the sandbox tool PATH (the documented installer preflights herdr)"
  else
    fail "route: the documented curl|sh installer failed (rc=$rc_a; see stdout.log)"
  fi
else
  tgt="$XDG_DATA_HOME/herdr-tts/plugin"
  if [[ -f "$tgt/hosts/herdr/tts-plugin/herdr-plugin.toml" ]]; then
    ok "route: documented curl|sh installer completed; plugin materialized at ${tgt#"$HOME"/}/hosts/herdr/tts-plugin"
  else
    fail "route: documented installer exited 0 but hosts/herdr/tts-plugin is missing under $tgt"
  fi
fi

# --- Leg B: OQ-6 exact-pin public retrieval (by-SHA fetch of fresh objects).
pb="$HOME/pin-probe"
rm -rf "$pb"
if step git clone --quiet "$ORIGIN_URL" "$pb"; then
  ok "pin: documented origin cloned into the clean sandbox (no prior object cache)"
  if step git -C "$pb" fetch --quiet origin "$PIN"; then
    ok "pin: exact SHA $PIN fetched by full id from the public origin (git fetch origin <sha>)"
    rev=$(git -C "$pb" rev-parse "${PIN}^{commit}" 2>>"$SCEN_DIR/stdout.log")
    if [[ $? -eq 0 && $rev == "$PIN" ]]; then
      ok "pin: fetched object resolves to exactly $PIN (no prefix, no moving ref)"
    else
      fail "pin: fetched revision does not resolve to the exact pin (got: ${rev:-none})"
    fi
    if git -C "$pb" cat-file -e "$PIN:hosts/herdr/tts-plugin/herdr-plugin.toml" 2>/dev/null; then
      ok "pin: pinned tree materializes hosts/herdr/tts-plugin/ (plugin manifest present at the pin)"
    else
      fail "pin: hosts/herdr/tts-plugin/ absent from the pinned tree"
    fi
    if git -C "$pb" cat-file -e "$PIN:engine/pyproject.toml" 2>/dev/null; then
      ok "pin: pinned tree materializes engine/ (the #subdirectory=engine target exists at the pin)"
    else
      fail "pin: engine/ absent from the pinned tree"
    fi
  else
    log_b=$(<"$SCEN_DIR/stdout.log")
    if [[ $log_b == *"BLOCKED-ORIGIN"* ]]; then
      OQ6_BLOCKED_REASON="origin policy refused the by-SHA fetch of the pinned revision"
    else
      OQ6_BLOCKED_REASON="pinned revision $PIN is not retrievable from the documented origin (by-SHA fetch failed; see stdout.log) — no substitute SHA, no moving branch, no cache-only pass"
    fi
  fi
else
  log_b=$(<"$SCEN_DIR/stdout.log")
  if [[ $log_b == *"BLOCKED-ORIGIN"* ]]; then
    OQ6_BLOCKED_REASON="origin policy refused cloning the documented origin for the pin probe"
  else
    fail "pin: cannot clone the documented origin for the pin probe (see stdout.log)"
  fi
fi

# --- Leg C: OQ-6 install form — engine from the PUBLIC origin through the
# repo-under-test bootstrap. No HERDR_AGENT_TTS_REF override: the DEFAULT pin
# is exactly what must be publicly installable. $CHECKOUT contains engine/,
# so bootstrap deliberately ignores it and resolves the pinned remote ref —
# which is the public-retrieval property under test. The shared venv location
# is cleared first: bootstrap's healthy-venv fast path must never mask this
# leg behind state left by an earlier scenario or run (self-containment).
# Skipped when leg B already established the pin is unavailable: a bootstrap
# failure would only be a consequence of that same missing prerequisite, and
# the truthful state is BLOCKED, never a FAIL manufactured downstream.
rm -rf "${XDG_DATA_HOME:?AT-11 sandbox contract}/herdr-tts"
venv_py="$XDG_DATA_HOME/herdr-tts/venv/bin/python"
if [[ -n $OQ6_BLOCKED_REASON ]]; then
  : # pin unavailable — recorded in blocked.reason; no downstream evidence possible
elif step bash "$CHECKOUT/hosts/herdr/tts-plugin/scripts/bootstrap.sh"; then
  ok "bootstrap: engine installed from the pinned public ref (local engine/ checkout ignored, no overrides)"
else
  fail "bootstrap: pinned engine install from the public origin failed (see stdout.log)"
fi
if [[ -z $OQ6_BLOCKED_REASON ]]; then
  if [[ -x $venv_py ]] && step "$venv_py" -c 'import agent_tts'; then
    ok "bootstrap: sandbox venv imports agent_tts"
  else
    fail "bootstrap: sandbox venv does not import agent_tts"
  fi
  du=""
  for g in "$XDG_DATA_HOME"/herdr-tts/venv/lib/python*/site-packages/agent_tts-*.dist-info/direct_url.json; do
    [[ -f $g ]] && du=$g
  done
  if [[ -n $du ]]; then
    cid=$(jq -r '.vcs_info.commit_id // "none"' "$du")
    sub=$(jq -r '.subdirectory // "none"' "$du")
    purl=$(jq -r '.url // "none"' "$du")
    if [[ $cid == "$PIN" ]]; then
      ok "engine install records commit_id == exact pin ($PIN)"
    else
      fail "engine install commit_id mismatch: $cid (expected $PIN)"
    fi
    if [[ $sub == engine ]]; then
      ok "engine install records subdirectory == engine"
    else
      fail "engine install subdirectory mismatch: $sub"
    fi
    if [[ $purl == "$ORIGIN_URL" ]]; then
      ok "engine install records the documented origin url"
    else
      fail "engine install url mismatch: $purl"
    fi
  else
    fail "engine install left no direct_url.json (installed commit cannot be proven)"
  fi
fi

# --- Final state: an unavailable documented prerequisite makes this scenario
# BLOCKED (exit 2) — but only when every executed assertion is green; any
# real failure stays a FAIL and is never downgraded to BLOCKED.
reason=""
[[ -n $OQ6_BLOCKED_REASON ]] && reason="$OQ6_BLOCKED_REASON"
[[ -n $ROUTE_BLOCKED_REASON ]] && reason="${reason:+$reason; }$ROUTE_BLOCKED_REASON"
if [[ -n $reason && $nbad -eq 0 ]]; then
  block "$reason (OQ-6 legs recorded above; M1 stays open)"
fi
