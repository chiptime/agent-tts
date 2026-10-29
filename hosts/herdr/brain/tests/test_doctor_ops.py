"""Operational doctor checks (S8, doc 11 fase 2): the brain-side rows.

Distinct from the onboarding doctor (``tools/herdr_onboarding/doctor.py``,
which probes the install): these rows are the RUNNING brain's own
surfaces — port conflicts, the HTTPS root's ownership (from a RECORDED
``tailscale serve status --json`` the operator supplies; the brain
never runs tailscale), VAPID keys, the push subscription store, the
crew roster and the operator config. Every check is read-only, secret
values never reach a detail string, and each failure names its repair.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from herdr_brain import doctor


FIXTURES = Path(__file__).parent / "fixtures" / "ops"


# ── port conflicts ──────────────────────────────────────────────────────────


class TestPortConflicts:
    def test_free_ports_pass(self):
        rows = doctor.check_ports(
            probe=lambda port: False, brain_port=8741, crew_port=8747
        )
        assert [r.name for r in rows] == ["port-brain", "port-crew"]
        assert all(r.ok for r in rows)

    def test_busy_brain_port_fails_naming_the_port(self):
        rows = doctor.check_ports(
            probe=lambda port: port == 8741, brain_port=8741, crew_port=8747
        )
        assert rows[0].ok is False
        assert "8741" in rows[0].detail and "8741" in rows[0].hint

    def test_busy_crew_port_fails(self):
        rows = doctor.check_ports(
            probe=lambda port: port == 8747, brain_port=8741, crew_port=8747
        )
        assert rows[1].ok is False
        assert "8747" in rows[1].hint


# ── root 443 ownership (recorded status JSON, never executed) ───────────────


class TestRoot443Ownership:
    def test_another_service_owning_443_is_the_hint(self):
        text = (FIXTURES / "serve-status-other-owns-443.json").read_text()
        row = doctor.check_root_443(text, brain_port=8741)
        assert row.ok is True  # informational: it warns, it does not fail
        assert "another service owns the HTTPS root port" in row.detail
        assert "8787" in row.detail  # names the recorded target generically

    def test_brain_owning_443_is_reported_as_ours(self):
        text = (FIXTURES / "serve-status-brain-owns-443.json").read_text()
        row = doctor.check_root_443(text, brain_port=8741)
        assert row.ok is True
        assert "already points at this brain" in row.detail

    def test_free_root_reports_no_mapping(self):
        text = (FIXTURES / "serve-status-free.json").read_text()
        row = doctor.check_root_443(text, brain_port=8741)
        assert row.ok is True
        assert "no mapping on 443" in row.detail

    def test_unparseable_status_is_an_honest_not_a_crash(self):
        row = doctor.check_root_443("not json", brain_port=8741)
        assert row.ok is True
        assert "could not read" in row.detail


# ── VAPID + push store ──────────────────────────────────────────────────────


class TestPushRows:
    def test_missing_vapid_points_at_starting_the_service(self, tmp_path):
        rows = doctor.check_push(tmp_path)
        vapid = next(r for r in rows if r.name == "vapid")
        assert vapid.ok is False
        assert "first start" in vapid.detail
        assert "herdr-brain restart" in vapid.hint

    def test_present_vapid_passes_without_leaking_the_private_key(self, tmp_path):
        from herdr_brain.webpush.vapid import generate_keys, save_keys

        keys = generate_keys()
        save_keys(tmp_path, keys)
        vapid = next(r for r in doctor.check_push(tmp_path) if r.name == "vapid")
        assert vapid.ok is True
        # No key material of either half reaches the report.
        assert keys.public_b64url[:8] not in vapid.detail

    def test_healthy_store_counts_subscriptions(self, tmp_path):
        from herdr_brain.webpush.store import SubscriptionStore

        store = SubscriptionStore(tmp_path)
        store.add("https://push.example/e1", "A" * 100, "B" * 20)
        row = next(r for r in doctor.check_push(tmp_path) if r.name == "push-store")
        assert row.ok is True
        assert "1" in row.detail

    def test_corrupt_store_fails_loudly(self, tmp_path):
        (tmp_path / "push-subscriptions.json").write_text("{corrupt")
        row = next(r for r in doctor.check_push(tmp_path) if r.name == "push-store")
        assert row.ok is False
        assert "dañado" in row.detail or "corrupt" in row.detail.lower()


# ── crew roster ─────────────────────────────────────────────────────────────


class TestCrewRow:
    def test_solo_is_ok_and_says_solo(self, tmp_path):
        row = doctor.check_crew(tmp_path)
        assert row.ok is True
        assert "solo" in row.detail

    def test_lead_roster_lists_the_role(self, tmp_path):
        from herdr_brain.crew.roster import CrewRoster

        roster = CrewRoster.load(tmp_path, now=lambda: 1_000.0)
        roster.init_lead("host-a", "crew-secret-1", now=1.0)
        roster.persist()
        row = doctor.check_crew(tmp_path)
        assert row.ok is True
        assert "lead" in row.detail and "host-a" in row.detail

    def test_corrupt_roster_fails_closed(self, tmp_path):
        (tmp_path / "crew.json").write_text("{corrupt")
        row = doctor.check_crew(tmp_path)
        assert row.ok is False
        assert "crew.json" in row.hint


# ── operator config ────────────────────────────────────────────────────────


class TestOperatorConfigRow:
    def test_empty_config_dir_is_ok(self, tmp_path):
        row = doctor.check_operator_config(config_dir=tmp_path)
        assert row.ok is True

    def test_healthy_rows_are_counted(self, tmp_path):
        (tmp_path / "quick-replies.toml").write_text(
            '[[replies]]\nlabel = "Sigue"\ntext = "sigue"\n'
        )
        row = doctor.check_operator_config(config_dir=tmp_path)
        assert row.ok is True
        assert "1" in row.detail

    def test_a_skipped_row_fails_with_its_reason(self, tmp_path):
        (tmp_path / "keys.toml").write_text(
            '[[keys]]\nlabel = "Bad"\nkeys = ["PageUp"]\n'
        )
        row = doctor.check_operator_config(config_dir=tmp_path)
        assert row.ok is False
        assert "keys.toml" in row.detail and "PageUp" in row.detail
        assert "config check" in row.hint


# ── the report ──────────────────────────────────────────────────────────────


class TestReport:
    def test_report_exits_nonzero_only_on_failed_rows(self, tmp_path, capsys):
        rows = doctor.check_ports(
            probe=lambda port: True, brain_port=8741, crew_port=8747
        )
        assert doctor.report(rows) == 1
        out = capsys.readouterr().out
        assert "FAIL port-brain" in out

    def test_all_ok_report_exits_zero(self, capsys):
        rows = doctor.check_ports(
            probe=lambda port: False, brain_port=8741, crew_port=8747
        )
        assert doctor.report(rows) == 0
