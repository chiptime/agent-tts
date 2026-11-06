"""Tests for access gate classification and guards on /call/stream (T1)."""

from __future__ import annotations

import pytest

from herdr_brain import access
from herdr_brain.config import Settings
from tests.conftest import SETTINGS_KWARGS


def gate_settings(**overrides) -> Settings:
    kwargs = {**SETTINGS_KWARGS, "allow_any_host": False}
    kwargs.update(overrides)
    return Settings(**kwargs)


def test_call_stream_is_classified_as_write():
    """PRD-03 /call/stream is a write-level route requiring same-origin and host validation."""
    assert access.classify("GET", "/call/stream") == "write"
    assert access.classify("WEBSOCKET", "/call/stream") == "write"


def test_call_stream_is_device_exempt_at_http_handshake():
    """Device auth token travels in the first WebSocket frame, not the HTTP handshake."""
    assert access.is_device_exempt("GET", "/call/stream") is True
    assert access.is_device_exempt("WEBSOCKET", "/call/stream") is True


def test_call_stream_enforces_host_allowlist():
    cfg = gate_settings()
    # Loopback and *.ts.net are allowed
    assert access.check_access({"host": "localhost"}, "write", cfg) is None
    assert access.check_access({"host": "node.tail1234.ts.net", "origin": "https://node.tail1234.ts.net"}, "write", cfg) is None

    # Unallowed host fails closed
    assert access.check_access({"host": "evil.example"}, "write", cfg) == access.HOST_NOT_ALLOWED


def test_call_stream_enforces_origin_on_remote_host():
    cfg = gate_settings()
    # Missing origin on remote host
    assert access.check_access({"host": "node.tail1234.ts.net"}, "write", cfg) == access.ORIGIN_REQUIRED
    # Cross origin on remote host
    assert access.check_access(
        {"host": "node.tail1234.ts.net", "origin": "https://evil.example"},
        "write",
        cfg,
    ) == access.CROSS_ORIGIN


def test_call_stream_guard_passes_for_valid_handshake():
    cfg = gate_settings()
    label, refusal = access.guard(
        {"host": "localhost", "origin": "http://localhost"},
        "GET",
        "/call/stream",
        cfg,
        pairing=None,
    )
    assert refusal is None
