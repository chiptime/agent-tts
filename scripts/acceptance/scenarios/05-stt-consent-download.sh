# Scenario 5: stt-consent-download. Real download requires an explicit
# harness authorization, never inferred from an allowlisted origin.
HERDR_SCENARIO4_API=1 source "$CHECKOUT/scripts/acceptance/scenarios/04-first-run-keys.sh"
onboarding_sandbox
if step env PYTHONPATH="$CHECKOUT/tools" "$ONBOARDING_PYTHON" - "$CHECKOUT" <<'PY'
import os, pathlib, sys
from herdr_onboarding.steps.stt import model_cached
assert not model_cached('base', os.environ)
source = pathlib.Path(sys.argv[1]) / 'hosts/herdr/brain/src/herdr_brain/stt.py'
assert 'def main(' in source.read_text() and '"pull"' in source.read_text()
print('clean sandbox: no base model; supported pull CLI exists; no pull executed')
PY
then
  ok "clean model store and supported brain pull entry point; no download initiated"
else
  bad "offline STT prerequisite assertions failed; not masked as BLOCKED"
  exit 1
fi
[[ "${HERDR_ACCEPTANCE_ALLOW_MODEL_DOWNLOAD:-}" == 1 ]] \
  || block "real base-model download and ready-health evidence require authorized model-origin access and engine prerequisites; network/model downloads/live services forbidden in this assignment"

# This opt-in leg is NOT executed by the offline verification command. It
# exercises the brain's documented builtin backend through the SAME pull
# command; no alternate downloader or new production protocol is introduced.
unset HF_HUB_OFFLINE
export PYTHONPATH="$CHECKOUT/tools:$CHECKOUT/hosts/herdr/brain/src"
export HERDR_BRAIN_STT_BACKEND=builtin HERDR_BRAIN_STT_WARMUP=0
if ! step "$ONBOARDING_PYTHON" -c 'import fastapi, httpx, faster_whisper'; then
  block "brain Python dependencies for the supported builtin pull backend are unavailable"
fi
redact_argv "$ONBOARDING_PYTHON" - >> "$SCEN_DIR/cmd.log"
"$ONBOARDING_PYTHON" - > "$SCEN_DIR/download.log" 2>&1 <<'PY'
import io, os, sys
from fastapi.testclient import TestClient
from herdr_brain.config import load_settings
from herdr_brain.server import create_app
from herdr_brain.stt import Transcriber
from herdr_onboarding.steps.stt import SttStep, model_cached
from herdr_onboarding.wizard import RunContext, WizardOptions

assert not model_cached('base', os.environ)
ctx = RunContext(WizardOptions(role='brain', non_interactive=True, stt='base'),
                 dict(os.environ), io.StringIO(), io.StringIO(), sys.stderr, False)
SttStep().run(ctx)  # actual python -m herdr_brain.stt pull, then real contract CLI
assert ctx.preferences['stt_downloaded'] is True and model_cached('base', os.environ)
settings = load_settings(dict(os.environ, HERDR_BRAIN_STT_MODEL='base'))
transcriber = Transcriber(settings)  # actual local model, no fake loader
transcriber.warmup()
assert transcriber.state == 'ready', transcriber.error()
app = create_app(settings=settings, transcriber=transcriber, daemon_probe=lambda: 'up')
assert TestClient(app).get('/health').json()['stt'] == 'ready'
print('real base-model pull and cache, real contract CLI, real STT ready handler; daemon doubled')
PY
rc=$?
if (( rc == 0 )); then
  ok "real base-model download and speech-surface contract verification"
  ok_stubbed "real STT ready handler; daemon double only, no real TTS-service claim"
else
  # A failed executed download/assertion is FAIL, not automatically relabelled
  # as a missing prerequisite. Its concrete cause is retained in download.log.
  bad "authorized consent/download leg failed (exit $rc; see download.log)"
fi
