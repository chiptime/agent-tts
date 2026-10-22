"""S12: exact CLI output and isolated launch/service boundaries."""
import hashlib
import os
from pathlib import Path
import subprocess

import pytest

ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def cli(tmp_path):
    home=tmp_path/"home"; home.mkdir()
    stubs=tmp_path/"bin"; stubs.mkdir()
    log=tmp_path/"boundaries"
    for command in ("bash","readlink","dirname","mkdir","cat","rm","touch","seq","grep","head","cut"):
        stub=stubs/command
        stub.write_text(f'#!/bin/sh\nexec /usr/bin/{command} "$@"\n'); stub.chmod(0o755)
    for command in ("ss","lsof","ps","systemctl","sleep","nohup","xdg-open","open","browser"):
        stub=stubs/command
        stub.write_text('#!/bin/sh\nprintf "%s\\n" "'+command+' $*" >> "$BOUNDARY_LOG"\nexit ${OPENER_RC:-0}\n')
        stub.chmod(0o755)
    curl=stubs/"curl"
    curl.write_text('#!/bin/sh\nprintf "%s\\n" "curl $*" >> "$BOUNDARY_LOG"\n[ "${DOWN:-0}" = 0 ] || exit 7\nprintf \'{"status":"ok"}\\n\'\n')
    curl.chmod(0o755)
    env=dict(HOME=str(home),TMPDIR=str(tmp_path),PATH=str(stubs),
             XDG_CONFIG_HOME=str(home/"config"),XDG_STATE_HOME=str(home/"state"),
             HERDR_BRAIN_PORT="8765",HERDR_BRAIN_BROWSER=str(stubs/"browser"),
             HERDR_BIN="herdr-fake",HERDR_ONBOARDING_NO_FIRST_RUN="1",BOUNDARY_LOG=str(log))
    def run(*args, **overrides):
        return subprocess.run(["/usr/bin/bash",str(ROOT/"bin/herdr-brain"),*args],
                              env={**env,**overrides},capture_output=True,text=True,timeout=10)
    def files():
        return {str(p.relative_to(home)):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in home.rglob("*") if p.is_file()}
    return run,log,files,stubs

@pytest.mark.parametrize("args",[("url",),("status",),(),("stop",),("restart",)])
def test_legacy_outputs_and_mutations(cli,args):
    run,log,files,stubs=cli
    result=run(*args)
    status='─── Herdr Brain Status ───\nProcess PID : not found (stopped)\nHealth Check: OK (200)\nDetails     : {"status":"ok"}\n\nLocal URL: http://localhost:8765/\n'
    expected={"url":"Local URL: http://localhost:8765/\n", "status":status,
              "stop":"Herdr Brain is not running.\n",
              "restart":"Restarting Herdr Brain...\nHerdr Brain is not running.\n"+status}
    command=args[0] if args else "status"
    assert (result.returncode,result.stdout,result.stderr)==(0,expected[command],"")
    expected_files = {"state/herdr-brain/supervisor.stop":hashlib.sha256(b"").hexdigest()} if command in ("stop","restart") else {}
    if command=="restart": expected_files["state/herdr-brain/server.log"]=hashlib.sha256(b"").hexdigest()
    assert files()==expected_files

@pytest.mark.parametrize("failure",["down","opener"])
def test_call_failure_exact_and_preserves_data(cli,failure):
    run,log,files,stubs=cli
    # Seed a normal existing state file; failures must not modify it.
    run("url")
    before=files()
    result=run("call",**({"DOWN":"1"} if failure=="down" else {"OPENER_RC":"3"}))
    expected=("[herdr-brain] ERROR: Herdr Brain is not reachable at http://localhost:8765/ (health check failed).\n"
              "  Start it first: bin/herdr-brain restart   (or check bin/herdr-brain status)\n") if failure=="down" else (
              f"[herdr-brain] ERROR: the browser opener ({stubs}/browser) failed.\n"
              "  Open http://localhost:8765/ manually.\n")
    assert result.returncode==1 and result.stderr==expected
    assert files()==before
    calls=log.read_text()
    assert "systemctl" not in calls and "nohup" not in calls
    assert ("browser http://localhost:8765/" in calls)==(failure=="opener")


def test_call_uses_configured_port_and_only_one_opener(cli):
    run,log,files,stubs=cli
    result=run("call")
    assert result.returncode==0 and result.stderr==""
    assert result.stdout == """[herdr-brain] Opening the Collie voice call in your desktop browser: http://localhost:8765/
[herdr-brain] The call UI is opening. It shares the same call as your phone:
  the same conversation, agents and pending approvals.
  - The browser will ask for microphone access — that prompt is the consent
    gate; grant it to talk (a blocked mic falls back to the keyboard).
  - If the call has not started, tap "📞 Llamar" in the page.
"""
    assert log.read_text().splitlines()==["curl -fsS -m 2 http://127.0.0.1:8765/health","browser http://localhost:8765/"]
    assert files()=={}


@pytest.mark.parametrize("fallback",["xdg-open","open","missing"])
def test_call_fallback_and_missing_opener_exact(cli,fallback):
    run,log,files,stubs=cli
    if fallback!="xdg-open": (stubs/"xdg-open").unlink()
    if fallback=="missing": (stubs/"open").unlink()
    result=run("call",HERDR_BRAIN_BROWSER="")
    if fallback=="missing":
        assert result.returncode==1
        assert result.stdout==""
        assert result.stderr==("[herdr-brain] ERROR: no desktop browser opener found (tried $HERDR_BRAIN_BROWSER, xdg-open, open).\n"
                               "  Set HERDR_BRAIN_BROWSER to a browser command, or open http://localhost:8765/ manually.\n")
        assert log.read_text().splitlines()==["curl -fsS -m 2 http://127.0.0.1:8765/health"]
    else:
        assert result.returncode==0 and result.stderr==""
        assert log.read_text().splitlines()==["curl -fsS -m 2 http://127.0.0.1:8765/health",fallback+" http://localhost:8765/"]
    assert files()=={}
