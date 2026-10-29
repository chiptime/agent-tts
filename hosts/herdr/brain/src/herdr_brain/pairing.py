"""Device pairing: a write credential the phone itself holds.

The brain's surface is driven from a phone — a PWA that can ask the
server to type into a live terminal. A write of that weight needs a
credential the DEVICE owns, not one the network asserts: a tailnet
name says where a request arrived from, never which pocket it left.
This module is that credential's whole lifecycle.

Enrolment is a bootstrap problem — the phone asking to be paired is
the one party that cannot yet prove anything — so it runs out of
band. The operator's terminal mints a one-time code
(``bin/herdr-brain pair``) and prints it; only its SHA-256 is written
under ``<HERDR_BRAIN_STATE_DIR>/pairing-pending.json``. The phone
posts ``{code,label}`` to ``POST /pair``, which mints a 256-bit
token, stores only ITS SHA-256 in ``paired-devices.json`` and returns
the token exactly once. The write requirement turns on with the first
pairing and turns off again only for an absent registry or a
well-formed zero-device one — a corrupt file fails CLOSED, enforcement
on with nobody authorized (doc 01 AC10).

Times are epoch seconds throughout. JSON keys are camelCase — the
on-disk shape these state files already carry; a different spelling
would orphan every registry an existing deployment has written.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import stat
import threading
from pathlib import Path
from typing import Callable, Optional

# File under the state dir holding the pending (unclaimed) pairing code.
PENDING_FILENAME = "pairing-pending.json"
# File under the state dir holding the paired-device registry.
DEVICES_FILENAME = "paired-devices.json"

# How long a minted code stays claimable (10 minutes).
CODE_TTL_S = 10 * 60
# Wrong-code attempts before the pending pairing is destroyed.
CODE_ATTEMPTS = 5
# Codes are this many characters.
CODE_LENGTH = 8
# The code alphabet with every glyph pair a human mistypes removed: no
# 0/O, no 1/I/L, no U/V. The operator reads this off a terminal and
# types it on a phone keyboard.
CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTWXYZ"

# At most one lastSeenAt write per device per this interval — a poll
# every 1.5s must not become a write.
SEEN_THROTTLE_S = 60.0

# The /pair claim budget (S11): at most this many claim attempts per
# client address per window, then 429 with a Retry-After. Refused
# attempts are not recorded, so a flood of 429s cannot push a client's
# recovery further out.
PAIR_RATE_ATTEMPTS = 10
PAIR_RATE_WINDOW_S = 60.0

_TOKEN_BYTES = 32  # 256-bit bearer tokens.

DEFAULT_STATE_DIR = "~/.local/state/herdr-brain"

# Duration units `pair --expires` / `devices set-expiry` accept. One
# value, one unit — a compound ("1h30m") or bare number is refused
# rather than guessed.
_DURATION_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)([smhdw])$")


def default_state_dir() -> Path:
    """The documented default state dir (coherent with DEFAULT_AUDIO_DIR)."""
    return Path(DEFAULT_STATE_DIR).expanduser()


_DURATION_UNITS_S = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0, "w": 604800.0}


def parse_duration(raw: object) -> Optional[float]:
    """``"30d"`` → 2592000.0; None for anything the operator did not
    spell as one positive value plus a unit (s/m/h/d/w). A bare number
    or a compound is refused, not guessed — an expiry is a security
    knob and "30" meaning seconds-or-days is how it goes wrong."""
    if not isinstance(raw, str):
        return None
    match = _DURATION_RE.match(raw.strip())
    if match is None:
        return None
    seconds = float(match.group(1)) * _DURATION_UNITS_S[match.group(2)]
    if seconds <= 0 or seconds != seconds or seconds in (float("inf"),):
        return None
    return seconds


def ensure_private_dir(path: Path) -> None:
    """0700 for the state dir, even when something else created it first.

    mkdir's mode only applies at creation: audio storage nested under
    the state dir (``audio_dir=<state>/audio``) can create the directory
    with the default umask, and a surviving group-writable mode would
    leave credential hashes and the audit trail readable beyond the
    operator (T4 B5). An existing directory owned by the current user is
    retightened with chmod; one owned by somebody else is left alone —
    the write that follows fails on its own with a clearer error than a
    chmod we are not entitled to would give.
    """
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        info = path.stat()
    except OSError:
        return
    if info.st_uid != os.geteuid():
        return
    if stat.S_IMODE(info.st_mode) != 0o700:
        os.chmod(path, 0o700)


# ── pure helpers ─────────────────────────────────────────────────────────────


def sha256_hex(value: str) -> str:
    """SHA-256 as lowercase hex. Used for codes and tokens alike — never
    store either in the clear."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hashes_equal(a: str, b: str) -> bool:
    """Compare two hex digests without leaking where they diverge. A
    length mismatch (a corrupted file) is a plain False, never a raise."""
    if not isinstance(a, str) or not isinstance(b, str) or len(a) != len(b):
        return False
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def normalize_code(raw: str) -> str:
    """Uppercase and strip everything outside the alphabet, so the
    operator may type the code with spaces, dashes or lower case.
    Deliberately NOT a spelling-correction pass: the alphabet has no
    confusable pairs left, so anything outside it is a typo the attempt
    counter should see."""
    if not isinstance(raw, str):
        return ""
    stripped = re.sub(r"[^0-9A-Z]", "", raw.upper())
    return "".join(ch for ch in stripped if ch in CODE_ALPHABET)


def generate_code(random: Callable[[int], bytes] = secrets.token_bytes) -> str:
    """Mint a fresh code. ``random`` is injected for deterministic tests."""
    return "".join(
        CODE_ALPHABET[b % len(CODE_ALPHABET)] for b in random(CODE_LENGTH)
    )


def generate_token(random: Callable[[int], bytes] = secrets.token_bytes) -> str:
    """Mint a 256-bit bearer token, base64url. The only time the token
    exists in the clear is here and in the one /pair response."""
    import base64

    raw = random(_TOKEN_BYTES)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def new_pending(code: str, now: float, ttl_s: float = CODE_TTL_S,
                label: Optional[str] = None,
                expires_in_s: Optional[float] = None) -> dict:
    """A fresh pending pairing for ``code``, expiring ``ttl_s`` from ``now``.

    ``label`` (optional) pins the device label the claim will use, so
    the operator — not the phone — chooses the revoke handle.
    ``expires_in_s`` (optional) is the TOKEN expiry the enrolled device
    will carry; it rides the pending because the credential is born at
    the claim, and the deadline must run from there, not from the mint."""
    pending = {
        "codeHash": sha256_hex(normalize_code(code)),
        "expiresAt": now + ttl_s,
        "attemptsLeft": CODE_ATTEMPTS,
    }
    if label is not None:
        pending["label"] = label
    if expires_in_s is not None:
        pending["expiresInS"] = expires_in_s
    return pending


def coerce_pending(raw: object) -> Optional[dict]:
    """Coerce an untrusted parsed value into a pending pairing, or None.
    An optional pinned label survives only when it is a usable string;
    an optional token-expiry duration survives only when usable."""
    if not isinstance(raw, dict):
        return None
    code_hash = raw.get("codeHash")
    expires_at = raw.get("expiresAt")
    attempts_left = raw.get("attemptsLeft")
    if not isinstance(code_hash, str) or not code_hash:
        return None
    if not isinstance(expires_at, (int, float)) or isinstance(expires_at, bool):
        return None
    if not isinstance(attempts_left, (int, float)) or isinstance(attempts_left, bool):
        return None
    pending = {
        "codeHash": code_hash,
        "expiresAt": float(expires_at),
        "attemptsLeft": int(attempts_left),
    }
    label = normalize_label(raw.get("label"))
    if label is not None:
        pending["label"] = label
    expires_in = raw.get("expiresInS")
    if isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool) \
            and expires_in > 0:
        pending["expiresInS"] = float(expires_in)
    return pending


def coerce_registry(raw: object) -> dict:
    """Coerce an untrusted parsed value into a registry, dropping any
    entry that isn't a complete device. A half-written or hand-edited
    file degrades to "fewer paired devices", never to a device with an
    empty tokenHash — which would otherwise authorise a caller whose
    token hashes to "". An ``expiresAt`` that is present but not a
    usable timestamp drops the entry too (S11): "forever" hand-edited
    into the file must not widen into a credential that never runs out;
    an ABSENT key is the F1 shape and simply never expires."""
    if not isinstance(raw, dict):
        return {"devices": []}
    entries = raw.get("devices")
    if not isinstance(entries, list):
        return {"devices": []}
    devices: list = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        label = entry.get("label")
        token_hash = entry.get("tokenHash")
        if not isinstance(label, str) or label.strip() == "":
            continue
        if not isinstance(token_hash, str) or len(token_hash) != 64:
            continue
        if any(d["label"] == label for d in devices):
            continue
        created = entry.get("createdAt")
        seen = entry.get("lastSeenAt")
        device = {
            "label": label,
            "tokenHash": token_hash,
            "createdAt": created if isinstance(created, (int, float)) and not isinstance(created, bool) else 0,
            "lastSeenAt": seen if isinstance(seen, (int, float)) and not isinstance(seen, bool) else 0,
        }
        expires_at = entry.get("expiresAt")
        if expires_at is not None:
            if not isinstance(expires_at, (int, float)) or isinstance(expires_at, bool) \
                    or not expires_at > 0:
                continue
            device["expiresAt"] = float(expires_at)
        devices.append(device)
    return {"devices": devices}


def enforced_from_registry(raw: object) -> bool:
    """Whether this PARSED registry content keeps the bearer requirement
    on. Fail closed (doc 01 AC10, T4 B1): only a WELL-FORMED registry
    with zero devices — the file revoking the last device writes —
    reads as "not enforced", and so does an absent file (handled by the
    caller). An unparsable/truncated file, a non-registry shape, or a
    registry whose entries coercion drops keeps the requirement ON with
    NO device authorized: coercing corruption down to "empty" would
    silently reopen every write the operator had armed. A registry with
    at least one kept device is the normal enforced state."""
    if not isinstance(raw, dict):
        return True
    devices = raw.get("devices")
    if not isinstance(devices, list):
        return True
    return bool(devices)


def normalize_label(raw: object) -> Optional[str]:
    """Validate and bound a client-supplied label. None when unusable —
    the same answer for "empty" and "1 KB of newlines", because the
    label is echoed into the UI (and, in T3, the audit log)."""
    if not isinstance(raw, str):
        return None
    label = re.sub(r"[\r\n\t]+", " ", raw).strip()
    if not label or len(label) > 48:
        return None
    return label


# Why a claim failed. expired/exhausted mean the pending pairing is gone.
CLAIM_NO_PENDING = "no-pending"
CLAIM_EXPIRED = "expired"
CLAIM_EXHAUSTED = "exhausted"
CLAIM_BAD_CODE = "bad-code"
CLAIM_DUPLICATE_LABEL = "duplicate-label"


def check_claim(pending: Optional[dict], code: str, now: float):
    """The whole of the code check as one pure decision: ``(ok, None)``
    or ``(reason, pending-to-persist)`` where a None pending means
    "delete the file". Nothing here touches a disk.

    An expired code is destroyed rather than left to be guessed at
    leisure, and the last wrong attempt destroys it too — so a code is
    at most 5 guesses out of 29^8, for 10 minutes."""
    if pending is None:
        return CLAIM_NO_PENDING, None
    if now >= pending["expiresAt"]:
        return CLAIM_EXPIRED, None
    if pending["attemptsLeft"] <= 0:
        return CLAIM_EXHAUSTED, None
    if hashes_equal(pending["codeHash"], sha256_hex(normalize_code(code))):
        return "ok", None
    attempts_left = pending["attemptsLeft"] - 1
    if attempts_left <= 0:
        return CLAIM_EXHAUSTED, None
    return CLAIM_BAD_CODE, {**pending, "attemptsLeft": attempts_left}


def find_by_token(registry: dict, token: Optional[str]) -> Optional[dict]:
    """The device whose token hash matches, or None. Every entry is
    compared in constant time; the list position is not a secret (labels
    are readable) and is unrelated to the token bytes."""
    if not token:
        return None
    digest = sha256_hex(token)
    found: Optional[dict] = None
    for device in registry["devices"]:
        if hashes_equal(device["tokenHash"], digest):
            found = device
    return found


def device_expired(device: dict, now: float) -> bool:
    """Whether the device's optional token deadline has passed. Absent
    (the F1 shape) means the credential never runs out."""
    expires_at = device.get("expiresAt")
    return expires_at is not None and now >= expires_at


def add_device(registry: dict, device: dict) -> Optional[dict]:
    """Add a device, or None when the label is already taken (labels are
    the revoke handle)."""
    if any(d["label"] == device["label"] for d in registry["devices"]):
        return None
    entry = {
        "label": device["label"],
        "tokenHash": device["tokenHash"],
        "createdAt": device["now"],
        "lastSeenAt": device["now"],
    }
    if device.get("expiresAt") is not None:
        entry["expiresAt"] = device["expiresAt"]
    return {"devices": [*registry["devices"], entry]}


def remove_device(registry: dict, label: str) -> Optional[dict]:
    """Drop a device by label. None when there was no such device (so a
    caller can 404)."""
    if not any(d["label"] == label for d in registry["devices"]):
        return None
    return {"devices": [d for d in registry["devices"] if d["label"] != label]}


def touch_device(registry: dict, label: str, now: float,
                 throttle_s: float = SEEN_THROTTLE_S) -> Optional[dict]:
    """Stamp lastSeenAt, or None when the previous stamp is recent
    enough that a write would be noise."""
    device = next((d for d in registry["devices"] if d["label"] == label), None)
    if device is None:
        return None
    if now - device["lastSeenAt"] < throttle_s:
        return None
    return {
        "devices": [
            {**d, "lastSeenAt": now} if d["label"] == label else d
            for d in registry["devices"]
        ],
    }


def to_device_wire(registry: dict, current: Optional[str]) -> list:
    """The wire shape of the registry for GET /devices — the token
    hashes never leave this module. ``expiresAt`` is None for the F1
    shape (never expires)."""
    return [
        {
            "label": d["label"],
            "createdAt": d["createdAt"],
            "lastSeenAt": d["lastSeenAt"],
            "expiresAt": d.get("expiresAt"),
            "current": d["label"] == current,
        }
        for d in registry["devices"]
    ]


def bearer_token(headers_get) -> Optional[str]:
    """The ``Authorization: Bearer <token>`` value, or None. Case-
    insensitive on the scheme (RFC 7235) and tolerant of extra spacing,
    because a proxy may rewrite whitespace. ``headers_get`` is a
    ``str -> Optional[str]`` lookup over the request's headers."""
    raw = headers_get("authorization")
    if not raw:
        return None
    match = re.match(r"^Bearer\s+(\S+)\s*$", raw.strip(), re.IGNORECASE)
    return match.group(1) if match else None


# ── the store ────────────────────────────────────────────────────────────────


class PairingStore:
    """The server's view of pairing over ``<state_dir>``: a synchronous
    gate for the request path (registry re-read per request, cached by
    mtime) plus the enrolment and revocation operations the endpoints
    and the CLI drive.

    "Enforced" is not a setting — it is "the registry says devices are
    paired". Pairing nobody (absent file) keeps the brain exactly as it
    was; pairing one device turns the requirement on for every device,
    the only ordering that cannot lock the operator out of their own
    brain halfway through. A CORRUPT registry keeps the requirement on
    with no device authorized (fail closed, doc 01 AC10) — and a claim
    rewrites a valid registry, so ``herdr-brain pair`` is always the
    way back in.
    """

    def __init__(self, state_dir, now: Callable[[], float] = None,
                 random: Callable[[int], bytes] = secrets.token_bytes):
        import time

        self.state_dir = Path(state_dir)
        self._now = now or time.time
        self._random = random
        # Reentrant: the read-modify-write verbs (claim, revoke, the
        # lastSeen stamp) hold it across their own _write_atomic calls.
        self._lock = threading.RLock()
        self._registry_cache: Optional[tuple[str, object]] = None

    # -- paths --

    @property
    def pending_path(self) -> Path:
        return self.state_dir / PENDING_FILENAME

    @property
    def registry_path(self) -> Path:
        return self.state_dir / DEVICES_FILENAME

    # -- atomic, owner-only writes --

    def _write_atomic(self, path: Path, text: str) -> None:
        """tempfile with a unique name + os.replace: readers never see a
        half-written file, and two writers never collide on one temp
        name (the pid plus a counter keeps them apart)."""
        ensure_private_dir(self.state_dir)
        tmp = path.with_name(
            f"{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
        )
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.replace(str(tmp), str(path))
            # os.replace lands a NEW inode, but this filesystem's mtime
            # granularity can leave a rapid same-size rewrite with an
            # identical mtime — drop the registry cache so our own
            # writes are always seen by the next read.
            with self._lock:
                self._registry_cache = None
        except BaseException:
            try:
                os.unlink(str(tmp))
            except OSError:
                pass
            raise

    @staticmethod
    def _read_json(path: Path):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    # -- the gate --

    def _load(self) -> tuple:
        """``(coerced registry, enforced)`` as of this instant: one stat
        per request, a re-parse only when the file identity (inode —
        every atomic replace lands a new one — plus mtime and size)
        changed, so a CLI revocation lands on the very next request
        without a restart. An absent file is the one non-enforced
        answer a corrupt file can never borrow (doc 01 AC10)."""
        try:
            st = self.registry_path.stat()
            key = f"{st.st_ino}:{st.st_mtime_ns}:{st.st_size}"
        except OSError:
            with self._lock:
                self._registry_cache = None
            return coerce_registry(None), False
        cached = self._registry_cache
        if cached is not None and cached[0] == key:
            raw = cached[1]
        else:
            raw = self._read_json(self.registry_path)
            with self._lock:
                self._registry_cache = (key, raw)
        return coerce_registry(raw), enforced_from_registry(raw)

    def registry(self) -> dict:
        """The registry as of this instant (see ``_load``). Corrupt
        entries are dropped here; whether dropping them lifts the write
        requirement is ``enforced()``'s separate, fail-closed answer."""
        return self._load()[0]

    def enforced(self) -> bool:
        """Whether a bearer token is required for writes. ON for any
        registry content except an ABSENT file or a well-formed
        zero-device registry (the post-revoke shape); a corrupt or
        truncated file stays ON with no device authorized — the
        requirement the operator armed cannot be corrupted away."""
        return self._load()[1]

    def resolve_status(self, token: Optional[str]) -> tuple:
        """The three-way answer the request path needs (S11):
        ``("ok", device)`` for a live credential, ``("expired", device)``
        for a token that matched but ran out (its own 403 text, and no
        lastSeen stamp — a refused device is not "seen"), ``("invalid",
        None)`` for everything else. On "ok" the lastSeenAt stamp runs,
        throttled and re-derived from disk exactly as ``resolve`` always
        did (see below)."""
        registry = self.registry()
        device = find_by_token(registry, token)
        if device is None:
            return "invalid", None
        if device_expired(device, self._now()):
            return "expired", device
        touched = touch_device(registry, device["label"], self._now())
        if touched is not None:
            try:
                with self._lock:
                    # RE-DERIVED FROM DISK, never written from the
                    # snapshot above: bin/herdr-brain devices revoke
                    # runs in ANOTHER process and writes this same
                    # file, so writing the snapshot blind would put a
                    # revoked device BACK — a stamp silently undoing a
                    # revocation, the one thing a credential store may
                    # not do.
                    current = coerce_registry(self._read_json(self.registry_path))
                    fresh = touch_device(current, device["label"], self._now())
                    if fresh is not None:
                        self._write_atomic(
                            self.registry_path,
                            json.dumps(fresh, ensure_ascii=False, indent=2) + "\n",
                        )
            except OSError as exc:
                # Fail-soft: the stamp is telemetry, never authority.
                import logging

                logging.getLogger("herdr_brain.pairing").warning(
                    "could not stamp lastSeenAt: %s", exc
                )
        return "ok", device

    def resolve(self, token: Optional[str]) -> Optional[dict]:
        """The device this request's bearer token belongs to, or None.
        The F1 contract unchanged: an expired token simply stops
        answering here too (the three-way split lives in
        ``resolve_status``)."""
        status, device = self.resolve_status(token)
        return device if status == "ok" else None

    def peek(self, token: Optional[str]) -> Optional[dict]:
        """The LIVE device this bearer belongs to, read-only: no
        lastSeenAt stamp, no registry write (B4 — the /pair bootstrap
        resolves a presented bearer only for audit attribution, and a
        refused bootstrap must leave the registry byte-identical).
        Unknown and expired tokens answer None, the same shape
        ``resolve`` keeps."""
        device = find_by_token(self.registry(), token)
        if device is None or device_expired(device, self._now()):
            return None
        return device

    # -- enrolment --

    def mint(self, label: Optional[str] = None,
             expires_in_s: Optional[float] = None) -> dict:
        """Mint a one-time code and persist only its hash. Returns the
        code for the terminal (the only copy that will ever exist) plus
        its expiry. A second mint overwrites the pending file, killing
        the previous code — the intended way to cancel one.

        A pinned label is normalized and validated HERE, at the source:
        an unusable one raises instead of silently pinning nothing (the
        claim would fall back to the phone's label, leaving the operator
        a revoke handle they never chose — T4 B4). ``expires_in_s``,
        when given, is the token deadline the claiming device will be
        enrolled with (S11) — validated for the same reason: an expiry
        that is zero or negative is no expiry, and pretending otherwise
        would hand out a credential the operator believes is bounded."""
        if label is not None:
            label = normalize_label(label)
            if label is None:
                raise ValueError("label must be 1-48 usable characters")
        if expires_in_s is not None and (not expires_in_s > 0):
            raise ValueError("expires must be a positive duration in seconds")
        now = self._now()
        code = generate_code(self._random)
        pending = new_pending(code, now, label=label, expires_in_s=expires_in_s)
        self._write_atomic(
            self.pending_path,
            json.dumps(pending, ensure_ascii=False, indent=2) + "\n",
        )
        minted = {"code": code, "expiresAt": pending["expiresAt"], "label": label}
        if expires_in_s is not None:
            minted["expiresInS"] = expires_in_s
        return minted

    def claim(self, code: str, label: str) -> tuple:
        """Claim the pending code and enrol ``label``. On success the
        token is returned ONCE — not stored, recoverable or
        re-derivable — and the pending pairing is destroyed, so a code
        is single-use even within its TTL. Returns ``("ok", {"token",
        "label"})`` or ``("fail", reason)``."""
        with self._lock:
            pending = coerce_pending(self._read_json(self.pending_path))
            reason, to_persist = check_claim(pending, code, self._now())
            if reason != "ok":
                if to_persist is None:
                    self._delete_pending()
                else:
                    self._write_atomic(
                        self.pending_path,
                        json.dumps(to_persist, ensure_ascii=False, indent=2) + "\n",
                    )
                return "fail", reason

            # An operator-pinned label is the revoke handle the operator
            # chose; otherwise the phone's (validated) label.
            effective = pending.get("label") if pending.get("label") else label
            token = generate_token(self._random)
            now = self._now()
            enrolment = {
                "label": effective,
                "tokenHash": sha256_hex(token),
                "now": now,
            }
            # The token deadline runs from the CLAIM — the moment the
            # credential is born — not from when the code was minted.
            if pending.get("expiresInS") is not None:
                enrolment["expiresAt"] = now + pending["expiresInS"]
            current = coerce_registry(self._read_json(self.registry_path))
            enrolled = add_device(current, enrolment)
            # A duplicate label leaves the pending pairing alive: the
            # operator retries with another name rather than re-running
            # `herdr-brain pair`.
            if enrolled is None:
                return "fail", CLAIM_DUPLICATE_LABEL
            self._write_atomic(
                self.registry_path,
                json.dumps(enrolled, ensure_ascii=False, indent=2) + "\n",
            )
            self._delete_pending()
            return "ok", {"token": token, "label": effective}

    def _delete_pending(self) -> None:
        """Deleting a spent pairing is idempotent."""
        try:
            self.pending_path.unlink()
        except OSError:
            pass

    # -- revocation --

    def revoke(self, label: str) -> bool:
        """Drop a device by label. False means no such label."""
        with self._lock:
            current = coerce_registry(self._read_json(self.registry_path))
            next_registry = remove_device(current, label)
            if next_registry is None:
                return False
            self._write_atomic(
                self.registry_path,
                json.dumps(next_registry, ensure_ascii=False, indent=2) + "\n",
            )
            return True

    # -- token expiry (S11) --

    def set_expiry(self, label: str, seconds: float) -> bool:
        """Arm (or re-arm) the label's token deadline ``seconds`` from
        now. False means no such label; a non-positive duration raises
        (the CLI validates the spelling — this guards the meaning).
        Re-arming a deadline that already ran out is the operator
        extending a lapsed phone, not a special case. Applies on the
        device's next request, no restart (same mtime re-read as
        revocation)."""
        if not seconds > 0:
            raise ValueError("expiry must be a positive duration in seconds")
        return self._rewrite_device(label, lambda d: {**d, "expiresAt": self._now() + seconds})

    def clear_expiry(self, label: str) -> bool:
        """Remove the label's deadline — the token goes back to the F1
        shape and never runs out. False means no such label."""
        return self._rewrite_device(label, lambda d: {k: v for k, v in d.items() if k != "expiresAt"})

    def _rewrite_device(self, label: str, change) -> bool:
        """Read-modify-write ONE device entry under the lock, re-derived
        from disk so a concurrent CLI verb can never be silently
        undone (the same invariant the lastSeen stamp keeps)."""
        with self._lock:
            current = coerce_registry(self._read_json(self.registry_path))
            if not any(d["label"] == label for d in current["devices"]):
                return False
            next_registry = {
                "devices": [
                    change(d) if d["label"] == label else d
                    for d in current["devices"]
                ],
            }
            self._write_atomic(
                self.registry_path,
                json.dumps(next_registry, ensure_ascii=False, indent=2) + "\n",
            )
            return True


# ── the /pair rate limit (S11) ────────────────────────────────────────────────


def rate_limit_retry_after(
    stamps, now: float,
    limit: int = PAIR_RATE_ATTEMPTS, window_s: float = PAIR_RATE_WINDOW_S,
):
    """Pure verdict over a client's previous attempt timestamps: None
    when one more attempt fits, else the seconds until the oldest
    counted attempt ages out of the window (a full window's wait at
    most, and never 0 — a sub-second hint tells the client to hammer)."""
    recent = [t for t in stamps if now - t < window_s]
    if len(recent) < limit:
        return None
    oldest = min(recent)
    wait = window_s - (now - oldest)
    return max(1, math.ceil(wait))


class ClaimRateLimiter:
    """In-process, bounded bookkeeping behind ``POST /pair``: at most
    ``limit`` claim attempts per client address per window, then a 429
    with the retry hint. Bounded BY CONSTRUCTION — one deque per client
    capped at ``limit`` stamps, at most ``max_clients`` clients tracked
    (beyond that the least recently active is dropped, which can only
    let an attacker through EARLIER, never keep an honest client out),
    stamps pruned as they age out."""

    def __init__(
        self,
        now: Optional[Callable[[], float]] = None,
        limit: int = PAIR_RATE_ATTEMPTS,
        window_s: float = PAIR_RATE_WINDOW_S,
        max_clients: int = 1024,
    ):
        import time

        self._now = now or time.time
        self._limit = limit
        self._window_s = window_s
        self._max_clients = max_clients
        self._attempts: dict = {}  # client -> list of stamps, oldest first

    def clients(self) -> list:
        """The tracked client addresses (test visibility; bounded)."""
        return list(self._attempts)

    def check(self, client: str, now: Optional[float] = None) -> Optional[int]:
        """Record one attempt and answer: None when it may proceed, or
        the Retry-After seconds when the budget is spent. Refused
        attempts are NOT recorded — punishing retries would stretch the
        window with every 429, and the honest recovery time is exactly
        "when the 10 recorded attempts age out"."""
        now = self._now() if now is None else now
        retry_after = rate_limit_retry_after(
            self._attempts.get(client, ()), now, self._limit, self._window_s
        )
        if retry_after is not None:
            return retry_after

        stamps = [t for t in self._attempts.get(client, ()) if now - t < self._window_s]
        stamps.append(now)
        self._attempts[client] = stamps

        if len(self._attempts) > self._max_clients:
            # Evict the client whose newest stamp is oldest (least
            # recently active), never the caller's own fresh entry.
            coldest = min(self._attempts, key=lambda c: self._attempts[c][-1])
            del self._attempts[coldest]
        return None
