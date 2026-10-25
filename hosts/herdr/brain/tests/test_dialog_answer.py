"""Walking the highlight to an option, then committing — never blind.

The dialog puts the highlight somewhere and only `enter` commits, so choosing
"Reject" means pressing the arrow N times, RE-READING after each press, and
committing only once the highlight is provably there. A world that models the
dialog (and can be told to misbehave) stands in for a live pane: no agent, no
terminal, no keystroke.
"""

from __future__ import annotations

import pytest

from herdr_brain.dialog import (
    DialogAction,
    DialogModel,
    answer_dialog,
    GuardOutcome,
)

LABELS = ("Allow once", "Allow always", "Reject")


class World:
    """A dialog that moves when it is told to, and can be made to misbehave."""

    def __init__(
        self,
        selected=0,
        *,
        moves=True,
        vanish_after=None,
        drift_to=None,
        drift_from_first=False,
        nav_keys=("left", "right"),
        commit_keys=("enter",),
    ):
        self.selected = selected
        self.moves = moves
        self.vanish_after = vanish_after
        self.drift_to = drift_to
        self.drift_from_first = drift_from_first
        self.nav_keys = nav_keys
        self.commit_keys = commit_keys
        self.reads = 0
        self.sent: list = []

    # the pane read
    def reader(self, pane_id, lines):
        self.reads += 1
        return "screen"

    def detect(self, screen):
        if self.vanish_after is not None and self.reads > self.vanish_after:
            return None  # the dialog is gone: the agent resumed
        if self.drift_to is not None and (self.drift_from_first or self.reads > 1):
            return self.model(title=self.drift_to)
        return self.model()

    def model(self, title="Permission required"):
        return DialogModel(
            title=title,
            actions=tuple(DialogAction(label, ()) for label in LABELS),
            nav_keys=self.nav_keys,
            commit_keys=self.commit_keys,
            selected=self.selected,
            signature=f"sig-{self.selected}",
        )

    # the keystroke door
    def send_keys(self, pane_id, keys):
        self.sent.append(tuple(keys))
        if not self.moves:
            return
        for key in keys:
            if key == "right":
                self.selected = min(self.selected + 1, len(LABELS) - 1)
            elif key == "left":
                self.selected = max(self.selected - 1, 0)

    @property
    def keys(self):
        return [key for batch in self.sent for key in batch]


def answer(world, target, **kwargs):
    return answer_dialog(
        world.reader,
        world.send_keys,
        "w1:p2",
        40,
        world.model() if kwargs.pop("tapped", None) is None else None,
        target,
        world.detect,
        sleep=lambda _seconds: None,
        **kwargs,
    )


# -- the happy walks --------------------------------------------------------


def test_commits_immediately_when_the_highlight_is_already_there():
    world = World(selected=0)

    outcome = answer(world, 0)

    assert outcome.sent is True
    assert world.keys == ["enter"]


@pytest.mark.parametrize(
    "start,target,expected",
    [
        (0, 1, ["right", "enter"]),
        (0, 2, ["right", "right", "enter"]),
        (2, 0, ["left", "left", "enter"]),
        (2, 1, ["left", "enter"]),
    ],
)
def test_walks_with_the_arrows_the_screen_named(start, target, expected):
    world = World(selected=start)

    outcome = answer(world, target)

    assert outcome.sent is True
    assert world.keys == expected


def test_never_commits_before_the_highlight_arrived():
    """The commit is the LAST thing, and only after the walk is verified."""
    world = World(selected=0)

    answer(world, 2)

    assert world.sent[-1] == ("enter",)
    assert [batch for batch in world.sent[:-1]] == [("right",), ("right",)]


# -- refusing -----------------------------------------------------------------


def test_refuses_when_the_screen_moved_before_the_first_key():
    """The entry guard reads FRESH: a dialog that is not the one tapped is
    refused before a single keystroke is written."""
    world = World()
    tapped = world.model()
    world.drift_to = "some other dialog"
    world.drift_from_first = True

    outcome = answer_dialog(
        world.reader, world.send_keys, "w1:p2", 40, tapped, 1, world.detect,
        sleep=lambda _s: None,
    )

    assert outcome.sent is False
    assert outcome.reason == "changed"
    assert world.sent == []


def test_aborts_without_committing_when_the_dialog_vanishes_mid_walk():
    world = World(selected=0, vanish_after=2)

    outcome = answer(world, 2)

    assert outcome.sent is False
    assert outcome.reason == "changed"
    assert "enter" not in world.keys


def test_gives_up_without_committing_when_the_arrows_do_nothing():
    """A dialog that ignores the key is a dialog we must not commit into."""
    world = World(selected=0, moves=False)

    outcome = answer(world, 2)

    assert outcome.sent is False
    assert outcome.reason == "no_progress"
    assert "enter" not in world.keys


def test_refuses_an_option_it_cannot_reach():
    world = World(selected=0, nav_keys=())

    outcome = answer(world, 2)

    assert outcome.sent is False
    assert outcome.reason == "unreachable_option"
    assert world.sent == []


def test_refuses_a_dialog_that_named_no_commit_key():
    world = World(selected=0, commit_keys=())

    outcome = answer(world, 0)

    assert outcome.sent is False
    assert world.sent == []


def test_an_out_of_range_target_is_refused():
    world = World(selected=0)

    outcome = answer(world, 99)

    assert outcome.sent is False
    assert world.sent == []
