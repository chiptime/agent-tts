# Scenario 8: six simulated breakages through the real doctor/check table.
# Files, modes, PATH and pidfiles are real isolated fixtures. Audio/STT
# subprocesses and HTTP are explicit doubles: no device, model or service.
[[ "${HERDR_SANDBOX:-}" == 1 ]] || block "doctor breakage requires the acceptance sandbox"
python="${HERDR_ACCEPTANCE_PYTHON:-$(command -v python3)}"
[[ -x "$python" ]] || block "a real Python interpreter is required for doctor breakage"
redact_argv "$python" - "$CHECKOUT" "$SCEN_DIR" >> "$SCEN_DIR/cmd.log"
"$python" - "$CHECKOUT" "$SCEN_DIR" > "$SCEN_DIR/doctor-breakage.log" 2>&1 <<'PY'
import io, json, os, pathlib, subprocess, sys
root, evidence = map(pathlib.Path, sys.argv[1:])
sys.path.insert(0, str(root / 'tools'))
from herdr_onboarding import cli, doctor
home = evidence / 'home'
local = home / '.local/bin'
local.mkdir(parents=True)
env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(evidence / 'config'),
           XDG_STATE_HOME=str(evidence / 'state'), XDG_DATA_HOME=str(evidence / 'data'),
           XDG_CACHE_HOME=str(evidence / 'cache'), PATH=str(local),
           HERDR_TTS_DAEMON_PID_FILE=str(evidence / 'tts.pid'))
for name in ('herdr-tts', 'pactl'):
    path = local / name
    path.write_text('#!/bin/sh\nexit 0\n'); path.chmod(0o755)
secret = evidence / 'config/herdr-brain/env'
secret.parent.mkdir(parents=True)
secret.write_text('GLM_API_KEY=synthetic-doctor-key\n'); secret.chmod(0o600)
tts_pid = pathlib.Path(env['HERDR_TTS_DAEMON_PID_FILE'])
brain_pid = evidence / 'state/herdr-brain/daemon.pid'
brain_pid.parent.mkdir(parents=True)
tts_pid.write_text(str(os.getpid())); brain_pid.write_text(str(os.getpid()))
original = doctor.Doctor.__init__
broken = None
def runner(argv, **kwargs):
    if '--contract-version' in argv:
        output = '0' if broken == 'contract' else '1'
    elif '-c' in argv:
        assert 'model_is_cached' in argv[2] and 'pull' not in argv
        assert kwargs['env']['HF_HUB_OFFLINE'] == '1'
        output = 'false' if broken == 'stt' else 'true'
    else:
        output = 'audio-fixture'
    return subprocess.CompletedProcess(argv, 0, output, '')
def init(self, role, environ):
    original(self, role, environ, runner=runner, fetch=lambda *a: (200, '{"tts":"ok"}'), platform='Linux')
doctor.Doctor.__init__ = init  # external-boundary injection, not replacement checks
def invoke():
    output = io.StringIO()
    rc = cli.main(['--role', 'brain', '--doctor', '--json'], env=env, stdout=output)
    assert 'synthetic-doctor-key' not in output.getvalue()
    return rc, json.loads(output.getvalue())
assert invoke()[0] == 0
for broken in ('path', 'audio', 'credentials', 'daemon', 'contract', 'stt'):
    if broken == 'path':
        env['PATH'] = str(local) + ':/usr/bin'  # remove local-bin coverage only
        # HOME change makes the still-discoverable CLI lack HOME/.local/bin coverage.
        env['HOME'] = str(evidence / 'other-home')
    elif broken == 'audio': (local / 'pactl').chmod(0o644)
    elif broken == 'credentials': secret.chmod(0o644)
    elif broken == 'daemon': tts_pid.write_text('0')
    rc, record = invoke()
    assert rc == 1
    row = next(row for row in record['checks'] if row['name'] == broken)
    assert row['ok'] is False and row['remediation']
    repair = row['remediation']
    # Shell syntax validation is safe; never execute installation/download/repair.
    assert subprocess.run(['/bin/bash', '-n', '-c', repair]).returncode == 0
    if broken == 'path': assert repair == 'export PATH="$HOME/.local/bin:$PATH"'
    elif broken == 'audio': assert 'alsa-utils' in repair
    elif broken == 'credentials': assert repair.endswith('herdr-brain doctor --fix-credentials')
    elif broken == 'daemon': assert '--restart-daemon' in repair and 'herdr-brain --no-first-run restart' in repair
    elif broken == 'contract': assert repair.endswith('tts-plugin/scripts/install.sh')
    elif broken == 'stt': assert 'HERDR_BRAIN_STT_MODEL=small' in repair and '-m herdr_brain.stt pull' in repair
    print(f'{broken}: {repair}')
    env['HOME'], env['PATH'] = str(home), str(local)
    (local / 'pactl').chmod(0o755); secret.chmod(0o600); tts_pid.write_text(str(os.getpid()))
broken = None
assert invoke()[0] == 0
assert not (evidence / 'config/herdr-tts/first-run.done').exists()
assert not (evidence / 'cache').exists()
print('six real check failures named their exact repair; no repair or download executed')
PY
rc=$?
if (( rc == 0 )); then
  ok_stubbed "six real doctor breakages name executable repairs; audio/STT/HTTP doubled"
else
  bad "doctor breakage drill failed (exit $rc; see doctor-breakage.log)"
fi
