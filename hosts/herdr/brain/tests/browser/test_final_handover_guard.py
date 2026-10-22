"""Final B1/B2 correction: guard admission, output and safety invariants."""
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from herdr_brain import server
from herdr_brain.config import Settings
from herdr_brain.history import default_history_path
from tests.conftest import SETTINGS_KWARGS
from test_live_handover_regression import Tools, Model, Transcriber, Device, PANES, fake_capture_browser, select_agent, live_service

@pytest.fixture
def guarded(tmp_path,monkeypatch):
    monkeypatch.setattr(server,'BrainTools',Tools)
    monkeypatch.setattr(server.AgentWatcher,'start',lambda self:None)
    monkeypatch.setattr(server.DaemonWatcher,'start',lambda self:None)
    monkeypatch.setattr(server,'tts_backend_status',lambda cfg:(server.TTS_BACKEND_OK,'fake'))
    Tools.executions=[]
    model=Model()
    control={'mode':'ok','renders':[],'hook':None}
    def render(cfg,text,out):
        control['renders'].append(text)
        if control['hook']: control['hook']()
        if control['mode']=='fail': raise RuntimeError('injected local render failure')
        out.write_bytes(b'ID3-fixture')
        return out
    cfg=Settings(**{**SETTINGS_KWARGS,'audio_dir':str(tmp_path/'audio')})
    app=server.create_app(settings=cfg,llm_factory=lambda *args:model,tts_renderer=render,
                          transcriber=Transcriber(),daemon_probe=lambda:'up')
    with TestClient(app) as client:
        assert client.post('/ask',json={'text':'proposal','session_id':'default'}).status_code==200
        control['renders'].clear()
        yield client,model,control,cfg


def fingerprint(client,cfg):
    gate=client.app.state.approval_store.current('default')
    return (gate.gate_id,gate.state,gate.revision,
            hashlib.sha256(json.dumps(asdict(gate.action),sort_keys=True).encode()).hexdigest(),
            hashlib.sha256(default_history_path(cfg).read_bytes()).hexdigest())


def voice(client,text='adelante',identity='guard-b0001'):
    return client.post('/ask',json={'text':text,'session_id':'default','conversation':True,
        'speech_request_id':identity,'speech_cancel_token':'token-b0001'})


@pytest.mark.parametrize('token',['adelante','no','cambia el texto'])
def test_protected_decisions_restate_without_model_action_or_history(guarded,token):
    client,model,control,cfg=guarded
    before=fingerprint(client,cfg)
    calls=len(model.calls)
    result=voice(client,token)
    assert result.status_code==200
    body=result.json()
    assert body['approval']['gate_id']==before[0]
    assert 'send reviewed summary' in body['answer'] and body['answer'].startswith('Antes de confirmar:')
    assert body['audio_url'] and body['speech']=={'id':'guard-b0001','status':'complete'}
    assert fingerprint(client,cfg)==before and len(model.calls)==calls and not Tools.executions
    gate=client.app.state.approval_store.current('default')
    assert gate.explained_speech_id=='guard-b0001' and gate.explained_revision==gate.revision


@pytest.mark.parametrize('mode',['fail','cancel','revision'])
def test_failed_cancelled_or_revised_restate_never_binds_stale_authority(guarded,mode):
    client,model,control,cfg=guarded
    before=fingerprint(client,cfg)
    gid=before[0]
    if mode=='fail': control['mode']='fail'
    if mode=='cancel':
        def cancel():
            response=client.post('/speech/guard-b0001/cancel',json={'speech_cancel_token':'token-b0001','session_id':'default'})
            assert response.status_code==200,response.text
        control['hook']=cancel
    if mode=='revision':
        def revise():
            assert client.patch(f'/approval/{gid}',json={'text':'send only reviewed diff'}).status_code==200
        control['hook']=revise
    result=voice(client)
    assert result.status_code==200,result.text
    gate=client.app.state.approval_store.current('default')
    assert gate.gate_id==gid and gate.state=='proposed'
    assert gate.explained_speech_id!='guard-b0001'
    expected={'fail':'failed','cancel':'cancelled','revision':'complete'}[mode]
    assert result.json()['speech']['status']==expected
    assert client.app.state.speech_jobs.get('guard-b0001').phase==expected
    assert len(model.calls)==1 and not Tools.executions
    after=fingerprint(client,cfg)
    assert after[-1]==before[-1]
    if mode!='revision': assert after==before


def test_invalid_and_duplicate_admission_have_no_guard_side_effects(guarded):
    client,model,control,cfg=guarded
    before=fingerprint(client,cfg)
    malformed=voice(client,identity='bad')
    assert malformed.status_code==422
    assert malformed.json()=={'detail':'speech_request_id must match [A-Za-z0-9._-]{8,64}'}
    client.app.state.speech_jobs.register('guard-b0001','token-b0001','default')
    duplicate=voice(client)
    assert duplicate.status_code==409
    assert duplicate.json()=={'detail':'an active speech job already holds this id'}
    assert fingerprint(client,cfg)==before
    assert control['renders']==[] and len(model.calls)==1 and not Tools.executions


def test_failed_live_recovery_shows_exact_banner_and_changes_no_history(browser_type,tmp_path,live_service):
    client,model,transcriber=live_service
    with fake_capture_browser(browser_type,tmp_path) as browser:
        a,b=Device(browser,client),Device(browser,client)
        try:
            select_agent(a.page,PANES[0])
            before=client.get('/call-history').content
            b.context.route('**/call/context',lambda route:route.fulfill(status=503,json={'detail':'context temporarily unavailable'}))
            b.page.locator('#call-btn').click()
            assert not b.wait_ask()
            assert b.page.locator('#banner').is_visible()
            assert b.page.locator('#banner').inner_text()=='No pude confirmar el contexto de la llamada — vuelve a intentarlo.'
            assert client.get('/call-history').content==before
            assert not model.calls and not Tools.executions
        finally:
            a.context.close();b.context.close()


def test_registry_full_guard_keeps_text_only_admission_contract(guarded):
    client,model,control,cfg=guarded
    registry=client.app.state.speech_jobs
    for index in range(registry._max_jobs):
        registry.register(f'active-{index:04d}','busy-token','default')
    before=fingerprint(client,cfg)
    response=voice(client)
    assert response.status_code==200,response.text
    body=response.json()
    assert fingerprint(client,cfg)==before and len(model.calls)==1 and not Tools.executions
    print('FULL_GUARD: gate/action/history unchanged; no model/dispatch;',
          'speech=',body.get('speech'),'audio_url=',body.get('audio_url'),
          'render_calls=',len(control['renders']))
    assert body.get('speech')=={'status':'degraded','reason':'registry-full'},body
    assert body['audio_url'] is None
    assert control['renders']==[]
