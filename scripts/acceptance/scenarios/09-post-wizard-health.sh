# Scenario 9: complete local first-run chain, explicitly NOT real-install V2
# or timed-human V3 evidence. The real leg requires explicit authorization
# and already-installed, live SANDBOX services. It never installs a substitute.
[[ "${HERDR_SANDBOX:-}" == 1 ]] || block "post-wizard health requires the acceptance sandbox"
if [[ "${HERDR_ACCEPTANCE_LOCAL_HEALTH:-}" != 1 ]]; then
  [[ "${HERDR_ACCEPTANCE_REAL_HEALTH:-}" == 1 ]] || block "real post-wizard proof requires documented-origin installation and live sandbox brain/TTS/herdr; real-service execution is not authorized (timed human UAT remains V3)"
  python="$CHECKOUT/hosts/herdr/brain/.venv/bin/python"
  [[ -x "$python" ]] || block "installed sandbox brain interpreter is missing"
  [[ -x "$XDG_DATA_HOME/herdr-tts/venv/bin/python" ]] || block "documented-origin sandbox plugin interpreter is missing"
  command -v herdr >/dev/null || block "installed sandbox herdr CLI is missing"
  [[ "${HERDR_TTS_DAEMON_PID_FILE:-}" == "$XDG_STATE_HOME/"* ]] || block "a sandbox-state TTS daemon pidfile override is required; host defaults are forbidden"
  export PYTHONPATH="$CHECKOUT/tools:$CHECKOUT/hosts/herdr/brain/src"
  export PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1
  [[ ! -e "$XDG_CONFIG_HOME/herdr-tts/first-run.done" ]] || block "real first-run proof requires an isolated installation without a completion marker"
  # No installer, model pull, credential value or daemon start on this leg.
  # The preceding authorized installation/service setup owns those prerequisites.
  redact_argv "$python" -m herdr_onboarding --role brain --non-interactive --voice-provider piper --keymap-style menu --stt none --json >> "$SCEN_DIR/cmd.log"
  "$python" -m herdr_onboarding --role brain --non-interactive --voice-provider piper --keymap-style menu --stt none --json > "$SCEN_DIR/real-first-run.log" 2>&1
  rc=$?
  if (( rc == 20 )); then block "sandbox brain credential input is missing; provide the supported FD/file channel"; fi
  if (( rc != 0 )); then bad "real first-run failed (exit $rc; see real-first-run.log)"; return; fi
  "$python" - "$CHECKOUT" > "$SCEN_DIR/real-health.log" 2>&1 <<'PY'
import json, os, pathlib
from herdr_onboarding.doctor import Doctor
from herdr_onboarding.health import check_brain_health, check_plugin_list
marker = pathlib.Path(os.environ['XDG_CONFIG_HOME']) / 'herdr-tts/first-run.done'
assert json.loads(marker.read_text())['role'] == 'brain'
assert Doctor('brain', os.environ).daemon().result.ok
assert check_brain_health(os.environ, attempts=1).ok
assert check_plugin_list(os.environ).ok
print('real pidfiles, /health tts: ok, clean plugin list and completion marker verified; V3 unclaimed')
PY
  rc=$?
  if (( rc == 0 )); then ok "real post-wizard health and marker (timed human UAT remains V3)";
  else bad "real post-wizard probes failed (exit $rc; see real-health.log)"; fi
  return
fi
export HERDR_SCENARIO4_API=1
source "$CHECKOUT/scripts/acceptance/scenarios/04-first-run-keys.sh"
onboarding_sandbox
redact_argv "$ONBOARDING_PYTHON" - "$CHECKOUT" >> "$SCEN_DIR/cmd.log"
"$ONBOARDING_PYTHON" - "$CHECKOUT" > "$SCEN_DIR/post-wizard.log" 2>&1 <<'PY'
import json, os, pathlib, subprocess, sys
root = pathlib.Path(sys.argv[1])
config = pathlib.Path(os.environ['XDG_CONFIG_HOME'])
home = pathlib.Path(os.environ['HOME'])
bindir = home / '.local/bin'
bindir.mkdir(parents=True)
(bindir / 'herdr').symlink_to(os.environ['HERDR_BIN'])
os.environ['PATH'] = str(bindir) + os.pathsep + os.environ['PATH']
brain = root / 'hosts/herdr/brain'
python = brain / '.venv/bin/python'
python.parent.mkdir(parents=True, exist_ok=True)
if not python.exists(): python.symlink_to(sys.executable)
marker = config / 'herdr-tts/first-run.done'
argv = ['bash', str(brain / 'bin/herdr-brain'), 'first-run', '--non-interactive',
        '--voice-provider', 'piper', '--keymap-style', 'menu', '--stt', 'none', '--json']
key = 'synthetic-post-wizard-key-not-a-credential'
rd, wr = os.pipe(); os.write(wr, (key + '\n').encode()); os.close(wr)
warning = home / 'manifest-warning'; warning.touch()
try:
    result = subprocess.run(argv, env=dict(os.environ, HERDR_ONBOARDING_SECRET_FD=str(rd)),
                            pass_fds=(rd,), capture_output=True, text=True, timeout=30)
finally:
    os.close(rd)
assert result.returncode == 30, result.stderr
assert not marker.exists(), 'failed health must withhold marker'
assert key not in result.stdout + result.stderr
warning.unlink()
result = subprocess.run(argv, env=os.environ, capture_output=True, text=True, timeout=30)
assert result.returncode == 0, result.stderr
record = json.loads(result.stdout)
assert record['marker_written'] is True
payload = json.loads(marker.read_text())
assert payload['role'] == 'brain' and payload['stt'] == 'none'
assert payload['keymap'] == 'existing', payload  # retry preserves adopted user keymap
assert json.loads((config / 'herdr-tts/keymap.json').read_text())['style'] == 'menu'
assert (config / 'herdr-brain/env').stat().st_mode & 0o777 == 0o600
assert key not in result.stdout + result.stderr
calls = (home / 'herdr.calls').read_text()
assert 'config check' in calls and 'server reload-config' in calls and 'plugin list' in calls, calls
assert (config / 'herdr-tts/keymap.json').is_file()
# Independently repeat the actual completion probes (HTTP/herdr doubled).
sys.path.insert(0, str(root / 'tools'))
from herdr_onboarding.health import check_brain_health, check_plugin_list
assert check_brain_health(os.environ, attempts=1).ok
assert check_plugin_list(os.environ).ok
completed = marker.read_bytes()
result = subprocess.run(argv, env=os.environ, capture_output=True, text=True, timeout=30)
assert result.returncode == 0 and marker.read_bytes() == completed
assert json.loads(result.stdout)['status'] == 'already-completed'
# Doctor does not inherit marker success: uncached STT/dead pidfiles remain
# failures, even after consent refusal legitimately completed the wizard.
result = subprocess.run(['bash', str(brain / 'bin/herdr-brain'), 'doctor', '--json'],
                        env=os.environ, capture_output=True, text=True, timeout=30)
assert result.returncode == 1, result.stderr
checks = {r['name']: r for r in json.loads(result.stdout)['checks']}
assert not checks['daemon']['ok'] and not checks['stt']['ok']
assert marker.read_bytes() == completed
assert not pathlib.Path(os.environ['HF_HUB_CACHE']).exists()
print('real first-run/keymap/marker chain; repeated tts: ok and clean plugin list use declared HTTP/herdr doubles')
PY
rc=$?
if (( rc == 0 )); then
  ok_stubbed "post-wizard chain, failed-gate retry, health/list and marker; HTTP/herdr doubled, no live or V3 claim"
else
  bad "post-wizard local chain failed (exit $rc; see post-wizard.log)"
fi
