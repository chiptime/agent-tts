#!/usr/bin/env bash
# voice-stack PWA changed-scope coverage pipeline (MQ-03, contract D4/T12).
#
# Executes the FULL metric chain end to end against worktree C:
#   1. Node suite with the instrumentation hook (pure modules + speech.js)
#   2. Browser E2E suite with route-served instrumented app.js/speech.js
#      (real app, real decoded media; E2E_JS_COVERAGE_DIR opt-in)
#   3. Merge all istanbul records -> combined detail + LCOV (FN/DA/BRDA)
#   4. Fresh instrumentation maps for the changed files
#   5. Machine changed-scope maps (app.js vs immutable B0 genesis;
#      speech.js as a new file => full scope)
#   6. coverage_gate.py changed-scope verdict (>=90/90) + common-set
#      totals non-regression vs the immutable baseline pwa.lcov
#
# Environment (all required):
#   REPO_C              worktree root (voice-stack checkout under test)
#   RUN_DIR             evidence dir for this gate run (own namespace)
#   B0_SNAPSHOT         immutable genesis baseline-snapshot.json (vs1 run)
#   B0_LCOV             immutable baseline pwa.lcov (vs1 run)
#   B0_APP_BLOB         content-addressed B0 app.js blob path (vs1 blobs/)
#   FORMER_EXCLUSIONS   the old blanket-exclusion file (REJECTED by the gate)
#   NODE_TOOLS          offline istanbul toolchain node_modules dir
#   PY                  python3 for the mapper/gate
# A fresh candidate snapshot is NOT needed: the gate re-hashes the mapped
# files on disk (candidate binding) and the map digests bind the detail.
set -euo pipefail

: "${REPO_C:?}" "${RUN_DIR:?}" "${B0_SNAPSHOT:?}" "${B0_LCOV:?}" \
   "${B0_APP_BLOB:?}" "${FORMER_EXCLUSIONS:?}" "${NODE_TOOLS:?}" "${PY:=python3}"

BRAIN="$REPO_C/hosts/herdr/brain"
STATIC="$BRAIN/src/herdr_brain/static"
TOOLS="$REPO_C/scripts/voice-stack/js-coverage"
COV_NODE="$RUN_DIR/cov-node"
COV_BROWSER="$RUN_DIR/cov-browser"
mkdir -p "$RUN_DIR" "$COV_NODE" "$COV_BROWSER"
export VOICE_STACK_JS_TOOLS="$NODE_TOOLS"

echo "== 1/6 node suite (instrumented require-hook) =="
cd "$BRAIN"
env JS_COVERAGE_DIR="$COV_NODE" PATH="${HOMEBREW_PREFIX:+$HOMEBREW_PREFIX/bin:}$PATH" \
  node --require "$TOOLS/hook.js" --test tests/js/ > "$RUN_DIR/node-tests.stdout.log" 2> "$RUN_DIR/node-tests.stderr.log"
NODE_RC=$?
sha256sum "$RUN_DIR/node-tests.stdout.log" "$RUN_DIR/node-tests.stderr.log" > "$RUN_DIR/node-tests.log.sha256"
echo "node-tests exit=$NODE_RC (full TAP in node-tests.stdout.log; sha256 recorded)"
grep -E "^.?.? ?(tests|pass|fail) [0-9]+" "$RUN_DIR/node-tests.stdout.log" | tail -3
[ "$NODE_RC" -eq 0 ] || { echo "NODE_SUITE_FAILED"; exit 1; }

echo "== 1b/6 node suite (raw V8 lane: baseline-comparable totals) =="
COV_V8="$RUN_DIR/cov-v8"
mkdir -p "$COV_V8"
env NODE_V8_COVERAGE="$COV_V8" PATH="${HOMEBREW_PREFIX:+$HOMEBREW_PREFIX/bin:}$PATH" \
  node --test tests/js/ > "$RUN_DIR/node-tests-v8.stdout.log" 2> "$RUN_DIR/node-tests-v8.stderr.log"
NODE_V8_RC=$?
sha256sum "$RUN_DIR/node-tests-v8.stdout.log" > "$RUN_DIR/node-tests-v8.log.sha256"
echo "node-tests-v8 exit=$NODE_V8_RC (full TAP in node-tests-v8.stdout.log)"
[ "$NODE_V8_RC" -eq 0 ] || { echo "NODE_V8_SUITE_FAILED"; exit 1; }
node "$TOOLS/v8-to-lcov.js" --static-dir "$STATIC" \
  --out-lcov "$RUN_DIR/node-v8-lcov.info" "$COV_V8"/coverage-*.json | tee "$RUN_DIR/v8-lane.log"

echo "== 2/6 browser E2E suite (instrumented route serve) =="
E2E_JS_COVERAGE_DIR="$COV_BROWSER" "$BRAIN/.venv/bin/python" -m pytest tests/e2e/ -q \
  > "$RUN_DIR/e2e-tests.stdout.log" 2> "$RUN_DIR/e2e-tests.stderr.log"
E2E_RC=$?
sha256sum "$RUN_DIR/e2e-tests.stdout.log" "$RUN_DIR/e2e-tests.stderr.log" > "$RUN_DIR/e2e-tests.log.sha256"
echo "e2e-tests exit=$E2E_RC:"; tail -1 "$RUN_DIR/e2e-tests.stdout.log"
[ "$E2E_RC" -eq 0 ] || { echo "E2E_SUITE_FAILED"; exit 1; }

echo "== 3/6 merge istanbul records =="
node "$TOOLS/merge-report.js" "$RUN_DIR/combined-detail.json" "$RUN_DIR/combined-lcov.info" \
  "$COV_NODE"/*.json "$COV_BROWSER"/browser-*.json | tee "$RUN_DIR/merge.log"

echo "== 4/6 fresh instrumentation maps =="
node "$TOOLS/instrument-file.js" "$STATIC/app.js" "$RUN_DIR/inst-app.js" "$RUN_DIR/maps-app.json"
node "$TOOLS/instrument-file.js" "$STATIC/speech.js" "$RUN_DIR/inst-speech.js" "$RUN_DIR/maps-speech.json"

echo "== 5/6 changed-scope maps =="
B0_APP_SHA=$("$PY" - "$B0_SNAPSHOT" <<'PYEOF'
import json, sys
snap = json.load(open(sys.argv[1]))
print(next(e["sha256"] for e in snap["scopes"]["G-JS"]["files"]
           if e["path"].endswith("static/app.js")))
PYEOF
)
cp "$B0_APP_BLOB" "$RUN_DIR/app.js.b0"
CUR_APP=$(sha256sum "$STATIC/app.js" | cut -d" " -f1)
CUR_SP=$(sha256sum "$STATIC/speech.js" | cut -d" " -f1)
"$PY" "$TOOLS/map-changed-scope.py" \
  --baseline-file "$RUN_DIR/app.js.b0" --baseline-sha256 "$B0_APP_SHA" \
  --current-file "$STATIC/app.js" --current-sha256 "$CUR_APP" \
  --detail "$RUN_DIR/combined-detail.json" --maps "$RUN_DIR/maps-app.json" \
  --out "$RUN_DIR/map-app.json"
"$PY" "$TOOLS/map-changed-scope.py" \
  --current-file "$STATIC/speech.js" --current-sha256 "$CUR_SP" \
  --detail "$RUN_DIR/combined-detail.json" --maps "$RUN_DIR/maps-speech.json" \
  --out "$RUN_DIR/map-speech.json"

echo "== 6/6 gate =="
# Totals ride the v8 lane (baseline-comparable per-line semantics); the
# changed-scope metrics ride the istanbul detail (precise statements/arcs).
set +e
"$PY" "$REPO_C/scripts/voice-stack/coverage_gate.py" \
  --changed-scope-map "$RUN_DIR/map-app.json" \
  --changed-scope-map "$RUN_DIR/map-speech.json" \
  --coverage-detail "$RUN_DIR/combined-detail.json" \
  --repo-root "$REPO_C" \
  --coverage-json "$RUN_DIR/node-v8-lcov.info" \
  --baseline-coverage "$B0_LCOV" \
  --exclusions "$FORMER_EXCLUSIONS" 2>&1 | tee "$RUN_DIR/gate.log"
GATE_RC=${PIPESTATUS[0]}
echo "GATE_EXIT=$GATE_RC" | tee -a "$RUN_DIR/gate.log"

echo "== post-run integrity record =="
# Hash every evidence artifact of this run (raw logs, lcov, detail, maps,
# gate output). Counts in this pipeline are only ever read from the FULL
# raw logs above — never from unrelated JSON files.
sha256sum \
  "$RUN_DIR/node-tests.stdout.log" "$RUN_DIR/node-tests.stderr.log" \
  "$RUN_DIR/node-tests-v8.stdout.log" "$RUN_DIR/node-tests-v8.stderr.log" \
  "$RUN_DIR/e2e-tests.stdout.log" "$RUN_DIR/e2e-tests.stderr.log" \
  "$RUN_DIR/combined-detail.json" "$RUN_DIR/combined-lcov.info" \
  "$RUN_DIR/node-v8-lcov.info" "$RUN_DIR/maps-app.json" "$RUN_DIR/maps-speech.json" \
  "$RUN_DIR/map-app.json" "$RUN_DIR/map-speech.json" "$RUN_DIR/gate.log" \
  > "$RUN_DIR/evidence.sha256"
echo "evidence.sha256 written ($(wc -l < "$RUN_DIR/evidence.sha256") files)"
exit "$GATE_RC"
