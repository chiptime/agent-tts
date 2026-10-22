"""Admission-only exception: retain prior authority and registry bounds."""
from pathlib import Path
import hashlib
import pytest
from test_final_handover_guard import guarded, fingerprint, voice, Tools


def files_under(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('token',['adelante','no','cambia el texto'])
def test_full_registry_preserves_original_binding_and_creates_no_job(guarded,token):
    client,model,control,cfg=guarded
    admitted=voice(client)
    assert admitted.status_code==200 and admitted.json()['speech']['status']=='complete'
    store=client.app.state.approval_store
    before_gate=store.current('default')
    assert before_gate.explained_speech_id=='guard-b0001'
    assert before_gate.explained_revision==before_gate.revision
    registry=client.app.state.speech_jobs
    assert registry._max_jobs==32
    for index in range(32):
        registry.register(f'busy-{index:04d}','busy-token','default')
    assert registry.active_count()==32
    before_jobs={key:(job.phase,job.cancel_requested) for key,job in registry._jobs.items()}
    before=fingerprint(client,cfg)
    audio_root=Path(cfg.audio_dir)
    before_files=files_under(audio_root)
    calls=len(model.calls)
    control['renders'].clear()
    response=voice(client,token,identity='overflow-b0002')
    assert response.status_code==200
    body=response.json()
    assert body['audio_url'] is None
    assert body['speech']=={'status':'degraded','reason':'registry-full'}
    assert body['approval']['gate_id']==before_gate.gate_id
    assert store.current('default')==before_gate
    assert fingerprint(client,cfg)==before
    assert registry.active_count()==32 and registry.get('overflow-b0002') is None
    assert {key:(job.phase,job.cancel_requested) for key,job in registry._jobs.items()}==before_jobs
    assert files_under(audio_root)==before_files
    assert control['renders']==[] and len(model.calls)==calls and not Tools.executions
    print('ADMISSION_PRESERVED',token,'HTTP200 audio=null exact-degraded original-binding unchanged active=32 new-jobs=0 renders=0 model=0 dispatch=0 history/action unchanged')


def test_legacy_protected_turn_remains_rendered_without_speech_metadata(guarded):
    client,model,control,cfg=guarded
    before=fingerprint(client,cfg)
    response=client.post('/ask',json={'text':'adelante','session_id':'default'})
    assert response.status_code==200
    body=response.json()
    assert body['audio_url'] and 'speech' not in body
    assert body['approval']['gate_id']==before[0]
    assert fingerprint(client,cfg)==before
    assert len(control['renders'])==1 and len(model.calls)==1 and not Tools.executions
    gate=client.app.state.approval_store.current('default')
    assert gate.explained_speech_id and gate.explained_revision==gate.revision
