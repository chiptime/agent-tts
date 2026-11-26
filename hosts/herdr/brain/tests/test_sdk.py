"""POST-05: the headless SDK surface (``/sdk/v1``) and its parity contract.

The SDK surface re-registers the SAME FastAPI handler objects the PWA
routes use under pinned ``/sdk/v1/*`` paths, so handler outcomes, the
doc-01 access matrix, the audit lines and the masking seams match
path-for-path by construction. Version skew is refused legibly (one
404 JSON naming the supported version) before the static mount can
answer, and the raw ASGI AccessMiddleware covers every SDK route like
any other — no bypass, no exemption.
"""

from __future__ import annotations

import json
import threading

import pytest
from fastapi.testclient import TestClient

import herdr_brain.server as server_module
from herdr_brain import pairing
from herdr_brain.config import Settings

from tests.conftest import SETTINGS_KWARGS
from tests.test_server import FakeLLM, FakeTTS, make_replay_tools

HERD = [
    {"pane_id": "w1:p1", "agent": "opencode", "agent_status": "working",
     "title": "A", "cwd": "/a", "session_id": "ses_1", "focused": True,
     "last_turn": {"role": "assistant", "text": "hi"}},
    {"pane_id": "w1:p2", "agent": "claude", "agent_status": "idle",
     "title": "B", "cwd": "/b", "session_id": None, "focused": False,
     "last_turn": None},
]


class HerdTools:
    """BrainTools double with the read surface the parity tests need."""

    def __init__(self, cfg):
        self.last_active = None
        self._view = {
            "status": {"active": True, "pane_id": "w1:p1", "agent": "opencode"},
            "transcript": "hola",
            "screen": "screen body",
            "screen_runs": None,
            "dialog": None,
            "pending": {"detected": False, "kind": None, "excerpt": None},
        }

    def herd(self):
        return [dict(entry) for entry in HERD]

    def agent_view(self, pane_id=None):
        return dict(self._view)

    def status_payload(self):
        return {"active": False}


def sdk_app(
    tmp_path,
    monkeypatch,
    *,
    tools_cls=None,
    allow_any_host=True,
    llm=None,
    **overrides,
):
    """create_app wired like the approval/audit tests, plus the settings
    overrides the access tests need (the legacy suite rides the
    allow-any-host opt-out; the gate tests opt back in)."""
    kwargs = {
        **SETTINGS_KWARGS,
        "audio_dir": str(tmp_path / "audio"),
        "state_dir": str(tmp_path / "state"),
        "allow_any_host": allow_any_host,
    }
    kwargs.update(overrides)
    cfg = Settings(**kwargs)
    tools_cls = tools_cls or make_replay_tools()
    monkeypatch.setattr(server_module, "BrainTools", tools_cls)
    llm = llm or FakeLLM(result={
        "answer": "Todo verde.", "pane_id": "w1:p9",
        "agent": "opencode", "session_id": "default",
    })
    app = server_module.create_app(
        settings=cfg, llm_factory=lambda c, t: llm, tts_renderer=FakeTTS()
    )
    app.state.test_llm = llm
    app.state.test_tools_cls = tools_cls
    app.state.test_state_dir = str(tmp_path / "state")
    return app


def pair_device(app, label="sdk-cli") -> str:
    """Mints a code out-of-band (the operator's shell) and claims it
    through the SAME store the /pair bootstrap drives — the credential
    the SDK client would hold after `herdr-brain sdk pair`."""
    store = pairing.PairingStore(app.state.test_state_dir)
    minted = store.mint(label=label)
    status, outcome = store.claim(minted["code"], label)
    assert status == "ok", outcome
    return outcome["token"]


def propose(app, text="corre los tests"):
    return app.state.approval_store.propose(
        "s1", text=text, timeout_ms=300000, pane_id="w7:p4", agent="opencode"
    )


# ── S1: parity by construction — the SAME handlers under /sdk/v1 ─────────────


class TestSdkParity:
    def test_herd_payload_is_byte_identical(self, tmp_path, monkeypatch):
        app = sdk_app(tmp_path, monkeypatch, tools_cls=HerdTools)
        client = TestClient(app)
        legacy = client.get("/herd")
        sdk = client.get("/sdk/v1/herd")
        assert legacy.status_code == 200
        assert sdk.status_code == 200
        assert sdk.content == legacy.content
        assert sdk.json() == HERD

    def test_view_payload_is_byte_identical(self, tmp_path, monkeypatch):
        app = sdk_app(tmp_path, monkeypatch, tools_cls=HerdTools)
        client = TestClient(app)
        legacy = client.get("/view", params={"pane_id": "w1:p1"})
        sdk = client.get("/sdk/v1/view", params={"pane_id": "w1:p1"})
        assert legacy.status_code == 200
        assert sdk.status_code == 200
        assert sdk.content == legacy.content

    def test_approval_current_payload_is_byte_identical(
        self, tmp_path, monkeypatch
    ):
        app = sdk_app(tmp_path, monkeypatch)
        gate = propose(app)
        client = TestClient(app)
        legacy = client.get("/approval/current", params={"session_id": "s1"})
        sdk = client.get(
            "/sdk/v1/approval/current", params={"session_id": "s1"}
        )
        assert legacy.status_code == 200
        assert sdk.status_code == 200
        assert sdk.content == legacy.content
        assert sdk.json()["approval"]["gate_id"] == gate.gate_id

    def test_sdk_routes_bind_the_same_handler_objects(
        self, tmp_path, monkeypatch
    ):
        app = sdk_app(tmp_path, monkeypatch)
        endpoints = {}
        for route in app.routes:
            path = getattr(route, "path", "")
            if path in ("/herd", "/sdk/v1/herd", "/view", "/sdk/v1/view",
                        "/ask", "/sdk/v1/ask", "/approval/current",
                        "/sdk/v1/approval/current",
                        "/approval/{gate_id}/action",
                        "/sdk/v1/approval/{gate_id}/action"):
                endpoints[path] = route.endpoint
        for legacy, sdk in (
            ("/herd", "/sdk/v1/herd"),
            ("/view", "/sdk/v1/view"),
            ("/ask", "/sdk/v1/ask"),
            ("/approval/current", "/sdk/v1/approval/current"),
            ("/approval/{gate_id}/action",
             "/sdk/v1/approval/{gate_id}/action"),
        ):
            assert endpoints[legacy] is endpoints[sdk], (
                f"{sdk} must register the SAME handler object as {legacy}"
            )


# ── S2: the doc-01 access matrix covers the SDK paths unchanged ──────────────


class TestSdkAccess:
    LOOPBACK = {"host": "127.0.0.1:8741"}
    REMOTE = {"host": "node.tail1234.ts.net:8443"}

    def test_sdk_read_demands_device_once_pairing_is_enforced(
        self, tmp_path, monkeypatch
    ):
        app = sdk_app(tmp_path, monkeypatch, allow_any_host=False)
        app.state.test_state_dir = str(tmp_path / "state")
        client = TestClient(app)
        # Nothing paired: the read passes without a credential (S9).
        assert client.get(
            "/sdk/v1/herd", headers=self.LOOPBACK
        ).status_code == 200
        # One paired device ⇒ enforcement ⇒ the SAME read now demands it.
        token = pair_device(app)
        refused = client.get("/sdk/v1/herd", headers=self.LOOPBACK)
        assert refused.status_code == 403
        assert refused.json()["detail"] == "dispositivo no emparejado"
        ok = client.get(
            "/sdk/v1/herd", headers={**self.LOOPBACK,
                                     "Authorization": f"Bearer {token}"}
        )
        assert ok.status_code == 200

    def test_sdk_ask_is_write_classified_without_origin_rules_relaxed(
        self, tmp_path, monkeypatch
    ):
        app = sdk_app(tmp_path, monkeypatch, allow_any_host=False)
        client = TestClient(app)
        # A remote write with NO Origin is refused exactly like POST /ask.
        remote = client.post(
            "/sdk/v1/ask", json={"text": "hola"}, headers=self.REMOTE
        )
        assert remote.status_code == 403
        assert remote.json()["detail"] == "origin requerido"
        # The legacy route answers the same refusal for the same request.
        legacy = client.post(
            "/ask", json={"text": "hola"}, headers=self.REMOTE
        )
        assert legacy.status_code == 403
        assert legacy.json() == remote.json()
        # Mirroring the same-origin Origin (what any script client does)
        # passes the gate — loopback needs no Origin at all.
        mirrored = client.post(
            "/sdk/v1/ask",
            json={"text": "hola"},
            headers={**self.REMOTE,
                     "origin": "https://node.tail1234.ts.net:8443"},
        )
        assert mirrored.status_code == 200
        loopback = client.post(
            "/sdk/v1/ask", json={"text": "hola"}, headers=self.LOOPBACK
        )
        assert loopback.status_code == 200

    def test_sdk_approval_action_demands_device_bearer_once_enforced(
        self, tmp_path, monkeypatch
    ):
        app = sdk_app(tmp_path, monkeypatch, allow_any_host=False)
        app.state.test_state_dir = str(tmp_path / "state")
        token = pair_device(app)
        gate = propose(app)
        client = TestClient(app)
        # The ntfy exemption is anchored to ^/approval/... and does NOT
        # extend to the SDK path: a scripted decision carries the full
        # device credential — strictly stronger, never a bypass.
        refused = client.post(
            f"/sdk/v1/approval/{gate.gate_id}/action",
            content="approve", headers=self.LOOPBACK,
        )
        assert refused.status_code == 403
        assert refused.json()["detail"] == "dispositivo no emparejado"
        assert app.state.approval_store.get(gate.gate_id).state == "proposed"
        ok = client.post(
            f"/sdk/v1/approval/{gate.gate_id}/action",
            content="approve",
            headers={**self.LOOPBACK, "Authorization": f"Bearer {token}"},
        )
        assert ok.status_code == 200
        assert app.state.approval_store.get(gate.gate_id).state == "approved"


# ── S3: version skew is refused legibly, never via the static mount ──────────


class TestSdkSkew:
    @pytest.mark.parametrize(
        "path",
        [
            "/sdk/v9/herd",
            "/sdk/v2/ask",
            "/sdk/v9",
            "/sdk",
            "/sdk/v1/inventado",
        ],
        ids=["unknown-version", "unknown-version-write", "bare-version",
             "sdk-root", "unknown-operation"],
    )
    def test_unknown_sdk_paths_are_legible_404_json(
        self, tmp_path, monkeypatch, path
    ):
        app = sdk_app(tmp_path, monkeypatch)
        resp = TestClient(app).get(path)
        assert resp.status_code == 404
        assert resp.headers["content-type"].startswith("application/json")
        body = resp.json()
        assert body["detail"] == "unknown sdk surface"
        assert body["supported"] == "v1"

    def test_post_to_unknown_version_is_refused_before_any_handler(
        self, tmp_path, monkeypatch
    ):
        app = sdk_app(tmp_path, monkeypatch)
        resp = TestClient(app).post("/sdk/v9/ask", json={"text": "hola"})
        assert resp.status_code == 404
        assert resp.json()["detail"] == "unknown sdk surface"

    def test_unknown_version_demands_the_device_before_revealing_anything(
        self, tmp_path, monkeypatch
    ):
        """Enforcement first, enumeration never: with pairing active an
        unauthenticated caller gets the same 403 the real routes give —
        the skew refusal is for callers the gate already let through
        (the crew dispatcher's discipline)."""
        app = sdk_app(tmp_path, monkeypatch, allow_any_host=False)
        pair_device(app)
        client = TestClient(app)
        refused = client.get("/sdk/v9/herd",
                             headers={"host": "127.0.0.1:8741"})
        assert refused.status_code == 403
        assert refused.json()["detail"] == "dispositivo no emparejado"


# ── S4: ask over the SDK — legacy contract, same audit line ──────────────────


class TestSdkAsk:
    def test_ask_over_sdk_returns_full_answer_contract(
        self, tmp_path, monkeypatch
    ):
        app = sdk_app(tmp_path, monkeypatch)
        resp = TestClient(app).post(
            "/sdk/v1/ask",
            json={"text": "en que estas?", "pane_id": "w1:p9"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["answer"] == "Todo verde."
        assert body["pane_id"] == "w1:p9"
        assert app.state.test_llm.calls == ["en que estas?"]

    def test_ask_over_sdk_leaves_the_same_audit_line(
        self, tmp_path, monkeypatch
    ):
        state = tmp_path / "state"
        app = sdk_app(tmp_path, monkeypatch, state_dir=str(state))
        resp = TestClient(app).post("/sdk/v1/ask", json={"text": "hola"})
        assert resp.status_code == 200
        lines = [
            json.loads(line)
            for line in (state / "audit.log").read_text(
                encoding="utf-8"
            ).splitlines()
        ]
        ask_lines = [l for l in lines if l["action"] == "ask"]
        assert len(ask_lines) == 1
        assert ask_lines[0]["detail"] == {"chars": len("hola")}
        # No paired device on this request: the field is omitted, not null.
        assert "device" not in ask_lines[0]

    def test_ask_over_sdk_blank_text_is_422_like_the_pwa_route(
        self, tmp_path, monkeypatch
    ):
        app = sdk_app(tmp_path, monkeypatch)
        resp = TestClient(app).post("/sdk/v1/ask", json={"text": ""})
        assert resp.status_code == 422

    def test_paired_ask_over_sdk_attributtes_the_audit_line(
        self, tmp_path, monkeypatch
    ):
        state = tmp_path / "state"

        class DeviceAwareLLM(FakeLLM):
            """The shared double predates the T3-fu device kwarg; a
            paired /ask turns passes it and the real BrainLLM.ask
            accepts it."""

            def ask(self, text, session_id=None, pane_id=None, **kwargs):
                return super().ask(
                    text, session_id=session_id, pane_id=pane_id
                )

        app = sdk_app(
            tmp_path, monkeypatch, state_dir=str(state),
            allow_any_host=False, llm=DeviceAwareLLM(result={
                "answer": "Todo verde.", "pane_id": "w1:p9",
                "agent": "opencode", "session_id": "default",
            }),
        )
        token = pair_device(app, label="sdk-cli")
        client = TestClient(app)
        resp = client.post(
            "/sdk/v1/ask",
            json={"text": "hola"},
            headers={"host": "127.0.0.1:8741",
                     "Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        lines = [
            json.loads(line)
            for line in (state / "audit.log").read_text(
                encoding="utf-8"
            ).splitlines()
        ]
        ask_lines = [l for l in lines if l["action"] == "ask"]
        assert len(ask_lines) == 1
        assert ask_lines[0]["device"] == "sdk-cli"


# ── S5: gate decisions over the SDK — atomic under concurrency ───────────────


class TestSdkApprovalAction:
    def test_invalid_decision_is_422_and_leaves_gate_live(
        self, tmp_path, monkeypatch
    ):
        app = sdk_app(tmp_path, monkeypatch)
        gate = propose(app)
        resp = TestClient(app).post(
            f"/sdk/v1/approval/{gate.gate_id}/action", content="maybe"
        )
        assert resp.status_code == 422
        assert app.state.approval_store.get(gate.gate_id).state == "proposed"

    def test_dead_gate_is_404(self, tmp_path, monkeypatch):
        app = sdk_app(tmp_path, monkeypatch)
        resp = TestClient(app).post(
            "/sdk/v1/approval/gate_dead/action", content="approve"
        )
        assert resp.status_code == 404

    def test_reject_resolves_and_dispatches_nothing(
        self, tmp_path, monkeypatch
    ):
        app = sdk_app(tmp_path, monkeypatch)
        gate = propose(app)
        resp = TestClient(app).post(
            f"/sdk/v1/approval/{gate.gate_id}/action", content="reject"
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "decision": "reject",
                               "state": "rejected"}
        created = app.state.test_tools_cls.created
        assert [d for t in created for d in t.dispatches] == []

    def test_concurrent_decisions_resolve_exactly_once(
        self, tmp_path, monkeypatch
    ):
        """Two clients decide the SAME gate at the same time: exactly one
        200, one 404 gate-gone, the frozen send dispatched exactly once —
        the atomic resolve the PWA and voice paths already rely on. The
        barrier guarantees both requests are IN FLIGHT together instead
        of serializing by thread-scheduling luck."""
        app = sdk_app(tmp_path, monkeypatch)
        gate = propose(app)
        outcomes: list = []
        barrier = threading.Barrier(2)

        def decide():
            client = TestClient(app)
            barrier.wait()
            resp = client.post(
                f"/sdk/v1/approval/{gate.gate_id}/action", content="approve"
            )
            outcomes.append((resp.status_code, resp.json()))

        threads = [threading.Thread(target=decide) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        statuses = sorted(code for code, _ in outcomes)
        assert statuses == [200, 404]
        winner = next(body for code, body in outcomes if code == 200)
        assert winner == {"ok": True, "decision": "approve",
                          "state": "approved"}
        sent = [d for t in app.state.test_tools_cls.created
                for d in t.dispatches]
        assert len(sent) == 1
        assert sent[0]["name"] == "send_to_session"

    def test_audit_line_carries_decision_gate_and_device(
        self, tmp_path, monkeypatch
    ):
        state = tmp_path / "state"
        app = sdk_app(tmp_path, monkeypatch, state_dir=str(state),
                      allow_any_host=False)
        token = pair_device(app, label="sdk-cli")
        gate = propose(app)
        client = TestClient(app)
        resp = client.post(
            f"/sdk/v1/approval/{gate.gate_id}/action",
            content="approve",
            headers={"host": "127.0.0.1:8741",
                     "Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        lines = [
            json.loads(line)
            for line in (state / "audit.log").read_text(
                encoding="utf-8"
            ).splitlines()
        ]
        action = next(l for l in lines if l["action"] == "approval.action")
        assert action["detail"]["decision"] == "approve"
        assert action["detail"]["gate_id"] == gate.gate_id
        # The device bearer IS the attribution — unlike the ntfy channel,
        # which records no device at all.
        assert action["device"] == "sdk-cli"

    def test_sdk_action_authenticates_with_the_device_bearer_while_ac8_stays_on_legacy(
        self, tmp_path, monkeypatch
    ):
        """With HERDR_BRAIN_APPROVAL_TOKEN configured TOGETHER with an
        enforced pairing, each channel authenticates with its own
        credential: the SDK route (write-classified, no ntfy exemption)
        accepts the paired device bearer — the legacy ntfy route keeps
        demanding the ntfy token and refuses the device bearer with the
        same 401 it always gave."""
        app = sdk_app(
            tmp_path, monkeypatch, allow_any_host=False,
            approval_token="ntfy-secret",
        )
        device_token = pair_device(app, label="sdk-cli")
        # Two DIFFERENT sessions: proposing a second gate on the same
        # session would retire the first (the deliberate new-work rule).
        gate_sdk = app.state.approval_store.propose(
            "s1", text="corre los tests", timeout_ms=300000,
            pane_id="w7:p4", agent="opencode",
        )
        gate_ntfy = app.state.approval_store.propose(
            "s2", text="corre los tests", timeout_ms=300000,
            pane_id="w7:p4", agent="opencode",
        )
        client = TestClient(app)
        loopback = {"host": "127.0.0.1:8741"}
        ok = client.post(
            f"/sdk/v1/approval/{gate_sdk.gate_id}/action",
            content="approve",
            headers={**loopback, "Authorization": f"Bearer {device_token}"},
        )
        assert ok.status_code == 200, ok.text
        assert app.state.approval_store.get(gate_sdk.gate_id).state == "approved"
        refused = client.post(
            f"/approval/{gate_ntfy.gate_id}/action",
            content="approve",
            headers={**loopback, "Authorization": f"Bearer {device_token}"},
        )
        assert refused.status_code == 401
        assert app.state.approval_store.get(gate_ntfy.gate_id).state == "proposed"
        good = client.post(
            f"/approval/{gate_ntfy.gate_id}/action",
            content="approve",
            headers={**loopback, "Authorization": "Bearer ntfy-secret"},
        )
        assert good.status_code == 200
        assert app.state.approval_store.get(gate_ntfy.gate_id).state == "approved"


# ── S6/S7: the `herdr-brain sdk` client (stdlib-only, in ops_cli) ────────────


class TestSdkCli:
    """The CLI is a thin HTTP client over one stubbed seam
    (ops_cli._sdk_http_request): token resolution, verb → request
    mapping, verbatim JSON output, the exit contract and the credential
    file discipline (0600, never printed)."""

    @staticmethod
    def _env(tmp_path, **extra):
        env = {
            "HERDR_BRAIN_STATE_DIR": str(tmp_path / "state"),
            "HERDR_BRAIN_SDK_URL": "http://127.0.0.1:8741",
        }
        env.update(extra)
        return env

    @staticmethod
    def _stub_http(monkeypatch, status=200, payload=b"{}", calls=None):
        from herdr_brain import ops_cli

        def fake(method, url, path, *, token=None, body=None):
            if calls is not None:
                calls.append({
                    "method": method, "url": url, "path": path,
                    "token": token, "body": body,
                })
            return status, payload

        monkeypatch.setattr(ops_cli, "_sdk_http_request", fake)

    @staticmethod
    def _write_token(tmp_path, token="file-token"):
        state = tmp_path / "state"
        state.mkdir(parents=True, exist_ok=True)
        (state / "sdk-device-token").write_text(f"{token}\n", encoding="utf-8")

    def test_herd_prints_verbatim_json_and_sends_bearer(
        self, tmp_path, monkeypatch, capsys
    ):
        from herdr_brain import ops_cli

        calls: list = []
        self._stub_http(monkeypatch, payload=b'[{"pane_id":"w1:p9"}]',
                        calls=calls)
        self._write_token(tmp_path)
        rc = ops_cli.main(["sdk", "herd"], env=self._env(tmp_path))
        assert rc == 0
        out = capsys.readouterr()
        assert out.out == '[{"pane_id":"w1:p9"}]\n'
        call = calls[0]
        assert (call["method"], call["path"]) == ("GET", "/sdk/v1/herd")
        assert call["url"] == "http://127.0.0.1:8741"
        assert call["token"] == "file-token"

    def test_env_token_wins_over_file_and_port_env_builds_default_url(
        self, tmp_path, monkeypatch, capsys
    ):
        from herdr_brain import ops_cli

        calls: list = []
        self._stub_http(monkeypatch, payload=b"[]", calls=calls)
        self._write_token(tmp_path, token="file-token")
        env = self._env(tmp_path, HERDR_BRAIN_SDK_TOKEN="env-token",
                        HERDR_BRAIN_PORT="9999")
        env.pop("HERDR_BRAIN_SDK_URL")
        rc = ops_cli.main(["sdk", "herd"], env=env)
        assert rc == 0
        assert calls[0]["token"] == "env-token"
        assert calls[0]["url"] == "http://127.0.0.1:9999"

    def test_missing_credential_fails_with_pair_hint(
        self, tmp_path, monkeypatch, capsys
    ):
        from herdr_brain import ops_cli

        calls: list = []
        self._stub_http(monkeypatch, payload=b"[]", calls=calls)
        rc = ops_cli.main(["sdk", "herd"], env=self._env(tmp_path))
        assert rc == 1
        err = capsys.readouterr().err
        assert "sdk pair" in err
        assert calls == []

    def test_pair_spends_code_stores_0600_and_never_prints_the_token(
        self, tmp_path, monkeypatch, capsys
    ):
        import stat as stat_module

        from herdr_brain import ops_cli

        calls: list = []
        self._stub_http(
            monkeypatch,
            payload=b'{"token": "fresh-secret-token", "label": "sdk-cli"}',
            calls=calls,
        )
        rc = ops_cli.main(
            ["sdk", "pair", "ABCD-1234", "--label", "sdk-cli"],
            env=self._env(tmp_path),
        )
        assert rc == 0
        call = calls[0]
        assert (call["method"], call["path"]) == ("POST", "/pair")
        assert call["body"] == {"code": "ABCD-1234", "label": "sdk-cli"}
        captured = capsys.readouterr()
        assert "fresh-secret-token" not in captured.out
        assert "fresh-secret-token" not in captured.err
        path = tmp_path / "state" / "sdk-device-token"
        assert path.read_text(encoding="utf-8").strip() == "fresh-secret-token"
        assert stat_module.S_IMODE(path.stat().st_mode) == 0o600

    def test_ask_posts_body_and_mirrors_origin(
        self, tmp_path, monkeypatch, capsys
    ):
        from herdr_brain import ops_cli

        calls: list = []
        self._stub_http(monkeypatch, payload=b'{"answer": "ok"}', calls=calls)
        self._write_token(tmp_path)
        rc = ops_cli.main(
            ["sdk", "ask", "hola", "--pane", "w1:p9", "--session", "s1",
             "--reset"],
            env=self._env(tmp_path),
        )
        assert rc == 0
        call = calls[0]
        assert (call["method"], call["path"]) == ("POST", "/sdk/v1/ask")
        assert call["body"] == {
            "text": "hola", "pane_id": "w1:p9", "session_id": "s1",
            "reset": True,
        }

    def test_writes_mirror_the_same_origin_origin(self):
        """Writes cross the doc-01 write gate like a browser: the client
        mirrors the base URL as its Origin (origin_host == host ⇒
        allowed; loopback needs no Origin but mirroring is honest)."""
        from herdr_brain import ops_cli

        for base in ("http://127.0.0.1:8741",
                     "https://node.tail1234.ts.net:8443"):
            request = ops_cli._sdk_request_object(
                "POST", base, "/sdk/v1/ask", token="t", body={"text": "hola"}
            )
            assert request.get_header("Origin") == base
            assert request.get_header("Authorization") == "Bearer t"
            assert request.get_header("Content-type") == "application/json"
            assert request.full_url == f"{base}/sdk/v1/ask"
        # Reads carry the bearer but no Origin (only writes need it).
        request = ops_cli._sdk_request_object(
            "GET", "http://127.0.0.1:8741", "/sdk/v1/herd", token="t"
        )
        assert request.get_header("Origin") is None
        assert request.get_header("Authorization") == "Bearer t"

    def test_view_sends_pane_and_modal_query(
        self, tmp_path, monkeypatch, capsys
    ):
        from herdr_brain import ops_cli

        calls: list = []
        self._stub_http(monkeypatch, payload=b"{}", calls=calls)
        self._write_token(tmp_path)
        rc = ops_cli.main(
            ["sdk", "view", "w1:p9", "--modal"], env=self._env(tmp_path)
        )
        assert rc == 0
        assert calls[0]["method"] == "GET"
        assert "pane_id=w1%3Ap9" in calls[0]["path"]
        assert "modal=1" in calls[0]["path"]

    def test_approval_current_and_action_map_to_their_paths(
        self, tmp_path, monkeypatch, capsys
    ):
        from herdr_brain import ops_cli

        calls: list = []
        self._stub_http(monkeypatch, payload=b'{"approval": null}',
                        calls=calls)
        self._write_token(tmp_path)
        env = self._env(tmp_path)
        assert ops_cli.main(["sdk", "approval-current"], env=env) == 0
        assert calls[-1]["path"] == "/sdk/v1/approval/current"
        assert ops_cli.main(
            ["sdk", "approval-action", "gate_1", "approve"], env=env
        ) == 0
        assert calls[-1]["method"] == "POST"
        assert calls[-1]["path"] == "/sdk/v1/approval/gate_1/action"
        assert calls[-1]["body"] == {"decision": "approve"}

    def test_invalid_decision_is_usage(self, tmp_path, monkeypatch):
        from herdr_brain import ops_cli

        self._stub_http(monkeypatch)
        self._write_token(tmp_path)
        rc = ops_cli.main(
            ["sdk", "approval-action", "gate_1", "maybe"],
            env=self._env(tmp_path),
        )
        assert rc == 2

    def test_http_error_prints_detail_to_stderr_exit_1(
        self, tmp_path, monkeypatch, capsys
    ):
        from herdr_brain import ops_cli

        self._stub_http(monkeypatch, status=403,
                        payload=b'{"detail": "dispositivo no emparejado"}')
        self._write_token(tmp_path)
        rc = ops_cli.main(["sdk", "herd"], env=self._env(tmp_path))
        assert rc == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "403" in captured.err
        assert "dispositivo no emparejado" in captured.err

    def test_unreachable_brain_exit_1(self, tmp_path, monkeypatch, capsys):
        from herdr_brain import ops_cli

        def boom(method, url, path, *, token=None, body=None):
            raise OSError("connection refused")

        monkeypatch.setattr(ops_cli, "_sdk_http_request", boom)
        self._write_token(tmp_path)
        rc = ops_cli.main(["sdk", "herd"], env=self._env(tmp_path))
        assert rc == 1
        assert "connection refused" in capsys.readouterr().err

    def test_pair_rejects_answers_without_a_usable_token(
        self, tmp_path, monkeypatch, capsys
    ):
        from herdr_brain import ops_cli

        self._write_token(tmp_path)
        token_path = tmp_path / "state" / "sdk-device-token"
        for payload in (b'{"label": "sdk-cli"}', b'{"token": "   "}'):
            self._stub_http(monkeypatch, payload=payload)
            rc = ops_cli.main(
                ["sdk", "pair", "ABCD-1234"], env=self._env(tmp_path)
            )
            assert rc == 1, payload
            assert "token" in capsys.readouterr().err
            # A rejected answer must not have touched the credential.
            assert token_path.read_text(
                encoding="utf-8"
            ).strip() == "file-token"

    def test_pair_replaces_a_loose_preexisting_file_owner_only(
        self, tmp_path, monkeypatch, capsys
    ):
        """The atomic store: a credential file some older run left at
        0644 is replaced through a unique 0600 sibling + os.replace —
        the loose bits never survive the write, not even for an
        instant, and symlinks are not followed."""
        import os
        import stat as stat_module

        from herdr_brain import ops_cli

        state = tmp_path / "state"
        state.mkdir(parents=True)
        path = state / "sdk-device-token"
        path.write_text("stale\n", encoding="utf-8")
        os.chmod(path, 0o644)
        self._stub_http(
            monkeypatch, payload=b'{"token": "fresh", "label": "sdk-cli"}'
        )
        rc = ops_cli.main(
            ["sdk", "pair", "ABCD-1234"], env=self._env(tmp_path)
        )
        assert rc == 0
        assert "fresh" not in capsys.readouterr().out
        assert path.read_text(encoding="utf-8").strip() == "fresh"
        assert stat_module.S_IMODE(path.stat().st_mode) == 0o600
        # The temp sibling is gone: exactly one file holds the token.
        assert [p.name for p in state.iterdir()] == ["sdk-device-token"]

    def test_unsupported_scheme_is_a_clean_failure(
        self, tmp_path, monkeypatch, capsys
    ):
        from herdr_brain import ops_cli

        calls: list = []
        self._stub_http(monkeypatch, calls=calls)
        self._write_token(tmp_path)
        env = self._env(tmp_path, HERDR_BRAIN_SDK_URL="file:///etc/passwd")
        rc = ops_cli.main(["sdk", "herd"], env=env)
        assert rc == 1
        err = capsys.readouterr().err
        assert "file" in err and "https" in err or "http" in err
        assert calls == []

    def test_malformed_url_is_a_clean_failure(
        self, tmp_path, monkeypatch, capsys
    ):
        from herdr_brain import ops_cli

        calls: list = []
        self._stub_http(monkeypatch, calls=calls)
        self._write_token(tmp_path)
        env = self._env(tmp_path, HERDR_BRAIN_SDK_URL="http://[::1")
        rc = ops_cli.main(["sdk", "herd"], env=env)
        assert rc == 1
        assert "error" in capsys.readouterr().err
        assert calls == []

    def test_origin_is_scheme_and_netloc_only(self):
        """A base URL carrying a subpath still sends the strict
        scheme://netloc Origin the doc-01 same-origin rule compares."""
        from herdr_brain import ops_cli

        request = ops_cli._sdk_request_object(
            "POST", "http://127.0.0.1:8741/sub", "/sdk/v1/ask",
            token="t", body={"text": "hola"},
        )
        assert request.get_header("Origin") == "http://127.0.0.1:8741"
        assert request.full_url == "http://127.0.0.1:8741/sub/sdk/v1/ask"

    def test_gate_id_traversal_and_spaces_are_rejected(
        self, tmp_path, monkeypatch, capsys
    ):
        from herdr_brain import ops_cli

        calls: list = []
        self._stub_http(monkeypatch, calls=calls)
        self._write_token(tmp_path)
        env = self._env(tmp_path)
        for bad in ("../ask", "gate 1", "", "a/b", ".."):
            rc = ops_cli.main(
                ["sdk", "approval-action", bad, "approve"], env=env
            )
            assert rc == 2, bad
        assert calls == []
        assert "gate_id" in capsys.readouterr().err

    def test_gate_id_is_quoted_into_a_single_segment(
        self, tmp_path, monkeypatch
    ):
        from herdr_brain import ops_cli

        calls: list = []
        self._stub_http(monkeypatch, payload=b'{"ok": true}', calls=calls)
        self._write_token(tmp_path)
        rc = ops_cli.main(
            ["sdk", "approval-action", "g+1", "approve"],
            env=self._env(tmp_path),
        )
        assert rc == 0
        assert calls[0]["path"] == "/sdk/v1/approval/g%2B1/action"

    def test_bare_sdk_verb_is_usage_exit_2(self, tmp_path, monkeypatch):
        from herdr_brain import ops_cli

        assert ops_cli.main(["sdk"], env=self._env(tmp_path)) == 2
        # An unknown subverb is argparse's own refusal (SystemExit 2).
        with pytest.raises(SystemExit) as excinfo:
            ops_cli.main(["sdk", "bogus"], env=self._env(tmp_path))
        assert excinfo.value.code == 2
