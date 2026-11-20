"""Verified sends (S12): outcome classification, receipts, stop and steer.

The classifier is a PURE function over evidence — the screen read before
the send, the screens read after it and the mux's own claim — so every
row of the outcome table below is a stub mux screen sequence, no real
pane involved. The tools-level tests drive :class:`BrainTools` with a
scripted mux double through the same public methods the routes call.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from herdr_brain.config import Settings
from herdr_brain.herdr import AgentInfo, HerdrError
from herdr_brain.send_receipts import (
    DISCARD_RECIPES,
    INTERRUPT_RECIPES,
    OUTCOMES,
    STEER_HARNESSES,
    STOP_ENDED_STATUSES,
    STOP_OUTCOMES,
    STOP_RUNNING_STATUSES,
    SendReceiptStore,
    VERIFIED_STOP_WAIT_MS,
    classify_followup,
    classify_send,
    classify_stop,
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

    Stop additions (S2): ``statuses`` scripts the pane state reported per
    ``list_agents`` call (the last one repeats, like the screens);
    ``wait_ok=False`` makes ``agent_wait`` raise the HerdrError timeout
    herdr uses when the state is not reached; ``fail_list`` makes
    ``list_agents`` itself unreadable. Defaults keep the pre-S2 shape.
    """

    def __init__(self, agents, screens, prompt=None, keys=None, fail_read=False,
                 statuses=None, wait_ok=True, fail_list=False):
        self._agents = agents
        self._screens = list(screens)
        self._prompt = prompt or {"ok": True, "status": "done", "output": "ok"}
        self._keys = keys or {"ok": True, "error": ""}
        self._fail_read = fail_read
        self._statuses = list(statuses) if statuses is not None else None
        self._status_idx = 0
        self._wait_ok = wait_ok
        self._fail_list = fail_list
        self.read_calls: list[str] = []
        self.prompt_calls: list[dict] = []
        self.key_calls: list[str] = []
        self.wait_calls: list[tuple] = []

    def list_agents(self):
        if self._fail_list:
            raise HerdrError("list failed")
        if self._statuses and self._agents:
            head, *rest = self._agents
            self._agents = [replace(head, status=self._statuses[self._status_idx]), *rest]
            if self._status_idx < len(self._statuses) - 1:
                self._status_idx += 1
        return list(self._agents)

    def agent_wait(self, pane_id, until="idle", timeout_ms=None):
        self.wait_calls.append((pane_id, until, timeout_ms))
        if not self._wait_ok:
            raise HerdrError("the state was not reached in time")

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


# ── classify_stop: the stop truth table (S2) ─────────────────────────────────


def test_the_stop_outcome_set_is_closed_and_ordered_for_docs():
    assert STOP_OUTCOMES == (
        "stopped",
        "dialog_open",
        "idle",
        "unverified",
        "unknown",
        "error",
        "unknown_pane",
        "unsupported_harness",
    )


@pytest.mark.parametrize("after", ["idle", "done", "blocked"])
def test_a_turn_that_left_its_running_state_is_stopped(after):
    outcome, detail = classify_stop("working", after)
    assert outcome == "stopped"
    assert detail


def test_a_turn_that_keep_running_is_never_stopped():
    outcome, detail = classify_stop("working", "working")
    assert outcome == "unverified"
    assert "no se confirmó" in detail


def test_an_unreadable_post_state_is_unknown_not_stopped():
    outcome, detail = classify_stop("working", None)
    assert outcome == "unknown"
    assert detail


def test_an_unrecognized_post_state_is_unknown_not_stopped():
    outcome, _ = classify_stop("working", "something-else")
    assert outcome == "unknown"


def test_a_stopped_claim_never_grows_without_the_transition():
    # The classifier cannot invent the stopped verdict: before and after
    # both ended (no running turn was ever interrupted) is NOT a stop.
    outcome, _ = classify_stop("idle", "idle")
    assert outcome != "stopped"


def test_the_stop_state_tables_split_dialog_from_ended():
    # S2: a dialog IS an ended state for the classifier, but the status
    # gate refuses a dialog pane before any key is chosen — the tables
    # carry both sides of that distinction.
    assert "blocked" in STOP_ENDED_STATUSES
    assert "blocked" not in STOP_RUNNING_STATUSES
    assert STOP_RUNNING_STATUSES == frozenset({"working"})
    assert VERIFIED_STOP_WAIT_MS > 0


# ── per-harness tables ────────────────────────────────────────────────────────


def test_steer_is_supported_by_the_chat_tui_family():
    assert steer_supported("claude")
    assert steer_supported("opencode")
    assert steer_supported("codex")
    assert not steer_supported("shell")
    assert not steer_supported("")


def test_interrupt_recipes_are_esc_for_known_tuis_and_refusal_otherwise():
    assert INTERRUPT_RECIPES
    # claude/codex stay single-esc (live re-verified 2026-11-19);
    # opencode is the live-verified DOUBLE esc (one esc only arms on
    # 1.19.5 — its own footer says «esc again to interrupt»).
    for agent in ("claude", "codex"):
        assert interrupt_recipe(agent) == ("esc",)
    assert interrupt_recipe("opencode") == ("esc", "esc")
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


def test_submit_sends_enter_only_after_the_echo_is_verified():
    """S2 borrador: Enter rides a read that SAW the receipt's echo on
    the input line — the audit's «Enter antes de releer» defect, fixed.
    The echo leaving the line still decides «sent»."""
    mux = ScriptedMux(
        [_agent()],
        screens=[
            "❯ 1) Yes\n│ > sigue adelante",   # before: the draft is there
            "user: sigue adelante\nworking",  # after: the echo left
        ],
    )
    result = _tools(mux).submit_typed_text("w1:p2", "sigue adelante")
    assert result["outcome"] == "sent"
    assert result["detail"]
    assert mux.key_calls == [("w1:p2", ["enter"])]
    assert len(mux.read_calls) >= 2


def test_submit_refuses_enter_when_the_echo_is_not_on_the_line():
    """The screen no longer shows the receipt's draft — the user may
    have sent or cleared it already. Enter could act on a NEW draft, so
    nothing is keyed and the outcome says exactly why."""
    mux = ScriptedMux([_agent()], screens=["user: something else\nworking"])
    result = _tools(mux).submit_typed_text("w1:p2", "sigue adelante")
    assert result["outcome"] == "unknown"
    assert "borrador" in result["detail"]
    assert not mux.key_calls


def test_submit_refuses_enter_when_the_screen_is_unreadable():
    """Fail-closed before the key: without a readable line there is no
    bound evidence the receipt's draft is still there — and a blind
    Enter is exactly what the audit found. Nothing was sent."""
    mux = ScriptedMux([_agent()], screens=[], fail_read=True)
    result = _tools(mux).submit_typed_text("w1:p2", "sigue adelante")
    assert result["outcome"] == "error"
    assert not mux.key_calls
    assert "no se envió" in result["detail"]


def test_submit_keeps_typed_when_the_echo_survives():
    mux = ScriptedMux([_agent()], screens=["❯ 1) Yes\n│ > sigue adelante"])
    result = _tools(mux).submit_typed_text("w1:p2", "sigue adelante")
    assert result["outcome"] == "typed_not_submitted"


def test_discard_sends_the_recipe_only_on_a_verified_echo():
    mux = ScriptedMux(
        [_agent(agent="claude")],
        screens=[
            "❯ 1) Yes\n│ > sigue adelante",  # before
            "❯ 1) Yes",                      # after: clean
        ],
    )
    result = _tools(mux).discard_typed_text("w1:p2", "claude", "sigue adelante")
    assert result["outcome"] == "discarded"
    assert mux.key_calls == [("w1:p2", ["esc"])]


def test_discard_refuses_esc_when_the_echo_is_absent():
    """The draft is gone with no read proving who took it: Esc into
    whatever replaced it could interrupt a running turn — refused."""
    mux = ScriptedMux([_agent(agent="claude")], screens=["❯ 1) Yes"])
    result = _tools(mux).discard_typed_text("w1:p2", "claude", "sigue adelante")
    assert result["outcome"] == "unknown"
    assert not mux.key_calls


def test_discard_keeps_typed_when_the_text_survives():
    mux = ScriptedMux(
        [_agent(agent="claude")],
        screens=["❯ 1) Yes\n│ > sigue adelante"],
    )
    result = _tools(mux).discard_typed_text("w1:p2", "claude", "sigue adelante")
    assert result["outcome"] == "typed_not_submitted"
    assert result["detail"]


# ── stop ──────────────────────────────────────────────────────────────────────


def test_stop_sends_the_recipe_and_verifies_the_turn_ended():
    """S2: the sent keys alone are never the verdict — ``stopped`` rides
    the pane leaving its running state (herdr ``agent wait``), which the
    route waits for within VERIFIED_STOP_WAIT_MS."""
    mux = ScriptedMux(
        [_agent(agent="claude", status="working")],
        screens=["work"],
        statuses=("working", "idle"),
    )
    result = _tools(mux).stop_pane("w1:p2")
    assert result["outcome"] == "stopped"
    assert result["keys"] == ["esc"]
    assert result["detail"]
    assert mux.key_calls == [("w1:p2", ["esc"])]
    assert mux.wait_calls == [("w1:p2", "idle", VERIFIED_STOP_WAIT_MS)]


def test_stop_dialog_open_refuses_without_keys():
    """S2 diálogo abierto: a pane waiting at a dialog gets NO interrupt
    key — the recipes are evidence-backed for running turns, and keys
    into a dialog would move its own selection. The refusal says so."""
    mux = ScriptedMux([_agent(agent="claude", status="blocked")], screens=[])
    result = _tools(mux).stop_pane("w1:p2")
    assert result["outcome"] == "dialog_open"
    assert result["detail"]
    assert not mux.key_calls
    assert not mux.wait_calls
    assert not mux.read_calls


def test_stop_idle_agent_refuses_without_keys():
    """S2 agente inactivo: nothing is running, nothing may be claimed
    stopped, and no key may fly (Esc into an idle TUI clears whatever
    draft sits in the composer)."""
    mux = ScriptedMux([_agent(agent="claude", status="idle")], screens=[])
    result = _tools(mux).stop_pane("w1:p2")
    assert result["outcome"] == "idle"
    assert result["detail"]
    assert not mux.key_calls
    assert not mux.wait_calls


def test_stop_unreadable_state_refuses_without_keys():
    """S2 resultado no verificable, before the keys: when the mux cannot
    say what state the pane is in, the brain cannot reason about where
    its key would land — nothing is sent and nothing is claimed."""
    for status in ("unknown", ""):
        mux = ScriptedMux([_agent(agent="claude", status=status)], screens=[])
        result = _tools(mux).stop_pane("w1:p2")
        assert result["outcome"] == "unknown", status
        assert result["detail"]
        assert not mux.key_calls
        assert not mux.read_calls


def test_stop_surviving_turn_reports_unverified_never_stopped():
    """S2 resultado no verificable, after the keys: the wait expired and
    the pane is still on its turn — the outcome says the stop was NOT
    confirmed (the keys were sent; that is a fact, not a verdict)."""
    mux = ScriptedMux(
        [_agent(agent="claude", status="working")],
        screens=["work"],
        statuses=("working", "working"),
        wait_ok=False,
    )
    result = _tools(mux).stop_pane("w1:p2")
    assert result["outcome"] == "unverified"
    assert result["outcome"] != "stopped"
    assert "no se confirmó" in result["detail"]
    assert mux.key_calls == [("w1:p2", ["esc"])]


def test_stop_with_unreadable_post_state_stays_fail_closed():
    """The wait timed out AND the fallback state read failed: the turn
    may or may not have ended — ``unknown``, never a claim."""
    mux = ScriptedMux(
        [_agent(agent="claude", status="working")],
        screens=["work"],
        statuses=("working", "unknown"),
        wait_ok=False,
    )
    result = _tools(mux).stop_pane("w1:p2")
    assert result["outcome"] == "unknown"
    assert "no se confirmó la parada" not in result["detail"]


def test_stop_reports_whether_the_draft_survived():
    """S2 borrador: with the route's receipt hint the stop names the
    draft's fate from screen evidence — gone or still on the input
    line — while the verdict itself stays the state evidence."""
    hint = "sigue adelante"
    gone = ScriptedMux(
        [_agent(agent="claude", status="working")],
        screens=[
            "❯ 1) Yes\n│ > sigue adelante",   # before: the draft is there
            "❯ 1) Yes",                       # after: the echo left
        ],
        statuses=("working", "idle"),
    )
    result = _tools(gone).stop_pane("w1:p2", hint)
    assert result["outcome"] == "stopped"
    assert "borrador ya no está" in result["detail"]

    kept = ScriptedMux(
        [_agent(agent="claude", status="working")],
        screens=[
            "❯ 1) Yes\n│ > sigue adelante",
            "❯ 1) Yes\n│ > sigue adelante",
        ],
        statuses=("working", "idle"),
    )
    kept_result = _tools(kept).stop_pane("w1:p2", hint)
    assert kept_result["outcome"] == "stopped"
    assert "borrador queda en la línea" in kept_result["detail"]


def test_stop_draft_evidence_never_invents_a_verdict():
    """With a hint but no readable screen, the stop still classifies from
    state evidence and simply says nothing about the draft."""
    mux = ScriptedMux(
        [_agent(agent="claude", status="working")],
        screens=[],
        fail_read=True,
        statuses=("working", "idle"),
    )
    result = _tools(mux).stop_pane("w1:p2", "sigue adelante")
    assert result["outcome"] == "stopped"
    assert "borrador" not in result["detail"]


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


# ── parity-f7e S1: live-verified recipes per harness ─────────────────────────
#
# Every recipe below was probed live on 2026-11-19 in throwaway tmux
# sessions (`tmux new-session -d -s f7e-<name> -x 160 -y 50 -c <mktemp
# dir>`), driving a harmless long turn ("run the shell command echo
# $RANDOM, wait one second, and repeat until you have run it N times"
# or a long pure-generation prompt), sending the candidate key with
# `tmux send-keys` and proving the turn stopped from `capture-pane`
# before/after excerpts. The operator's panes were never touched.


def test_claude_and_codex_keep_their_single_escape_recipes():
    """PRESERVE: the two original single-esc rows are unchanged.

    Live re-verified 2026-11-19 on claude 2.1.328 («⎿  Interrupted ·
    What should Claude do instead?») and codex-cli 0.170.0 («■
    Conversation interrupted»), each frozen several seconds after one
    Escape with the composer back.
    """
    assert INTERRUPT_RECIPES["claude"] == ("esc",)
    assert INTERRUPT_RECIPES["codex"] == ("esc",)


def test_opencode_takes_the_live_verified_double_escape():
    """opencode 1.19.5: ONE esc only ARMS the interrupt.

    Probe evidence (2026-11-19, opencode 1.19.5): with a 40-iteration
    echo loop running, one Escape left the loop streaming (runs 5-9
    kept printing) while the progress footer switched to «esc again to
    interrupt»; the SECOND Escape froze it at run 10 (stable 5 s later,
    run list collapsed). Matches the reference product's verified
    recipe (ref:web/src/lib/surface/profiles/opencode.json:78,
    "interrupt": ["Escape", "Escape"]).
    """
    assert INTERRUPT_RECIPES["opencode"] == ("esc", "esc")


def test_pi_joins_with_the_live_verified_escape():
    """pi 1.5.2: one Escape aborts the running turn.

    Probe evidence (2026-11-19): counting turn interrupted mid-stream
    («Operation aborted», counter frozen, «✿ waiting for input» back,
    stable 7 s later).
    """
    assert INTERRUPT_RECIPES["pi"] == ("esc",)


def test_antigravity_joins_under_both_of_its_agent_names():
    """Antigravity CLI 1.5.12: one Escape interrupts the running turn.

    Probe evidence (2026-11-19): generation frozen mid-word («⎿
    Interrupted · What should Antigravity CLI do instead?», composer
    back, stable 6 s later). herdr reports the pane as agent "agy"
    (source herdr:antigravity_cli) while the reference product's
    contract also accepts "antigravity"
    (ref:web/src/lib/surface/profiles/agy.json:67), so both names get
    the recipe — the brain keys on whatever the mux says.
    """
    assert INTERRUPT_RECIPES["agy"] == ("esc",)
    assert INTERRUPT_RECIPES["antigravity"] == ("esc",)


def test_reference_verified_escape_harnesses_join_the_table():
    """Not installed on this host — recipe provenance is the reference
    product's verified profiles (esc-only rows; same key the live
    probes above verified for the installed family):

    - kiro:  ref:web/src/lib/surface/profiles/kiro.json:72
    - muse:  ref:web/src/lib/surface/profiles/muse.json:81
    - omp:   ref:web/src/lib/surface/profiles/omp.json:263
    """
    assert INTERRUPT_RECIPES["kiro"] == ("esc",)
    assert INTERRUPT_RECIPES["muse"] == ("esc",)
    assert INTERRUPT_RECIPES["omp"] == ("esc",)


def test_ctrl_c_harnesses_stay_refused():
    """cursor/grok/hermes interrupt with ctrl+c in the reference
    (ref:profiles/cursor.json, grok.json, hermes.json), but ctrl+c is
    outside the brain's closed SEND_KEY_ALLOWLIST (herdr.py) and that
    file is deliberately not widened in this slice — so they keep the
    honest 422 instead of a recipe that could never be sent."""
    for agent in ("cursor", "grok", "hermes"):
        assert interrupt_recipe(agent) is None


def test_gemini_stays_unverified_and_refused():
    """gemini CLI 0.70.0 is installed but stays out: the live probe hit
    its OAuth wall (no cached auth), and herdr does not know gemini as
    an agent at all (not in the reference contract's MUX_AGENT_NAMES —
    a gemini pane reads as shell), so no keyed recipe could reach it."""
    assert interrupt_recipe("gemini") is None


def test_the_steer_table_gains_pi_and_the_reference_verified_rows():
    """pi steer is LIVE-verified (2026-11-19, pi 1.5.2): text + Enter on
    a working turn was accepted as a queued steering message («▸
    Steering: Stop the loop now…» / «❁ queued») and answered the moment
    the turn ended — the same mid-turn acceptance claude has. kiro,
    muse and omp ride the reference product's verified profiles
    (enterWhileRunning "steers": kiro.json:85, muse.json:95,
    omp.json:280 with steer keys at omp.json:299). agy's steer is NOT
    added: its mid-turn message was accepted but with unclear
    semantics, and the reference ships no measured enterWhileRunning
    for it either."""
    for agent in ("claude", "opencode", "codex", "pi", "kiro", "muse", "omp"):
        assert steer_supported(agent)
    for agent in ("shell", "", "gemini", "agy", "antigravity"):
        assert not steer_supported(agent)


def test_voice_stop_dispatch_uses_the_same_interrupt_table():
    """S1(3): the voice path (stop_agent) rides stop_pane — the SAME
    table. Dispatching the tool for a covered harness sends the recipe;
    for an uncovered one it refuses and touches no keys."""
    pi_mux = ScriptedMux([_agent(agent="pi", status="working")], screens=["work"])
    result = _tools(pi_mux).dispatch("stop_agent", {"pane_id": "w1:p2"})
    assert result.startswith("stop stopped")
    assert pi_mux.key_calls == [("w1:p2", ["esc"])]

    grok_mux = ScriptedMux([_agent(agent="grok", status="working")], screens=["$ "])
    result = _tools(grok_mux).dispatch("stop_agent", {"pane_id": "w1:p2"})
    assert "unsupported_harness" in result
    assert not grok_mux.key_calls


# ── parity-f7g: trackers on the HTTP surface (S1/S4) ──────────────────────────


def _http_receipt(outcome, detail="", text_hint=""):
    return {"outcome": outcome, "detail": detail, "text_hint": text_hint}


class TrackerFakeTools:
    """The same FakeTools shape tests/test_server_reply.py uses: the
    route only needs verified_reply_to_pane's envelope."""

    outcome: dict = {
        "outcome": "delivered", "pane_id": "w1:p2", "status": "stalled",
        "detail": "", "receipt": _http_receipt("sent"),
    }

    def __init__(self, cfg):
        type(self).outcome = dict(type(self).outcome, pane_id="w1:p2")

    def verified_reply_to_pane(self, pane_id, text, steer=False):
        return dict(type(self).outcome, pane_id=pane_id)


@pytest.fixture
def tracker_client(settings, tmp_path, monkeypatch):
    import herdr_brain.server as server_module
    from fastapi.testclient import TestClient

    TrackerFakeTools.outcome = {
        "outcome": "delivered", "pane_id": "w1:p2", "status": "stalled",
        "detail": "", "receipt": _http_receipt("sent"),
    }
    monkeypatch.setattr(server_module, "BrainTools", TrackerFakeTools)
    cfg = Settings(**{
        **settings.__dict__,
        "audio_dir": str(tmp_path / "audio"),
        "state_dir": str(tmp_path / "state"),
    })
    app = server_module.create_app(settings=cfg)
    client = TestClient(app)
    client.app = app
    return client


def _post_sent(client, text="sigue"):
    resp = client.post("/pane/reply", json={"pane_id": "w1:p2", "text": text})
    assert resp.status_code == 200
    return resp.json()


class TestTrackerOnHTTP:
    def test_a_sent_reply_exposes_its_tracker_on_the_receipts(self, tracker_client):
        body = _post_sent(tracker_client)
        assert body["receipt"]["outcome"] == "sent"
        # The reply response itself stays byte-shaped as before (no
        # track key on it — S4 puts tracking on GET /pane/receipts).
        assert "track" not in body["receipt"]

        listed = tracker_client.get("/pane/receipts", params={"pane_id": "w1:p2"})
        receipt = listed.json()["receipts"][0]
        assert receipt["track"]["state"] == "sent"
        assert receipt["track"]["elapsed_s"] >= 0.0
        assert receipt["track"]["stalled"] is False

    def test_the_initial_state_rides_the_sse_hub(self, tracker_client):
        hub = tracker_client.app.state.watcher.hub
        sub_id, queue = hub.subscribe()
        try:
            _post_sent(tracker_client)
            event = queue.get(timeout=2)
        finally:
            hub.unsubscribe(sub_id)
        assert event["type"] == "send.track"
        assert event["pane_id"] == "w1:p2"
        assert event["state"] == "sent"
        assert event["receipt_id"].startswith("rct-")

    def test_effective_replies_start_done(self, tracker_client):
        TrackerFakeTools.outcome = {
            "outcome": "delivered", "pane_id": "w1:p2", "status": "done",
            "detail": "", "receipt": _http_receipt("effective"),
        }
        _post_sent(tracker_client)
        receipt = tracker_client.get("/pane/receipts").json()["receipts"][0]
        assert receipt["outcome"] == "effective"
        assert receipt["track"]["state"] == "done"

    def test_untracked_receipts_keep_their_exact_shape(self, tracker_client):
        """PRESERVE (S4 additive contract): a receipt with no tracker —
        here a typed draft — serializes without any track key."""
        TrackerFakeTools.outcome = {
            "outcome": "delivered", "pane_id": "w1:p2", "status": "timeout",
            "detail": "",
            "receipt": _http_receipt("typed_not_submitted", text_hint="x"),
        }
        _post_sent(tracker_client)
        receipt = tracker_client.get("/pane/receipts").json()["receipts"][0]
        assert set(receipt) == {
            "id", "pane_id", "outcome", "detail", "text_hint",
            "created_at", "resolved_at",
        }

    def test_a_newer_send_supersedes_the_older_tracker(self, tracker_client):
        _post_sent(tracker_client, text="one")
        _post_sent(tracker_client, text="two")
        receipts = tracker_client.get("/pane/receipts").json()["receipts"]
        assert receipts[0]["track"]["state"] == "sent"      # newest
        assert receipts[1]["track"]["state"] == "superseded"  # older send

    def test_the_tracker_survives_polls_until_its_outcome(self, tracker_client):
        _post_sent(tracker_client)
        for _ in range(3):
            listed = tracker_client.get("/pane/receipts", params={"pane_id": "w1:p2"})
            assert listed.json()["receipts"][0]["track"]["state"] == "sent"

    def test_legacy_doubles_without_verified_reply_stay_tracker_free(
        self, settings, tmp_path, monkeypatch
    ):
        """PRESERVE (the hasattr seam): a pre-S12 tools double answers
        with the legacy body — no receipt, no tracker, byte for byte."""
        import herdr_brain.server as server_module
        from fastapi.testclient import TestClient

        class LegacyTools:
            def __init__(self, cfg):
                pass

            def reply_to_pane(self, pane_id, text):
                return {"outcome": "delivered", "pane_id": pane_id, "detail": ""}

        monkeypatch.setattr(server_module, "BrainTools", LegacyTools)
        cfg = Settings(**{
            **settings.__dict__,
            "audio_dir": str(tmp_path / "audio"),
            "state_dir": str(tmp_path / "state"),
        })
        client = TestClient(server_module.create_app(settings=cfg))
        resp = client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "go"})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "pane_id": "w1:p2"}
        assert client.get("/pane/receipts").json()["receipts"] == []
