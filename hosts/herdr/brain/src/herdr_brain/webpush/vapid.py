"""VAPID key lifecycle (RFC 8292) — generate, persist, load, import.

Keys resolve in one place: the ``HERDR_BRAIN_VAPID_*`` environment wins,
then ``<state_dir>/vapid.json``. The file is 0600 inside a 0700 state
dir (it IS a signing key) and is written atomically — a crash never
leaves a half-written key behind. The dotenv-style import exists for the
operator who already has a pair from an older panel: it matches
``*_VAPID_PUBLIC`` / ``*_VAPID_PRIVATE`` / ``*_VAPID_SUBJECT`` keys by
suffix, so whatever product exported the file keeps its own name and we
never write it down.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from . import crypto
from ..pairing import ensure_private_dir

#: Key file name inside the state dir; owned here.
VAPID_FILENAME = "vapid.json"

#: Environment keys, resolved by the caller's environ (injectable).
ENV_PUBLIC = "HERDR_BRAIN_VAPID_PUBLIC"
ENV_PRIVATE = "HERDR_BRAIN_VAPID_PRIVATE"
ENV_SUBJECT = "HERDR_BRAIN_VAPID_SUBJECT"

#: Default claim lifetime for the JWT (RFC 8292 suggests ≥ 12 h… no:
#: services accept up to 24 h; 12 h is the commonly issued value).
JWT_LIFETIME_S = 12 * 3600


class VapidError(Exception):
    """Keys are missing, malformed, or not a matching pair."""


@dataclass(frozen=True)
class VapidKeys:
    """A usable signing identity: uncompressed P-256 public point bytes,
    the private scalar, and the ``mailto:`` operator contact."""

    public: bytes
    private: int
    subject: str

    @property
    def public_b64url(self) -> str:
        """The form ``pushManager.subscribe`` expects as ``applicationServerKey``."""
        return crypto.b64url_encode(self.public)


def _validate(public: bytes, private: int, subject: str) -> VapidKeys:
    if not 1 <= private < crypto.P256_N:
        raise VapidError("private key is not a P-256 scalar")
    # Fail at load, not mid-send: a mismatched pair produces signatures
    # every push service rejects with an opaque error.
    if crypto.public_from_private(private) != public:
        raise VapidError("public and private VAPID keys are not a pair")
    if not subject.startswith("mailto:"):
        raise VapidError("VAPID subject must be a mailto: contact")
    return VapidKeys(public=public, private=private, subject=subject)


def generate_keys(subject: str = "mailto:operator@herdr-brain.local") -> VapidKeys:
    private, public = crypto.generate_keypair()
    return VapidKeys(public=public, private=private, subject=subject)


def save_keys(state_dir: Path, keys: VapidKeys) -> Path:
    """Atomically writes ``vapid.json`` at 0600; the state dir stays 0700."""
    state_dir = Path(state_dir)
    ensure_private_dir(state_dir)
    path = state_dir / VAPID_FILENAME
    payload = json.dumps(
        {
            "public": keys.public_b64url,
            "private": crypto.b64url_encode(keys.private.to_bytes(32, "big")),
            "subject": keys.subject,
        },
        ensure_ascii=False,
        indent=2,
    )
    tmp = path.with_suffix(".json.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def _decode_pair(public_text: str, private_text: str) -> tuple:
    try:
        public = crypto.b64url_decode(public_text)
        private_bytes = crypto.b64url_decode(private_text)
    except Exception as exc:  # noqa: BLE001 — surface every shape as VapidError
        raise VapidError(f"VAPID keys are not valid base64url: {exc}") from exc
    if len(private_bytes) != 32:
        raise VapidError("private VAPID key must be 32 bytes of base64url")
    return public, int.from_bytes(private_bytes, "big")


def load_keys(
    state_dir: Path, environ: Optional[Dict[str, str]] = None
) -> Optional[VapidKeys]:
    """Env first, then the key file; ``None`` when nothing is configured.

    A missing configuration is the soft-off state (the server runs
    without push); a PRESENT but broken one is loud, never guessed.
    """
    env = os.environ if environ is None else environ
    env_public = env.get(ENV_PUBLIC, "").strip()
    env_private = env.get(ENV_PRIVATE, "").strip()
    env_subject = env.get(ENV_SUBJECT, "").strip()
    if env_public or env_private or env_subject:
        if not (env_public and env_private and env_subject):
            raise VapidError(
                f"partial VAPID configuration: {ENV_PUBLIC}, {ENV_PRIVATE} and "
                f"{ENV_SUBJECT} must all be set together"
            )
        public, private = _decode_pair(env_public, env_private)
        return _validate(public, private, env_subject)

    path = Path(state_dir) / VAPID_FILENAME
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VapidError(f"cannot read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise VapidError(f"{path} is not a VAPID key object")
    try:
        public, private = _decode_pair(raw["public"], raw["private"])
        subject = str(raw["subject"])
    except KeyError as exc:
        raise VapidError(f"{path} is missing field {exc}") from exc
    return _validate(public, private, subject)


#: Suffixes the dotenv import matches, generically prefixed.
_IMPORT_SUFFIXES = ("VAPID_PUBLIC", "VAPID_PRIVATE", "VAPID_SUBJECT")
_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def import_keys_from_env_file(path: Path) -> VapidKeys:
    """Reads an operator's existing pair from a dotenv-style file.

    Only the suffix is ours; the prefix belongs to whatever product
    wrote the file, so any ``<NAME>_VAPID_*`` row is accepted. Values
    keep their quotes stripped and base64 padding intact.
    """
    found: Dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise VapidError(f"cannot read {path}: {exc}") from exc
    for line in text.splitlines():
        match = _LINE_RE.match(line)
        if not match:
            continue
        name, value = match.group(1), match.group(2).strip()
        for suffix in _IMPORT_SUFFIXES:
            if name.endswith(suffix) and name != suffix:
                value = value.strip().strip('"').strip("'")
                found.setdefault(suffix, value)
    missing = [s for s in _IMPORT_SUFFIXES if s not in found]
    if missing:
        raise VapidError(
            f"{path} has no complete VAPID pair (missing: {', '.join(missing)}); "
            "expected keys named *_VAPID_PUBLIC, *_VAPID_PRIVATE, *_VAPID_SUBJECT"
        )
    public, private = _decode_pair(found["VAPID_PUBLIC"], found["VAPID_PRIVATE"])
    return _validate(public, private, found["VAPID_SUBJECT"])


def build_vapid_jwt(keys: VapidKeys, audience: str, now: float) -> str:
    """The ``Authorization: vapid t=…`` token (RFC 8292 §3.2).

    ES256 with the raw ``r||s`` signature form, signed deterministically
    (RFC 6979) so repeated sends to one origin are reproducible.
    """
    if not audience.startswith("https://"):
        raise VapidError("audience must be the push service https origin")
    if not keys.subject.startswith("mailto:"):
        raise VapidError("VAPID subject must be a mailto: contact")
    header = crypto.b64url_encode(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
    claims = crypto.b64url_encode(
        json.dumps(
            {
                "aud": audience,
                "exp": int(now) + JWT_LIFETIME_S,
                "sub": keys.subject,
            }
        ).encode()
    )
    signature = crypto.ecdsa_sign_p256_sha256(
        keys.private, f"{header}.{claims}".encode()
    )
    return f"{header}.{claims}.{crypto.b64url_encode(signature)}"
