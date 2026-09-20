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
from .tools import BrainTools
from .tts import new_audio_path, render_mp3

_TTSRenderer = Callable[[Settings, str, Path], Path]
_LLMFactory = Callable[[Settings, BrainTools], BrainLLM]

_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9._-]+$")
STATIC_DIR = Path(__file__).resolve().parent / "static"


class TextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4_000)


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

    # The LLM client is built lazily: /health and /tts work without
    # GLM_API_KEY, and /ask reports the missing configuration as a 503.
    llm_holder: dict = {}

    def get_llm() -> BrainLLM:
        if "instance" not in llm_holder:
            llm_holder["instance"] = (llm_factory or default_llm_factory)(cfg, tools)
        return llm_holder["instance"]

    app = FastAPI(title="herdr-brain", version=__version__)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/state")
    def state() -> dict:
        """Active agent pane snapshot for the PWA header (read-only)."""
        try:
            active = tools.active_status()
        except HerdrError:
            # A polling endpoint must degrade gracefully when herdr hiccups.
            active = None
        if active is None:
            return {
                "active": False,
                "pane_id": None,
                "agent": None,
                "agent_status": None,
                "title": None,
                "cwd": None,
                "session_id": None,
            }
        return {
            "active": True,
            "pane_id": active.pane_id,
            "agent": active.agent,
            "agent_status": active.status,
            "title": active.title,
            "cwd": active.cwd,
            "session_id": active.session_value,
        }

    @app.post("/ask")
    def ask(body: TextRequest) -> dict:
        try:
            result = get_llm().ask(body.text)
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
    import uvicorn

    from .config import load_settings

    uvicorn.run(create_app(load_settings()), host="127.0.0.1", port=8741)


if __name__ == "__main__":  # pragma: no cover
    main()
