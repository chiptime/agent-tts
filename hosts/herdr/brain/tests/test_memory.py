"""Unit tests for the conversation memory ring buffers."""

from __future__ import annotations

import threading

import pytest

from herdr_brain.memory import (
    DEFAULT_SESSION,
    MAX_MESSAGES,
    ConversationStore,
    clip_content,
)


class TestConversationStore:
    def test_append_and_history_order(self):
        store = ConversationStore()
        store.append("s1", "user", "hi")
        store.append("s1", "assistant", "hello")
        history = store.history("s1")
        assert [(m.role, m.content) for m in history] == [("user", "hi"), ("assistant", "hello")]

    def test_sessions_are_isolated(self):
        store = ConversationStore()
        store.append("s1", "user", "one")
        store.append("s2", "user", "two")
        assert [m.content for m in store.history("s1")] == ["one"]
        assert [m.content for m in store.history("s2")] == ["two"]

    def test_ring_cap_evicts_oldest(self):
        store = ConversationStore()
        for i in range(MAX_MESSAGES + 4):
            store.append("s1", "user", f"msg-{i}")
        history = store.history("s1")
        assert len(history) == MAX_MESSAGES
        assert history[0].content == "msg-4"
        assert history[-1].content == f"msg-{MAX_MESSAGES + 3}"

    def test_reset_clears_only_target_session(self):
        store = ConversationStore()
        store.append("s1", "user", "keep me out")
        store.append("s2", "user", "keep me in")
        store.reset("s1")
        assert store.history("s1") == []
        assert [m.content for m in store.history("s2")] == ["keep me in"]

    def test_empty_session_id_uses_default(self):
        store = ConversationStore()
        store.append(None, "user", "anon")
        store.append("", "user", "also anon")
        assert [m.content for m in store.history(None)] == ["anon", "also anon"]
        assert store.normalize(None) == DEFAULT_SESSION

    def test_history_returns_copy(self):
        store = ConversationStore()
        store.append("s1", "user", "hi")
        snapshot = store.history("s1")
        store.append("s1", "user", "mutator")
        assert len(snapshot) == 1

    def test_concurrent_appends_stay_bounded(self):
        store = ConversationStore()

        def spam(n):
            for i in range(n):
                store.append("s1", "user", f"t{i}")

        threads = [threading.Thread(target=spam, args=(50,)) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(store.history("s1")) == MAX_MESSAGES


class TestStagedTurns:
    """Two-phase ring commits (S7/L34): a turn that can be rejected
    stages its messages — identities reserved, shared ring untouched —
    and lands them in ONE lock-held commit. A rejected turn therefore
    evicts nothing, not even at full capacity."""

    def test_staged_messages_touch_nothing_until_commit(self):
        store = ConversationStore()
        staged = store.stage_turn("s1")
        staged.append("user", "q")
        staged.append("assistant", "a")
        assert store.history("s1") == []  # nothing landed yet
        committed = staged.commit()
        assert [m.content for m in store.history("s1")] == ["q", "a"]
        assert [m.seq for m in store.history("s1")] == [m.seq for m in committed]

    def test_abort_at_full_capacity_evicts_nothing(self):
        # THE L34 CONTRACT: a rejected turn must leave the full ring
        # exactly as it found it.
        store = ConversationStore()
        for i in range(MAX_MESSAGES):
            store.append("s1", "user", f"m{i}")
        staged = store.stage_turn("s1")
        staged.append("user", "q")
        staged.append("assistant", "a")
        staged.abort()
        ring = store.history("s1")
        assert len(ring) == MAX_MESSAGES
        assert ring[0].content == "m0"  # nothing was evicted
        assert ring[-1].content == f"m{MAX_MESSAGES - 1}"

    def test_commit_at_full_capacity_keeps_normal_bounded_eviction(self):
        store = ConversationStore()
        for i in range(MAX_MESSAGES):
            store.append("s1", "user", f"m{i}")
        staged = store.stage_turn("s1")
        staged.append("user", "q")
        staged.append("assistant", "a")
        staged.commit()
        ring = store.history("s1")
        assert len(ring) == MAX_MESSAGES  # bounded, never grown
        assert [m.content for m in ring] == [
            *[f"m{i}" for i in range(2, MAX_MESSAGES)],
            "q",
            "a",
        ]

    def test_staging_is_session_scoped(self):
        store = ConversationStore()
        store.append("s2", "user", "keeper")
        staged = store.stage_turn("s1")
        staged.append("user", "q")
        staged.abort()
        assert store.history("s1") == []
        assert [m.content for m in store.history("s2")] == ["keeper"]
        staged2 = store.stage_turn("s1")
        staged2.append("assistant", "a")
        staged2.commit()
        assert [m.content for m in store.history("s2")] == ["keeper"]

    def test_abort_after_reset_leaves_the_fresh_state(self):
        store = ConversationStore()
        staged = store.stage_turn("s1")
        staged.append("user", "q")
        store.reset("s1")  # a concurrent reset/reseed wins the race
        staged.abort()
        assert store.history("s1") == []

    def test_commit_after_reset_lands_on_the_fresh_ring(self):
        store = ConversationStore()
        store.append("s1", "user", "old")
        staged = store.stage_turn("s1")
        staged.append("user", "q")
        store.reset("s1")  # reset between stage and commit
        staged.commit()
        # The staging lands on whatever the session ring is AT COMMIT —
        # it never resurrects the pre-reset contents.
        assert [m.content for m in store.history("s1")] == ["q"]

    def test_equal_text_stagings_keep_separate_identities(self):
        store = ConversationStore()
        doomed = store.stage_turn("s1")
        doomed.append("user", "repetido")
        keeper = store.stage_turn("s1")
        keeper.append("user", "repetido")
        assert doomed.seqs != keeper.seqs  # identity, never text, owns the message
        doomed.abort()
        keeper.commit()
        assert [m.content for m in store.history("s1")] == ["repetido"]

    def test_reserved_identities_are_never_reused_after_abort(self):
        store = ConversationStore()
        staged = store.stage_turn("s1")
        reserved = staged.append("user", "q").seq
        staged.abort()
        fresh = store.append("s1", "user", "fresh")
        assert fresh.seq != reserved

    def test_settled_staging_refuses_further_use(self):
        store = ConversationStore()
        staged = store.stage_turn("s1")
        staged.append("user", "q")
        staged.commit()
        with pytest.raises(RuntimeError):
            staged.append("assistant", "late")
        with pytest.raises(RuntimeError):
            staged.commit()


class TestClip:
    def test_short_text_untouched(self):
        assert clip_content("hello") == "hello"

    def test_long_text_clipped_with_marker(self):
        out = clip_content("x" * 5000)
        assert len(out) <= 4000
        assert out.endswith("...")

    def test_none_becomes_empty(self):
        assert clip_content(None) == ""
