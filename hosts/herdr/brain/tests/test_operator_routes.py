"""Operator config routes (S3, doc 06): the fixed PWA contract.

Three routes over the four validated TOML surfaces. The contract is
FROZEN for the PWA sibling: ``GET /operator/config`` serves exactly the
agreed shape (launchers NEVER carry their argv), ``POST
/operator/launch`` names a row by id (never a command line) and
executes it through the multiplexer, ``POST /operator/preset`` resolves
a key preset SERVER-side by the pane's agent family and rides the
existing /pane/keys code path. Both writes are classified writes behind
the access gate and audited.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import herdr_brain.server as server_module
from herdr_brain import access
from herdr_brain.config import Settings
from herdr_brain.operator_config import OperatorConfig
from herdr_brain.server import create_app

from tests.conftest import SETTINGS_KWARGS

REPLIES_TOML = """
[[replies]]
label = "Sigue"
text = "continúa por favor"

[[replies]]
label = "Detente"
text = "para ahora"
scope = "claude"
"""

COMMANDS_TOML = """
[[commands]]
label = "Status"
command = "/status"

[[commands]]
label = "Compact"
command = "/compact "
"""

KEYS_TOML = """
[[keys]]
label = "Rechazar"
keys = ["left", "enter"]

[[keys]]
label = "Aprobar todo"
keys = ["right", "enter"]
scope = "opencode"
"""

LAUNCHERS_TOML = """
[[launchers]]
label = "Repo shell"
argv = ["bash"]

[[launchers]]
label = "Danger shell"
argv = ["bash", "-c", "echo hi"]
cwd = "/definitely/not/here"
no_prompts = true

[[launchers]]
label = "TTY shell"
argv = ["zsh"]
cwd = "/tmp"
"""


class LaunchTools:
    """BrainTools double recording the launch/preset executions."""

    def __init__(self, cfg=None):
        self.launches: list[tuple] = []
        self.key_sends: list[tuple] = []
        self.fail_launch = False
        self.pane_agent = {"w1:p9": "opencode", "w2:p1": "claude"}

    def status_payload(self):
        return {"active": False}

    def herd(self):
        return []

    def launch_operator_tab(self, label, cwd, command_line):
        if self.fail_launch:
            from herdr_brain.herdr import HerdrError

            raise HerdrError("mux refused")
        self.launches.append((label, cwd, command_line))
        return {"tab_id": "t-1", "pane_id": "w9:p1"}

    def pane_info(self, pane_id):
        if pane_id not in self.pane_agent:
            return None
        return {
            "pane_id": pane_id,
            "agent": self.pane_agent[pane_id],
            "session_id": None,
            "cwd": None,
        }

    def send_keys_to_pane(self, pane_id, keys):
        self.key_sends.append((pane_id, list(keys)))
        return {"outcome": "sent", "pane_id": pane_id, "detail": ""}


def write_config(directory: Path) -> OperatorConfig:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "quick-replies.toml").write_text(REPLIES_TOML)
    (directory / "commands.toml").write_text(COMMANDS_TOML)
    (directory / "keys.toml").write_text(KEYS_TOML)
    (directory / "launchers.toml").write_text(LAUNCHERS_TOML)
    return OperatorConfig(config_dir=directory)


def make_client(tmp_path, monkeypatch, tools=None) -> TestClient:
    tools = tools or LaunchTools()
    monkeypatch.setattr(server_module, "BrainTools", lambda cfg: tools)
    settings = Settings(**{
        **SETTINGS_KWARGS,
        "state_dir": str(tmp_path / "state"),
        "audio_dir": str(tmp_path / "audio"),
    })
    app = create_app(
        settings=settings,
        operator_config=write_config(tmp_path / "operator"),
    )
    return TestClient(app)


def audit_lines(state_dir: Path) -> list[dict]:
    path = state_dir / "audit.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


# ── GET /operator/config — the frozen contract ──────────────────────────────


class TestOperatorConfigRoute:
    def test_serves_exactly_the_frozen_shape(self, tmp_path, monkeypatch):
        client = make_client(tmp_path, monkeypatch)
        resp = client.get("/operator/config", params={"agent": "claude"})
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {
            "quick_replies", "commands", "key_presets", "launchers"
        }
        # claude has scoped replies: ONLY the scoped rows are served.
        assert body["quick_replies"] == [
            {"id": "detente", "label": "Detente", "text": "para ahora"}
        ]
        assert body["commands"][0] == {
            "id": "status", "label": "Status", "command": "/status"
        }
        assert body["key_presets"] == [
            {"id": "rechazar", "label": "Rechazar", "keys": ["left", "enter"]}
        ]
        # Launchers publish identity ONLY — the argv never crosses the
        # wire, and no_prompts tells the PWA to confirm twice.
        assert body["launchers"] == [
            {"id": "repo-shell", "label": "Repo shell", "no_prompts": False},
            {"id": "danger-shell", "label": "Danger shell", "no_prompts": True},
            {"id": "tty-shell", "label": "TTY shell", "no_prompts": False},
        ]
        assert "argv" not in json.dumps(body)

    def test_scope_resolution_without_agent_serves_global_rows(self, tmp_path, monkeypatch):
        client = make_client(tmp_path, monkeypatch)
        body = client.get("/operator/config").json()
        assert [r["id"] for r in body["quick_replies"]] == ["sigue"]
        # opencode has its own key preset: substitution, not fusion.
        scoped = client.get("/operator/config", params={"agent": "opencode"}).json()
        assert [k["id"] for k in scoped["key_presets"]] == ["aprobar-todo"]

    def test_reload_by_mtime_on_the_next_request(self, tmp_path, monkeypatch):
        client = make_client(tmp_path, monkeypatch)
        assert client.get("/operator/config").json()["quick_replies"] == [
            {"id": "sigue", "label": "Sigue", "text": "continúa por favor"}
        ]
        (tmp_path / "operator" / "quick-replies.toml").write_text(
            '[[replies]]\nlabel = "Nuevo"\ntext = "nuevo texto"\n'
        )
        body = client.get("/operator/config").json()
        assert body["quick_replies"] == [
            {"id": "nuevo", "label": "Nuevo", "text": "nuevo texto"}
        ]

    def test_is_a_read_route(self):
        assert access.classify("GET", "/operator/config") == "read"


# ── POST /operator/launch — the allowlist execution ─────────────────────────


class TestOperatorLaunch:
    def _post(self, client, payload):
        return client.post("/operator/launch", json=payload)

    def test_launches_the_named_row_through_the_mux(self, tmp_path, monkeypatch):
        tools = LaunchTools()
        client = make_client(tmp_path, monkeypatch, tools=tools)
        resp = self._post(client, {"id": "repo-shell"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True and body["pane_id"] == "w9:p1"
        # Executed with the operator's exact row, never client input.
        assert tools.launches == [("Repo shell", None, "bash")]

    def test_unknown_row_is_a_404_and_executes_nothing(self, tmp_path, monkeypatch):
        tools = LaunchTools()
        client = make_client(tmp_path, monkeypatch, tools=tools)
        resp = self._post(client, {"id": "no-such-row", "command": "rm -rf /"})
        assert resp.status_code == 404
        assert tools.launches == []
        lines = audit_lines(tmp_path / "state")
        assert lines[-1]["action"] == "operator.launch"
        assert lines[-1]["detail"]["outcome"] == "unknown"

    def test_no_prompts_row_requires_confirmation(self, tmp_path, monkeypatch):
        tools = LaunchTools()
        client = make_client(tmp_path, monkeypatch, tools=tools)
        resp = self._post(client, {"id": "danger-shell"})
        assert resp.status_code == 409
        assert resp.json() == {"detail": "confirmation_required"}
        assert tools.launches == []
        # The confirmed launch runs (its cwd is checked next).

    def test_missing_cwd_is_a_409(self, tmp_path, monkeypatch):
        tools = LaunchTools()
        client = make_client(tmp_path, monkeypatch, tools=tools)
        resp = self._post(client, {"id": "danger-shell", "confirm": True})
        assert resp.status_code == 409
        assert resp.json() == {"detail": "cwd_missing"}
        assert tools.launches == []

    def test_existing_cwd_launches_with_it(self, tmp_path, monkeypatch):
        tools = LaunchTools()
        client = make_client(tmp_path, monkeypatch, tools=tools)
        resp = self._post(client, {"id": "tty-shell"})
        assert resp.status_code == 200
        assert tools.launches == [("TTY shell", "/tmp", "zsh")]

    def test_mux_failure_is_a_502(self, tmp_path, monkeypatch):
        tools = LaunchTools()
        tools.fail_launch = True
        client = make_client(tmp_path, monkeypatch, tools=tools)
        resp = self._post(client, {"id": "repo-shell"})
        assert resp.status_code == 502

    def test_launch_is_a_classified_write(self):
        assert access.classify("POST", "/operator/launch") == "write"

    def test_every_launch_is_audited(self, tmp_path, monkeypatch):
        tools = LaunchTools()
        client = make_client(tmp_path, monkeypatch, tools=tools)
        self._post(client, {"id": "repo-shell"})
        lines = audit_lines(tmp_path / "state")
        assert lines[-1]["action"] == "operator.launch"
        assert lines[-1]["detail"] == {
            "outcome": "executed", "id": "repo-shell", "pane_id": "w9:p1"
        }


# ── POST /operator/preset — server-side key resolution ─────────────────────


class TestOperatorPreset:
    def _post(self, client, payload):
        return client.post("/operator/preset", json=payload)

    def test_resolves_keys_by_the_panes_agent_family(self, tmp_path, monkeypatch):
        tools = LaunchTools()
        client = make_client(tmp_path, monkeypatch, tools=tools)
        # The claude pane: the GLOBAL "Rechazar" preset applies.
        resp = self._post(client, {"pane_id": "w2:p1", "id": "rechazar"})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "pane_id": "w2:p1"}
        assert tools.key_sends == [("w2:p1", ["left", "enter"])]

    def test_scoped_preset_resolves_for_its_family(self, tmp_path, monkeypatch):
        tools = LaunchTools()
        client = make_client(tmp_path, monkeypatch, tools=tools)
        resp = self._post(client, {"pane_id": "w1:p9", "id": "aprobar-todo"})
        assert resp.status_code == 200
        assert tools.key_sends == [("w1:p9", ["right", "enter"])]

    def test_unknown_pane_is_a_404(self, tmp_path, monkeypatch):
        tools = LaunchTools()
        client = make_client(tmp_path, monkeypatch, tools=tools)
        resp = self._post(client, {"pane_id": "w0:p0", "id": "rechazar"})
        assert resp.status_code == 404
        assert tools.key_sends == []

    def test_unknown_preset_for_that_family_is_a_404(self, tmp_path, monkeypatch):
        tools = LaunchTools()
        client = make_client(tmp_path, monkeypatch, tools=tools)
        # "aprobar-todo" is scoped to opencode; the claude pane cannot
        # name it (scope substitution means it is not in its catalog).
        resp = self._post(client, {"pane_id": "w2:p1", "id": "aprobar-todo"})
        assert resp.status_code == 404
        assert tools.key_sends == []

    def test_preset_is_a_classified_write(self):
        assert access.classify("POST", "/operator/preset") == "write"

    def test_every_preset_is_audited(self, tmp_path, monkeypatch):
        tools = LaunchTools()
        client = make_client(tmp_path, monkeypatch, tools=tools)
        self._post(client, {"pane_id": "w1:p9", "id": "aprobar-todo"})
        lines = audit_lines(tmp_path / "state")
        assert lines[-1]["action"] == "operator.preset"
        assert lines[-1]["detail"] == {
            "outcome": "sent", "id": "aprobar-todo", "keys": ["right", "enter"]
        }
