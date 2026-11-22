"""The menu-hints grammar: footer-derived actions for the generic menu.

The generic menu is the LAST-RESORT dialog: claimed only when the tail
footer names its own keys as ``·``-separated ``<key> to <verb>`` hints,
and every emitted key comes from that footer. The ban on synthesised
digits (ADR 0009) lives in ``menu_key_for``: confirming the /model
picker with a digit would REWRITE the user's default, so no menu
grammar may ever emit one.

Every mapping is normalised against the DIALOG recipe dialect: a footer
that names a key the guarded answer path cannot send (``shift+tab``,
``ctrl+r``) yields ``None`` — the segment is skipped, not the whole
footer failed. A single segment is never a claim: ``parse_single_hint``
exists for the caller that has other evidence the screen is a modal
(the ``▔`` edge).
"""

from __future__ import annotations

import pytest

from herdr_brain.dialogs.menu_hints import (
    capitalize_menu_label,
    menu_key_for,
    parse_key_hint_footer,
    parse_single_hint,
    read_key_hint_footer,
)


class TestMenuKeyFor:
    def test_enter_esc_tab_map(self):
        assert menu_key_for("Enter") == "enter"
        assert menu_key_for("esc") == "esc"
        assert menu_key_for("Escape") == "esc"
        assert menu_key_for("Tab") == "tab"

    def test_arrows_map(self):
        assert menu_key_for("↑") == "up"
        assert menu_key_for("↓") == "down"
        assert menu_key_for("←") == "left"
        assert menu_key_for("→") == "right"

    def test_single_lowercase_letter_maps_verbatim(self):
        assert menu_key_for("s") == "s"
        assert menu_key_for("r") == "r"

    def test_uppercase_letter_is_prose(self):
        # Matched case-sensitively: Claude prints the case it means, and
        # an uppercase token is prose ("A to Z"), not a key hint.
        assert menu_key_for("S") is None
        assert menu_key_for("AB") is None

    def test_dialect_closed_keys_are_none(self):
        # Not in the verified herdr dialect the guarded path can send
        # (SEND_KEY_ALLOWLIST or a single lowercase letter): the footer
        # names them, but no button may carry them.
        assert menu_key_for("shift+tab") is None
        assert menu_key_for("ctrl+r") is None
        assert menu_key_for("PageUp") is None
        assert menu_key_for("something else") is None
        assert menu_key_for("") is None


class TestDigitsNeverMap:
    @pytest.mark.parametrize("token", ["0", "1", "2", "5", "9", "10", "42"])
    def test_menu_key_for_never_a_digit(self, token):
        # ADR 0009: a digit confirms AND rewrites the default in the
        # /model picker — no menu grammar may ever emit one. The ban
        # lives HERE, not at the send layer, so no detector can route
        # around it.
        assert menu_key_for(token) is None

    def test_digit_hint_segment_is_skipped(self):
        # "1 to pick" parses as a hint shape but its key is a digit: the
        # segment is skipped, the sendable remainder stays honest.
        actions = parse_key_hint_footer("1 to pick · Esc to cancel")
        assert [action.keys for action in actions] == [("esc",)]
        assert actions[0].cancel is True


class TestParseKeyHintFooter:
    def test_model_picker_footer(self):
        actions = parse_key_hint_footer(
            "Enter to set as default · s to use this session only · Esc to cancel"
        )
        assert [(a.label, a.keys) for a in actions] == [
            ("Set as default", ("enter",)),
            ("Use this session only", ("s",)),
            ("Cancel", ("esc",)),
        ]
        assert [a.cancel for a in actions] == [False, False, True]

    def test_fewer_than_two_segments_is_no_footer(self):
        assert parse_key_hint_footer("Enter to select") == []
        assert parse_key_hint_footer("no hints here") == []

    def test_unsendable_segment_skipped_not_fatal(self):
        actions = parse_key_hint_footer("shift+tab to focus · Esc to close")
        assert [(a.label, a.keys) for a in actions] == [("Close", ("esc",))]

    def test_non_hint_segments_skipped(self):
        actions = parse_key_hint_footer("Haiku 4.5 · Fastest · Esc to exit")
        # Only the hint segment becomes an action; value segments are prose.
        assert [(a.label, a.keys) for a in actions] == [("Exit", ("esc",))]


class TestParseSingleHint:
    def test_esc_only(self):
        assert parse_single_hint("Esc to cancel") == ("esc", "cancel")

    def test_multi_segment_refused(self):
        assert parse_single_hint("Enter to view · Esc to close") is None

    def test_unsendable_or_non_hint_refused(self):
        assert parse_single_hint("shift+tab to focus") is None
        assert parse_single_hint("not a hint") is None


class TestCapitalizeMenuLabel:
    def test_sentence_capitalise(self):
        assert capitalize_menu_label("set as default") == "Set as default"
        assert capitalize_menu_label("Cancel") == "Cancel"
        assert capitalize_menu_label("") == ""


class TestReadKeyHintFooter:
    def test_single_row(self):
        found = read_key_hint_footer(
            ["padding", "", "Enter to select · Esc to cancel"]
        )
        assert found is not None
        assert found.text == "Enter to select · Esc to cancel"
        assert [a.keys for a in found.actions] == [("enter",), ("esc",)]
        assert found.start_line == 2
        assert found.end_line == 2

    def test_joins_wrapped_rows_at_shared_indent(self):
        found = read_key_hint_footer(
            [
                "  Enter to install the selected version · Esc to",
                "  cancel without installing anything",
            ]
        )
        assert found is not None
        assert found.text == (
            "Enter to install the selected version · Esc to cancel without installing anything"
        )
        assert [(a.label, a.keys) for a in found.actions] == [
            ("Install the selected version", ("enter",)),
            ("Cancel without installing anything", ("esc",)),
        ]

    def test_blank_row_ends_the_group(self):
        found = read_key_hint_footer(
            ["Esc to cancel", "", "Enter to select · Esc to cancel"]
        )
        # k=2 would start on the blank row between them: no larger group.
        assert found is not None
        assert found.text == "Enter to select · Esc to cancel"

    def test_indent_mismatch_refuses_the_row(self):
        # A continuation at a DIFFERENT indent than the block's first row
        # is not part of it (the terminal-wrap exception is indent 0
        # only) — and a one-segment footer is no claim, so the whole
        # screen stays raw.
        assert (
            read_key_hint_footer(
                [
                    "  Enter to select · Esc",
                    "        to cancel",
                ]
            )
            is None
        )

    def test_terminal_wrap_at_zero_accepted(self):
        # The previous row could not have held the next word: the
        # terminal broke the line and the continuation starts at column 0
        # (claude-lab--menu-effort-slider at 40 columns, reference
        # capture: footer's first row ends at the pane edge).
        texts = [
            "  s to use this session only · Esc to",
            "cancel",
        ]
        found = read_key_hint_footer(texts)
        assert found is not None
        assert found.text == "s to use this session only · Esc to cancel"
        assert [(a.label, a.keys) for a in found.actions] == [
            ("Use this session only", ("s",)),
            ("Cancel", ("esc",)),
        ]

    def test_soft_wrap_needs_no_room_on_prev(self):
        # A row at indent 0 whose predecessor HAD room for its first word
        # (a full-width rule elsewhere on the screen sets the width) is
        # ordinary output, not a wrapped footer: the whole group dies and
        # a one-segment remainder is no claim either.
        texts = [
            "─" * 40,
            "  Enter to select · Esc",
            "to cancel",
        ]
        assert read_key_hint_footer(texts) is None

    def test_interleaved_option_row_kills_the_footer(self):
        # An option row as its OWN segment (a `·` on both sides, as a
        # wrapped footer would paint it) makes every group that includes
        # it carry non-hint text: the group is refused whole, never
        # parsed in part.
        texts = [
            "Enter to set as default ·",
            "3. A numbered option row ·",
            "Esc to cancel",
        ]
        assert read_key_hint_footer(texts) is None

    def test_output_above_footer_limits_the_group(self):
        # Output above the footer (here at the transcript's flush-left
        # indent, the footer drawn indented) refuses the larger group by
        # indent — but the footer rows alone are still an honest claim
        # with their every named key.
        texts = [
            "⏺ Some agent output line",
            "  Enter to set as default · Esc to cancel",
        ]
        found = read_key_hint_footer(texts)
        assert found is not None
        assert [a.keys for a in found.actions] == [("enter",), ("esc",)]
        assert found.start_line == 1

    def test_largest_group_wins(self):
        # At 40 columns the effort footer's trailing rows parse on their
        # own; the whole group must win, or the top hints are silently
        # lost ("Esc to" alone yields no Enter action).
        texts = [
            "↑/↓ to select · Enter to view ·",
            "s for this session only · Esc to",
            "cancel",
        ]
        found = read_key_hint_footer(texts)
        assert found is not None
        assert [(a.label, a.keys) for a in found.actions] == [
            ("View", ("enter",)),
            ("Cancel", ("esc",)),
        ]

    def test_empty_returns_none(self):
        assert read_key_hint_footer([]) is None
        assert read_key_hint_footer(["", "   "]) is None
