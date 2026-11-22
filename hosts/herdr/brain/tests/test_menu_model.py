"""The ``menu`` kind in the neutral structured dialog model.

The generic menu rides the SAME payload shape every other dialog
uses, with two additive facts: a per-option ``cancel`` flag (the Esc
hint is the way out, not a peer action) and the screen's ``←/→ to
adjust`` affordance carried model-only (no buttons in this unit). The
race guard needs nothing new: the footer's actions are keys-recipe
options compared by the region fingerprint, exactly like every other
keys-recipe dialog.
"""

from __future__ import annotations

from herdr_brain.dialogs.model import DialogOption, StructuredDialog


class TestMenuKindRegistration:
    def test_menu_kind_is_registered(self):
        from herdr_brain.dialogs.model import KINDS

        assert "menu" in KINDS


class TestMenuPayload:
    def _menu(self, **extra) -> StructuredDialog:
        fields = dict(
            agent="claude",
            kind="menu",
            question="Select model",
            options=(
                DialogOption(label="Set as default", keys=("enter",)),
                DialogOption(label="Use this session only", keys=("s",)),
                DialogOption(label="Cancel", keys=("esc",), cancel=True),
            ),
            fingerprint="f" * 16,
            hint="Enter to set as default · s to use this session only · Esc to cancel",
        )
        fields.update(extra)
        return StructuredDialog(**fields)

    def test_options_ride_their_footer_keys(self):
        payload = self._menu().payload()

        assert payload["recipe"] == "keys"
        assert [o["label"] for o in payload["options"]] == [
            "Set as default",
            "Use this session only",
            "Cancel",
        ]
        assert payload["kind"] == "menu"

    def test_cancel_flag_rides_only_on_esc_rows(self):
        payload = self._menu().payload()

        assert "cancel" not in payload["options"][0]
        assert "cancel" not in payload["options"][1]
        assert payload["options"][2]["cancel"] is True

    def test_adjust_rides_only_when_present_model_only_no_buttons(self):
        # Q2: detect leftRight/scale and carry it in the model, no
        # buttons in this unit — the PWA gets the facts, the options
        # stay the footer's actions.
        payload = self._menu(
            adjust_verb="adjust",
            adjust_label="◐ Medium effort",
            adjust_values=("Low", "Medium", "High"),
        ).payload()

        assert payload["adjust"] == {
            "verb": "adjust",
            "label": "◐ Medium effort",
            "values": ["Low", "Medium", "High"],
        }
        # The adjust affordance NEVER becomes a button.
        assert [o["label"] for o in payload["options"]] == [
            "Set as default",
            "Use this session only",
            "Cancel",
        ]

    def test_no_adjust_key_without_the_affordance(self):
        payload = self._menu().payload()
        assert "adjust" not in payload


class TestExistingPayloadShapesUnchanged:
    """One test per shape the change touches: every existing payload
    keeps its exact previous keys (additive-only contract)."""

    def test_select_payload_keeps_its_shape(self):
        dialog = StructuredDialog(
            agent="claude",
            kind="select",
            question="Q?",
            options=(DialogOption(label="A", keys=("1", "enter")),),
            fingerprint="f" * 16,
            hint="Enter to select · Esc to cancel",
        )

        payload = dialog.payload()

        assert list(payload) == [
            "unread",
            "agent",
            "kind",
            "question",
            "step",
            "hint",
            "fingerprint",
            "recipe",
            "options",
        ]
        assert list(payload["options"][0]) == ["label", "description", "checked"]

    def test_walk_payload_keeps_its_shape(self):
        dialog = StructuredDialog(
            agent="claude",
            kind="model_picker",
            question="Select model",
            options=(DialogOption(label="Opus"),),
            fingerprint="f" * 16,
            nav_keys=("up", "down"),
            commit_keys=("enter",),
            selected=0,
        )

        payload = dialog.payload()

        assert payload["recipe"] == "walk"
        assert "adjust" not in payload
        assert list(payload["options"][0]) == ["label", "description", "checked"]


class TestMenuGuardModel:
    def _menu(self, **extra) -> StructuredDialog:
        fields = dict(
            agent="claude",
            kind="menu",
            question="Select model",
            options=(
                DialogOption(label="Set as default", keys=("enter",)),
                DialogOption(label="Use this session only", keys=("s",)),
            ),
            fingerprint="f" * 16,
        )
        fields.update(extra)
        return StructuredDialog(**fields)

    def test_the_guard_twin_compares_by_fingerprint(self):
        from herdr_brain.dialog import same_dialog, same_dialog_identity

        guard = self._menu().as_guard_model()

        assert guard.signature == "f" * 16
        assert [a.keys for a in guard.actions] == [("enter",), ("s",)]
        # The footer's actions are keys-recipe commits: strict equality
        # is THE check, and the twin satisfies it against a fresh twin.
        assert same_dialog(guard, self._menu().as_guard_model()) is True
        assert same_dialog_identity(guard, self._menu().as_guard_model()) is True
