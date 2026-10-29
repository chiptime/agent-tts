"""The lead-side crew client (S7): one per peer.

Each ``CrewClient`` speaks the peer's ``/crew/v1/*`` surface with the
two wire factors — a TLS context that trusts EXACTLY the peer's
pinned certificate, and the crew secret as a bearer pulled from a
provider on every call (so a rotation needs no rebuilt client).

The transport is injectable: production uses ``UrllibTransport`` over
the pinned context; tests inject a fake and no socket is ever opened.
A transport failure raises ``CrewUnreachable`` and lands in the
client's ``PeerHealth`` — exponential backoff with a cap, explicit
``unreachable`` state, recovery on the first success.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

from .tls import pinned_client_context

#: Per-request ceiling for a crew call. Crew traffic is interactive
#: (a proxy read in someone's /ask or an approval), so it fails fast.
DEFAULT_TIMEOUT_S = 8.0


class CrewUnreachable(Exception):
    """The peer could not be reached at all (network, TLS, timeout)."""

    def __init__(self, peer: str, reason: str):
        super().__init__(f"crew peer {peer} unreachable: {reason}")
        self.peer = peer
        self.reason = reason


class CrewRemoteRefused(Exception):
    """The peer answered with an error status. The status and body ride
    on the exception; nothing is retried — a refusal is an answer."""

    def __init__(self, peer: str, status: int, body: object):
        super().__init__(f"crew peer {peer} refused: {status}")
        self.peer = peer
        self.status = status
        self.body = body


@dataclass
class CrewHttpResponse:
    """The transport-neutral reply: a status and the parsed body (None
    when the body was not JSON)."""

    status: int
    body: object = None


class CrewTransport(Protocol):
    """The one seam between the client and the wire. ``request`` either
    returns a ``CrewHttpResponse`` or raises (OSError family for
    unreachable); it never interprets statuses."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict,
        json_body: Optional[object] = None,
        timeout_s: Optional[float] = None,
    ) -> CrewHttpResponse: ...


class UrllibTransport:
    """The default transport over stdlib urllib, pinned by the ssl
    context built at construction. Crew URLs are always https."""

    def __init__(self, ssl_context):
        self._ssl_context = ssl_context

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict,
        json_body: Optional[object] = None,
        timeout_s: Optional[float] = None,
    ) -> CrewHttpResponse:
        data = None
        send_headers = dict(headers)
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            send_headers.setdefault("content-type", "application/json")
        request = urllib.request.Request(url, data=data, method=method)
        for name, value in send_headers.items():
            request.add_header(name, value)
        try:
            with urllib.request.urlopen(
                request, context=self._ssl_context, timeout=timeout_s
            ) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:  # an answer, not a failure
            raw = exc.read()
            status = exc.code
        return CrewHttpResponse(status, _parse_json(raw))


def _parse_json(raw: bytes) -> object:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


@dataclass
class PeerHealth:
    """Per-peer reachability state: explicit, small, and honest.

    One failure marks the peer ``unreachable`` — it is then skipped
    until ``retry_after``, so a sweep of reads does not hang on a dead
    host. The backoff is exponential with a cap; the first success
    clears everything. This is bookkeeping, not circuit-breaking: any
    call that must go through (an approval) still tries.
    """

    backoff_base_s: float = 5.0
    backoff_cap_s: float = 300.0
    state: str = "fresh"
    consecutive_failures: int = 0
    retry_after: Optional[float] = None
    last_success: Optional[float] = None

    def should_try(self, now: float) -> bool:
        if self.state == "fresh":
            return True
        return self.retry_after is None or now >= self.retry_after

    def record_success(self, now: float) -> None:
        self.state = "fresh"
        self.consecutive_failures = 0
        self.retry_after = None
        self.last_success = now

    def record_failure(self, now: float) -> None:
        self.consecutive_failures += 1
        self.state = "unreachable"
        backoff = min(
            self.backoff_base_s * (2 ** (self.consecutive_failures - 1)),
            self.backoff_cap_s,
        )
        self.retry_after = now + backoff


def _quote_path_segment(value: str) -> str:
    import urllib.parse

    return urllib.parse.quote(str(value), safe="")


def _encode_query(query: Optional[dict]) -> str:
    import urllib.parse

    if not query:
        return ""
    pairs = [
        (name, str(value))
        for name, value in query.items()
        if value is not None
    ]
    return "?" + urllib.parse.urlencode(pairs)


class CrewClient:
    """One peer's ``/crew/v1/*`` surface, pinned and secret-bearing."""

    def __init__(
        self,
        name: str,
        url: str,
        cert_pem: str,
        secret_provider: Callable[[], str],
        *,
        transport: Optional[CrewTransport] = None,
        ssl_context=None,
        client_cert=None,
        client_key=None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        now: Optional[Callable[[], float]] = None,
    ):
        self.name = name
        self.url = url.rstrip("/")
        self.cert_pem = cert_pem
        self._secret = secret_provider
        self._timeout_s = timeout_s
        self._now = now or time.time
        self.health = PeerHealth()
        if transport is not None:
            self._transport = transport
        else:
            context = ssl_context or pinned_client_context(
                cert_pem, client_cert=client_cert, client_key=client_key
            )
            self._transport = UrllibTransport(context)

    # -- the wire ---------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[dict] = None,
        json_body: Optional[object] = None,
    ) -> CrewHttpResponse:
        url = f"{self.url}{path}{_encode_query(query)}"
        headers = {"authorization": f"Bearer {self._secret()}"}
        try:
            response = self._transport.request(
                method,
                url,
                headers=headers,
                json_body=json_body,
                timeout_s=self._timeout_s,
            )
        except CrewUnreachable:
            raise
        except (OSError, ValueError) as exc:  # refused, reset, timeout, bad url
            self.health.record_failure(self._now())
            raise CrewUnreachable(self.name, str(exc)) from exc
        self.health.record_success(self._now())
        return response

    def _call(self, method: str, path: str, **kwargs) -> object:
        """A convenience call: 2xx returns the parsed body, anything
        else raises ``CrewRemoteRefused``."""
        response = self._request(method, path, **kwargs)
        if response.status >= 400:
            raise CrewRemoteRefused(self.name, response.status, response.body)
        return response.body

    # -- proxied reads ─────────────────────────────────────────────────────

    def herd(self) -> object:
        return self._call("GET", "/crew/v1/herd")

    def view(self, pane_id: str) -> object:
        return self._call("GET", "/crew/v1/view", query={"pane_id": pane_id})

    def history(self, pane_id: str, **query) -> object:
        return self._call(
            "GET", "/crew/v1/history", query={"pane_id": pane_id, **query}
        )

    def mirror(self, pane_id: str, **query) -> object:
        segment = _quote_path_segment(pane_id)
        return self._call("GET", f"/crew/v1/pane/{segment}/mirror", query=query or None)

    # -- proxied writes ────────────────────────────────────────────────────

    def reply(self, pane_id: str, text: str) -> object:
        segment = _quote_path_segment(pane_id)
        return self._call(
            "POST", f"/crew/v1/pane/{segment}/reply", json_body={"text": text}
        )

    def keys(self, pane_id: str, keys: list) -> object:
        segment = _quote_path_segment(pane_id)
        return self._call(
            "POST", f"/crew/v1/pane/{segment}/keys", json_body={"keys": keys}
        )

    def dialog_answer(self, pane_id: str, payload: dict) -> object:
        segment = _quote_path_segment(pane_id)
        return self._call(
            "POST", f"/crew/v1/pane/{segment}/dialog/answer", json_body=payload
        )

    # -- approvals: forwarded BY the lead, owned by this peer ─────────────

    def approval_current(self, pane_id: Optional[str] = None) -> object:
        query = {"pane_id": pane_id} if pane_id else None
        return self._call("GET", "/crew/v1/approval/current", query=query)

    def approval_resolve(self, gate_id: str, decision: str) -> CrewHttpResponse:
        """Raw on purpose: the caller (the gate router) needs the
        owner's verdict status — a 404 for a dead gate is an ANSWER
        that must travel back, not an exception to swallow."""
        segment = _quote_path_segment(gate_id)
        return self._request(
            "POST",
            f"/crew/v1/approval/{segment}/action",
            json_body={"decision": decision},
        )
