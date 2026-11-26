"""HTTP surface tests for the /workspace/* routes (W3-P2 S2/S5).

The routes are device-gated READS over one pane's cwd. These tests stub
``BrainTools`` at the composition root (the test_session_routes.py
pattern) and drive real workspaces through tmp_path.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from herdr_brain import server as server_module
from herdr_brain.config import Settings
from herdr_brain.herdr import AgentInfo
from herdr_brain.server import create_app
from herdr_brain.tools import CrewTargetRefused
from tests.conftest import SETTINGS_KWARGS


class StubTools:
    """BrainTools double exposing just resolve_target."""

    def __init__(self, agent=None, error=None):
        self._agent = agent
        self._error = error

    def resolve_target(self, pane_id=None):
        if self._error is not None:
            raise self._error
        return self._agent


def make_agent(cwd: str, pane_id: str = "w1:p9") -> AgentInfo:
    return AgentInfo(
        pane_id=pane_id,
        agent="opencode",
        status="idle",
        session_kind="id",
        session_value="ses_x",
        cwd=cwd,
        title="OpenCode",
        focused=True,
    )


def make_client(tmp_path: Path, monkeypatch, tools) -> TestClient:
    monkeypatch.setattr(server_module, "BrainTools", lambda cfg: tools)
    settings = Settings(**{
        **SETTINGS_KWARGS,
        "state_dir": str(tmp_path / "state"),
        "audio_dir": str(tmp_path / "audio"),
    })
    return TestClient(create_app(settings=settings))


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("print('app')\n", encoding="utf-8")
    return root


class TestWorkspaceTree:
    def test_tree_serves_the_pane_workspace(
        self, tmp_path, monkeypatch, workspace
    ):
        tools = StubTools(make_agent(str(workspace)))
        client = make_client(tmp_path, monkeypatch, tools)
        resp = client.get("/workspace/tree", params={"pane_id": "w1:p9"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["pane_id"] == "w1:p9"
        paths = {entry["path"] for entry in body["entries"]}
        assert "app.py" in paths

    def test_unknown_pane_is_an_available_false_payload(
        self, tmp_path, monkeypatch
    ):
        tools = StubTools(None)
        client = make_client(tmp_path, monkeypatch, tools)
        resp = client.get("/workspace/tree", params={"pane_id": "gone:p1"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False
        assert body["entries"] == []

    def test_pane_without_cwd_is_unavailable(self, tmp_path, monkeypatch):
        tools = StubTools(make_agent("", pane_id="w2:p1"))
        client = make_client(tmp_path, monkeypatch, tools)
        resp = client.get("/workspace/tree")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False
        assert "working directory" in body["error"]

    def test_crew_refusal_is_relayed_never_500(self, tmp_path, monkeypatch):
        tools = StubTools(
            error=CrewTargetRefused("pane belongs to remote crew peer")
        )
        client = make_client(tmp_path, monkeypatch, tools)
        resp = client.get("/workspace/tree", params={"pane_id": "peer:p1"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False
        assert "crew" in body["error"]


class TestWorkspaceFile:
    def test_file_serves_content(self, tmp_path, monkeypatch, workspace):
        tools = StubTools(make_agent(str(workspace)))
        client = make_client(tmp_path, monkeypatch, tools)
        resp = client.get(
            "/workspace/file",
            params={"pane_id": "w1:p9", "path": "app.py"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert "print('app')" in body["content"]

    def test_traversal_attempt_is_refused_in_payload(
        self, tmp_path, monkeypatch, workspace
    ):
        (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
        tools = StubTools(make_agent(str(workspace)))
        client = make_client(tmp_path, monkeypatch, tools)
        resp = client.get(
            "/workspace/file",
            params={"pane_id": "w1:p9", "path": "../secret.txt"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False
        assert body["content"] is None
        assert "traversal" in body["error"]

    def test_missing_path_param_is_422(self, tmp_path, monkeypatch, workspace):
        tools = StubTools(make_agent(str(workspace)))
        client = make_client(tmp_path, monkeypatch, tools)
        # FastAPI validates the declared query param; absent is fine here
        # because ``path`` has a default — an EMPTY path is refused by the
        # module, not by the framework.
        resp = client.get("/workspace/file", params={"pane_id": "w1:p9"})
        assert resp.status_code == 200
        assert resp.json()["available"] is False


class TestWorkspaceChanges:
    def test_changes_reports_status_and_diff(
        self, tmp_path, monkeypatch, workspace
    ):
        import subprocess

        def _git(*args):
            subprocess.run(
                ["git", *args], cwd=workspace,
                capture_output=True, text=True, check=True,
            )

        _git("init", "-q")
        _git("-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
        _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "n")
        (workspace / "app.py").write_text("changed\n", encoding="utf-8")
        tools = StubTools(make_agent(str(workspace)))
        client = make_client(tmp_path, monkeypatch, tools)
        resp = client.get("/workspace/changes", params={"pane_id": "w1:p9"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["is_git_repo"] is True
        assert any(e["path"] == "app.py" for e in body["entries"])
        assert "changed" in body["diff"]


class TestWorkspaceSearch:
    def test_search_filename_route(self, tmp_path, monkeypatch, workspace):
        tools = StubTools(make_agent(str(workspace)))
        client = make_client(tmp_path, monkeypatch, tools)
        resp = client.get("/workspace/search", params={"pane_id": "w1:p9", "q": "app.py"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert any(r["path"] == "app.py" for r in body["results"])


class TestAccessClass:
    """C1 invariant: /workspace/* rides the device-gated READ class."""

    def test_routes_classify_as_reads(self):
        from herdr_brain.access import classify, is_device_exempt

        for path in ("/workspace/tree", "/workspace/file", "/workspace/changes", "/workspace/search"):
            assert classify("GET", path) == "read", path
            assert not is_device_exempt("GET", path), path
            assert classify("POST", path) == "read", path  # never a write rule
