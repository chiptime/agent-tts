"""HTTP surface of the brain: /ask, /approval/*, /tts, /audio/<file>, /health.

POST /ask runs the LLM tool loop, renders the answer to MP3 through the
herdr-tts CLI surface (contract v1) and returns an audio_url; playback
happens on the CLIENT, never on PC speakers. POST /tts is plain TTS for
the PWA's local echo. POST /speech/{id}/cancel aborts an in-flight
speech job (VS1.3) — verdict-shaped, idempotent, token-redacted. The
/approval/* endpoints resolve the action gates /ask opens: approve
replays the frozen send, reject cancels, resolve maps a voice utterance
through the lexicon, PATCH edits the text, and /approval/current
recovers a live gate after reload (PRD-action-approval-gate §5).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import queue as queue_module
import re
import subprocess
import threading
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
    CREATE_SESSION,
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
from .consult import ConsultService
from .herdr import HerdrError
from .history import HistoryStore, default_history_path
from .llm import BrainLLM, BrainLLMError
from .memory import MAX_MESSAGES, ConversationStore
from .reader import ReaderCache, turn_id
from .speech import (
    PHASE_CANCELLED,
    PHASE_COMPLETE,
    PHASE_FAILED,
    PHASE_RENDERING,
    DuplicateActiveJob,
    RegistryFull,
    SegmentedSpeechProducer,
    SpeechJob,
    SpeechRegistry,
    build_registry,
    choose_speech_path,
    is_terminal_phase,
    sweep_unconsumed,
    validate_speech_request_id,
)
# Module handle onto speech: the VS2.5 transport constants below are
# read at USE time so monkeypatching them in speech affects this server.
from . import speech as _speech
from .stt import STATE_READY, STATE_UNAVAILABLE, UNAVAILABLE_HINT, Transcriber
from .tools import BrainTools
from .tools import status_payload as _status_payload
from .tts import (
    TTS_BACKEND_MISSING,
    TTS_BACKEND_OK,
    new_audio_path,
    render_html,
    render_mp3,
    render_mp3_cancellable,
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
# Serving cap for GET /call-history: the PWA boot repaint only needs the
# recent transcript, not the whole persisted call.
CALL_HISTORY_TURNS = 200

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
    Send gates carry the frozen prompt (``text``/``pane_id``); create
    gates carry the frozen panel spec (``title``/``task``/``cwd``) with
    the agent kind in ``agent`` — no pane exists yet.
    """
    elapsed = (time.time() if now is None else now) - gate.created_at
    remaining = timeout_s - elapsed
    if gate.tool == CREATE_SESSION:
        return {
            "gate_id": gate.gate_id,
            "tool": gate.tool,
            "agent": gate.action.agent_kind,
            "title": gate.action.title,
            "task": gate.action.task,
            "cwd": gate.action.cwd,
            "timeout_ms": gate.action.timeout_ms,
            "expires_in_s": max(0, math.ceil(remaining)),
        }
    return {
        "gate_id": gate.gate_id,
        "tool": gate.tool,
        "pane_id": gate.action.pane_id,
        "agent": gate.action.agent,
        "text": gate.action.text,
        "timeout_ms": gate.action.timeout_ms,
        "expires_in_s": max(0, math.ceil(remaining)),
    }


def announcement_event_payload(announcement: dict) -> dict:
    """SSE ``data:`` payload of one announcement event (VS1.4, additive).

    The watcher's announcements now carry a brain-minted
    ``speech_request_id`` (``ann-<uuid4>``); this construction seam
    passes the dict through VERBATIM, so the field rides the wire when
    present and an announcement lacking it (a legacy or third-party hub
    publisher) serializes EXACTLY like the v1 payload — no key is ever
    injected, dropped, or renamed here.
    """
    return dict(announcement)

# The phone MUST be able to tell which build it runs: index.html is served
# with no-cache and every asset reference carries ?v=<git short hash>.
_NO_CACHE_HEADERS = {"Cache-Control": "no-cache"}
# Pinned CSP for the index response ONLY (design CSP audit): every JS file
# is same-origin, no inline <script>, no eval — but the inline <style>
# block and two style="…" attributes REQUIRE style-src 'unsafe-inline'.
# Do NOT tighten it away: the PWA breaks. Asset routes keep plain
# no-cache headers; the policy never leaks off the index.
READER_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "media-src 'self' blob:; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'"
)
_INDEX_HEADERS = {**_NO_CACHE_HEADERS, "Content-Security-Policy": READER_CSP}
_VERSIONED_REFS = (
    # consult.js loads BEFORE app.js (index.html script order): a stale
    # cached Consult module makes the new app.js call methods that do
    # not exist, so /ask aborts locally before any POST.
    ('src="/consult.js"', 'src="/consult.js?v={v}"'),
    ('src="/app.js"', 'src="/app.js?v={v}"'),
    ('src="/endpointing.js"', 'src="/endpointing.js?v={v}"'),
    ('src="/vad.js"', 'src="/vad.js?v={v}"'),
    ('src="/approval.js"', 'src="/approval.js?v={v}"'),
    ('src="/reader.js"', 'src="/reader.js?v={v}"'),
    ('src="/toast.js"', 'src="/toast.js?v={v}"'),
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
    # Voice-stack VS1.1: optional speech job admission. Absent fields =
    # legacy turn, byte-comparable with the pre-speech surface (extra
    # fields are ignored, so old and new clients interoperate).
    speech_request_id: Optional[str] = Field(default=None, max_length=64)
    speech_cancel_token: Optional[str] = None


class ResetRequest(BaseModel):
    session_id: Optional[str] = Field(default=None, max_length=MAX_SESSION_ID_CHARS)


class SpeechCancelRequest(BaseModel):
    """VS1.3 cancel body. Both fields are required and non-empty (422).

    The token travels here ONLY to reach the registry's constant-time
    digest check — it is never logged, echoed, or stored plain.
    """

    session_id: str = Field(min_length=1, max_length=MAX_SESSION_ID_CHARS)
    speech_cancel_token: str = Field(min_length=1)


class ResolveRequest(BaseModel):
    # Empty STT captures are allowed: the lexicon resolves them to
    # unknown, which feeds the reprompt budget (safe direction).
    utterance: str = Field(default="", max_length=8_000)


class ApprovalPatchRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8_000)


def default_llm_factory(settings: Settings, tools: BrainTools) -> BrainLLM:
    return BrainLLM(settings, tools)


def _admit_speech_job(
    body: TextRequest, registry: SpeechRegistry
) -> tuple[Optional[SpeechJob], Optional[str]]:
    """Validates and registers the /ask speech job, BEFORE the LLM runs.

    Returns ``(job, degraded_reason)``: ``(None, None)`` on legacy turns
    (no id — zero registry interaction), ``(job, None)`` when admitted,
    ``(None, "registry-full")`` when the ACTIVE bound is hit — that turn
    must run text-only degraded, never an uncancellable legacy render.
    HTTP failures raise here so ask() side effects never start for a
    malformed or duplicate request.
    """
    if body.speech_request_id is None:
        return None, None
    if not validate_speech_request_id(body.speech_request_id):
        raise HTTPException(
            status_code=422,
            detail="speech_request_id must match [A-Za-z0-9._-]{8,64}",
        )
    if not body.speech_cancel_token:
        raise HTTPException(
            status_code=422,
            detail="speech_cancel_token is required when speech_request_id is set",
        )
    try:
        job = registry.register(
            body.speech_request_id, body.speech_cancel_token, body.session_id
        )
    except DuplicateActiveJob as exc:
        raise HTTPException(
            status_code=409, detail="an active speech job already holds this id"
        ) from exc
    except RegistryFull:
        return None, "registry-full"
    return job, None


# Broken-terminal set for GET /speech/{id}/next (VS2.5): a job that
# ended cancelled/degraded/failed/expired NEVER serves another segment,
# no matter what its buffer still holds. Complete is terminal but NOT
# broken — the consumer keeps draining what was already staged.
def _next_terminal_broken(phase: str) -> bool:
    return is_terminal_phase(phase) and phase != PHASE_COMPLETE


# Validates+applies one /next ack against the job's watermark without
# an interleaved concurrent poll regressing it (validate and advance in
# one critical section; never held during a long-poll hold).
_SPEECH_NEXT_ACK_LOCK = threading.Lock()


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
    speech_registry: Optional[SpeechRegistry] = None,
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
    ``tts_renderer`` keeps its classic 3-arg contract; a double that
    declares ``takes_cancel_event = True`` additionally receives the
    speech job's cancel event (4th arg) on identified /ask turns, so
    tests can model mid-render cancellation (VS1.3) without subprocesses.
    """
    if settings is None:
        from .config import load_settings

        settings = load_settings()
    cfg = settings

    # Consult (on-demand context, T9): the composition root is built
    # HERE — the one place BrainTools is constructed for production —
    # so the model's tool surface gains the read-only consult tools
    # (FR-02 on-demand surface). Attached after construction with the
    # same hasattr convention as attach_store below, so injected test
    # doubles keep working. The service's model client is lazy, so boot
    # still works without GLM_API_KEY. Zero changes to the conversation
    # ring, system prompt focus semantics, or approval flow.
    consult = ConsultService(settings=cfg)
    tools = BrainTools(cfg)
    if hasattr(tools, "attach_consult"):
        tools.attach_consult(consult)
    synth = tts_renderer or render_mp3
    # One reader cache per app instance (mirrors the tts_renderer seam):
    # production rides the real herdr-tts --render-html surface, tests
    # inject a counting fake to prove cache hits spawn no subprocess.
    reader_cache = ReaderCache(cfg, renderer=reader_renderer or render_html)
    store = ConversationStore()
    history = HistoryStore(default_history_path(cfg))
    # Boot seed: restore the conversation ring from the persisted call
    # history so LLM context survives service restarts. Only the default
    # session is seeded — the call history is one global call. This is
    # also the opportunistic compaction point (load_and_compact), so a
    # long-lived file is trimmed at boot, never on the append path.
    for record in history.load_and_compact(last_n=MAX_MESSAGES):
        store.append(None, record["role"], record["text"])
    approval_store = ApprovalGateStore(timeout_s=cfg.approval_timeout_s)

    # Speech job registry (voice-stack VS1.1): bounded, in-process, and
    # injectable like every other seam — tests drive small bounds and
    # fake clocks through the same object the handlers use.
    speech_jobs = speech_registry if speech_registry is not None else build_registry()

    if watcher is None:
        watcher = AgentWatcher(cfg, tts_renderer=synth)
        watcher.start()

    # Consulting-state events (FR-18 seam): consult start/end rides the
    # SAME SSE announcement hub as agent transitions; the PWA renders
    # the "consulting" state in T10. Attached after the watcher exists
    # either way (injected test watchers carry their own hub; hub fakes
    # without publish simply leave the seam unset).
    if hasattr(watcher.hub, "publish"):
        consult.attach_event_sink(watcher.hub.publish)

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
    app.state.speech_jobs = speech_jobs
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
            headers=dict(_INDEX_HEADERS),
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

    @app.get("/reader.js", include_in_schema=False)
    def reader_js() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "reader.js", media_type="text/javascript",
            headers=dict(_NO_CACHE_HEADERS),
        )

    @app.get("/toast.js", include_in_schema=False)
    def toast_js() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "toast.js", media_type="text/javascript",
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

        Event payload: {type, pane_id, agent, status, label, text,
        audio_url} plus speech_request_id ("ann-<uuid4>", VS1.4) when the
        announcement carries it — an announcement without the field
        serializes with the v1 key set only. Comment heartbeats keep
        intermediaries from closing the stream.
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
                        yield "data: " + json.dumps(
                            announcement_event_payload(announcement),
                            ensure_ascii=False,
                        ) + "\n\n"
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
        # A new conversation drops the persisted call transcript too —
        # the history file is one global call, so the clear is global.
        history.clear()
        return {"ok": True, "session_id": ConversationStore.normalize(body.session_id)}

    @app.get("/call-history")
    def call_history(before: Optional[str] = None, limit: int = 25) -> dict:
        """Persisted call transcript for the PWA, one page at a time.

        Oldest-first turns plus ``has_more`` (at least one record exists
        strictly older than the oldest returned turn). ``before`` is the
        pagination cursor (the oldest ts already rendered); ``limit``
        defaults to 25 and is clamped to [1, CALL_HISTORY_TURNS]. No
        params → the newest page, so the boot repaint paints only the
        recent transcript and offers "Ver más" for the rest.
        """
        capped = max(1, min(limit, CALL_HISTORY_TURNS))
        turns = history.load_before(before, capped)
        has_more = bool(turns) and bool(history.load_before(turns[0]["ts"], 1))
        return {"turns": turns, "has_more": has_more}

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

    def speak_answer(
        answer: Optional[str], cancel_event: Optional["threading.Event"] = None
    ) -> Optional[str]:
        """Renders an answer to MP3, fail-soft: TTS never breaks answers.

        ``cancel_event`` (identified speech jobs, VS1.3) switches the
        render to the ABORTABLE path. Renderer seam contract:

        - no ``tts_renderer`` injected (production): the identified-job
          render rides ``render_mp3_cancellable`` so a concurrent
          cancel aborts the speech job mid-render;
        - an injected renderer stays AUTHORITATIVE at its classic
          3-arg signature — EXCEPT when the double declares
          ``takes_cancel_event = True``, in which case it receives the
          job's event as a 4th argument and models the cancel/abort
          itself (the marker keeps every existing 3-arg fake green).

        A cancel-driven TTSError("cancelled") lands in the same
        fail-soft except as any render failure: the caller decides the
        job's terminal phase from ``cancel_requested``.
        """
        if not answer:
            return None
        out_path = new_audio_path(cfg)
        try:
            if cancel_event is None:
                synth(cfg, answer, out_path)
            elif tts_renderer is None:
                render_mp3_cancellable(cfg, answer, out_path, cancel_event)
            elif getattr(tts_renderer, "takes_cancel_event", False):
                tts_renderer(cfg, answer, out_path, cancel_event)
            else:
                tts_renderer(cfg, answer, out_path)
            return f"/audio/{out_path.name}"
        except Exception as exc:  # noqa: BLE001 — TTS must never break the answer
            # Degrade to a text-only answer, but never silently: the log
            # names the speech backend contract when that is the cause.
            logger.warning("TTS render failed — answer degrades to text-only: %s", exc)
            return None

    def speakable_answer(result: dict) -> Optional[str]:
        """The answer text exactly as speech would render it: the
        deterministic approval closer appended AFTER the model's echo
        when a gate opened. Shared by the audio render path and the
        segmented producer (VS2.4) so both speak the SAME shaped text."""
        answer = result.get("answer")
        if result.get("approval") is not None:
            return f"{answer} {APPROVAL_CLOSER}" if answer else APPROVAL_CLOSER
        return answer

    def shape_ask_response(
        result: dict, speak: bool = True, cancel_event: Optional["threading.Event"] = None
    ) -> dict:
        """Shapes an LLM turn the /ask way: approval{}, deterministic
        closer, TTS audio_url. The approve replay reuses this so its
        response follows the exact /ask conventions. ``speak=False``
        suppresses only the audio render (cancelled or degraded speech
        jobs) — text and approval shaping are untouched. ``cancel_event``
        forwards to speak_answer's abortable render path (VS1.3)."""
        answer = speakable_answer(result)
        approval: Optional[dict] = None
        if result.get("approval") is not None:
            approval = approval_payload(result["approval"], cfg.approval_timeout_s)
            # (The closer was already folded into ``answer`` above,
            # appended after the model's echo so the approval question
            # never depends on it.)
        return {
            "answer": answer,
            "pane_id": result.get("pane_id"),
            "agent": result.get("agent"),
            "session_id": result.get("session_id"),
            "audio_url": speak_answer(answer, cancel_event) if speak else None,
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
        """Executes the frozen action and reports the outcome.

        This is THE approve execution (AC6 — exactly once, guaranteed by
        the store's approve-once resolve): the replay rides the exact path
        a live tool call would take — ``dispatch(gate.tool, ...)`` with the
        frozen args, so sanitization and error handling match the ungated
        flow; then the model reports the completion through the normal
        loop entry and the answer is shaped like /ask.
        """
        if gate.tool == CREATE_SESSION:
            return _replay_create_and_report(gate)
        target = tools.resolve_target(gate.action.pane_id)
        tool_result = tools.dispatch(
            SEND_TO_SESSION,
            {"text": gate.action.text, "timeout_ms": gate.action.timeout_ms},
            target=target,
        )
        report_prompt = (
            f"The approved prompt was delivered to {gate.action.agent or 'the agent'} "
            f"(pane {gate.action.pane_id or 'unknown'}). Tool result:\n\n{tool_result}\n\n"
            "Report the outcome to the user in one or two short spoken sentences. "
            'If the tool result status is "timeout" or "stalled": the message WAS '
            "delivered and the agent is still working on it — say that, and do NOT "
            "offer to resend or retry (it would duplicate the prompt). "
            'Only "blocked" means the message did not go through.'
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

    def _replay_create_and_report(gate: ApprovalGate) -> dict:
        """Approve execution for create gates: dispatch the frozen panel
        spec (no target — the tool creates its own pane), then report."""
        tool_result = tools.dispatch(
            CREATE_SESSION,
            {
                "agent_kind": gate.action.agent_kind or "",
                "title": gate.action.title or "",
                "task": gate.action.task or "",
                "cwd": gate.action.cwd or "",
            },
        )
        report_prompt = (
            f"The approved panel creation ran: agent kind "
            f"{gate.action.agent_kind or 'unknown'}, title "
            f"{gate.action.title or 'unknown'}. Tool result:\n\n{tool_result}\n\n"
            "Report the outcome to the user in one or two short spoken sentences. "
            "The tool result names the created tab/pane ids and whether the task "
            "was delivered."
        )
        try:
            result = get_llm().ask(
                report_prompt,
                session_id=gate.session_id,
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
        # Speech admission (VS1.1) precedes EVERY side effect: malformed
        # ids and duplicate actives fail fast, and the job sits in
        # waiting_llm before the LLM loop starts so a cancel can land
        # while the model is still thinking.
        job, degraded = _admit_speech_job(body, speech_jobs)
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
            if job is not None:
                # The turn died before shaping: failed is terminal, so the
                # id frees for the client's retry instead of sticking.
                speech_jobs.mark(job.id, PHASE_FAILED)
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        # Persist the turn exactly once, mirroring the ring (raw question
        # + raw answer — the approval closer is response shaping, not
        # content). The seam is deliberately HERE, not inside llm.ask:
        # the approve replay also calls llm.ask with a synthetic report
        # prompt, and that must never enter the call history.
        history.append("user", body.text)
        history.append("assistant", result.get("answer") or "")
        if job is None and degraded is None:
            # Legacy turn: no registry interaction, no speech key —
            # byte-comparable with the pre-speech surface.
            return shape_ask_response(result)
        if degraded is not None:
            # Registry full: text-only degraded. Answer and history behave
            # exactly as always; audio is skipped because an unregistered
            # render could never be cancelled.
            response = shape_ask_response(result, speak=False)
            response["speech"] = {"status": "degraded", "reason": degraded}
            return response
        if job.cancel_requested:
            # Cancel landed mid-flight: it touches ONLY speech — the
            # textual answer and history are returned exactly as always.
            speech_jobs.mark(job.id, PHASE_CANCELLED)
            response = shape_ask_response(result, speak=False)
            response["speech"] = {"id": job.id, "status": job.phase}
            return response
        # Identified, non-degraded, non-cancelled turn (VS2.4): pick the
        # speech path per contract tts-brain-v2.md — the probe is cached
        # per tts_bin, so this adds one host check per binary, not per
        # turn.
        path, reason = choose_speech_path(cfg)
        if path == "segmented":
            # ASYNC segmented dispatch: the response returns right after
            # the LLM while a per-job producer thread drives the host
            # subprocess (render → segment moves → bounded staging).
            producer = SegmentedSpeechProducer(
                cfg, speech_jobs, job, speakable_answer(result)
            )
            job.segments = producer.buffer
            job.producer = producer
            producer.start()
            response = shape_ask_response(result, speak=False)
            # "delivering" is the LITERAL contract dispatch status (T5),
            # NOT an echo of the job's phase — internally the job rides
            # rendering→delivering inside the registry while segments
            # stream; /next (VS2.5) serves them from the registry.
            # audio_url stays None: segments arrive via /next, never as
            # one full file on this path.
            response["speech"] = {"id": job.id, "status": "delivering"}
            return response
        speech_jobs.mark(job.id, PHASE_RENDERING)
        response = shape_ask_response(result, cancel_event=job.cancel_event)
        # speak_answer is fail-soft: a missing audio_url on a turn that
        # had an answer means the render threw. WHICH throw decides the
        # terminal phase honestly (VS1.3): audio won → complete; no
        # audio + a cancel mark → cancelled (the abort landed mid-render
        # — or raced the render, which the audio branch resolved first);
        # any other render failure → failed.
        if response.get("audio_url"):
            speech_jobs.mark(job.id, PHASE_COMPLETE)
        elif job.cancel_requested:
            speech_jobs.mark(job.id, PHASE_CANCELLED)
        else:
            speech_jobs.mark(job.id, PHASE_FAILED)
        # v1-only host (or failed probe) on an identified turn: the
        # legacy full-file flow above stays EXACTLY as-is, plus the
        # contract's VISIBLE degradation marker — the textual answer is
        # never blocked (tts-brain-v2.md, Negotiation).
        response["speech"] = {"id": job.id, "status": job.phase, "degraded": reason}
        return response

    @app.post("/speech/{speech_request_id}/cancel")
    def speech_cancel(speech_request_id: str, body: SpeechCancelRequest) -> dict:
        """Honest, idempotent speech cancel (VS1.3).

        The registry returns a verdict; this maps it to the wire:
        forbidden → 403, unknown → 200 ``unknown-or-expired`` (a purged,
        retention-expired job answers EXACTLY like a never-seen id —
        same shape, same code path, no enumeration), already-terminal →
        200 ``already-complete``, cancelled → 200 ``cancelled``.
        Repeated cancels converge: while the job is still active the
        verdict stays ``cancelled`` (Event.set is idempotent); once
        terminal it is ``already-complete``. Only verdicts are logged
        or returned — the capability token never appears anywhere.
        """
        verdict = speech_jobs.request_cancel(
            speech_request_id, body.speech_cancel_token, body.session_id
        )
        if verdict == "forbidden":
            raise HTTPException(status_code=403, detail="forbidden")
        status = {
            "unknown": "unknown-or-expired",
            "already-terminal": "already-complete",
            "cancelled": "cancelled",
        }[verdict]
        return {"status": status}

    @app.get("/speech/{speech_request_id}/next")
    def speech_next(
        speech_request_id: str,
        after: int = -1,
        ack: int = -1,
        session_id: Optional[str] = None,
    ) -> dict:
        """Dual-watermark long-poll segment transport (VS2.5, design T5).

        ``after`` is the consumption cursor — the endpoint serves seq
        ``after+1`` and nothing else, so a served (and surely acked)
        seq can never be re-served and dedup is inherent. ``ack`` is
        the playback watermark: capacity is released ONLY by a valid
        monotone ack (an equal re-poll is idempotent and releases
        nothing); any violation — ``ack > after``, ``ack < -1``, or
        ``ack < job.ack_watermark`` — is a TYPED 422, never silent.

        Active jobs with nothing ready long-poll up to
        SPEECH_NEXT_HOLD_S (module constant, read at use time); a job
        that went terminal answers its status IMMEDIATELY (T5: next()
        returns the state, never a silent skip). A connection-level
        abort cancels NOTHING — only POST /speech/{id}/cancel may end a
        job; a vanished consumer is reclaimed later, visibly, by the
        lazy sweep at entry.
        """
        sweep_unconsumed(speech_jobs)
        job = speech_jobs.get(speech_request_id)
        if job is None:
            # Unknown id: honest 404, no enumeration of live vs purged.
            raise HTTPException(status_code=404, detail="speech job not found")
        if (
            session_id is not None
            and job.session_id is not None
            and session_id != job.session_id
        ):
            # Session ROUTING (PRD 02 §2C), not a new auth scheme: a
            # mismatched session gets the unknown-id 404 — same shape.
            raise HTTPException(status_code=404, detail="speech job not found")
        with _SPEECH_NEXT_ACK_LOCK:
            if not (-1 <= ack <= after) or ack < job.ack_watermark:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "invalid-ack",
                        "ack": ack,
                        "after": after,
                        "job_ack": job.ack_watermark,
                    },
                )
            if ack > job.ack_watermark:
                # Monotone progress: advance the watermark and release
                # ONLY here (equal/idempotent re-polls release nothing).
                job.ack_watermark = ack
                if job.segments is not None:
                    job.segments.ack_upto(ack)
        buffer = job.segments  # None on every non-segmented path
        want = after + 1
        deadline = time.monotonic() + _speech.SPEECH_NEXT_HOLD_S
        while True:
            staged = buffer.snapshot() if buffer is not None else []
            segment = next((seg for seg in staged if seg["seq"] == want), None)
            if segment is not None and not _next_terminal_broken(job.phase):
                return {
                    "seq": want,
                    "audio_url": f"/audio/{segment['file']}",
                    # Final ONLY when this is the last staged seq AND the
                    # job finished complete (an active job always has a
                    # "maybe more coming" answer).
                    "is_final": (
                        job.phase == PHASE_COMPLETE
                        and want == max(seg["seq"] for seg in staged)
                    ),
                    "mime": "audio/mpeg",
                }
            if is_terminal_phase(job.phase):
                # Terminal answers immediately, no hold: broken jobs
                # never serve new segments; a complete job with every
                # segment consumed reports its terminal status.
                return {"wait": True, "status": job.phase}
            if time.monotonic() >= deadline:
                return {"wait": True}
            time.sleep(_speech.SPEECH_NEXT_POLL_S)

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
        """Manual edit or voice re-dictation result: swaps the frozen
        editable field (text for send gates, task for create gates; the
        wire body is ``text`` either way) and restarts the timer (the
        store resets created_at)."""
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
    settings = load_settings()
    uvicorn.run(create_app(settings), host=host, port=settings.brain_port)


if __name__ == "__main__":  # pragma: no cover
    main()
