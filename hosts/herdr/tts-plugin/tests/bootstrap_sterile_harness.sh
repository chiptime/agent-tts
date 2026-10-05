#!/usr/bin/env bash
# voice-stack MQ-05 — STERILE bootstrap.sh exercise for the Bash gates.
#
# Executes the REAL candidate scripts/bootstrap.sh in a fully sandboxed
# environment: fake HOME and XDG dirs, a fake `uv` on PATH that records its
# argv and creates stub venvs, no network, no global dirs, no real installs.
# The fake venv python always succeeds at `import agent_tts`, so both the
# fast path (healthy venv) and the full install path run deterministically.
#
# Protocol: same named-case shape as host_cli_cases.sh so the PS4 xtrace
# collector of scripts/voice-stack/bash_changed_lines.py can drive it:
#   CASE <name> START / CASE <name> OK | FAIL
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_HOST_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"   # hosts/herdr/tts-plugin
BOOTSTRAP="$REPO_HOST_DIR/scripts/bootstrap.sh"

SANDBOX_ROOT="$(mktemp -d)"
trap 'rm -rf "$SANDBOX_ROOT"' EXIT

# -- the fake toolchain -------------------------------------------------------
make_fake_env() {
  local box="$1"
  mkdir -p "$box/home" "$box/data" "$box/bin" "$box/config" "$box/state"
  # fake uv: `uv venv <dir>` creates a stub venv python; `uv pip install
  # ...` records its argv. Never touches the network or a real interpreter.
  cat > "$box/bin/uv" <<'UV'
#!/usr/bin/env bash
set -uo pipefail
if [[ "${1:-}" == "venv" ]]; then
  dir="${3:-}"
  mkdir -p "$dir/bin"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$dir/bin/python"
  chmod +x "$dir/bin/python"
  exit 0
fi
if [[ "${1:-}" == "pip" ]]; then
  printf 'uv %s\n' "$*" >> "$UV_ARGV_LOG"
  exit 0
fi
exit 0
UV
  chmod +x "$box/bin/uv"
}

run_bootstrap() { # $1 box, $2.. env assignments
  local box="$1"; shift
  env -i \
    PATH="$box/bin:/usr/bin:/bin" \
    HOME="$box/home" \
    XDG_DATA_HOME="$box/data" \
    XDG_CONFIG_HOME="$box/config" \
    XDG_STATE_HOME="$box/state" \
    UV_ARGV_LOG="$box/uv-argv.log" \
    ${SHELLOPTS:+SHELLOPTS="$SHELLOPTS"} \
    ${PS4:+PS4="$PS4"} \
    "$@" \
    bash "$BOOTSTRAP"
}

case__bootstrap_default_pin_installs_published_engine() {
  local box="$SANDBOX_ROOT/default"
  make_fake_env "$box"
  local rc=0
  run_bootstrap "$box" > "$box/out" 2> "$box/err" || rc=$?
  [[ $rc -eq 0 ]] || { cat "$box/err" >&2; return 1; }
  [[ -s "$box/uv-argv.log" ]] || return 1
  # the install argv carries the pinned engine ref — the published commit,
  # never a branch or short SHA
  grep -q "git+https://github.com/chiptime/agent-tts.git@e592ef31c828c5c637b3737604f173b1e0a07b80#subdirectory=engine" \
    "$box/uv-argv.log" || return 1
  # test instrumentation rides along on the full path
  grep -q "pytest" "$box/uv-argv.log" || return 1
  grep -q "TTS environment ready" "$box/out"
}

case__bootstrap_ref_override_flows_to_installer() {
  local box="$SANDBOX_ROOT/override"
  make_fake_env "$box"
  local rc=0
  run_bootstrap "$box" \
    HERDR_AGENT_TTS_REF=1234567890abcdef1234567890abcdef12345678 \
    > "$box/out" 2> "$box/err" || rc=$?
  [[ $rc -eq 0 ]] || { cat "$box/err" >&2; return 1; }
  grep -q "@1234567890abcdef1234567890abcdef12345678#subdirectory=engine" \
    "$box/uv-argv.log"
}

case__bootstrap_healthy_venv_fast_path_no_install() {
  local box="$SANDBOX_ROOT/fastpath"
  make_fake_env "$box"
  # pre-create the healthy venv: the fast path must leave it untouched and
  # run NO install command at all
  mkdir -p "$box/data/herdr-tts/venv/bin"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$box/data/herdr-tts/venv/bin/python"
  chmod +x "$box/data/herdr-tts/venv/bin/python"
  local rc=0
  run_bootstrap "$box" > "$box/out" 2> "$box/err" || rc=$?
  [[ $rc -eq 0 ]] || { cat "$box/err" >&2; return 1; }
  [[ ! -e "$box/uv-argv.log" ]]
}

fail=0
names=""
for fn in $(declare -F | awk '{print $3}' | grep '^case__'); do
  names="$names ${fn#case__}"
done
wanted="${CASES:-$names}"
for name in $wanted; do
  echo "CASE $name START"
  if "case__$name"; then echo "CASE $name OK"; else echo "CASE $name FAIL"; fail=1; fi
done
exit $fail
