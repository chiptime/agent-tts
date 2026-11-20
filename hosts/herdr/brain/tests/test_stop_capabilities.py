"""Stop/steer capability per pane (parity-f7e S1(2)).

The /herd entries grow two ADDITIVE keys — ``can_stop`` and
``can_steer`` — derived from the same per-harness tables the stop route
and the voice interrupt gate on, so the PWA can explain per pane why
Stop is unavailable instead of letting the user arm a button that can
only ever answer 422. A harness without a verified recipe keeps the
honest refusal: ``POST /pane/stop`` answers 422 with a Spanish detail
(pinned here at the HTTP level).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import herdr_brain.server as server_module
from herdr_brain.config import Settings
from herdr_brain.herdr import AgentInfo, HerdrError
from herdr_brain.send_receipts import INTERRUPT_RECIPES, STEER_HARNESSES
from herdr_brain.tools import BrainTools

from tests.conftest import SETTINGS_KWARGS
from tests.test_pairing_api import LOOPBACK


class HerdStub:
    """The minimal mux seam tools.herd() needs: list + transcripts."""

    def __init__(self, agents):
        self._agents = agents

    def list_agents(self):
        return list(self._agents)

    def active_agent(self):
        return next((a for a in self._agents if a.focused), None)


def _agent(pane_id, agent, status="idle"):
    return AgentInfo(
        pane_id=pane_id,
        agent=agent,
        status=status,
        session_kind="id",
        session_value=f"ses-{pane_id}",
        cwd="/repo",
        title=pane_id,
        focused=False,
    )


def _herd(agents, settings):
    # No transcript store in these tests: every transcript-reading seam
    # degrades per agent, leaving the pure mux-driven fields.
    tools = BrainTools(settings, herdr=HerdStub(agents))
    tools._enrich = lambda agent: agent
    tools._latest_turn = lambda agent: None
    tools._model_of = lambda agent: None
    tools._agent_error = lambda agent: None
    return tools.herd()


def test_herd_entries_carry_the_capability_keys_per_harness(settings):
    entries = _herd(
        [
            _agent("w1:p1", "claude"),
            _agent("w1:p2", "pi"),
            _agent("w1:p3", "agy"),
            _agent("w1:p4", "grok"),
        ],
        settings,
    )
    by_pane = {e["pane_id"]: e for e in entries}
    assert by_pane["w1:p1"]["can_stop"] is True
    assert by_pane["w1:p1"]["can_steer"] is True
    # pi: live-verified interrupt AND steer.
    assert by_pane["w1:p2"]["can_stop"] is True
    assert by_pane["w1:p2"]["can_steer"] is True
    # agy: live-verified interrupt only (steer unverified).
    assert by_pane["w1:p3"]["can_stop"] is True
    assert by_pane["w1:p3"]["can_steer"] is False
    # grok: no expressible recipe (ctrl+c is outside the allowlist).
    assert by_pane["w1:p4"]["can_stop"] is False
    assert by_pane["w1:p4"]["can_steer"] is False


def test_capability_keys_are_additive_on_the_herd_entry(settings):
    """PRESERVE: the pre-f7e /herd entry key set is unchanged — the two
    new keys are additions on top, no existing key renamed or moved."""
    entries = _herd([_agent("w1:p1", "claude")], settings)
    assert set(entries[0]) == {
        # The exact pre-f7e shape…
        "pane_id", "agent", "agent_status", "title", "cwd",
        "session_id", "model", "focused", "last_turn", "last_turn_age_s",
        "agent_error",
        # …plus the two additive capability keys.
        "can_stop", "can_steer",
    }


def test_capabilities_never_drift_from_the_recipe_tables(settings):
    for agent in ("claude", "codex", "opencode", "pi", "agy", "grok", "gemini"):
        entries = _herd([_agent("w1:p1", agent)], settings)
        assert entries[0]["can_stop"] == (agent in INTERRUPT_RECIPES)
        assert entries[0]["can_steer"] == (agent in STEER_HARNESSES)


class UnsupportedStopTools:
    """Server double whose stop always refuses like an uncovered
    harness would (the route maps it to the pinned 422)."""

    stop_outcome: dict = {
        "outcome": "unsupported_harness",
        "pane_id": "w1:p2",
        "keys": [],
        "detail": "no interrupt recipe for harness 'grok'",
    }

    def __init__(self, cfg):
        type(self).stops = []

    def stop_pane(self, pane_id, text_hint=""):
        type(self).stops.append(pane_id)
        return dict(type(self).stop_outcome, pane_id=pane_id)


def test_stop_route_answers_422_with_a_spanish_detail(tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "BrainTools", UnsupportedStopTools)
    cfg = Settings(**{
        **SETTINGS_KWARGS,
        "audio_dir": str(tmp_path / "audio"),
        "state_dir": str(tmp_path / "state"),
    })
    client = TestClient(server_module.create_app(settings=cfg), base_url=LOOPBACK)

    resp = client.post("/pane/stop", json={"pane_id": "w1:p2"})

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    # The explanation is Spanish, names the safety reason and offers the
    # manual alternative — the copy S1(2) asks the PWA to mirror.
    assert "interrumpir" in detail
    assert "seguridad" in detail
    assert "atajo" in detail
