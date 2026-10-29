"""Crew enrolment: the join token (doc 10, task S7).

A lead mints one short-lived token and hands it to the operator, who
carries it to the candidate host. The candidate presents the token
plus its own name, crew URL and certificate PEM; the lead checks the
token, records the peer with the certificate's pin, and the token
dies.

The token discipline is the one ``pairing.py`` proved for pairing
codes, unchanged in spirit and mechanics:

- only the SHA-256 of the token is stored, in a 0600 file written
  atomically;
- one use: a successful enrolment consumes it, and a replay enrolls
  nobody;
- a TTL (10 minutes): an expired token is destroyed, not parked;
- a guess budget: a handful of wrong presentations burns the token,
  so it cannot be brute-forced at leisure;
- a REFUSED payload (bad name, http URL, unusable PEM) does not burn
  the token — the operator fixes the typo and presents again.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Optional

from ..pairing import (
    ensure_private_dir,
    generate_token,
    hashes_equal,
    sha256_hex,
)
from .roster import CrewRoster, ROLE_LEAD, write_private_atomic

#: A join token lives ten minutes — enough to walk it between hosts,
#: short enough that a leaked token is a small window.
JOIN_TOKEN_TTL_S = 600.0

#: Wrong-token presentations before the token is destroyed.
JOIN_TOKEN_ATTEMPTS = 5

TOKEN_FILENAME = "crew-join-token.json"


def new_token_state(now: float, ttl_s: float = JOIN_TOKEN_TTL_S) -> dict:
    return {"expiresAt": now + ttl_s, "attemptsLeft": JOIN_TOKEN_ATTEMPTS}


def check_join_token(
    state: Optional[dict], presented: object, now: float
) -> tuple[str, Optional[dict]]:
    """The whole token check as one pure decision (pairing.check_claim
    discipline): ``("ok", None)`` or ``(reason, state-to-persist)``
    where a None state means "delete the token file". No disk here.

    An expired or exhausted token is destroyed rather than left to be
    guessed at leisure; the last wrong attempt destroys it too.
    """
    if state is None:
        return "no-token", None
    if now >= state["expiresAt"]:
        return "expired", None
    if state["attemptsLeft"] <= 0:
        return "exhausted", None
    candidate = presented if isinstance(presented, str) else ""
    if hashes_equal(state["tokenHash"], sha256_hex(candidate)):
        return "ok", None
    attempts_left = state["attemptsLeft"] - 1
    if attempts_left <= 0:
        return "exhausted", None
    return "bad-token", {**state, "attemptsLeft": attempts_left}


class JoinTokens:
    """The lead's token jar over ``<state_dir>/crew-join-token.json``.

    ``mint`` replaces any live token (only the newest is presentable);
    ``present`` is the enrolment decision: token check first, payload
    validation second (refusals there never burn the token), and
    consumption only on a fully successful enrolment.
    """

    def __init__(
        self,
        state_dir,
        now: Optional[Callable[[], float]] = None,
        random: Callable[[int], bytes] = None,
    ):
        self.state_dir = Path(state_dir)
        self._now = now or time.time
        self._random = random  # None → pairing.generate_token's default

    @property
    def path(self) -> Path:
        return self.state_dir / TOKEN_FILENAME

    def mint(self, ttl_s: float = JOIN_TOKEN_TTL_S) -> str:
        """Mint a fresh join token, replacing any previous one. The
        token exists in the clear exactly here and in the one line the
        CLI prints for the operator to carry."""
        ensure_private_dir(self.state_dir)
        if self._random is not None:
            token = generate_token(random=self._random)
        else:
            token = generate_token()
        state = new_token_state(self._now(), ttl_s)
        state["tokenHash"] = sha256_hex(token)
        write_private_atomic(self.path, json.dumps(state, indent=2) + "\n")
        return token

    def _read(self) -> Optional[dict]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict) or not isinstance(raw.get("tokenHash"), str):
            return None
        if not isinstance(raw.get("expiresAt"), (int, float)) or isinstance(raw.get("expiresAt"), bool):
            return None
        if not isinstance(raw.get("attemptsLeft"), int) or isinstance(raw.get("attemptsLeft"), bool):
            return None
        return {
            "tokenHash": raw["tokenHash"],
            "expiresAt": float(raw["expiresAt"]),
            "attemptsLeft": int(raw["attemptsLeft"]),
        }

    def _consume(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass

    def present(
        self,
        token: str,
        *,
        name: str,
        url: str,
        cert_pem: str,
        roster: CrewRoster,
    ) -> tuple[str, Optional[dict]]:
        """Try to enrol one member. Returns ``(reason, entry)``:
        ``("enrolled", entry)`` on success; any other reason carries
        None and enrols nobody.

        Reasons: ``no-token`` / ``expired`` / ``exhausted`` /
        ``bad-token`` (token side, all persisted per the budget),
        ``bad-name`` / ``bad-url`` / ``bad-cert`` (payload side, the
        token is untouched), ``not-lead`` (this roster does not lead a
        crew).
        """
        from .roster import normalize_name
        from .tls import CrewTlsError, pin_of_pem

        # 1. the token, under the guess budget
        reason, to_persist = check_join_token(self._read(), token, self._now())
        if reason != "ok":
            if to_persist is not None:
                write_private_atomic(self.path, json.dumps(to_persist, indent=2) + "\n")
            else:
                self._consume()
            return reason, None

        # 2. the payload; a refusal here never burns the token
        if normalize_name(name) is None:
            return "bad-name", None
        if not isinstance(url, str) or not url.strip().startswith("https://"):
            return "bad-url", None
        try:
            pin_of_pem(cert_pem)
        except CrewTlsError:
            return "bad-cert", None
        if roster.role != ROLE_LEAD:
            return "not-lead", None

        # 3. consume first, enrol second: a replayed token can never
        #    race a second enrolment through this jar
        self._consume()
        entry = roster.record_peer(name, url, cert_pem, now=self._now())
        roster.persist()
        return "enrolled", entry
