"""The guarded recipe-key channel through the REAL mux port (T10/S3).

Verifier BLOCKER 2: production BrainTools._herdr is create_mux() -- the
HerdrMuxAdapter or a tmux/zellij/composite adapter -- and none of them
exposed send_recipe_keys, so every footer-letter recipe fell back to
plain send_keys and was refused by the letter-free allowlist. The
channel has to be threaded through the port: each adapter validates the
recipe dialect (letters included) before any spawn, and the raw keys
surface keeps its letter-free gate.

Safety: recorded runners only -- no test spawns a real multiplexer
(the * _live.py suites cover the real sessions).
"""

from __future__ import annotations

import json

import pytest

from pathlib import Path

from herdr_brain.config import Settings
from herdr_brain.herdr import AgentInfo, HerdrClient
from herdr_brain.mux import CompositeMux, MuxRefusal
from herdr_brain.mux.registry import create_mux
from herdr_brain.mux.adapters.herdr import HerdrMuxAdapter
from herdr_brain.mux.adapters.tmux import TmuxAdapter
from herdr_brain.mux.adapters.zellij import ZellijAdapter


class RecordedRunner:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append({"cmd": cmd, **kwargs})
        result = self.results.pop(0)
        return __import__("subprocess").CompletedProcess(cmd, result[0], result[1], result[2])


def herdr_client(settings, runner):
    return HerdrClient(settings, runner=runner)


class TestHerdrAdapterRecipeKeys:
    def test_a_footer_letter_reaches_the_cli_argv(self, settings):
        runner = RecordedRunner((0, "", ""))
        adapter = HerdrMuxAdapter(client=herdr_client(settings, runner))

        result = adapter.send_recipe_keys("w1:p2", ["u"])

        assert result["ok"] is True
        assert runner.calls[0]["cmd"] == [
            "herdr-fake", "agent", "send-keys", "w1:p2", "u",
        ]

    def test_the_recipe_surface_keeps_the_plain_surface_letter_free(self, settings):
        runner = RecordedRunner((0, "", ""))
        adapter = HerdrMuxAdapter(client=herdr_client(settings, runner))

        adapter.send_recipe_keys("w1:p2", ["u", "enter"])
        # The plain surface (raw /pane/keys) must refuse the letter
        # BEFORE any spawn: the recorded runner never gains a call.
        from herdr_brain.herdr import HerdrError

        with pytest.raises(HerdrError, match="unsupported key"):
            adapter.send_keys("w1:p2", ["u"])
        assert len(runner.calls) == 1


class TestTmuxAdapterRecipeKeys:
    def test_a_footer_letter_translates_as_a_literal(self):
        runner = RecordedRunner((0, "", ""))
        adapter = TmuxAdapter(bin_path="tmux-test", runner=runner)

        result = adapter.send_recipe_keys("3", ["d"])

        assert result["ok"] is True
        assert runner.calls[-1]["cmd"] == ["tmux-test", "send-keys", "-t", "3", "d"]

    def test_uppercase_and_shifted_tokens_stay_refused(self):
        runner = RecordedRunner((0, "", ""))
        adapter = TmuxAdapter(bin_path="tmux-test", runner=runner)

        for letter in ("A", "Q", "ctrl+z"):
            with pytest.raises(MuxRefusal):
                adapter.send_recipe_keys("3", [letter])
        assert runner.calls == []


class TestZellijAdapterRecipeKeys:
    def test_a_footer_letter_translates_as_a_literal(self):
        from tests.test_mux_zellij import RecordedRunner as ZRun  # same shape

        runner = ZRun((("action", "send-keys"), 0, "", ""))
        adapter = ZellijAdapter(bin_path="zellij-test", runner=runner)

        result = adapter.send_recipe_keys("1", ["d"])

        assert result["ok"] is True
        assert runner.calls[-1]["cmd"][-1] == "d"

    def test_uppercase_stays_refused(self):
        from tests.test_mux_zellij import RecordedRunner as ZRun

        runner = ZRun((("action", "send-keys"), 0, "", ""))
        adapter = ZellijAdapter(bin_path="zellij-test", runner=runner)

        with pytest.raises(MuxRefusal):
            adapter.send_recipe_keys("1", ["D"])
        assert runner.calls == []


class TestCompositeRoutesRecipeKeys:
    def test_recipe_keys_route_to_the_owning_child(self):
        recorded = []

        class Child:
            name = "herdr"

            def send_recipe_keys(self, pane_id, keys):
                recorded.append((pane_id, list(keys)))
                return {"ok": True, "error": ""}

        composite = CompositeMux({"herdr": Child()})

        assert composite.send_recipe_keys("w1:p2", ["u"]) == {"ok": True, "error": ""}
        assert recorded == [("w1:p2", ["u"])]  # routed pane id passes whole


class TestProductionMuxChain:
    """The verifier's end-to-end pin: the REAL create_mux() adapter that
    production BrainTools receives routes a footer-letter recipe to the
    CLI, while the raw /pane/keys route still refuses letters."""

    MARKETPLACES = (
        Path(__file__).parent / "fixtures" / "dialogs" / "claude-menu-marketplaces.txt"
    ).read_text(encoding="utf-8")

    def _mux(self, settings):
        calls = []

        def runner(cmd, **kwargs):
            calls.append(list(cmd))
            output = (
                json.dumps(
                    {"result": {"panels": [{"id": "w1:p3", "title": "Claude"}]}}
                )
                if "list" in " ".join(cmd)
                else '{"ok": true}'
            )
            return __import__("subprocess").CompletedProcess(cmd, 0, output, "")

        mux = create_mux(settings, runner=runner)
        mux.list_agents = lambda: [
            AgentInfo(
                pane_id="w1:p3",
                agent="claude",
                status="blocked",
                session_kind="id",
                session_value="x",
                cwd="/repo",
                title="Claude",
                focused=True,
            )
        ]
        mux.read_screen = lambda *a, **k: self.MARKETPLACES
        return mux, calls

    def test_a_tap_dispatches_the_letter_through_the_real_adapter(
        self, settings, tmp_path
    ):
        from herdr_brain.tools import BrainTools
        from herdr_brain.dialogs import recognize

        mux, calls = self._mux(settings)

        # The production shape: BrainTools receives the mux adapter, and
        # the adapter DOES expose the guarded recipe surface.
        assert hasattr(mux, "send_recipe_keys")
        tools = BrainTools(settings, herdr=mux)
        fingerprint = recognize("claude", self.MARKETPLACES).fingerprint

        result = tools.answer_structured_dialog("w1:p3", fingerprint, option=1)

        assert result["outcome"] == "answered", result
        assert result["keys"] == ["u"]
        # The letter reached the CLI as one argv token of agent send-keys.
        assert any(call[1:4] == ["agent", "send-keys", "w1:p3"] and "u" in call
                   for call in calls), calls

    def test_the_raw_keys_route_still_refuses_letters(self, settings, tmp_path):
        """Pen-and-paper pin at the route: /pane/keys validates against
        the letter-free allowlist and answers 422."""
        from fastapi.testclient import TestClient

        import herdr_brain.server as server_module
        from herdr_brain.tools import BrainTools

        mux, calls = self._mux(settings)

        def factory(cfg, **kwargs):
            return BrainTools(cfg, herdr=mux)

        original = server_module.BrainTools
        server_module.BrainTools = factory
        try:
            client = TestClient(
                server_module.create_app(
                    settings=Settings(
                        **{**settings.__dict__,
                           "state_dir": str(tmp_path / "state"),
                           "audio_dir": str(tmp_path / "audio")}
                    )
                )
            )
            response = client.post(
                "/pane/keys", json={"pane_id": "w1:p3", "keys": ["u"]}
            )
            assert response.status_code == 422
            assert "unsupported key" in str(response.json()["detail"])
        finally:
            server_module.BrainTools = original
        assert calls == []  # nothing was ever spawned for the refused letter
