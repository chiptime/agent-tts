"""Access gate for the brain's HTTP surface (doc 01 §2-§5, S11).

The brain answers one phone over one tailnet, and every guard here
exists because that perimeter alone is not an identity. Three
independent doors compose by AND on every WRITE route — and, once
pairing is enforced, on READS too (S11), minus the shell/bootstrap
exemptions that must answer before any credential can exist:

1. ``check_access`` — Host allowlist (fail-closed, anti DNS-rebinding),
   Origin same-origin rule (anti CSRF) and the optional Tailscale
   identity check. Applies to EVERY route; reads need no Origin.
2. ``device_auth`` — the optional proxy device header/allowlist
   (``HERDR_BRAIN_DEVICE_HEADER``), for deployments behind a trusted
   reverse proxy. Off by default.
3. Device pairing — enforced exactly when ``paired-devices.json`` is
   non-empty: the request must carry the bearer token of a paired,
   unexpired device (``device_token`` query parameter on the two
   headerless GET transports, see ``query_token_allowed``).

Each refusal carries its own 403 text (doc 01 §5), so the PWA can tell
"this device holds no credential" (offer pairing) from "this host is
not allowed" (an operator problem).

Default Host allowlist (L5 in odd/tasks/parity-f1-security.md):
loopback forms, any ``*.ts.net`` name (MagicDNS names are issued only
inside the tailnet, and the phone's ``https://<node>.ts.net:8443``
access must keep working with no new configuration), the hosts of the
configured allowed origins, and ``HERDR_BRAIN_ALLOWED_HOSTS``.
``HERDR_BRAIN_ALLOW_ANY_HOST=1`` is the warned opt-out.
"""

from __future__ import annotations

import re
from typing import Mapping, Optional, Protocol
from urllib.parse import urlsplit

# Loopback forms with or without a port. ``::1`` appears both bare and
# bracketed depending on the client library.
_LOOPBACK_HOST = re.compile(
    r"^(localhost|127\.0\.0\.1|\[::1\]|::1)(:\d+)?$", re.IGNORECASE
)
# Any MagicDNS name: <machine>.<tailnet>.ts.net, with any port. A bare
# ``ts.net`` or an embedded suffix (evil.ts.net.evil.example) must not
# match, hence the anchored structure.
_TSNET_HOST = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*\.ts\.net(:\d+)?$",
    re.IGNORECASE,
)

# Distinct 403 texts per gate (doc 01 §5). The PWA matches
# DEVICE_NOT_PAIRED verbatim to offer the pairing flow.
HOST_NOT_ALLOWED = "host no permitido"
CROSS_ORIGIN = "origin cruzado"
ORIGIN_REQUIRED = "origin requerido"
BAD_ORIGIN = "origin no válido"
IDENTITY_REQUIRED = "identidad requerida"
IDENTITY_NOT_TRUSTED = "identidad no confiable"
DEVICE_NOT_AUTHORISED = "dispositivo no autorizado"
DEVICE_NOT_PAIRED = "dispositivo no emparejado"
# S11: a token that matched a paired device whose deadline has passed.
# Distinct from NOT_PAIRED on purpose: the fix is re-pairing (or the
# operator extending the deadline), and the PWA says which.
DEVICE_EXPIRED = "dispositivo caducado"


def _strip_port(host: str) -> str:
    """``node.example:8443`` → ``node.example``; bracketed IPv6 keeps its
    brackets so ``[::1]:8441`` → ``[::1]``."""
    if host.startswith("["):
        end = host.find("]")
        if end != -1:
            return host[: end + 1]
        return host
    return host.split(":", 1)[0]


def is_loopback_host(host: str) -> bool:
    return bool(_LOOPBACK_HOST.match(host or ""))


def is_tsnet_host(host: str) -> bool:
    return bool(_TSNET_HOST.match(host or ""))


class AccessConfig(Protocol):
    """The Settings fields this gate reads (structural, for tests)."""

    allowed_hosts: tuple
    allowed_origins: tuple
    allow_any_host: bool
    trusted_user: str
    trusted_user_optional: bool
    device_header: str
    device_allowlist: tuple


def is_host_allowed(host: str, cfg: AccessConfig) -> bool:
    """Whether the request's Host header is one the brain answers to:
    a loopback form, a ``*.ts.net`` name, an explicit
    ``HERDR_BRAIN_ALLOWED_HOSTS`` entry (bare or with any port), or the
    host of a configured allowed origin. Pure, exported for tests."""
    if not host:
        return False
    if is_loopback_host(host):
        return True
    if is_tsnet_host(host):
        return True
    bare = _strip_port(host)
    for entry in cfg.allowed_hosts:
        if host == entry or bare == _strip_port(entry):
            return True
    for origin in cfg.allowed_origins:
        parsed = urlsplit(origin if "//" in origin else f"https://{origin}")
        origin_host = parsed.netloc or parsed.path
        if origin_host and (host == origin_host or bare == _strip_port(origin_host)):
            return True
    return False


def check_access(
    headers: Mapping[str, str], level: str, cfg: AccessConfig
) -> Optional[str]:
    """The Host/Origin/identity gate, or None when the request passes.

    - Host allowlist, ALWAYS ON and BEFORE any Origin logic, so a
      rebinding request (Host==Origin==evil.example) never reaches it.
      ``cfg.allow_any_host`` is the operator's explicit opt-out.
    - Same-origin only: an Origin, when present, must match the Host,
      be loopback (dev-server pages driving a loopback brain), or be
      explicitly allowed. Browsers omit Origin on same-origin GETs (so
      the poll passes) and send it on POSTs.
    - Origin required for writes: a state-changing request (or the
      ``/pair`` bootstrap, which hands out write credentials — T4 B2)
      with no Origin is trusted only from loopback (curl on the host) —
      or under ``allow_any_host``, which has already accepted the host
      hole. Browsers always send Origin on fetch POSTs, so a missing
      Origin on a remote write is a non-browser or Origin-stripped
      request.
    - Tailscale identity: when a trusted user is configured, the
      request must carry a matching ``Tailscale-User-Login``. A missing
      header is rejected too (fail-closed: `tailscale serve` injects no
      Tailscale-User-* for tagged nodes); ``trusted_user_optional`` is
      the escape for host-local development.
    """
    host = headers.get("host", "")

    if not cfg.allow_any_host and not is_host_allowed(host, cfg):
        return HOST_NOT_ALLOWED

    origin = headers.get("origin")
    if origin:
        origin_host = (urlsplit(origin).netloc or "") if "//" in origin else ""
        if not origin_host:
            return BAD_ORIGIN
        allowed = (
            origin_host == host
            or is_loopback_host(origin_host)
            or origin in cfg.allowed_origins
        )
        if not allowed:
            return CROSS_ORIGIN
    elif level in ("write", "pair") and not (
        is_loopback_host(host) or cfg.allow_any_host
    ):
        # A write — or the /pair bootstrap, which mints a write
        # credential — with no Origin from a non-loopback Host isn't a
        # real browser request: refuse (T4 B2).
        return ORIGIN_REQUIRED

    if cfg.trusted_user:
        login = headers.get("tailscale-user-login")
        if login:
            if login != cfg.trusted_user:
                return IDENTITY_NOT_TRUSTED
        elif not cfg.trusted_user_optional:
            # Fail closed: an absent header is not "a loopback caller" —
            # it is any tagged node on the tailnet.
            return IDENTITY_REQUIRED
    return None


def device_auth(headers: Mapping[str, str], cfg: AccessConfig) -> dict:
    """Optional per-device authorisation through a header a trusted
    upstream proxy injects (``HERDR_BRAIN_DEVICE_HEADER``). Matrix:

    - feature off (no header configured) → not enforced, fully
      authorised (today's behaviour);
    - header absent → read-only: configuring the header is the operator
      asserting the proxy sets it on every request, and a request
      without one did not come through that proxy. No loopback
      exemption — a loopback peer says nothing about the caller;
    - header present and allowlisted → authorised, attributed;
    - header present, unlisted (or the ``unknown`` sentinel) →
      read-only.
    """
    if not cfg.device_header:
        return {"enforced": False, "device": None, "authorized": True}
    raw = headers.get(cfg.device_header.lower())
    device = raw.strip() if isinstance(raw, str) and raw.strip() else None
    if not device:
        return {"enforced": True, "device": None, "authorized": False}
    authorized = device != "unknown" and device in tuple(cfg.device_allowlist)
    return {"enforced": True, "device": device, "authorized": authorized}


# ── route classification (doc 01 §4) ─────────────────────────────────────────

# Writes: routes that type into a terminal, mutate the call state, or
# manage credentials. Everything else is read-only at the access level.
_WRITE_RULES = (
    ("POST", re.compile(r"^/pane/reply$")),
    ("POST", re.compile(r"^/pane/keys$")),
    ("POST", re.compile(r"^/pane/dialog/answer$")),
    ("POST", re.compile(r"^/pane/permission/reply$")),
    # S12 verified sends: the submit/discard follow-ups type keys into a
    # real terminal, and the interrupt stops a live turn — writes, gated
    # and audited like the replies they finish. GET /pane/receipts stays
    # a read (device-gated once pairing is enforced, never exempt).
    ("POST", re.compile(r"^/pane/stop$")),
    ("POST", re.compile(r"^/pane/receipt/[^/]+/(submit|discard)$")),
    ("POST", re.compile(r"^/approval/[^/]+/(approve|reject|resolve)$")),
    ("PATCH", re.compile(r"^/approval/[^/]+$")),
    ("POST", re.compile(r"^/devices/revoke$")),
    # S5 web push: subscribing/unsubscribing changes which phones the
    # brain may wake, and a transport test IS a delivery — all three are
    # writes behind the device gate and the audit log.
    ("POST", re.compile(r"^/push/(subscribe|unsubscribe|test)$")),
    # S13 operator messages: the send types into a real pane (through
    # the walky delivery gate). The inbox read stays a read.
    ("POST", re.compile(r"^/msg/send$")),
    ("POST", re.compile(r"^/ask$")),
    ("POST", re.compile(r"^/transcribe$")),
    ("POST", re.compile(r"^/reset$")),
)


def classify(method: str, path: str) -> str:
    """``"write"`` for device-gated routes, ``"pair"`` for the pairing
    bootstrap (write-level Host/Origin rules, deliberately no device
    gates), ``"read"`` for everything else — including ``POST
    /call/context`` (only restores UI context, L4), ``POST /tts`` (the
    PWA's local echo), ``POST /approval/{id}/action`` (keeps
    HERDR_BRAIN_APPROVAL_TOKEN because ntfy cannot attach a bearer) and
    the speech transport (job-token authenticated, unchanged)."""
    if method == "POST" and path == "/pair":
        return "pair"
    for route_method, pattern in _WRITE_RULES:
        if method == route_method and pattern.match(path):
            return "write"
    return "read"


# ── the S11 read gate ─────────────────────────────────────────────────────────
#
# Once pairing is enforced, READS carry the device requirement too —
# except the surfaces that must answer before any credential can exist
# or that hold credentials of their own. The PWA shell is one of them:
# it has to LOAD before it can offer the pairing flow. StaticFiles
# serves any file in static/, so assets are exempted by shape (name +
# asset suffix at the root), not by an exact list that would silently
# gate the next file someone drops in.
_DEVICE_EXEMPT_RULES = (
    ("GET", re.compile(r"^/health$")),
    ("GET", re.compile(r"^/(index\.html)?$")),
    ("GET", re.compile(
        r"^/[A-Za-z0-9][A-Za-z0-9._-]*"
        r"\.(js|css|svg|png|ico|webp|jpe?g|gif|woff2?|ttf|webmanifest|map)$"
    )),
    ("POST", re.compile(r"^/approval/[^/]+/action$")),  # ntfy: its own token
    (None, re.compile(r"^/speech/[^/]+/(next|cancel)$")),  # job-token bound
    ("POST", re.compile(r"^/pair$")),  # the bootstrap itself (defence in depth)
)


def is_device_exempt(method: str, path: str) -> bool:
    """Whether this route NEVER demands the device credential, however
    enforcement stands. ``method=None`` rules match any method."""
    for rule_method, pattern in _DEVICE_EXEMPT_RULES:
        if rule_method is not None and method != rule_method:
            continue
        if pattern.match(path):
            return True
    return False


# EventSource and <audio src> cannot send headers: exactly these two
# GET transports may carry the device token as a ``device_token`` query
# parameter instead (S11.2) — never any other route, never a write.
_QUERY_TOKEN_ROUTES = (
    ("GET", re.compile(r"^/events$")),
    ("GET", re.compile(r"^/audio/[^/]+$")),
)


def query_token_allowed(method: str, path: str) -> bool:
    """Whether this request may present the device credential in the
    query string. Confined by an allowlist, not denied by a list of
    writes: a new route opts IN, it cannot inherit the hole."""
    for route_method, pattern in _QUERY_TOKEN_ROUTES:
        if method == route_method and pattern.match(path):
            return True
    return False


class PairingGate(Protocol):
    """The pairing dependency, structurally: the questions the request
    path asks."""

    def enforced(self) -> bool: ...

    def resolve_status(self, token: Optional[str]) -> tuple: ...

    def peek(self, token: Optional[str]) -> Optional[dict]: ...


def guard(
    headers: Mapping[str, str],
    method: str,
    path: str,
    cfg: AccessConfig,
    pairing: Optional[PairingGate],
    query_token: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """The combined gate used by the middleware. Returns
    ``(device_label, refusal_text)`` — exactly one of the two is set.

    On success the device label (None when no paired bearer was
    presented) is exposed to handlers as ``request.state.device`` for
    the audit log (T3).

    ``query_token`` is the ``device_token`` query parameter the caller
    extracted; the guard accepts it as a credential only on the two
    headerless GET transports (see ``query_token_allowed``) and a
    header bearer always wins when both are present.

    Under enforcement (S11) every route demands the device credential
    except the exemptions of ``is_device_exempt`` — the write routes of
    doc 01 §4 AND the reads: an unpaired client's 403 is the signal
    that opens the PWA's pairing flow."""
    level = classify(method, path)

    refusal = check_access(headers, level, cfg)
    if refusal is not None:
        return None, refusal

    if level == "write" and pairing is not None:
        header_gate = device_auth(headers, cfg)
        if not header_gate["authorized"]:
            return None, DEVICE_NOT_AUTHORISED

    status, device = "invalid", None
    if pairing is not None:
        from .pairing import bearer_token

        token = bearer_token(headers.get)
        if not token and query_token and query_token_allowed(method, path):
            token = query_token
        if token:
            if level == "pair":
                # B4: the bootstrap is device-exempt, so the bearer is
                # resolved only for the audit line's attribution — and
                # READ-ONLY (``peek``): a refused (429) pair request
                # must leave the registry byte-identical, with no
                # lastSeenAt stamp on a valid credential.
                device = pairing.peek(token)
            else:
                status, device = pairing.resolve_status(token)

    needs_device = pairing is not None and pairing.enforced() and (
        level == "write" or (level == "read" and not is_device_exempt(method, path))
    )
    if needs_device and status == "expired":
        # The device IS known — its credential simply ran out. Saying
        # "not paired" here would hide the one thing the operator set
        # up on purpose (S11.3).
        return None, DEVICE_EXPIRED
    if needs_device and device is None:
        # Distinct from DEVICE_NOT_AUTHORISED on purpose: "not
        # authorised" is the operator's proxy allowlist, "not paired"
        # is this device's own missing credential — fixed in completely
        # different places.
        return None, DEVICE_NOT_PAIRED

    label = device["label"] if device else None
    return label, None
