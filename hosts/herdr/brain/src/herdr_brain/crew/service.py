"""The peer-side crew surface: a framework-free ``/crew/v1/*``
dispatcher the FastAPI router wraps (task S7).

Every route except enrolment sits behind the two-factor check in
``auth.check_crew_auth``; the FastAPI layer extracts the peer
certificate's DER from the TLS scope and hands it in. A corrupt
roster fails CLOSED: the dispatcher answers with the same uniform 401
as any missing factor. Handler results travel as ``(status, payload)``
tuples or plain payloads (200); a crashing handler is a clean 500
whose text never crosses the wire.

Route table (the contract between crew versions across machines):

    POST /crew/v1/enrol                       join token (the only
                                              route without the two
                                              factors — the token IS
                                              the authentication)
    GET  /crew/v1/herd
    GET  /crew/v1/view?pane_id=
    GET  /crew/v1/history?pane_id=&…
    GET  /crew/v1/pane/{pane_id}/mirror?…
    POST /crew/v1/pane/{pane_id}/reply        {"text"}
    POST /crew/v1/pane/{pane_id}/keys         {"keys"}
    POST /crew/v1/pane/{pane_id}/dialog/answer  {…}
    GET  /crew/v1/approval/current?pane_id=
    POST /crew/v1/approval/{gate_id}/action   {"decision"}
    POST /crew/v1/msg/{who,send,status,cancel}  message payload
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from .auth import refusal_response
from .enrol import JoinTokens
from .roster import CrewRoster

#: Handler keys the wiring parent provides (each maps to one route).
HANDLER_KEYS = (
    "herd",
    "view",
    "history",
    "mirror",
    "reply",
    "keys",
    "dialog_answer",
    "approval_current",
    "approval_action",
    "msg_who",
    "msg_send",
    "msg_status",
    "msg_cancel",
)

_NOT_FOUND = (404, {"detail": "not found"})
_METHOD_NOT_ALLOWED = (405, {"detail": "method not allowed"})
_BAD_REQUEST = (400, {"detail": "bad request"})
_HANDLER_ERROR = (500, {"detail": "error"})


def _routes() -> dict:
    """(method, template) → (handler key, kwargs kind). Templates use
    ``{name}`` segments; ``pane``/``gate_id`` become handler kwargs."""
    return {
        ("POST", "/crew/v1/enrol"): ("enrol", "enrol"),
        ("GET", "/crew/v1/herd"): ("herd", "plain"),
        ("GET", "/crew/v1/view"): ("view", "pane_query"),
        ("GET", "/crew/v1/history"): ("history", "pane_query_rest"),
        ("GET", "/crew/v1/pane/{pane}/mirror"): ("mirror", "pane_path_rest"),
        ("POST", "/crew/v1/pane/{pane}/reply"): ("reply", "pane_path_body"),
        ("POST", "/crew/v1/pane/{pane}/keys"): ("keys", "pane_path_body"),
        ("POST", "/crew/v1/pane/{pane}/dialog/answer"): ("dialog_answer", "pane_path_body"),
        ("GET", "/crew/v1/approval/current"): ("approval_current", "pane_query_rest"),
        ("POST", "/crew/v1/approval/{gate_id}/action"): ("approval_action", "gate_action"),
        **{("POST", f"/crew/v1/msg/{op}"): (f"msg_{op}", "member_body")
           for op in ("who", "send", "status", "cancel")},
    }


class CrewService:
    """Dispatches one crew request to a handler, enforcing the gate.

    ``roster_loader`` re-reads the roster per request (the pairing
    discipline: rotations and revocations land without a restart) and
    may raise ``CrewRosterCorrupt`` — which this dispatcher converts
    to the uniform refusal, because a roster that cannot be read
    authorizes nobody.
    """

    def __init__(
        self,
        roster_loader: Callable[[], CrewRoster],
        handlers: dict,
        *,
        join_tokens: Optional[JoinTokens] = None,
        now: Optional[Callable[[], float]] = None,
    ):
        self._roster_loader = roster_loader
        self._handlers = handlers
        self._join_tokens = join_tokens
        self._now = now or time.time
        self._routes = _routes()

    def handle(
        self,
        method: str,
        path: str,
        query: Optional[dict] = None,
        body: Optional[dict] = None,
        headers_get: Optional[Callable[[str], Optional[str]]] = None,
        peer_cert_der: Optional[bytes] = None,
    ) -> tuple:
        """One request in, one ``(status, payload)`` out."""
        headers_get = headers_get or (lambda name: None)
        query = query or {}
        body = body if isinstance(body, dict) else {}

        match = self._match(method, path)
        if isinstance(match, tuple) and match[0] == "enrol":
            return self._enrol(body, headers_get)

        # everything else sits behind the two factors — including the
        # not-found answers, so an unauthenticated caller cannot even
        # enumerate the surface
        roster = self._load_roster()
        if roster is None:
            return refusal_response()
        from .auth import check_crew_auth

        member = check_crew_auth(headers_get, peer_cert_der, roster, now=self._now())
        if member is None:
            return refusal_response()

        if not isinstance(match, tuple):
            return _METHOD_NOT_ALLOWED if match == "bad-method" else _NOT_FOUND
        return self._dispatch(match, query, body, member)

    # -- routing internals ------------------------------------------------

    def _match(self, method: str, path: str):
        """→ (handler_key, kwargs_kind, {path kwargs}) for a route,
        the string ``"bad-method"`` when the path exists under another
        method, or None when the path is no crew route at all."""
        path_matched = False
        for (route_method, template), spec in self._routes.items():
            kwargs = _match_path(template, path)
            if kwargs is None:
                continue
            path_matched = True
            if route_method == method:
                return (spec[0], spec[1], kwargs)
        return "bad-method" if path_matched else None

    def _load_roster(self) -> Optional[CrewRoster]:
        try:
            return self._roster_loader()
        except Exception:
            return None  # corrupt or unreadable: fail closed

    def _enrol(self, body: dict, headers_get) -> tuple:
        if self._join_tokens is None:
            return _NOT_FOUND
        from ..pairing import bearer_token

        roster = self._load_roster()
        if roster is None:
            return refusal_response()  # a roster we cannot read enrols nobody
        token = bearer_token(headers_get) or ""
        name = body.get("name")
        url = body.get("url")
        cert_pem = body.get("cert_pem")
        reason, entry = self._join_tokens.present(
            token,
            name=name if isinstance(name, str) else "",
            url=url if isinstance(url, str) else "",
            cert_pem=cert_pem if isinstance(cert_pem, str) else "",
            roster=roster,
        )
        if reason in {"bad-name", "bad-url", "bad-cert"}:
            return 400, {"detail": reason}
        if reason != "enrolled" or entry is None:
            return refusal_response()  # token-side failures: uniform
        return 200, {"member": name, "entry": entry}

    def _dispatch(self, match, query: dict, body: dict, member: str) -> tuple:
        handler_key, kwargs_kind, kwargs = match
        handler = self._handlers.get(handler_key)
        if not callable(handler):
            return _NOT_FOUND

        call_kwargs = dict(kwargs)
        try:
            if kwargs_kind == "member_body":
                # Transport identity is never taken from JSON, including
                # when a body contains a field named 'member'.
                result = handler(member=member, payload=body)
            elif kwargs_kind == "plain":
                result = handler()
            elif kwargs_kind == "pane_query":
                call_kwargs["pane_id"] = query.get("pane_id")
                result = handler(**call_kwargs)
            elif kwargs_kind == "pane_query_rest":
                rest = {k: v for k, v in query.items() if k != "pane_id"}
                call_kwargs["pane_id"] = query.get("pane_id")
                result = handler(**call_kwargs, **rest)
            elif kwargs_kind == "pane_path_rest":
                rest = dict(query)
                call_kwargs["pane_id"] = call_kwargs.pop("pane", None)
                result = handler(**call_kwargs, **rest)
            elif kwargs_kind == "pane_path_body":
                call_kwargs["pane_id"] = call_kwargs.pop("pane", None)
                result = handler(**call_kwargs, **body)
            elif kwargs_kind == "gate_action":
                decision = body.get("decision")
                if not isinstance(decision, str) or not decision:
                    return 400, {"detail": "decision is required"}
                call_kwargs["decision"] = decision
                result = handler(**call_kwargs)
            else:  # pragma: no cover — internal table only
                return _NOT_FOUND
        except TypeError as exc:
            return 400, {"detail": f"bad request: {exc}"}
        except Exception:
            return _HANDLER_ERROR

        if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], int):
            return int(result[0]), result[1]
        return 200, result


def _match_path(template: str, path: str) -> Optional[dict]:
    """Template match with ``{var}`` capture. Exact, segment-wise, no
    globs: a crew path is a contract, not a pattern."""
    template_parts = template.strip("/").split("/")
    path_parts = path.strip("/").split("/")
    if len(template_parts) != len(path_parts):
        return None
    kwargs: dict[str, str] = {}
    for template_part, path_part in zip(template_parts, path_parts):
        if template_part.startswith("{") and template_part.endswith("}"):
            if not path_part:
                return None
            kwargs[template_part[1:-1]] = path_part
        elif template_part != path_part:
            return None
    return kwargs
