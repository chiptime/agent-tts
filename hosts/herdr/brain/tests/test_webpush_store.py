"""Subscription store: lifecycle, atomicity, fail-closed corruption (S5).

The store is the risk-item-1 surface: ``push-subscriptions.json`` under
the state dir, 0600, atomic tmp+rename, and a corruption policy of
REFUSING to write or send rather than silently resetting — losing the
file means re-subscribing every phone by hand.
"""

from __future__ import annotations

import json
import os
import stat

import pytest

from herdr_brain.webpush import errors
from herdr_brain.webpush.store import (
    EVICT_AFTER,
    SubscriptionStore,
    push_service_origin,
)


def _add(store, endpoint, created=None, device="phone"):
    return store.add(endpoint, p256dh="A" * 43, auth="B" * 22, device=device, _now=created)


# ── add / replace / remove ───────────────────────────────────────────────────


def test_add_persists_row_with_private_modes(tmp_path):
    store = SubscriptionStore(tmp_path, clock=lambda: 1000.0)
    _add(store, "https://push.example/sub/1", device="movil")
    path = tmp_path / "push-subscriptions.json"
    raw = json.loads(path.read_text())
    row = raw["subscriptions"]["https://push.example/sub/1"]
    assert row["device"] == "movil"
    assert row["keys"]["p256dh"] == "A" * 43
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700


def test_add_replaces_named_endpoint_in_same_write(tmp_path):
    store = SubscriptionStore(tmp_path, clock=lambda: 1000.0)
    _add(store, "https://push.example/sub/old")
    store.add(
        "https://push.example/sub/new",
        p256dh="A" * 43,
        auth="B" * 22,
        replaces="https://push.example/sub/old",
    )
    endpoints = {s.endpoint for s in store.list()}
    assert endpoints == {"https://push.example/sub/new"}


def test_remove_returns_true_only_for_known(tmp_path):
    store = SubscriptionStore(tmp_path, clock=lambda: 1000.0)
    _add(store, "https://push.example/sub/1")
    assert store.remove("https://push.example/sub/1") is True
    assert store.list() == []
    assert store.remove("https://push.example/sub/1") is False


def test_reload_keeps_state(tmp_path):
    store = SubscriptionStore(tmp_path, clock=lambda: 1000.0)
    _add(store, "https://push.example/sub/1", device="tablet")
    reopened = SubscriptionStore(tmp_path, clock=lambda: 2000.0)
    assert [s.device for s in reopened.list()] == ["tablet"]


# ── bounded size ─────────────────────────────────────────────────────────────


def test_bounded_size_evicts_oldest(tmp_path):
    store = SubscriptionStore(tmp_path, clock=lambda: 0.0, max_subscriptions=3)
    for i in range(4):
        _add(store, f"https://push.example/sub/{i}", created=float(i))
    endpoints = {s.endpoint for s in store.list()}
    assert "https://push.example/sub/0" not in endpoints
    assert len(endpoints) == 3


# ── send-result accounting: prune 404/410, witness eviction ──────────────────


def test_gone_statuses_prune_immediately(tmp_path):
    store = SubscriptionStore(tmp_path, clock=lambda: 0.0)
    _add(store, "https://push.example/sub/1")
    store.record_result("https://push.example/sub/1", 404)
    assert store.list() == []
    _add(store, "https://push.example/sub/2")
    store.record_result("https://push.example/sub/2", 410)
    assert store.list() == []


def test_failures_evict_only_with_healthy_sibling_witness(tmp_path):
    store = SubscriptionStore(tmp_path, clock=lambda: 0.0)
    _add(store, "https://push.a/sub/sick")
    _add(store, "https://push.a/sub/healthy")
    _add(store, "https://push.b/sub/other-service")
    for _ in range(EVICT_AFTER):
        store.record_result("https://push.a/sub/sick", 502)
    # No sibling of push.a delivered yet: nothing is evicted.
    assert len(store.list()) == 3
    # A global outage (nobody healthy anywhere) evicts nothing either.
    store.evict_witnessed(set())
    assert len(store.list()) == 3
    # push.a delivered through its healthy sibling: the sick row goes.
    store.record_result("https://push.a/sub/healthy", 201)
    store.evict_witnessed({"https://push.a"})
    endpoints = {s.endpoint for s in store.list()}
    assert "https://push.a/sub/sick" not in endpoints
    assert "https://push.a/sub/healthy" in endpoints
    assert "https://push.b/sub/other-service" in endpoints


def test_success_resets_failure_streak(tmp_path):
    store = SubscriptionStore(tmp_path, clock=lambda: 0.0)
    _add(store, "https://push.example/sub/1")
    for _ in range(EVICT_AFTER - 1):
        store.record_result("https://push.example/sub/1", 500)
    store.record_result("https://push.example/sub/1", 201)
    store.record_result("https://push.example/sub/1", 500)
    store.evict_witnessed({"https://push.example"})
    assert len(store.list()) == 1  # streak was cut by the success


def test_push_service_origin_groups_by_scheme_and_host():
    assert (
        push_service_origin("https://fcm.googleapis.com/fcm/send/abc")
        == "https://fcm.googleapis.com"
    )
    assert (
        push_service_origin("https://fcm.googleapis.com/fcm/send/other")
        == push_service_origin("https://fcm.googleapis.com/fcm/send/abc")
    )


# ── corruption policy: fail closed ───────────────────────────────────────────


def test_corrupt_file_refuses_writes_and_keeps_bytes(tmp_path):
    path = tmp_path / "push-subscriptions.json"
    garbage = b"{not json at all"
    path.write_bytes(garbage)
    store = SubscriptionStore(tmp_path, clock=lambda: 0.0)
    assert store.corrupt is not None
    with pytest.raises(errors.StoreError):
        _add(store, "https://push.example/sub/1")
    assert path.read_bytes() == garbage  # no silent reset, no rewrite
    with pytest.raises(errors.StoreError):
        store.list()


def test_corrupt_file_blocks_sends_through_pending(tmp_path):
    # A corrupted store must never hand out recipients: sending to a
    # stale list after the file broke would surprise devices.
    path = tmp_path / "push-subscriptions.json"
    path.write_bytes(b"[]")  # wrong shape on purpose
    store = SubscriptionStore(tmp_path, clock=lambda: 0.0)
    assert store.corrupt is not None
    with pytest.raises(errors.StoreError):
        store.list()


def test_failed_save_leaves_previous_file_intact(tmp_path, monkeypatch):
    store = SubscriptionStore(tmp_path, clock=lambda: 0.0)
    _add(store, "https://push.example/sub/1")
    path = tmp_path / "push-subscriptions.json"
    before = path.read_bytes()

    real_replace = os.replace

    def broken_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(errors.StoreError):
        _add(store, "https://push.example/sub/2")
    monkeypatch.setattr(os, "replace", real_replace)
    assert path.read_bytes() == before
    leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []  # tmp cleaned up; no partial states


def test_no_temp_file_left_behind_on_success(tmp_path):
    store = SubscriptionStore(tmp_path, clock=lambda: 0.0)
    _add(store, "https://push.example/sub/1")
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
