#!/bin/sh
# Fetch the pinned Smart Turn v3.2 CPU ONNX weights.
#
# Local-only runtime artifact for the ADVISORY semantic turn detector
# (PRD-05 FR-02 amended, D18). Default OFF. This script is the ONLY
# documented pull path: the brain and pytest never download it, and the
# binary is never committed to the repository.
#
# Authorized sources only:
#   https://huggingface.co/pipecat-ai/smart-turn-v3
#   redirect host: https://us.aws.cdn.hf.co
#
# Usage:
#   scripts/fetch-smart-turn.sh [destination]
#
# Default destination (runtime state, outside the repo):
#   ~/.local/state/herdr-brain/models/smart-turn-v3.2-cpu.onnx
# When no argument is given, HERDR_BRAIN_SMART_TURN_MODEL overrides the
# default if it is set.

set -eu

URL="https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/main/smart-turn-v3.2-cpu.onnx"
EXPECTED_SIZE=8679182
EXPECTED_SHA256="2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f"

if [ "$#" -gt 1 ]; then
  printf '%s\n' "usage: $0 [destination]" >&2
  exit 64
fi

if [ "$#" -eq 1 ]; then
  dest=$1
elif [ -n "${HERDR_BRAIN_SMART_TURN_MODEL:-}" ]; then
  dest=$HERDR_BRAIN_SMART_TURN_MODEL
else
  dest="${HOME:?HOME is not set}/.local/state/herdr-brain/models/smart-turn-v3.2-cpu.onnx"
fi

dest_dir=$(dirname "$dest")
if ! mkdir -p "$dest_dir"; then
  printf 'error: cannot create destination directory: %s\n' "$dest_dir" >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  printf '%s\n' "error: curl is required but was not found in PATH" >&2
  exit 1
fi

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    printf '%s\n' "error: neither sha256sum nor shasum is available" >&2
    return 1
  fi
}

size_of() {
  wc -c < "$1" | tr -d ' '
}

if [ -f "$dest" ]; then
  if [ "$(size_of "$dest")" = "$EXPECTED_SIZE" ] && \
     [ "$(sha256_of "$dest")" = "$EXPECTED_SHA256" ]; then
    printf 'already present and verified: %s\n' "$dest"
    exit 0
  fi
  printf 'error: %s exists but does not match the pinned size/hash\n' "$dest" >&2
  printf '%s\n' "       refusing to overwrite it; move it away and re-run." >&2
  exit 1
fi

tmp="$dest.part.$$"
cleanup() {
  rm -f "$tmp"
}
trap cleanup 0 HUP INT TERM

printf 'downloading %s\n' "$URL"
if ! curl --fail --silent --show-error --location \
    --proto '=https' --tlsv1.2 \
    --output "$tmp" "$URL"; then
  printf '%s\n' "error: download failed (network, TLS or HTTP error)" >&2
  exit 1
fi

actual_size=$(size_of "$tmp")
if [ "$actual_size" != "$EXPECTED_SIZE" ]; then
  printf 'error: size mismatch: expected %s bytes, got %s\n' \
    "$EXPECTED_SIZE" "$actual_size" >&2
  exit 1
fi

actual_sha=$(sha256_of "$tmp")
if [ "$actual_sha" != "$EXPECTED_SHA256" ]; then
  printf 'error: SHA-256 mismatch:\n  expected %s\n  got      %s\n' \
    "$EXPECTED_SHA256" "$actual_sha" >&2
  exit 1
fi

mv "$tmp" "$dest"
trap - 0 HUP INT TERM
printf 'verified and installed: %s\n' "$dest"
printf '  size:   %s bytes\n' "$EXPECTED_SIZE"
printf '  sha256: %s\n' "$EXPECTED_SHA256"
