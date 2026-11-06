"""Server-side speech-to-text with faster-whisper (voice engine v2).

Two backends (F4.10): the DEFAULT ``engine`` backend delegates to the
agent-tts resident worker through its CLI (see stt_engine.py — the brain
never runs faster-whisper in-process nor imports agent_tts);
``HERDR_BRAIN_STT_BACKEND=builtin`` is the escape hatch back to this
module's legacy in-process Transcriber. Both share the same HTTP
contract (/health stt field, /transcribe 503 semantics with hint).

Model policy (HARD RULE): the model is NEVER auto-downloaded at boot or
at import. Downloading is an explicit operator action::

    python -m herdr_brain.stt pull

which shows download progress and runs with HF_HUB_DOWNLOAD_TIMEOUT=30 so
a stalled mirror fails instead of hanging. In engine mode the pull
delegates to the engine pull CLI (streaming passthrough — still the only
download path). If the model is not present at boot, /health reports
stt="unavailable" and /transcribe answers 503 with a hint naming the
pull command. If the model IS already present, a daemon thread warms it
so the first real utterance is not cold; the warm path loads local files
only and cannot download.

Language is auto-detected by whisper (language=None); the PWA user speaks
Spanish or English and the model handles both. faster-whisper decodes the
browser's webm/opus clips through bundled PyAV, so no extra tooling is
needed on the client side.

The live engine ("Dictado: En vivo", S4) rides a DEDICATED LiveTranscriber
below — same Transcriber shape, its own model (large-v3 by default), same
never-download rule via local_files_only=True, degrading once to a
fallback model with a socket notice when VRAM is short (D13).
"""

from __future__ import annotations

import argparse
import io
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

# S4 notice reasons: forwarded to the live client ONCE per connection as
# {"type": "notice", "reason": ...}. Informational only — unlike an
# "error" message a notice never triggers the client's Servidor fallback.
LIVE_NOTICE_OOM = "live_stt_fallback_oom"
LIVE_NOTICE_MODEL_ABSENT = "live_stt_fallback_model_absent"

# D8: the live engine's recognition hint — the transcription language is
# forced to the app locale and an operator-editable list of technical
# terms rides along as the whisper initial prompt. The default carries
# the vocabulary this project is actually spoken about; operators
# override it with HERDR_BRAIN_LIVE_STT_VOCABULARY (comma/space
# separated; whitespace-only disables the hint entirely).
DEFAULT_LIVE_VOCABULARY = ("commit", "push", "PR", "rama", "herdr", "opencode")
LIVE_VOCABULARY_ENV = "HERDR_BRAIN_LIVE_STT_VOCABULARY"
_MAX_VOCAB_TERMS = 60
_MAX_VOCAB_TERM_CHARS = 40
_MAX_VOCAB_PROMPT_CHARS = 300


def live_vocabulary_hint() -> Optional[str]:
    """The D8 technical-vocabulary prompt for live transcription.

    Bounded and control-character free by construction: a hostile or
    oversized env value can never blow up the whisper prompt. Returns
    None when the operator disabled the hint (whitespace-only value).
    """
    raw = os.environ.get(LIVE_VOCABULARY_ENV)
    if raw is None:
        terms: list[str] = list(DEFAULT_LIVE_VOCABULARY)
    else:
        terms = [
            term for term in
            (chunk.strip() for chunk in raw.replace(";", ",").split(","))
            if term
        ]
    cleaned: list[str] = []
    for term in terms:
        # Keep printable characters and inner spaces; drop control
        # characters (newlines, NULs, tabs) and normalize whitespace runs.
        safe = "".join(
            ch for ch in term
            if ch.isprintable() and (not ch.isspace() or ch == " ")
        )
        safe = " ".join(safe.split())[:_MAX_VOCAB_TERM_CHARS].strip()
        if safe:
            cleaned.append(safe)
    prompt = " ".join(cleaned[:_MAX_VOCAB_TERMS])
    if len(prompt) > _MAX_VOCAB_PROMPT_CHARS:
        prompt = prompt[:_MAX_VOCAB_PROMPT_CHARS].rsplit(" ", 1)[0]
    return prompt or None

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

    Accepts faster-whisper size aliases, explicit HF repo ids (checked in
    the default HF cache) and local CT2 directory / file paths. Checks exactly
    the file set faster-whisper's download_model allow_patterns requests —
    a full-snapshot check would be too strict: an interrupted first download
    leaves README/.gitattributes missing while the model itself is complete
    and perfectly usable offline.
    """
    if Path(model_name).exists():
        return True  # explicit local path
    repo_id = _SIZE_ALIASES.get(model_name, model_name)
    try:
        from huggingface_hub import try_to_load_from_cache

        for filename in ("config.json", "model.bin", "tokenizer.json"):
            if try_to_load_from_cache(repo_id, filename) is None:
                return False
        # Vocabulary ships as .txt (Systran CT2 models) or .json.
        vocab = (
            try_to_load_from_cache(repo_id, "vocabulary.txt"),
            try_to_load_from_cache(repo_id, "vocabulary.json"),
        )
        return any(v is not None for v in vocab)
    except Exception:  # noqa: BLE001 — anything means "not cached here"
        return False


def resolve_snapshot_dir(model_ref: str) -> Optional[str]:
    """Resolves a live-model reference to what WhisperModel accepts.

    Three shapes (S4): a plain model name (returned unchanged —
    WhisperModel resolves it from the local HF cache under
    ``local_files_only=True``), an already-usable model directory (a CT2
    export or a snapshot dir; returned unchanged), and a HuggingFace repo
    cache dir (``models--org--name`` layout), which WhisperModel cannot
    use directly: its ``snapshots/<hash>`` directory is resolved via
    ``refs/main`` or, without the ref, the single snapshot present.
    Returns None when a repo cache dir holds no usable snapshot (missing
    ref and zero or multiple snapshots). NEVER touches the network.
    """
    path = Path(model_ref)
    if not path.is_dir():
        return model_ref  # a plain name / repo id — cache resolution is WhisperModel's
    snapshots = path / "snapshots"
    if not snapshots.is_dir():
        return model_ref  # already a model dir, not a repo cache layout
    ref_file = path / "refs" / "main"
    if ref_file.is_file():
        snap = snapshots / ref_file.read_text(encoding="utf-8").strip()
        if snap.is_dir():
            return str(snap)
    entries = [entry for entry in snapshots.iterdir() if entry.is_dir()]
    if len(entries) == 1:
        return str(entries[0])
    return None


def _live_model_files_present(resolved_ref: Optional[str]) -> bool:
    """Offline presence check for a RESOLVED live-model reference.

    Stricter than :func:`model_is_cached` for directories: a snapshot dir
    without the CT2 weights is an interrupted cache and counts as absent,
    so the live engine degrades to its fallback instead of failing live
    calls at load time. NEVER touches the network.
    """
    if resolved_ref is None:
        return False
    if Path(resolved_ref).is_dir():
        return (Path(resolved_ref) / "model.bin").is_file()
    return model_is_cached(resolved_ref)


def _is_cuda_oom_error(exc: BaseException) -> bool:
    """True for the RuntimeErrors the live engine degrades on (D13):
    CUDA or out-of-memory failures at model load or transcription."""
    if not isinstance(exc, RuntimeError):
        return False
    text = str(exc).lower()
    return "out of memory" in text or "cuda" in text


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

    def transcribe_in_memory(self, data: bytes, suffix: str = ".wav",
                             language: Optional[str] = None,
                             initial_prompt: Optional[str] = None) -> str:
        """Transcribes audio data in-memory without persisting raw audio to disk (D9).

        ``language`` and ``initial_prompt`` carry the live engine's D8
        hints (app locale + technical vocabulary); both stay None on the
        shared paths so the model call shape is unchanged for them.
        """
        model = self._ensure_model()
        buf = io.BytesIO(data)
        segments, _info = model.transcribe(
            buf, language=language, initial_prompt=initial_prompt
        )
        text = " ".join(segment.text for segment in segments).strip()
        return " ".join(text.split())


class LiveTranscriber(Transcriber):
    """Dedicated in-process transcriber for the live engine (S4, D7/D12/D13).

    Same shape and machinery as the shared :class:`Transcriber` (lazy load,
    warmup/READY states, ``transcribe_in_memory``) so the socket keeps one
    contract, with the live policy wrapped around the loader:

    - the primary model (``settings.live_stt_model`` — a name, a snapshot
      dir, or an HF repo cache dir) is loaded STRICTLY from local files
      (``local_files_only=True`` — the never-download HARD RULE) on the
      configured device/compute;
    - an absent primary, or a CUDA/OOM ``RuntimeError`` at load or
      transcribe, degrades ONCE to ``settings.live_stt_fallback_model``
      and records :attr:`notice_reason`, which the socket forwards to the
      client as a single informational ``{"type": "notice"}`` message;
    - any other load error propagates (the standard warmup/unavailable
      path) — degradation is never a mask for a broken model.

    ``loader`` and ``fallback_loader`` are injection points for tests; the
    boot warmup only starts when the PRIMARY is local, so a default
    deploy without ``HERDR_BRAIN_LIVE_STT_MODEL`` never touches the GPU at
    boot (the fallback loads lazily with its notice on first use).
    """

    def __init__(
        self,
        settings: Settings,
        loader: Optional[Callable[[], object]] = None,
        state: Optional[str] = None,
        cached: Optional[Callable[[], bool]] = None,
        fallback_loader: Optional[Callable[[], object]] = None,
    ):
        # The wrapped loader IS the live policy; the primary stays
        # injectable exactly like the base Transcriber's loader.
        self._primary_loader = loader or self._load_real_primary
        self._fallback_loader = fallback_loader or self._load_real_fallback
        self._fallback_active = False
        self.notice_reason: Optional[str] = None
        super().__init__(
            settings,
            loader=self._load_primary_or_fallback,
            state=state,
            # "Present" for the boot gate: EITHER model can serve live
            # audio — an absent primary degrades, it does not block.
            cached=cached or self._any_live_model_present,
        )

    # ── presence (offline, never downloads) ──

    def _primary_model_present(self) -> bool:
        return _live_model_files_present(
            resolve_snapshot_dir(self._settings.live_stt_model)
        )

    def _any_live_model_present(self) -> bool:
        if self._primary_model_present():
            return True
        return _live_model_files_present(
            resolve_snapshot_dir(self._settings.live_stt_fallback_model)
        )

    # ── loaders ──

    def _load_real_primary(self):
        return _load_local_whisper(
            self._settings.live_stt_model, self._settings
        )

    def _load_real_fallback(self):
        return _load_local_whisper(
            self._settings.live_stt_fallback_model, self._settings
        )

    def _load_primary_or_fallback(self) -> object:
        """The live load policy around the primary loader.

        Absent primary → fallback + notice BEFORE touching the GPU.
        CUDA/OOM RuntimeError at load → fallback + notice. Anything else
        propagates (warmup marks the transcriber unavailable, as usual).
        Runs under the base ``_ensure_model`` lock.
        """
        if not self._primary_model_present():
            return self._load_fallback_noticing(LIVE_NOTICE_MODEL_ABSENT)
        try:
            return self._primary_loader()
        except RuntimeError as exc:
            if _is_cuda_oom_error(exc):
                return self._load_fallback_noticing(LIVE_NOTICE_OOM)
            raise

    def _load_fallback_noticing(self, notice_reason: str) -> object:
        model = self._fallback_loader()
        self._fallback_active = True
        self.notice_reason = notice_reason
        return model

    # ── boot warmup ──

    def maybe_start_warmup(self) -> Optional[threading.Thread]:
        """Live boot rule: the warm thread starts ONLY when the primary
        model is local. With the primary absent (e.g. the default name
        without HERDR_BRAIN_LIVE_STT_MODEL) boot stays cold and quiet —
        the first live request degrades lazily to the fallback with its
        notice; the thread's OOM-at-load degradation still covers the
        configured deploy.
        """
        if not self._primary_model_present():
            return None
        return super().maybe_start_warmup()

    # ── transcription with D13 degradation ──

    def transcribe_in_memory(self, data: bytes, suffix: str = ".wav",
                             language: Optional[str] = None,
                             initial_prompt: Optional[str] = None) -> str:
        """In-memory transcription (D9: the only live path), degrading
        once to the fallback when CUDA/OOM strikes mid-call and retrying
        the same audio on it — with the D8 hints (app locale, technical
        vocabulary) intact on the retry. A second OOM propagates —
        degradation is exactly once."""
        try:
            return super().transcribe_in_memory(
                data, suffix, language=language, initial_prompt=initial_prompt
            )
        except RuntimeError as exc:
            if not _is_cuda_oom_error(exc):
                raise
            with self._model_lock:
                if not self._fallback_active:
                    self._model = self._fallback_loader()
                    self._fallback_active = True
                    self.notice_reason = LIVE_NOTICE_OOM
            return super().transcribe_in_memory(
                data, suffix, language=language, initial_prompt=initial_prompt
            )


def _load_local_whisper(model_ref: str, settings: Settings):
    """Loads WhisperModel strictly from local files (the HARD RULE).

    ``local_files_only=True`` keeps a missing cache entry a loud error
    instead of a download attempt — the pull CLI stays the only download
    path, exactly like the shared backend.
    """
    resolved = resolve_snapshot_dir(model_ref)
    if resolved is None:
        raise RuntimeError(
            f"el modelo en vivo '{model_ref}' no tiene un snapshot utilizable"
        )
    from faster_whisper import WhisperModel  # imported lazily on purpose

    return WhisperModel(
        resolved,
        device=settings.live_stt_device,
        compute_type=settings.live_stt_compute,
        local_files_only=True,
    )


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
        if settings.stt_backend == "engine":
            # Engine backend: the engine CLI owns the download and the
            # progress output streams straight to the operator.
            from .stt_engine import run_engine_pull

            return run_engine_pull()
        if settings.stt_backend == "hub":
            print("The hub manages its own STT model; no local model was downloaded.")
            return 0
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
