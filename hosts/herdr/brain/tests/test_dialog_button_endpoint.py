"""POST /pane/dialog/answer: the guarded button-tap path (S2).

One tap on a structured dialog option is a WRITE against a live
terminal, so it earns the same discipline as every other key route:

- the pane is resolved EXACTLY (an unknown pane is a 404, never the
  focused fallback — answering the wrong agent's permission dialog
  would be silent and hard to undo);
- the server RE-READS the pane and compares the dialog fingerprint:
  a screen that moved under the tap is a 409, and no key leaves;
- a recognized option's recipe is sent through the closed allowlist
  (digits included); a walk-recipe dialog is walked by the guard with a
  re-read after every arrow;
- every attempt lands in the audit trail as ``pane.dialog.answer``;
- the route is WRITE-classified (device-gated like /pane/keys).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import herdr_brain.server as server_module
from herdr_brain.config import Settings
from herdr_brain.dialogs import recognize
from herdr_brain.herdr import AgentInfo

FIXTURES = Path(__file__).parent / "fixtures" / "dialogs"

CLAUDE_SELECT = (FIXTURES / "claude-select.txt").read_text(encoding="utf-8")
GROK_PERMISSION = (FIXTURES / "grok-permission.txt").read_text(encoding="utf-8")

BLOCKED_CLAUDE = AgentInfo(
    pane_id="w1:p3",
    agent="claude",
    status="blocked",
    session_kind="id",
    session_value="00000000-1111-2222-3333-444444444444",
    cwd="/repo",
    title="Claude",
    focused=True,
)

BLOCKED_GROK = AgentInfo(
    pane_id="w2:p1",
    agent="grok",
    status="blocked",
    session_kind="id",
    session_value="bbbbbbbb-1111-2222-3333-444444444444",
    cwd="/repo",
    title="Grok",
    focused=True,
)


class DialogTapStub:
    """HerdrClient double: one blocked pane whose styled read IS a
    dialog fixture, with recorded key sends."""

    def __init__(self, agents, screen):
        self._agents = agents
        self._screen = screen
        self.key_calls: list = []

    def list_agents(self):
        return list(self._agents)

    def active_agent(self):
        from herdr_brain.herdr import pick_active

        return pick_active(self._agents)

    def read_screen(self, pane_id, n_lines=None, source="visible", as_ansi=False):
        return self._screen

    def send_keys(self, pane_id, keys):
        self.key_calls.append({"pane_id": pane_id, "keys": list(keys)})
        return {"ok": True, "error": ""}


@pytest.fixture
def make_client(settings, tmp_path, monkeypatch):
    def build(agents, screen, sleeper=None):
        stub = DialogTapStub(agents, screen)
        state_dir = tmp_path / "state"

        def factory(cfg, **kwargs):
            from herdr_brain.tools import BrainTools

            tools = BrainTools(cfg, herdr=stub, sleeper=sleeper)
            return tools

        monkeypatch.setattr(server_module, "BrainTools", factory)
        cfg = Settings(**{**settings.__dict__, "state_dir": str(state_dir),
                          "audio_dir": str(tmp_path / "audio")})
        client = TestClient(server_module.create_app(settings=cfg))
        client.state_dir = state_dir
        client.audit_path = state_dir / "audit.log"
        return client, stub

    return build


def audit_text(client) -> str:
    return client.audit_path.read_text(encoding="utf-8")


def audit_entries(client) -> list:
    import json as _json

    return [
        _json.loads(line)
        for line in audit_text(client).splitlines()
        if line.strip()
    ]


def _fingerprint(agent: str, screen: str) -> str:
    model = recognize(agent, screen)
    assert model is not None and not model.unread
    return model.fingerprint


class TestAnswerByRecipe:
    def test_a_tap_sends_the_option_recipe_and_audits_it(self, make_client):
        client, stub = make_client([BLOCKED_CLAUDE], CLAUDE_SELECT)
        fingerprint = _fingerprint("claude", CLAUDE_SELECT)

        response = client.post(
            "/pane/dialog/answer",
            json={"pane_id": "w1:p3", "fingerprint": fingerprint, "option": 1},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["option"] == 1
        assert stub.key_calls == [{"pane_id": "w1:p3", "keys": ["2", "enter"]}]

        entries = audit_entries(client)
        answer = next(e for e in entries if e["action"] == "pane.dialog.answer")
        assert answer["pane_id"] == "w1:p3"
        assert answer["detail"]["outcome"] == "answered"
        assert answer["detail"]["option"] == 1
        assert answer["detail"]["fingerprint"] == fingerprint

    def test_recipe_keys_are_sent_as_one_sequence(self, make_client):
        client, stub = make_client([BLOCKED_GROK], GROK_PERMISSION)
        fingerprint = _fingerprint("grok", GROK_PERMISSION)

        # Option 0 is the one-shot Yes row — the screen's own number 3.
        response = client.post(
            "/pane/dialog/answer",
            json={"pane_id": "w2:p1", "fingerprint": fingerprint, "option": 0},
        )

        assert response.status_code == 200
        assert stub.key_calls == [{"pane_id": "w2:p1", "keys": ["3"]}]


class TestRaceGuard:
    def test_a_changed_dialog_is_a_409_and_presses_nothing(self, make_client):
        client, stub = make_client([BLOCKED_CLAUDE], CLAUDE_SELECT)
        stale = _fingerprint("claude", CLAUDE_SELECT.replace("Manzana", "Melón"))

        response = client.post(
            "/pane/dialog/answer",
            json={"pane_id": "w1:p3", "fingerprint": stale, "option": 0},
        )

        assert response.status_code == 409
        assert "cambió" in response.json()["detail"]
        assert stub.key_calls == []
        entries = audit_entries(client)
        assert entries[-1]["detail"]["reason"] == "changed"

    def test_a_dialog_that_vanished_is_a_409(self, make_client):
        client, stub = make_client([BLOCKED_CLAUDE], "$ pytest -q\n")

        response = client.post(
            "/pane/dialog/answer",
            json={"pane_id": "w1:p3", "fingerprint": "deadbeef", "option": 0},
        )

        assert response.status_code == 409
        assert stub.key_calls == []


class TestValidation:
    def test_an_unknown_pane_is_a_404(self, make_client):
        client, stub = make_client([BLOCKED_CLAUDE], CLAUDE_SELECT)

        response = client.post(
            "/pane/dialog/answer",
            json={"pane_id": "w9:p9", "fingerprint": "x", "option": 0},
        )

        assert response.status_code == 404
        assert stub.key_calls == []

    def test_an_out_of_range_option_is_a_422(self, make_client):
        client, stub = make_client([BLOCKED_CLAUDE], CLAUDE_SELECT)
        fingerprint = _fingerprint("claude", CLAUDE_SELECT)

        response = client.post(
            "/pane/dialog/answer",
            json={"pane_id": "w1:p3", "fingerprint": fingerprint, "option": 9},
        )

        assert response.status_code == 422
        assert stub.key_calls == []

    def test_a_blank_fingerprint_is_a_422(self, make_client):
        client, _ = make_client([BLOCKED_CLAUDE], CLAUDE_SELECT)

        response = client.post(
            "/pane/dialog/answer",
            json={"pane_id": "w1:p3", "fingerprint": "", "option": 0},
        )

        assert response.status_code == 422


class TestWriteClassification:
    def test_the_route_is_write_classified(self):
        from herdr_brain.access import classify

        assert classify("POST", "/pane/dialog/answer") == "write"


GROK_ASK = (FIXTURES / "grok-ask.txt").read_text(encoding="utf-8")
GROK_PARKED = (FIXTURES / "grok-ask-parked.txt").read_text(encoding="utf-8")


class TestStructuredInView:
    def test_view_carries_the_structured_dialog(self, make_client):
        client, _ = make_client([BLOCKED_CLAUDE], CLAUDE_SELECT)

        body = client.get("/view", params={"pane_id": "w1:p3"}).json()

        structured = body["structured"]
        assert structured["kind"] == "select"
        assert structured["agent"] == "claude"
        assert structured["options"][0]["label"] == "Manzana"
        # Recipes stay server-side: the payload names the option, never
        # the keys.
        assert "keys" not in structured["options"][0]
        assert structured["fingerprint"]

    def test_view_carries_the_unread_state_for_a_dialog_a_reader_refuses(
        self, make_client
    ):
        """A RECOGNIZED dialog in a state no button may drive (the
        parked card swallows bare digits) is the explicit unread card."""
        client, _ = make_client([BLOCKED_GROK], GROK_PARKED)

        body = client.get("/view", params={"pane_id": "w2:p1"}).json()

        assert body["structured"]["unread"] is True
        assert body["structured"]["agent"] == "grok"

    def test_view_omits_structured_when_no_reader_claimed_the_screen(
        self, make_client
    ):
        """A blocked screen NO recognizer claimed keeps the pre-S2
        contract: no ``structured`` field, the legacy pending state
        (blocked, kind unknown) and the manual keypad. Nothing is
        guessed and the /view shape is unchanged for such panes."""
        client, _ = make_client(
            [BLOCKED_CLAUDE], "some undecipherable modal\n│ │ │\n"
        )

        body = client.get("/view", params={"pane_id": "w1:p3"}).json()

        assert "structured" not in body
        assert body["pending"]["detected"] is True
        assert body["pending"]["kind"] is None

    def test_view_has_no_structured_dialog_when_not_blocked(self, make_client):
        working = AgentInfo(**{**BLOCKED_CLAUDE.__dict__, "status": "working"})
        client, _ = make_client([working], CLAUDE_SELECT)

        body = client.get("/view", params={"pane_id": "w1:p3"}).json()

        assert "structured" not in body


class TestWalkRecipe:
    """A dialog whose screen prints no digit is answered by the guard's
    pointer walk: one arrow, a re-read, and only then Enter."""

    def test_walk_answers_kiro_trust_without_ever_guessing_a_digit(self, settings):
        from herdr_brain.tools import BrainTools

        kiro_agent = AgentInfo(**{**BLOCKED_CLAUDE.__dict__, "agent": "kiro"})
        kiro_trust = (FIXTURES / "kiro-trust.txt").read_text(encoding="utf-8")
        moved = kiro_trust.replace("› Trust, continue", "  Trust, continue").replace(
            "  Don't trust, exit", "› Don't trust, exit"
        )
        screens = [kiro_trust, moved]

        class WalkingStub(DialogTapStub):
            def read_screen(self, pane_id, n_lines=None, source="visible", as_ansi=False):
                return screens[0]

            def send_keys(self, pane_id, keys):
                result = super().send_keys(pane_id, keys)
                if keys and keys[-1] == "down":
                    screens.pop(0)
                return result

        stub = WalkingStub([kiro_agent], kiro_trust)
        tools = BrainTools(settings, herdr=stub, sleeper=lambda _s: None)
        fingerprint = _fingerprint("kiro", kiro_trust)

        result = tools.answer_structured_dialog("w1:p3", fingerprint, 1)

        assert result["outcome"] == "answered"
        assert stub.key_calls == [
            {"pane_id": "w1:p3", "keys": ["down"]},
            {"pane_id": "w1:p3", "keys": ["enter"]},
        ]

    def test_mux_without_keys_refuses_gracefully(self, settings):
        """A pane on a multiplexer that cannot send logical keys (zellij)
        answers with an explainable refusal, never a crash."""
        from herdr_brain.mux.refusal import MuxRefusal
        from herdr_brain.tools import BrainTools

        kiro_agent = AgentInfo(**{**BLOCKED_CLAUDE.__dict__, "agent": "kiro"})
        kiro_trust = (FIXTURES / "kiro-trust.txt").read_text(encoding="utf-8")

        class RefusingStub(DialogTapStub):
            def send_keys(self, pane_id, keys):
                raise MuxRefusal(
                    "unsupported", "keys", "zellij writes characters, not key names"
                )

        stub = RefusingStub([kiro_agent], kiro_trust)
        tools = BrainTools(settings, herdr=stub, sleeper=lambda _s: None)
        fingerprint = _fingerprint("kiro", kiro_trust)

        result = tools.answer_structured_dialog("w1:p3", fingerprint, 0)

        assert result["outcome"] == "refused"
        assert result["reason"] == "send-failed"
        assert "zellij" in result["detail"]


class TestVoicePathReadsStructuredDialogs:
    """The spoken path gains the harness reading (S2, spec wording:
    voice READS the structured options; answers stay under the EXISTING
    verbal binding — no new authority). A Kiro trust prompt the generic
    heuristic never understood is read through the recognizer, spoken
    with its real labels, and answered with the same arrow-walk-from-
    the-known-selection choreography and turn-bound authorization.
    """

    def _tools(self, settings):
        from herdr_brain.tools import BrainTools

        kiro_agent = AgentInfo(**{**BLOCKED_CLAUDE.__dict__, "agent": "kiro"})
        screen = (FIXTURES / "kiro-trust.txt").read_text(encoding="utf-8")
        stub = DialogTapStub([kiro_agent], screen)
        tools = BrainTools(settings, herdr=stub, sleeper=lambda _s: None)
        return tools, kiro_agent, stub

    def test_read_dialog_shapes_structured_options_for_the_spoken_path(
        self, settings
    ):
        tools, kiro_agent, stub = self._tools(settings)

        dialog = tools.read_dialog(kiro_agent)

        assert dialog is not None
        assert dialog["question"] == "Trust this workspace?"
        assert [option["title"] for option in dialog["options"]] == [
            "Trust, continue", "Don't trust, exit",
        ]
        assert [option["n"] for option in dialog["options"]] == [1, 2]
        # The › pointer the terminal shows names the KNOWN selection.
        assert dialog["selected"] == 1

    def test_an_explained_structured_dialog_is_answered_under_the_binding(
        self, settings
    ):
        tools, kiro_agent, stub = self._tools(settings)

        tools.begin_turn()
        spoken = tools.explain_dialog(kiro_agent)
        assert spoken is not None
        assert [option["title"] for option in spoken["options"]] == [
            "Trust, continue", "Don't trust, exit",
        ]
        # The user's words must postdate the explanation.
        tools.begin_turn()

        outcome = json.loads(
            tools.answer_dialog(
                "opción 2", target=kiro_agent, user_utterance="opción 2"
            )
        )

        assert outcome["outcome"] == "answered"
        assert outcome["title"] == "Don't trust, exit"
        # The existing choreography: walk the KNOWN selection (row 1)
        # one step down, then commit — exactly as before S2.
        assert outcome["keys"] == ["down", "enter"]
        assert stub.key_calls == [
            {"pane_id": kiro_agent.pane_id, "keys": ["down", "enter"]}
        ]

    def test_a_same_turn_choice_still_refuses_and_presses_nothing(self, settings):
        tools, kiro_agent, stub = self._tools(settings)

        tools.begin_turn()
        assert tools.explain_dialog(kiro_agent) is not None
        # NO further turn: the user has not heard the options yet.

        outcome = json.loads(
            tools.answer_dialog(
                "opción 2", target=kiro_agent, user_utterance="opción 2"
            )
        )

        assert outcome["outcome"] == "refused"
        assert outcome["reason"] == "just-appeared"
        assert stub.key_calls == []
