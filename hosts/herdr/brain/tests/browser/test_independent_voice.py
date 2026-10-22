import json
import pytest
from fastapi.testclient import TestClient
from herdr_brain.config import Settings
from herdr_brain.server import approval_payload
from tests.conftest import SETTINGS_KWARGS
from tests.test_server import approval_app, make_replay_tools, FakeTTS
from tests.browser.test_voice_dialog import say, wait_post, post_body, play_through

@pytest.fixture
def service(tmp_path, monkeypatch):
    tools = make_replay_tools()
    settings = Settings(**{**SETTINGS_KWARGS, "audio_dir": str(tmp_path / "audio")})
    app = approval_app(settings, tmp_path / "audio", monkeypatch, tools_cls=tools, tts=FakeTTS(fail=True))
    client = TestClient(app)
    gate = app.state.approval_store.propose("default", text="corre los tests", pane_id="w1:p9", agent="opencode")
    return app, client, gate, tools

def connected(karaoke_app, service, *, split=False):
    app, client, gate, tools = service
    h = karaoke_app()
    h.context.add_init_script("localStorage.setItem('herdr-brain-voice-engine', 'navegador');")
    held = []
    replies = []
    def ask(route):
        h.requests.append(route.request)
        body = route.request.post_data_json
        sid = body["speech_request_id"]
        app.state.speech_jobs.register(sid, body["speech_cancel_token"], "default")
        app.state.speech_jobs.mark(sid, "complete")
        if hasattr(app.state.approval_store, "bind_explanation"):
            app.state.approval_store.bind_explanation(gate.gate_id, sid)
        route.fulfill(json={"answer": "Tengo una propuesta. Se enviará a opencode: corre los tests.", "audio_url": "/audio/first.mp3" if split else None, "speech": {"id": sid, "status": "delivering" if split else "degraded", "first_seq": 0, "first_is_final": False}, "approval": approval_payload(app.state.approval_store.get(gate.gate_id), 60)})
    def resolve(route):
        h.requests.append(route.request)
        response = client.post(f"/approval/{gate.gate_id}/resolve", json=route.request.post_data_json)
        replies.append(response.json())
        route.fulfill(status=response.status_code, json=response.json())
    h.context.route("**/ask", ask)
    h.context.route("**/approval/*/resolve", resolve)
    h.context.route("**/speech/*/next?*", lambda route: held.append(route))
    h.load()
    h.page.set_default_timeout(10000)
    h.page.locator("#call-btn").click()
    h.page.wait_for_function("() => __karaokeHarness.recognitions.length > 0")
    say(h, "qué está pasando", 0)
    wait_post(h, "/ask")
    return h, held, replies

def test_no_audio_restatement_does_not_authorize_revised_action(karaoke_app, service):
    app, client, gate, tools = service
    h, held, replies = connected(karaoke_app, service)
    h.page.wait_for_function("() => __karaokeHarness.recognitions.length > 1")
    client.patch(f"/approval/{gate.gate_id}", json={"text": "borra las pruebas del login"})
    # Realistic TTS failure on the clarification: no explanation is played.
    say(h, "adelante", 1)
    wait_post(h, "/resolve")
    h.page.wait_for_function("() => __karaokeHarness.recognitions.length > 2")
    assert replies[0]["decision"] == "clarify"
    assert replies[0]["audio_url"] is None
    assert not tools.created[0].dispatches
    h.page.wait_for_timeout(5100)  # production endpointer suppresses repeated finals for five seconds
    say(h, "adelante", h.page.evaluate("() => __karaokeHarness.recognitions.length - 1"))
    h.page.wait_for_function("() => __karaokeHarness.fetchCalls.filter(p => p.endsWith('/resolve')).length > 1")
    h.page.wait_for_timeout(150)
    print("NO_AUDIO", replies, tools.created[0].dispatches)
    assert not tools.created[0].dispatches
    assert app.state.approval_store.get(gate.gate_id).state == "proposed"

def test_first_sentence_is_not_full_explanation(karaoke_app, service):
    app, client, gate, tools = service
    h, held, replies = connected(karaoke_app, service, split=True)
    h.page.wait_for_function("() => __karaokeHarness.fetchCalls.some(p => p.endsWith('/next'))")
    h.page.wait_for_function("() => !document.getElementById('player').paused")
    assert held
    play_through(h)
    h.page.wait_for_function("() => __karaokeHarness.recognitions.length > 1")
    say(h, "adelante", 1)
    wait_post(h, "/resolve")
    h.page.wait_for_timeout(150)
    body = post_body(h, "/resolve")
    for route in held:
        route.fulfill(json={"wait": True, "status": "cancelled"})
    print("PARTIAL", body, replies, tools.created[0].dispatches)
    assert body["speech_request_id"] is None
    assert not tools.created[0].dispatches
