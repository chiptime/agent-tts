#!/usr/bin/env bash
# voice-stack MQ-05 — combined Bash harness driver for G-BASH-LINES.
#
# The changed-lines gate executes ONE harness under PS4 xtrace. The M1
# candidate's modified executable Bash lines live in bin/herdr-tts (exercised
# through sandboxed sourcing), in the host and critical case harnesses,
# and in the sterile bootstrap harness. This wrapper runs all three in one
# xtraced process tree. Same CASE protocol; any harness failure fails the run.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
rc=0
bash "$SCRIPT_DIR/host_cli_cases.sh" || rc=1
bash "$SCRIPT_DIR/critical_cases.sh" || rc=1
bash "$SCRIPT_DIR/bootstrap_sterile_harness.sh" || rc=1
bash "$SCRIPT_DIR/voice_cases.sh" || rc=1
bash "$SCRIPT_DIR/config_cases.sh" || rc=1
bash "$SCRIPT_DIR/lifecycle_cases.sh" || rc=1
bash "$SCRIPT_DIR/keymap_cases.sh" || rc=1
bash "$SCRIPT_DIR/ptt_cases.sh" || rc=1
bash "$SCRIPT_DIR/radio_cases.sh" || rc=1
exit $rc
