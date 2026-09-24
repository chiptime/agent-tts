"""HTTP surface of the brain: /ask, /approval/*, /tts, /audio/<file>, /health.

POST /ask runs the LLM tool loop, renders the answer to MP3 through the
herdr-tts CLI surface (contract v1) and returns an audio_url; playback
happens on the CLIENT, never on PC speakers. POST /tts is plain TTS for
the PWA's local echo. The /approval/* endpoints resolve the action gates
/ask opens: approve replays the frozen send, reject cancels, resolve maps
a voice utterance through the lexicon, PATCH edits the text, and
/approval/current recovers a live gate after reload
(PRD-action-approval-gate §5).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import queue as queue_module
import re
import subprocess
import time
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

from fastapi import FastAPI, File, HTTPException, Path as PathParam, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .approval import (
    DECISION_APPROVE,
    DECISION_REJECT,
    DECISION_REPROMPT,
    PROPOSED,
    SEND_TO_SESSION,
    ApprovalGate,
    ApprovalGateStore,
)
from .approval_lexicon import (
    OUTCOME_APPROVE,
    OUTCOME_REJECT,
    OUTCOME_REPLACE_INTENT,
    resolve_utterance,
)
from .config import Settings
from .herdr import HerdrError
from .llm import BrainLLM, BrainLLMError
from .memory import ConversationStore
from .reader import ReaderCache, turn_id
from .stt import STATE_READY, STATE_UNAVAILABLE, UNAVAILABLE_HINT, Transcriber
from .tools import BrainTools
from .tools import status_payload as _status_payload
from .tts import (
    TTS_BACKEND_MISSING,
    TTS_BACKEND_OK,
    new_audio_path,
    render_html,
    render_mp3,
    tts_backend_status,
)
from .tts_daemon import DAEMON_UP, DaemonWatcher, daemon_status
from .watcher import AgentWatcher

_TTSRenderer = Callable[[Settings, str, Path], Path]
_ReaderRenderer = Callable[[Settings, str, Path], "tuple[str, dict]"]
_LLMFactory = Callable[[Settings, BrainTools], BrainLLM]

_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9._-]+$")

logger = logging.getLogger("herdr_brain.server")
STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_SESSION_ID_CHARS = 128

# Deterministic approval closer (PRD-action-approval-gate §7): appended
# server-side to the spoken answer whenever a gate opens — the question
# wording never depends on the model. Product copy; keep verbatim.
APPROVAL_CLOSER = "¿Se envía?"

# Spoken re-prompt after one ambiguous confirming utterance
# (PRD-action-approval-gate §4). Product copy; keep verbatim.
REPROMPT_LINE = "¿Sí o no?"


def approval_payload(gate: ApprovalGate, timeout_s: int, now: Optional[float] = None) -> dict:
    """Shapes the ``approval{}`` object of /ask (and approval endpoints).

    ``expires_in_s`` follows the lazy expiry model: remaining seconds from
    the gate's ``created_at`` plus the configured timeout, no timers. It
    counts down (ceil, clamped at 0) so the client can render its ring.
    """
    elapsed = (time.time() if now is None else now) - gate.created_at
    remaining = timeout_s - elapsed
    return {
        "gate_id": gate.gate_id,
        "tool": gate.tool,
        "pane_id": gate.action.pane_id,
        "agent": gate.action.agent,
        "text": gate.action.text,
        "timeout_ms": gate.action.timeout_ms,
        "expires_in_s": max(0, math.ceil(remaining)),
    }

# The phone MUST be able to tell which build it runs: index.html is served
# with no-cache and every asset reference carries ?v=<git short hash>.
_NO_CACHE_HEADERS = {"Cache-Control": "no-cache"}
_VERSIONED_REFS = (
    ('src="/app.js"', 'src="/app.js?v={v}"'),
    ('src="/endpointing.js"', 'src="/endpointing.js?v={v}"'),
    ('src="/vad.js"', 'src="/vad.js?v={v}"'),
    ('src="/approval.js"', 'src="/approval.js?v={v}"'),
    ('href="/manifest.webmanifest"', 'href="/manifest.webmanifest?v={v}"'),
    ('href="/icon.svg"', 'href="/icon.svg?v={v}"'),
)
_VERSION_PLACEHOLDER = '<span id="app-version">dev</span>'
_HEADER_VERSION_PLACEHOLDER = '<span id="app-version-header">?</span>'


@lru_cache(maxsize=1)
def resolve_version() -> str:
    """Git short hash of the running build, cached at startup ('dev' fallback)."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(Path(__file__).resolve().parents[2]),
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except Exception:  # noqa: BLE001 — a missing git must never break the server
        pass
    return "dev"


def render_index(version: str) -> str:
    """Static index.html with versioned asset refs and the visible version."""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    for old, new in _VERSIONED_REFS:
        html = html.replace(old, new.format(v=version))
    html = html.replace(_VERSION_PLACEHOLDER, f'<span id="app-version">v{version}</span>')
    html = html.replace(_HEADER_VERSION_PLACEHOLDER, f'<span id="app-version-header">v{version}</span>')
    return html


class TextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8_000)
    session_id: Optional[str] = Field(default=None, max_length=MAX_SESSION_ID_CHARS)
    pane_id: Optional[str] = Field(default=None, max_length=MAX_SESSION_ID_CHARS)
    reset: bool = False


class ResetRequest(BaseModel):
    session_id: Optional[str] = Field(default=None, max_length=MAX_SESSION_ID_CHARS)


class ResolveRequest(BaseModel):
    # Empty STT captures are allowed: the lexicon resolves them to
    # unknown, which feeds the reprompt budget (safe direction).
    utterance: str = Field(default="", max_length=8_000)


class ApprovalPatchRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8_000)


def default_llm_factory(settings: Settings, tools: BrainTools) -> BrainLLM:
    return BrainLLM(settings, tools)


def create_app(
    settings: Optional[Settings] = None,
    llm_factory: Optional[_LLMFactory] = None,
    tts_renderer: Optional[_TTSRenderer] = None,
    reader_renderer: Optional[_ReaderRenderer] = None,
    watcher: Optional[AgentWatcher] = None,
    sse_heartbeat_s: float = 15.0,
    sse_stream_limit: Optional[int] = None,
    version: Optional[str] = None,
    transcriber: Optional[Transcriber] = None,
    daemon_probe: Optional[Callable[[], str]] = None,
) -> FastAPI:
    """Builds the FastAPI app with injectable backends for tests.

    An injected ``watcher`` is NOT started as a thread (tests drive
    ``poll_once`` manually); without one, a watcher thread is created and
    started for production. ``sse_stream_limit`` ends the /events stream
    after N events/heartbeats — a test hook only (starlette's TestClient
    buffers whole responses, so infinite streams cannot be asserted);
    production never sets it. An injected ``transcriber`` skips the STT boot
    path entirely (tests fake the engine); without one, the real engine is
    created and warmed in the background ONLY when the model files are
    already local and ``settings.stt_warmup`` is on (default in production,
    off in tests) — the server never downloads the model by itself.
    """
    if settings is None:
        from .config import load_settings

        settings = load_settings()
    cfg = settings

    tools = BrainTools(cfg)
    synth = tts_renderer or render_mp3
    # One reader cache per app instance (mirrors the tts_renderer seam):
    # production rides the real herdr-tts --render-html surface, tests
    # inject a counting fake to prove cache hits spawn no subprocess.
    reader_cache = ReaderCache(cfg, renderer=reader_renderer or render_html)
    store = ConversationStore()
    approval_store = ApprovalGateStore(timeout_s=cfg.approval_timeout_s)

    if watcher is None:
        watcher = AgentWatcher(cfg, tts_renderer=synth)
        watcher.start()

    if transcriber is None:
        transcriber = Transcriber(cfg)
        if cfg.stt_warmup:
            # Warm from local files only; flips to unavailable (with the
            # pull hint) when the model was never pulled. NEVER downloads.
            transcriber.maybe_start_warmup()

    # Speech backend contract (fail-soft): a missing herdr-tts must never
    # crash the server — text answers keep working, only speech degrades.
    tts_status, tts_detail = tts_backend_status(cfg)
    if tts_status != TTS_BACKEND_OK:
        logger.warning("speech backend MISSING: %s", tts_detail)
    else:
        logger.info("speech backend contract ok: %s", tts_detail)

    # herdr-tts DAEMON liveness (the PC-speaker announcement channel): the
    # probe is fail-soft and injectable; the watcher warns on the surviving
    # channel (SSE) when the daemon dies. Both announce channels are
    # always-on BY DESIGN (fail-noisy for approval flows).
    daemon_probe_fn: Callable[[], str] = daemon_probe or daemon_status
    try:
        _daemon_now = daemon_probe_fn()
    except Exception:  # noqa: BLE001 — a broken probe must never kill boot
        _daemon_now = "down"
    logger.info(
        "herdr-tts daemon probe: %s (PC announcement channel %s)",
        _daemon_now,
        "alive" if _daemon_now == DAEMON_UP else "DEAD — brain will warn over SSE",
    )
    daemon_watcher = DaemonWatcher(
        cfg, hub=watcher.hub, tts_renderer=synth, probe=daemon_probe_fn
    )
    daemon_watcher.start()

    # The LLM client is built lazily: /health and /tts work without
    # GLM_API_KEY, and /ask reports the missing configuration as a 503.
    llm_holder: dict = {}

    def get_llm() -> BrainLLM:
        if "instance" not in llm_holder:
            llm = (llm_factory or default_llm_factory)(cfg, tools)
            # Custom factories may not wire a store; bind the shared ones so
            # /reset and /ask always see the same conversation memory, and
            # every send_to_session interception opens its gate in the SAME
            # approval store the approval endpoints resolve against.
            if hasattr(llm, "attach_store"):
                llm.attach_store(store)
            if hasattr(llm, "attach_approval_store"):
                llm.attach_approval_store(approval_store)
            llm_holder["instance"] = llm
        return llm_holder["instance"]

    app = FastAPI(title="herdr-brain", version=__version__)
    app.state.watcher = watcher
    app.state.daemon_watcher = daemon_watcher
    app.state.transcriber = transcriber
    app.state.approval_store = approval_store
    app.state.sse_heartbeat_s = sse_heartbeat_s
    app.state.sse_stream_limit = sse_stream_limit

    build = version if version is not None else resolve_version()
    app.state.build = build

    @app.get("/", include_in_schema=False)
    def index() -> HTMLResponse:
        # Re-read per request: caching the rendered HTML at boot served a
        # stale page after static edits, so the phone mixed fresh JS with
        # an old DOM and rendered nothing. Page loads are rare (PWA), the
        # file is small, and the no-cache header already promises fresh.
        return HTMLResponse(
            content=render_index(build),
            headers=dict(_NO_CACHE_HEADERS),
        )

    @app.get("/app.js", include_in_schema=False)
    def app_js() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "app.js", media_type="text/javascript",
            headers=dict(_NO_CACHE_HEADERS),
        )

    @app.get("/endpointing.js", include_in_schema=False)
    def endpointing_js() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "endpointing.js", media_type="text/javascript",
            headers=dict(_NO_CACHE_HEADERS),
        )

    @app.get("/vad.js", include_in_schema=False)
    def vad_js() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "vad.js", media_type="text/javascript",
            headers=dict(_NO_CACHE_HEADERS),
        )

    @app.get("/approval.js", include_in_schema=False)
    def approval_js() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "approval.js", media_type="text/javascript",
            headers=dict(_NO_CACHE_HEADERS),
        )

    @app.get("/sw.js", include_in_schema=False)
    def sw_js() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "sw.js", media_type="text/javascript",
            headers=dict(_NO_CACHE_HEADERS),
        )

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "manifest.webmanifest",
            media_type="application/manifest+json",
            headers=dict(_NO_CACHE_HEADERS),
        )

    @app.get("/icon.svg", include_in_schema=False)
    def icon() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "icon.svg", media_type="image/svg+xml",
            headers=dict(_NO_CACHE_HEADERS),
        )

    @app.get("/events")
    async def events(request: Request) -> StreamingResponse:
        """SSE stream of agent transition announcements.

        Event payload: {type, pane_id, agent, status, label, text, audio_url}.
        Comment heartbeats keep intermediaries from closing the stream.
        """
        hub = app.state.watcher.hub
        heartbeat = app.state.sse_heartbeat_s
        stream_limit = app.state.sse_stream_limit
        sub_id, announcements = hub.subscribe()
        loop = asyncio.get_running_loop()

        def _next_event():
            try:
                return announcements.get(timeout=heartbeat)
            except queue_module.Empty:
                return None

        async def generator():
            try:
                yield ": connected\n\n"
                emitted = 0
                while stream_limit is None or emitted < stream_limit:
                    announcement = await loop.run_in_executor(None, _next_event)
                    if announcement is None:
                        yield ": heartbeat\n\n"
                    else:
                        yield "data: " + json.dumps(announcement, ensure_ascii=False) + "\n\n"
                    emitted += 1
            finally:
                hub.unsubscribe(sub_id)

        return StreamingResponse(
            generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/health")
    def health() -> dict:
        # Three-state tts: 'missing' (surface contract unmet — computed once
        # at boot, the CLI version is static per run) dominates; with the
        # contract present, daemon liveness splits ok | degraded
        # (degraded = PC-speaker channel dead, phone channel still alive).
        if tts_status != TTS_BACKEND_OK:
            tts_field: str = tts_status
        else:
            try:
                tts_field = "ok" if daemon_probe_fn() == DAEMON_UP else "degraded"
            except Exception:  # noqa: BLE001 — health must never raise
                tts_field = "degraded"
        return {
            "status": "ok",
            "version": __version__,
            "stt": transcriber.state,
            "tts": tts_field,
        }

    @app.post("/transcribe")
    async def transcribe(audio: UploadFile = File(...)) -> dict:
        """Server-side STT: multipart field 'audio' (webm/opus from
        MediaRecorder, or any media faster-whisper decodes via PyAV) ->
        ``{"text": "..."}``."""
        if transcriber.state == STATE_UNAVAILABLE:
            raise HTTPException(
                status_code=503,
                detail="Transcripción no disponible: " + UNAVAILABLE_HINT + ".",
            )
        if transcriber.state != STATE_READY:
            raise HTTPException(
                status_code=503,
                detail="El modelo de voz se está cargando — prueba de nuevo "
                "en unos segundos.",
            )
        data = await audio.read()
        if not data:
            raise HTTPException(status_code=400, detail="Audio vacío.")
        loop = asyncio.get_running_loop()
        try:
            text = await loop.run_in_executor(None, transcriber.transcribe_bytes, data)
        except Exception as exc:  # noqa: BLE001 — decode/transcribe failures → 503
            raise HTTPException(
                status_code=503, detail=f"No se pudo transcribir el audio: {exc}"
            ) from exc
        return {"text": text.strip()}

    @app.post("/reset")
    def reset(body: ResetRequest) -> dict:
        store.reset(body.session_id)
        return {"ok": True, "session_id": ConversationStore.normalize(body.session_id)}

    @app.get("/state")
    def state() -> dict:
        """Active agent pane snapshot for the PWA header (read-only)."""
        try:
            return tools.status_payload()
        except Exception:  # noqa: BLE001 — a polling endpoint never 500s
            return _status_payload(None)

    @app.get("/herd")
    def herd() -> list:
        """All agent panes with status and their latest transcript turn."""
        try:
            return tools.herd()
        except Exception:  # noqa: BLE001 — the herd endpoint never 500s
            return []

    @app.get("/view")
    def view(pane_id: Optional[str] = None) -> dict:
        """Superset of /state for one pane (default: focused)."""
        try:
            return tools.agent_view(pane_id)
        except Exception:  # noqa: BLE001 — the view endpoint never 500s
            return {
                "status": _status_payload(None),
                "transcript": None,
                "screen": None,
                "pending": {"detected": False, "kind": None, "excerpt": None},
            }

    @app.get("/conversation")
    def conversation(pane_id: Optional[str] = None) -> dict:
        """Full-text recent conversation of one pane (last 20 turns)."""
        try:
            return tools.conversation(pane_id)
        except Exception:  # noqa: BLE001
            return {"pane_id": pane_id, "agent": None, "session_id": None,
                    "turns": [], "window": 20}

    @app.get("/conversation/{pane_id}/rendered")
    def conversation_rendered(
        pane_id: str = PathParam(max_length=MAX_SESSION_ID_CHARS),
    ) -> dict:
        """Rendered conversation snapshot for the reader surface.

        Same resolution convention as /conversation but STRICTER on the
        unknown-pane case: an unresolved pane is a 404 (an addressed
        resource that does not exist), never a reader fallback body — the
        client must distinguish "pane gone" from "nothing rendered".
        Rendered fields are fail-soft: html/map null TOGETHER with text
        always present; the reader cache logs failure classes with the
        cache-key prefix only.
        """
        data = tools.conversation(pane_id)
        if not data.get("agent"):
            raise HTTPException(status_code=404, detail="conversation not found")
        session_id = data.get("session_id")
        rendered = reader_cache.render_snapshot(data.get("turns") or [])
        return {
            "pane_id": data.get("pane_id", pane_id),
            "session_id": session_id,
            "turns": [
                {
                    "turn_id": turn_id(
                        session_id, index, turn.get("role", ""), turn.get("text", "")
                    ),
                    "role": turn.get("role"),
                    "text": turn.get("text"),
                    "html": turn.get("html"),
                    "map": turn.get("map"),
                }
                for index, turn in enumerate(rendered)
            ],
        }

    @app.get("/screen")
    def screen(pane_id: Optional[str] = None) -> dict:
        """Full screen tail (~120 scrollback lines) of one pane."""
        try:
            return tools.screen_full(pane_id)
        except Exception:  # noqa: BLE001
            return {"pane_id": pane_id, "agent": None, "screen": None}

    # -- answer shaping + approval resolution (shared by /ask and approve) --

    def speak_answer(answer: Optional[str]) -> Optional[str]:
        """Renders an answer to MP3, fail-soft: TTS never breaks answers."""
        if not answer:
            return None
        out_path = new_audio_path(cfg)
        try:
            synth(cfg, answer, out_path)
            return f"/audio/{out_path.name}"
        except Exception as exc:  # noqa: BLE001 — TTS must never break the answer
            # Degrade to a text-only answer, but never silently: the log
            # names the speech backend contract when that is the cause.
            logger.warning("TTS render failed — answer degrades to text-only: %s", exc)
            return None

    def shape_ask_response(result: dict) -> dict:
        """Shapes an LLM turn the /ask way: approval{}, deterministic
        closer, TTS audio_url. The approve replay reuses this so its
        response follows the exact /ask conventions."""
        answer: Optional[str] = result.get("answer")
        gate: Optional[ApprovalGate] = result.get("approval")
        approval: Optional[dict] = None
        if gate is not None:
            approval = approval_payload(gate, cfg.approval_timeout_s)
            # Deterministic closer, spoken and shown — appended AFTER the
            # model's echo so the approval question never depends on it.
            answer = f"{answer} {APPROVAL_CLOSER}" if answer else APPROVAL_CLOSER
        return {
            "answer": answer,
            "pane_id": result.get("pane_id"),
            "agent": result.get("agent"),
            "session_id": result.get("session_id"),
            "audio_url": speak_answer(answer),
            "approval": approval,
        }

    def gate_gone() -> HTTPException:
        """404 for unknown, expired, superseded or already-resolved gates.

        Lazy expiry fires on the get() touch, so an elapsed window surfaces
        here exactly like an unknown id — silent, toward cancel (PRD §6).
        """
        return HTTPException(
            status_code=404,
            detail="approval gate not found or no longer active",
        )

    def live_gate_or_404(gate_id: str) -> ApprovalGate:
        gate = approval_store.get(gate_id)
        if gate is None or gate.state != PROPOSED:
            raise gate_gone()
        return gate

    def replay_and_report(gate: ApprovalGate) -> dict:
        """Executes the frozen send and reports the outcome.

        This is THE approve execution (AC6 — exactly once, guaranteed by
        the store's approve-once resolve): the replay rides the exact path
        a live tool call would take — ``dispatch("send_to_session", ...)``
        on the re-resolved target — so sanitization and error handling
        match the ungated flow; then the model reports the completion
        through the normal loop entry and the answer is shaped like /ask.
        """
        target = tools.resolve_target(gate.action.pane_id)
        tool_result = tools.dispatch(
            SEND_TO_SESSION,
            {"text": gate.action.text, "timeout_ms": gate.action.timeout_ms},
            target=target,
        )
        report_prompt = (
            f"The approved prompt was sent to {gate.action.agent or 'the agent'} "
            f"(pane {gate.action.pane_id or 'unknown'}) and the agent finished "
            f"with:\n\n{tool_result}\n\n"
            "Report this outcome to the user in one or two short spoken "
            "sentences. Do not send anything else."
        )
        try:
            result = get_llm().ask(
                report_prompt,
                session_id=gate.session_id,
                pane_id=gate.action.pane_id,
            )
        except BrainLLMError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return shape_ask_response(result)

    @app.get("/approval/current")
    def approval_current(session_id: Optional[str] = None) -> dict:
        """The session's live gate or ``{approval: null}`` — reload
        recovery for the PWA (PRD §5). Same session-id source as /ask:
        absent id normalizes to the default session."""
        gate = approval_store.current(session_id)
        approval = (
            approval_payload(gate, cfg.approval_timeout_s) if gate is not None else None
        )
        return {"approval": approval}

    @app.post("/ask")
    def ask(body: TextRequest) -> dict:
        if body.reset:
            store.reset(body.session_id)
        # A new turn always retires the session's live gate (PRD §4): the
        # user moved on, so an unanswered gate must never linger. propose()
        # supersedes too, but a question-only ask must not leave a zombie.
        approval_store.supersede(body.session_id)
        try:
            result = get_llm().ask(
                body.text, session_id=body.session_id, pane_id=body.pane_id
            )
        except BrainLLMError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return shape_ask_response(result)

    @app.post("/approval/{gate_id}/approve")
    def approval_approve(gate_id: str) -> dict:
        """Replays the frozen send exactly once and reports the outcome.

        Same response shape as /ask (report answer + TTS audio_url). The
        replay IS the single approve execution; a second approve finds a
        terminal gate and 404s (approve-once, AC6).
        """
        live_gate_or_404(gate_id)
        resolved, applied = approval_store.resolve(gate_id, DECISION_APPROVE)
        if not applied:
            raise gate_gone()
        return replay_and_report(resolved)

    @app.post("/approval/{gate_id}/reject")
    def approval_reject(gate_id: str) -> dict:
        """Silent cancellation: no TTS is generated server-side (PRD §5)."""
        live_gate_or_404(gate_id)
        resolved, applied = approval_store.resolve(gate_id, DECISION_REJECT)
        if not applied:
            raise gate_gone()
        return {"ok": True, "state": resolved.state}

    @app.post("/approval/{gate_id}/resolve")
    def approval_resolve(gate_id: str, body: ResolveRequest) -> dict:
        """Voice path: maps a confirming-state STT utterance onto the gate.

        Response: ``{decision, answer, audio_url, approval}`` where decision
        is approve / reject / listen_replace / reprompt (PRD §5). answer +
        audio_url carry the report (approve) or the spoken re-prompt; the
        reject and listen_replace outcomes stay silent server-side.
        """
        gate = live_gate_or_404(gate_id)
        outcome = resolve_utterance(body.utterance)
        if outcome == OUTCOME_APPROVE:
            resolved, applied = approval_store.resolve(gate_id, DECISION_APPROVE)
            if not applied:
                raise gate_gone()
            turn = replay_and_report(resolved)
            return {
                "decision": "approve",
                "answer": turn["answer"],
                "audio_url": turn["audio_url"],
                "approval": turn["approval"],
            }
        if outcome == OUTCOME_REJECT:
            resolved, applied = approval_store.resolve(gate_id, DECISION_REJECT)
            if not applied:
                raise gate_gone()
            return {"decision": "reject", "answer": None, "audio_url": None, "approval": None}
        if outcome == OUTCOME_REPLACE_INTENT:
            # The gate stays live and untouched: the replacement text is
            # NOT in this utterance. The client runs ONE dictation round
            # and PATCHes the new text, which restarts the timer (PRD §5).
            return {"decision": "listen_replace", "answer": None, "audio_url": None, "approval": None}
        # Unknown/ambiguous: one spoken reprompt, a second auto-rejects
        # toward the safe direction (PRD §4).
        if gate.reprompt_count >= 1:
            resolved, applied = approval_store.resolve(gate_id, DECISION_REJECT)
            if not applied:
                raise gate_gone()
            return {"decision": "reject", "answer": None, "audio_url": None, "approval": None}
        approval_store.resolve(gate_id, DECISION_REPROMPT)
        return {
            "decision": "reprompt",
            "answer": REPROMPT_LINE,
            "audio_url": speak_answer(REPROMPT_LINE),
            "approval": None,
        }

    @app.patch("/approval/{gate_id}")
    def approval_patch(gate_id: str, body: ApprovalPatchRequest) -> dict:
        """Manual edit or voice re-dictation result: swaps the frozen text
        and restarts the timer (the store resets created_at)."""
        live_gate_or_404(gate_id)
        updated = approval_store.patch(gate_id, body.text)
        if updated is None:
            raise gate_gone()
        return {"ok": True, "approval": approval_payload(updated, cfg.approval_timeout_s)}

    @app.post("/tts")
    def tts(body: TextRequest) -> dict:
        out_path = new_audio_path(cfg)
        try:
            synth(cfg, body.text, out_path)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"tts failed: {exc}") from exc
        return {"audio_url": f"/audio/{out_path.name}"}

    @app.get("/audio/{name}")
    def audio(name: str) -> FileResponse:
        if not _SAFE_FILENAME.match(name) or ".." in name:
            raise HTTPException(status_code=404, detail="not found")
        path = cfg.audio_dir / name
        if not path.is_file():
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(path, media_type="audio/mpeg")

    # Static PWA assets, mounted last so every API route above wins.
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

    return app


def main() -> None:  # pragma: no cover - manual entrypoint
    import logging
    import os

    import uvicorn

    from .config import load_settings

    # Under systemd the journal only sees what Python actually emits:
    # configure the root logger so the contract/startup INFO lines (speech
    # backend ok, etc.) reach it, not just warnings.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Default loopback; override (e.g. 0.0.0.0 under WSL2 with a Windows-side
    # tailscale serve proxy) via HERDR_BRAIN_HOST.
    host = os.getenv("HERDR_BRAIN_HOST", "127.0.0.1")
    port = int(os.getenv("HERDR_BRAIN_PORT", "8741"))
    uvicorn.run(create_app(load_settings()), host=host, port=port)


if __name__ == "__main__":  # pragma: no cover
    main()
