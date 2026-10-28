"""The dialog guard: what has to be true BEFORE a keystroke is written.

A keystroke lands in a live terminal, so the question is never "did the
dialog look like this a moment ago" but "is it still THAT dialog, right
now". The machinery here is model-parametric — it knows nothing about any
agent's dialog grammar, only how to refuse.

Design following a guard pattern proven in practice by another Herdr plugin
on this machine, reimplemented in brain's own terms. Its
empirical findings are the load-bearing part:

  - Herdr's ``pane.read.revision`` is a stub upstream (always 0, verified
    live 2026-07-05), so the revision is NOT the check that protects a live
    terminal: equality of the derived dialog — highlight included, via its
    signature — is.
  - a committing key must pass strict equality; a navigation key only needs
    the weaker identity check, because the highlight moving IS the expected
    result of the previous arrow.
  - a vanished dialog resolves to "drifted", never to a retryable timeout: a
    blind keystroke would hit whatever replaced it.
"""

from __future__ import annotations

import pytest

from herdr_brain.dialog import (
    DialogAction,
    DialogModel,
    GuardOutcome,
    entry_guard,
    poll_until,
    same_dialog,
    same_dialog_identity,
)


def model(signature="sig-1", title="Permission required") -> DialogModel:
    return DialogModel(
        title=title,
        actions=(
            DialogAction(label="Allow once", keys=("enter",)),
            DialogAction(label="Reject", keys=("right", "enter")),
        ),
        nav_keys=("left", "right"),
        signature=signature,
    )


class FakeReader:
    """Returns queued screens; the guard always asks for a FRESH one."""

    def __init__(self, screens):
        self.screens = list(screens)
        self.calls = 0

    def __call__(self, pane_id, lines):
        self.calls += 1
        screen = self.screens.pop(0) if self.screens else None
        if isinstance(screen, Exception):
            raise screen
        return screen


def detect(screen):
    """Stand-in detector: a screen is a signature or None (no dialog)."""
    return None if screen is None else model(signature=screen)


# -- comparators ------------------------------------------------------------


def test_strict_equality_notices_the_signature():
    assert same_dialog(model("a"), model("a")) is True
    assert same_dialog(model("a"), model("b")) is False


def test_identity_ignores_the_signature():
    """A moved highlight changes the signature by design; the arrow that
    moved it must not be judged as drift."""
    assert same_dialog_identity(model("a"), model("b")) is True


def test_identity_still_separates_different_dialogs():
    assert same_dialog_identity(model("a"), model("a", title="Other")) is False


# -- entry guard ------------------------------------------------------------


def test_guard_passes_when_the_dialog_is_still_the_one_tapped():
    reader = FakeReader(["sig-1"])

    outcome = entry_guard(reader, "w1:p2", 20, model("sig-1"), detect)

    assert outcome.ok is True
    assert outcome.model is not None and outcome.model.signature == "sig-1"
    assert reader.calls == 1  # one FRESH read, never the tapped render


def test_guard_refuses_when_the_screen_moved_on():
    reader = FakeReader(["sig-2"])

    outcome = entry_guard(reader, "w1:p2", 20, model("sig-1"), detect)

    assert outcome.ok is False
    assert outcome.reason == "changed"
    assert outcome.model is None


def test_guard_refuses_when_the_dialog_is_gone():
    outcome = entry_guard(FakeReader([None]), "w1:p2", 20, model("sig-1"), detect)

    assert outcome.ok is False
    assert outcome.reason == "changed"


def test_guard_reports_a_read_failure_instead_of_passing():
    outcome = entry_guard(FakeReader([RuntimeError("herdr gone")]), "w1:p2", 20, model(), detect)

    assert outcome.ok is False
    assert outcome.reason == "error"


def test_guard_never_treats_a_failure_as_a_pass():
    outcome = entry_guard(FakeReader([]), "w1:p2", 20, model(), detect)
    assert outcome.ok is False


# -- three-valued polling ---------------------------------------------------


def test_poll_returns_ok_when_the_awaited_state_arrives():
    reader = FakeReader(["sig-1", "sig-1"])

    verdict = poll_until(
        reader, "w1:p2", 20, model("sig-1"), detect,
        accept=lambda m: True, sleep=lambda _ms: None,
    )

    assert verdict == "ok"


def test_poll_reads_more_than_once_before_accepting():
    screens = ["sig-1"] * 3
    reader = FakeReader(screens)
    seen = {"n": 0}

    def accept(_m):
        seen["n"] += 1
        return seen["n"] == 3

    assert poll_until(reader, "w1:p2", 20, model("sig-1"), detect, accept=accept, sleep=lambda _: None) == "ok"
    assert reader.calls == 3


def test_poll_reports_drift_when_a_different_dialog_appeared():
    reader = FakeReader(["sig-1", "sig-1"])

    verdict = poll_until(
        reader, "w1:p2", 20, model("sig-1"), detect,
        accept=lambda _m: False,
        sleep=lambda _ms: None,
        attempts=2,
    )

    assert verdict == "timeout"  # same dialog, state never came: retry is safe


def test_poll_reports_drift_when_the_identity_changed():
    reader = FakeReader(["sig-1", "sig-1"])

    verdict = poll_until(
        reader, "w1:p2", 20, model("sig-1"), detect,
        accept=lambda _m: False,
        sleep=lambda _ms: None,
        attempts=2,
        identity=lambda a, b: False,
    )

    assert verdict == "drifted"


def test_poll_reports_drift_when_the_dialog_vanished_never_timeout():
    """A gone dialog must never invite a retry: the key would land on
    whatever replaced it."""
    reader = FakeReader([None, None])

    verdict = poll_until(
        reader, "w1:p2", 20, model("sig-1"), detect,
        accept=lambda _m: False,
        sleep=lambda _ms: None,
        attempts=2,
    )

    assert verdict == "drifted"
