#!/usr/bin/env bash
# Read-only smoke checks for herdr-brain.
#
# SAFETY: this script NEVER sends prompts to agent sessions. It only runs
# read-only operations: `herdr agent list` parsing, active pane detection,
# a transcript read for the focused session, and the HTTP /health check.
# The write path (herdr agent prompt) is exercised exclusively by unit
# tests with mocked subprocess.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
HERDR="${HERDR_BIN:-herdr}"
PORT="${SMOKE_PORT:-8971}"
PASS=0
FAIL=0

ok()   { echo "PASS  $1"; PASS=$((PASS + 1)); }
bad()  { echo "FAIL  $1"; FAIL=$((FAIL + 1)); }
section() { echo; echo "== $1 =="; }

section "1. herdr agent list parses (read-only)"
LIST_OUT="$("$HERDR" agent list 2>&1)"
AGENTS_INFO="$(HERDR_LIST_OUTPUT="$LIST_OUT" "$PY" - <<'PYEOF'
import json, os, sys
from herdr_brain.herdr import parse_agent_list, pick_active

agents = parse_agent_list(os.environ["HERDR_LIST_OUTPUT"])
active = pick_active(agents)
print(json.dumps({"count": len(agents), "active": None if active is None else {
    "pane_id": active.pane_id, "agent": active.agent, "status": active.status,
    "session_id": active.session_value, "cwd": active.cwd,
}}))
PYEOF
)" || AGENTS_INFO=""
echo "$AGENTS_INFO" | "$PY" -c "import json,sys; d=json.load(sys.stdin); assert d['count']>0 and d['active']" >/dev/null 2>&1 \
  && ok "agent list parsed; active pane: $(echo "$AGENTS_INFO" | "$PY" -c "import json,sys; a=json.load(sys.stdin)['active']; print(a['pane_id'], a['agent'], a['status'])")" \
  || bad "agent list parse or active pane detection"

SESSION_ID="$(echo "$AGENTS_INFO" | "$PY" -c "import json,sys; print(json.load(sys.stdin)['active']['session_id'])" 2>/dev/null || true)"
AGENT_KIND="$(echo "$AGENTS_INFO" | "$PY" -c "import json,sys; print(json.load(sys.stdin)['active']['agent'])" 2>/dev/null || true)"

section "2. transcript read for the focused session (read-only)"
if [ -n "$SESSION_ID" ]; then
  TRANSCRIPT_HEAD="$("$PY" - "$AGENT_KIND" "$SESSION_ID" <<'PYEOF'
import sys
from herdr_brain.transcripts import read_transcript
text = read_transcript(sys.argv[1], sys.argv[2], n_turns=3)
print((text or "NO_TRANSCRIPT")[:200].replace("\n", " | "))
PYEOF
)"
  if [ -n "$TRANSCRIPT_HEAD" ]; then
    if [ "$TRANSCRIPT_HEAD" = "NO_TRANSCRIPT" ]; then
      ok "transcript connector ran; no store matched this session (screen fallback would apply)"
    else
      ok "transcript read: ${TRANSCRIPT_HEAD:0:120}..."
    fi
  else
    bad "transcript read for session $SESSION_ID"
  fi
else
  bad "no active session id available for transcript read"
fi

section "3. /health (read-only, server started locally)"
SERVER_PID=""
cleanup() { [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null; wait "$SERVER_PID" 2>/dev/null; }
trap cleanup EXIT

HERDR_BRAIN_AUDIO_DIR="${TMPDIR:-/tmp}/herdr-brain-smoke-audio" \
  "$PY" -m uvicorn herdr_brain.server:create_app --factory \
  --host 127.0.0.1 --port "$PORT" >/dev/null 2>&1 &
SERVER_PID=$!

HEALTH_OK=0
for _ in $(seq 1 30); do
  sleep 0.3
  if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then HEALTH_OK=1; break; fi
done
if [ "$HEALTH_OK" = "1" ]; then
  ok "/health responds: $(curl -fsS "http://127.0.0.1:$PORT/health")"
else
  bad "/health did not respond on port $PORT"
fi

section "Summary"
echo "passed: $PASS  failed: $FAIL"
[ "$FAIL" = "0" ]
