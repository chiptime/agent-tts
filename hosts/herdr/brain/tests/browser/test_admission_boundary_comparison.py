from test_final_handover_guard import guarded, voice

def test_saturated_admission_contract(guarded):
    client,model,control,cfg=guarded
    registry=client.app.state.speech_jobs
    for index in range(registry._max_jobs):
        registry.register(f'active-{index:04d}','busy-token','default')
    response=voice(client)
    body=response.json()
    print('SATURATION',response.status_code,body.get('speech'),'audio_url=',body.get('audio_url'),'render_calls=',len(control['renders']))
    assert response.status_code==200
    assert body.get('speech')=={'status':'degraded','reason':'registry-full'}
    assert body['audio_url'] is None
    assert control['renders']==[]
