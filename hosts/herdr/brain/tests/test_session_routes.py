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
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

import herdr_brain.server as server_module
from herdr_brain import access, pairing
from herdr_brain.config import Settings
from herdr_brain.herdr import AgentInfo, HerdrError, PaneInfo, WorkspaceInfo
from herdr_brain.launch_receipts import (
    LAUNCH_RECEIPTS_FILENAME,
    LAUNCH_RECEIPT_TTL_S,
    LaunchReceiptStore,
)
from herdr_brain.operator_config import OperatorConfig
from herdr_brain.server import create_app
from herdr_brain.tools import TOOLS_SCHEMA
from herdr_brain.transcript_readers.registry import default_registry

from tests.conftest import SETTINGS_KWARGS


# ── the stubbed tools double (hermetic: never a real herdr) ───────────


class PaneListStub:
    """HerdrClient double for the T5b pane-list seam (route tests).

    Counts its calls so a test can pin "ONE pane-list call served ALL
    workspaces". Raises the scripted error instead of answering when
    one is set (the HerdrError → agent-pane fallback path)."""

    def __init__(self, panes=(), error: Exception | None = None):
        self.panes = list(panes)
        self.error = error
        self.calls = 0

    def list_panes(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.panes)


class SessionTools:
    """BrainTools double: ``create_session``/``resume_session`` recorded,
    herd scripted.

    ``last_created`` mirrors the real property so the route's pane_id
    extraction runs the same code path production does. ``dispatch``
    mirrors the replay seam the approve path rides."""

    def __init__(
        self,
        cfg=None,
        herd_entries=(),
        workspaces=(),
        panes=(),
        pane_error=None,
    ):
        self.calls: list[dict] = []
        self.resume_calls: list[dict] = []
        self.dispatches: list[dict] = []
        self.herd_entries = list(herd_entries)
        self.workspaces = list(workspaces)
        # T5b: the pane-list seam mirrors production's chain — the
        # client behind the mux, reached as ``tools._herdr``. Unset by
        # default: the existing T5 fixtures ride the agent-pane
        # fallback exactly as before.
        self.pane_client = None
        if panes or pane_error is not None:
            self.pane_client = PaneListStub(panes=panes, error=pane_error)
        self._herdr = self.pane_client
        self.create_result = (
            "Panel creado: tab=t-1 pane=w3:p1 agente=x (claude). Tarea entregada."
        )
        self.resume_result = (
            "Panel reanudado: tab=t-1 pane=w3:p1 claude sesión abc en /repo."
        )
        # S2 concurrency probe: keep create_session in flight long enough
        # for two overlapping requests to actually overlap.
        self.slow_s = 0.0

    def status_payload(self):
        return {"active": False}

    def herd(self):
        return [dict(entry) for entry in self.herd_entries]

    def list_workspaces(self):
        return list(self.workspaces)

    def create_session(self, agent_kind, title, task="", cwd="", workspace_id=None):
        row = {
            "agent_kind": agent_kind, "title": title, "task": task, "cwd": cwd
        }
        # Recorded only when set: the pre-S1b call shape stays byte-equal.
        if workspace_id:
            row["workspace_id"] = workspace_id
        self.calls.append(row)
        if self.slow_s:
            time.sleep(self.slow_s)
        return self.create_result

    def resume_session(
        self, harness, session_id, cwd="", title="", workspace_id=None
    ):
        row = {
            "harness": harness, "session_id": session_id, "cwd": cwd, "title": title
        }
        if workspace_id:
            row["workspace_id"] = workspace_id
        self.resume_calls.append(row)
        return self.resume_result

    def dispatch(self, name, arguments, target=None, **_kwargs):
        self.dispatches.append({"name": name, "arguments": dict(arguments), "target": target})
        if name == "create_session":
            return self.create_session(
                str(arguments.get("agent_kind", "")),
                str(arguments.get("title", "")),
                str(arguments.get("task", "")),
                str(arguments.get("cwd", "")),
            )
        if name == "resume_session":
            return self.resume_session(
                str(arguments.get("harness", "")),
                str(arguments.get("session_id", "")),
            )
        return f"error: unknown tool {name}"

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
    workspaces: list | None = None,
    panes: list | None = None,
    pane_error: Exception | None = None,
) -> tuple[TestClient, SessionTools]:
    tools = tools or SessionTools(
        herd_entries=extra_herd or [],
        workspaces=workspaces or [],
        panes=panes or [],
        pane_error=pane_error,
    )
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


# ── S2: idempotent launch — one request_id, one tab (doc 03 §4) ────────


def receipts_path(tmp_path: Path) -> Path:
    return tmp_path / "state" / LAUNCH_RECEIPTS_FILENAME


class TestSessionNewIdempotency:
    def test_same_request_id_twice_replays_one_create(self, tmp_path, monkeypatch):
        root = tmp_path / "work"
        client, tools = make_client(tmp_path, monkeypatch)
        body = {"harness": "claude", "cwd": str(root), "request_id": "rq-1"}
        first = client.post("/session/new", json=body)
        second = client.post("/session/new", json=body)
        assert first.status_code == 200
        assert second.status_code == 200
        # The SAME body — pane_id included — and only ONE launch.
        assert first.json() == second.json()
        assert len(tools.calls) == 1

    def test_concurrent_same_request_id_produces_one_create(self, tmp_path, monkeypatch):
        root = tmp_path / "work"
        tools = SessionTools()
        tools.slow_s = 0.3  # both requests are genuinely in flight
        client, _ = make_client(tmp_path, monkeypatch, tools=tools)
        body = {"harness": "claude", "cwd": str(root), "request_id": "rq-race"}
        app = client.app
        barrier = threading.Barrier(2, timeout=10)
        results: dict[int, object] = {}

        def post(tag: int) -> None:
            own = TestClient(app)  # one client per thread, same app
            barrier.wait()
            results[tag] = own.post("/session/new", json=body)

        threads = [threading.Thread(target=post, args=(i,)) for i in (1, 2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        assert results[1].status_code == 200
        assert results[2].status_code == 200
        assert results[1].json() == results[2].json()
        # The loser waited and read the receipt: exactly one create.
        assert len(tools.calls) == 1

    def test_without_request_id_every_post_launches(self, tmp_path, monkeypatch):
        # No id: no dedup — the pre-S2 behavior is unchanged.
        root = tmp_path / "work"
        client, tools = make_client(tmp_path, monkeypatch)
        body = {"harness": "claude", "cwd": str(root)}
        assert client.post("/session/new", json=body).status_code == 200
        assert client.post("/session/new", json=body).status_code == 200
        assert len(tools.calls) == 2

    def test_failed_launch_is_not_cached_retry_with_same_id(self, tmp_path, monkeypatch):
        root = tmp_path / "work"
        tools = SessionTools()
        tools.create_result = "error creating tab: mux refused"
        client, tools = make_client(tmp_path, monkeypatch, tools=tools)
        body = {"harness": "claude", "cwd": str(root), "request_id": "rq-e"}
        assert client.post("/session/new", json=body).status_code == 502
        # The failure left no receipt: the SAME id may launch again.
        tools.create_result = "Panel creado: tab=t-1 pane=w3:p1 agente=x (claude)."
        retry = client.post("/session/new", json=body)
        assert retry.status_code == 200
        assert len(tools.calls) == 2

    def test_refusal_writes_no_receipt_and_the_id_retries_clean(self, tmp_path, monkeypatch):
        # T1's validation order is unchanged: 422s happen BEFORE any
        # receipt exists, so a refused id is never poisoned.
        root = tmp_path / "work"
        client, tools = make_client(tmp_path, monkeypatch)
        refused = client.post("/session/new", json={
            "harness": "nope", "cwd": str(root), "request_id": "rq-v",
        })
        assert refused.status_code == 422
        assert not receipts_path(tmp_path).exists()
        fixed = client.post("/session/new", json={
            "harness": "claude", "cwd": str(root), "request_id": "rq-v",
        })
        assert fixed.status_code == 200
        assert len(tools.calls) == 1

    def test_replay_writes_no_second_audit_line(self, tmp_path, monkeypatch):
        root = tmp_path / "work"
        client, _ = make_client(tmp_path, monkeypatch)
        body = {"harness": "claude", "cwd": str(root), "request_id": "rq-a"}
        assert client.post("/session/new", json=body).status_code == 200
        assert client.post("/session/new", json=body).status_code == 200
        lines = [l for l in audit_lines(tmp_path) if l["action"] == "session.new"]
        # The first launch audits exactly as today; the replay nothing.
        assert len(lines) == 1
        assert lines[0]["detail"]["outcome"] == "executed"

    def test_expired_receipt_proceeds_and_prunes_the_file(self, tmp_path, monkeypatch):
        state = tmp_path / "state"
        state.mkdir(parents=True, exist_ok=True)
        stale_ts = time.time() - (LAUNCH_RECEIPT_TTL_S + 60)
        seed = {
            "version": 1,
            "receipts": {
                # the id under test: expired, so it must NOT replay
                "rq-old": {
                    "request_id": "rq-old",
                    "response": {"ok": True, "result": "stale", "pane_id": "w0:p9"},
                    "ts": stale_ts,
                },
                # an unrelated expired id: must leave the file for good
                "rq-older": {
                    "request_id": "rq-older",
                    "response": {"ok": True, "result": "stale", "pane_id": "w0:p8"},
                    "ts": stale_ts,
                },
            },
        }
        (state / LAUNCH_RECEIPTS_FILENAME).write_text(json.dumps(seed))
        root = tmp_path / "work"
        client, tools = make_client(tmp_path, monkeypatch)
        resp = client.post("/session/new", json={
            "harness": "claude", "cwd": str(root), "request_id": "rq-old",
        })
        assert resp.status_code == 200
        # The LIVE answer, not the expired receipt's stale pane.
        assert resp.json()["pane_id"] == "w3:p1"
        assert len(tools.calls) == 1
        saved = json.loads(receipts_path(tmp_path).read_text())
        assert "rq-older" not in saved["receipts"]
        assert saved["receipts"]["rq-old"]["response"]["pane_id"] == "w3:p1"


class TestLaunchReceiptStore:
    """Unit-level: the on-disk receipt store's own contract."""

    @staticmethod
    def _launch_recorder(launches: list, response: dict):
        def launch():
            launches.append(1)
            return dict(response)

        return launch

    def test_replay_returns_the_cached_response_without_relaunching(self, tmp_path):
        now = [1000.0]
        store = LaunchReceiptStore(tmp_path, clock=lambda: now[0])
        launches: list = []
        response = {"ok": True, "result": "Panel creado", "pane_id": "w3:p1"}
        first = store.launch_once("rq-1", self._launch_recorder(launches, response))
        replayed = store.launch_once("rq-1", self._launch_recorder(launches, response))
        assert first == replayed == response
        assert launches == [1]

    def test_receipt_file_is_0600_inside_a_0700_dir(self, tmp_path):
        store = LaunchReceiptStore(tmp_path)
        store.launch_once("rq-1", lambda: {"ok": True, "pane_id": "w3:p1"})
        path = tmp_path / LAUNCH_RECEIPTS_FILENAME
        assert path.stat().st_mode & 0o777 == 0o600
        assert tmp_path.stat().st_mode & 0o777 == 0o700

    def test_receipt_rows_carry_request_id_response_and_ts(self, tmp_path):
        now = [1234.5]
        store = LaunchReceiptStore(tmp_path, clock=lambda: now[0])
        store.launch_once("rq-shape", lambda: {"ok": True, "pane_id": "w3:p1"})
        raw = json.loads((tmp_path / LAUNCH_RECEIPTS_FILENAME).read_text())
        row = raw["receipts"]["rq-shape"]
        assert row["request_id"] == "rq-shape"
        assert row["response"] == {"ok": True, "pane_id": "w3:p1"}
        assert row["ts"] == 1234.5

    def test_entries_older_than_the_ttl_replay_nothing(self, tmp_path):
        now = [1000.0]
        store = LaunchReceiptStore(tmp_path, clock=lambda: now[0])
        launches: list = []
        response = {"ok": True, "pane_id": "w3:p1"}
        store.launch_once("rq-ttl", self._launch_recorder(launches, response))
        now[0] += LAUNCH_RECEIPT_TTL_S + 1  # past the window
        result = store.launch_once("rq-ttl", self._launch_recorder(launches, response))
        assert result == response
        assert launches == [1, 1]  # the second one launched for real

    def test_failed_launch_raises_through_and_records_nothing(self, tmp_path):
        store = LaunchReceiptStore(tmp_path)

        def exploding():
            raise RuntimeError("mux died mid-launch")

        try:
            store.launch_once("rq-boom", exploding)
        except RuntimeError:
            pass
        assert not (tmp_path / LAUNCH_RECEIPTS_FILENAME).exists()

    def test_failed_save_keeps_old_bytes_and_leaves_no_partial(self, tmp_path, monkeypatch):
        import os as os_mod

        store = LaunchReceiptStore(tmp_path)
        store.launch_once("rq-1", lambda: {"ok": True, "pane_id": "w3:p1"})
        path = tmp_path / LAUNCH_RECEIPTS_FILENAME
        before = path.read_bytes()

        def boom(src, dst):
            raise OSError("simulated crash between write and rename")

        monkeypatch.setattr(os_mod, "replace", boom)
        # A broken disk must not fail the response: the tab already
        # exists, so the receipt degrades to in-memory only.
        store.launch_once("rq-2", lambda: {"ok": True, "pane_id": "w9:p9"})
        assert path.read_bytes() == before  # previous state intact
        leftovers = [
            p.name for p in tmp_path.iterdir()
            if p.name != LAUNCH_RECEIPTS_FILENAME
        ]
        assert leftovers == []  # no partial file, no tmp residue

    def test_corrupt_file_degrades_to_no_dedup_and_heals_on_next_save(self, tmp_path):
        (tmp_path / LAUNCH_RECEIPTS_FILENAME).write_text("{ not json")
        store = LaunchReceiptStore(tmp_path)
        launches: list = []
        response = {"ok": True, "result": "Panel creado", "pane_id": "w3:p1"}
        first = store.launch_once("rq-heal", self._launch_recorder(launches, response))
        replayed = store.launch_once("rq-heal", self._launch_recorder(launches, response))
        assert first == replayed == response
        assert launches == [1]
        # The next successful save rewrote a VALID file over the junk.
        raw = json.loads((tmp_path / LAUNCH_RECEIPTS_FILENAME).read_text())
        assert "rq-heal" in raw["receipts"]


# ── S3: GET /sessions/recent — resumable conversations, masked ───────


def seed_recent_rows(cwd: str, count: int = 12, secret: str = "SECRETOKEN") -> list:
    # A real token shape so mask_text actually detects it (sk-/ghp-
    # style with a long tail — see secrets_mask's patterns).
    return [
        {
            "session_id": f"ses_x{i:02d}",
            "cwd": cwd,
            "title": f"Fix the API sk-{secret.lower()}{i:02d}abcdefghij",
            "last_ts": "2026-11-18T10:00:00Z",
        }
        for i in range(count)
    ]


class TestSessionsRecentRoute:
    def test_lists_masked_and_capped_rows_for_an_enabled_harness(self, tmp_path, monkeypatch):
        root = (tmp_path / "work").resolve()
        rows = seed_recent_rows(str(root))
        monkeypatch.setattr(server_module, "recent_sessions", lambda h, cap=10: rows)
        client, _ = make_client(tmp_path, monkeypatch)
        resp = client.get("/sessions/recent", params={"harness": "opencode"})
        assert resp.status_code == 200
        body = resp.json()
        # Only the evidence-backed harnesses are offered resume.
        assert body["harnesses"] == ["claude", "codex", "opencode", "pi"]
        assert len(body["sessions"]) == 10  # capped at 10 of 12
        first = body["sessions"][0]
        assert first["session_id"] == "ses_x00"
        assert first["cwd"] == str(root)
        assert first["last_ts"] == "2026-11-18T10:00:00Z"
        # Titles leave masked — a secret in the label never survives.
        assert "secretoken" not in json.dumps(body).lower()

    def test_out_of_root_cwds_are_not_offered(self, tmp_path, monkeypatch):
        root = tmp_path / "work"
        rows = [
            {"session_id": "in", "cwd": str(root), "title": "t", "last_ts": None},
            {"session_id": "out", "cwd": str(tmp_path / "elsewhere"), "title": "t", "last_ts": None},
        ]
        monkeypatch.setattr(server_module, "recent_sessions", lambda h, cap=10: rows)
        client, _ = make_client(tmp_path, monkeypatch)
        sessions = client.get("/sessions/recent", params={"harness": "claude"}).json()["sessions"]
        assert [row["session_id"] for row in sessions] == ["in"]

    def test_unknown_harness_lists_empty_not_an_error(self, tmp_path, monkeypatch):
        seen: list = []

        def boom(harness, cap=10):
            seen.append(harness)
            raise AssertionError("unknown harness must not touch the store")

        monkeypatch.setattr(server_module, "recent_sessions", boom)
        client, _ = make_client(tmp_path, monkeypatch)
        resp = client.get("/sessions/recent", params={"harness": "not-a-harness"})
        assert resp.status_code == 200
        assert resp.json()["sessions"] == []
        assert seen == []

    def test_disabled_harness_lists_empty(self, tmp_path, monkeypatch):
        # "grok" IS a registry harness but has NO verified resume form:
        # not offered, and its store is never read.
        seen: list = []

        def boom(harness, cap=10):
            seen.append(harness)
            raise AssertionError("a disabled harness must not touch the store")

        monkeypatch.setattr(server_module, "recent_sessions", boom)
        client, _ = make_client(tmp_path, monkeypatch)
        resp = client.get("/sessions/recent", params={"harness": "grok"})
        assert resp.status_code == 200
        assert resp.json()["sessions"] == []
        assert seen == []

    def test_unconfigured_roots_degrade_to_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            server_module, "recent_sessions",
            lambda h, cap=10: seed_recent_rows("/anywhere", count=2),
        )
        client, _ = make_client(tmp_path, monkeypatch, with_roots=False)
        body = client.get("/sessions/recent", params={"harness": "claude"}).json()
        assert body["sessions"] == []
        # The enabled harness list stays published (the picker needs it).
        assert body["harnesses"] == ["claude", "codex", "opencode", "pi"]

    def test_recent_route_is_a_read(self):
        assert access.classify("GET", "/sessions/recent") == "read"


# ── S3: POST /session/resume — the 422 fail-closed matrix ────────────


class TestSessionResumeRefusals:
    def test_unknown_harness_resumes_nothing(self, tmp_path, monkeypatch):
        root = tmp_path / "work"
        client, tools = make_client(tmp_path, monkeypatch)
        resp = client.post("/session/resume", json={
            "harness": "definitely-not-a-harness", "session_id": "abc",
        })
        assert resp.status_code == 422
        assert resp.json() == {"detail": "unknown_harness"}
        assert tools.resume_calls == []

    def test_registry_harness_without_verified_resume_is_refused(self, tmp_path, monkeypatch):
        # "grok" is launchable but has no verified resume form.
        root = tmp_path / "work"
        client, tools = make_client(tmp_path, monkeypatch)
        resp = client.post("/session/resume", json={
            "harness": "grok", "session_id": "abc",
        })
        assert resp.status_code == 422
        assert resp.json() == {"detail": "resume_not_enabled"}
        assert tools.resume_calls == []

    def test_unknown_session_is_refused(self, tmp_path, monkeypatch):
        root = tmp_path / "work"
        monkeypatch.setattr(server_module, "resume_target", lambda h, s: None)
        client, tools = make_client(tmp_path, monkeypatch)
        resp = client.post("/session/resume", json={
            "harness": "claude", "session_id": "missing",
        })
        assert resp.status_code == 422
        assert resp.json() == {"detail": "unknown_session"}
        assert tools.resume_calls == []

    def test_store_without_a_cwd_is_cwd_unknown_never_a_guess(self, tmp_path, monkeypatch):
        root = tmp_path / "work"
        monkeypatch.setattr(
            server_module, "resume_target",
            lambda h, s: {"cwd": None, "title": "T"},
        )
        client, tools = make_client(tmp_path, monkeypatch)
        resp = client.post("/session/resume", json={
            "harness": "claude", "session_id": "abc",
        })
        assert resp.status_code == 422
        assert resp.json() == {"detail": "cwd_unknown"}
        assert tools.resume_calls == []

    def test_session_cwd_outside_roots_is_refused(self, tmp_path, monkeypatch):
        root = tmp_path / "work"
        monkeypatch.setattr(
            server_module, "resume_target",
            lambda h, s: {"cwd": str(tmp_path / "elsewhere"), "title": "T"},
        )
        client, tools = make_client(tmp_path, monkeypatch)
        resp = client.post("/session/resume", json={
            "harness": "claude", "session_id": "abc",
        })
        assert resp.status_code == 422
        assert resp.json() == {"detail": "root_not_allowed"}
        assert tools.resume_calls == []

    def test_unconfigured_roots_refuse_every_resume(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            server_module, "resume_target",
            lambda h, s: {"cwd": "/repo", "title": "T"},
        )
        client, tools = make_client(tmp_path, monkeypatch, with_roots=False)
        resp = client.post("/session/resume", json={
            "harness": "claude", "session_id": "abc",
        })
        assert resp.status_code == 422
        assert resp.json() == {"detail": "roots_unconfigured"}
        assert tools.resume_calls == []

    def test_refusals_audit_the_outcome_only(self, tmp_path, monkeypatch):
        monkeypatch.setattr(server_module, "resume_target", lambda h, s: None)
        client, _ = make_client(tmp_path, monkeypatch)
        client.post("/session/resume", json={"harness": "claude", "session_id": "x"})
        lines = [l for l in audit_lines(tmp_path) if l["action"] == "session.resume"]
        assert len(lines) == 1
        assert lines[0]["detail"] == {"outcome": "unknown_session"}

    def test_resume_route_is_a_classified_write(self):
        # A resume launches a real agent in a real tab: it must sit
        # behind the SAME device gate as /session/new.
        assert access.classify("POST", "/session/resume") == "write"


# ── S3: POST /session/resume — the success path ──────────────────────


class TestSessionResumeSuccess:
    def _seed(self, tmp_path, monkeypatch, cwd=None, title="Fix the login SECRETOKEN"):
        root = (tmp_path / "work").resolve()
        target_cwd = cwd if cwd is not None else str(root)
        monkeypatch.setattr(
            server_module, "resume_target",
            lambda h, s: {"cwd": target_cwd, "title": title},
        )
        return root

    def test_resumes_through_the_tools_seam_with_exact_args(self, tmp_path, monkeypatch):
        root = self._seed(tmp_path, monkeypatch)
        client, tools = make_client(tmp_path, monkeypatch)
        resp = client.post("/session/resume", json={
            "harness": "claude", "session_id": "abc-123",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["pane_id"] == "w3:p1"
        assert body["result"] == tools.resume_result
        assert tools.resume_calls == [{
            "harness": "claude",
            "session_id": "abc-123",
            "cwd": str(root),
            "title": "Fix the login SECRETOKEN",
        }]
        assert tools.calls == []  # never the create path

    def test_harness_is_normalized_and_id_stripped(self, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        client, tools = make_client(tmp_path, monkeypatch)
        resp = client.post("/session/resume", json={
            "harness": "  Claude ", "session_id": "  abc-123  ",
        })
        assert resp.status_code == 200
        assert tools.resume_calls[0]["harness"] == "claude"
        assert tools.resume_calls[0]["session_id"] == "abc-123"

    def test_audit_line_carries_device_and_no_content(self, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        client, _ = make_client(tmp_path, monkeypatch)
        token = pair_device(client, tmp_path / "state")
        resp = client.post("/session/resume", json={
            "harness": "claude", "session_id": "abc-123",
        }, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        lines = [l for l in audit_lines(tmp_path) if l["action"] == "session.resume"]
        assert len(lines) == 1
        line = lines[0]
        assert line["device"] == "phone"
        assert line["detail"]["outcome"] == "executed"
        assert line["detail"]["harness"] == "claude"
        assert line["detail"]["session_id"] == "abc-123"
        assert line["detail"]["pane_id"] == "w3:p1"
        # Content discipline: the session title never reaches the line.
        assert "SECRETOKEN" not in json.dumps(line)
        assert "Fix the login" not in json.dumps(line)

    def test_a_resume_error_string_maps_to_a_502(self, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        tools = SessionTools()
        tools.resume_result = "error typing the resume command for claude: run boom"
        client, _ = make_client(tmp_path, monkeypatch, tools=tools)
        resp = client.post("/session/resume", json={
            "harness": "claude", "session_id": "abc",
        })
        assert resp.status_code == 502
        lines = [l for l in audit_lines(tmp_path) if l["action"] == "session.resume"]
        assert lines[-1]["detail"]["outcome"] == "error"

    def test_same_request_id_replays_one_resume(self, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        client, tools = make_client(tmp_path, monkeypatch)
        body = {"harness": "claude", "session_id": "abc", "request_id": "rq-r"}
        first = client.post("/session/resume", json=body)
        second = client.post("/session/resume", json=body)
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
        assert len(tools.resume_calls) == 1
        # One launch, one audit line — the replay writes nothing.
        lines = [l for l in audit_lines(tmp_path) if l["action"] == "session.resume"]
        assert len(lines) == 1

    def test_refusal_leaves_no_receipt_and_the_id_retries_clean(self, tmp_path, monkeypatch):
        root = self._seed(tmp_path, monkeypatch)
        client, tools = make_client(tmp_path, monkeypatch)
        refused = client.post("/session/resume", json={
            "harness": "grok", "session_id": "abc", "request_id": "rq-rv",
        })
        assert refused.status_code == 422
        assert not receipts_path(tmp_path).exists()
        ok = client.post("/session/resume", json={
            "harness": "claude", "session_id": "abc", "request_id": "rq-rv",
        })
        assert ok.status_code == 200
        assert len(tools.resume_calls) == 1


# ── S3: the voice gate — resume_session mirrors create_session ───────


class TestResumeVoiceGate:
    @staticmethod
    def _make_llm(settings, stub, responses):
        from tests.test_llm import make_brain

        return make_brain(settings, stub, responses=responses)

    def test_resume_call_freezes_a_gate_and_launches_nothing(
        self, settings, make_stub
    ):
        from tests.test_llm import text_response, tool_call_response
        from tests.test_tools_create_session import CreateStub

        stub = CreateStub(agents=make_stub()._agents)
        llm, _ = self._make_llm(settings, stub, responses=[
            tool_call_response(
                "r1", "resume_session",
                {"harness": "claude", "session_id": "abc-123"},
            ),
            text_response("Voy a retomar esa conversación."),
        ])
        result = llm.ask("retoma la conversación de claude")
        gate = llm._approval_store.current(None)
        assert gate is not None and gate.state == "proposed"
        assert gate.tool == "resume_session"
        assert gate.action.agent_kind == "claude"
        assert gate.action.task == "abc-123"
        assert result["approval"] is not None
        # Nothing launched: the gate IS the freeze.
        assert stub.tab_calls == [] and stub.pane_run_calls == []

    def test_the_tool_message_says_pending_not_resumed(self, settings, make_stub):
        from tests.test_llm import text_response, tool_call_response
        from tests.test_tools_create_session import CreateStub

        stub = CreateStub(agents=make_stub()._agents)
        llm, _ = self._make_llm(settings, stub, responses=[
            tool_call_response(
                "r1", "resume_session", {"harness": "pi", "session_id": "01a1"},
            ),
            text_response("Pendiente tu aprobación."),
        ])
        llm.ask("retoma la sesión de pi")
        tool_messages = [
            m for m in llm._client.create_kwargs[-1]["messages"]
            if m.get("role") == "tool"
        ]
        content = tool_messages[0]["content"]
        assert "pending user approval" in content
        assert "NOT" in content and "resumed" in content

    def test_unverified_harness_opens_no_gate(self, settings, make_stub):
        from tests.test_llm import text_response, tool_call_response
        from tests.test_tools_create_session import CreateStub

        stub = CreateStub(agents=make_stub()._agents)
        llm, _ = self._make_llm(settings, stub, responses=[
            tool_call_response(
                "r1", "resume_session", {"harness": "grok", "session_id": "abc"},
            ),
            text_response("Ese no se puede retomar."),
        ])
        result = llm.ask("retoma la de grok")
        assert result["approval"] is None
        tool_messages = [
            m for m in llm._client.create_kwargs[-1]["messages"]
            if m.get("role") == "tool"
        ]
        assert tool_messages[0]["content"].startswith(
            "error: resume is not verified for grok"
        )

    def test_no_store_blocks_fail_safe(self, settings, make_stub):
        from herdr_brain.llm import BrainLLM
        from herdr_brain.tools import BrainTools
        from tests.test_llm import ScriptedLLM, text_response, tool_call_response
        from tests.test_tools_create_session import CreateStub

        stub = CreateStub(agents=make_stub()._agents)
        tools = BrainTools(settings, herdr=stub)
        llm = BrainLLM(
            settings,
            tools,
            client=ScriptedLLM([
                tool_call_response(
                    "r1", "resume_session", {"harness": "claude", "session_id": "x"},
                ),
                text_response("No pude retomarla."),
            ]),
        )
        result = llm.ask("retoma")
        assert stub.tab_calls == []
        assert result["approval"] is None
        tool_messages = [
            m for m in llm._client.create_kwargs[-1]["messages"]
            if m.get("role") == "tool"
        ]
        assert tool_messages[0]["content"].startswith("error: resume_session is blocked")

    def test_missing_args_mirror_the_tool_error(self, settings, make_stub):
        from tests.test_llm import text_response, tool_call_response
        from tests.test_tools_create_session import CreateStub

        stub = CreateStub(agents=make_stub()._agents)
        llm, _ = self._make_llm(settings, stub, responses=[
            tool_call_response("r1", "resume_session", {"harness": "claude"}),
            text_response("Falta la sesión."),
        ])
        result = llm.ask("retoma")
        assert result["approval"] is None
        tool_messages = [
            m for m in llm._client.create_kwargs[-1]["messages"]
            if m.get("role") == "tool"
        ]
        assert tool_messages[0]["content"] == "error: harness and session_id are required"


# ── S3: the approve replay dispatches the frozen resume ───────────────


class TestResumeServerReplay:
    def test_approve_dispatches_resume_and_switches_the_call(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        from tests.test_server import FakeLLM, FakeTTS

        tools = SessionTools()
        monkeypatch.setattr(server_module, "BrainTools", lambda cfg: tools)
        cfg_dir = tmp_path / "operator"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        settings = Settings(**{
            **SETTINGS_KWARGS,
            "state_dir": str(tmp_path / "state"),
            "audio_dir": str(tmp_path / "audio"),
        })
        app = create_app(
            settings=settings,
            operator_config=OperatorConfig(config_dir=cfg_dir),
            llm_factory=lambda c, t: FakeLLM(result={
                "answer": "Conversación reanudada.",
                "pane_id": None, "agent": None, "session_id": "s1",
            }),
            tts_renderer=FakeTTS(),
        )
        gate = app.state.approval_store.propose(
            "s1", text="", agent="claude", tool="resume_session",
            agent_kind="claude", title="", task="abc-123",
        )
        resp = TestClient(app).post(f"/approval/{gate.gate_id}/approve")
        assert resp.status_code == 200
        assert len(tools.dispatches) == 1
        sent = tools.dispatches[0]
        assert sent["name"] == "resume_session"
        assert sent["arguments"] == {"harness": "claude", "session_id": "abc-123"}
        assert sent["target"] is None  # no pane re-resolution on resume gates
        assert tools.resume_calls == [{
            "harness": "claude", "session_id": "abc-123", "cwd": "", "title": "",
        }]
        body = resp.json()
        assert body["answer"] == "Conversación reanudada."
        # The reopened panel becomes the call's target, like a create.
        assert body["switch_pane"]["pane_id"] == "w3:p1"
        assert app.state.approval_store.get(gate.gate_id).state == "approved"

    def test_resume_gate_payload_rides_the_create_card_shape(self):
        from herdr_brain.approval import ApprovalGateStore
        from herdr_brain.server import approval_payload

        store = ApprovalGateStore()
        gate = store.propose(
            "s1", text="", agent="claude", tool="resume_session",
            agent_kind="claude", title="", task="abc-123",
        )
        payload = approval_payload(gate, 60, now=gate.created_at)
        assert payload == {
            "gate_id": gate.gate_id,
            "tool": "resume_session",
            "agent": "claude",
            "title": "",
            "task": "abc-123",
            "cwd": "",
            "timeout_ms": None,
            "expires_in_s": 60,
        }
        assert "pane_id" not in payload and "text" not in payload


# ── S3: BrainTools.resume_session — the launch seam itself ───────────


class TestToolsResumeSession:
    @staticmethod
    def _tools(settings, stub):
        from herdr_brain.tools import BrainTools

        return BrainTools(settings, herdr=stub)

    def test_claude_resume_creates_one_tab_and_types_the_verified_argv(
        self, settings, make_stub
    ):
        from tests.test_tools_create_session import NEW_PANE, NEW_TAB, CreateStub

        stub = CreateStub(agents=make_stub()._agents)
        tools = self._tools(settings, stub)
        result = tools.resume_session("claude", "abc-123", cwd="/repo", title="Fix login")
        assert result.startswith("Panel reanudado")
        assert f"tab={NEW_TAB}" in result and f"pane={NEW_PANE}" in result
        # ONE tab, scoped to the session's folder.
        assert stub.tab_calls == [{"label": "Fix login", "cwd": "/repo"}]
        # The VERIFIED argv, typed verbatim into the pane's shell.
        assert stub.pane_run_calls == [
            {"pane_id": NEW_PANE, "command": "claude --resume abc-123"}
        ]
        assert stub.start_calls == []
        created = tools.last_created()
        assert created.pane_id == NEW_PANE
        assert created.session_value == "abc-123"

    def test_opencode_resume_types_oa_with_the_session_flag(
        self, settings, make_stub
    ):
        from tests.test_tools_create_session import NEW_PANE, CreateStub

        stub = CreateStub(agents=make_stub()._agents)
        tools = self._tools(settings, stub)
        result = tools.resume_session("opencode", "ses_abc123XYZ", cwd="/repo")
        assert result.startswith("Panel reanudado")
        assert stub.pane_run_calls == [
            {"pane_id": NEW_PANE, "command": "oa -s ses_abc123XYZ"}
        ]
        # The same readiness discipline as a created opencode panel.
        assert stub.agent_wait_calls and stub.agent_wait_calls[0]["pane_id"] == NEW_PANE

    def test_pi_and_codex_argv_come_from_the_table(self, settings, make_stub):
        from tests.test_tools_create_session import CreateStub

        stub = CreateStub(agents=make_stub()._agents)
        tools = self._tools(settings, stub)
        tools.resume_session("pi", "01a1135f-d580-73a9-9f65-dfac5f4c88fd", cwd="/repo")
        tools.resume_session(
            "codex", "0b9edffe-2e2e-4ef8-a56f-f5c21621a972", cwd="/repo"
        )
        commands = [call["command"] for call in stub.pane_run_calls]
        assert commands == [
            "pi --session 01a1135f-d580-73a9-9f65-dfac5f4c88fd",
            "codex resume 0b9edffe-2e2e-4ef8-a56f-f5c21621a972",
        ]

    def test_a_sid_with_shell_metacharacters_is_quoted_not_interpreted(
        self, settings, make_stub
    ):
        from tests.test_tools_create_session import CreateStub

        stub = CreateStub(agents=make_stub()._agents)
        tools = self._tools(settings, stub)
        result = tools.resume_session("claude", "abc; rm -rf /", cwd="/repo")
        # Quoted for the shell: the typed line can never run a second command.
        assert stub.pane_run_calls == [
            {"pane_id": stub.tab_calls[0] and "w2:p3", "command": "claude --resume 'abc; rm -rf /'"}
        ]
        assert result.startswith("Panel reanudado")

    def test_cwd_derived_from_the_store_when_not_given(self, settings, make_stub, monkeypatch):
        import herdr_brain.tools as tools_module
        from tests.test_tools_create_session import CreateStub

        stub = CreateStub(agents=make_stub()._agents)
        monkeypatch.setattr(
            tools_module, "resume_target",
            lambda h, s: {"cwd": "/from/store", "title": "Store title"},
        )
        tools = self._tools(settings, stub)
        result = tools.resume_session("claude", "abc")
        assert result.startswith("Panel reanudado")
        assert stub.tab_calls == [{"label": "Store title", "cwd": "/from/store"}]

    def test_unknown_cwd_refuses_before_any_tab_exists(self, settings, make_stub, monkeypatch):
        import herdr_brain.tools as tools_module
        from tests.test_tools_create_session import CreateStub

        stub = CreateStub(agents=make_stub()._agents)
        monkeypatch.setattr(
            tools_module, "resume_target", lambda h, s: {"cwd": None, "title": "T"},
        )
        tools = self._tools(settings, stub)
        result = tools.resume_session("claude", "abc")
        assert result.startswith("error")
        assert "cwd_unknown" in result
        assert stub.tab_calls == [] and stub.pane_run_calls == []

    def test_unverified_harness_never_opens_a_tab(self, settings, make_stub):
        from tests.test_tools_create_session import CreateStub

        stub = CreateStub(agents=make_stub()._agents)
        tools = self._tools(settings, stub)
        result = tools.resume_session("cursor", "abc", cwd="/repo")
        assert result.startswith("error: resume is not verified for cursor")
        assert stub.tab_calls == []

    def test_a_failed_command_type_is_an_honest_error_never_a_bare_shell(
        self, settings, make_stub
    ):
        from tests.test_tools_create_session import NEW_PANE, NEW_TAB, CreateStub

        stub = CreateStub(fail_run=True, agents=make_stub()._agents)
        tools = self._tools(settings, stub)
        result = tools.resume_session("claude", "abc", cwd="/repo")
        assert result.startswith("error typing the resume command")
        # The ids ride the error: the tab exists, the agent does not.
        assert NEW_TAB in result and NEW_PANE in result
        assert stub.tab_calls  # the tab WAS created
        assert "run boom" in result

    def test_an_undelivered_resume_command_closes_the_orphan(
        self, settings, make_stub
    ):
        # S4: the tab is THIS operation's own creation and the resume
        # command was never typed — a probe with no agent proves the pane
        # empty, so its shell is exited to close the orphan tab.
        from tests.test_tools_create_session import NEW_PANE, CreateStub

        stub = CreateStub(fail_run=True, agents=make_stub()._agents)
        tools = self._tools(settings, stub)
        result = tools.resume_session("claude", "abc", cwd="/repo")
        assert result.startswith("error typing the resume command")
        assert "were created and closed" in result
        assert "no agent was ever on the pane" in result
        assert stub.pane_run_calls == [
            {"pane_id": NEW_PANE, "command": "claude --resume abc"},
            {"pane_id": NEW_PANE, "command": "exit"},
        ]

    def test_a_failed_closing_keeps_the_resume_tab_named(self, settings, make_stub):
        from tests.test_tools_create_session import NEW_PANE, NEW_TAB, CreateStub

        stub = CreateStub(fail_run=True, fail_exit=True, agents=make_stub()._agents)
        tools = self._tools(settings, stub)
        result = tools.resume_session("claude", "abc", cwd="/repo")
        assert result.startswith("error typing the resume command")
        assert "closing failed: exit boom" in result
        assert NEW_TAB in result and NEW_PANE in result

    def test_the_tab_label_is_masked_and_the_title_falls_back(
        self, settings, make_stub
    ):
        from tests.test_tools_create_session import CreateStub

        stub = CreateStub(agents=make_stub()._agents)
        tools = self._tools(settings, stub)
        tools.resume_session(
            "claude", "abc", cwd="/repo", title="con sk-1234567890abcdefghij0 dentro"
        )
        label = stub.tab_calls[0]["label"]
        assert "sk-1234567890abcdefghij0" not in label
        tools.resume_session("claude", "def", cwd="/repo")
        assert stub.tab_calls[1]["label"]  # non-empty fallback label


# ── S3: the verified resume table is evidence-annotated ──────────────


class TestResumeTable:
    def test_every_entry_carries_an_evidence_string(self):
        from herdr_brain.tools import RESUME_COMMANDS

        assert set(RESUME_COMMANDS) == {"claude", "codex", "opencode", "pi"}
        for harness, entry in RESUME_COMMANDS.items():
            argv, evidence = entry
            assert "{" in argv and "}" in argv, "argv is a template"
            assert harness in argv.split()[0] or argv.startswith(harness) or argv.startswith("oa"), argv
            assert isinstance(evidence, str) and "--help" in evidence, (
                "evidence must name its source"
            )

    def test_the_schema_offers_resume_session_with_both_args(self):
        names = {tool["function"]["name"] for tool in TOOLS_SCHEMA}
        assert "resume_session" in names
        entry = next(
            tool for tool in TOOLS_SCHEMA
            if tool["function"]["name"] == "resume_session"
        )
        params = entry["function"]["parameters"]
        assert set(params["required"]) == {"harness", "session_id"}


# ── S1b (L11): every workspace is a space; launches can land inside one ─


SPACES = [
    WorkspaceInfo(workspace_id="w1", label="main", number=1, focused=True),
    WorkspaceInfo(workspace_id="w9", label="vacía", number=2, focused=False),
]


class TestSessionRootsSpaces:
    def test_lists_every_workspace_including_one_without_live_panes(
        self, tmp_path, monkeypatch
    ):
        # L11: the picker must show ALL spaces, not only the live ones —
        # a workspace with no agent pane is LISTED with a null cwd.
        root = tmp_path / "work"
        inside = root / "proj"
        inside.mkdir(parents=True)
        herd = [
            {"pane_id": "w1:p2", "cwd": str(inside), "last_turn_age_s": 5.0,
             "focused": False},
        ]
        client, _ = make_client(
            tmp_path, monkeypatch, extra_herd=herd, workspaces=SPACES
        )
        spaces = client.get("/session/roots").json()["spaces"]
        assert spaces == [
            {
                "workspace_id": "w1",
                "label": "main",
                "cwd": str(inside.resolve()),
                "inside_roots": True,
                "focused": True,
            },
            {
                "workspace_id": "w9",
                "label": "vacía",
                "cwd": None,
                "inside_roots": False,
                "focused": False,
            },
        ]

    def test_an_outside_roots_space_is_still_listed(self, tmp_path, monkeypatch):
        herd = [
            {"pane_id": "w1:p1", "cwd": str(tmp_path / "elsewhere"),
             "last_turn_age_s": 1.0, "focused": False},
        ]
        client, _ = make_client(
            tmp_path, monkeypatch, extra_herd=herd, workspaces=SPACES
        )
        spaces = client.get("/session/roots").json()["spaces"]
        assert spaces[0]["cwd"] == str((tmp_path / "elsewhere").resolve())
        assert spaces[0]["inside_roots"] is False  # listed, only unpickable

    def test_the_focused_pane_wins_the_cwd_derivation(self, tmp_path, monkeypatch):
        root = tmp_path / "work"
        focused_dir = root / "focused"
        stale_dir = root / "stale"
        focused_dir.mkdir(parents=True)
        stale_dir.mkdir(parents=True)
        herd = [
            {"pane_id": "w1:p1", "cwd": str(stale_dir), "last_turn_age_s": 1.0,
             "focused": False},
            # fresher by age but NOT focused: the focused pane wins.
            {"pane_id": "w1:p2", "cwd": str(focused_dir), "last_turn_age_s": 90.0,
             "focused": True},
        ]
        client, _ = make_client(
            tmp_path, monkeypatch, extra_herd=herd, workspaces=SPACES
        )
        spaces = client.get("/session/roots").json()["spaces"]
        assert spaces[0]["cwd"] == str(focused_dir.resolve())

    def test_without_focus_the_freshest_pane_wins(self, tmp_path, monkeypatch):
        root = tmp_path / "work"
        stale = root / "stale"
        fresh = root / "fresh"
        stale.mkdir(parents=True)
        fresh.mkdir(parents=True)
        herd = [
            {"pane_id": "w1:p1", "cwd": str(stale), "last_turn_age_s": 500.0,
             "focused": False},
            {"pane_id": "w1:p2", "cwd": str(fresh), "last_turn_age_s": 2.0,
             "focused": False},
        ]
        client, _ = make_client(
            tmp_path, monkeypatch, extra_herd=herd, workspaces=SPACES
        )
        spaces = client.get("/session/roots").json()["spaces"]
        assert spaces[0]["cwd"] == str(fresh.resolve())

    def test_pane_prefixes_match_their_own_workspace_only(self, tmp_path, monkeypatch):
        root = tmp_path / "work"
        w11_cwd = root / "eleven"
        w11_cwd.mkdir(parents=True)
        herd = [
            # w1's pane; w11 must NOT adopt it ("w1" is not "w11").
            {"pane_id": "w1:p1", "cwd": str(root), "last_turn_age_s": 1.0,
             "focused": False},
        ]
        w11 = WorkspaceInfo(workspace_id="w11", label="once", number=1, focused=False)
        client, _ = make_client(
            tmp_path, monkeypatch, extra_herd=herd, workspaces=[w11]
        )
        spaces = client.get("/session/roots").json()["spaces"]
        assert spaces == [{
            "workspace_id": "w11", "label": "once", "cwd": None,
            "inside_roots": False, "focused": False,
        }]

    def test_no_workspaces_degrades_to_no_spaces(self, tmp_path, monkeypatch):
        # The workspace listing degraded (empty): the picker shows no
        # spaces and the rest of the sheet keeps working.
        client, _ = make_client(tmp_path, monkeypatch)
        body = client.get("/session/roots").json()
        assert body["spaces"] == []
        assert body["roots"]


class TestSessionRootsPaneDerivedCwds:
    """T5b (L11 follow-up): space cwds come from ``herdr pane list``.

    Every pane carries a cwd — agent or not — so a workspace with NO
    running agent still resolves its folder and becomes pickable; the
    T5 agent-pane derivation stays as the failure fallback."""

    def test_a_workspace_with_no_agent_pane_resolves_its_plain_pane_cwd(
        self, tmp_path, monkeypatch
    ):
        # The L11 gap: w9 has no agent pane anywhere (empty herd), yet
        # its one plain shell pane pins the folder.
        root = tmp_path / "work"
        plain = root / "plain"
        plain.mkdir(parents=True)
        panes = [PaneInfo(pane_id="w9:p1", cwd=str(plain), focused=False)]
        client, tools = make_client(
            tmp_path, monkeypatch, extra_herd=[], workspaces=SPACES, panes=panes
        )
        spaces = client.get("/session/roots").json()["spaces"]
        assert spaces == [
            {
                "workspace_id": "w1", "label": "main",
                "cwd": None, "inside_roots": False, "focused": True,
            },
            {
                "workspace_id": "w9", "label": "vacía",
                "cwd": str(plain.resolve()), "inside_roots": True,
                "focused": False,
            },
        ]
        # ONE pane-list call served BOTH workspaces — never per space.
        assert tools.pane_client.calls == 1

    def test_the_focused_pane_wins_the_pane_derivation(self, tmp_path, monkeypatch):
        root = tmp_path / "work"
        first_dir = root / "first"
        focused_dir = root / "focused"
        first_dir.mkdir(parents=True)
        focused_dir.mkdir(parents=True)
        panes = [
            PaneInfo(pane_id="w1:p1", cwd=str(first_dir), focused=False),
            PaneInfo(pane_id="w1:p2", cwd=str(focused_dir), focused=True),
        ]
        client, _ = make_client(
            tmp_path, monkeypatch, workspaces=SPACES, panes=panes
        )
        spaces = client.get("/session/roots").json()["spaces"]
        assert spaces[0]["cwd"] == str(focused_dir.resolve())

    def test_without_a_focused_pane_the_first_listed_pane_wins(
        self, tmp_path, monkeypatch
    ):
        root = tmp_path / "work"
        first_dir = root / "first"
        second_dir = root / "second"
        first_dir.mkdir(parents=True)
        second_dir.mkdir(parents=True)
        panes = [
            PaneInfo(pane_id="w1:p1", cwd=str(first_dir), focused=False),
            PaneInfo(pane_id="w1:p2", cwd=str(second_dir), focused=False),
        ]
        client, _ = make_client(
            tmp_path, monkeypatch, workspaces=SPACES, panes=panes
        )
        spaces = client.get("/session/roots").json()["spaces"]
        assert spaces[0]["cwd"] == str(first_dir.resolve())

    def test_a_pane_list_failure_falls_back_to_the_agent_pane_derivation(
        self, tmp_path, monkeypatch
    ):
        # HerdrError on `pane list`: the sheet answers with the T5
        # agent-pane ranking, never an error of its own.
        root = tmp_path / "work"
        agent_cwd = root / "agent"
        agent_cwd.mkdir(parents=True)
        herd = [
            {"pane_id": "w1:p1", "cwd": str(agent_cwd),
             "last_turn_age_s": 3.0, "focused": False},
        ]
        client, _ = make_client(
            tmp_path, monkeypatch, extra_herd=herd, workspaces=SPACES,
            pane_error=HerdrError("pane list boom"),
        )
        spaces = client.get("/session/roots").json()["spaces"]
        assert spaces[0]["cwd"] == str(agent_cwd.resolve())

    def test_pane_prefixes_match_their_own_workspace_only(
        self, tmp_path, monkeypatch
    ):
        root = tmp_path / "work"
        w1_cwd = root / "one"
        w1_cwd.mkdir(parents=True)
        panes = [
            # w1's pane; w11 must NOT adopt it ("w1" is not "w11").
            PaneInfo(pane_id="w1:p1", cwd=str(w1_cwd), focused=False),
        ]
        w11 = WorkspaceInfo(workspace_id="w11", label="once", number=1, focused=False)
        client, _ = make_client(
            tmp_path, monkeypatch, workspaces=[w11], panes=panes
        )
        spaces = client.get("/session/roots").json()["spaces"]
        assert spaces == [{
            "workspace_id": "w11", "label": "once", "cwd": None,
            "inside_roots": False, "focused": False,
        }]

    def test_no_workspaces_skips_the_pane_listing_entirely(
        self, tmp_path, monkeypatch
    ):
        # Nothing to decorate: the route must not spend a subprocess.
        panes = [PaneInfo(pane_id="w1:p1", cwd="/anywhere", focused=False)]
        client, tools = make_client(
            tmp_path, monkeypatch, workspaces=[], panes=panes
        )
        body = client.get("/session/roots").json()
        assert body["spaces"] == []
        assert tools.pane_client.calls == 0


class TestSessionNewWorkspace:
    def test_unknown_workspace_creates_nothing(self, tmp_path, monkeypatch):
        root = tmp_path / "work"
        client, tools = make_client(tmp_path, monkeypatch, workspaces=SPACES)
        resp = client.post("/session/new", json={
            "harness": "claude", "cwd": str(root), "workspace_id": "w404",
        })
        assert resp.status_code == 422
        assert resp.json() == {"detail": "unknown_workspace"}
        assert tools.calls == []

    def test_the_cwd_gate_still_decides_first(self, tmp_path, monkeypatch):
        # Validation order unchanged (S1): an invalid cwd answers
        # root_not_allowed even when the workspace is ALSO unknown.
        client, tools = make_client(tmp_path, monkeypatch, workspaces=SPACES)
        resp = client.post("/session/new", json={
            "harness": "claude", "cwd": str(tmp_path / "elsewhere"),
            "workspace_id": "w404",
        })
        assert resp.status_code == 422
        assert resp.json() == {"detail": "root_not_allowed"}
        assert tools.calls == []

    def test_launch_passes_the_workspace_to_create_session(
        self, tmp_path, monkeypatch
    ):
        root = (tmp_path / "work").resolve()
        client, tools = make_client(tmp_path, monkeypatch, workspaces=SPACES)
        resp = client.post("/session/new", json={
            "harness": "claude", "cwd": str(root), "workspace_id": "w1",
        })
        assert resp.status_code == 200
        assert tools.calls == [{
            "agent_kind": "claude",
            "title": "claude · work",
            "task": "",
            "cwd": str(root),
            "workspace_id": "w1",
        }]

    def test_launch_without_workspace_keeps_the_legacy_call(
        self, tmp_path, monkeypatch
    ):
        root = tmp_path / "work"
        client, tools = make_client(tmp_path, monkeypatch, workspaces=SPACES)
        resp = client.post("/session/new", json={
            "harness": "claude", "cwd": str(root),
        })
        assert resp.status_code == 200
        assert tools.calls == [{
            "agent_kind": "claude", "title": "claude · work",
            "task": "", "cwd": str(root),
        }]

    def test_the_audit_line_carries_the_workspace_id(self, tmp_path, monkeypatch):
        root = tmp_path / "work"
        client, _ = make_client(tmp_path, monkeypatch, workspaces=SPACES)
        client.post("/session/new", json={
            "harness": "claude", "cwd": str(root), "workspace_id": "w1",
        })
        lines = [l for l in audit_lines(tmp_path) if l["action"] == "session.new"]
        assert lines[0]["detail"]["workspace_id"] == "w1"


class TestSessionResumeWorkspace:
    def _seed(self, tmp_path, monkeypatch):
        root = (tmp_path / "work").resolve()
        monkeypatch.setattr(
            server_module, "resume_target",
            lambda h, s: {"cwd": str(root), "title": "T"},
        )
        return root

    def test_unknown_workspace_resumes_nothing(self, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        client, tools = make_client(tmp_path, monkeypatch, workspaces=SPACES)
        resp = client.post("/session/resume", json={
            "harness": "claude", "session_id": "abc", "workspace_id": "w404",
        })
        assert resp.status_code == 422
        assert resp.json() == {"detail": "unknown_workspace"}
        assert tools.resume_calls == []

    def test_resume_passes_the_workspace_to_resume_session(
        self, tmp_path, monkeypatch
    ):
        root = self._seed(tmp_path, monkeypatch)
        client, tools = make_client(tmp_path, monkeypatch, workspaces=SPACES)
        resp = client.post("/session/resume", json={
            "harness": "claude", "session_id": "abc", "workspace_id": "w9",
        })
        assert resp.status_code == 200
        assert tools.resume_calls == [{
            "harness": "claude", "session_id": "abc", "cwd": str(root),
            "title": "T", "workspace_id": "w9",
        }]

    def test_resume_without_workspace_keeps_the_legacy_call(
        self, tmp_path, monkeypatch
    ):
        root = self._seed(tmp_path, monkeypatch)
        client, tools = make_client(tmp_path, monkeypatch, workspaces=SPACES)
        resp = client.post("/session/resume", json={
            "harness": "claude", "session_id": "abc",
        })
        assert resp.status_code == 200
        assert tools.resume_calls == [{
            "harness": "claude", "session_id": "abc", "cwd": str(root),
            "title": "T",
        }]
