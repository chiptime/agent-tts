"""Transcript readers for the new harnesses: cursor, kiro, muse, opencode2.

Each connector reads its SYNTHESIZED fixture store under
``tests/fixtures/transcripts`` through the same env-overridable roots
production would set (doc 05's contract), pins the on-disk shape it
reads, and degrades to an empty page when the store is absent (the
screen mirror stays the only view).
"""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"

CURSOR_SESSION = "cur-1111-2222"
KIRO_SESSION = "sess_aaaa1111-2222-3333-4444-555555555555"
MUSE_SESSION = "eeee1111-2222-3333-4444-555555555555"
OPENCODE2_SESSION = "ses_v2fixture0001"


@pytest.fixture
def new_harness_env(monkeypatch):
    monkeypatch.setenv("CURSOR_ROOT", str(FIXTURES / "cursor"))
    monkeypatch.setenv("KIRO_ROOT", str(FIXTURES / "kiro" / "sessions"))
    monkeypatch.setenv("MUSE_ROOT", str(FIXTURES / "muse" / "sessions"))
    monkeypatch.setenv("OPENCODE2_DB", str(FIXTURES / "opencode2" / "opencode.db"))
    from herdr_brain.transcript_readers.registry import default_registry

    return default_registry()


def test_the_registry_serves_the_new_harnesses(new_harness_env):
    assert set(new_harness_env.harnesses()) >= {
        "cursor", "kiro", "muse", "opencode2",
    }


class TestCursor:
    def test_reads_user_and_assistant_turns(self, new_harness_env):
        reader = new_harness_env.resolve("cursor")
        turns = reader.read(CURSOR_SESSION, 10)

        assert [turn.role for turn in turns] == ["user", "assistant", "user", "assistant"]
        assert "Resume the failing test" in turns[0].text
        assert "462 tests pass" in turns[-1].text

    def test_paging_and_limits(self, new_harness_env):
        reader = new_harness_env.resolve("cursor")
        newest_two = reader.read(CURSOR_SESSION, 2)
        assert [turn.role for turn in newest_two] == ["user", "assistant"]
        assert "Fix it" in newest_two[0].text

        older = reader.read(CURSOR_SESSION, 2, skip_newest=2)
        assert older[0].text != newest_two[0].text
        assert "Resume the failing test" in older[0].text

    def test_an_absent_root_degrades_to_no_turns(self, tmp_path, monkeypatch):
        from herdr_brain.transcript_readers.cursor import CursorTranscript

        monkeypatch.setenv("CURSOR_ROOT", str(tmp_path / "nowhere"))
        assert CursorTranscript().read(CURSOR_SESSION, 10) == []


class TestKiro:
    def test_reads_user_and_say_rows_only(self, new_harness_env):
        """Tool rows, lifecycle rows and the synthetic steering hand-off
        are not conversation turns."""
        reader = new_harness_env.resolve("kiro")
        turns = reader.read(KIRO_SESSION, 10)

        assert [turn.role for turn in turns] == [
            "user", "assistant", "assistant", "assistant",
        ]
        assert "approval panel fixture" in turns[0].text
        assert "pointer walk" in turns[1].text
        assert "Anything else" in turns[2].text
        assert "Session compacted" in turns[3].text
        assert "handed off mid turn" not in "\n".join(t.text for t in turns)

    def test_a_session_without_a_log_is_empty(self, new_harness_env):
        reader = new_harness_env.resolve("kiro")
        assert reader.read("sess_00000000-0000-0000-0000-000000000000", 5) == []


class TestMuse:
    def test_reads_accepted_intents_and_committed_replies(self, new_harness_env):
        reader = new_harness_env.resolve("muse")
        turns = reader.read(MUSE_SESSION, 10)

        assert [turn.role for turn in turns] == ["user", "assistant", "user", "assistant"]
        assert "Which approval rows" in turns[0].text
        assert "persistent row" in turns[1].text

    def test_title_is_not_read_from_elsewhere(self, new_harness_env):
        reader = new_harness_env.resolve("muse")
        assert reader.read_title(MUSE_SESSION) is None


class TestOpencode2:
    def test_reads_session_v2_rows_with_inline_content(self, new_harness_env):
        """The role lives in ``type``; the text is ``data.text`` (user)
        or the ``text`` items of ``data.content`` (assistant). Session
        events like compaction are not turns."""
        reader = new_harness_env.resolve("opencode2")
        turns = reader.read(OPENCODE2_SESSION, 10)

        assert [turn.role for turn in turns] == ["user", "assistant", "user", "assistant"]
        assert turns[1].text == "Only the one-shot and reject rows."
        assert "Shipped." in turns[-1].text
        assert "Objective" not in "\n".join(t.text for t in turns)

    def test_title_comes_from_the_session_row(self, new_harness_env):
        reader = new_harness_env.resolve("opencode2")
        assert reader.read_title(OPENCODE2_SESSION) == "Dialog buttons"

    def test_a_missing_database_degrades_to_no_turns(self, tmp_path, monkeypatch):
        from herdr_brain.transcript_readers.opencode2 import Opencode2Transcript

        monkeypatch.setenv("OPENCODE2_DB", str(tmp_path / "none.db"))
        assert Opencode2Transcript().read(OPENCODE2_SESSION, 10) == []


class TestShapeFallback:
    def test_a_ses_id_still_routes_to_opencode_first(self, new_harness_env):
        """The existing shape sniff must keep answering opencode for
        ``ses_*`` ids (S9: everything existing keeps working)."""
        from herdr_brain.transcripts import connector_for

        connector = connector_for(None, OPENCODE2_SESSION, opencode=None, claude=None, antigravity=None)
        assert connector is not None
        assert connector.name == "opencode"
