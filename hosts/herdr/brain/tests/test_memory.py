"""Unit tests for the conversation memory ring buffers."""

from __future__ import annotations

import threading

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


class TestClip:
    def test_short_text_untouched(self):
        assert clip_content("hello") == "hello"

    def test_long_text_clipped_with_marker(self):
        out = clip_content("x" * 5000)
        assert len(out) <= 4000
        assert out.endswith("...")

    def test_none_becomes_empty(self):
        assert clip_content(None) == ""
