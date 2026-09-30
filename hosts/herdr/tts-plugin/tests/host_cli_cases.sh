#!/usr/bin/env bash
# voice-stack VS0.8 — named-case execution harness for the Bash decision
# matrix (scripts/voice-stack/bash_matrix.py).
#
# Protocol (parsed by the checker):
#   CASE <name> START
#   CASE <name> OK | CASE <name> FAIL
#
# A case is a function named case__<name> (hyphens become underscores).
# Run all cases:      bash tests/host_cli_cases.sh
# Run a subset:       CASES="name1 name2" bash tests/host_cli_cases.sh
# Exit status is non-zero iff any executed case failed; a FAIL never stops
# the run (the matrix needs the full outcome record).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_HOST_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"   # hosts/herdr/tts-plugin

case__host_cli_present() {
  local entry="$REPO_HOST_DIR/bin/herdr-tts"
  [[ -f "$entry" ]]
  [[ -x "$entry" ]]
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
