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
from pydantic import BaseModel, Field

from . import __version__
from .config import Settings
from .llm import BrainLLM, BrainLLMError
from .tools import BrainTools
from .tts import new_audio_path, render_mp3

_TTSRenderer = Callable[[Settings, str, Path], Path]
_LLMFactory = Callable[[Settings, BrainTools], BrainLLM]

_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9._-]+$")


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

    return app


def main() -> None:  # pragma: no cover - manual entrypoint
    import uvicorn

    from .config import load_settings

    uvicorn.run(create_app(load_settings()), host="127.0.0.1", port=8741)


if __name__ == "__main__":  # pragma: no cover
    main()
