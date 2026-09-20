"""Kokoro-82M v1.0 local ONNX provider (offline, studio-grade neural TTS).

All heavy dependencies are resolved LAZILY at synthesis time, never at import:

- ``onnxruntime`` (``pip install onnxruntime``) is imported only when a
  session is created; if missing, an actionable RuntimeError is raised.
- Phonemization uses espeak-ng, either through the ``phonemizer`` package (if
  already installed) or the ``espeak-ng``/``espeak`` binary via subprocess; if
  neither is present, an actionable RuntimeError is raised.

Model files live in the voice store managed by Mission A
(``agent-tts voice install kokoro``):

- ``<store>/kokoro/model.onnx``     — Kokoro-82M v1.0 ONNX weights (~325 MB fp32)
- ``<store>/kokoro/voices/*.bin``   — per-voice style embeddings (float32 banks)
- ``<store>/kokoro/config.json``    — model config including the phoneme vocab

Exact source URLs (see ``agent_tts.voices``):
- Model + voice bins: ``https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX/resolve/main``
  (official ONNX export of the v1.0 release; the original
  ``hexgrad/Kokoro-82M-v1.0-onnx`` repo is gated behind HF auth — override the
  base with ``AGENT_TTS_KOKORO_BASE_URL`` if you host a mirror).
- Phoneme vocab (``config.json``): ``https://huggingface.co/hexgrad/Kokoro-82M/resolve/main/config.json``

``supports_stream=False``: Kokoro runs whole-utterance Transformer + iSTFTNet
inference per call and emits a complete PCM buffer, so there is no incremental
audio surface to yield frames from. Interactive latency is instead provided by
the CLI's pipelined sentence-group streaming, which already calls
``synthesize()`` once per sentence group while playback runs.
"""

import json
import os
import shutil
import struct
import subprocess
import sys
from typing import Callable, List, Optional

from agent_tts.boundaries import BoundaryMap, SynthesisResult, estimate_boundaries_from_text
from agent_tts.providers.base import TTSProvider, parse_rate_to_multiplier

# Maps a Kokoro voice prefix (e.g. 'ef_dora' -> 'e') to its espeak-ng voice.
ESPEAK_VOICE_MAP = {
    "a": "en-us",   # American English (af_*, am_*)
    "b": "en-gb",   # British English (bf_*, bm_*)
    "e": "es",      # Spanish (ef_*, em_*)
    "f": "fr-fr",   # French (ff_*)
    "h": "hi",      # Hindi (hf_*, hm_*)
    "i": "it",      # Italian (if_*, im_*)
    "j": "ja",      # Japanese (jf_*, jm_*)
    "p": "pt-br",   # Portuguese (pf_*, pm_*)
    "z": "cmn",     # Mandarin (zf_*, zm_*)
}

STYLE_DIM = 256  # float32 style vector per voice entry


def _kokoro_error(message: str) -> RuntimeError:
    return RuntimeError(message)


class KokoroTTSProvider(TTSProvider):
    """Offline local neural TTS provider using Kokoro-82M ONNX models."""

    name: str = "kokoro"
    supports_stream: bool = False
    SAMPLE_RATE = 24000

    DEFAULT_VOICE = "ef_dora"  # sensible Spanish default for this repo

    def __init__(
        self,
        model_path: Optional[str] = None,
        voices_dir: Optional[str] = None,
        config_path: Optional[str] = None,
        store_root: Optional[str] = None,
        session_factory: Optional[Callable[[str], object]] = None,
        phonemize_fn: Optional[Callable[[str, str], str]] = None,
    ):
        from agent_tts.voices import kokoro_model_dir

        bundle = kokoro_model_dir(store_root)
        self.model_path = (
            model_path
            or os.environ.get("AGENT_TTS_KOKORO_MODEL", "")
            or os.path.join(bundle, "model.onnx")
        )
        self.voices_dir = voices_dir or os.environ.get(
            "AGENT_TTS_KOKORO_VOICES_DIR", os.path.join(bundle, "voices")
        )
        self.config_path = config_path or os.environ.get(
            "AGENT_TTS_KOKORO_CONFIG", os.path.join(bundle, "config.json")
        )
        self.session_factory = session_factory
        self.phonemize_fn = phonemize_fn
        self._session = None
        self._token_input_name: Optional[str] = None
        self._speed_rank1: Optional[bool] = None

    # --- Availability & voice resolution ------------------------------------

    def is_available(self) -> bool:
        """True when the ONNX model file exists in the resolved location."""
        return bool(self.model_path) and os.path.isfile(self.model_path)

    def resolve_voice(self, voice: str) -> str:
        """Maps the requested voice to a Kokoro voice id (es voices bundled)."""
        env_voice = os.environ.get("AGENT_TTS_KOKORO_VOICE", "")
        candidate = (voice or "").strip()
        # The CLI default voice belongs to Edge (e.g. es-ES-ElviraNeural);
        # only accept names shaped like Kokoro voice ids (xx_yyyy).
        if candidate and "_" in candidate and not candidate.startswith("es-"):
            return candidate
        if env_voice:
            return env_voice
        return self.DEFAULT_VOICE

    # --- Lazy heavy dependencies ---------------------------------------------

    def _get_session(self):
        """Creates (and caches) the onnxruntime InferenceSession for the model."""
        if self._session is not None:
            return self._session
        if not self.is_available():
            raise _kokoro_error(
                "Kokoro model not found. Install it with:\n"
                "  agent-tts voice install kokoro\n"
                f"(looked for: {self.model_path})"
            )
        try:
            import onnxruntime as ort
        except ImportError as e:
            raise _kokoro_error(
                "Kokoro synthesis requires the optional 'kokoro' dependencies, which are not installed.\n"
                "Install them with:\n"
                "  pip install 'agent-tts[kokoro]'\n"
                "(equivalent to: pip install onnxruntime)\n"
                "Then make sure the model is downloaded: agent-tts voice install kokoro"
            ) from e
        factory = self.session_factory or (
            lambda path: ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        )
        self._session = factory(self.model_path)
        return self._session

    def _espeak_voice(self, voice: str) -> str:
        prefix = (voice or self.DEFAULT_VOICE)[:1].lower()
        return ESPEAK_VOICE_MAP.get(prefix, "en-us")

    def _default_phonemize(self, text: str, voice: str) -> str:
        """Phonemizes text via the phonemizer lib, else the espeak-ng binary."""
        espeak_voice = self._espeak_voice(voice)
        try:
            from phonemizer.backend import EspeakBackend

            backend = EspeakBackend(espeak_voice, language_switch="remove-flags")
            return str(backend.phonemize([text])[0]).strip()
        except ImportError:
            pass
        binary = shutil.which("espeak-ng") or shutil.which("espeak")
        if not binary:
            raise _kokoro_error(
                "Kokoro phonemization requires espeak-ng. Install it with:\n"
                "  sudo apt-get install espeak-ng   # Debian/Ubuntu\n"
                "  brew install espeak-ng           # macOS\n"
                "or: pip install phonemizer (also needs espeak-ng)"
            )
        try:
            proc = subprocess.run(
                [binary, "--ipa", "-q", "--stdin", "-v", espeak_voice],
                input=text.encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=True,
            )
        except (subprocess.SubprocessError, OSError) as e:
            raise _kokoro_error(f"espeak-ng phonemization failed: {e}") from e
        return proc.stdout.decode("utf-8", errors="replace").strip()

    def _phonemize(self, text: str, voice: str) -> str:
        fn = self.phonemize_fn or self._default_phonemize
        return fn(text, voice)

    def _load_vocab(self) -> dict:
        if not os.path.isfile(self.config_path):
            raise _kokoro_error(
                "Kokoro config.json (phoneme vocab) not found. Reinstall the model:\n"
                "  agent-tts voice install kokoro\n"
                f"(looked for: {self.config_path})"
            )
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except (OSError, ValueError) as e:
            raise _kokoro_error(f"Kokoro config.json is unreadable: {e}") from e
        vocab = config.get("vocab")
        if not isinstance(vocab, dict) or not vocab:
            raise _kokoro_error("Kokoro config.json has no 'vocab' table; reinstall with 'agent-tts voice install kokoro'")
        return vocab

    def _tokenize(self, phonemes: str) -> List[int]:
        """Maps IPA phonemes to token ids; unsupported symbols are dropped."""
        vocab = self._load_vocab()
        pad = vocab.get("$", 0)
        ids = [vocab[ch] for ch in phonemes if ch in vocab]
        return [pad] + ids + [pad]

    def _read_style(self, voice: str) -> bytes:
        """Loads the raw float32 style vector (1 x 256) for the voice."""
        voice_file = os.path.join(self.voices_dir, f"{voice}.bin")
        if not os.path.isfile(voice_file):
            raise _kokoro_error(
                f"Kokoro voice '{voice}' is not installed (missing {voice_file}).\n"
                "Install the model bundle with: agent-tts voice install kokoro"
            )
        with open(voice_file, "rb") as f:
            data = f.read(STYLE_DIM * 4)
        if len(data) < STYLE_DIM * 4:
            raise _kokoro_error(f"Kokoro voice file is truncated: {voice_file}")
        return data

    def _token_input_name_for(self, session) -> str:
        """Resolves the model's token input name from the session itself.

        ONNX exports of Kokoro disagree on the token input name: the
        onnx-community/Kokoro-82M-v1.0-ONNX bundle names it ``input_ids``
        while other exports call it ``tokens``. Introspection (cached per
        session) keeps both working; sessions that cannot be introspected
        (test fakes) fall back to the original ``tokens`` name.
        """
        if self._token_input_name is not None:
            return self._token_input_name
        name = "tokens"
        try:
            input_names = [i.name for i in session.get_inputs()]
        except Exception:
            input_names = []
        if "input_ids" in input_names:
            name = "input_ids"
        self._token_input_name = name
        return name

    def _speed_input_for(self, session, speed: float):
        """Builds the ``speed`` feed value matching the model's declared rank.

        ONNX exports disagree here too: the onnx-community bundle declares
        ``speed`` with shape ``[1]`` (rank 1) while other exports use a
        rank-0 scalar. The declared shape decides; sessions that cannot be
        introspected fall back to the original rank-0 scalar.
        """
        if self._speed_rank1 is None:
            rank1 = False
            try:
                for info in session.get_inputs():
                    if info.name == "speed":
                        shape = list(getattr(info, "shape", None) or [])
                        rank1 = bool(shape)  # [] -> rank 0; [1]/[...] -> rank 1
                        break
            except Exception:
                rank1 = False
            self._speed_rank1 = rank1
        if self._speed_rank1:
            import numpy as np

            return np.array([speed], dtype=np.float32)
        return speed

    def _infer(self, tokens: List[int], style: bytes, speed: float) -> bytes:
        """Runs the ONNX model and returns raw little-endian float32 PCM bytes."""
        session = self._get_session()
        try:
            import numpy as np
        except ImportError as e:
            raise _kokoro_error(
                "Kokoro inference requires numpy (installed with the 'kokoro' extra).\n"
                "Install it with:\n"
                "  pip install 'agent-tts[kokoro]'"
            ) from e
        outputs = session.run(
            None,
            {
                self._token_input_name_for(session): np.array([tokens], dtype=np.int64),
                "style": np.frombuffer(style, dtype=np.float32).reshape(1, STYLE_DIM),
                # Rank negotiated per model: onnxruntime rejects bare numpy
                # scalars for rank-1 inputs and vice versa.
                "speed": self._speed_input_for(session, speed),
            },
        )
        audio = outputs[0]
        if isinstance(audio, (bytes, bytearray)):
            return bytes(audio)
        # Real sessions return a numpy float32 array (often 2-D, e.g. (1, N));
        # bytes() on it would iterate numpy scalars and fail. Normalize to
        # contiguous little-endian float32 bytes instead.
        return (
            np.asarray(audio, dtype=np.float32)
            .reshape(-1)
            .astype("<f4", copy=False)
            .tobytes()
        )

    @staticmethod
    def _floats_to_wav(raw_f32: bytes, sample_rate: int = 24000) -> bytes:
        """Converts little-endian float32 PCM samples into a 16-bit WAV file."""
        count = len(raw_f32) // 4
        floats = struct.unpack(f"<{count}f", raw_f32[: count * 4])
        pcm = bytearray(count * 2)
        for i, sample in enumerate(floats):
            clamped = max(-1.0, min(1.0, sample))
            pcm[i * 2 : i * 2 + 2] = struct.pack("<h", int(clamped * 32767))
        data = bytes(pcm)
        header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
        header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
        header += b"data" + struct.pack("<I", len(data))
        return header + data

    # --- Provider contract ----------------------------------------------------

    async def synthesize(
        self,
        text: str,
        voice: str = "",
        rate: str = "+0%",
        volume: str = "+0%",
        pitch: str = "+0Hz",
        stop_checker: Optional[Callable[[], bool]] = None,
    ) -> SynthesisResult:
        """Synthesizes text into WAV bytes (16-bit PCM, 24 kHz mono) via Kokoro."""
        if stop_checker and stop_checker():
            return SynthesisResult(b"", BoundaryMap())
        if not text or not text.strip():
            return SynthesisResult(b"", BoundaryMap())

        voice_id = self.resolve_voice(voice)
        speed = parse_rate_to_multiplier(rate)

        session = self._get_session()  # validates model + onnxruntime up front
        del session
        phonemes = self._phonemize(text, voice_id)
        tokens = self._tokenize(phonemes)
        style = self._read_style(voice_id)
        if stop_checker and stop_checker():
            return SynthesisResult(b"", BoundaryMap())

        raw_f32 = self._infer(tokens, style, speed)
        wav = self._floats_to_wav(raw_f32, self.SAMPLE_RATE)
        if stop_checker and stop_checker():
            return SynthesisResult(b"", BoundaryMap())
        if not wav:
            return SynthesisResult(b"", BoundaryMap())

        duration = max(1.0, (len(wav) - 44) / 2 / float(self.SAMPLE_RATE))
        boundaries = estimate_boundaries_from_text(text, duration)
        if os.environ.get("AGENT_TTS_DEBUG"):
            print(f"Kokoro: {len(phonemes)} phonemes -> {len(wav)} bytes WAV @24kHz", file=sys.stderr)
        return SynthesisResult(wav, boundaries)
