"""Live pane mirror endpoint tests (doc 05 AC1).

Styled runs for a pane that is NOT blocked, plain text of the same window,
bounded line counts — and no terminal emulation anywhere.
"""

from __future__ import annotations

import pytest

from herdr_brain.config import MAX_SCREEN_LINES, Settings
from tests.test_history_page import StubHerdr, make_agent

ANSI_SCREEN = (
    "\x1b[1;32mOK\x1b[0m build passed\n"
    "plain line\n"
    "\x1b[7mselected option\x1b[27m\n"
    "\x1b[38;5;208mwarn\x1b[0m disk 80%\n"
)


@pytest.fixture
def client_factory(settings, monkeypatch):
    def _make(agents, screens=None):
        import herdr_brain.server as server_module
        from herdr_brain.tools import BrainTools

        stub = StubHerdr(agents, screens)

        class ToolsWithStub(BrainTools):
            def __init__(self, cfg):
                super().__init__(cfg, herdr=stub)

        monkeypatch.setattr(server_module, "BrainTools", ToolsWithStub)
        cfg = Settings(**settings.__dict__)
        from fastapi.testclient import TestClient

        return TestClient(server_module.create_app(settings=cfg))

    return _make


class TestMirrorEndpoint:
    def test_runs_and_text_for_a_working_pane(self, client_factory):
        client = client_factory(
            [make_agent(status="working")], screens={"w1:p1": ANSI_SCREEN}
        )
        resp = client.get("/pane/w1:p1/mirror")
        assert resp.status_code == 200
        body = resp.json()
        assert body["pane_id"] == "w1:p1"
        assert body["agent"] == "claude"
        assert body["status"] == "working"
        # Faithful ansi_runs output: bold+green first run, reverse video
        # third line, 256-colour fourth line, merged plain second line.
        # Green is xterm's normal palette (#00cd00, view.py _ANSI16_RGB).
        first = body["runs"][0][0]
        assert first["t"] == "OK"
        assert first.get("b") is True
        assert first.get("fg") == "#00cd00"
        assert body["runs"][2][0]["t"] == "selected option"
        assert body["runs"][2][0].get("rev") is True
        assert body["runs"][3][0].get("fg") == "#ff8700"
        # Plain text is the same window with escapes stripped.
        assert body["text"].splitlines() == [
            "OK build passed", "plain line", "selected option", "warn disk 80%",
        ]

    def test_clamped_lines_passed_to_the_reader(self, settings, monkeypatch):
        import herdr_brain.server as server_module
        from herdr_brain.tools import BrainTools

        stub = StubHerdr([make_agent()], screens={"w1:p1": ANSI_SCREEN})

        class ToolsWithStub(BrainTools):
            def __init__(self, cfg):
                super().__init__(cfg, herdr=stub)

        monkeypatch.setattr(server_module, "BrainTools", ToolsWithStub)
        from fastapi.testclient import TestClient

        client = TestClient(server_module.create_app(settings=Settings(**settings.__dict__)))
        client.get("/pane/w1:p1/mirror?lines=5000")
        mirror_calls = [c for c in stub.screen_calls if c["ansi"]]
        assert mirror_calls, "mirror must read with styling"
        assert all(c["n_lines"] <= MAX_SCREEN_LINES for c in mirror_calls)
        client.get("/pane/w1:p1/mirror?lines=0")
        assert stub.screen_calls[-1]["n_lines"] >= 1

    def test_unknown_pane_is_404(self, client_factory):
        client = client_factory([], screens={})
        assert client.get("/pane/nope/mirror").status_code == 404

    def test_screen_read_failure_degrades_to_empty_runs(self, settings, monkeypatch):
        import herdr_brain.server as server_module
        from herdr_brain.tools import BrainTools

        stub = StubHerdr([make_agent()], screens=None)

        class ToolsWithStub(BrainTools):
            def __init__(self, cfg):
                super().__init__(cfg, herdr=stub)

        monkeypatch.setattr(server_module, "BrainTools", ToolsWithStub)
        from fastapi.testclient import TestClient

        client = TestClient(server_module.create_app(settings=Settings(**settings.__dict__)))
        resp = client.get("/pane/w1:p1/mirror")
        assert resp.status_code == 200
        body = resp.json()
        assert body["runs"] == []
        assert body["text"] is None
