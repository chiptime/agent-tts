"""S13/S14: already-open devices must adopt live context before voice input."""
import json
import hashlib
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from herdr_brain.config import Settings
from herdr_brain.history import default_history_path
from herdr_brain.herdr import AgentInfo
from herdr_brain import server
from tests.conftest import SETTINGS_KWARGS
from test_desktop_call import fake_capture_browser, select_agent

ORIGIN = "http://127.0.0.1:41731"
PANES = ["w1:p9", "w2:p1"]

class Tools:
    executions=[]
    def dispatch(self,name,arguments,target=None):
        self.executions.append((name,arguments))
        return json.dumps({"ok":True,"delivered":True})
    def __init__(self, *args, **kwargs): pass
    def herd(self):
        return [dict(pane_id=p, agent="opencode", agent_status="idle", title=p,
                     focused=p == PANES[1], cwd="/repo", session_id="agent-"+p,
                     last_turn=None) for p in PANES]
    def agent_view(self, pane_id=None):
        return dict(status=dict(active=True, pane_id=pane_id or PANES[1],
                    agent="opencode", agent_status="idle"), screen=None,
                    transcript=None, screen_runs=None, dialog=None,
                    pending=dict(detected=False))
    def conversation(self, pane_id=None):
        return dict(pane_id=pane_id or PANES[1], agent="opencode",
                    session_id="agent", turns=[], window=20)
    def resolve_target(self, pane_id=None):
        return AgentInfo(pane_id=pane_id or PANES[1], agent="opencode", status="idle",
                         session_kind="id", session_value="agent", cwd="/repo",
                         title="agent", focused=True)

class Model:
    def __init__(self): self.calls=[]
    def attach_store(self, store): self.store=store
    def attach_approval_store(self, store): self.gates=store
    def ask(self, text, session_id=None, pane_id=None):
        self.calls.append(dict(text=text, session_id=session_id, pane_id=pane_id))
        gate = self.gates.propose(session_id, text="send reviewed summary", pane_id=PANES[0],
                                  agent="opencode", timeout_ms=300000) if text == "proposal" else None
        return dict(answer="Resumen listo.", pane_id=pane_id, agent="opencode",
                    session_id=session_id, approval=gate)

class Transcriber:
    state="ready"
    def __init__(self): self.text="how is it going"
    def transcribe_bytes(self, data):
        assert len(data)>1000
        return self.text

@pytest.fixture
def live_service(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "BrainTools", Tools)
    monkeypatch.setattr(server.AgentWatcher, "start", lambda self: None)
    monkeypatch.setattr(server.DaemonWatcher, "start", lambda self: None)
    monkeypatch.setattr(server, "tts_backend_status", lambda cfg: (server.TTS_BACKEND_OK, "fake"))
    Tools.executions=[]
    model, transcriber = Model(), Transcriber()
    sample=Path(__file__).resolve().parents[1]/"e2e/fixtures/sample.mp3"
    def tts(cfg, text, out):
        out.write_bytes(sample.read_bytes())
        return out
    app=server.create_app(settings=Settings(**{**SETTINGS_KWARGS,"audio_dir":str(tmp_path/"audio")}),
                          llm_factory=lambda *a:model, tts_renderer=tts,
                          transcriber=transcriber, daemon_probe=lambda:"up")
    with TestClient(app) as client:
        yield client, model, transcriber

class Device:
    def __init__(self, browser, client, delay_boot=False):
        self.requests=[]
        self.context=browser.new_context(permissions=["microphone"], service_workers="block")
        def route(r):
            req=r.request
            parsed=urlsplit(req.url)
            assert parsed.netloc=="127.0.0.1:41731", req.url
            self.requests.append((req.method, parsed.path, req.post_data if "application/json" in req.headers.get("content-type", "") else None))
            if parsed.path=="/events":
                r.fulfill(body=": isolated\n\n",content_type="text/event-stream")
                return
            response=client.request(req.method, parsed.path+("?"+parsed.query if parsed.query else ""),
                                    content=req.post_data_buffer,
                                    headers={k:v for k,v in req.headers.items() if k in ("content-type","range")})
            r.fulfill(status=response.status_code, body=response.content,
                      headers={k:v for k,v in response.headers.items() if k.lower() not in ("content-length","content-encoding")})
        self.context.route("**/*",route)
        if delay_boot:
            self.context.add_init_script("""(() => {
              const real = window.fetch.bind(window);
              window.fetch = async (input, opts) => {
                const response = await real(input, opts);
                if (input === '/call/context' && !opts) {
                  const data = await response.json();
                  window.bootSnapshot = data;
                  await new Promise(resolve => { window.releaseBoot = resolve; });
                  return new Response(JSON.stringify(data), {status: 200});
                }
                return response;
              };
            })();""")
        self.page=self.context.new_page()
        self.page.goto(ORIGIN,wait_until="load")
        self.page.wait_for_function("() => document.getElementById('pane-picker-label').textContent !== 'Cargando…'")
        self.page.wait_for_timeout(150)
    def wait_ask(self):
        self.page.wait_for_timeout(6000)
        return [json.loads(body) for method,path,body in self.requests if method=="POST" and path=="/ask"]

@pytest.mark.parametrize("direction", ["forward", "reverse-paused"])
def test_already_open_device_adopts_latest_pane(browser_type,tmp_path,live_service,direction):
    client, model, transcriber=live_service
    with fake_capture_browser(browser_type,tmp_path) as browser:
        a,b=Device(browser,client),Device(browser,client)
        try:
            source,target=(a,b) if direction=="forward" else (b,a)
            if direction=="reverse-paused":
                target.page.locator("#call-btn").click()
                target.page.locator("#pause-btn").click()
            select_agent(source.page,PANES[0])
            assert client.get("/call/context").json()["pane_id"]==PANES[0]
            target.page.locator("#pause-btn" if direction=="reverse-paused" else "#call-btn").click()
            asks=target.wait_ask()
            assert asks, "real captured utterance never reached /ask"
            assert asks[-1]["session_id"]=="default"
            assert asks[-1]["pane_id"]==PANES[0], asks[-1]
        finally:
            a.context.close(); b.context.close()

def test_gate_created_after_other_device_boot_is_not_discarded(browser_type,tmp_path,live_service):
    client,model,transcriber=live_service
    with fake_capture_browser(browser_type,tmp_path) as browser:
        a,b=Device(browser,client),Device(browser,client)
        try:
            transcriber.text="proposal"
            a.page.locator("#call-btn").click()
            assert a.wait_ask()
            a.page.wait_for_function("() => document.getElementById('player').ended")
            a.page.locator("#call-btn").click()
            gate=client.get("/approval/current").json()["approval"]
            assert gate
            transcriber.text="adelante"
            b.page.locator("#call-btn").click()
            asks=b.wait_ask()
            live=client.get("/approval/current").json()["approval"]
            assert not asks, {"unexpected_asks":asks,"gate_after":live}
            assert live and live["gate_id"]==gate["gate_id"]
            resolves=[json.loads(body) for method,path,body in b.requests if method=="POST" and path.endswith("/resolve")]
            assert resolves and resolves[-1].get("speech_request_id") is None
        finally:
            a.context.close(); b.context.close()


def test_delayed_boot_cannot_overwrite_explicit_selection(browser_type,tmp_path,live_service):
    client,model,transcriber=live_service
    client.post("/call/context",json={"pane_id":PANES[1]})
    with fake_capture_browser(browser_type,tmp_path) as browser:
        device=Device(browser,client,delay_boot=True)
        try:
            device.page.wait_for_function("() => !!window.releaseBoot")
            # Existing periodic inventory refresh runs while bootstrap is held.
            device.page.wait_for_timeout(5100)
            select_agent(device.page,PANES[0])
            assert client.get("/call/context").json()["pane_id"]==PANES[0]
            device.page.evaluate("() => window.releaseBoot()")
            device.page.wait_for_timeout(200)
            device.page.locator("#call-btn").click()
            asks=device.wait_ask()
            assert asks and asks[-1]["pane_id"]==PANES[0], asks
        finally:
            device.context.close()


def test_new_device_recovers_gate_without_playback_authority(browser_type,tmp_path,live_service):
    client,model,transcriber=live_service
    with fake_capture_browser(browser_type,tmp_path) as browser:
        a=Device(browser,client)
        transcriber.text="proposal"
        a.page.locator("#call-btn").click()
        assert a.wait_ask()
        a.page.wait_for_function("() => document.getElementById('player').ended")
        gate=client.app.state.approval_store.current("default")
        assert gate and gate.explained_speech_id
        job=client.app.state.speech_jobs.get(gate.explained_speech_id)
        assert job.phase=="complete"
        a.page.locator("#call-btn").click()
        b=Device(browser,client)
        try:
            b.page.wait_for_selector("#approval-float:not(.hidden)")
            transcriber.text="adelante"
            b.page.locator("#call-btn").click()
            assert not b.wait_ask()
            resolves=[json.loads(body) for method,path,body in b.requests if method=="POST" and path.endswith("/resolve")]
            assert resolves and resolves[-1].get("speech_request_id") is None
            live=client.app.state.approval_store.get(gate.gate_id)
            assert live.state=="proposed" and live.explained_speech_id
            assert not Tools.executions
        finally:
            a.context.close();b.context.close()


def test_recovered_revised_gate_and_manual_authority(live_service,tmp_path):
    client,model,transcriber=live_service
    assert client.post("/ask",json={"text":"proposal","session_id":"default"}).status_code==200
    store=client.app.state.approval_store
    gate=store.current("default")
    gid=gate.gate_id
    def action_hash():
        return hashlib.sha256(json.dumps(asdict(store.get(gid).action),sort_keys=True).encode()).hexdigest()
    def history_hash():
        return hashlib.sha256(default_history_path(Settings(**{**SETTINGS_KWARGS,"audio_dir":str(tmp_path/"audio")})).read_bytes()).hexdigest()
    original=(action_hash(),history_hash())
    clarified=client.post(f"/approval/{gid}/resolve",json={"utterance":"adelante"})
    assert clarified.status_code==200 and clarified.json()["decision"]=="clarify"
    assert (action_hash(),history_hash())==original and not Tools.executions
    receipt=clarified.json()["explain_speech_id"]
    assert receipt
    assert client.patch(f"/approval/{gid}",json={"text":"send only reviewed diff"}).status_code==200
    revised=(action_hash(),history_hash())
    stale=client.post(f"/approval/{gid}/resolve",json={"utterance":"adelante","speech_request_id":receipt})
    assert stale.status_code==200 and stale.json()["decision"]=="clarify"
    assert "send only reviewed diff" in stale.json()["answer"]
    assert (action_hash(),history_hash())==revised and not Tools.executions
    # Direct manual authorization remains available and executes only once.
    assert client.post(f"/approval/{gid}/approve").status_code==200
    assert len(Tools.executions)==1
    approved=(action_hash(),history_hash())
    again=client.post(f"/approval/{gid}/approve")
    assert again.status_code==404
    assert len(Tools.executions)==1 and (action_hash(),history_hash())==approved


def test_context_validation_and_read_only_endpoints_preserve_data(live_service,tmp_path):
    client,model,transcriber=live_service
    assert client.post("/ask",json={"text":"status","session_id":"default"}).status_code==200
    def data_hashes():
        return {str(p.relative_to(tmp_path)):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in tmp_path.rglob("*") if p.is_file()}
    assert client.post("/call/context",json={"pane_id":PANES[0]}).status_code==200
    original=client.get("/call/context").json()
    before=data_hashes()
    for payload in ({"pane_id":7},{"pane_id":"x"*200},{"pane_id":"w1:p9\nother"}):
        response=client.post("/call/context",json=payload)
        assert response.status_code==422
        assert client.get("/call/context").json()==original
        assert data_hashes()==before
    for path in ("/view?pane_id=w1:p9","/call-history","/herd"):
        assert client.get(path).status_code==200
        assert data_hashes()==before
    same=client.post("/call/context",json={"pane_id":PANES[0]}).json()
    assert same["revision"]==original["revision"]+1
    cleared=client.post("/call/context",json={"pane_id":None}).json()
    assert cleared=={"call_id":"default","pane_id":None,"revision":same["revision"]+1}
    assert data_hashes()==before
