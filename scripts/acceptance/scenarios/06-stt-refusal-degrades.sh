# Scenario 6: stt-refusal-degrades. Real app handler and wizard in-process;
# LLM, TTS, herdr and daemon are labelled external doubles, not live engines.
HERDR_SCENARIO4_API=1 source "$CHECKOUT/scripts/acceptance/scenarios/04-first-run-keys.sh"
onboarding_sandbox
export PYTHONPATH="$CHECKOUT/tools:$CHECKOUT/hosts/herdr/brain/src:$CHECKOUT/hosts/herdr/brain"
if ! step "$ONBOARDING_PYTHON" -c 'import fastapi, httpx'; then
  block "local brain Python dependencies unavailable; no installation authorized"
fi
redact_argv "$ONBOARDING_PYTHON" - >> "$SCEN_DIR/cmd.log"
"$ONBOARDING_PYTHON" - > "$SCEN_DIR/refusal.log" 2>&1 <<'PY'
import io, json, os, pathlib
from fastapi.testclient import TestClient
from herdr_brain.config import load_settings
from herdr_brain.server import create_app
from herdr_brain.stt import Transcriber
from herdr_onboarding.cli import main
from herdr_onboarding.health import HealthGate
from herdr_onboarding.secrets import brain_env_path, merge_env_file
from tests.test_server import FakeLLM, FakeTTS

merge_env_file(brain_env_path(os.environ), {'GLM_API_KEY': 'fixture-only'})
cfg = load_settings(dict(os.environ, GLM_API_KEY='fixture-only', HERDR_BRAIN_STT_WARMUP='0',
                         HERDR_BRAIN_AUDIO_DIR=str(pathlib.Path(os.environ['HOME']) / 'audio')))
def forbidden():
    raise AssertionError('refusal must never load or download a model')
transcriber = Transcriber(cfg, cached=lambda: False, loader=forbidden)
assert transcriber.maybe_start_warmup() is None
for fail in (False, True):
    app = create_app(settings=cfg, transcriber=transcriber,
                     llm_factory=lambda _c, _t: FakeLLM(),
                     tts_renderer=FakeTTS(fail=fail), daemon_probe=lambda: 'up')
    client = TestClient(app)
    reply = client.get('/health')
    assert reply.json()['stt'] == 'unavailable'
    assert reply.json()['tts'] == 'ok'  # daemon double, not a live daemon
    gate = HealthGate(fetch=lambda url, timeout: (reply.status_code, reply.text))
    code = main(['--role', 'brain', '--non-interactive', '--stt', 'none',
                 '--keymap-style', 'none'], env=dict(os.environ),
                health_gate=gate, stdin=io.StringIO(), stdout=io.StringIO(), stderr=io.StringIO())
    assert code == 0
    assert client.post('/transcribe', files={'audio': ('x.webm', b'x', 'audio/webm')}).status_code == 503
    response = client.post('/ask', json={'text': 'fixture question'})
    assert response.status_code == 200 and response.json()['answer']
    assert (response.json()['audio_url'] is None) is fail
    if not fail:
        assert client.get(response.json()['audio_url']).status_code == 200
marker = pathlib.Path(os.environ['XDG_CONFIG_HOME']) / 'herdr-tts/first-run.done'
assert json.loads(marker.read_text())['stt'] == 'none'
assert not pathlib.Path(os.environ['HF_HUB_CACHE']).exists()
print('STT refusal: real /health unavailable, /transcribe 503, /ask audio_url and documented null')
PY
rc=$?
if (( rc == 0 )); then
  ok_stubbed "real app/wizard refusal journey; LLM/TTS/herdr/daemon doubled, no download"
else
  bad "refusal integration failed (exit $rc; see refusal.log)"
fi
