"""B1/B2/B3 correction-only asynchronous boundary regressions."""
import json
import pytest
from test_live_handover_regression import (
    Device, PANES, Tools, fake_capture_browser, live_service, select_agent,
)

@pytest.mark.parametrize("utterance", ["adelante", "summarize the new task"])
def test_gate_created_after_barrier_snapshot(browser_type,tmp_path,live_service,utterance):
    client,model,transcriber=live_service
    with fake_capture_browser(browser_type,tmp_path) as browser:
        a,b=Device(browser,client),Device(browser,client)
        created=[]
        try:
            a_session=a.page.evaluate("localStorage.getItem('herdr-brain-session')")
            b_session=b.page.evaluate("localStorage.getItem('herdr-brain-session')")
            def between_check_and_ask(route):
                if created: return route.fallback()
                # The client's barrier is complete when /ask is dispatched.
                # Open A's gate before that request reaches the real server.
                empty=client.get('/approval/current',params={'session_id':b_session})
                assert empty.json()=={'approval':None}
                assert client.post('/ask',json={'text':'proposal','session_id':a_session}).status_code==200
                gate=client.get('/approval/current',params={'session_id':a_session}).json()['approval']
                assert gate
                created.append(gate['gate_id'])
                route.fallback()
            b.context.route('**/ask',between_check_and_ask)
            transcriber.text=utterance
            b.page.locator('#call-btn').click()
            asks=b.wait_ask()
            assert created
            gate=client.get('/approval/current',params={'session_id':a_session}).json()['approval']
            assert not Tools.executions
            if utterance=='adelante':
                assert gate and gate['gate_id']==created[0], {'asks':asks,'gate_after':gate}
            else:
                assert asks and asks[-1]['text']==utterance
                # Deliberate new work still supersedes its own session's gate.
                assert client.post('/ask',json={'text':'proposal','session_id':b_session}).status_code==200
                assert client.get('/approval/current',params={'session_id':b_session}).json()['approval']
                assert client.post('/ask',json={'text':utterance,'session_id':b_session}).status_code==200
                assert client.get('/approval/current',params={'session_id':b_session}).json()=={'approval':None}
        finally:
            a.context.close();b.context.close()


def test_failed_live_context_does_not_send_stale_target(browser_type,tmp_path,live_service):
    client,model,transcriber=live_service
    with fake_capture_browser(browser_type,tmp_path) as browser:
        a,b=Device(browser,client),Device(browser,client)
        failures=[]
        try:
            select_agent(a.page,PANES[0])
            assert client.get('/call/context').json()['pane_id']==PANES[0]
            def fail_live(route):
                if route.request.method!='GET': return route.fallback()
                failures.append(route.request.url)
                route.fulfill(status=503,json={'detail':'context temporarily unavailable'})
            b.context.route('**/call/context',fail_live)
            b.page.locator('#call-btn').click()
            asks=b.wait_ask()
            assert failures, 'live recovery was not exercised'
            assert not asks, {'unexpected_asks':asks,'latest_pane':PANES[0]}
        finally:
            a.context.close();b.context.close()


@pytest.mark.parametrize('boundary',['context','gate'])
def test_hangup_during_barrier_drops_late_utterance(browser_type,tmp_path,live_service,boundary):
    client,model,transcriber=live_service
    with fake_capture_browser(browser_type,tmp_path) as browser:
        b=Device(browser,client)
        try:
            if boundary=='gate':
                session=b.page.evaluate("localStorage.getItem('herdr-brain-session')")
                assert client.post('/ask',json={'text':'proposal','session_id':session}).status_code==200
            # An injected fetch delay holds only the completed utterance's
            # live barrier, never startup or the native microphone gesture.
            b.page.evaluate("""boundary => {
              const real=window.fetch.bind(window);
              window.fetch=async (url,opts) => {
                const response=await real(url,opts);
                const match=boundary==='context' ? url==='/call/context' && !!opts
                    : String(url).startsWith('/approval/current');
                if(match){
                  const data=await response.json();
                  window.heldBarrier=true;
                  await new Promise(resolve => window.releaseBarrier=resolve);
                  return new Response(JSON.stringify(data),{status:200});
                }
                return response;
              };
            }""",boundary)
            b.page.locator('#call-btn').click()
            b.page.wait_for_function('() => !!window.releaseBarrier',timeout=15000)
            b.page.locator('#call-btn').click()
            assert b.page.locator('#call-btn').inner_text().strip()=='📞 Llamar'
            b.page.evaluate('() => window.releaseBarrier()')
            asks=b.wait_ask()
            assert not asks
            assert b.page.locator('#call-btn').inner_text().strip()=='📞 Llamar'
            assert b.page.evaluate("() => [...document.querySelectorAll('audio')].every(a=>a.paused)")
            assert not Tools.executions
        finally:b.context.close()
