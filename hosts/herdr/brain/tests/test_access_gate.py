"""Access gate (doc 01 §2-§4): Host allowlist
(fail-closed), Origin/CSRF same-origin rules, optional Tailscale
identity, optional proxy device header, and the write/read route
classification — with the distinct 403 texts of doc 01 §5.
"""

from __future__ import annotations

import pytest

from herdr_brain import access
from herdr_brain.config import Settings

from tests.conftest import SETTINGS_KWARGS


def gate_settings(**overrides) -> Settings:
    """Settings with the REAL gate on (SETTINGS_KWARGS rides the
    allow-any-host opt-out for the legacy suite; these tests opt back
    in because they pin the gate itself)."""
    kwargs = {**SETTINGS_KWARGS, "allow_any_host": False}
    kwargs.update(overrides)
    return Settings(**kwargs)


def headers(host=None, origin=None, **extra):
    h = dict(extra)
    if host is not None:
        h["host"] = host
    if origin is not None:
        h["origin"] = origin
    return h


# ── Host allowlist (doc 01, AC6; L5 default set) ─────────────────────────────


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "127.0.0.1:8741",
        "localhost",
        "localhost:3000",
        "[::1]",
        "[::1]:8741",
        "node.tail1234.ts.net",
        "node.tail1234.ts.net:8443",
        "machine-name.tail-name.ts.net:8443",
    ],
)
def test_default_allowed_hosts_cover_loopback_and_ts_net(host):
    assert access.is_host_allowed(host, gate_settings()) is True


@pytest.mark.parametrize(
    "host",
    [
        "",
        "evil.example",
        "evil.example:8443",
        "rebind.ts.net.evil.example",
        "evil.ts.net.evil.example:8443",
        "ts.net",
        "127.0.0.1.evil.example",
        "10.0.0.5:8741",
    ],
)
def test_other_hosts_are_rejected_fail_closed(host):
    assert access.is_host_allowed(host, gate_settings()) is False


def test_allowed_hosts_env_extends_the_allowlist_with_or_without_port():
    cfg = gate_settings(allowed_hosts=("lan-host", "box.home:9000"))
    assert access.is_host_allowed("lan-host", cfg) is True
    assert access.is_host_allowed("lan-host:8741", cfg) is True
    assert access.is_host_allowed("box.home:9000", cfg) is True
    assert access.is_host_allowed("box.home", cfg) is True
    assert access.is_host_allowed("other.home", cfg) is False


def test_hosts_derived_from_allowed_origins_are_allowed():
    cfg = gate_settings(allowed_origins=("https://brain.home.example:8443",))
    assert access.is_host_allowed("brain.home.example:8443", cfg) is True
    assert access.is_host_allowed("brain.home.example", cfg) is True
    assert access.is_host_allowed("other.example", cfg) is False


def test_allow_any_host_is_the_documented_opt_out():
    # The pure host matcher stays fail-closed; the opt-out is applied by
    # check_access before it (the is_host_allowed/check_access split).
    assert access.is_host_allowed("evil.example", gate_settings()) is False
    cfg = gate_settings(allow_any_host=True)
    assert access.check_access(headers(host="evil.example"), "read", cfg) is None
    # Even the opt-out keeps the cross-origin rule for real browsers.
    assert access.check_access(
        headers(host="evil.example", origin="https://other.example"), "write", cfg
    ) == "origin cruzado"


# ── check_access: Host before Origin, Origin same-origin, identity ──────────


def test_disallowed_host_is_rejected_before_any_other_logic():
    cfg = gate_settings()
    # A request whose Origin agrees with its (bad) Host — the DNS
    # rebinding shape — is still refused by the Host gate first.
    refusal = access.check_access(
        headers(host="evil.example", origin="https://evil.example"), "read", cfg
    )
    assert refusal == "host no permitido"


def test_cross_origin_write_is_rejected():
    cfg = gate_settings()
    refusal = access.check_access(
        headers(host="node.tail1234.ts.net:8443", origin="https://evil.example"),
        "write",
        cfg,
    )
    assert refusal == "origin cruzado"


def test_same_origin_write_passes():
    cfg = gate_settings()
    assert access.check_access(
        headers(host="node.tail1234.ts.net:8443",
                origin="https://node.tail1234.ts.net:8443"),
        "write",
        cfg,
    ) is None


def test_write_without_origin_from_non_loopback_host_is_rejected():
    cfg = gate_settings()
    refusal = access.check_access(headers(host="node.tail1234.ts.net:8443"), "write", cfg)
    assert refusal == "origin requerido"


def test_write_without_origin_from_loopback_is_trusted():
    cfg = gate_settings()
    assert access.check_access(headers(host="127.0.0.1:8741"), "write", cfg) is None
    assert access.check_access(headers(host="localhost"), "write", cfg) is None


def test_read_without_origin_passes_from_allowed_hosts():
    cfg = gate_settings()
    assert access.check_access(headers(host="node.tail1234.ts.net:8443"), "read", cfg) is None


def test_loopback_origin_on_a_non_loopback_host_is_allowed():
    # localhost origins (dev-server pages driving the tailnet-deployed
    # brain) stay allowed.
    cfg = gate_settings()
    assert access.check_access(
        headers(host="node.tail1234.ts.net:8443", origin="http://localhost:5173"),
        "write",
        cfg,
    ) is None


def test_configured_origin_extends_the_same_origin_rule():
    cfg = gate_settings(allowed_origins=("https://ui.example",))
    assert access.check_access(
        headers(host="node.tail1234.ts.net:8443", origin="https://ui.example"),
        "write",
        cfg,
    ) is None
    assert access.check_access(
        headers(host="node.tail1234.ts.net:8443", origin="https://other.example"),
        "write",
        cfg,
    ) == "origin cruzado"


def test_unparseable_origin_is_rejected():
    cfg = gate_settings()
    assert access.check_access(
        headers(host="127.0.0.1", origin="not-a-url"), "write", cfg
    ) == "origin no válido"


# ── trusted user (doc 01, AC7) ───────────────────────────────────────────────


def test_trusted_user_requires_a_matching_tailscale_login():
    cfg = gate_settings(trusted_user="me@example.com")
    ok = headers(host="127.0.0.1", **{"tailscale-user-login": "me@example.com"})
    assert access.check_access(ok, "write", cfg) is None
    missing = headers(host="127.0.0.1")
    assert access.check_access(missing, "write", cfg) == "identidad requerida"
    wrong = headers(host="127.0.0.1", **{"tailscale-user-login": "other@example.com"})
    assert access.check_access(wrong, "write", cfg) == "identidad no confiable"


def test_trusted_user_optional_reopens_only_the_missing_header():
    cfg = gate_settings(trusted_user="me@example.com", trusted_user_optional=True)
    assert access.check_access(headers(host="127.0.0.1"), "write", cfg) is None
    wrong = headers(host="127.0.0.1", **{"tailscale-user-login": "other@example.com"})
    assert access.check_access(wrong, "write", cfg) == "identidad no confiable"


# ── proxy device header (doc 01 §2) ──────────────────────────────────────────


def test_device_header_gate_matrix():
    off = gate_settings()
    assert access.device_auth(headers(), off) == {
        "enforced": False, "device": None, "authorized": True,
    }

    cfg = gate_settings(device_header="X-Device", device_allowlist=("phone",))
    # Header absent → read-only (fail-closed), no loopback exemption.
    assert access.device_auth(headers(), cfg) == {
        "enforced": True, "device": None, "authorized": False,
    }
    assert access.device_auth(headers(host="127.0.0.1"), cfg)["authorized"] is False
    # Present + allowlisted → authorized, attributed.
    ok = headers(**{"x-device": "phone"})
    assert access.device_auth(ok, cfg) == {"enforced": True, "device": "phone", "authorized": True}
    # Present + unknown → read-only; the "unknown" sentinel never passes.
    assert access.device_auth(headers(**{"x-device": "tablet"}), cfg)["authorized"] is False
    assert access.device_auth(headers(**{"x-device": "unknown"}), cfg)["authorized"] is False


# ── route classification (doc 01 §4, S3) ─────────────────────────────────────


@pytest.mark.parametrize(
    "method,path,level",
    [
        ("POST", "/pane/reply", "write"),
        ("POST", "/pane/keys", "write"),
        ("POST", "/pane/permission/reply", "write"),
        ("POST", "/approval/g1/approve", "write"),
        ("POST", "/approval/g1/reject", "write"),
        ("POST", "/approval/g1/resolve", "write"),
        ("PATCH", "/approval/g1", "write"),
        ("POST", "/devices/revoke", "write"),
        ("POST", "/ask", "write"),
        ("POST", "/transcribe", "write"),
        ("POST", "/reset", "write"),
        ("POST", "/pane/dialog/answer", "write"),
        # S12 verified sends: the follow-ups type keys and the interrupt
        # stops a live turn — writes; the receipts listing is a read.
        ("POST", "/pane/stop", "write"),
        ("POST", "/pane/receipt/rct-abc123/submit", "write"),
        ("POST", "/pane/receipt/rct-abc123/discard", "write"),
        ("GET", "/pane/receipts", "read"),
        # Read-only set (doc 01 §4): everything else.
        ("GET", "/health", "read"),
        ("GET", "/state", "read"),
        ("GET", "/herd", "read"),
        ("GET", "/view", "read"),
        ("GET", "/conversation", "read"),
        ("GET", "/conversation/w1:p9/rendered", "read"),
        ("GET", "/screen", "read"),
        ("GET", "/events", "read"),
        ("GET", "/call-history", "read"),
        ("GET", "/call/context", "read"),
        ("POST", "/call/context", "read"),  # L4: only restores UI context
        ("GET", "/approval/current", "read"),
        ("GET", "/devices", "read"),
        ("POST", "/tts", "read"),           # PWA local echo
        ("GET", "/audio/x.mp3", "read"),
        ("GET", "/speech/job1/next", "read"),
        ("POST", "/speech/job1/cancel", "read"),
        ("GET", "/", "read"),
        ("GET", "/app.js", "read"),
        ("POST", "/pair", "pair"),          # bootstrap: write-level access only
        ("POST", "/approval/g1/action", "read"),  # keeps HERDR_BRAIN_APPROVAL_TOKEN
        ("GET", "/no-such-route", "read"),
    ],
)
def test_route_classification_matrix(method, path, level):
    assert access.classify(method, path) == level


def test_query_strings_do_not_affect_classification():
    assert access.classify("GET", "/speech/job1/next?after=3") == "read"
    assert access.classify("POST", "/approval/g1/action?decision=approve") == "read"


# ── the combined write guard ─────────────────────────────────────────────────


class StubStore:
    """PairingStore double: enforcement and a single known token."""

    def __init__(self, devices=(), tokens=()):
        self._devices = list(devices)
        self._tokens = set(tokens)
        self.resolved = []
        self.peeked = []

    def enforced(self):
        return bool(self._devices)

    def resolve_status(self, token):
        self.resolved.append(token)
        if token in self._tokens:
            return "ok", {"label": "phone"}
        return "invalid", None

    def peek(self, token):
        self.peeked.append(token)
        if token in self._tokens:
            return {"label": "phone"}
        return None

    def resolve(self, token):
        status, device = self.resolve_status(token)
        return device if status == "ok" else None


def test_write_guard_layers_the_three_gates_with_distinct_texts():
    cfg = gate_settings()
    store = StubStore(devices=["phone"], tokens=("good-token",))

    # All gates satisfied (loopback shell, no bearer needed… enforced
    # pairing means the bearer IS needed — see next block).
    _, refusal = access.guard(
        headers(host="127.0.0.1", authorization="Bearer good-token"),
        "POST", "/pane/reply", cfg, store,
    )
    assert refusal is None

    # Pairing gate: enforced but the bearer is missing/wrong.
    store_no_token = StubStore(devices=["phone"])
    _, refusal = access.guard(
        headers(host="127.0.0.1"), "POST", "/pane/reply", cfg, store_no_token
    )
    assert refusal == "dispositivo no emparejado"

    # Header gate: configured header not allowlisted beats pairing.
    cfg3 = gate_settings(device_header="X-Device", device_allowlist=("phone",))
    store3 = StubStore(devices=["phone"], tokens=("good-token",))
    _, refusal = access.guard(
        headers(host="127.0.0.1", authorization="Bearer good-token"),
        "POST", "/pane/reply", cfg3, store3,
    )
    assert refusal == "dispositivo no autorizado"

    # Host gate fires before everything.
    _, refusal = access.guard(
        headers(host="evil.example"), "GET", "/herd", cfg3, store3
    )
    assert refusal == "host no permitido"


def test_write_guard_resolves_the_device_label_for_handlers():
    cfg = gate_settings()
    store = StubStore(devices=["phone"], tokens=("good-token",))
    label, refusal = access.guard(
        headers(host="127.0.0.1", authorization="Bearer good-token"),
        "POST", "/pane/reply", cfg, store,
    )
    assert refusal is None
    assert label == "phone"


def test_the_pair_bootstrap_resolves_the_bearer_read_only():
    """B4: /pair is device-exempt, so the bearer it may carry is only
    audit attribution — resolved READ-ONLY (``peek``), never through
    ``resolve_status`` (which stamps lastSeenAt). A refused (429) pair
    request must leave the registry byte-identical."""
    cfg = gate_settings()
    store = StubStore(devices=["phone"], tokens=("good-token",))
    label, refusal = access.guard(
        headers(host="127.0.0.1", authorization="Bearer good-token"),
        "POST", "/pair", cfg, store,
    )
    assert refusal is None
    assert label == "phone"  # attribution survives for the audit line
    assert store.resolved == []  # no credential touch on the bootstrap
    assert store.peeked == ["good-token"]


def test_write_guard_passes_without_a_registry():
    """S7: an empty registry keeps every write exactly as open as today."""
    cfg = gate_settings()
    store = StubStore()  # nothing paired
    _, refusal = access.guard(
        headers(host="127.0.0.1"), "POST", "/pane/reply", cfg, store
    )
    assert refusal is None
    _, refusal = access.guard(
        headers(host="node.tail1234.ts.net:8443", origin="https://node.tail1234.ts.net:8443"),
        "POST", "/ask", cfg, store,
    )
    assert refusal is None


def test_pair_route_takes_the_write_origin_gate_but_no_device_gates():
    cfg = gate_settings()
    store = StubStore(devices=["phone"])
    # No bearer, nothing paired-eligible… bootstrap must still pass from
    # a loopback shell with no Origin.
    _, refusal = access.guard(headers(host="127.0.0.1"), "POST", "/pair", cfg, store)
    assert refusal is None
    # But its cross-site Origin is refused exactly like a write's.
    _, refusal = access.guard(
        headers(host="node.tail1234.ts.net:8443", origin="https://evil.example"),
        "POST", "/pair", cfg, store,
    )
    assert refusal == "origin cruzado"


def test_pair_without_origin_from_non_loopback_host_is_rejected():
    """T4 B2: /pair mints a WRITE credential, so the write rule "no
    Origin from a non-loopback Host" binds it too — the bootstrap is
    exempt from the device gates, never from Host/Origin."""
    cfg = gate_settings()
    store = StubStore(devices=["phone"])
    _, refusal = access.guard(
        headers(host="node.tail1234.ts.net:8443"), "POST", "/pair", cfg, store
    )
    assert refusal == "origin requerido"
    # The phone's real shape (browsers always send Origin on POST) and
    # the operator's loopback shell both pass.
    assert access.check_access(
        headers(host="node.tail1234.ts.net:8443",
                origin="https://node.tail1234.ts.net:8443"),
        "pair", cfg,
    ) is None
    assert access.check_access(headers(host="127.0.0.1"), "pair", cfg) is None


# ── reads under enforcement (S11): the device exemption and query token ──────


@pytest.mark.parametrize(
    "method,path,exempt",
    [
        # The PWA shell must load before any credential exists: health,
        # index, assets and the service worker stay open.
        ("GET", "/health", True),
        ("GET", "/", True),
        ("GET", "/index.html", True),
        ("GET", "/app.js", True),
        ("GET", "/pairing.js", True),
        ("GET", "/mermaid.min.js", True),
        ("GET", "/sw.js", True),
        ("GET", "/manifest.webmanifest", True),
        ("GET", "/icon.svg", True),
        ("GET", "/styles.css", True),
        # The bootstrap and the two routes with credentials of their own.
        ("POST", "/pair", True),
        ("POST", "/approval/g1/action", True),
        ("GET", "/speech/job1/next", True),
        ("POST", "/speech/job1/cancel", True),
        # Everything the PWA reads for data is gated once enforced.
        ("GET", "/herd", False),
        ("GET", "/view", False),
        ("GET", "/state", False),
        ("GET", "/events", False),
        ("GET", "/audio/x.mp3", False),
        ("GET", "/devices", False),
        ("GET", "/history", False),
        ("GET", "/screen", False),
        ("POST", "/tts", False),
        ("POST", "/call/context", False),
        ("GET", "/no-such-route", False),
        # Only asset-LIKE names are exempt: an API path with a .js suffix
        # carved into it is still data.
        ("GET", "/history.js", True),  # a static asset name, not a route
        ("GET", "/events.js", True),
    ],
)
def test_device_exemption_matrix(method, path, exempt):
    assert access.is_device_exempt(method, path) is exempt


@pytest.mark.parametrize(
    "method,path,allowed",
    [
        ("GET", "/events", True),
        ("GET", "/audio/clip.mp3", True),
        ("POST", "/events", False),      # never a write, and not real anyway
        ("GET", "/herd", False),
        ("POST", "/pane/reply", False),
        ("POST", "/pair", False),
        ("GET", "/audio/sub/clip.mp3", False),  # one path segment, like the route
    ],
)
def test_query_token_is_confined_to_events_and_audio_gets(method, path, allowed):
    assert access.query_token_allowed(method, path) is allowed


def test_read_guard_demands_a_device_once_pairing_is_enforced():
    cfg = gate_settings()
    store = StubStore(devices=["phone"])
    _, refusal = access.guard(headers(host="127.0.0.1"), "GET", "/herd", cfg, store)
    assert refusal == "dispositivo no emparejado"

    ok = StubStore(devices=["phone"], tokens=("good-token",))
    _, refusal = access.guard(
        headers(host="127.0.0.1", authorization="Bearer good-token"),
        "GET", "/herd", cfg, ok,
    )
    assert refusal is None


def test_read_guard_exempts_the_shell_so_the_pwa_can_boot_to_pair():
    cfg = gate_settings()
    store = StubStore(devices=["phone"])  # enforced, no credential
    for method, path in (("GET", "/"), ("GET", "/health"), ("GET", "/app.js")):
        _, refusal = access.guard(headers(host="127.0.0.1"), method, path, cfg, store)
        assert refusal is None, path


def test_read_guard_without_a_registry_stays_open():
    """S9: nothing paired, nothing demanded — on reads too."""
    cfg = gate_settings()
    store = StubStore()
    _, refusal = access.guard(headers(host="127.0.0.1"), "GET", "/herd", cfg, store)
    assert refusal is None


def test_an_expired_credential_is_refused_with_its_own_text():
    cfg = gate_settings()

    class ExpiredStore(StubStore):
        def resolve_status(self, token):
            if token in self._tokens:
                return "expired", {"label": "phone"}
            return "invalid", None

    store = ExpiredStore(devices=["phone"], tokens=("old-token",))
    for method, path in (("GET", "/herd"), ("POST", "/pane/reply")):
        _, refusal = access.guard(
            headers(host="127.0.0.1", authorization="Bearer old-token"),
            method, path, cfg, store,
        )
        assert refusal == "dispositivo caducado", path


def test_the_query_token_opens_only_its_two_get_routes():
    cfg = gate_settings()
    store = StubStore(devices=["phone"], tokens=("good-token",))

    for path in ("/events", "/audio/x.mp3"):
        _, refusal = access.guard(
            headers(host="127.0.0.1"), "GET", path, cfg, store,
            query_token="good-token",
        )
        assert refusal is None, path

    # Everywhere else the parameter is inert — including writes.
    _, refusal = access.guard(
        headers(host="127.0.0.1"), "GET", "/herd", cfg, store,
        query_token="good-token",
    )
    assert refusal == "dispositivo no emparejado"
    _, refusal = access.guard(
        headers(host="127.0.0.1"), "POST", "/pane/reply", cfg, store,
        query_token="good-token",
    )
    assert refusal == "dispositivo no emparejado"


def test_a_wrong_query_token_refuses_like_a_wrong_bearer():
    cfg = gate_settings()
    store = StubStore(devices=["phone"], tokens=("good-token",))
    _, refusal = access.guard(
        headers(host="127.0.0.1"), "GET", "/events", cfg, store,
        query_token="not-a-token",
    )
    assert refusal == "dispositivo no emparejado"
