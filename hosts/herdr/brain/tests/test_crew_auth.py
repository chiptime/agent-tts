"""The crew two-factor check (S7): a crew request is authenticated
only when BOTH independent factors hold — the TLS peer certificate's
SHA-256 pin matches an enrolled member AND the Bearer crew secret
matches (current, or previous inside the rotation grace window).

Any failure — either factor wrong, missing, or the roster unreadable —
yields the SAME refusal. Which factor failed is never disclosed.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from herdr_brain.crew.auth import check_crew_auth
from herdr_brain.crew.roster import CrewRoster

FIXTURES = Path(__file__).parent / "fixtures" / "crew"
PEER_DER = (FIXTURES / "peer.der").read_bytes()
LEAD_DER = (FIXTURES / "lead.der").read_bytes()
PEER_PEM = (FIXTURES / "peer.pem").read_text(encoding="utf-8")

SECRET = "crew-secret-1"


def headers_with(secret: str | None) -> dict:
    h = {}
    if secret is not None:
        h["authorization"] = f"Bearer {secret}"
    return h


def getter(headers: dict):
    return lambda name: headers.get(name.lower())


@pytest.fixture
def roster(tmp_path: Path) -> CrewRoster:
    state = CrewRoster.load(tmp_path / "state", now=lambda: 1_000.0)
    state.init_lead("host-a", SECRET, now=1_000.0)
    state.record_peer("host-b", "https://b.example:8747", PEER_PEM, now=1_001.0)
    return state


def auth(headers: dict, der: bytes | None, state: CrewRoster, now: float = 1_100.0):
    return check_crew_auth(getter(headers), der, state, now=now)


# ── both factors present and correct ─────────────────────────────────────────


def test_matching_pin_and_secret_authenticate_the_member(roster: CrewRoster) -> None:
    assert auth(headers_with(SECRET), PEER_DER, roster) == "host-b"


# ── the matrix: each factor wrong or missing ────────────────────────────────


def test_wrong_certificate_pin_is_refused(roster: CrewRoster) -> None:
    # LEAD_DER is a valid certificate, but not the enrolled member's
    assert auth(headers_with(SECRET), LEAD_DER, roster) is None


def test_missing_certificate_is_refused(roster: CrewRoster) -> None:
    assert auth(headers_with(SECRET), None, roster) is None


def test_garbage_certificate_bytes_are_refused(roster: CrewRoster) -> None:
    assert auth(headers_with(SECRET), b"\x00\x01not-a-cert", roster) is None


def test_wrong_secret_is_refused(roster: CrewRoster) -> None:
    assert auth(headers_with("wrong-secret"), PEER_DER, roster) is None


def test_missing_secret_is_refused(roster: CrewRoster) -> None:
    assert auth({}, PEER_DER, roster) is None


def test_non_bearer_scheme_is_refused(roster: CrewRoster) -> None:
    assert auth({"authorization": f"Basic {SECRET}"}, PEER_DER, roster) is None


def test_both_factors_wrong_is_refused(roster: CrewRoster) -> None:
    assert auth(headers_with("nope"), LEAD_DER, roster) is None


def test_solo_roster_authenticates_nobody(tmp_path: Path) -> None:
    state = CrewRoster.load(tmp_path / "state", now=lambda: 1_000.0)
    assert auth(headers_with(SECRET), PEER_DER, state) is None


# ── the refusal carries no oracle ────────────────────────────────────────────


def test_every_failure_produces_the_identical_refusal(roster: CrewRoster) -> None:
    from herdr_brain.crew.auth import refusal_response

    cases = [
        (headers_with("wrong-secret"), PEER_DER),  # secret factor wrong
        (headers_with(SECRET), LEAD_DER),          # cert factor wrong
        ({}, PEER_DER),                            # secret factor missing
        (headers_with("nope"), LEAD_DER),          # both wrong
    ]
    refusals = [repr(refusal_response()) for _ in cases]
    assert len(set(refusals)) == 1  # one constant refusal shape
    status, body = refusal_response()
    assert status == 401 and body == {"detail": "unauthorized"}
    for hdrs, der in cases:
        assert auth(hdrs, der, roster) is None


# ── rotation grace on the wire ───────────────────────────────────────────────


def test_previous_secret_authenticates_inside_grace(roster: CrewRoster) -> None:
    roster.rotate_secret("crew-secret-2", now=1_500.0, grace_s=300.0)
    # the member's cert is unchanged; the OLD secret still works at 1_700
    assert auth(headers_with(SECRET), PEER_DER, roster, now=1_700.0) == "host-b"
    assert (
        auth(headers_with("crew-secret-2"), PEER_DER, roster, now=1_700.0) == "host-b"
    )
    # after the grace deadline only the new secret works
    assert auth(headers_with(SECRET), PEER_DER, roster, now=1_801.0) is None
    assert (
        auth(headers_with("crew-secret-2"), PEER_DER, roster, now=1_801.0) == "host-b"
    )


# ── pin derivation ───────────────────────────────────────────────────────────


def test_pin_is_sha256_of_der_bytes() -> None:
    from herdr_brain.crew.auth import cert_sha256_pin

    assert cert_sha256_pin(PEER_DER) == hashlib.sha256(PEER_DER).hexdigest()
    assert cert_sha256_pin(None) is None
