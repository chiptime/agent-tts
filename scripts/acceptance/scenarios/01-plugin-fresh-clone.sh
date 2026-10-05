# Scenario 1: plugin-fresh-clone (AT-11 slice 8, task 1.8; level V2, M1).
#
# PRD scenario "Instalación del plugin desde clon fresco" — the documented
# non-registry fresh-clone route: the id=install-plugin-curl block executes
# LITERALLY from hosts/herdr/tts-plugin/README.md (curl | sh installer).
#
# Design Decision 11: the block is ref-parameterized with a single
# ${HERDR_TTS_REF:-v0.16.0} expansion, and this scenario selects the
# AUTHORIZED candidate branch by exporting HERDR_TTS_REF before executing
# the literal block — the documented command itself is never rewritten.
# The export reaches BOTH halves: the raw installer-script URL expanded by
# the block and the clone install.sh performs through its own knob.
#
# The scenario also carries the OQ-6 evidence legs (design Decision 9,
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
# Candidate ≠ stable (Decision 11, spec supplementary validation-ref
# policy): this run validates ONLY the authorized candidate ref and its
# resolved commit (recorded in assert.log and candidate.json). It never
# claims the unpublished stable tag v0.16.0 or public main is fixed,
# published, or validated; their preserved BLOCKED evidence from the
# prior task-1.8 run stays recorded. The engine pin is never substituted,
# aliased, or fallen back to from the candidate ref.
#
# Truthfulness rules (tasks.md 1.8): PASS requires the literal documented
# block to complete in the clean sandbox at the candidate ref. A candidate
# revision that is unresolvable or unavailable at the origin makes the
# scenario BLOCKED with that recorded reason while the remaining legs still
# run and record evidence. Never a substitute SHA, a moving branch as an
# engine pin, a cache-only pass, or a silent fallback. An unexplained route
# failure is a FAIL, not a BLOCKED.
#
# Pure bash on purpose: the sandbox PATH allowlist has no grep/sed/date.

PIN=d66616bce3ad8193f11ae615bd58bb4508eb65be
ORIGIN_URL=https://github.com/chiptime/agent-tts.git
CANDIDATE_REF=validation/at-11-instalable
STABLE_URL=https://raw.githubusercontent.com/chiptime/agent-tts/v0.16.0/hosts/herdr/tts-plugin/scripts/install.sh
REFEXPAND='${HERDR_TTS_REF:-v0.16.0}'

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

# --- Decision-11 static gate (RED tests a+b), BEFORE any ref is selected:
# the documented curl block must be ref-parameterized with a byte-stable
# stable default. These are assertions about the repo under test; failing
# them is a real FAIL, never a BLOCKED.
doc_body=""
doc_body=$(doc_block hosts/herdr/tts-plugin/README.md install-plugin-curl 2>>"$SCEN_DIR/stdout.log") || true
if [[ -z $doc_body ]]; then
  fail "doc-block: id=install-plugin-curl could not be extracted (see stdout.log)"
elif [[ ${HERDR_TTS_REF+x} == x ]]; then
  fail "sandbox contract: HERDR_TTS_REF must be unset at scenario start (test b premise)"
else
  n=0; rest=$doc_body
  while [[ $rest == *"$REFEXPAND"* ]]; do rest=${rest#*"$REFEXPAND"}; n=$((n + 1)); done
  if (( n == 1 )); then
    ok "ref-parameterization: the curl block carries exactly one $REFEXPAND expansion (test a)"
  else
    fail "ref-parameterization: expected exactly one ref expansion in the curl block, found $n (test a)"
  fi
  if [[ ${doc_body//"$REFEXPAND"/} == *v0.16.0* ]]; then
    fail "ref-parameterization: a hardcoded v0.16.0 survives outside the expansion (test a)"
  else
    ok "ref-parameterization: no hardcoded stable ref in the block URL outside the expansion (test a)"
  fi
  if [[ ${doc_body//"$REFEXPAND"/v0.16.0} == *"$STABLE_URL"* ]]; then
    ok "stable default: with HERDR_TTS_REF unset the block resolves the documented stable URL unchanged (test b)"
  else
    fail "stable default: the unset expansion does not resolve the documented stable URL (test b)"
  fi
fi

# --- Candidate attribution (Decision 11 test d): resolve the authorized
# candidate branch to its exact commit from the documented origin BEFORE
# running, so every later assertion attributes evidence to this commit and
# never to a later branch tip. An unresolvable candidate ref is BLOCKED.
cand_sha=""
printf '%s\n' "git ls-remote $ORIGIN_URL refs/heads/$CANDIDATE_REF" >> "$SCEN_DIR/cmd.log"
ls_out=$(git ls-remote "$ORIGIN_URL" "refs/heads/$CANDIDATE_REF" 2>>"$SCEN_DIR/stdout.log")
if [[ $ls_out =~ ^([0-9a-f]{40})[[:space:]]+refs/heads/ ]]; then
  cand_sha=${BASH_REMATCH[1]}
  ok "candidate: authorized ref $CANDIDATE_REF resolves to commit $cand_sha at the documented origin (test d)"
else
  if [[ $(<"$SCEN_DIR/stdout.log") == *"BLOCKED-ORIGIN"* ]]; then
    creason="origin policy refused resolving the candidate ref $CANDIDATE_REF (BLOCKED-ORIGIN; see stdout.log)"
  else
    creason="authorized candidate ref $CANDIDATE_REF does not resolve to a commit at the documented origin (see stdout.log)"
  fi
  # BLOCKED only when every executed assertion is green; a real failure
  # recorded above stays a FAIL and is never downgraded to BLOCKED.
  if (( nbad == 0 )); then
    block "$creason — no candidate run, no substitute ref, M1 stays open"
  fi
  fail "candidate: $creason — no candidate run, no substitute ref"
  exit 0
fi
printf '{"candidate_ref": "%s", "resolved_commit": "%s", "engine_pin": "%s", "origin": "%s"}\n' \
  "$CANDIDATE_REF" "$cand_sha" "$PIN" "$ORIGIN_URL" > "$SCEN_DIR/candidate.json"
ok "attribution: candidate.json records candidate_ref=$CANDIDATE_REF resolved_commit=$cand_sha engine_pin=$PIN (test d)"

# --- Leg A: the literal documented fresh-clone route (id=install-plugin-curl),
# executed at the candidate ref through the block's own parameterization.
# Note: the block is a `curl | bash` pipeline, so a failed fetch can exit 0
# (empty stdin into bash); the outcome is classified from the recorded
# output, never from the pipeline status alone.
export HERDR_TTS_REF="$CANDIDATE_REF"
rc_a=0
run_doc_block hosts/herdr/tts-plugin/README.md install-plugin-curl || rc_a=$?
log_a=$(<"$SCEN_DIR/stdout.log")
if [[ $log_a == *"BLOCKED-ORIGIN"* ]]; then
  ROUTE_BLOCKED_REASON="origin policy refused the documented route (BLOCKED-ORIGIN in the route leg; see stdout.log)"
elif [[ $log_a == *"returned error: 404"* || $log_a == *"404: Not Found"* ]]; then
  ROUTE_BLOCKED_REASON="documented installer artifact unavailable at the origin for ref $CANDIDATE_REF (HTTP 404 on the raw installer URL; see stdout.log)"
elif (( rc_a != 0 )); then
  if [[ -z $HERDR_BIN && $log_a == *herdr* ]]; then
    ROUTE_BLOCKED_REASON="host prerequisite unavailable: no real herdr binary found on the sandbox tool PATH (the documented installer preflights herdr)"
  else
    fail "route: the documented curl|sh installer failed (rc=$rc_a; see stdout.log)"
  fi
else
  tgt="$XDG_DATA_HOME/herdr-tts/plugin"
  if [[ -f "$tgt/hosts/herdr/tts-plugin/herdr-plugin.toml" ]]; then
    ok "route: documented curl|sh installer completed at the candidate ref; plugin materialized at ${tgt#"$HOME"/}/hosts/herdr/tts-plugin"
    if [[ $log_a == *"cloning the agent-tts monorepo ($CANDIDATE_REF)"* ]]; then
      ok "route: installer reports cloning the monorepo at $CANDIDATE_REF (override reached the install clone)"
    else
      fail "route: installer output does not report the candidate ref for the clone (see stdout.log)"
    fi
    head_a=$(git -C "$tgt" rev-parse HEAD 2>>"$SCEN_DIR/stdout.log")
    if [[ $? -eq 0 && $head_a == "$cand_sha" ]]; then
      ok "route: installed checkout HEAD == resolved candidate commit $cand_sha (override selected the installed plugin ref; test c)"
    else
      fail "route: installed checkout HEAD (${head_a:-none}) != resolved candidate commit $cand_sha (override did not select the clone)"
    fi
    # Test c, fetched-script half: the raw installer URL at the candidate ref
    # must serve byte-identical bytes to the candidate tree's install.sh —
    # proving the override selected the script the block fetched and ran.
    if step curl -fsSL \
        "https://raw.githubusercontent.com/chiptime/agent-tts/${HERDR_TTS_REF}/hosts/herdr/tts-plugin/scripts/install.sh" \
        -o "$SCEN_DIR/installer-at-candidate.sh"; then
      want_blob=$(git -C "$tgt" rev-parse "HEAD:hosts/herdr/tts-plugin/scripts/install.sh" 2>>"$SCEN_DIR/stdout.log")
      got_blob=$(git hash-object "$SCEN_DIR/installer-at-candidate.sh" 2>>"$SCEN_DIR/stdout.log")
      if [[ -n $want_blob && $want_blob == "$got_blob" ]]; then
        ok "route: installer script served at $CANDIDATE_REF is byte-identical to the candidate tree (override selected the fetched script; test c)"
      else
        fail "route: installer script at $CANDIDATE_REF (blob ${got_blob:-none}) != candidate tree install.sh (blob ${want_blob:-none})"
      fi
    else
      if [[ $(<"$SCEN_DIR/stdout.log") == *"BLOCKED-ORIGIN"* ]]; then
        ROUTE_BLOCKED_REASON="origin policy refused fetching the installer script at the candidate ref (BLOCKED-ORIGIN; see stdout.log)"
      else
        fail "route: cannot fetch the installer script at the candidate ref for the byte-identity probe (see stdout.log)"
      fi
    fi
  else
    fail "route: documented installer exited 0 but hosts/herdr/tts-plugin is missing under $tgt"
  fi
fi

# --- Leg B: OQ-6 exact-pin public retrieval (by-SHA fetch of fresh objects).
# Independent of the candidate ref by design (Decision 11): the candidate
# branch adds no reachability for the engine SHA, so this leg keeps its own
# BLOCKED semantics either way.
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
# is exactly what must be publicly installable, and the candidate ref must
# never leak into the engine resolution (Decision 11 test f). $CHECKOUT
# contains engine/, so bootstrap deliberately ignores it and resolves the
# pinned remote ref — which is the public-retrieval property under test. The
# shared venv location is cleared first: bootstrap's healthy-venv fast path
# must never mask this leg behind state left by an earlier scenario or run
# (self-containment). Skipped when leg B already established the pin is
# unavailable: a bootstrap failure would only be a consequence of that same
# missing prerequisite, and the truthful state is BLOCKED, never a FAIL
# manufactured downstream.
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
      ok "engine install records commit_id == exact pin ($PIN) — the candidate ref never reached the engine resolution (test f)"
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

# --- Decision-11 test e: no evidence line this scenario recorded claims the
# stable route is published, repaired, or validated (self-discipline scan of
# the run's own assert.log; candidate evidence is candidate evidence only).
alog=$(<"$SCEN_DIR/assert.log")
noclaim=1
for _pat in "v0.16.0 is published" "v0.16.0 published" "v0.16.0 repaired" "v0.16.0 validated" \
            "main is published" "main published" "main repaired" "main validated"; do
  if [[ $alog == *"$_pat"* ]]; then
    fail "no-claim: evidence matches forbidden stable-route claim pattern '$_pat' (test e)"
    noclaim=0
  fi
done
if (( noclaim )); then
  ok "no-claim: no evidence line claims stable v0.16.0 or public main is published, repaired, or validated (test e)"
fi

# --- Final state: an unavailable documented prerequisite makes this scenario
# BLOCKED (exit 2) — but only when every executed assertion is green; any
# real failure stays a FAIL and is never downgraded to BLOCKED.
reason=""
[[ -n $OQ6_BLOCKED_REASON ]] && reason="$OQ6_BLOCKED_REASON"
[[ -n $ROUTE_BLOCKED_REASON ]] && reason="${reason:+$reason; }$ROUTE_BLOCKED_REASON"
if [[ -n $reason && $nbad -eq 0 ]]; then
  block "$reason (candidate ref $CANDIDATE_REF @ $cand_sha; OQ-6 legs recorded above; M1 stays open)"
fi
