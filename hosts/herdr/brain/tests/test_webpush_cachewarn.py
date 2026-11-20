"""Prompt-cache warning hook (S5): one warning per idle period.

Pure logic — the parent decides when to feed it. A pane's hot context
expires ``ttl`` seconds after its last activity; the warning fires
``warn_before`` seconds early, exactly once per idle period, and the
next activity re-arms it.

observe() takes an idle DELTA and no clock at all: dedup lives on the
delta's own monotonicity, so no pair of caller clocks can make one
idle period look like many (the duplicate-notification bug this
contract exists to prevent).
"""

from __future__ import annotations

from herdr_brain.webpush.cachewarn import CacheWarnEngine


def test_no_warning_while_hot():
    engine = CacheWarnEngine(ttl_s=300.0, warn_before_s=60.0)
    assert engine.observe("p1", idle_s=100.0) is None


def test_warning_fires_once_inside_the_warning_margin():
    engine = CacheWarnEngine(ttl_s=300.0, warn_before_s=60.0)
    # 240 s idle: 60 s left on the clock — inside the margin.
    due = engine.observe("p1", idle_s=240.0)
    assert due is not None and due["pane_id"] == "p1"
    assert due["idle_s"] == 240.0
    assert due["expires_in_s"] == 60.0
    # Later ticks in the SAME idle period stay silent: the delta only
    # grows while the pane idles.
    assert engine.observe("p1", idle_s=250.0) is None
    assert engine.observe("p1", idle_s=300.0) is None
    assert engine.observe("p1", idle_s=1000.0) is None


def test_poll_jitter_never_fabricates_new_idle_periods():
    # The regression pin, in two parts. The first dedup reconstructed a
    # stamp as ``now - idle`` with both sides sampled from different
    # clocks at different instants, so the stamp wobbled by microseconds
    # between polls of ONE idle period and each wobble read as a NEW
    # period — a duplicate warning per poll. observe() now takes no
    # clock, so no jitter can fabricate a new period; and a wall-scale
    # ``now`` cannot leak into the idle at all.
    engine = CacheWarnEngine(ttl_s=300.0, warn_before_s=60.0)
    assert engine.observe("p1", idle_s=240.0) is not None
    for tick in range(1, 25):
        # The watcher's poll cadence: the delta just grows.
        assert engine.observe("p1", idle_s=240.0 + 4.5 * tick) is None


def test_activity_rearms_the_warning():
    engine = CacheWarnEngine(ttl_s=300.0, warn_before_s=60.0)
    assert engine.observe("p1", idle_s=240.0) is not None
    # The pane worked again: its idle delta went DOWN — recorded even
    # while hot — so 240 s into the NEW idle period it is due again.
    assert engine.observe("p1", idle_s=10.0) is None
    assert engine.observe("p1", idle_s=240.0) is not None


def test_panes_are_independent():
    engine = CacheWarnEngine(ttl_s=300.0, warn_before_s=60.0)
    assert engine.observe("p1", idle_s=240.0) is not None
    assert engine.observe("p2", idle_s=240.0) is not None
    assert engine.observe("p1", idle_s=250.0) is None
    assert engine.observe("p2", idle_s=250.0) is None


def test_past_expiry_still_warns_once_not_silently():
    # Late wiring must not swallow the signal: the context is already
    # cold, which is exactly what the operator needs to hear.
    engine = CacheWarnEngine(ttl_s=300.0, warn_before_s=60.0)
    due = engine.observe("p1", idle_s=400.0)
    assert due is not None and due["idle_s"] == 400.0
    assert due["expires_in_s"] == 0.0


def test_negative_idle_clamps_to_zero():
    # A clock jitter (stamp marginally after now) must never read as a
    # negative idle and panic the caller.
    engine = CacheWarnEngine(ttl_s=300.0, warn_before_s=60.0)
    assert engine.observe("p1", idle_s=-3.0) is None
