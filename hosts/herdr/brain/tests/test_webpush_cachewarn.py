"""Prompt-cache warning hook (S5): one warning per idle period.

Pure logic — the parent decides when to feed it. A pane's hot context
expires ``ttl`` seconds after its last activity; the warning fires
``warn_before`` seconds early, exactly once per idle period, and the
next activity re-arms it.

The observe() contract takes an idle DELTA, never an absolute
timestamp: a monotonic watcher and a wall-clock service cannot
disagree about a delta (the mixed-domain counter bug this contract
exists to prevent).
"""

from __future__ import annotations

from herdr_brain.webpush.cachewarn import CacheWarnEngine


def test_no_warning_while_hot():
    engine = CacheWarnEngine(ttl_s=300.0, warn_before_s=60.0)
    assert engine.observe("p1", idle_s=100.0, now=1100.0) is None


def test_warning_fires_once_inside_the_warning_margin():
    engine = CacheWarnEngine(ttl_s=300.0, warn_before_s=60.0)
    # 240 s idle: 60 s left on the clock — inside the margin.
    due = engine.observe("p1", idle_s=240.0, now=1240.0)
    assert due is not None and due["pane_id"] == "p1"
    assert due["idle_s"] == 240.0
    assert due["expires_in_s"] == 60.0
    # Later ticks in the SAME idle period stay silent: the dedup stamp
    # reconstructed as now - idle stays constant while the pane idles.
    assert engine.observe("p1", idle_s=250.0, now=1250.0) is None
    assert engine.observe("p1", idle_s=300.0, now=1300.0) is None


def test_activity_rearms_the_warning():
    engine = CacheWarnEngine(ttl_s=300.0, warn_before_s=60.0)
    assert engine.observe("p1", idle_s=240.0, now=1240.0) is not None
    # Activity moved the pane — 240 s into the NEW idle period it is
    # due again (a different reconstructed stamp).
    assert engine.observe("p1", idle_s=240.0, now=1580.0) is not None


def test_panes_are_independent():
    engine = CacheWarnEngine(ttl_s=300.0, warn_before_s=60.0)
    assert engine.observe("p1", idle_s=240.0, now=1240.0) is not None
    assert engine.observe("p2", idle_s=240.0, now=1240.0) is not None
    assert engine.observe("p1", idle_s=250.0, now=1250.0) is None
    assert engine.observe("p2", idle_s=250.0, now=1250.0) is None


def test_past_expiry_still_warns_once_not_silently():
    # Late wiring must not swallow the signal: the context is already
    # cold, which is exactly what the operator needs to hear.
    engine = CacheWarnEngine(ttl_s=300.0, warn_before_s=60.0)
    due = engine.observe("p1", idle_s=400.0, now=1400.0)
    assert due is not None and due["idle_s"] == 400.0
    assert due["expires_in_s"] == 0.0


def test_a_delta_is_domain_free_even_against_a_wall_clock_now():
    # The regression pin: under the old absolute-stamp contract, a
    # monotonic watcher stamp fed to a wall-clock now produced an idle
    # of ~1.79e9 s and every pane warned instantly with an absurd
    # counter. A delta carries no domain, so a huge unrelated ``now``
    # cannot corrupt it.
    engine = CacheWarnEngine(ttl_s=300.0, warn_before_s=60.0)
    due = engine.observe("p1", idle_s=240.0, now=1_794_873_484.5)
    assert due is not None and due["idle_s"] == 240.0
    assert due["expires_in_s"] == 60.0
    # Same idle period (now and idle advance together): still silent.
    assert engine.observe("p1", idle_s=250.0, now=1_794_873_494.5) is None


def test_negative_idle_clamps_to_zero():
    # A clock jitter (stamp marginally after now) must never read as a
    # negative idle and panic the caller.
    engine = CacheWarnEngine(ttl_s=300.0, warn_before_s=60.0)
    assert engine.observe("p1", idle_s=-3.0, now=1000.0) is None
