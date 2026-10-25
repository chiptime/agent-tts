"""OpenCodePermissions: list a session's dialogs and answer them.

Transport is injected (httpx.MockTransport), so no opencode server runs and
no test touches the user's live sessions. The shapes encoded here are the
ones opencode 1.18.32 actually serves: v2, session-scoped, and a reply that
answers 204 with NO body.
"""

from __future__ import annotations

import httpx
import pytest

from herdr_brain.permissions import (
    OpenCodeError,
    OpenCodeNotFound,
    OpenCodePermissions,
    REPLY_ALWAYS,
    REPLY_ONCE,
    REPLY_REJECT,
)

BASE = "http://127.0.0.1:4096"
SESSION = "ses_test0000session"

PENDING = {
    "data": [
        {
            "id": "per_abc123",
            "sessionID": SESSION,
            "action": "edit",
            "resources": ["src/x.py"],
        }
    ]
}


def client_for(handler):
    return OpenCodePermissions(
        BASE, client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_list_pending_uses_the_session_scoped_route():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=PENDING)

    pending = client_for(handler).list_pending(session_id=SESSION)

    assert seen["url"] == f"{BASE}/api/session/{SESSION}/permission"
    assert [p.request_id for p in pending] == ["per_abc123"]


def test_list_pending_maps_opencode_names_onto_the_phone_words():
    def handler(request):
        return httpx.Response(200, json=PENDING)

    pending = client_for(handler).list_pending(session_id=SESSION)[0]

    assert pending.session_id == SESSION
    assert pending.permission == "edit"
    assert pending.patterns == ("src/x.py",)


def test_list_pending_reports_no_dialogs():
    def handler(request):
        return httpx.Response(200, json={"data": []})

    assert client_for(handler).list_pending(session_id=SESSION) == []


@pytest.mark.parametrize("status", [400, 401, 500])
def test_list_pending_raises_on_error_status(status):
    def handler(request):
        return httpx.Response(status, json={"error": "nope"})

    with pytest.raises(OpenCodeError):
        client_for(handler).list_pending(session_id=SESSION)


def test_list_pending_raises_when_the_server_is_unreachable():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(OpenCodeError):
        client_for(handler).list_pending(session_id=SESSION)


@pytest.mark.parametrize("payload", [{"unexpected": True}, [], {"data": "nope"}])
def test_list_pending_raises_on_a_payload_that_is_not_a_list(payload):
    def handler(request):
        return httpx.Response(200, json=payload)

    with pytest.raises(OpenCodeError):
        client_for(handler).list_pending(session_id=SESSION)


@pytest.mark.parametrize("reply", [REPLY_ONCE, REPLY_ALWAYS, REPLY_REJECT])
def test_reply_posts_the_exact_contract_and_reads_204_as_success(reply):
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(204)

    assert client_for(handler).reply("per_abc123", reply, SESSION) is True
    assert seen["method"] == "POST"
    assert seen["url"] == f"{BASE}/api/session/{SESSION}/permission/per_abc123/reply"
    assert seen["body"] == '{"reply":"%s"}' % reply


def test_reply_also_accepts_a_200_with_a_body():
    def handler(request):
        return httpx.Response(200, json=True)

    assert client_for(handler).reply("per_abc123", REPLY_ONCE, SESSION) is True


def test_reply_maps_404_to_a_distinct_not_found():
    def handler(request):
        return httpx.Response(
            404,
            json={"_tag": "PermissionNotFoundError", "requestID": "per_abc123", "message": "gone"},
        )

    with pytest.raises(OpenCodeNotFound) as exc:
        client_for(handler).reply("per_abc123", REPLY_ONCE, SESSION)

    assert "gone" in str(exc.value)


def test_reply_refuses_an_unknown_value_without_calling_http():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(204)

    with pytest.raises(OpenCodeError):
        client_for(handler).reply("per_abc123", "yes", SESSION)

    assert calls == []


def test_reply_requires_the_session():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(204)

    with pytest.raises(OpenCodeError):
        client_for(handler).reply("per_abc123", REPLY_ONCE, "")

    assert calls == []


def test_reply_raises_when_unreachable():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(OpenCodeError):
        client_for(handler).reply("per_abc123", REPLY_ONCE, SESSION)
