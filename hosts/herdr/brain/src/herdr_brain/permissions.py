"""OpenCode permission dialogs: list what is pending and answer it.

An agent waiting at an approval dialog cannot be reached with
``herdr agent prompt`` (herdr refuses it with ``agent_blocked`` before
writing any input), so the answer travels through opencode's own permission
API instead.

Which surface is LIVE was settled by experiment, not by reading the spec
(opencode 1.18.32): a real pending request created with
``effect: "ask"`` did NOT appear in the legacy ``GET /permission`` list at
all, while the session-scoped v2 routes listed it and answered it. So this
client uses v2 only:

    GET  /api/session/{sessionID}/permission            -> {"data": [...]}
    POST /api/session/{sessionID}/permission/{id}/reply -> 204, NO body

The wire names differ from the user-facing ones and are mapped here:
opencode says ``action``/``resources``, the phone says permission/patterns.
Note the reply answers with **204 No Content**, so success is the status
code and never a JSON body — reading a body there would report failure on a
perfectly good answer.

This module holds transport only. It is deliberately NOT reachable from the
LLM: the same capability as a model tool would let an agent approve its own
permission request, which is exactly what the dialog exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import httpx

REPLY_ONCE = "once"
REPLY_ALWAYS = "always"
REPLY_REJECT = "reject"
REPLIES = (REPLY_ONCE, REPLY_ALWAYS, REPLY_REJECT)

DEFAULT_TIMEOUT_S = 10.0
#: How much of an error body ever reaches a log line or an HTTP detail.
ERROR_EXCERPT_CHARS = 200


class OpenCodeError(RuntimeError):
    """An opencode permission call could not be completed.

    Covers transport failure, a non-2xx status and a payload that does not
    match the documented shape: never silently degraded into "no pending
    permissions", which would read as "nothing to answer".
    """


class OpenCodeNotFound(OpenCodeError):
    """The request is gone: already answered elsewhere, or expired.

    Kept distinct from a transport failure so the phone learns that its tap
    came too late instead of "opencode is unreachable".
    """


@dataclass(frozen=True)
class PermissionRequest:
    """One dialog waiting for an answer.

    ``permission``/``patterns`` are the phone's words for opencode's
    ``action``/``resources``.
    """

    request_id: str
    session_id: str
    permission: str
    patterns: tuple


class OpenCodePermissions:
    """Thin client over the opencode server's permission routes.

    ``client`` is injectable so tests drive a MockTransport and never touch
    a live opencode server.
    """

    def __init__(
        self,
        base_url: str,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        client: Optional[httpx.Client] = None,
    ):
        self._base = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout_s)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # -- reads --------------------------------------------------------------

    def list_pending(self, session_id: str) -> list[PermissionRequest]:
        """The session's pending dialogs.

        Session-scoped on purpose: the legacy global list is not the live
        registry (see the module docstring), and a pane always knows its
        session.
        """
        response = self._request("GET", f"/api/session/{session_id}/permission")
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise OpenCodeError(
                "opencode permission list is not shaped like {data: [...]}"
            )
        return [_to_request(item) for item in payload["data"]]

    # -- writes -------------------------------------------------------------

    def reply(self, request_id: str, reply: str, session_id: str) -> bool:
        """Answers one dialog. ``reply`` is the exact contract value.

        Validated locally before any request: an unknown word must never
        reach opencode as a guess. The route answers **204 No Content**, so
        a 2xx with no body IS the success signal.
        """
        if reply not in REPLIES:
            raise OpenCodeError(
                f"reply must be one of {', '.join(REPLIES)}, got {reply!r}"
            )
        if not request_id or not session_id:
            raise OpenCodeError("request_id and session_id are both required")
        self._request(
            "POST",
            f"/api/session/{session_id}/permission/{request_id}/reply",
            json={"reply": reply},
        )
        return True

    # -- plumbing -----------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        url = f"{self._base}{path}"
        try:
            response = self._client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise OpenCodeError(f"opencode {method} {path} failed: {exc}") from exc
        if response.status_code == 404:
            raise OpenCodeNotFound(
                f"opencode {method} {path} returned 404: {_error_detail(response)}"
            )
        if response.status_code >= 400:
            raise OpenCodeError(
                f"opencode {method} {path} returned {response.status_code}:"
                f" {_error_detail(response)}"
            )
        return response


def _error_detail(response: httpx.Response) -> str:
    """Best-effort message from an error body, bounded and never raw-dumped."""
    try:
        payload = response.json()
    except ValueError:
        text = (response.text or "").strip()
        return text[:ERROR_EXCERPT_CHARS]
    if isinstance(payload, dict):
        for key in ("message", "error", "_tag"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value[:ERROR_EXCERPT_CHARS]
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("message"), str):
            return data["message"][:ERROR_EXCERPT_CHARS]
    return response.text[:ERROR_EXCERPT_CHARS]


def _to_request(item: object) -> PermissionRequest:
    """Rebuilds one request, failing loud on a payload that is not shaped
    like opencode's permission entry."""
    if not isinstance(item, dict):
        raise OpenCodeError(
            f"opencode permission entry is {type(item).__name__}, expected an object"
        )
    try:
        return PermissionRequest(
            request_id=item["id"],
            session_id=item["sessionID"],
            permission=item.get("action", ""),
            patterns=tuple(item.get("resources") or ()),
        )
    except (KeyError, TypeError) as exc:
        raise OpenCodeError(f"opencode permission entry is malformed: {exc}") from exc
