"""Unit tests for the pending-action detector and view truncation."""

from __future__ import annotations

from herdr_brain.view import (
    MAX_EXCERPT_CHARS,
    PendingAction,
    detect_pending,
    truncate_text,
)


class TestDetectPending:
    def test_quiet_screen_and_working_status(self):
        result = detect_pending("building project...\ndone in 3s", "working")
        assert result == PendingAction(detected=False, kind=None, excerpt=None)

    def test_blocked_status_alone_detects(self):
        result = detect_pending("Press any key", "blocked")
        assert result.detected is True
        assert result.kind is None
        assert result.excerpt == "Press any key"

    def test_permission_line_wins(self):
        screen = "Running tests...\nDo you want to allow access? (y/n)"
        result = detect_pending(screen, "working")
        assert result.detected is True
        assert result.kind == "permission"
        assert result.excerpt.endswith("(y/n)")

    def test_error_line_detected(self):
        result = detect_pending("Traceback (most recent call last):\nTypeError: boom", "working")
        assert result.detected is True
        assert result.kind == "error"
        assert "TypeError" in result.excerpt

    def test_question_line_detected(self):
        result = detect_pending("Should I continue? ", "working")
        assert result.detected is True
        assert result.kind == "question"

    def test_bottom_up_first_match_wins(self):
        screen = "error in early log\nall good\nProceed with install? (y/n)"
        result = detect_pending(screen, "idle")
        assert result.kind == "permission"
        assert "install?" in result.excerpt

    def test_error_beats_question_but_not_permission(self):
        assert detect_pending("failed. retry?", "idle").kind == "error"
        assert detect_pending("allow failed import?", "idle").kind == "permission"

    def test_press_enter_is_a_question(self):
        result = detect_pending("Press enter to continue", "idle")
        assert result.detected is True
        assert result.kind == "question"

    def test_empty_screen_not_blocked(self):
        result = detect_pending("", "working")
        assert result == PendingAction(detected=False, kind=None, excerpt=None)

    def test_empty_screen_blocked_falls_back_without_excerpt(self):
        result = detect_pending(None, "blocked")
        assert result.detected is True
        assert result.kind is None
        assert result.excerpt is None

    def test_excerpt_is_capped(self):
        long_line = "allow " + "x" * 500 + "?"
        result = detect_pending(long_line, "idle")
        assert result.detected is True
        assert len(result.excerpt) <= MAX_EXCERPT_CHARS

    def test_case_insensitive_markers(self):
        assert detect_pending("ALLOW LOGIN?", "idle").kind == "permission"
        assert detect_pending("Command FAILED", "idle").kind == "error"


class TestTruncate:
    def test_short_untouched(self):
        assert truncate_text("hola", 300) == "hola"

    def test_long_cut_with_marker(self):
        out = truncate_text("x" * 400, 300)
        assert len(out) == 300
        assert out.endswith("...")

    def test_none_safe(self):
        assert truncate_text(None, 300) == ""
