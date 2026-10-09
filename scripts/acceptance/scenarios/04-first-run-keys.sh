# Scenario 4: first-run-keys. Local wizard/launcher integration, NOT a
# real-daemon installation proof: HTTP and herdr are external test doubles.
# Every assertion depending on those doubles is tagged ok_stubbed.

onboarding_sandbox() {
  export HOME="$SCEN_DIR/home" XDG_CONFIG_HOME="$SCEN_DIR/config"
  export XDG_DATA_HOME="$SCEN_DIR/data" XDG_CACHE_HOME="$SCEN_DIR/cache"
  export XDG_STATE_HOME="$SCEN_DIR/state" PYTHONDONTWRITEBYTECODE=1
  export HF_HUB_OFFLINE=1 HF_HUB_CACHE="$SCEN_DIR/cache/huggingface/hub"
  export HERDR_TTS_HOME="$CHECKOUT/hosts/herdr/tts-plugin"
  export HERDR_CONFIG_DIR="$XDG_CONFIG_HOME"
  export HERDR_ONBOARDING_HOME="$CHECKOUT/tools"
  export HERDR_BIN="$SCEN_DIR/herdr-double"
  export HERDR_TTS_LOCK_FILE="$SCEN_DIR/playing.lock"
  export HERDR_TTS_PID_FILE="$SCEN_DIR/current.pid"
  export HERDR_TTS_IPC_SOCKET="$SCEN_DIR/player.sock"
  export HERDR_TTS_SNOOZE_FILE="$SCEN_DIR/snooze.json"
  export HERDR_TTS_DAEMON_PID_FILE="$SCEN_DIR/daemon.pid"
  export HERDR_TTS_DAEMON_LOG="$SCEN_DIR/daemon.log"
  export HERDR_TTS_KEYMAP_FILE="$XDG_CONFIG_HOME/herdr-tts/keymap.json"
  ONBOARDING_PYTHON="${HERDR_ACCEPTANCE_PYTHON:-$(command -v python3)}"
  [[ -x "$ONBOARDING_PYTHON" ]] || block "a real Python interpreter is required"
  "$ONBOARDING_PYTHON" - "$SCEN_DIR" <<'PY'
import os, pathlib, sys
root = pathlib.Path(sys.argv[1])
for name in ('home', 'config', 'data/herdr-tts/venv/bin', 'state', 'cache', 'doubles'):
    (root / name).mkdir(parents=True, exist_ok=True)
(root / 'data/herdr-tts/venv/bin/python').symlink_to(sys.executable)
herdr = root / 'herdr-double'
herdr.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$HOME/herdr.calls"
case "$*" in
  'plugin list')
    if test -f "$HOME/manifest-warning"; then
      echo 'warning: manifest unavailable: test fixture' >&2
    else echo 'herdr.tts enabled'; fi ;;
  'config check'|'config check '*|'server reload-config') exit 0 ;;
  *) exit 99 ;;
esac
''')
herdr.chmod(0o755)
# An explicit external HTTP double for the real CLI's default HealthGate.
# No socket may be opened by this subprocess. Never production configuration.
(root / 'doubles/sitecustomize.py').write_text('''
import io, json, socket, urllib.request
class Reply(io.StringIO):
    status = 200
    def read(self, *args):
        return json.dumps({"tts": "ok", "stt": "unavailable"}).encode()
urllib.request.OpenerDirector.open = lambda *a, **k: Reply()
def forbidden(*a, **k):
    raise AssertionError("scenario HTTP double must prevent real network access")
socket.create_connection = forbidden
''')
PY
  export PYTHONPATH="$SCEN_DIR/doubles"
}

if [[ "${HERDR_SCENARIO4_API:-}" != 1 ]]; then
  onboarding_sandbox
  # Real launcher + real interpreter + real wizard. No credential in argv:
  # Python supplies a pipe FD to the launcher and snapshots the child argv.
  step "$ONBOARDING_PYTHON" - "$CHECKOUT" <<'PY'
import json, os, pathlib, subprocess, sys
root = pathlib.Path(sys.argv[1])
config = pathlib.Path(os.environ['XDG_CONFIG_HOME'])
marker = config / 'herdr-tts/first-run.done'
key = 'fixture-first-run-key-not-a-real-credential'
rd, wr = os.pipe()
os.write(wr, (key + '\n').encode()); os.close(wr)
env = dict(os.environ, HERDR_ONBOARDING_SECRET_FD=str(rd))
argv = ['bash', str(root / 'hosts/herdr/tts-plugin/bin/herdr-tts'),
        'first-run', '--non-interactive', '--voice-provider', 'piper',
        '--keymap-style', 'none', '--json']
# Plugin-only onboarding never demands a GLM key.
result = subprocess.run(argv, env=os.environ, capture_output=True, text=True, timeout=30)
assert result.returncode == 0, result.stderr
assert not (config / 'herdr-brain/env').exists()
assert json.loads(marker.read_text())['role'] == 'plugin'
marker.unlink()  # explicit retry fixture, not an installer operation
brain = root / 'hosts/herdr/brain'
(brain / '.venv/bin').mkdir(parents=True, exist_ok=True)
python = brain / '.venv/bin/python'
if not python.exists(): python.symlink_to(sys.executable)
argv = ['bash', str(brain / 'bin/herdr-brain'), 'first-run', '--non-interactive',
        '--voice-provider', 'piper', '--keymap-style', 'none', '--stt', 'none', '--json']
assert key not in ' '.join(argv)
try:
    result = subprocess.run(argv, env=env, pass_fds=(rd,), capture_output=True, text=True, timeout=30)
finally:
    os.close(rd)
assert result.returncode == 0, result.stderr
assert key not in result.stdout + result.stderr
record = json.loads(result.stdout)
assert record['marker_written'] is True
secret_file = config / 'herdr-brain/env'
assert key in secret_file.read_text()
assert secret_file.stat().st_mode & 0o777 == 0o600
assert json.loads(marker.read_text())['stt'] == 'none'
# Failed gate preserves complete credentials and preferences, with no marker.
before = secret_file.read_bytes()
marker.unlink()
(pathlib.Path(os.environ['HOME']) / 'manifest-warning').touch()
result = subprocess.run(argv, env=os.environ, capture_output=True, text=True, timeout=30)
assert result.returncode == 30
assert not marker.exists() and secret_file.read_bytes() == before
assert key not in result.stdout + result.stderr
(pathlib.Path(os.environ['HOME']) / 'manifest-warning').unlink()
result = subprocess.run(argv, env=os.environ, capture_output=True, text=True, timeout=30)
assert result.returncode == 0 and marker.exists()
assert secret_file.read_bytes() == before
# Marker suppresses repeat work and is byte-identical, not merely valid JSON.
completed = marker.read_bytes()
result = subprocess.run(argv, env=os.environ, capture_output=True, text=True, timeout=30)
assert result.returncode == 0 and marker.read_bytes() == completed
assert json.loads(result.stdout)['status'] == 'already-completed'
assert not pathlib.Path(os.environ['HF_HUB_CACHE']).exists()
print('real launcher/wizard: keyless plugin, FD capture, mode 600, retry and marker preservation')
PY
  rc=$?
  if (( rc == 0 )); then
    ok_stubbed "real launcher/wizard FD capture and retry; HTTP/herdr doubled, no real service claim"
  else
    bad "first-run integration failed (exit $rc; see stdout.log)"
  fi
fi
