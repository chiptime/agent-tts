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
import re
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
OPENCODE_MULTI = (FIXTURES / "opencode-multi.txt").read_text(encoding="utf-8")
OPENCODE_MULTI_CONFIRM = (FIXTURES / "opencode-multi-confirm.txt").read_text(
    encoding="utf-8"
)

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

BLOCKED_OPENCODE = AgentInfo(
    pane_id="w1:p3",
    agent="opencode",
    status="blocked",
    session_kind="id",
    session_value="00000000-1111-2222-3333-444444444444",
    cwd="/repo",
    title="OpenCode",
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
    def build(agents, screen, sleeper=None, stub=None, audit_content="preview"):
        stub = stub or DialogTapStub(agents, screen)
        state_dir = tmp_path / "state"

        def factory(cfg, **kwargs):
            from herdr_brain.tools import BrainTools

            tools = BrainTools(cfg, herdr=stub, sleeper=sleeper)
            return tools

        monkeypatch.setattr(server_module, "BrainTools", factory)
        cfg = Settings(**{**settings.__dict__, "state_dir": str(state_dir),
                          "audio_dir": str(tmp_path / "audio"),
                          "audit_content": audit_content})
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


def multi_screen(checked, pointer) -> str:
    """The claude multi-select shape with the given checked boxes (a
    set of 0-based row indexes) and the pointer on ``pointer``."""
    names = ("Cheese", "Pepperoni", "Mushrooms")
    rows = []
    for index, name in enumerate(names):
        marker = "Filled checkbox" if index in checked else "Empty checkbox"
        arrow = "❯" if index == pointer else " "
        rows.append(f"│ {arrow} {index + 1}. {marker} {name}")
    arrow = "❯" if pointer == 3 else " "
    rows.append(f"│ {arrow} 4. Submit")
    return (
        "│ Which toppings do you want?\n"
        "│ Pick every one you like.\n"
        "│\n"
        + "\n".join(rows)
        + "\n│\n"
        + "  Space to select · Enter to submit · ↑/↓ to navigate · Esc to cancel\n"
    )


class MultiSelectStub(DialogTapStub):
    """Drives the synthesized multi-select screen: a digit flips its box
    and moves the pointer there, the arrows walk the pointer, Enter
    closes the dialog."""

    def __init__(self, agents, checked, pointer=0):
        super().__init__(agents, "")
        self.checked = set(checked)
        self.pointer = pointer
        self.closed = False

    def read_screen(self, pane_id, n_lines=None, source="visible", as_ansi=False):
        if self.closed:
            return "$ pytest -q\n..........\n10 passed in 0.02s\n$ _"
        return multi_screen(self.checked, self.pointer)

    def send_keys(self, pane_id, keys):
        super().send_keys(pane_id, keys)
        for key in keys:
            if key in ("1", "2", "3"):
                index = int(key) - 1
                if index in self.checked:
                    self.checked.discard(index)
                else:
                    self.checked.add(index)
                self.pointer = index
            elif key == "4":
                self.pointer = 3
            elif key == "down":
                self.pointer = min(3, self.pointer + 1)
            elif key == "up":
                self.pointer = max(0, self.pointer - 1)
            elif key == "enter":
                self.closed = True
        return {"ok": True, "error": ""}


class HijackedMultiSelectStub(MultiSelectStub):
    """After the FIRST key the agent redraws a different question: the
    dialog identity changed mid-answer and nothing further may leave."""

    def __init__(self, agents, checked, pointer=0):
        super().__init__(agents, checked, pointer)
        self.hijacked = False

    def read_screen(self, pane_id, n_lines=None, source="visible", as_ansi=False):
        if self.hijacked:
            return multi_screen(self.checked, self.pointer).replace(
                "Which toppings", "Which sauces"
            )
        return super().read_screen(pane_id, n_lines, source, as_ansi)

    def send_keys(self, pane_id, keys):
        result = super().send_keys(pane_id, keys)
        self.hijacked = True
        return result


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


class TestMultiSelectAnswer:
    """``choices`` answers a multiselect: every box is toggled with a
    re-read after each key, the final state must match the request
    EXACTLY, and only then does the pointer walk to the submit row and
    Enter commit. Any surprise is a 409 ``changed`` with nothing further
    sent — the same discipline as a single-option tap."""

    def _post(self, client, choices, fingerprint):
        return client.post(
            "/pane/dialog/answer",
            json={
                "pane_id": "w1:p3",
                "fingerprint": fingerprint,
                "choices": choices,
            },
        )

    def test_choices_set_exactly_the_requested_boxes_then_submit(self, make_client):
        stub = MultiSelectStub([BLOCKED_CLAUDE], checked={1})
        client, _ = make_client(None, None, sleeper=lambda _s: None, stub=stub)
        fingerprint = _fingerprint("claude", stub.read_screen("w1:p3"))

        response = self._post(client, [0, 2], fingerprint)

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["choices"] == [0, 2]
        # One digit per box in row order — Pepperoni's pre-checked box
        # is turned OFF — then the pointer walks to Submit and Enter.
        assert stub.key_calls == [
            {"pane_id": "w1:p3", "keys": ["1"]},
            {"pane_id": "w1:p3", "keys": ["2"]},
            {"pane_id": "w1:p3", "keys": ["3"]},
            {"pane_id": "w1:p3", "keys": ["down"]},
            {"pane_id": "w1:p3", "keys": ["enter"]},
        ]
        assert stub.checked == {0, 2}
        entries = audit_entries(client)
        answer = next(e for e in entries if e["action"] == "pane.dialog.answer")
        assert answer["detail"]["outcome"] == "answered"
        assert answer["detail"]["choices"] == [0, 2]
        assert answer["detail"]["fingerprint"] == fingerprint

    def test_choices_with_nothing_to_toggle_walk_straight_to_submit(
        self, make_client
    ):
        stub = MultiSelectStub([BLOCKED_CLAUDE], checked={0, 2}, pointer=3)
        client, _ = make_client(None, None, sleeper=lambda _s: None, stub=stub)
        fingerprint = _fingerprint("claude", stub.read_screen("w1:p3"))

        response = self._post(client, [0, 2], fingerprint)

        assert response.status_code == 200
        assert stub.key_calls == [{"pane_id": "w1:p3", "keys": ["enter"]}]

    def test_a_midway_screen_change_is_a_409_and_nothing_further_leaves(
        self, make_client
    ):
        stub = HijackedMultiSelectStub([BLOCKED_CLAUDE], checked=set())
        client, _ = make_client(None, None, sleeper=lambda _s: None, stub=stub)
        fingerprint = _fingerprint("claude", stub.read_screen("w1:p3"))

        response = self._post(client, [0, 2], fingerprint)

        assert response.status_code == 409
        assert "cambió" in response.json()["detail"]
        # The first toggle left; the hijacked dialog got NOTHING further.
        assert stub.key_calls == [{"pane_id": "w1:p3", "keys": ["1"]}]
        entries = audit_entries(client)
        assert entries[-1]["detail"]["reason"] == "changed"
        assert entries[-1]["detail"]["choices"] == [0, 2]

    def test_a_changed_dialog_before_any_key_is_a_409(self, make_client):
        stub = MultiSelectStub([BLOCKED_CLAUDE], checked={1})
        client, _ = make_client(None, None, sleeper=lambda _s: None, stub=stub)
        stale = _fingerprint(
            "claude", stub.read_screen("w1:p3").replace("Mushrooms", "Anchovies")
        )

        response = self._post(client, [0], stale)

        assert response.status_code == 409
        assert stub.key_calls == []

    def test_choices_against_a_single_select_dialog_is_a_422(self, make_client):
        client, stub = make_client(
            [BLOCKED_CLAUDE], CLAUDE_SELECT, sleeper=lambda _s: None
        )
        fingerprint = _fingerprint("claude", CLAUDE_SELECT)

        response = self._post(client, [0], fingerprint)

        assert response.status_code == 422
        assert stub.key_calls == []

    def test_a_choice_naming_the_submit_row_is_a_422(self, make_client):
        stub = MultiSelectStub([BLOCKED_CLAUDE], checked={1})
        client, _ = make_client(None, None, sleeper=lambda _s: None, stub=stub)
        fingerprint = _fingerprint("claude", stub.read_screen("w1:p3"))

        response = self._post(client, [3], fingerprint)

        assert response.status_code == 422
        assert stub.key_calls == []

    def test_choices_and_option_together_is_a_422(self, make_client):
        client, _ = make_client(
            [BLOCKED_CLAUDE], CLAUDE_SELECT, sleeper=lambda _s: None
        )

        response = client.post(
            "/pane/dialog/answer",
            json={
                "pane_id": "w1:p3",
                "fingerprint": "x",
                "option": 0,
                "choices": [0],
            },
        )

        assert response.status_code == 422

    def test_an_empty_or_repeated_choices_list_is_a_422(self, make_client):
        client, _ = make_client(
            [BLOCKED_CLAUDE], CLAUDE_SELECT, sleeper=lambda _s: None
        )

        empty = self._post(client, [], "x")
        repeated = self._post(client, [0, 0], "x")

        assert empty.status_code == 422
        assert repeated.status_code == 422


def opencode_multi_screen(checked) -> str:
    """The pinned ``opencode-multi.txt`` list with the given 0-based
    boxes checked — the redraw the toggle choreography reads back."""
    screen = OPENCODE_MULTI
    for index, name in enumerate(("Red", "Green", "Blue", "Yellow")):
        glyph = "✓" if index in checked else " "
        screen = re.sub(
            rf"{index + 1}\. \[[xX✔✓ ]\] {re.escape(name)}",
            f"{index + 1}. [{glyph}] {name}",
            screen,
        )
    return screen


def opencode_confirm_screen(answer: str) -> str:
    """The pinned Confirm-tab review (``opencode-multi-confirm.txt``)
    with the given answer line."""
    return OPENCODE_MULTI_CONFIRM.replace("Colour: Red", f"Colour: {answer}")


class OpenCodeMultiStub(DialogTapStub):
    """Drives the pinned OpenCode multi-select exactly as the reference
    measured it (QUESTION_NOTES.md): a digit flips its own box,
    ``tab`` moves to the Confirm tab — and back, for with two tabs
    every move goes the other way — and ``enter`` submits only from
    the Confirm tab."""

    LABELS = ("Red", "Green", "Blue", "Yellow")

    def __init__(self, agents, checked):
        super().__init__(agents, "")
        self.checked = set(checked)
        self.on_confirm = False
        self.closed = False

    def read_screen(self, pane_id, n_lines=None, source="visible", as_ansi=False):
        if self.closed:
            return "$ pytest -q\n..........\n10 passed in 0.02s\n$ _"
        if self.on_confirm:
            picked = [
                name
                for index, name in enumerate(self.LABELS)
                if index in self.checked
            ]
            return opencode_confirm_screen(", ".join(picked) or "(not answered)")
        return opencode_multi_screen(self.checked)

    def send_keys(self, pane_id, keys):
        super().send_keys(pane_id, keys)
        for key in keys:
            if key in ("1", "2", "3", "4"):
                index = int(key) - 1
                if index in self.checked:
                    self.checked.discard(index)
                else:
                    self.checked.add(index)
            elif key == "tab":
                self.on_confirm = not self.on_confirm
            elif key == "enter" and self.on_confirm:
                self.closed = True
        return {"ok": True, "error": ""}


class HijackedOpenCodeTabStub(OpenCodeMultiStub):
    """After ``tab`` the redraw is ANOTHER dialog's list — never this
    dialog's Confirm review: the commit must stop with nothing further
    sent (no ``enter``)."""

    def read_screen(self, pane_id, n_lines=None, source="visible", as_ansi=False):
        if self.on_confirm:
            return opencode_multi_screen(self.checked).replace(
                "Which colours?", "Which sauces?"
            ).replace("Colour   Confirm", "Sauce   Confirm")
        return super().read_screen(pane_id, n_lines, source, as_ansi)


class WizardStripOpenCodeStub(OpenCodeMultiStub):
    """The same list under a THREE-chip strip (a many-question call's
    step): the active chip is painted style, so no commit can be
    derived from text alone."""

    def read_screen(self, pane_id, n_lines=None, source="visible", as_ansi=False):
        return super().read_screen(
            pane_id, n_lines, source, as_ansi
        ).replace("Colour   Confirm", "Colour   Size   Confirm")


class TestOpenCodeMultiTabCommit:
    """T4b: the pinned OpenCode multi-select carries NO submit row —
    the reference measured the commit as the Confirm tab's own
    choreography (a digit toggles its own box, ``tab`` reaches
    Confirm, ``enter`` submits there). The boxes are toggled and
    verified exactly as on Claude; then ``tab`` must land THIS dialog's
    Confirm review — the same tab chips, the review footer — before
    ``enter``. Anything else is a 409 with nothing further sent."""

    def _post(self, client, choices, fingerprint):
        return client.post(
            "/pane/dialog/answer",
            json={
                "pane_id": "w1:p3",
                "fingerprint": fingerprint,
                "choices": choices,
            },
        )

    def test_choices_toggle_then_tab_to_confirm_and_enter(self, make_client):
        stub = OpenCodeMultiStub([BLOCKED_OPENCODE], checked={0})
        client, _ = make_client(None, None, sleeper=lambda _s: None, stub=stub)
        fingerprint = _fingerprint("opencode", stub.read_screen("w1:p3"))

        response = self._post(client, [1, 2], fingerprint)

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["choices"] == [1, 2]
        # Red's pre-checked box turns OFF and Green and Blue turn ON —
        # one digit each, re-read between — then the measured commit:
        # tab reaches the Confirm tab, enter submits on it.
        assert stub.key_calls == [
            {"pane_id": "w1:p3", "keys": ["1"]},
            {"pane_id": "w1:p3", "keys": ["2"]},
            {"pane_id": "w1:p3", "keys": ["3"]},
            {"pane_id": "w1:p3", "keys": ["tab"]},
            {"pane_id": "w1:p3", "keys": ["enter"]},
        ]
        assert stub.closed is True
        entries = audit_entries(client)
        answer = next(e for e in entries if e["action"] == "pane.dialog.answer")
        assert answer["detail"]["outcome"] == "answered"
        assert answer["detail"]["choices"] == [1, 2]

    def test_a_wizard_step_strip_refuses_before_any_key(self, make_client):
        """A three-chip strip has another question's tab between the
        active one and Confirm (the wrap was measured), and Enter on a
        checkbox step would TOGGLE a box: no commit is derivable from
        text, so the answer refuses before sending a key."""
        stub = WizardStripOpenCodeStub([BLOCKED_OPENCODE], checked=set())
        client, _ = make_client(None, None, sleeper=lambda _s: None, stub=stub)
        fingerprint = _fingerprint("opencode", stub.read_screen("w1:p3"))

        response = self._post(client, [0], fingerprint)

        assert response.status_code == 422
        assert stub.key_calls == []

    def test_drift_after_tab_is_a_409_and_no_enter_leaves(self, make_client):
        stub = HijackedOpenCodeTabStub([BLOCKED_OPENCODE], checked=set())
        client, _ = make_client(None, None, sleeper=lambda _s: None, stub=stub)
        fingerprint = _fingerprint("opencode", stub.read_screen("w1:p3"))

        response = self._post(client, [0], fingerprint)

        assert response.status_code == 409
        assert "cambió" in response.json()["detail"]
        # The toggle and the tab left; the hijacked redraw — not this
        # dialog's Confirm review — got NOTHING further.
        assert stub.key_calls == [
            {"pane_id": "w1:p3", "keys": ["1"]},
            {"pane_id": "w1:p3", "keys": ["tab"]},
        ]
        assert stub.closed is False
        entries = audit_entries(client)
        assert entries[-1]["detail"]["reason"] == "changed"
        assert entries[-1]["detail"]["choices"] == [0]


def free_text_screen(pointer: int, typed: str = "") -> str:
    """The synthesized Claude select shape with a ``Type something.``
    row; once text is typed the row shows it verbatim."""
    row3 = typed if typed else "Type something."
    return (
        "│ Which fruit do you want?\n"
        "│ Pick the one you like best.\n"
        "│\n"
        f"│ {'❯' if pointer == 0 else ' '} 1. Manzana\n"
        f"│ {'❯' if pointer == 1 else ' '} 2. Pera\n"
        f"│ {'❯' if pointer == 2 else ' '} 3. {row3}\n"
        "│\n"
        "  Enter to select · ↑/↓ to navigate · Esc to cancel\n"
    )


class FreeTextStub(DialogTapStub):
    """Drives the synthesized free-text select: a digit moves the
    pointer to its row (the field takes the keyboard), literal text
    lands in the row verbatim, and Enter commits the typed answer."""

    def __init__(self, agents):
        super().__init__(agents, "")
        self.pointer = 0
        self.typed = ""
        self.closed = False
        self.text_calls: list = []

    def read_screen(self, pane_id, n_lines=None, source="visible", as_ansi=False):
        if self.closed:
            return "$ pytest -q\n..........\n10 passed in 0.02s\n$ _"
        return free_text_screen(self.pointer, self.typed)

    def send_keys(self, pane_id, keys):
        super().send_keys(pane_id, keys)
        for key in keys:
            if key in ("1", "2", "3"):
                self.pointer = int(key) - 1
            elif key == "enter" and self.typed:
                self.closed = True
        return {"ok": True, "error": ""}

    def send_text(self, pane_id, text):
        self.text_calls.append({"pane_id": pane_id, "text": text})
        self.typed = text


class HijackedFreeTextStub(FreeTextStub):
    """After the Nth key the agent redraws a different question: the
    dialog identity changed mid-recipe and nothing further — no more
    clears, no text, no Enter — may leave."""

    def __init__(self, agents, hijack_after=2):
        super().__init__(agents)
        self.hijack_after = hijack_after
        self.key_sends = 0
        self.hijacked = False

    def read_screen(self, pane_id, n_lines=None, source="visible", as_ansi=False):
        if self.hijacked:
            return free_text_screen(self.pointer, self.typed).replace(
                "Which fruit", "Which sauce"
            )
        return super().read_screen(pane_id, n_lines, source, as_ansi)

    def send_keys(self, pane_id, keys):
        result = super().send_keys(pane_id, keys)
        self.key_sends += 1
        if self.key_sends >= self.hijack_after:
            self.hijacked = True
        return result


class TestFreeTextAnswer:
    """``text`` answers a free-text option (S3): the recipe selects the
    row with its digit, clears the field (ctrl+u then ctrl+k, one
    verified step each), types the sanitized text, re-reads that the
    text is on screen, and only then commits with Enter. Any surprise
    mid-recipe is a 409 ``changed`` with nothing further sent."""

    def _post(self, client, option, text, fingerprint):
        return client.post(
            "/pane/dialog/answer",
            json={
                "pane_id": "w1:p3",
                "fingerprint": fingerprint,
                "option": option,
                "text": text,
            },
        )

    def test_text_clears_types_verifies_then_commits(self, make_client):
        stub = FreeTextStub([BLOCKED_CLAUDE])
        client, _ = make_client(None, None, sleeper=lambda _s: None, stub=stub)
        fingerprint = _fingerprint("claude", stub.read_screen("w1:p3"))

        response = self._post(client, 2, "usa la rama main", fingerprint)

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["option"] == 2
        # The digit focuses the field; the clears are one verified key
        # each; the text rides the literal-text surface; Enter commits.
        assert stub.key_calls == [
            {"pane_id": "w1:p3", "keys": ["3"]},
            {"pane_id": "w1:p3", "keys": ["ctrl+u"]},
            {"pane_id": "w1:p3", "keys": ["ctrl+k"]},
            {"pane_id": "w1:p3", "keys": ["enter"]},
        ]
        assert stub.text_calls == [
            {"pane_id": "w1:p3", "text": "usa la rama main"}
        ]
        assert stub.closed is True
        entries = audit_entries(client)
        answer = next(e for e in entries if e["action"] == "pane.dialog.answer")
        assert answer["detail"]["outcome"] == "answered"
        assert answer["detail"]["option"] == 2
        assert answer["detail"]["text"] == "usa la rama main"
        assert answer["detail"]["fingerprint"] == fingerprint

    def test_the_typed_text_is_sanitized_like_a_pane_reply(self, make_client):
        """A literal newline in the answer would submit the field early:
        the same collapse /pane/reply applies protects this surface
        too."""
        stub = FreeTextStub([BLOCKED_CLAUDE])
        client, _ = make_client(None, None, sleeper=lambda _s: None, stub=stub)
        fingerprint = _fingerprint("claude", stub.read_screen("w1:p3"))

        response = self._post(client, 2, "usa la\nrama   main", fingerprint)

        assert response.status_code == 200
        assert stub.text_calls == [
            {"pane_id": "w1:p3", "text": "usa la rama main"}
        ]

    def test_drift_mid_recipe_is_a_409_and_nothing_further_leaves(
        self, make_client
    ):
        stub = HijackedFreeTextStub([BLOCKED_CLAUDE])
        client, _ = make_client(None, None, sleeper=lambda _s: None, stub=stub)
        fingerprint = _fingerprint("claude", stub.read_screen("w1:p3"))

        response = self._post(client, 2, "usa la rama main", fingerprint)

        assert response.status_code == 409
        assert "cambió" in response.json()["detail"]
        # The digit and the first clear left; the hijacked dialog got
        # NOTHING further: no second clear, no text, no Enter.
        assert stub.key_calls == [
            {"pane_id": "w1:p3", "keys": ["3"]},
            {"pane_id": "w1:p3", "keys": ["ctrl+u"]},
        ]
        assert stub.text_calls == []
        assert stub.closed is False
        entries = audit_entries(client)
        assert entries[-1]["detail"]["reason"] == "changed"

    def test_text_on_a_dialog_without_a_free_text_option_is_a_422(
        self, make_client
    ):
        client, stub = make_client(
            [BLOCKED_CLAUDE], CLAUDE_SELECT, sleeper=lambda _s: None
        )
        fingerprint = _fingerprint("claude", CLAUDE_SELECT)

        response = self._post(client, 0, "hola", fingerprint)

        assert response.status_code == 422
        assert stub.key_calls == []

    def test_destructive_text_is_delivered_the_guard_is_client_side(
        self, make_client
    ):
        """The destructive analyzer runs in the PWA before posting; the
        server does not censor a confirmed answer — it delivers the text
        and audits it, exactly like /pane/reply."""
        stub = FreeTextStub([BLOCKED_CLAUDE])
        client, _ = make_client(None, None, sleeper=lambda _s: None, stub=stub)
        fingerprint = _fingerprint("claude", stub.read_screen("w1:p3"))

        response = self._post(client, 2, "sudo rm -rf /tmp/demo", fingerprint)

        assert response.status_code == 200
        assert stub.text_calls == [
            {"pane_id": "w1:p3", "text": "sudo rm -rf /tmp/demo"}
        ]
        assert stub.closed is True

    def test_the_audit_line_carries_the_pane_reply_preview_discipline(
        self, make_client
    ):
        from herdr_brain.audit import MAX_STR

        stub = FreeTextStub([BLOCKED_CLAUDE])
        client, _ = make_client(None, None, sleeper=lambda _s: None, stub=stub)
        fingerprint = _fingerprint("claude", stub.read_screen("w1:p3"))
        long_text = "responde con " + "x" * (MAX_STR + 10)

        assert self._post(client, 2, long_text, fingerprint).status_code == 200

        entries = audit_entries(client)
        answer = next(e for e in entries if e["action"] == "pane.dialog.answer")
        # Preview mode: bounded, single-line — same cap as pane.reply.
        assert answer["detail"]["text"] == long_text[:MAX_STR] + "…"

    def test_none_audit_mode_redacts_the_free_text(self, make_client):
        stub = FreeTextStub([BLOCKED_CLAUDE])
        client, _ = make_client(
            None, None, sleeper=lambda _s: None, stub=stub, audit_content="none"
        )
        fingerprint = _fingerprint("claude", stub.read_screen("w1:p3"))

        response = self._post(client, 2, "purge the database", fingerprint)

        assert response.status_code == 200
        entries = audit_entries(client)
        answer = next(e for e in entries if e["action"] == "pane.dialog.answer")
        assert answer["detail"]["text"] == "⟨redacted⟩"
        assert answer["detail"]["outcome"] == "answered"
        assert "purge the database" not in audit_text(client)

    def test_a_text_the_screen_never_shows_is_never_committed(self, make_client):
        """The pre-Enter check is a re-read, not an assumption: a typed
        text that does not show in the field stops the recipe before
        Enter — half-typed state is never submitted blindly."""

        class VanishingTextStub(FreeTextStub):
            def send_text(self, pane_id, text):
                super().send_text(pane_id, text)
                self.typed = ""  # the terminal dropped the text

        stub = VanishingTextStub([BLOCKED_CLAUDE])
        client, _ = make_client(None, None, sleeper=lambda _s: None, stub=stub)
        fingerprint = _fingerprint("claude", stub.read_screen("w1:p3"))

        response = self._post(client, 2, "usa la rama main", fingerprint)

        assert response.status_code == 409
        assert [call["keys"] for call in stub.key_calls] == [
            ["3"], ["ctrl+u"], ["ctrl+k"],
        ]
        assert stub.closed is False


class TestFreeTextValidation:
    def test_text_without_an_option_is_a_422(self, make_client):
        client, stub = make_client(
            [BLOCKED_CLAUDE], CLAUDE_SELECT, sleeper=lambda _s: None
        )

        response = client.post(
            "/pane/dialog/answer",
            json={
                "pane_id": "w1:p3",
                "fingerprint": "x",
                "text": "hola",
            },
        )

        assert response.status_code == 422
        assert stub.key_calls == []

    def test_text_with_choices_is_a_422(self, make_client):
        client, _ = make_client(
            [BLOCKED_CLAUDE], CLAUDE_SELECT, sleeper=lambda _s: None
        )

        response = client.post(
            "/pane/dialog/answer",
            json={
                "pane_id": "w1:p3",
                "fingerprint": "x",
                "choices": [0],
                "text": "hola",
            },
        )

        assert response.status_code == 422

    def test_blank_text_is_a_422(self, make_client):
        client, _ = make_client(
            [BLOCKED_CLAUDE], CLAUDE_SELECT, sleeper=lambda _s: None
        )

        response = client.post(
            "/pane/dialog/answer",
            json={
                "pane_id": "w1:p3",
                "fingerprint": "x",
                "option": 0,
                "text": "   ",
            },
        )

        assert response.status_code == 422


WIZARD_STEP1 = (FIXTURES / "claude-wizard.txt").read_text(encoding="utf-8")


class WizardTwoStepStub(DialogTapStub):
    """Drives a two-question wizard with the S4 fixtures: step 1 is the
    select screen (``1/2``), step 2 the multi-select screen (``2/2``).
    A digit+Enter on step 1 advances to step 2; the multi-select keys
    behave like MultiSelectStub; the submit's Enter closes the wizard."""

    def __init__(self, agents):
        super().__init__(agents, "")
        self.phase = 1
        self.checked = set()
        self.pointer = 0

    def _render(self):
        if self.phase == 1:
            return WIZARD_STEP1
        if self.phase == 2:
            # The marker must sit directly above the block (with only a
            # blank between), exactly like the wizard fixtures.
            return multi_screen(self.checked, self.pointer).replace(
                "│ Which toppings do you want?\n│ Pick every one you like.\n",
                "│ 2/2 Which toppings?\n",
            )
        return "$ pytest -q\n..........\n10 passed in 0.02s\n$ _"

    def read_screen(self, pane_id, n_lines=None, source="visible", as_ansi=False):
        return self._render()

    def send_keys(self, pane_id, keys):
        super().send_keys(pane_id, keys)
        for key in keys:
            if self.phase == 1:
                if key == "enter":
                    self.phase = 2
            elif self.phase == 2:
                if key in ("1", "2", "3"):
                    index = int(key) - 1
                    if index in self.checked:
                        self.checked.discard(index)
                    else:
                        self.checked.add(index)
                    self.pointer = index
                elif key == "4":
                    self.pointer = 3
                elif key == "down":
                    self.pointer = min(3, self.pointer + 1)
                elif key == "up":
                    self.pointer = max(0, self.pointer - 1)
                elif key == "enter":
                    self.phase = 3
        return {"ok": True, "error": ""}


CLAUDE_MODEL_PICKER = (FIXTURES / "claude-model-picker.txt").read_text(
    encoding="utf-8"
)
CLAUDE_SWITCH_MODEL = (FIXTURES / "claude-switch-model-yes.txt").read_text(
    encoding="utf-8"
)


def picker_screen(pointer: int) -> str:
    """The pinned Claude /model list with the pointer moved to
    ``pointer`` (0-based over the five rows)."""
    screen = CLAUDE_MODEL_PICKER
    rows = ["Default (recommended) ✔", "Opus (1M context)", "Fable", "Sonnet", "Haiku"]
    for index, label in enumerate(rows):
        glyph = "❯" if index == pointer else " "
        screen = re.sub(
            rf"^(\s*)[❯ ] {index + 1}\. {re.escape(label)}",
            rf"\1{glyph} {index + 1}. {label}",
            screen,
            flags=re.MULTILINE,
        )
    return screen


def confirm_screen(pointer: int) -> str:
    """The pinned "Switch model?" confirmation with the pointer on
    ``pointer`` (0 = Yes row, 1 = No row)."""
    yes = "❯ 1. Yes, switch to Fable 5.1"
    no = "❯ 2. No, go back"
    plain_yes = "  1. Yes, switch to Fable 5.1"
    plain_no = "  2. No, go back"
    if pointer == 0:
        return CLAUDE_SWITCH_MODEL.replace(no, plain_no)
    return CLAUDE_SWITCH_MODEL.replace(yes, plain_yes).replace(plain_no, no)


class ModelPickerStub(DialogTapStub):
    """Drives the pinned /model picker exactly as Claude Code does: the
    arrows move the pointer over the list, Enter on a row closes the
    list, and — because the conversation is cached — the "Switch
    model?" confirmation appears; Enter on its pointed row closes it
    too. A DIGIT would confirm and rewrite the default model: the
    choreography under test must never send one."""

    def __init__(self, agents):
        super().__init__(agents, "")
        self.pointer = 0
        self.confirm_pointer = 0
        self.stage = "list"

    def read_screen(self, pane_id, n_lines=None, source="visible", as_ansi=False):
        if self.stage == "list":
            return picker_screen(self.pointer)
        if self.stage == "confirm":
            return confirm_screen(self.confirm_pointer)
        return "$ claude\n❯ _"

    def send_keys(self, pane_id, keys):
        super().send_keys(pane_id, keys)
        for key in keys:
            if self.stage == "list":
                if key == "down":
                    self.pointer = min(4, self.pointer + 1)
                elif key == "up":
                    self.pointer = max(0, self.pointer - 1)
                elif key == "enter":
                    self.stage = "confirm"
            elif self.stage == "confirm":
                if key == "down":
                    self.confirm_pointer = min(1, self.confirm_pointer + 1)
                elif key == "up":
                    self.confirm_pointer = max(0, self.confirm_pointer - 1)
                elif key == "enter":
                    self.stage = "done"
        return {"ok": True, "error": ""}


class TestModelPickerAnswer:
    """parity-f7d S2: a model picker is answered by the guard's pointer
    walk — one arrow, a re-read, only then Enter — and NEVER by a digit
    (in these lists a digit confirms and rewrites the default model,
    ADR 0009)."""

    def _post(self, client, option, fingerprint):
        return client.post(
            "/pane/dialog/answer",
            json={"pane_id": "w1:p3", "fingerprint": fingerprint, "option": option},
        )

    def test_a_tap_walks_with_arrows_and_enter_never_a_digit(self, make_client):
        stub = ModelPickerStub([BLOCKED_CLAUDE])
        client, _ = make_client(None, None, sleeper=lambda _s: None, stub=stub)
        fingerprint = _fingerprint("claude", stub.read_screen("w1:p3"))

        # Option 2 is Fable; the pointer stands on row 0.
        response = self._post(client, 2, fingerprint)

        assert response.status_code == 200
        assert response.json()["option"] == 2
        assert stub.key_calls == [
            {"pane_id": "w1:p3", "keys": ["down"]},
            {"pane_id": "w1:p3", "keys": ["down"]},
            {"pane_id": "w1:p3", "keys": ["enter"]},
        ]
        # THE invariant (ADR 0009): no digit ever reaches a model
        # picker, even though the rows are numbered.
        sent = [key for call in stub.key_calls for key in call["keys"]]
        assert all(key not in "0123456789" for key in sent)
        assert stub.stage == "confirm"

    def test_the_pointed_row_commits_with_enter_alone(self, make_client):
        stub = ModelPickerStub([BLOCKED_CLAUDE])
        client, _ = make_client(None, None, sleeper=lambda _s: None, stub=stub)
        fingerprint = _fingerprint("claude", stub.read_screen("w1:p3"))

        response = self._post(client, 0, fingerprint)

        assert response.status_code == 200
        assert stub.key_calls == [{"pane_id": "w1:p3", "keys": ["enter"]}]

    def test_drift_under_the_tap_is_a_409_and_presses_nothing(self, make_client):
        stub = ModelPickerStub([BLOCKED_CLAUDE])
        client, _ = make_client(None, None, sleeper=lambda _s: None, stub=stub)
        stale = _fingerprint("claude", picker_screen(3))

        response = self._post(client, 2, stale)

        assert response.status_code == 409
        assert stub.key_calls == []

    def test_the_confirmation_stage_walks_and_never_digits_either(
        self, make_client
    ):
        stub = ModelPickerStub([BLOCKED_CLAUDE])
        stub.stage = "confirm"
        client, _ = make_client(None, None, sleeper=lambda _s: None, stub=stub)
        fingerprint = _fingerprint("claude", stub.read_screen("w1:p3"))

        # "No, go back" (option 1): the pointer sits on row 0 (Yes).
        response = self._post(client, 1, fingerprint)

        assert response.status_code == 200
        assert stub.key_calls == [
            {"pane_id": "w1:p3", "keys": ["down"]},
            {"pane_id": "w1:p3", "keys": ["enter"]},
        ]
        sent = [key for call in stub.key_calls for key in call["keys"]]
        assert all(key not in "0123456789" for key in sent)


class TestWizardTwoStepFlow:
    """S4 proof: the wizard needs NO endpoint fields. Step 1 answers
    with ``option`` and the screen advances to step 2; step 2 answers
    with ``choices`` and the pane resumes. A step-1 answer that arrives
    when the screen is already at step 2 is the 409 ``changed`` the PWA
    surfaces as its abort card — the guard, not the client, decides."""

    def test_both_steps_flow_through_option_then_choices(self, make_client):
        stub = WizardTwoStepStub([BLOCKED_CLAUDE])
        client, _ = make_client(None, None, sleeper=lambda _s: None, stub=stub)

        step1 = recognize("claude", WIZARD_STEP1)
        assert step1.payload()["wizard"] == {"step": 1, "steps": 2}
        first = client.post(
            "/pane/dialog/answer",
            json={
                "pane_id": "w1:p3",
                "fingerprint": step1.fingerprint,
                "option": 1,
            },
        )
        assert first.status_code == 200
        assert stub.key_calls == [{"pane_id": "w1:p3", "keys": ["2", "enter"]}]

        step2 = recognize("claude", stub.read_screen("w1:p3"))
        assert step2.kind == "multiselect"
        assert step2.payload()["wizard"] == {"step": 2, "steps": 2}
        second = client.post(
            "/pane/dialog/answer",
            json={
                "pane_id": "w1:p3",
                "fingerprint": step2.fingerprint,
                "choices": [0, 2],
            },
        )
        assert second.status_code == 200
        assert second.json()["choices"] == [0, 2]
        assert [call["keys"] for call in stub.key_calls[1:]] == [
            ["1"], ["3"], ["down"], ["enter"],
        ]
        assert stub.phase == 3

        entries = audit_entries(client)
        answers = [e for e in entries if e["action"] == "pane.dialog.answer"]
        assert [a["detail"].get("option") for a in answers] == [1, None]
        assert [a["detail"].get("choices") for a in answers] == [None, [0, 2]]

    def test_a_stale_step_fingerprint_against_the_next_screen_is_a_409(
        self, make_client
    ):
        """The server-side stop: a step answered against a screen that
        already moved to another step sends NOTHING — the drift the PWA
        must surface, never retry around."""
        stub = WizardTwoStepStub([BLOCKED_CLAUDE])
        client, _ = make_client(None, None, sleeper=lambda _s: None, stub=stub)
        step1 = recognize("claude", WIZARD_STEP1)
        stub.phase = 2  # the screen already advanced

        response = client.post(
            "/pane/dialog/answer",
            json={
                "pane_id": "w1:p3",
                "fingerprint": step1.fingerprint,
                "option": 0,
            },
        )

        assert response.status_code == 409
        assert "cambió" in response.json()["detail"]
        assert stub.key_calls == []
