"""Crew federation for herdr-brain (doc 10, task S7).

A lead and its peers federate over a mutually-pinned TLS link with a
shared crew secret. This package is the whole federation core, self-
contained and framework-free: the roster and enrolment (``roster``,
``enrol``), the two-factor check (``auth``), the TLS material
(``tls``), the lead-side proxy client with per-peer health (``client``),
herd aggregation and pane routing (``aggregate``), the gate-ownership
router (``gates``), the peer-side request dispatcher (``service``) and
the operator verbs (``cli``).

Design stance, in one breath: the pin is the identity, the secret is
the second factor, the refusal is uniform, gates live on the host that
owns the agent, and promotion is manual — an operator types it, and
nothing in this package ever elects, watches, or takes over on its
own.
"""

from .auth import (
    UNAUTHORIZED_BODY,
    UNAUTHORIZED_STATUS,
    cert_sha256_pin,
    check_crew_auth,
    refusal_response,
)
from .enrol import (
    JOIN_TOKEN_ATTEMPTS,
    JOIN_TOKEN_TTL_S,
    JoinTokens,
    check_join_token,
)
from .roster import (
    CERTS_DIRNAME,
    CREW_FILENAME,
    ROLE_LEAD,
    ROLE_PEER,
    ROLE_SOLO,
    ROTATE_GRACE_S,
    CrewRoster,
    CrewRosterCorrupt,
    CrewStateError,
    default_self_name,
    mint_crew_secret,
    normalize_name,
)
from .tls import (
    CrewTlsError,
    ensure_host_certificate,
    openssl_self_signed_command,
    pem_to_der,
    pin_of_der,
    pin_of_pem,
    pinned_client_context,
    server_ssl_context,
)

__all__ = [
    "CERTS_DIRNAME",
    "CREW_FILENAME",
    "JOIN_TOKEN_ATTEMPTS",
    "JOIN_TOKEN_TTL_S",
    "ROLE_LEAD",
    "ROLE_PEER",
    "ROLE_SOLO",
    "ROTATE_GRACE_S",
    "UNAUTHORIZED_BODY",
    "UNAUTHORIZED_STATUS",
    "CrewRoster",
    "CrewRosterCorrupt",
    "CrewStateError",
    "CrewTlsError",
    "JoinTokens",
    "cert_sha256_pin",
    "check_crew_auth",
    "check_join_token",
    "default_self_name",
    "ensure_host_certificate",
    "mint_crew_secret",
    "normalize_name",
    "openssl_self_signed_command",
    "pem_to_der",
    "pin_of_der",
    "pin_of_pem",
    "pinned_client_context",
    "refusal_response",
    "server_ssl_context",
]
