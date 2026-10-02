# Scenario 2: plugin-subdir-install (AT-11 slice 8, task 1.8; level V2, M1).
#
# The OQ-1 probe (design Decision 1): does the documented registry route
#   herdr plugin install chiptime/agent-tts/hosts/herdr/tts-plugin
# materialize the FULL monorepo at managed_path with
#   plugin_root = managed_path/<subdir>?
# (the wizard-reachability premise: from <plugin_root>/bin the shared
# tools/ package stays reachable by ascending the managed checkout).
#
# Design Decision 11: the EVIDENCE leg is herdr's own supported candidate
# selection — `herdr plugin install … --ref validation/at-11-instalable
# --yes` — the authorized pre-V3 candidate branch carrying the corrected
# installer/pin, never the unpublished stable tag and never public main.
# Legs, all recorded honestly in cmd.log/stdout.log:
#   1a) the literal id=install-plugin-github block, executed VERBATIM. On
#       non-interactive stdin herdr refuses with rc=2 and asks for --yes —
#       herdr's own documented unattended flag. The refusal is asserted as
#       observed behavior, never silently worked around.
#   1b) the CANDIDATE leg: the same documented command + herdr's documented
#       --yes and --ref validation/at-11-instalable. This is the evidence
#       leg; the registration it materializes carries the OQ-1 assertions
#       and its resolved_commit must equal the candidate branch commit.
#   2)  DIAGNOSTIC FALLBACK ONLY (demoted by Decision 11): the documented
#       command with the product's HERDR_AGENT_TTS_REF test override at the
#       default revision. It runs solely to diagnose a failed candidate leg
#       (publication/prerequisite gap vs real defect); its evidence is
#       recorded as diagnostics and is never presented as the candidate
#       route or a PASS.
#
# Candidate ≠ stable: this run validates ONLY the authorized candidate ref
# and its resolved commit (recorded in assert.log and candidate.json). It
# never claims the unpublished stable tag v0.16.0 or public main is fixed,
# published, or validated; the preserved BLOCKED evidence from the prior
# task-1.8 run stays recorded. The candidate ref never substitutes for the
# immutable engine pin (asserted on direct_url.json below).
#
# Origin policy note: herdr clones via its own transport, which the PATH
# shim cannot intercept — so the resulting checkout's origin remote is
# asserted equal to the documented HTTPS origin (post-hoc verification).
# The engine install inside the [[build]] does go through the shimmed git.
#
# Pure bash on purpose: the sandbox PATH allowlist has no grep/sed/date.

PIN=d66616bce3ad8193f11ae615bd58bb4508eb65be
ORIGIN_URL=https://github.com/chiptime/agent-tts.git
SPEC=chiptime/agent-tts/hosts/herdr/tts-plugin
CANDIDATE_REF=validation/at-11-instalable

nbad=0
fail() { bad "$1"; nbad=$((nbad + 1)); }

# Deterministic non-interactive stdin: the verbatim command must refuse
# visibly (rc=2, "requires --yes"), never hang on a TTY prompt.
exec 0</dev/null

# Real-herdr discovery, derived never hardcoded: probe ONLY directories the
# harness itself already allowlisted onto the sandbox PATH. The OQ-1 probe
# requires the REAL plugin manager; a stub would prove nothing and is never
# used (no ok_stubbed in this scenario).
HERDR_BIN=""
IFS=: read -r -a _pdirs <<< "$PATH"
for _d in "${_pdirs[@]}"; do
  [[ -n $_d && -x "$_d/herdr" ]] && { HERDR_BIN="$_d/herdr"; break; }
done
if [[ -z $HERDR_BIN ]]; then
  block "host prerequisite unavailable: no real herdr binary found on the sandbox tool PATH — the subdirectory-install probe requires the real plugin manager"
fi
hver=$("$HERDR_BIN" --version 2>>"$SCEN_DIR/stdout.log" || echo "unknown")
ok "prerequisite: real herdr resolved on the sandbox tool PATH ($hver at ${HERDR_BIN%/*}/herdr)"

# --- Candidate attribution (Decision 11 test d): resolve the authorized
# candidate branch to its exact commit from the documented origin BEFORE
# any install, so the registration's resolved_commit is attributed to this
# commit and never to a later branch tip. Unresolvable ⇒ BLOCKED.
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
printf '{"candidate_ref": "%s", "resolved_commit": "%s", "engine_pin": "%s", "origin": "%s", "spec": "%s"}\n' \
  "$CANDIDATE_REF" "$cand_sha" "$PIN" "$ORIGIN_URL" "$SPEC" > "$SCEN_DIR/candidate.json"
ok "attribution: candidate.json records candidate_ref=$CANDIDATE_REF resolved_commit=$cand_sha engine_pin=$PIN (test d)"

# Inspect whatever registration exists and assert the OQ-1 predicate on the
# registry's own fields. Every lookup is return-code or shape checked.
# Arg 1: "candidate" — additionally require resolved_commit == the resolved
# candidate commit (attribution); "diagnostic" — record fields as-is, the
# registration came from the demoted fallback leg and is diagnostics only.
inspect_registration() {
  local mode=$1
  printf '%s\n' "herdr plugin list --json > plugin-list.json" >> "$SCEN_DIR/cmd.log"
  if ! "$HERDR_BIN" plugin list --json > "$SCEN_DIR/plugin-list.json" 2>>"$SCEN_DIR/stdout.log"; then
    fail "registry: herdr plugin list --json failed (see stdout.log)"
    return 1
  fi
  pro=$(jq -r '.result.plugins[]? | select(.plugin_id=="herdr.tts") | .plugin_root' "$SCEN_DIR/plugin-list.json")
  mpath=$(jq -r '.result.plugins[]? | select(.plugin_id=="herdr.tts") | .source.managed_path' "$SCEN_DIR/plugin-list.json")
  sdir=$(jq -r '.result.plugins[]? | select(.plugin_id=="herdr.tts") | .source.subdir' "$SCEN_DIR/plugin-list.json")
  skind=$(jq -r '.result.plugins[]? | select(.plugin_id=="herdr.tts") | .source.kind' "$SCEN_DIR/plugin-list.json")
  scommit=$(jq -r '.result.plugins[]? | select(.plugin_id=="herdr.tts") | .source.resolved_commit' "$SCEN_DIR/plugin-list.json")
  if [[ -z $pro || $pro == null ]]; then
    fail "registry: herdr.tts is not registered after the documented install command"
    return 1
  fi
  if [[ -z $mpath || $mpath == null || -z $sdir || $sdir == null ]]; then
    fail "registry: herdr.tts entry lacks managed_path/subdir fields (schema mismatch: managed_path=${mpath:-none} subdir=${sdir:-none})"
    return 1
  fi
  if [[ $mode == candidate ]]; then
    ok "registry: herdr.tts registered from the candidate ref (source.kind=$skind resolved_commit=$scommit)"
  else
    ok "diagnostic registration: herdr.tts registered (source.kind=$skind resolved_commit=$scommit) — fallback-leg evidence, not the candidate route"
  fi
  if [[ "$pro" == "$mpath/$sdir" ]]; then
    ok "OQ-1: plugin_root == managed_path/<subdir> ($pro)"
  else
    fail "OQ-1 FALSIFIED: plugin_root ($pro) != managed_path/subdir ($mpath/$sdir) — the pre-designed vendoring fallback (design Decision 1) applies"
  fi
  if [[ $mode == candidate ]]; then
    if [[ $scommit == "$cand_sha" ]]; then
      ok "attribution: registration resolved_commit == candidate branch commit $cand_sha (test d)"
    else
      fail "attribution: registration resolved_commit ($scommit) != candidate branch commit $cand_sha (test d)"
    fi
  fi
  if [[ -f "$mpath/hosts/herdr/tts-plugin/herdr-plugin.toml" ]]; then
    ok "OQ-1: managed checkout materializes the plugin at hosts/herdr/tts-plugin"
  else
    fail "OQ-1: managed checkout does not contain hosts/herdr/tts-plugin"
  fi
  if [[ -d "$mpath/engine" ]]; then
    ok "OQ-1: managed checkout materializes engine/ (full monorepo — wizard-reachability premise of design Decision 1)"
  else
    fail "OQ-1: managed checkout has no engine/ — not a full-monorepo materialization"
  fi
  top=$(git -C "$mpath" rev-parse --show-toplevel 2>>"$SCEN_DIR/stdout.log")
  if [[ $? -eq 0 && "$top" == "$mpath" ]]; then
    ok "OQ-1: managed_path is itself a git checkout root"
  else
    fail "OQ-1: managed_path is not a git checkout root (got: ${top:-none})"
  fi
  ourl=$(git -C "$mpath" remote get-url origin 2>>"$SCEN_DIR/stdout.log")
  if [[ $? -eq 0 && $ourl == "$ORIGIN_URL" ]]; then
    ok "origin: herdr's internal clone origin == documented HTTPS origin (post-hoc policy verification)"
  else
    fail "origin: managed checkout origin is not the documented origin (got: ${ourl:-none})"
  fi
  venv_py="$XDG_DATA_HOME/herdr-tts/venv/bin/python"
  if [[ -x $venv_py ]] && "$venv_py" -c 'import agent_tts' >>"$SCEN_DIR/stdout.log" 2>&1; then
    ok "OQ-1: [[build]] venv inside the sandbox imports agent_tts (real install, not a stub)"
  else
    fail "OQ-1: the [[build]] venv does not import agent_tts"
  fi
  # Decision 11 test f: whichever installer/plugin ref was selected, the
  # engine resolution must still record the exact pin + subdirectory — a
  # branch/tag value here would mean the candidate leaked into the engine.
  du=""
  for g in "$XDG_DATA_HOME"/herdr-tts/venv/lib/python*/site-packages/agent_tts-*.dist-info/direct_url.json; do
    [[ -f $g ]] && du=$g
  done
  if [[ -n $du ]]; then
    cid=$(jq -r '.vcs_info.commit_id // "none"' "$du")
    sub=$(jq -r '.subdirectory // "none"' "$du")
    if [[ $cid == "$PIN" ]]; then
      ok "engine install records commit_id == exact pin ($PIN) — the selected ref never reached the engine resolution (test f)"
    else
      fail "engine install commit_id mismatch: $cid (expected $PIN) — the selected ref leaked into the engine resolution"
    fi
    if [[ $sub == engine ]]; then
      ok "engine install records subdirectory == engine"
    else
      fail "engine install subdirectory mismatch: $sub"
    fi
  else
    fail "engine install left no direct_url.json (engine provenance cannot be proven)"
  fi
}

ROUTE_BLOCKED_REASON=""

# Self-containment: scenarios share one sandbox (HOME/XDG) per harness run.
# bootstrap.sh's healthy-venv fast path would let an earlier scenario's venv
# mask the candidate revision's REAL [[build]] state — clearing the shared
# data dir makes every install leg below a genuinely fresh build.
rm -rf "${XDG_DATA_HOME:?AT-11 sandbox contract}/herdr-tts"

# --- Leg 1a: the literal documented command, verbatim.
rc_a=0
run_doc_block hosts/herdr/tts-plugin/README.md install-plugin-github || rc_a=$?
log_a=$(<"$SCEN_DIR/stdout.log")
registered_clean=0
if (( rc_a == 0 )); then
  registered_clean=1
  ok "route: the literal documented command completed in the clean environment"
elif [[ $log_a == *"requires --yes when stdin is not interactive"* ]]; then
  ok "route: verbatim command executed and refused non-interactively (herdr documents --yes for unattended runs)"
elif [[ $log_a == *"BLOCKED-ORIGIN"* ]]; then
  ROUTE_BLOCKED_REASON="origin policy refused the documented registry route (BLOCKED-ORIGIN; see stdout.log)"
else
  fail "route: verbatim documented command failed unexpectedly (rc=$rc_a; see stdout.log)"
fi

# --- Leg 1b: the CANDIDATE leg (Decision 11 evidence leg) — herdr's own
# supported ref selection, clean environment:
#   herdr plugin install chiptime/agent-tts/hosts/herdr/tts-plugin \
#     --ref validation/at-11-instalable --yes
# pub_gap records a failure that is NOT a policy refusal; whether it is a
# candidate/prerequisite gap (⇒ BLOCKED) or a real defect (⇒ FAIL) is
# decided by the demoted diagnostic leg below.
pub_gap=0
if (( registered_clean == 0 )) && [[ -z $ROUTE_BLOCKED_REASON ]]; then
  rc_b=0
  step "$HERDR_BIN" plugin install "$SPEC" --ref "$CANDIDATE_REF" --yes || rc_b=$?
  if (( rc_b == 0 )); then
    registered_clean=1
    ok "route: documented command + --ref $CANDIDATE_REF + --yes completed in the clean environment (candidate evidence leg)"
  else
    log_b=$(<"$SCEN_DIR/stdout.log")
    if [[ $log_b == *"BLOCKED-ORIGIN"* ]]; then
      ROUTE_BLOCKED_REASON="origin policy refused the candidate registry install (BLOCKED-ORIGIN; see stdout.log)"
    else
      pub_gap=1
    fi
  fi
fi

# --- Leg 2: DIAGNOSTIC FALLBACK (demoted by Decision 11) — the documented
# command with the product's HERDR_AGENT_TTS_REF test override at the
# default revision. Runs ONLY to diagnose a failed candidate leg: if this
# completes where the candidate leg failed, the gap was a [[build]]
# prerequisite at the selected revision (recorded as the BLOCKED reason);
# if it also fails, the failure is a real defect (FAIL). Its registration
# is inspected in "diagnostic" mode — evidence clearly separated from the
# candidate PASS, never presented as the candidate route.
registered=0
if (( registered_clean == 1 )); then
  inspect_registration candidate && registered=1
elif (( pub_gap == 1 )); then
  rc_2=0
  HERDR_AGENT_TTS_REF="$PIN" step "$HERDR_BIN" plugin install "$SPEC" --yes || rc_2=$?
  if (( rc_2 == 0 )); then
    ok "diagnostic fallback: documented command completes with HERDR_AGENT_TTS_REF=$PIN at the default revision (the [[build]] prerequisite at the candidate ref was the gap, not herdr's layout)"
    inspect_registration diagnostic && registered=1
    ROUTE_BLOCKED_REASON="candidate registry route could not complete at ref $CANDIDATE_REF (see stdout.log), diagnosed by the fallback leg as a [[build]] prerequisite gap — candidate route NOT passed; M1 stays open"
  else
    fail "route: documented command failed at the candidate ref AND with the pin override (see stdout.log) — not a publication gap"
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

# --- Final state: the OQ-1 evidence is recorded above; when the CANDIDATE
# documented route could not complete solely because of a missing
# prerequisite at the selected revision, the scenario is BLOCKED (exit 2)
# naming that prerequisite — never PASS, and never a FAIL downgrade of a
# real failure.
if [[ -n $ROUTE_BLOCKED_REASON && $nbad -eq 0 ]]; then
  block "$ROUTE_BLOCKED_REASON (candidate ref $CANDIDATE_REF @ $cand_sha; OQ-1 evidence recorded above; M1 stays open)"
fi
if (( registered_clean == 0 && registered == 0 && nbad == 0 )); then
  block "documented subdirectory route could not complete and the OQ-1 registration never materialized (see stdout.log)"
fi
