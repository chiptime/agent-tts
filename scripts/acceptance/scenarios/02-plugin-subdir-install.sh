# Scenario 2: plugin-subdir-install (AT-11 slice 8, task 1.8; level V2, M1).
#
# The OQ-1 probe (design Decision 1): does the documented registry route
#   herdr plugin install chiptime/agent-tts/hosts/herdr/tts-plugin
# materialize the FULL monorepo at managed_path with
#   plugin_root = managed_path/<subdir>?
# (the wizard-reachability premise: from <plugin_root>/bin the shared
# tools/ package stays reachable by ascending the managed checkout).
#
# Legs, all recorded honestly in cmd.log/stdout.log:
#   1a) the literal id=install-plugin-github block, executed VERBATIM. On
#       non-interactive stdin herdr refuses with rc=2 and asks for --yes —
#       herdr's own documented unattended flag. The refusal is asserted as
#       observed behavior, never silently worked around.
#   1b) the same documented command + herdr's documented --yes, clean env.
#       If it fails at the public default revision's [[build]] (public main
#       still pins a pre-monorepo ref with no engine/ subdirectory — the
#       corrected pin is authored in this change and unpublished), that is a
#       missing prerequisite (a published working revision), classified
#       BLOCKED — proven by differential diagnosis in leg 2, not by brittle
#       output matching.
#   2)  the same command with the product's documented HERDR_AGENT_TTS_REF
#       test override (scripts/bootstrap.sh's own knob) so the [[build]]
#       completes and the registration materializes. This leg secures the
#       OQ-1 evidence TODAY; it never claims the unmodified public route
#       works (see blocked.reason when leg 1 could not complete).
#
# PASS requires the documented command to complete in a CLEAN environment
# (once a working revision is published, leg 1 registers and leg 2 is
# skipped); OQ-1 assertions run against whichever leg registered.
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

# Inspect whatever registration exists and assert the OQ-1 predicate on the
# registry's own fields. Every lookup is return-code or shape checked.
inspect_registration() {
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
  ok "registry: herdr.tts registered (source.kind=$skind resolved_commit=$scommit)"
  if [[ "$pro" == "$mpath/$sdir" ]]; then
    ok "OQ-1: plugin_root == managed_path/<subdir> ($pro)"
  else
    fail "OQ-1 FALSIFIED: plugin_root ($pro) != managed_path/subdir ($mpath/$sdir) — the pre-designed vendoring fallback (design Decision 1) applies"
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
}

ROUTE_BLOCKED_REASON=""

# Self-containment: scenarios share one sandbox (HOME/XDG) per harness run.
# bootstrap.sh's healthy-venv fast path would let an earlier scenario's venv
# mask the public default revision's REAL [[build]] state — clearing the
# shared data dir makes every install leg below a genuinely fresh build.
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

# --- Leg 1b: documented command + herdr's documented --yes, clean env.
# pub_gap records a clean-environment failure that is NOT a policy refusal;
# whether it is a publication gap (missing prerequisite ⇒ BLOCKED) or a real
# defect (⇒ FAIL) is decided by the differential leg below.
pub_gap=0
if (( registered_clean == 0 )) && [[ -z $ROUTE_BLOCKED_REASON ]]; then
  rc_b=0
  step "$HERDR_BIN" plugin install "$SPEC" --yes || rc_b=$?
  if (( rc_b == 0 )); then
    registered_clean=1
    ok "route: documented command + --yes completed in the clean environment"
  else
    log_b=$(<"$SCEN_DIR/stdout.log")
    if [[ $log_b == *"BLOCKED-ORIGIN"* ]]; then
      ROUTE_BLOCKED_REASON="origin policy refused the registry install (BLOCKED-ORIGIN; see stdout.log)"
    else
      pub_gap=1
    fi
  fi
fi

# --- Leg 2: differential leg with the product's documented pin override —
# completes the [[build]] at the corrected pin so the registration (and the
# OQ-1 evidence) materializes even while no public default revision carries
# the fix. The override is bootstrap.sh's own test knob and is recorded here
# and in cmd.log; it is never presented as the unmodified public route.
# If this leg succeeds where the clean leg failed, the public gap was the
# [[build]] prerequisite — recorded as the BLOCKED reason, never as a PASS.
registered=0
if (( registered_clean == 1 )); then
  inspect_registration && registered=1
elif [[ -z $ROUTE_BLOCKED_REASON ]]; then
  rc_2=0
  HERDR_AGENT_TTS_REF="$PIN" step "$HERDR_BIN" plugin install "$SPEC" --yes || rc_2=$?
  if (( rc_2 == 0 )); then
    ok "differential leg: documented command completes with the bootstrap's HERDR_AGENT_TTS_REF test override at pin $PIN (the [[build]] prerequisite, not herdr's layout, was the public gap)"
    inspect_registration && registered=1
    if (( pub_gap == 1 )); then
      stale=""
      log_b=$(<"$SCEN_DIR/stdout.log")
      [[ $log_b =~ from\ the\ pinned\ ref\ ([0-9a-f]{7,40}) ]] && stale=${BASH_REMATCH[1]}
      ROUTE_BLOCKED_REASON="documented subdirectory route cannot complete at the public default revision: its [[build]] bootstrap still pins ${stale:-a pre-monorepo ref} with no engine/ subdirectory, while the corrected pin $PIN is authored in this change and unpublished (proven by the differential leg above)"
    fi
  else
    fail "route: documented command failed even with the pin override (see stdout.log) — not a publication gap"
  fi
fi

# --- Final state: the OQ-1 evidence is recorded above; when the CLEAN
# documented route could not complete solely because no published revision
# has a working [[build]], the scenario is BLOCKED (exit 2) naming that
# prerequisite — never PASS, and never a FAIL downgrade of a real failure.
if [[ -n $ROUTE_BLOCKED_REASON && $nbad -eq 0 ]]; then
  block "$ROUTE_BLOCKED_REASON (OQ-1 evidence recorded above; M1 stays open)"
fi
if (( registered_clean == 0 && registered == 0 && nbad == 0 )); then
  block "documented subdirectory route could not complete and the OQ-1 registration never materialized (see stdout.log)"
fi
