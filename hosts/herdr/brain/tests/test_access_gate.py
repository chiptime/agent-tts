"""Access gate (Panel parity F1, doc 01 §2-§4): Host allowlist
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
    # check_access before it (Panel's isHostAllowed/checkAccess split).
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
    # Panel parity: localhost origins (dev server pages driving the
    # loopback-deployed bridge) stay allowed.
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

    def enforced(self):
        return bool(self._devices)

    def resolve(self, token):
        self.resolved.append(token)
        if token in self._tokens:
            return {"label": "phone"}
        return None


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
