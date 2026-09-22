"""Server-side speech-to-text with faster-whisper (voice engine v2).

Model policy (HARD RULE): the model is NEVER auto-downloaded at boot or at
import. Downloading is an explicit operator action::

    python -m herdr_brain.stt pull

which shows download progress and runs with HF_HUB_DOWNLOAD_TIMEOUT=30 so a
stalled mirror fails instead of hanging. If the model is not present at
boot, /health reports stt="unavailable" and /transcribe answers 503 with a
hint naming the pull command. If the model IS already present, a daemon
thread warms it so the first real utterance is not cold; the warm path
loads local files only and cannot download.

Language is auto-detected by whisper (language=None); the PWA user speaks
Spanish or English and the model handles both. faster-whisper decodes the
browser's webm/opus clips through bundled PyAV, so no extra tooling is
needed on the client side.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import Callable, Optional

from .config import Settings, load_settings

STATE_LOADING = "loading"
STATE_READY = "ready"
STATE_UNAVAILABLE = "unavailable"

PULL_COMMAND = "python -m herdr_brain.stt pull"
UNAVAILABLE_HINT = (
    "falta el modelo de voz en el servidor: ejecuta "
    f"`{PULL_COMMAND}` y reinicia el servicio"
)

# faster-whisper size aliases -> HuggingFace repos (mirrors
# faster_whisper.utils._MODELS for the sizes we document, without importing
# ctranslate2 just to answer "is it cached?").
_SIZE_ALIASES = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v2": "Systran/faster-whisper-large-v2",
    "large-v3": "Systran/faster-whisper-large-v3",
}


def model_is_cached(model_name: str) -> bool:
    """True when the model files are already local. NEVER touches the network.

    Accepts faster-whisper size aliases, explicit HF repo ids (checked in the
    default HF cache) and local CT2 directory / file paths.
    """
    if Path(model_name).exists():
        return True  # explicit local path
    repo_id = _SIZE_ALIASES.get(model_name, model_name)
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(repo_id=repo_id, local_files_only=True)
        return True
    except Exception:  # noqa: BLE001 — anything means "not cached here"
        return False


class Transcriber:
    """Lazy faster-whisper wrapper with a health state.

    ``loader`` and ``cached`` are injection points for tests: the default
    ``loader`` imports faster-whisper (lazily, first use only) and the
    default ``cached`` checks the local HF cache offline.
    """

    def __init__(
        self,
        settings: Settings,
        loader: Optional[Callable[[], object]] = None,
        state: Optional[str] = None,
        cached: Optional[Callable[[], bool]] = None,
    ):
        self._settings = settings
        self._loader = loader or self._load_real_model
        self._cached = cached or (lambda: model_is_cached(settings.stt_model))
        self._model: Optional[object] = None
        self._model_lock = threading.Lock()
        self._error: Optional[str] = None
        self.state = state if state is not None else STATE_LOADING

    def _load_real_model(self):
        from faster_whisper import WhisperModel  # imported lazily on purpose

        return WhisperModel(
            self._settings.stt_model,
            device=self._settings.stt_device,
            compute_type=self._settings.stt_compute,
        )

    def model_present(self) -> bool:
        return bool(self._cached())

    def maybe_start_warmup(self) -> Optional[threading.Thread]:
        """Starts the background warmup thread ONLY if the model is local.

        Never downloads: downloading is the pull CLI's job. When the model is
        absent the state flips to unavailable (with the pull hint as the
        error) and no thread starts. Returns the thread or None.
        """
        if not self.model_present():
            self._error = UNAVAILABLE_HINT
            self.state = STATE_UNAVAILABLE
            return None
        thread = threading.Thread(target=self.warmup, name="stt-warmup", daemon=True)
        thread.start()
        return thread

    def _ensure_model(self) -> object:
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    self._model = self._loader()
        return self._model

    def warmup(self) -> None:
        """Loads the model (downloading only when called from the pull CLI)."""
        try:
            self._ensure_model()
            self.state = STATE_READY
        except Exception as exc:  # noqa: BLE001 — health must show the failure
            self._error = str(exc)
            self.state = STATE_UNAVAILABLE

    def error(self) -> Optional[str]:
        return self._error

    def transcribe_bytes(self, data: bytes, suffix: str = ".webm") -> str:
        """Transcribes an encoded audio clip (webm/opus from MediaRecorder).

        faster-whisper decodes via bundled PyAV, so any container/codec the
        browser produces works without extra tooling.
        """
        model = self._ensure_model()
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            segments, _info = model.transcribe(tmp_path, language=None)
            text = " ".join(segment.text for segment in segments).strip()
        finally:
            try:
                Path(tmp_path).unlink()
            except OSError:
                pass
        return " ".join(text.split())


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m herdr_brain.stt",
        description="Gestión del modelo de reconocimiento de voz (STT).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "pull",
        help="descarga el modelo STT (con progreso) para que /transcribe funcione",
    )
    args = parser.parse_args(argv)

    if args.command == "pull":
        # Fail fast on a stalled mirror instead of hanging forever.
        os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "30")
        settings = load_settings()
        transcriber = Transcriber(settings)
        name = settings.stt_model
        if transcriber.model_present():
            print(f"El modelo '{name}' ya está descargado — cargando…")
        else:
            print(f"Descargando el modelo '{name}' (puede tardar varios minutos)…")
        transcriber.warmup()
        if transcriber.state != STATE_READY:
            print(f"ERROR: {transcriber.error()}", file=sys.stderr)
            return 1
        print(f"Modelo '{name}' listo (stt=ready). /transcribe ya puede usarse.")
        return 0
    parser.error(f"comando desconocido: {args.command}")  # pragma: no cover
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover - manual entrypoint
    raise SystemExit(main())
