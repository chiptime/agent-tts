import dataclasses, hashlib, json, threading
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from tests.test_server import approval_app, make_replay_tools, FakeTTS

def action_hash(gate):
    return hashlib.sha256(json.dumps(dataclasses.asdict(gate.action), sort_keys=True).encode()).hexdigest()

def test_concurrent_revision_invalidates_checked_receipt(settings, tmp_path, monkeypatch):
    tools = make_replay_tools()
    app = approval_app(settings, tmp_path / "audio", monkeypatch, tools_cls=tools, tts=FakeTTS(fail=True))
    store = app.state.approval_store
    gate = store.propose("s1", text="run tests", pane_id="w1:p9", agent="opencode")
    app.state.speech_jobs.register("explained-original", "token", "s1")
    app.state.speech_jobs.mark("explained-original", "complete")
    if hasattr(store, "bind_explanation"):
        store.bind_explanation(gate.gate_id, "explained-original")
    ready, resume = threading.Event(), threading.Event()
    original_resolve = store.resolve
    def interleave(*args, **kwargs):
        ready.set()
        assert resume.wait(5)
        return original_resolve(*args, **kwargs)
    monkeypatch.setattr(store, "resolve", interleave)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(TestClient(app).post, f"/approval/{gate.gate_id}/resolve", json={"utterance": "adelante", "speech_request_id": "explained-original"})
        assert ready.wait(5)
        try:
            response = TestClient(app).patch(f"/approval/{gate.gate_id}", json={"text": "delete tests"})
            assert response.status_code == 200
        finally:
            resume.set()
        result = future.result(timeout=5)
    print("REVISION_RACE", result.json(), tools.created[0].dispatches)
    assert not tools.created[0].dispatches
    assert store.get(gate.gate_id).state == "proposed"

def test_another_client_cannot_inherit_failed_clarify(settings, tmp_path, monkeypatch):
    tools = make_replay_tools()
    app = approval_app(settings, tmp_path / "audio", monkeypatch, tools_cls=tools, tts=FakeTTS(fail=True))
    gate = app.state.approval_store.propose("s1", text="run tests", pane_id="w1:p9", agent="opencode")
    before = action_hash(gate)
    first = TestClient(app).post(f"/approval/{gate.gate_id}/resolve", json={"utterance": "adelante"})
    assert first.json()["decision"] == "clarify"
    assert first.json()["audio_url"] is None
    assert action_hash(app.state.approval_store.get(gate.gate_id)) == before
    assert not tools.created[0].dispatches
    second = TestClient(app).post(f"/approval/{gate.gate_id}/resolve", json={"utterance": "adelante", "speech_request_id": "different-client"})
    print("CROSS_CLIENT", second.json(), tools.created[0].dispatches)
    assert not tools.created[0].dispatches
