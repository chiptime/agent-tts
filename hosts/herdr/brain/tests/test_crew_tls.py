"""Crew TLS material from the stdlib alone: pins are SHA-256 over DER
bytes, the client context trusts EXACTLY one pinned certificate (no CA
store, no default paths), and the host's own certificate is generated
by the local ``openssl`` binary — never hand-rolled X.509 in Python.
"""

from __future__ import annotations

import hashlib
import shutil
import ssl
from pathlib import Path

import pytest

from herdr_brain.crew.tls import (
    ensure_host_certificate,
    openssl_self_signed_command,
    pem_to_der,
    pin_of_der,
    pin_of_pem,
    pinned_client_context,
    server_ssl_context,
)

FIXTURES = Path(__file__).parent / "fixtures" / "crew"
PEER_PEM = (FIXTURES / "peer.pem").read_text(encoding="utf-8")
PEER_DER = (FIXTURES / "peer.der").read_bytes()
LEAD_PEM = (FIXTURES / "lead.pem").read_text(encoding="utf-8")


# ── PEM/DER and pins ─────────────────────────────────────────────────────────


def test_pem_to_der_matches_openssl_der() -> None:
    assert pem_to_der(PEER_PEM) == PEER_DER


def test_pins_are_sha256_hex_of_der() -> None:
    expected = hashlib.sha256(PEER_DER).hexdigest()
    assert pin_of_der(PEER_DER) == expected
    assert pin_of_pem(PEER_PEM) == expected
    assert pin_of_pem(LEAD_PEM) != expected  # different cert, different pin


def test_pem_to_der_rejects_non_certificate_text() -> None:
    from herdr_brain.crew.tls import CrewTlsError

    with pytest.raises(CrewTlsError):
        pem_to_der("hello world")


# ── the pinned client context ────────────────────────────────────────────────


def test_client_context_pins_exactly_one_certificate() -> None:
    context = pinned_client_context(PEER_PEM)

    assert context.verify_mode == ssl.CERT_REQUIRED
    # the pin IS the identity: no hostname expectation, no CA hierarchy
    assert context.check_hostname is False
    cas = context.get_ca_certs()
    assert len(cas) == 1  # ONLY the pinned cert — no default CA store
    assert pin_of_der(PEER_DER) == hashlib.sha256(
        pem_to_der(PEER_PEM)
    ).hexdigest()


def test_two_pins_do_not_trust_each_other() -> None:
    pinned_peer = pinned_client_context(PEER_PEM)
    pinned_lead = pinned_client_context(LEAD_PEM)
    peer_cas = pinned_peer.get_ca_certs()
    lead_cas = pinned_lead.get_ca_certs()
    assert len(peer_cas) == 1 and len(lead_cas) == 1
    assert peer_cas != lead_cas


# ── the server side ──────────────────────────────────────────────────────────


def test_server_context_requires_a_client_certificate(tmp_path: Path) -> None:
    # materialize a throwaway key/cert pair from the fixture certificate
    # is impossible without the key, so generate one with openssl here
    if shutil.which("openssl") is None:
        pytest.skip("openssl binary not available")
    import subprocess

    cert = tmp_path / "s.pem"
    key = tmp_path / "s.key"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(key), "-out", str(cert),
            "-days", "2", "-nodes", "-subj", "/CN=crew-test-server",
        ],
        check=True,
        capture_output=True,
    )
    context = server_ssl_context(cert, key, cert, require_client=True)
    assert context.verify_mode == ssl.CERT_REQUIRED

    optional = server_ssl_context(cert, key, cert, require_client=False)
    assert optional.verify_mode == ssl.CERT_OPTIONAL


# ── host certificate generation via the openssl binary ───────────────────────


def test_openssl_command_text_is_printable_and_concrete() -> None:
    text = openssl_self_signed_command("host-a", "/path/crew.crt", "/path/crew.key")
    assert "openssl" in text
    assert "req" in text and "-x509" in text
    assert "host-a" in text  # the CN names the host
    assert "/path/crew.crt" in text and "/path/crew.key" in text


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl binary not available")
def test_ensure_host_certificate_generates_then_reuses(tmp_path: Path) -> None:
    cert, key, pin = ensure_host_certificate(tmp_path, "host-a")
    assert cert.exists() and key.exists()
    assert pin == pin_of_pem(cert.read_text(encoding="utf-8"))

    cert2, key2, pin2 = ensure_host_certificate(tmp_path, "host-a")
    assert (cert2, pin2) == (cert, pin)  # stable identity across restarts
