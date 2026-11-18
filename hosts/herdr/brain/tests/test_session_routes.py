"""Session launch routes (parity-f7c S1, doc 03 §1–§2).

The PWA "New" sheet reuses the VOICE launch path server-side:
``GET /session/roots`` publishes the operator's allowed roots, the live
recents that fall inside them and the CLOSED harness list (the
transcript-readers registry canonical names); ``POST /session/new``
validates harness + cwd against exactly that configuration and then
calls the SAME ``BrainTools.create_session`` the voice gate replays —
never a parallel launch path. Fail-closed by design: no roots
configured ⇒ no launch at all. Every attempt is audited as
``session.new`` with device attribution and LENGTHS, never content.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import herdr_brain.server as server_module
from herdr_brain import access, pairing
from herdr_brain.config import Settings
from herdr_brain.herdr import AgentInfo
from herdr_brain.operator_config import OperatorConfig
from herdr_brain.server import create_app
from herdr_brain.tools import TOOLS_SCHEMA
from herdr_brain.transcript_readers.registry import default_registry

from tests.conftest import SETTINGS_KWARGS


# ── the stubbed tools double (hermetic: never a real herdr) ───────────


class SessionTools:
    """BrainTools double: ``create_session`` recorded, herd scripted.

    ``last_created`` mirrors the real property so the route's pane_id
    extraction runs the same code path production does."""

    def __init__(self, cfg=None, herd_entries=()):
        self.calls: list[dict] = []
        self.herd_entries = list(herd_entries)
        self.create_result = (
            "Panel creado: tab=t-1 pane=w3:p1 agente=x (claude). Tarea entregada."
        )

    def status_payload(self):
        return {"active": False}

    def herd(self):
        return [dict(entry) for entry in self.herd_entries]

    def create_session(self, agent_kind, title, task="", cwd=""):
        self.calls.append(
            {"agent_kind": agent_kind, "title": title, "task": task, "cwd": cwd}
        )
        return self.create_result

    def last_created(self):
        """A METHOD, like the real BrainTools surface the route reads."""
        return AgentInfo(
            pane_id="w3:p1", agent="x", status="idle", session_kind="",
            session_value="", cwd="", title="", focused=True,
        )


def roots_toml(*paths: Path) -> str:
    rows = []
    for path in paths:
        rows.append(f'[[roots]]\npath = "{path}"\n')
    return "\n".join(rows)


def make_client(
    tmp_path: Path,
    monkeypatch,
    *,
    tools: SessionTools | None = None,
    with_roots: bool = True,
    extra_herd: list[dict] | None = None,
) -> tuple[TestClient, SessionTools]:
    tools = tools or SessionTools(herd_entries=extra_herd or [])
    monkeypatch.setattr(server_module, "BrainTools", lambda cfg: tools)
    cfg_dir = tmp_path / "operator"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    if with_roots:
        root_a = tmp_path / "work"
        root_b = tmp_path / "code"
        root_a.mkdir(parents=True, exist_ok=True)
        root_b.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "roots.toml").write_text(roots_toml(root_a, root_b))
    settings = Settings(**{
        **SETTINGS_KWARGS,
        "state_dir": str(tmp_path / "state"),
        "audio_dir": str(tmp_path / "audio"),
    })
    app = create_app(settings=settings, operator_config=OperatorConfig(config_dir=cfg_dir))
    return TestClient(app), tools


def audit_lines(tmp_path: Path) -> list[dict]:
    path = tmp_path / "state" / "audit.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def herd_entry(cwd, age_s=10.0, pane_id="w1:p1"):
    return {"pane_id": pane_id, "cwd": str(cwd), "last_turn_age_s": age_s}


def pair_device(client: TestClient, state_dir: Path, label="phone") -> str:
    """The audit-route pattern: mint out-of-band, claim through /pair."""
    minted = pairing.PairingStore(state_dir).mint()
    resp = client.post("/pair", json={"code": minted["code"], "label": label})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


# ── GET /session/roots — configured surfaces, honestly degraded ───────


class TestSessionRootsRoute:
    def test_serves_roots_harnesses_and_recents(self, tmp_path, monkeypatch):
        root_a = (tmp_path / "work").resolve()
        root_b = (tmp_path / "code").resolve()
        client, _ = make_client(tmp_path, monkeypatch)
        resp = client.get("/session/roots")
        assert resp.status_code == 200
        body = resp.json()
        assert body["roots"] == [
            {"path": str(root_a), "label": ""},
            {"path": str(root_b), "label": ""},
        ]
        # The CLOSED harness list is the registry's canonical names.
        assert body["harnesses"] == default_registry().harnesses()
        assert body["recents"] == []

    def test_unconfigured_degrades_to_empty_lists(self, tmp_path, monkeypatch):
        client, _ = make_client(tmp_path, monkeypatch, with_roots=False)
        body = client.get("/session/roots").json()
        assert body["roots"] == []
        assert body["recents"] == []
        # The harness list does not depend on roots: it stays published.
        assert body["harnesses"] == default_registry().harnesses()

    def test_recents_are_in_root_cwds_only_deduplicated(self, tmp_path, monkeypatch):
        root_a = tmp_path / "work"
        inside = root_a / "proj"
        inside.mkdir(parents=True)
        herd = [
            herd_entry(inside, age_s=5.0, pane_id="w1:p1"),
            herd_entry(inside, age_s=50.0, pane_id="w2:p1"),   # same cwd, dup
            herd_entry(tmp_path / "elsewhere", age_s=1.0),     # outside roots
            herd_entry("", age_s=2.0),                          # no cwd
        ]
        client, _ = make_client(tmp_path, monkeypatch, extra_herd=herd)
        recents = client.get("/session/roots").json()["recents"]
        assert recents == [{"cwd": str(inside.resolve())}]

    def test_recents_newest_first_and_capped(self, tmp_path, monkeypatch):
        root = tmp_path / "work"
        herd = []
        for i in range(10):
            folder = root / f"p{i}"
            folder.mkdir(parents=True)
            herd.append(herd_entry(folder, age_s=float(i), pane_id=f"w{i}:p1"))
        client, _ = make_client(tmp_path, monkeypatch, extra_herd=herd)
        recents = client.get("/session/roots").json()["recents"]
        assert len(recents) == 8
        # age 0 is the newest live pane's cwd; the cap drops ages 8 and 9.
        assert recents[0] == {"cwd": str((root / "p0").resolve())}
        assert {"cwd": str((root / "p8").resolve())} not in recents
        assert {"cwd": str((root / "p9").resolve())} not in recents

    def test_roots_route_is_a_read(self):
        assert access.classify("GET", "/session/roots") == "read"


# ── POST /session/new — the 422 fail-closed matrix ────────────────────


class TestSessionNewRefusals:
    def test_unknown_harness_creates_nothing(self, tmp_path, monkeypatch):
        root = tmp_path / "work"
        client, tools = make_client(tmp_path, monkeypatch)
        resp = client.post("/session/new", json={
            "harness": "definitely-not-a-harness", "cwd": str(root),
        })
        assert resp.status_code == 422
        assert resp.json() == {"detail": "unknown_harness"}
        assert tools.calls == []

    def test_alias_names_are_not_the_closed_list(self, tmp_path, monkeypatch):
        # "oa" is an ALIAS in the registry, not a canonical harness: the
        # closed list answers "which connectors does this build have".
        root = tmp_path / "work"
        client, tools = make_client(tmp_path, monkeypatch)
        resp = client.post("/session/new", json={"harness": "oa", "cwd": str(root)})
        assert resp.status_code == 422
        assert resp.json() == {"detail": "unknown_harness"}
        assert tools.calls == []

    def test_cwd_outside_roots_is_refused(self, tmp_path, monkeypatch):
        client, tools = make_client(tmp_path, monkeypatch)
        resp = client.post("/session/new", json={
            "harness": "claude", "cwd": str(tmp_path / "elsewhere"),
        })
        assert resp.status_code == 422
        assert resp.json() == {"detail": "root_not_allowed"}
        assert tools.calls == []

    def test_dotdot_escape_is_refused_after_resolve(self, tmp_path, monkeypatch):
        root = tmp_path / "work"
        escape = root / ".." / ".."
        client, tools = make_client(tmp_path, monkeypatch)
        resp = client.post("/session/new", json={"harness": "claude", "cwd": str(escape)})
        assert resp.status_code == 422
        assert resp.json() == {"detail": "root_not_allowed"}
        assert tools.calls == []

    def test_symlink_escape_is_refused_after_resolve(self, tmp_path, monkeypatch):
        root = tmp_path / "work"
        client, tools = make_client(tmp_path, monkeypatch)
        link = root / "escape"
        link.symlink_to(tmp_path)  # points OUTSIDE every configured root
        resp = client.post("/session/new", json={"harness": "claude", "cwd": str(link)})
        assert resp.status_code == 422
        assert resp.json() == {"detail": "root_not_allowed"}
        assert tools.calls == []

    def test_symlink_that_stays_inside_a_root_is_allowed(self, tmp_path, monkeypatch):
        client, tools = make_client(tmp_path, monkeypatch)
        root = tmp_path / "work"
        real = root / "real"
        real.mkdir()
        link = root / "link"
        link.symlink_to(real)
        resp = client.post("/session/new", json={"harness": "claude", "cwd": str(link)})
        assert resp.status_code == 200
        # create_session received the RESOLVED cwd, not the symlink spelling.
        assert tools.calls[0]["cwd"] == str(real.resolve())

    def test_missing_directory_inside_a_root_is_refused(self, tmp_path, monkeypatch):
        root = tmp_path / "work"
        client, tools = make_client(tmp_path, monkeypatch)
        resp = client.post("/session/new", json={
            "harness": "claude", "cwd": str(root / "not-there"),
        })
        assert resp.status_code == 422
        assert resp.json() == {"detail": "root_not_allowed"}
        assert tools.calls == []

    def test_unconfigured_roots_refuse_every_launch(self, tmp_path, monkeypatch):
        client, tools = make_client(tmp_path, monkeypatch, with_roots=False)
        resp = client.post("/session/new", json={
            "harness": "claude", "cwd": str(tmp_path),
        })
        assert resp.status_code == 422
        assert resp.json() == {"detail": "roots_unconfigured"}
        assert tools.calls == []

    def test_new_route_is_a_classified_write(self):
        assert access.classify("POST", "/session/new") == "write"


# ── POST /session/new — the success path through create_session ───────


class TestSessionNewSuccess:
    def test_calls_create_session_with_the_exact_args(self, tmp_path, monkeypatch):
        root = (tmp_path / "work").resolve()
        client, tools = make_client(tmp_path, monkeypatch)
        resp = client.post("/session/new", json={
            "harness": "claude", "cwd": str(root),
            "title": "Refactor", "task": "corre los tests",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["pane_id"] == "w3:p1"
        assert body["result"] == tools.create_result
        assert tools.calls == [{
            "agent_kind": "claude",
            "title": "Refactor",
            "task": "corre los tests",
            "cwd": str(root),
        }]

    def test_title_defaults_to_harness_and_folder(self, tmp_path, monkeypatch):
        root = tmp_path / "work"
        client, tools = make_client(tmp_path, monkeypatch)
        resp = client.post("/session/new", json={"harness": "claude", "cwd": str(root)})
        assert resp.status_code == 200
        assert tools.calls[0]["title"] == "claude · work"
        assert tools.calls[0]["task"] == ""

    def test_harness_is_normalized_to_the_canonical_name(self, tmp_path, monkeypatch):
        root = tmp_path / "work"
        client, tools = make_client(tmp_path, monkeypatch)
        resp = client.post("/session/new", json={"harness": "  Claude ", "cwd": str(root)})
        assert resp.status_code == 200
        assert tools.calls[0]["agent_kind"] == "claude"

    def test_a_creation_error_string_maps_to_a_502(self, tmp_path, monkeypatch):
        root = tmp_path / "work"
        tools = SessionTools()
        tools.create_result = "error creating tab: mux refused"
        client, _ = make_client(tmp_path, monkeypatch, tools=tools)
        resp = client.post("/session/new", json={"harness": "claude", "cwd": str(root)})
        assert resp.status_code == 502


# ── the audit line: device attribution, lengths never content ─────────


class TestSessionNewAudit:
    def test_success_line_carries_device_and_lengths_only(self, tmp_path, monkeypatch):
        root = (tmp_path / "work").resolve()
        client, _ = make_client(tmp_path, monkeypatch)
        token = pair_device(client, tmp_path / "state")
        resp = client.post("/session/new", json={
            "harness": "claude", "cwd": str(root),
            "title": "Secreto", "task": "borra la base de datos SECRETOKEN",
        }, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        lines = [l for l in audit_lines(tmp_path) if l["action"] == "session.new"]
        assert len(lines) == 1
        line = lines[0]
        assert line["device"] == "phone"
        assert line["detail"]["outcome"] == "executed"
        assert line["detail"]["harness"] == "claude"
        assert line["detail"]["cwd"] == str(root)
        assert line["detail"]["chars"] == {"title": 7, "task": 33}
        assert line["detail"]["pane_id"] == "w3:p1"
        # Content discipline: neither the title nor the task text — nor
        # anything derived from them beyond the lengths — is on the line.
        assert "Secreto" not in json.dumps(line)
        assert "SECRETOKEN" not in json.dumps(line)

    def test_refusals_audit_the_outcome_only(self, tmp_path, monkeypatch):
        client, _ = make_client(tmp_path, monkeypatch)
        client.post("/session/new", json={"harness": "nope", "cwd": "/tmp"})
        lines = [l for l in audit_lines(tmp_path) if l["action"] == "session.new"]
        assert len(lines) == 1
        assert lines[0]["detail"] == {"outcome": "unknown_harness"}


# ── PWA-only: the route never joins the LLM tool surface ──────────────


class TestSessionNewNotInTheToolSchema:
    def test_session_launch_is_not_a_llm_tool(self):
        names = {tool["function"]["name"] for tool in TOOLS_SCHEMA}
        assert "session_new" not in names
        assert "launch_session" not in names
        # The voice path keeps its own gated create_session, unchanged.
        assert "create_session" in names
