"""The headless SDK surface: pinned ``/sdk/v1/*`` routes (POST-05).

A scripted client needs a durable, versioned slice of the brain without
the PWA: the herd, one pane's view, a conversational ask, and the live
approval gate with its decision endpoint. This module re-implements
none of it — ``register_sdk_surface`` re-registers the SAME FastAPI
handler objects the PWA routes use under the versioned paths, so the
handler outcomes, the doc-01 access matrix (the raw ASGI
AccessMiddleware covers every registered route), the audit lines and
the masking seams match path-for-path by construction.

Transport note (design decision, approved plan): plain HTTP JSON on the
brain's own port — not WebSocket (no streaming consumer; the only WS
route serves the live call audio) and not stdio (the daemon owns the
state and the tools; a stdio process would be a second brain instance
or an HTTP proxy with extra steps). ``herdr-brain sdk`` in the
operations CLI is the stdio-facing client.

Version skew: the version lives in the path, like crew's ``/crew/v1``.
Routes are pinned to ``SDK_VERSION``; one catch-all registered before
the static mount answers unknown versions and unknown operations with a
legible 404 JSON naming the supported version, so a stale client is
told what to speak instead of hitting StaticFiles' plain-text 404.
"""

from __future__ import annotations

from fastapi import FastAPI

#: The only protocol version this build speaks. A new version means new
#: pinned routes (or a new register call) — never widening v1 in place.
SDK_VERSION = "v1"

_SKEW_METHODS = ("GET", "POST", "PATCH", "PUT", "DELETE")


def register_sdk_surface(
    app: FastAPI,
    *,
    herd,
    view,
    approval_current,
    ask,
    approval_action,
) -> None:
    """Re-register the PWA route handlers under ``/sdk/v1/*`` (POST-05).

    Same function objects → same handlers, same access matrix, same
    audit lines with device attribution, same masking seams.
    ``include_in_schema=False`` keeps the OpenAPI surface identical to
    pre-SDK.

    Order matters twice: the specific routes go FIRST so FastAPI's
    registration-order matching always prefers them over the skew
    catch-all, and the caller must invoke this BEFORE mounting the
    static files, so unknown SDK paths never fall through to
    StaticFiles' plain-text 404.
    """
    prefix = f"/sdk/{SDK_VERSION}"
    app.add_api_route(
        f"{prefix}/herd", herd, methods=["GET"], include_in_schema=False
    )
    app.add_api_route(
        f"{prefix}/view", view, methods=["GET"], include_in_schema=False
    )
    app.add_api_route(
        f"{prefix}/approval/current",
        approval_current,
        methods=["GET"],
        include_in_schema=False,
    )
    app.add_api_route(
        f"{prefix}/ask", ask, methods=["POST"], include_in_schema=False
    )
    app.add_api_route(
        f"{prefix}/approval/{{gate_id}}/action",
        approval_action,
        methods=["POST"],
        include_in_schema=False,
    )

    def sdk_unknown(version: str, rest: str = "") -> dict:
        """One legible refusal for every unknown version or operation.
        Revealed only to callers the access middleware already let
        through — an unauthenticated caller cannot enumerate the
        surface (the crew dispatcher's same discipline)."""
        return {"detail": "unknown sdk surface", "supported": SDK_VERSION}

    app.add_api_route(
        "/sdk/{version}/{rest:path}",
        sdk_unknown,
        methods=list(_SKEW_METHODS),
        status_code=404,
        include_in_schema=False,
    )
    app.add_api_route(
        "/sdk/{version}",
        sdk_unknown,
        methods=list(_SKEW_METHODS),
        status_code=404,
        include_in_schema=False,
    )

    def sdk_unknown_root() -> dict:
        """Bare ``/sdk`` carries no version segment: the same legible
        refusal instead of falling through to the static mount."""
        return {"detail": "unknown sdk surface", "supported": SDK_VERSION}

    app.add_api_route(
        "/sdk",
        sdk_unknown_root,
        methods=list(_SKEW_METHODS),
        status_code=404,
        include_in_schema=False,
    )
