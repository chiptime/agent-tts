"""The crew roster: this host's federation identity, persisted.

Federation (doc 10) is explicit and operator-driven. A brain starts
``solo`` — no crew, no peers, no bytes of overhead anywhere. ``init``
on one host makes it the ``lead``; every other member joins as a
``peer`` holding the lead's entry. The single file ``crew.json`` under
the state dir carries the whole truth:

- the role (``solo`` | ``lead`` | ``peer``) and this host's name;
- the enrolled peers: ``{name, url, cert_sha256_pin, added_at}``;
- the crew secret as a SHA-256 hash ONLY — the secret itself is shown
  once at enrolment and never written anywhere;
- the previous secret's hash plus a deadline, so a rotation keeps one
  old secret valid for a bounded grace window while the operator
  walks the new secret across the fleet.

Persistence discipline is the one pairing.py proved (doc 01): the file
is written through a unique 0600 temp file + ``os.replace``, the state
dir is 0700, and a CORRUPT file fails CLOSED — ``load`` raises
``CrewRosterCorrupt`` rather than silently resetting to solo, because a
silent reset would un-pin every peer and hand a stranger the crew.

Promotion is MANUAL and stays manual: ``promote`` rewrites roles
explicitly on the host that runs it. Nothing in this module watches,
elects, or takes over anything on its own.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import socket
import threading
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from ..pairing import (
    ensure_private_dir,
    generate_token,
    hashes_equal,
    sha256_hex,
)
from .tls import pem_to_der, pin_of_pem

#: The one roster file, directly under the state dir (task S7 shape).
CREW_FILENAME = "crew.json"

#: Where enrolled members' certificate PEMs live (public material,
#: private directory) so the client can build pinned contexts.
CERTS_DIRNAME = "crew-certs"

ROLE_SOLO = "solo"
ROLE_LEAD = "lead"
ROLE_PEER = "peer"
ROLES = (ROLE_SOLO, ROLE_LEAD, ROLE_PEER)

#: Member names double as routing hosts (``<host>:<pane>``), so they
#: read like hostnames: lowercase, start alnum, dashes inside.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

#: How long the previous crew secret stays valid after a rotation.
ROTATE_GRACE_S = 600.0

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


class CrewStateError(Exception):
    """A crew operation the operator must fix — never a crash."""


class CrewRosterCorrupt(CrewStateError):
    """The roster file exists but is not a trustworthy roster.

    Fail closed: the caller refuses to authenticate anyone or mutate
    the state until the operator looks at the file. This is never a
    reset to solo — a reset would un-pin every peer (doc 10 risk 1).
    """


def normalize_name(raw: object) -> Optional[str]:
    """A usable member name, or None. Names are routing keys, so the
    bar is the hostname bar: lowercase, no spaces, bounded length."""
    if not isinstance(raw, str):
        return None
    name = raw.strip().lower()
    if not NAME_RE.match(name):
        return None
    return name


def default_self_name() -> str:
    """This host's own name, from the machine, fallen back to a safe
    constant the NAME_RE always accepts."""
    raw = (socket.gethostname() or "").strip().lower()
    name = normalize_name(raw)
    return name or "brain"


def write_private_atomic(path: Path, text: str) -> None:
    """Atomic, owner-only file write — the pairing.py discipline:

    a unique temp name (pid + random) created 0600 with O_EXCL, the
    bytes, then ``os.replace``. Readers never see a half-written file,
    two writers never collide, and a failure removes only its own temp.
    """
    ensure_private_dir(path.parent)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(str(tmp), str(path))
    except BaseException:
        try:
            os.unlink(str(tmp))
        except OSError:
            pass
        raise


def _https_url(raw: object) -> Optional[str]:
    """A peer address usable for the crew link: https only. The crew
    link is mTLS-pinned by design; a plain-http URL would silently
    drop that factor, so it is refused at the door."""
    if not isinstance(raw, str):
        return None
    parsed = urlparse(raw.strip())
    if parsed.scheme != "https" or not parsed.netloc:
        return None
    return raw.strip()


def _pin(raw: object) -> Optional[str]:
    if not isinstance(raw, str) or not _HEX64_RE.match(raw):
        return None
    return raw


class CrewRoster:
    """The server's and the CLI's view of ``<state_dir>/crew.json``.

    Mutating verbs change the in-memory roster only; ``persist()``
    lands the file atomically. Loading is strict: anything malformed
    raises ``CrewRosterCorrupt`` so every consumer fails closed.
    """

    def __init__(self, state_dir, now: Optional[Callable[[], float]] = None):
        import time

        self.state_dir = Path(state_dir) if state_dir is not None else None
        self._now = now or time.time
        self._lock = threading.RLock()

        self.role: str = ROLE_SOLO
        self.self_name: Optional[str] = None
        self.lead_name: Optional[str] = None
        self.peers: dict[str, dict] = {}
        self.secret_hash: Optional[str] = None
        self.previous_secret_hash: Optional[str] = None
        self.previous_secret_deadline: float = 0.0

    # -- loading ---------------------------------------------------------

    @property
    def path(self) -> Optional[Path]:
        if self.state_dir is None:
            return None
        return self.state_dir / CREW_FILENAME

    @classmethod
    def load(
        cls, state_dir, now: Optional[Callable[[], float]] = None
    ) -> "CrewRoster":
        """The roster as it is on disk. An ABSENT file is a fresh solo
        brain; a present but malformed one raises (fail closed)."""
        roster = cls(state_dir, now=now)
        path = roster.path
        if path is None or not path.exists():
            return roster
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CrewRosterCorrupt(f"crew roster unreadable: {path}: {exc}") from exc
        roster._adopt(raw, path)
        return roster

    def _adopt(self, raw: object, path: Path) -> None:
        """Validate untrusted parsed state into this roster, or fail
        closed. Every field is checked; nothing is guessed."""
        if not isinstance(raw, dict):
            raise CrewRosterCorrupt(f"crew roster not an object: {path}")
        role = raw.get("role")
        if role not in ROLES:
            raise CrewRosterCorrupt(f"crew roster role invalid: {path}")

        self_name = raw.get("self")
        if self_name is not None and normalize_name(self_name) is None:
            raise CrewRosterCorrupt(f"crew roster self name invalid: {path}")
        lead_name = raw.get("lead")
        if lead_name is not None and normalize_name(lead_name) is None:
            raise CrewRosterCorrupt(f"crew roster lead name invalid: {path}")

        peers_raw = raw.get("peers")
        if not isinstance(peers_raw, dict):
            raise CrewRosterCorrupt(f"crew roster peers invalid: {path}")
        peers: dict[str, dict] = {}
        for name, entry in peers_raw.items():
            if normalize_name(name) is None or not isinstance(entry, dict):
                raise CrewRosterCorrupt(f"crew roster peer invalid: {path}")
            url = _https_url(entry.get("url"))
            pin = _pin(entry.get("cert_sha256_pin"))
            added_at = entry.get("added_at")
            if url is None or pin is None or not isinstance(added_at, (int, float)) \
                    or isinstance(added_at, bool):
                raise CrewRosterCorrupt(f"crew roster peer entry invalid: {path}")
            peers[name] = {
                "url": url,
                "cert_sha256_pin": pin,
                "added_at": float(added_at),
            }

        secret_hash = raw.get("secret_hash")
        if secret_hash is not None and _pin(secret_hash) is None:
            raise CrewRosterCorrupt(f"crew roster secret hash invalid: {path}")
        previous_hash = raw.get("previous_secret_hash")
        if previous_hash is not None and _pin(previous_hash) is None:
            raise CrewRosterCorrupt(f"crew roster previous hash invalid: {path}")
        deadline = raw.get("previous_secret_deadline", 0.0)
        if not isinstance(deadline, (int, float)) or isinstance(deadline, bool):
            raise CrewRosterCorrupt(f"crew roster grace deadline invalid: {path}")
        if role == ROLE_SOLO and (secret_hash is not None or peers):
            raise CrewRosterCorrupt(f"crew roster solo state carries a crew: {path}")

        self.role = role
        self.self_name = self_name
        self.lead_name = lead_name
        self.peers = peers
        self.secret_hash = secret_hash
        self.previous_secret_hash = previous_hash
        self.previous_secret_deadline = float(deadline)

    # -- persistence -------------------------------------------------------

    def persist(self) -> None:
        """Write the roster atomically, 0600, under a 0700 dir."""
        if self.path is None:
            raise CrewStateError("roster has no state dir to persist to")
        payload = {
            "version": 1,
            "role": self.role,
            "self": self.self_name,
            "lead": self.lead_name,
            "peers": self.peers,
            "secret_hash": self.secret_hash,
            "previous_secret_hash": self.previous_secret_hash,
            "previous_secret_deadline": self.previous_secret_deadline,
        }
        with self._lock:
            write_private_atomic(self.path, json.dumps(payload, indent=2) + "\n")

    # -- predicates ----------------------------------------------------------

    @property
    def has_peers(self) -> bool:
        return bool(self.peers)

    @property
    def has_crew(self) -> bool:
        return self.role != ROLE_SOLO

    def peer(self, name: str) -> Optional[dict]:
        return self.peers.get(name)

    def peer_cert_pem(self, name: str) -> Optional[str]:
        """An enrolled member's certificate PEM (kept under the certs
        dir at enrolment), or None — the client pins from this."""
        if self.state_dir is None or name not in self.peers:
            return None
        cert_path = self.state_dir / CERTS_DIRNAME / f"{name}.pem"
        try:
            return cert_path.read_text(encoding="utf-8")
        except OSError:
            return None

    # -- the crew secret -----------------------------------------------------

    def secret_matches(self, secret: object, now: Optional[float] = None) -> bool:
        """Whether ``secret`` is the crew secret — the current one, or
        the previous one inside the rotation grace window.

        Every candidate is compared in constant time and BOTH are
        always evaluated: the answer never leaks which (if either)
        factor-era the caller presented.
        """
        if not isinstance(secret, str) or not secret:
            return False
        if not self.secret_hash:
            return False
        moment = self._now() if now is None else now
        current = hashes_equal(self.secret_hash, sha256_hex(secret))
        previous = False
        if self.previous_secret_hash and moment < self.previous_secret_deadline:
            previous = hashes_equal(self.previous_secret_hash, sha256_hex(secret))
        return bool(current or previous)

    # -- mutating verbs ------------------------------------------------------

    def init_lead(self, name: str, secret: str, now: Optional[float] = None) -> None:
        """solo → lead: name this host and mint the crew secret."""
        if self.role != ROLE_SOLO:
            raise CrewStateError("already in a crew (leave or promote instead)")
        member = normalize_name(name)
        if member is None:
            raise CrewStateError(f"not a usable crew member name: {name!r}")
        if not isinstance(secret, str) or len(secret) < 8:
            raise CrewStateError("crew secret must be a string of at least 8 chars")
        self.self_name = member
        self.role = ROLE_LEAD
        self.lead_name = member
        self.secret_hash = sha256_hex(secret)
        self.previous_secret_hash = None
        self.previous_secret_deadline = 0.0

    def join_as_peer(
        self,
        self_name: str,
        lead_name: str,
        lead_url: str,
        lead_cert_pem: str,
        secret: str,
        now: Optional[float] = None,
    ) -> None:
        """solo → peer, recording the lead this host follows.

        The lead's member NAME is explicit — the operator typed it on
        both hosts, and a name guessed out of a certificate would be a
        name nobody confirmed. The lead's certificate is pinned, which
        is the part that must not be guessed.
        """
        if self.role != ROLE_SOLO:
            raise CrewStateError("already in a crew (leave or promote instead)")
        member = normalize_name(self_name)
        if member is None:
            raise CrewStateError(f"not a usable crew member name: {self_name!r}")
        lead = normalize_name(lead_name)
        if lead is None:
            raise CrewStateError(f"not a usable crew member name: {lead_name!r}")
        url = _https_url(lead_url)
        if url is None:
            raise CrewStateError(f"lead url must be https: {lead_url!r}")
        if not isinstance(secret, str) or not secret:
            raise CrewStateError("crew secret is required at join")
        moment = self._now() if now is None else now
        self.self_name = member
        self.role = ROLE_PEER
        self.lead_name = lead
        self._record_member(lead, url, lead_cert_pem, moment)
        self.secret_hash = sha256_hex(secret)

    def record_peer(
        self, name: str, url: str, cert_pem: str, now: Optional[float] = None
    ) -> dict:
        """Enrol a member: pin its certificate, keep its PEM for the
        pinned client contexts. Re-enrolment (same name) replaces the
        pin and address — a regenerated certificate is a new identity
        for the same member, recorded explicitly."""
        member = normalize_name(name)
        if member is None:
            raise CrewStateError(f"not a usable crew member name: {name!r}")
        clean_url = _https_url(url)
        if clean_url is None:
            raise CrewStateError(f"crew peer url must be https: {url!r}")
        moment = self._now() if now is None else now
        return self._record_member(member, clean_url, cert_pem, moment)

    def _record_member(self, member: str, url: str, cert_pem: str, moment: float) -> dict:
        if not isinstance(cert_pem, str):
            raise CrewStateError("certificate PEM is required")
        try:
            pin = pin_of_pem(cert_pem)
        except Exception as exc:  # any PEM failure is an enrolment refusal
            raise CrewStateError(f"not a usable certificate PEM: {exc}") from exc
        entry = {
            "url": url,
            "cert_sha256_pin": pin,
            "added_at": self.peers.get(member, {}).get("added_at", moment),
        }
        self.peers[member] = entry
        self._save_member_cert(member, cert_pem)
        return entry

    def _save_member_cert(self, member: str, cert_pem: str) -> None:
        if self.state_dir is None:
            return
        certs_dir = self.state_dir / CERTS_DIRNAME
        ensure_private_dir(certs_dir)
        write_private_atomic(certs_dir / f"{member}.pem", cert_pem)

    def remove(self, name: str) -> bool:
        """Drop a peer by name, leave the crew entirely (reset to solo)
        when the name is this host — or, on a peer, the lead it
        follows: a peer without its lead is not a crew, and an orphan
        half-roster is a state nobody operator-declared."""
        if normalize_name(name) is None:
            return False
        leaving = (
            self.self_name is not None and name == self.self_name
        ) or (
            self.role == ROLE_PEER and name == self.lead_name
        )
        if leaving:
            self.role = ROLE_SOLO
            self.self_name = None
            self.lead_name = None
            self.peers = {}
            self.secret_hash = None
            self.previous_secret_hash = None
            self.previous_secret_deadline = 0.0
            return True
        if name in self.peers:
            del self.peers[name]
            return True
        return False

    def rotate_secret(
        self, new_secret: str, now: Optional[float] = None, grace_s: float = ROTATE_GRACE_S
    ) -> None:
        """Replace the crew secret, keeping the previous one valid for
        ``grace_s`` seconds so the operator can walk the new secret
        across every member without splitting the crew."""
        if not isinstance(new_secret, str) or len(new_secret) < 8:
            raise CrewStateError("crew secret must be a string of at least 8 chars")
        if not self.secret_hash:
            raise CrewStateError("no crew secret to rotate (init or join first)")
        moment = self._now() if now is None else now
        self.previous_secret_hash = self.secret_hash
        self.previous_secret_deadline = moment + grace_s
        self.secret_hash = sha256_hex(new_secret)

    def promote(self, new_lead: str) -> None:
        """MANUAL promotion: rewrite the roles around ``new_lead``.

        The host that runs this declares the new lead explicitly. If
        the new lead is this host, this host becomes the lead; any
        other known member demotes this host to a plain peer. There is
        no automatic path here — no election, no timeout, no takeover:
        promotion happens when an operator types it, and nowhere else.
        """
        member = normalize_name(new_lead)
        if member is None:
            raise CrewStateError(f"not a usable crew member name: {new_lead!r}")
        if self.self_name is None:
            raise CrewStateError("not in a crew (init or join first)")
        known = {self.self_name, *self.peers.keys()}
        if member not in known:
            raise CrewStateError(f"unknown crew member: {new_lead!r}")
        self.lead_name = member
        self.role = ROLE_LEAD if member == self.self_name else ROLE_PEER


def mint_crew_secret(random=None) -> str:
    """A fresh crew secret, base64url — shown once, stored hashed."""
    if random is not None:
        return generate_token(random=random)
    return generate_token()
