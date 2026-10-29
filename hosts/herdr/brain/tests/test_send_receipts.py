"""Verified sends (S12): outcome classification, receipts, stop and steer.

The classifier is a PURE function over evidence — the screen read before
the send, the screens read after it and the mux's own claim — so every
row of the outcome table below is a stub mux screen sequence, no real
pane involved. The tools-level tests drive :class:`BrainTools` with a
scripted mux double through the same public methods the routes call.
"""

from __future__ import annotations

import pytest

from herdr_brain.herdr import AgentInfo, HerdrError
from herdr_brain.send_receipts import (
    DISCARD_RECIPES,
    INTERRUPT_RECIPES,
    OUTCOMES,
    STEER_HARNESSES,
    SendReceiptStore,
    classify_followup,
    classify_send,
    discard_recipe,
    echo_tail,
    input_line,
    interrupt_recipe,
    steer_supported,
    text_echoed_on_input,
)
from herdr_brain.tools import BrainTools


def _agent(pane_id="w1:p2", agent="claude", status="idle"):
    return AgentInfo(
        pane_id=pane_id,
        agent=agent,
        status=status,
        session_kind="opencode",
        session_value="sess-1",
        cwd="/tmp",
        title="the pane",
        focused=True,
    )


class ScriptedMux:
    """Mux double with a scripted screen sequence and prompt result.

    ``screens`` are returned one per read_screen call; the last one
    repeats once the sequence is exhausted, so a poll's final read is the
    stable evidence the classifier reasons about.
    """

    def __init__(self, agents, screens, prompt=None, keys=None, fail_read=False):
        self._agents = agents
        self._screens = list(screens)
        self._prompt = prompt or {"ok": True, "status": "done", "output": "ok"}
        self._keys = keys or {"ok": True, "error": ""}
        self._fail_read = fail_read
        self.read_calls: list[str] = []
        self.prompt_calls: list[dict] = []
        self.key_calls: list[str] = []

    def list_agents(self):
        return list(self._agents)

    def read_screen(self, pane_id, n_lines=None, source="visible", as_ansi=False):
        self.read_calls.append(pane_id)
        if self._fail_read:
            raise HerdrError("read failed")
        if not self._screens:
            return ""
        if len(self._screens) > 1:
            return self._screens.pop(0)
        return self._screens[0]

    def send_prompt(self, pane_id, text, timeout_ms=None):
        self.prompt_calls.append(
            {"pane_id": pane_id, "text": text, "timeout_ms": timeout_ms}
        )
        return self._prompt

    def send_keys(self, pane_id, keys):
        self.key_calls.append((pane_id, list(keys)))
        return self._keys


def _tools(mux):
    sleeps: list[float] = []

    def fake_sleep(seconds):
        sleeps.append(seconds)

    tools = BrainTools(
        _settings_like(), herdr=mux, clock=lambda: 0.0, sleeper=fake_sleep
    )
    tools._sleeps = sleeps
    return tools


class _SettingsLike:
    prompt_timeout_ms = 15_000


def _settings_like():
    return _SettingsLike()


# ── the closed outcome set ────────────────────────────────────────────────────


def test_the_outcome_set_is_closed_and_ordered_for_docs():
    assert OUTCOMES == (
        "effective",
        "sent",
        "typed_not_submitted",
        "discarded",
        "refused",
        "unknown",
    )


# ── input-line evidence helpers ───────────────────────────────────────────────


def test_input_line_is_the_last_non_empty_line():
    assert input_line("\n\n> say something\n\n") == "> say something"
    assert input_line("") == ""


def test_echo_tail_is_a_short_stable_marker_of_the_text():
    assert echo_tail("por favor arregla el login") == "por favor arregla el login"
    long = "x" * 80 + "-the-end-"
    assert echo_tail(long) == long[-40:]


def test_text_echoed_on_input_matches_the_tail_wherever_the_box_paints_it():
    screen = "❯ 1) Yes  2) No\n\n  por favor arregla el login"
    assert text_echoed_on_input(screen, "por favor arregla el login")
    assert not text_echoed_on_input("transcript: nada en la entrada", "otro texto")


# ── classify_send: one row per outcome ────────────────────────────────────────


def test_done_waits_classify_effective():
    before = "❯ 1) Yes  2) No"
    after = "user: run the tests\nspinner…"
    outcome, _ = classify_send(before, [after], "run the tests", "done")
    assert outcome == "effective"


def test_blocked_send_is_refused():
    outcome, detail = classify_send("screen", ["screen"], "hi", "blocked")
    assert outcome == "refused"
    assert detail


@pytest.mark.parametrize("status", ["stalled", "timeout"])
def test_written_but_unconfirmed_waits_classify_sent(status):
    before = "❯ idle prompt"
    after = "user: sigue adelante\nassistant: thinking"
    outcome, _ = classify_send(before, [after], "sigue adelante", status)
    assert outcome == "sent"


def test_text_still_on_the_input_line_with_a_frozen_body_is_typed_not_submitted():
    # The input-box model of a chat TUI: the box line carries the draft,
    # the body above it does not move until the turn is taken.
    before = "❯ 1) Yes  2) No\n│ >"
    after = "❯ 1) Yes  2) No\n│ > sigue adelante"
    outcome, _ = classify_send(before, [after, after], "sigue adelante", "timeout")
    assert outcome == "typed_not_submitted"


def test_the_poll_upgrades_a_late_takeover_to_sent():
    before = "❯ 1) Yes  2) No"
    typing = "❯ 1) Yes  2) No\n  sigue adelante"
    took = "user: sigue adelante\nworking…"
    outcome, _ = classify_send(before, [typing, took], "sigue adelante", "timeout")
    assert outcome == "sent"


def test_fire_and_forget_with_screen_movement_is_sent():
    before = "$ "
    after = "$ ls\nfile-a file-b"
    outcome, _ = classify_send(before, [after], "ls", "unknown")
    assert outcome == "sent"


def test_fire_and_forget_with_no_evidence_is_unknown():
    outcome, detail = classify_send("$ ", ["$ "], "ls", "unknown")
    assert outcome == "unknown"
    assert detail


def test_unreadable_after_screens_degrade_to_unknown_or_sent_by_claim():
    assert classify_send("$ ", [], "ls", "unknown")[0] == "unknown"
    # The mux CLAIMS the prompt was written: submitted, unconfirmed.
    assert classify_send("$ ", [], "ls", "timeout")[0] == "sent"


# ── classify_followup: submit / discard ───────────────────────────────────────


def test_followup_resolves_when_the_echo_left_the_input_line():
    screens = ["user: sigue adelante\nworking"]
    outcome, _ = classify_followup(screens, "sigue adelante", "sent")
    assert outcome == "sent"


def test_followup_stays_typed_when_the_echo_survives():
    screens = ["❯ 1) Yes\n  sigue adelante"]
    outcome, detail = classify_followup(screens, "sigue adelante", "sent")
    assert outcome == "typed_not_submitted"
    assert detail


def test_discard_followup_names_the_discarded_outcome():
    screens = ["❯ 1) Yes"]
    outcome, _ = classify_followup(screens, "sigue adelante", "discarded")
    assert outcome == "discarded"


def test_unreadable_followup_is_unknown():
    outcome, _ = classify_followup([], "text", "sent")
    assert outcome == "unknown"


# ── per-harness tables ────────────────────────────────────────────────────────


def test_steer_is_supported_by_chat_tuis_only():
    assert steer_supported("claude")
    assert steer_supported("opencode")
    assert steer_supported("codex")
    assert not steer_supported("shell")
    assert not steer_supported("")


def test_interrupt_recipes_are_esc_for_known_tuis_and_refusal_otherwise():
    assert INTERRUPT_RECIPES
    for agent in ("claude", "opencode", "codex"):
        recipe = interrupt_recipe(agent)
        assert recipe == ("esc",)
    assert interrupt_recipe("shell") is None
    assert interrupt_recipe("") is None


def test_discard_recipe_defaults_to_the_safe_esc():
    for agent in ("claude", "opencode", "codex", "shell", ""):
        assert discard_recipe(agent) == DISCARD_RECIPES.get(agent, ("esc",))
    assert all(keys == ("esc",) for keys in DISCARD_RECIPES.values())


def test_recipes_stay_inside_the_key_allowlist():
    from herdr_brain.herdr import SEND_KEY_ALLOWLIST

    for recipe in (*INTERRUPT_RECIPES.values(), *DISCARD_RECIPES.values()):
        assert recipe, "a recipe table row must not be empty"
        for key in recipe:
            assert key in SEND_KEY_ALLOWLIST


# ── the bounded receipt store ─────────────────────────────────────────────────


def test_store_records_and_lists_newest_first():
    store = SendReceiptStore(clock=lambda: 1.0, id_factory=iter(["rct-a", "rct-b"]).__next__)
    first = store.record("w1:p2", "sent", "ok")
    second = store.record("w1:p9", "typed_not_submitted", "still typed")
    assert first["id"] == "rct-a"
    assert [r["id"] for r in store.list()] == ["rct-b", "rct-a"]
    assert [r["id"] for r in store.list("w1:p2")] == ["rct-a"]


def test_store_bounds_itself_dropping_the_oldest():
    seq = iter(f"rct-{i:03d}" for i in range(70))
    store = SendReceiptStore(max_receipts=8, clock=lambda: 0.0, id_factory=seq.__next__)
    for _ in range(10):
        store.record("w1:p2", "sent")
    kept = [r["id"] for r in store.list()]
    assert len(kept) == 8
    assert kept[0] == "rct-009"
    assert kept[-1] == "rct-002"


def test_store_resolve_flips_the_outcome_once():
    store = SendReceiptStore(clock=lambda: 0.0, id_factory=iter(["rct-a"]).__next__)
    receipt = store.record("w1:p2", "typed_not_submitted", "still typed")
    resolved = store.resolve("rct-a", "discarded", "input cleared")
    assert resolved["outcome"] == "discarded"
    assert resolved["resolved_at"] is not None
    assert store.get("rct-a")["outcome"] == "discarded"
    assert store.get("rct-missing") is None


def test_store_masks_detail_and_text_hint_before_keeping_them():
    store = SendReceiptStore(clock=lambda: 0.0, id_factory=iter(["rct-a"]).__next__)
    receipt = store.record(
        "w1:p2",
        "sent",
        "sk-ant-api03-abcdef123456 still on screen",
        text_hint="token sk-ant-api03-abcdef123456",
    )
    assert "sk-ant-api03" not in receipt["detail"]
    assert "sk-ant-api03" not in receipt["text_hint"]


def test_receipt_ids_carry_their_own_prefix():
    store = SendReceiptStore(clock=lambda: 0.0)
    receipt = store.record("w1:p2", "sent")
    assert receipt["id"].startswith("rct-")


# ── BrainTools.verified_reply_to_pane (through the public method) ─────────────


def test_verified_reply_effective_reads_before_and_polls_after():
    mux = ScriptedMux(
        [_agent()],
        screens=[
            "❯ 1) Yes  2) No",           # before
            "user: run the tests",         # after poll read
        ],
        prompt={"ok": True, "status": "done", "output": "done"},
    )
    result = _tools(mux).verified_reply_to_pane("w1:p2", "run the tests")
    assert result["outcome"] == "delivered"
    assert result["receipt"]["outcome"] == "effective"
    assert mux.prompt_calls and mux.prompt_calls[0]["pane_id"] == "w1:p2"
    # The read BEFORE the send happened (one read), then the poll.
    assert len(mux.read_calls) >= 2


def test_verified_reply_detects_typed_not_submitted():
    before = "❯ 1) Yes  2) No\n│ >"
    typing = "❯ 1) Yes  2) No\n│ > sigue adelante"
    mux = ScriptedMux(
        [_agent()],
        screens=[before, typing],
        prompt={"ok": False, "status": "timeout", "output": ""},
    )
    tools = _tools(mux)
    result = tools.verified_reply_to_pane("w1:p2", "sigue adelante")
    assert result["receipt"]["outcome"] == "typed_not_submitted"
    # A bounded poll: the classifier saw the stable echo, not one read.
    assert len(mux.read_calls) >= 3
    assert tools._sleeps  # the poll waits between reads


def test_verified_reply_blocked_keeps_the_dialog_contract():
    mux = ScriptedMux(
        [_agent(status="blocked")],
        screens=["dialog"],
        prompt={"ok": False, "status": "blocked", "output": ""},
    )
    result = _tools(mux).verified_reply_to_pane("w1:p2", "yes")
    assert result["outcome"] == "blocked"
    assert result["receipt"]["outcome"] == "refused"


def test_verified_reply_unknown_pane_never_reads_or_sends():
    mux = ScriptedMux([_agent()], screens=["x"])
    result = _tools(mux).verified_reply_to_pane("w9:p9", "hi")
    assert result["outcome"] == "unknown_pane"
    assert not mux.prompt_calls


def test_verified_reply_steer_refused_for_unsupported_harnesses():
    mux = ScriptedMux([_agent(agent="shell")], screens=["$ "])
    result = _tools(mux).verified_reply_to_pane("w1:p2", "keep going", steer=True)
    assert result["outcome"] == "unsupported_steer"
    assert result["detail"]
    assert not mux.prompt_calls


def test_verified_reply_steer_sends_when_supported():
    mux = ScriptedMux(
        [_agent(agent="opencode", status="working")],
        screens=["working…", "user: keep going\nworking…"],
        prompt={"ok": False, "status": "stalled", "output": ""},
    )
    result = _tools(mux).verified_reply_to_pane("w1:p2", "keep going", steer=True)
    assert result["outcome"] == "delivered"
    assert result["receipt"]["outcome"] == "sent"


def test_verified_reply_masks_the_receipt_detail():
    mux = ScriptedMux(
        [_agent()],
        screens=["❯ sk-ant-api03-abcdef123456"],
        prompt={"ok": False, "status": "blocked", "output": ""},
    )
    result = _tools(mux).verified_reply_to_pane("w1:p2", "sk-ant-api03-abcdef123456")
    assert result["receipt"]["outcome"] == "refused"
    assert "sk-ant-api03" not in result["receipt"]["detail"]


# ── submit / discard through tools ────────────────────────────────────────────


def test_submit_typed_text_sends_enter_and_requires_the_echo_gone():
    mux = ScriptedMux(
        [_agent()],
        screens=["user: sigue adelante\nworking"],
    )
    result = _tools(mux).submit_typed_text("w1:p2", "sigue adelante")
    assert result["outcome"] == "sent"
    assert mux.key_calls == [("w1:p2", ["enter"])]


def test_submit_keeps_typed_when_the_echo_survives():
    mux = ScriptedMux([_agent()], screens=["❯ 1) Yes\n│ > sigue adelante"])
    result = _tools(mux).submit_typed_text("w1:p2", "sigue adelante")
    assert result["outcome"] == "typed_not_submitted"


def test_discard_typed_text_sends_the_recipe_and_confirms():
    mux = ScriptedMux([_agent(agent="claude")], screens=["❯ 1) Yes"])
    result = _tools(mux).discard_typed_text("w1:p2", "claude", "sigue adelante")
    assert result["outcome"] == "discarded"
    assert mux.key_calls == [("w1:p2", ["esc"])]


def test_discard_keeps_typed_when_the_text_survives():
    mux = ScriptedMux(
        [_agent(agent="claude")],
        screens=["❯ 1) Yes\n│ > sigue adelante"],
    )
    result = _tools(mux).discard_typed_text("w1:p2", "claude", "sigue adelante")
    assert result["outcome"] == "typed_not_submitted"
    assert result["detail"]


# ── stop ──────────────────────────────────────────────────────────────────────


def test_stop_sends_the_interrupt_recipe_for_known_harnesses():
    mux = ScriptedMux([_agent(agent="claude", status="working")], screens=["work"])
    result = _tools(mux).stop_pane("w1:p2")
    assert result["outcome"] == "stopped"
    assert result["keys"] == ["esc"]
    assert mux.key_calls == [("w1:p2", ["esc"])]


def test_stop_refuses_unknown_harnesses_without_sending_keys():
    mux = ScriptedMux([_agent(agent="shell", status="working")], screens=["$ "])
    result = _tools(mux).stop_pane("w1:p2")
    assert result["outcome"] == "unsupported_harness"
    assert not mux.key_calls


def test_stop_unknown_pane_is_a_404_outcome():
    mux = ScriptedMux([_agent()], screens=["x"])
    result = _tools(mux).stop_pane("w9:p9")
    assert result["outcome"] == "unknown_pane"


def test_stop_maps_mux_key_failures_to_error():
    mux = ScriptedMux(
        [_agent(agent="claude", status="working")],
        screens=["work"],
        keys={"ok": False, "error": "mux refused"},
    )
    result = _tools(mux).stop_pane("w1:p2")
    assert result["outcome"] == "error"
    assert "mux refused" in result["detail"]


def test_steer_harnesses_and_interrupt_table_agree_on_the_chat_tuis():
    # Steering injects into a running turn; the interrupt must reach the
    # same harnesses the steer table names.
    assert STEER_HARNESSES <= set(INTERRUPT_RECIPES)
