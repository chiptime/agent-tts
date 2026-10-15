"""OpenCodePermissions: list pending dialogs and answer them.

Transport is injected (httpx.MockTransport), so no opencode server runs and
no test touches the user's live sessions.
"""

from __future__ import annotations

import httpx
import pytest

from herdr_brain.permissions import (
    OpenCodeError,
    OpenCodePermissions,
    REPLY_ALWAYS,
    REPLY_ONCE,
    REPLY_REJECT,
)

BASE = "http://127.0.0.1:4096"

PENDING = [
    {
        "id": "per_abc123",
        "sessionID": "ses_test0000session",
        "permission": "edit",
        "patterns": ["src/x.py"],
        "metadata": {},
        "always": ["src/*"],
    },
    {
        "id": "per_other",
        "sessionID": "ses_othersession",
        "permission": "bash",
        "patterns": ["rm -rf /"],
        "metadata": {},
        "always": [],
    },
]


def client_for(handler):
    return OpenCodePermissions(
        BASE, client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_list_pending_parses_requests():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=PENDING)

    pending = client_for(handler).list_pending()

    assert [p.request_id for p in pending] == ["per_abc123", "per_other"]
    assert pending[0].session_id == "ses_test0000session"
    assert pending[0].permission == "edit"
    assert pending[0].patterns == ("src/x.py",)
    assert seen["url"] == BASE + "/permission"


def test_list_pending_filters_by_session():
    def handler(request):
        return httpx.Response(200, json=PENDING)

    pending = client_for(handler).list_pending(session_id="ses_test0000session")

    assert [p.request_id for p in pending] == ["per_abc123"]


def test_list_pending_sends_no_scope_filter():
    """Only the exact session filter is applied, and it is applied here.

    A `directory` scope on the route is unverified against a live request,
    and a filter that can only remove results would hide a real dialog.
    """
    seen = {}

    def handler(request):
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=[])

    client_for(handler).list_pending(session_id="ses_test0000session")
    assert seen["params"] == {}


@pytest.mark.parametrize("status", [400, 401, 500])
def test_list_pending_raises_on_error_status(status):
    def handler(request):
        return httpx.Response(status, json={"error": "nope"})

    with pytest.raises(OpenCodeError):
        client_for(handler).list_pending()


def test_list_pending_raises_when_the_server_is_unreachable():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(OpenCodeError):
        client_for(handler).list_pending()


def test_list_pending_raises_on_a_non_array_payload():
    def handler(request):
        return httpx.Response(200, json={"unexpected": True})

    with pytest.raises(OpenCodeError):
        client_for(handler).list_pending()


@pytest.mark.parametrize("reply", [REPLY_ONCE, REPLY_ALWAYS, REPLY_REJECT])
def test_reply_posts_the_exact_contract(reply):
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json=True)

    assert client_for(handler).reply("per_abc123", reply) is True
    assert seen["method"] == "POST"
    assert seen["url"] == BASE + "/permission/per_abc123/reply"
    assert seen["body"] == '{"reply":"%s"}' % reply


def test_reply_reports_a_vanished_request_distinctly():
    def handler(request):
        return httpx.Response(
            404, json={"_tag": "PermissionNotFoundError", "requestID": "per_abc123", "message": "gone"}
        )

    with pytest.raises(OpenCodeError) as exc:
        client_for(handler).reply("per_abc123", REPLY_ONCE)

    assert "gone" in str(exc.value) or "404" in str(exc.value)


def test_reply_refuses_an_unknown_value_without_calling_http():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=True)

    with pytest.raises(OpenCodeError):
        client_for(handler).reply("per_abc123", "yes")

    assert calls == []


def test_reply_raises_when_unreachable():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(OpenCodeError):
        client_for(handler).reply("per_abc123", REPLY_ONCE)
