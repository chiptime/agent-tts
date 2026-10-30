"""Authenticated identity must travel outside the message body."""

import pytest

from herdr_brain.crew.client import CrewClient, CrewHttpResponse
from tests.test_crew_service import (
    PEER_DER, SECRET, getter, headers, make_service, roster, state_dir,
)


@pytest.mark.parametrize("op", ["who", "send", "status", "cancel"])
def test_message_wire_stamps_authenticated_member(roster, op):
    seen = []
    service = make_service(roster, handlers={
        f"msg_{op}": lambda *, member, payload: seen.append((member, payload)) or {"ok": True}
    })
    body = {"member": "forged-host", "body": "hello"}
    assert service.handle(
        "POST", f"/crew/v1/msg/{op}", body=body,
        headers_get=getter(headers(SECRET)), peer_cert_der=PEER_DER,
    ) == (200, {"ok": True})
    assert seen == [("host-b", body)]
    for auth, cert in [(headers(None), PEER_DER), (headers(SECRET), None)]:
        assert service.handle(
            "POST", f"/crew/v1/msg/{op}", body=body,
            headers_get=getter(auth), peer_cert_der=cert,
        ) == (401, {"detail": "unauthorized"})
    assert len(seen) == 1


def test_message_client_uses_existing_pinned_transport():
    calls = []

    class Transport:
        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            return CrewHttpResponse(200, {"ok": True})

    client = CrewClient("b", "https://b", "unused", lambda: SECRET, transport=Transport())
    assert client.message("send", {"body": "hello"}) == {"ok": True}
    assert calls[0][0:2] == ("POST", "https://b/crew/v1/msg/send")
    assert calls[0][2]["headers"] == {"authorization": f"Bearer {SECRET}"}
    assert calls[0][2]["json_body"] == {"body": "hello"}
