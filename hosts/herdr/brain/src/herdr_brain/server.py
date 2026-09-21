"""HTTP surface of the brain: /ask, /tts, /audio/<file>, /health.

POST /ask runs the LLM tool loop, renders the answer to MP3 (Piper/agent-tts
engine, --no-play) and returns an audio_url; playback happens on the CLIENT,
never on PC speakers. POST /tts is plain TTS for the PWA's local echo.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .config import Settings
from .herdr import HerdrError
from .llm import BrainLLM, BrainLLMError
from .memory import ConversationStore
from .tools import BrainTools
from .tools import status_payload as _status_payload
from .tts import new_audio_path, render_mp3

_TTSRenderer = Callable[[Settings, str, Path], Path]
_LLMFactory = Callable[[Settings, BrainTools], BrainLLM]

_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9._-]+$")
STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_SESSION_ID_CHARS = 128


class TextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4_000)
    session_id: Optional[str] = Field(default=None, max_length=MAX_SESSION_ID_CHARS)
    reset: bool = False


class ResetRequest(BaseModel):
    session_id: Optional[str] = Field(default=None, max_length=MAX_SESSION_ID_CHARS)


def default_llm_factory(settings: Settings, tools: BrainTools) -> BrainLLM:
    return BrainLLM(settings, tools)


def create_app(
    settings: Optional[Settings] = None,
    llm_factory: Optional[_LLMFactory] = None,
    tts_renderer: Optional[_TTSRenderer] = None,
) -> FastAPI:
    """Builds the FastAPI app with injectable LLM and TTS backends for tests."""
    if settings is None:
        from .config import load_settings

        settings = load_settings()
    cfg = settings

    tools = BrainTools(cfg)
    synth = tts_renderer or render_mp3
    store = ConversationStore()

    # The LLM client is built lazily: /health and /tts work without
    # GLM_API_KEY, and /ask reports the missing configuration as a 503.
    llm_holder: dict = {}

    def get_llm() -> BrainLLM:
        if "instance" not in llm_holder:
            llm = (llm_factory or default_llm_factory)(cfg, tools)
            # Custom factories may not wire a store; bind the shared one so
            # /reset and /ask always see the same conversation memory.
            if hasattr(llm, "attach_store"):
                llm.attach_store(store)
            llm_holder["instance"] = llm
        return llm_holder["instance"]

    app = FastAPI(title="herdr-brain", version=__version__)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

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
    def view() -> dict:
        """Superset of /state: transcript tail, screen tail, pending hint."""
        try:
            return tools.agent_view()
        except Exception:  # noqa: BLE001 — the view endpoint never 500s
            return {
                "status": _status_payload(None),
                "transcript": None,
                "screen": None,
                "pending": {"detected": False, "kind": None, "excerpt": None},
            }

    @app.post("/ask")
    def ask(body: TextRequest) -> dict:
        if body.reset:
            store.reset(body.session_id)
        try:
            result = get_llm().ask(body.text, session_id=body.session_id)
        except BrainLLMError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        audio_url: Optional[str] = None
        if result.get("answer"):
            out_path = new_audio_path(cfg)
            try:
                synth(cfg, result["answer"], out_path)
                audio_url = f"/audio/{out_path.name}"
            except Exception:  # noqa: BLE001 — TTS must never break the answer
                audio_url = None
        return {
            "answer": result.get("answer"),
            "pane_id": result.get("pane_id"),
            "agent": result.get("agent"),
            "session_id": result.get("session_id"),
            "audio_url": audio_url,
        }

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
    import os

    import uvicorn

    from .config import load_settings

    # Default loopback; override (e.g. 0.0.0.0 under WSL2 with a Windows-side
    # tailscale serve proxy) via HERDR_BRAIN_HOST.
    host = os.getenv("HERDR_BRAIN_HOST", "127.0.0.1")
    port = int(os.getenv("HERDR_BRAIN_PORT", "8741"))
    uvicorn.run(create_app(load_settings()), host=host, port=port)


if __name__ == "__main__":  # pragma: no cover
    main()
