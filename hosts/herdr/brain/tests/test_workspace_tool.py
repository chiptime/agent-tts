"""Tests for the read_workspace_changes voice tool (W3-P2 S4/S5)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from herdr_brain import workspace
from herdr_brain.config import Settings
from herdr_brain.herdr import AgentInfo
from herdr_brain.tools import TOOLS_SCHEMA, BrainTools
from tests.conftest import SETTINGS_KWARGS


def make_agent(cwd: str, pane_id: str = "w1:p9", focused: bool = True) -> AgentInfo:
    return AgentInfo(
        pane_id=pane_id,
        agent="opencode",
        status="idle",
        session_kind="id",
        session_value="ses_x",
        cwd=cwd,
        title="OpenCode",
        focused=focused,
    )


@pytest.fixture
def tools(settings, make_stub):
    """BrainTools whose stub serves two panes: the focused w1:p9 and w2:p3."""
    return BrainTools(settings, herdr=make_stub())


@pytest.fixture
def git_ws(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("print('app')\n", encoding="utf-8")

    def _git(*args):
        subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=True
        )

    _git("init", "-q")
    _git("-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "n")
    (root / "app.py").write_text("changed\n", encoding="utf-8")
    return root


class TestReadWorkspaceChanges:
    def test_selected_pane_payload_matches_the_pwa_response(
        self, tools, git_ws
    ):
        target = make_agent(str(git_ws))
        payload = json.loads(tools.read_workspace_changes(target))
        assert payload["pane_id"] == "w1:p9"
        assert payload["available"] is True
        assert payload["is_git_repo"] is True
        assert any(e["path"] == "app.py" for e in payload["entries"])
        assert "changed" in payload["diff"]
        # Same answer the PWA gets from GET /workspace/changes.
        direct = workspace.git_changes(str(git_ws))
        assert payload["entries"] == direct["entries"]
        assert payload["diff"] == direct["diff"]

    def test_dispatch_routes_by_name_with_target(self, tools, git_ws):
        target = make_agent(str(git_ws))
        payload = json.loads(
            tools.dispatch("read_workspace_changes", {}, target=target)
        )
        assert payload["available"] is True
        assert payload["pane_id"] == "w1:p9"

    def test_explicit_pane_id_reads_that_pane_fail_closed(
        self, tools, git_ws, tmp_path
    ):
        other_cwd = tmp_path / "other"
        other_cwd.mkdir()
        agents = [
            make_agent(str(git_ws), pane_id="w1:p9", focused=True),
            make_agent(str(other_cwd), pane_id="w2:p3", focused=False),
        ]
        tools = BrainTools(
            Settings(**{
                **SETTINGS_KWARGS,
                "state_dir": str(tmp_path / "s"),
            }),
            herdr=type("S", (), {"list_agents": staticmethod(lambda: agents)})(),
        )
        payload = json.loads(
            tools.dispatch(
                "read_workspace_changes",
                {"pane_id": "w2:p3"},
                target=agents[0],
            )
        )
        assert payload["pane_id"] == "w2:p3"
        assert payload["available"] is True
        assert payload["is_git_repo"] is False  # other dir is not a repo

    def test_unknown_pane_id_fails_closed_never_falls_back(
        self, tools, git_ws
    ):
        target = make_agent(str(git_ws))
        payload = json.loads(
            tools.read_workspace_changes(target, pane_id="gone:p1")
        )
        assert payload["available"] is False
        assert "unknown pane" in payload["error"]

    def test_no_target_and_no_pane_is_an_error_payload(
        self, settings, make_stub
    ):
        tools = BrainTools(settings, herdr=make_stub(agents=[]))
        payload = json.loads(tools.read_workspace_changes())
        assert payload["available"] is False
        assert "no active agent pane" in payload["error"]

    def test_pane_without_cwd_is_unavailable(self, settings, make_stub):
        tools = BrainTools(
            settings,
            herdr=make_stub(agents=[make_agent("", pane_id="w1:p9")]),
        )
        payload = json.loads(tools.read_workspace_changes())
        assert payload["available"] is False
        assert "working directory" in payload["error"]

    def test_dispatch_rejects_non_string_pane_id(self, tools, git_ws):
        target = make_agent(str(git_ws))
        result = tools.dispatch(
            "read_workspace_changes", {"pane_id": 42}, target=target
        )
        assert result.startswith("error: invalid arguments")

    def test_tool_is_in_the_schema_with_pane_id_param(self):
        entry = next(
            t for t in TOOLS_SCHEMA
            if t["function"]["name"] == "read_workspace_changes"
        )
        assert "pane_id" in entry["function"]["parameters"]["properties"]
        assert entry["function"]["parameters"]["required"] == []
