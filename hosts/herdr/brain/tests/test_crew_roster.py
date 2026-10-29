"""The crew roster (S7 core): roles, peers with pinned certificates,
and the crew secret stored only as a hash with a rotation grace window.

Every rule here is the persisted-credentials risk (doc 10 risk 1/2):
the file is written atomically with 0600, a corrupt file fails CLOSED
(never a silent reset to solo), and the secret itself never lands on
disk.
"""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

import pytest

from herdr_brain.pairing import sha256_hex

from herdr_brain.crew.roster import (
    CREW_FILENAME,
    CrewRoster,
    CrewRosterCorrupt,
    CrewStateError,
)

FIXTURES = Path(__file__).parent / "fixtures" / "crew"
PEER_PEM = (FIXTURES / "peer.pem").read_text(encoding="utf-8")


def der_pin(path: Path) -> str:
    """The SHA-256 pin of a certificate file's DER bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state"  # NOT pre-created: roster mkdir's it 0700


def load(state_dir: Path) -> CrewRoster:
    return CrewRoster.load(state_dir, now=lambda: 1_000.0)


# ── modes ────────────────────────────────────────────────────────────────────


def test_fresh_state_is_solo_with_no_peers(state_dir: Path) -> None:
    roster = load(state_dir)
    assert roster.role == "solo"
    assert roster.self_name is None
    assert roster.peers == {}
    assert not roster.has_peers
    assert not roster.has_crew


def test_init_lead_mints_secret_shown_only_as_hash(state_dir: Path) -> None:
    roster = load(state_dir)
    roster.init_lead("host-a", "s3cret-value", now=1_000.0)
    roster.persist()

    assert roster.role == "lead"
    assert roster.self_name == "host-a"
    assert roster.lead_name == "host-a"
    assert roster.secret_matches("s3cret-value", now=1_001.0)
    assert not roster.secret_matches("wrong", now=1_001.0)

    raw = (state_dir / CREW_FILENAME).read_text(encoding="utf-8")
    assert "s3cret-value" not in raw  # the secret never lands on disk
    assert sha256_hex("s3cret-value") in raw  # only its hash does


def test_init_twice_refuses(state_dir: Path) -> None:
    roster = load(state_dir)
    roster.init_lead("host-a", "crew-secret-1", now=1_000.0)
    with pytest.raises(CrewStateError):
        roster.init_lead("host-a", "crew-secret-2", now=1_001.0)


# ── persistence discipline: 0600, atomic, fail-closed ───────────────────────


def test_roster_file_is_0600_dir_0700_no_tmp_left(state_dir: Path) -> None:
    roster = load(state_dir)
    roster.init_lead("host-a", "crew-secret-1", now=1_000.0)
    roster.record_peer("host-b", "https://b.example:8747", PEER_PEM, now=1_002.0)
    roster.persist()
    roster.rotate_secret("crew-secret-2", now=1_003.0)
    roster.persist()

    path = state_dir / CREW_FILENAME
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600
    assert stat.S_IMODE(state_dir.stat().st_mode) == 0o700
    # atomic write: the rename landed, no temp siblings survive
    leftovers = [p.name for p in state_dir.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []
    json.loads(path.read_text(encoding="utf-8"))  # and it parses


def test_corrupt_roster_fails_closed(state_dir: Path) -> None:
    roster = load(state_dir)
    roster.init_lead("host-a", "crew-secret-1", now=1_000.0)
    roster.persist()
    (state_dir / CREW_FILENAME).write_text("{not json", encoding="utf-8")

    with pytest.raises(CrewRosterCorrupt):
        CrewRoster.load(state_dir)


def test_truncated_roster_fails_closed(state_dir: Path) -> None:
    # valid JSON, wrong shape: still closed, never a silent solo reset
    state_dir.mkdir(parents=True)
    (state_dir / CREW_FILENAME).write_text('{"role": "lead"}', encoding="utf-8")
    state_dir.chmod(0o700)
    with pytest.raises(CrewRosterCorrupt):
        CrewRoster.load(state_dir)


# ── peers ────────────────────────────────────────────────────────────────────


def test_record_peer_pins_certificate_sha256(state_dir: Path) -> None:
    roster = load(state_dir)
    roster.init_lead("host-a", "crew-secret-1", now=1_000.0)
    roster.record_peer("host-b", "https://b.example:8747", PEER_PEM, now=1_002.0)

    entry = roster.peers["host-b"]
    assert set(entry) == {"url", "cert_sha256_pin", "added_at"}
    assert entry["url"] == "https://b.example:8747"
    assert entry["cert_sha256_pin"] == der_pin(FIXTURES / "peer.der")
    assert entry["added_at"] == 1_002.0
    assert roster.has_peers and roster.has_crew


def test_record_peer_rejects_plain_http_and_bad_names(state_dir: Path) -> None:
    roster = load(state_dir)
    roster.init_lead("host-a", "crew-secret-1", now=1_000.0)
    with pytest.raises(CrewStateError):
        roster.record_peer("host-b", "http://b.example:8747", PEER_PEM, now=1_002.0)
    with pytest.raises(CrewStateError):
        roster.record_peer("Bad Name", "https://b.example:8747", PEER_PEM, now=1_002.0)
    with pytest.raises(CrewStateError):
        roster.record_peer("host-b", "https://b.example:8747", "not a pem", now=1_002.0)
    assert roster.peers == {}


def test_join_as_peer_records_lead(state_dir: Path) -> None:
    roster = load(state_dir)
    lead_pem = (FIXTURES / "lead.pem").read_text(encoding="utf-8")
    roster.join_as_peer(
        "host-b", "host-a", "https://a.example:8747", lead_pem, "crew-secret-1", now=1_000.0
    )
    roster.persist()

    assert roster.role == "peer"
    assert roster.self_name == "host-b"
    assert roster.lead_name == "host-a"
    assert roster.peers["host-a"]["url"] == "https://a.example:8747"
    assert roster.secret_matches("crew-secret-1", now=1_001.0)


def test_remove_peer_and_leave(state_dir: Path) -> None:
    roster = load(state_dir)
    roster.init_lead("host-a", "crew-secret-1", now=1_000.0)
    roster.record_peer("host-b", "https://b.example:8747", PEER_PEM, now=1_002.0)

    assert roster.remove("host-b") is True
    assert roster.peers == {}
    assert roster.remove("host-b") is False


# ── secret rotation with a grace window ──────────────────────────────────────


def test_rotation_keeps_previous_secret_valid_within_grace(state_dir: Path) -> None:
    roster = load(state_dir)
    roster.init_lead("host-a", "old-secret-0", now=1_000.0)
    roster.rotate_secret("new-secret-0", now=2_000.0, grace_s=300.0)

    # the new secret is valid immediately and stays valid
    assert roster.secret_matches("new-secret-0", now=2_000.0)
    assert roster.secret_matches("new-secret-0", now=9_999.0)
    # the previous secret stays valid only inside the grace window
    assert roster.secret_matches("old-secret-0", now=2_299.0)
    assert not roster.secret_matches("old-secret-0", now=2_301.0)
    # and nothing else ever matches
    assert not roster.secret_matches("other", now=2_100.0)


def test_no_grace_before_first_rotation(state_dir: Path) -> None:
    roster = load(state_dir)
    roster.init_lead("host-a", "crew-secret-1", now=1_000.0)
    assert not roster.secret_matches("anything-else", now=1_000.0)


# ── manual promotion ─────────────────────────────────────────────────────────


def test_promote_rewrites_roles_explicitly(state_dir: Path) -> None:
    lead = load(state_dir)
    lead.init_lead("host-a", "crew-secret-1", now=1_000.0)
    lead.record_peer("host-b", "https://b.example:8747", PEER_PEM, now=1_002.0)
    lead.persist()

    # the promoting host declares ITSELF the new lead; the old lead is
    # demoted to a plain peer member — explicit, operator-driven, once.
    promoted = CrewRoster.load(state_dir, now=lambda: 1_005.0)
    promoted.promote("host-a")
    assert promoted.role == "lead"
    assert promoted.lead_name == "host-a"
    assert set(promoted.peers) == {"host-b"}


def test_promote_peer_side_marks_demotion(state_dir: Path) -> None:
    roster = load(state_dir)
    roster.join_as_peer(
        "host-b", "host-a", "https://a.example:8747",
        (FIXTURES / "lead.pem").read_text(encoding="utf-8"), "crew-secret-1", now=1_000.0,
    )
    roster.promote("host-a")  # the lead declared elsewhere: we demote to peer
    assert roster.role == "peer"
    assert roster.lead_name == "host-a"


def test_promote_unknown_member_refuses_and_leaves_file_untouched(
    state_dir: Path,
) -> None:
    roster = load(state_dir)
    roster.init_lead("host-a", "crew-secret-1", now=1_000.0)
    roster.persist()
    before = (state_dir / CREW_FILENAME).read_text(encoding="utf-8")

    with pytest.raises(CrewStateError):
        roster.promote("ghost-host")
    roster.persist()
    assert (state_dir / CREW_FILENAME).read_text(encoding="utf-8") == before
