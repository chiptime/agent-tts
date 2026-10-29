"""Prompt-cache warning hook (S5): one warning per idle period.

Pure logic — the parent decides when to feed it. A pane's hot context
expires ``ttl`` seconds after its last activity; the warning fires
``warn_before`` seconds early, exactly once per idle period, and the
next activity re-arms it.
"""

from __future__ import annotations

from herdr_brain.webpush.cachewarn import CacheWarnEngine


def test_no_warning_while_active():
    engine = CacheWarnEngine(ttl_s=300.0, warn_before_s=60.0)
    assert engine.observe("p1", last_activity=1000.0, now=1100.0) is None


def test_warning_fires_once_inside_the_warning_margin():
    engine = CacheWarnEngine(ttl_s=300.0, warn_before_s=60.0)
    # 240 s idle: 60 s left on the clock — inside the margin.
    due = engine.observe("p1", last_activity=1000.0, now=1240.0)
    assert due is not None and due["pane_id"] == "p1"
    assert due["idle_s"] == 240.0
    assert due["expires_in_s"] == 60.0
    # Later ticks in the SAME idle period stay silent.
    assert engine.observe("p1", last_activity=1000.0, now=250.0 + 1000.0) is None
    assert engine.observe("p1", last_activity=1000.0, now=1300.0) is None


def test_activity_rearms_the_warning():
    engine = CacheWarnEngine(ttl_s=300.0, warn_before_s=60.0)
    assert engine.observe("p1", last_activity=1000.0, now=1240.0) is not None
    assert engine.observe("p1", last_activity=1300.0, now=1540.0) is not None


def test_panes_are_independent():
    engine = CacheWarnEngine(ttl_s=300.0, warn_before_s=60.0)
    assert engine.observe("p1", last_activity=1000.0, now=1240.0) is not None
    assert engine.observe("p2", last_activity=1000.0, now=1240.0) is not None
    assert engine.observe("p1", last_activity=1000.0, now=1250.0) is None
    assert engine.observe("p2", last_activity=1000.0, now=1250.0) is None


def test_past_expiry_still_warns_once_not_silently():
    # Late wiring must not swallow the signal: the context is already
    # cold, which is exactly what the operator needs to hear.
    engine = CacheWarnEngine(ttl_s=300.0, warn_before_s=60.0)
    due = engine.observe("p1", last_activity=1000.0, now=1400.0)
    assert due is not None and due["idle_s"] == 400.0
    assert due["expires_in_s"] == 0.0
