"""Crew runtime wiring (S7, doc 10): the daemon-side glue.

The crew core (``herdr_brain.crew``) is framework-free on purpose; this
module is the thin adapter that connects it to the running brain:

* ``CrewGateway`` — the LEAD side: pane routing (``<host>:<pane>``),
  the merged herd, and remote gate forwarding through ``GateRouter``.
  It re-reads the roster per use, so rotations, revocations and manual
  promotions land without a restart.
* ``create_crew_app`` — the PEER side: one FastAPI adapter that hands
  every ``/crew/v1/*`` request (method, path, query, body, headers and
  the TLS peer certificate DER) to ``CrewService.handle``.
* ``listener_kwargs`` — the separate TLS listener's uvicorn arguments,
  built ONLY when the roster says this host is in a crew. A solo brain
  starts nothing: zero listeners, zero bytes of crew on the wire.

The crew secret never lives in the roster (only its hash). The daemon
reads it from ``HERDR_BRAIN_CREW_SECRET`` or a 0600
``<state_dir>/crew-secret`` file the operator writes once at
``crew init``/``join``; without it the lead cannot call its peers and
they surface as unreachable — loud, never guessed.
"""

from __future__ import annotations

import json
import logging
import os
import ssl
import time
from pathlib import Path
from typing import Callable, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .crew.aggregate import PeerHerd, merge_herds, split_pane_id
from .crew.client import CrewClient, CrewRemoteRefused, CrewUnreachable
from .crew.gates import GateIndex, GateRouter
from .crew.roster import (
    CREW_FILENAME,
    ROLE_LEAD,
    ROLE_PEER,
    CrewRoster,
    CrewRosterCorrupt,
)
from .crew.service import CrewService
from .crew.tls import ensure_host_certificate

logger = logging.getLogger("herdr_brain.crew_runtime")

#: The crew's own TLS port (doc 10 question 2: a dedicated second
#: listener — the pin lives in the TLS layer, not a path prefix).
DEFAULT_CREW_PORT = 8747
ENV_CREW_PORT = "HERDR_BRAIN_CREW_PORT"
ENV_CREW_HOST = "HERDR_BRAIN_CREW_HOST"
#: The crew secret for the DAEMON (the roster stores only its hash).
#: Value form; when unset the 0600 file below is read instead.
ENV_CREW_SECRET = "HERDR_BRAIN_CREW_SECRET"
#: Default bind for the crew listener. Loopback keeps a laptop-safe
#: default; a crew lead/peer sets the tailnet interface explicitly.
DEFAULT_CREW_HOST = "127.0.0.1"

CREW_SECRET_FILENAME = "crew-secret"
CREW_TLS_DIRNAME = "crew-tls"


# ── roster + secret plumbing ────────────────────────────────────────────────


def load_roster(state_dir) -> CrewRoster:
    """The roster as it is on disk. An ABSENT file is a fresh solo
    roster; a corrupt one raises (the caller decides how loudly)."""
    return CrewRoster.load(Path(state_dir))


def crew_secret_provider(
    state_dir, environ: Optional[dict] = None
) -> Callable[[], str]:
    """``() -> crew secret``: env value first, then the 0600 file the
    operator wrote at enrolment. An empty secret is honest — the lead
    simply cannot authenticate to its peers."""

    def _provider() -> str:
        env = os.environ if environ is None else environ
        from_env = str(env.get(ENV_CREW_SECRET, "")).strip()
        if from_env:
            return from_env
        path = Path(state_dir) / CREW_SECRET_FILENAME
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    return _provider


# ── the lead-side gateway ───────────────────────────────────────────────────


class CrewGateway:
    """Lead-side crew routing over the enrolled peers.

    Kept deliberately small: it routes pane ids, merges herds (dead
    peers stay EXPLICIT, never silently gone), and forwards remote
    gate decisions through ``GateRouter`` — a remote gate is NEVER
    resolved against the local store.
    """

    def __init__(
        self,
        state_dir,
        secret_provider: Optional[Callable[[], str]] = None,
        transport_factory: Optional[Callable] = None,
        now: Optional[Callable[[], float]] = None,
    ):
        self._state_dir = Path(state_dir)
        self._secret_provider = secret_provider or crew_secret_provider(
            self._state_dir
        )
        # Test seam: (peer_name, url, cert_pem) -> CrewTransport.
        self._transport_factory = transport_factory
        self._now = now or time.time
        self._clients: dict[str, CrewClient] = {}
        self._gate_index = GateIndex()

    # -- roster ------------------------------------------------------------

    def roster(self) -> Optional[CrewRoster]:
        """The live roster, or None when unreadable. A corrupt roster
        disables crew ROUTING loudly (the local brain keeps serving);
        the crew LISTENER keeps failing every request closed through
        the service's own roster loader."""
        try:
            return load_roster(self._state_dir)
        except CrewRosterCorrupt as exc:
            logger.error("crew roster unreadable — crew routing off: %s", exc)
            return None

    def self_name(self) -> str:
        roster = self.roster()
        return (roster.self_name if roster is not None else None) or "local"

    def peer_names(self) -> set:
        roster = self.roster()
        return set(roster.peers) if roster is not None else set()

    # -- clients -----------------------------------------------------------

    def client_for(self, peer: str) -> Optional[CrewClient]:
        """The pinned client for an enrolled peer, or None (not
        enrolled / no certificate material / no roster)."""
        roster = self.roster()
        if roster is None:
            return None
        entry = roster.peer(peer)
        if entry is None:
            return None
        cert_pem = roster.peer_cert_pem(peer)
        if not cert_pem:
            logger.error("crew peer %s has no certificate material", peer)
            return None
        client = self._clients.get(peer)
        if client is None:
            kwargs: dict = {"secret_provider": self._secret_provider, "now": self._now}
            if self._transport_factory is not None:
                kwargs["transport"] = self._transport_factory(peer, entry["url"], cert_pem)
            client = CrewClient(peer, entry["url"], cert_pem, **kwargs)
            self._clients[peer] = client
        return client

    # -- pane routing ------------------------------------------------------

    def route_pane(self, pane_id) -> Optional[str]:
        """The enrolled peer a pane id routes to, or None when the id
        is local (bare, or host-shaped but naming no member — the
        caller's local surface then answers it honestly, including its
        unknown-pane 404: the host prefix is NEVER stripped)."""
        roster = self.roster()
        if roster is None:
            return None
        host, _rest = split_pane_id(pane_id)
        if host is not None and roster.peer(host) is not None:
            return host
        return None

    # -- the merged herd ----------------------------------------------------

    def merged_herd(self, local_herd: Optional[list]) -> dict:
        """Local truth plus the peers' herds (doc 10): remote panes
        gain ``host``, local panes keep the single-host shape, and a
        peer that cannot be reached appears as ``unreachable`` instead
        of vanishing its agents silently. Peers in backoff are not
        re-probed this sweep and stay listed unreachable."""
        roster = self.roster()
        contributions: list = []
        for name in (roster.peers if roster is not None else {}):
            herd = None
            client = self.client_for(name)
            if client is not None and client.health.should_try(self._now()):
                try:
                    herd = client.herd()
                except (CrewUnreachable, CrewRemoteRefused) as exc:
                    logger.warning("crew herd sweep: peer %s unreachable: %s", name, exc)
            contributions.append(PeerHerd(name, herd))
        return merge_herds(local_herd, contributions)

    # -- remote gates -------------------------------------------------------

    @property
    def gate_index(self) -> GateIndex:
        return self._gate_index

    def remote_current_gate(self) -> Optional[tuple]:
        """The first fresh peer reporting a live gate:
        ``(approval_payload, host)``. Records ownership in the gate
        index so the decision routes can forward later. Approvals are
        ALWAYS tried (doc 10: they must not be dropped) — backoff does
        not apply to this probe."""
        roster = self.roster()
        if roster is None:
            return None
        for name in roster.peers:
            client = self.client_for(name)
            if client is None:
                continue
            try:
                body = client.approval_current()
            except (CrewUnreachable, CrewRemoteRefused) as exc:
                logger.warning("crew approval probe: peer %s unreachable: %s", name, exc)
                continue
            approval = body.get("approval") if isinstance(body, dict) else None
            if isinstance(approval, dict) and approval.get("gate_id"):
                self._gate_index.record(approval["gate_id"], name)
                return approval, name
        return None

    def forward_gate(self, gate_id: str, decision: str) -> Optional[object]:
        """A known-REMOTE gate decision forwarded to its owner through
        ``GateRouter``, or None when the gate is not a known remote one
        (the caller's LOCAL handling continues untouched). The owner's
        answer travels back verbatim — a 404 from the owner IS the
        answer; nothing is retried, nothing resolves locally."""
        owner = self._gate_index.owner_of(gate_id)
        if owner is None:
            return None

        def _local_resolve(gid: str, _decision: str) -> tuple:
            # A remote gate must never resolve against this store: a
            # store that never held it would "apply" a decision to
            # nothing (gates.py invariant, kept explicit here).
            return 404, {"detail": "gate dead"}

        router = GateRouter(
            this_host=self.self_name(),
            local_resolve=_local_resolve,
            forward=self._forward_decision,
            known_members=lambda: {self.self_name(), *self.peer_names()},
        )
        result = router.resolve(gate_id, decision, owner_host=owner)
        if result.applied:
            self._gate_index.forget(gate_id)
        return result

    def _forward_decision(self, peer: str, gate_id: str, decision: str) -> tuple:
        client = self.client_for(peer)
        if client is None:
            return 503, {"detail": f"crew peer {peer} is not reachable"}
        response = client.approval_resolve(gate_id, decision)
        return response.status, response.body


# ── the peer-side adapter ───────────────────────────────────────────────────


def peer_cert_der(scope: dict) -> Optional[bytes]:
    """The TLS peer certificate's DER bytes from the ASGI scope, or
    None. uvicorn exposes the SSL object under ``scope["ssl_object"]``
    on TLS connections; without it the request simply authenticates as
    nobody and the two-factor check refuses (fail closed)."""
    ssl_obj = scope.get("ssl_object")
    if ssl_obj is None:
        return None
    try:
        der = ssl_obj.getpeercert(binary_form=True)
    except Exception:  # noqa: BLE001 — a broken transport answers nobody
        return None
    return der if der else None


def create_crew_app(
    handlers: dict,
    roster_loader: Callable[[], CrewRoster],
    *,
    join_tokens=None,
    now: Optional[Callable[[], float]] = None,
    service: Optional[CrewService] = None,
) -> FastAPI:
    """The FastAPI adapter over ``CrewService.handle``.

    Mechanical by design (the crew README contract): one catch-all
    route per method extracts the request's five facts — method, path,
    query, JSON body, header getter — plus the peer certificate DER,
    and returns the handler's ``(status, payload)`` as JSON. The
    uniform 401s, the not-found answers and handler errors all come
    from the service; this layer adds nothing.
    """
    crew_service = service if service is not None else CrewService(
        roster_loader, handlers, join_tokens=join_tokens, now=now
    )
    app = FastAPI(title="herdr-brain-crew", docs_url=None, redoc_url=None)

    @app.api_route(
        "/crew/v1/{rest:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    )
    async def crew_route(rest: str, request: Request):  # noqa: ANN202
        body: Optional[dict] = None
        if request.method in ("POST", "PUT", "PATCH"):
            raw = await request.body()
            if raw:
                try:
                    parsed = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    parsed = None
                body = parsed if isinstance(parsed, dict) else {}
        status, payload = crew_service.handle(
            request.method,
            request.url.path,
            dict(request.query_params),
            body,
            request.headers.get,
            peer_cert_der(request.scope),
        )
        return JSONResponse(payload, status_code=status)

    return app


# ── the listener ────────────────────────────────────────────────────────────


def listener_should_start(roster: Optional[CrewRoster]) -> bool:
    """The daemon starts the crew listener ONLY for a roster that
    declares a crew (lead or peer). Solo — the default — starts
    nothing."""
    return roster is not None and roster.has_crew


def _ca_bundle(state_dir: Path, roster: CrewRoster) -> Optional[Path]:
    """The enrolled members' certificates concatenated into one trust
    bundle for the listener (regenerated per boot; public material)."""
    certs_dir = state_dir / "crew-certs"
    pems = []
    for name in roster.peers:
        pem = roster.peer_cert_pem(name)
        if pem:
            pems.append(pem.strip() + "\n")
    if not pems:
        return None
    bundle = state_dir / CREW_TLS_DIRNAME / "ca-bundle.pem"
    bundle.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    bundle.write_text("".join(pems), encoding="utf-8")
    try:
        os.chmod(bundle, 0o600)
    except OSError:
        pass
    return bundle


def listener_kwargs(
    roster: Optional[CrewRoster],
    state_dir,
    host: str = DEFAULT_CREW_HOST,
    port: int = DEFAULT_CREW_PORT,
) -> Optional[dict]:
    """The uvicorn kwargs of the crew listener, or None when it must
    not start (solo, or TLS material the operator must fix first).

    Handshake stance (crew/README): a PEER requires the client
    certificate against the enrolled bundle — the only expected client
    is the lead, pinned at join. The LEAD keeps the enrolment window
    open (CERT_OPTIONAL): a candidate's certificate is by definition
    not enrolled yet, and every route except ``/crew/v1/enrol`` still
    fails closed in the application-layer two-factor check.
    """
    if not listener_should_start(roster):
        return None
    roster = roster  # type: ignore[assignment]
    state_dir = Path(state_dir)
    try:
        cert_path, key_path, _pin = ensure_host_certificate(
            state_dir / CREW_TLS_DIRNAME, roster.self_name or "brain"
        )
    except Exception as exc:  # noqa: BLE001 — CrewTlsError and friends
        logger.error(
            "crew listener OFF — host certificate unavailable (see the "
            "crew CLI for the exact openssl command): %s",
            exc,
        )
        return None
    kwargs: dict = {
        "host": host,
        "port": port,
        "log_config": None,
        "proxy_headers": False,
        "ssl_certfile": str(cert_path),
        "ssl_keyfile": str(key_path),
    }
    bundle = _ca_bundle(state_dir, roster)
    if roster.role == ROLE_PEER and bundle is not None:
        kwargs["ssl_cert_reqs"] = ssl.CERT_REQUIRED
        kwargs["ssl_ca_certs"] = str(bundle)
    else:
        if roster.role == ROLE_PEER:
            logger.warning(
                "crew listener: no enrolled certificates to require — "
                "running the enrolment-window stance (CERT_OPTIONAL)"
            )
        kwargs["ssl_cert_reqs"] = ssl.CERT_OPTIONAL
    return kwargs


def roster_file_exists(state_dir) -> bool:
    """Whether an operator has declared a crew on this host at all."""
    return (Path(state_dir) / CREW_FILENAME).exists()
