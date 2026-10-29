"""The lead-side crew client (S7): one CrewClient per peer, speaking
``/crew/v1/*`` over a pinned TLS context with the crew secret as
bearer, an injectable transport (no sockets in tests), explicit
timeouts, and per-peer health with exponential backoff.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from herdr_brain.crew.client import (
    CrewClient,
    CrewHttpResponse,
    CrewRemoteRefused,
    CrewUnreachable,
    PeerHealth,
)

FIXTURES = Path(__file__).parent / "fixtures" / "crew"
PEER_PEM = (FIXTURES / "peer.pem").read_text(encoding="utf-8")
SECRET = "crew-secret-1"


class FakeTransport:
    """Records every call; answers from a responder or a canned map."""

    def __init__(self, responder=None) -> None:
        self.calls: list[dict] = []
        self.responder = responder

    def request(self, method, url, *, headers, json_body=None, timeout_s=None):
        call = {
            "method": method,
            "url": url,
            "headers": dict(headers),
            "json_body": json_body,
            "timeout_s": timeout_s,
        }
        self.calls.append(call)
        if self.responder is not None:
            return self.responder(call)
        return CrewHttpResponse(200, {"ok": True})


def make_client(transport, **kwargs) -> CrewClient:
    defaults = dict(
        name="host-b",
        url="https://b.example:8747",
        cert_pem=PEER_PEM,
        secret_provider=lambda: SECRET,
        transport=transport,
    )
    defaults.update(kwargs)
    return CrewClient(**defaults)


# ── every call carries the two wire factors and a timeout ────────────────────


def test_reads_hit_the_crew_routes_with_bearer_and_timeout() -> None:
    transport = FakeTransport()
    client = make_client(transport, timeout_s=3.5)

    client.herd()
    client.view("p-1")
    client.history("p-1")
    client.mirror("p-2")

    seen = transport.calls
    assert [c["method"] for c in seen] == ["GET"] * 4
    assert seen[0]["url"].endswith("/crew/v1/herd")
    assert seen[1]["url"].endswith("/crew/v1/view?pane_id=p-1")
    assert seen[2]["url"].endswith("/crew/v1/history?pane_id=p-1")
    assert seen[3]["url"].endswith("/crew/v1/pane/p-2/mirror")
    for call in seen:
        assert call["headers"]["authorization"] == f"Bearer {SECRET}"
        assert call["timeout_s"] == 3.5


def test_writes_post_to_the_crew_routes_with_bodies() -> None:
    transport = FakeTransport()
    client = make_client(transport)

    client.reply("p-1", "hello there")
    client.keys("p-1", ["enter"])
    client.dialog_answer("p-1", {"option": 2})
    client.approval_resolve("g123", "approve")
    client.approval_current("p-1")

    seen = transport.calls
    assert [c["method"] for c in seen] == ["POST", "POST", "POST", "POST", "GET"]
    assert seen[0]["url"].endswith("/crew/v1/pane/p-1/reply")
    assert seen[0]["json_body"] == {"text": "hello there"}
    assert seen[1]["url"].endswith("/crew/v1/pane/p-1/keys")
    assert seen[1]["json_body"] == {"keys": ["enter"]}
    assert seen[2]["url"].endswith("/crew/v1/pane/p-1/dialog/answer")
    assert seen[2]["json_body"] == {"option": 2}
    assert seen[3]["url"].endswith("/crew/v1/approval/g123/action")
    assert seen[3]["json_body"] == {"decision": "approve"}
    assert seen[4]["url"].endswith("/crew/v1/approval/current?pane_id=p-1")
    for call in seen:
        assert call["headers"]["authorization"] == f"Bearer {SECRET}"


def test_pane_ids_are_path_safe() -> None:
    transport = FakeTransport()
    client = make_client(transport)
    # in a PATH segment a pane id must not be able to escape its slot
    client.reply("p/../../x", "hello")
    url = transport.calls[0]["url"]
    segment = url.rsplit("/crew/v1/pane/", 1)[1]
    assert segment.startswith("p%2F..%2F")  # slashes encoded, no traversal
    assert "?" not in segment.split("/reply")[0]


# ── the secret is pulled per call (rotation without a rebuild) ───────────────


def test_secret_provider_is_consulted_per_request() -> None:
    holder = {"secret": SECRET}
    transport = FakeTransport()
    client = make_client(transport, secret_provider=lambda: holder["secret"])

    client.herd()
    holder["secret"] = "crew-secret-2"
    client.herd()

    auths = [c["headers"]["authorization"] for c in transport.calls]
    assert auths == [f"Bearer {SECRET}", "Bearer crew-secret-2"]


# ── remote errors surface, unreachable is explicit ──────────────────────────


def test_error_statuses_raise_for_reads() -> None:
    transport = FakeTransport(responder=lambda call: CrewHttpResponse(500, {"detail": "boom"}))
    client = make_client(transport)
    with pytest.raises(CrewRemoteRefused):
        client.herd()


def test_approval_resolve_returns_the_raw_response_for_404_semantics() -> None:
    transport = FakeTransport(responder=lambda call: CrewHttpResponse(404, {"detail": "gone"}))
    client = make_client(transport)
    response = client.approval_resolve("dead-gate", "approve")
    assert response.status == 404  # the owner's verdict travels, not an exception


def test_transport_failure_raises_crew_unreachable_and_marks_health() -> None:
    def explode(call):
        raise OSError("connection refused")

    transport = FakeTransport(responder=explode)
    client = make_client(transport, now=lambda: 10_000.0)

    with pytest.raises(CrewUnreachable) as exc:
        client.herd()
    assert exc.value.peer == "host-b"
    assert client.health.state == "unreachable"
    # inside the backoff window the peer is not retried
    assert client.health.should_try(10_000.5) is False
    assert client.health.should_try(10_500.0) is True


def test_success_returns_health_to_fresh() -> None:
    transport = FakeTransport()
    client = make_client(transport, now=lambda: 10_000.0)
    client.health.record_failure(10_000.0)
    client.herd()
    assert client.health.state == "fresh"
    assert client.health.consecutive_failures == 0


# ── the backoff schedule itself ──────────────────────────────────────────────


def test_peer_health_backoff_doubles_and_caps() -> None:
    health = PeerHealth(backoff_base_s=5.0, backoff_cap_s=20.0)
    health.record_failure(now=0.0)
    assert health.retry_after == 5.0 and health.state == "unreachable"
    health.record_failure(now=0.0)
    assert health.retry_after == 10.0
    health.record_failure(now=0.0)
    assert health.retry_after == 20.0  # capped
    health.record_failure(now=0.0)
    assert health.retry_after == 20.0  # still capped
    assert health.should_try(19.9) is False
    assert health.should_try(20.1) is True
    health.record_success(now=21.0)
    assert health.state == "fresh"
    assert health.should_try(21.0) is True
