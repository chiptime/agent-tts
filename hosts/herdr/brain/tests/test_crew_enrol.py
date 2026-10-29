"""Join tokens (S7 enrolment): minted on the lead, single use, short
TTL, bounded guesses — the pairing.py code discipline carried over to
crew enrolment. A peer presents the token plus its certificate PEM and
name/url; the lead records the peer with the certificate's pin.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from herdr_brain.crew.enrol import (
    JOIN_TOKEN_ATTEMPTS,
    JOIN_TOKEN_TTL_S,
    JoinTokens,
)
from herdr_brain.crew.roster import CrewRoster

FIXTURES = Path(__file__).parent / "fixtures" / "crew"
PEER_PEM = (FIXTURES / "peer.pem").read_text(encoding="utf-8")

URL = "https://b.example:8747"


class Clock:
    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state"


@pytest.fixture
def lead(state_dir: Path, clock: Clock) -> CrewRoster:
    roster = CrewRoster.load(state_dir, now=clock)
    roster.init_lead("host-a", "crew-secret-1", now=clock())
    return roster


def tokens(state_dir: Path, clock: Clock) -> JoinTokens:
    return JoinTokens(state_dir, now=clock)


# ── minting ──────────────────────────────────────────────────────────────────


def test_mint_shows_token_once_and_stores_only_hash(
    state_dir: Path, clock: Clock, lead: CrewRoster
) -> None:
    token = tokens(state_dir, clock).mint()
    assert isinstance(token, str) and len(token) >= 20

    raw = (state_dir / "crew-join-token.json").read_text(encoding="utf-8")
    assert token not in raw  # hash on disk, never the token
    mode = stat.S_IMODE((state_dir / "crew-join-token.json").stat().st_mode)
    assert mode == 0o600


def test_mint_replaces_previous_token(state_dir: Path, clock: Clock, lead: CrewRoster) -> None:
    jar = tokens(state_dir, clock)
    first = jar.mint()
    second = jar.mint()
    assert first != second
    # only the newest token can enrol: the first one is refused
    # (it reads as an unknown token and burns budget like any guess)
    reason, entry = jar.present(first, name="host-b", url=URL, cert_pem=PEER_PEM, roster=lead)
    assert reason != "enrolled"
    assert entry is None


# ── single use ───────────────────────────────────────────────────────────────


def test_present_consumes_token_exactly_once(
    state_dir: Path, clock: Clock, lead: CrewRoster
) -> None:
    jar = tokens(state_dir, clock)
    token = jar.mint()

    reason, entry = jar.present(token, name="host-b", url=URL, cert_pem=PEER_PEM, roster=lead)
    assert reason == "enrolled"
    assert entry is not None and entry["url"] == URL
    assert "host-b" in lead.peers

    again, entry2 = jar.present(token, name="host-c", url=URL, cert_pem=PEER_PEM, roster=lead)
    assert again != "enrolled"
    assert entry2 is None
    assert "host-c" not in lead.peers  # the replay enrolled nobody


# ── TTL and attempt limit ────────────────────────────────────────────────────


def test_expired_token_is_refused_and_destroyed(
    state_dir: Path, clock: Clock, lead: CrewRoster
) -> None:
    jar = tokens(state_dir, clock)
    token = jar.mint()
    clock.now += JOIN_TOKEN_TTL_S + 1

    reason, _ = jar.present(token, name="host-b", url=URL, cert_pem=PEER_PEM, roster=lead)
    assert reason == "expired"
    # destroyed, not parked: a later present is refused outright
    reason2, _ = jar.present(token, name="host-b", url=URL, cert_pem=PEER_PEM, roster=lead)
    assert reason2 == "no-token"


def test_wrong_token_guesses_are_bounded(
    state_dir: Path, clock: Clock, lead: CrewRoster
) -> None:
    jar = tokens(state_dir, clock)
    token = jar.mint()

    for _ in range(JOIN_TOKEN_ATTEMPTS):
        reason, entry = jar.present(
            "guess" * 6, name="host-b", url=URL, cert_pem=PEER_PEM, roster=lead
        )
        assert entry is None
    assert reason == "exhausted"

    # the real token died with the budget: no offline guessing at leisure
    reason, _ = jar.present(token, name="host-b", url=URL, cert_pem=PEER_PEM, roster=lead)
    assert reason in {"no-token", "exhausted"}


def test_bad_payload_does_not_burn_the_token(
    state_dir: Path, clock: Clock, lead: CrewRoster
) -> None:
    jar = tokens(state_dir, clock)
    token = jar.mint()

    reason, _ = jar.present(token, name="host-b", url="http://insecure", cert_pem=PEER_PEM, roster=lead)
    assert reason == "bad-url"
    # the operator fixes the typo and the SAME token still works
    reason2, _ = jar.present(token, name="host-b", url=URL, cert_pem=PEER_PEM, roster=lead)
    assert reason2 == "enrolled"


def test_present_requires_a_lead(state_dir: Path, clock: Clock) -> None:
    roster = CrewRoster.load(state_dir, now=clock)  # still solo
    jar = tokens(state_dir, clock)
    token = jar.mint()
    reason, _ = jar.present(token, name="host-b", url=URL, cert_pem=PEER_PEM, roster=roster)
    assert reason == "not-lead"
