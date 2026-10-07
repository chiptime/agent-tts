"""Generic offline-safe STT core for agent-tts (faster-whisper based).

Extracted from the proven herdr-brain behavior, but host-agnostic and with
one HARD RULE tightened: the engine NEVER hands faster-whisper a repo id
alias. ``Transcriber._load_real_model`` resolves the model to a LOCAL
directory first (existing path, or a ``local_files_only=True`` snapshot
lookup), so a warm-cache race can never degrade into a silent network
fetch. If the model is not fully cached, loading raises
``ModelUnavailableError`` naming the pull command.

Model acquisition policy (HARD RULE, inherited from the brain): the model
is never auto-downloaded at import, boot, or request time. The ONLY
download path is the explicit ``pull`` operation (``agent-tts-stt pull`` /
``pull_model`` here), which tests always mock.

No optional dependency is imported at module scope: ``faster_whisper`` and
``huggingface_hub`` are imported lazily inside the seams that need them,
so importing ``agent_tts.stt`` costs nothing on a base TTS install.
"""

from __future__ import annotations

import errno
import hashlib
import os
import shutil
import stat
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

STATE_LOADING = "loading"
STATE_READY = "ready"
STATE_UNAVAILABLE = "unavailable"

PULL_COMMAND = "agent-tts-stt pull"
EXTRA_HINT = (
    "the STT extra is not installed; install it with "
    "pip install 'agent-tts[stt]' (adds faster-whisper)"
)

# faster-whisper size aliases -> HuggingFace repos (mirrors
# faster_whisper.utils._MODELS for the documented sizes without importing
# ctranslate2 just to answer "is it cached?").
SIZE_ALIASES = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v2": "Systran/faster-whisper-large-v2",
    "large-v3": "Systran/faster-whisper-large-v3",
}

# Defaults deliberately reuse the verified herdr-brain STT defaults
# (small / auto / auto) so both deployments behave identically.
DEFAULT_MODEL = "small"
DEFAULT_DEVICE = "auto"
DEFAULT_COMPUTE = "auto"
_ALLOWED_DEVICES = ("auto", "cpu", "cuda")

ENV_MODEL = "AGENT_TTS_STT_MODEL"
ENV_DEVICE = "AGENT_TTS_STT_DEVICE"
ENV_COMPUTE = "AGENT_TTS_STT_COMPUTE"
ENV_SNAPSHOT_DIR = "AGENT_TTS_STT_SNAPSHOT_DIR"

# Inference file set used for the offline cache probe AND the resolved-
# directory verification. tokenizer.json is REQUIRED on the resolved dir:
# faster-whisper's constructor (transcribe.py, v1.x) falls back to
# tokenizers.Tokenizer.from_pretrained("openai/whisper-tiny") — a Hugging
# Face hub fetch — when the directory lacks it. Verifying the asset set
# makes that fallback unreachable. preprocessor_config.json is optional
# (missing → offline feature defaults).
_REQUIRED_ASSETS = ("config.json", "model.bin", "tokenizer.json")
_INFERENCE_FILES = _REQUIRED_ASSETS


class SttError(Exception):
    """Base for all typed STT errors. ``kind`` is the wire error kind,
    ``exit_code`` is the matching agent-tts-stt CLI exit status."""

    kind = "stt_error"
    exit_code = 1


class InvalidConfigError(SttError):
    kind = "invalid_config"


class MissingExtraError(SttError):
    kind = "missing_extra"
    exit_code = 6


class ModelUnavailableError(SttError):
    kind = "model_unavailable"
    exit_code = 4


@dataclass(frozen=True)
class SttSettings:
    """Generic STT settings (engine-level, not tied to any host)."""

    model: str = DEFAULT_MODEL
    device: str = DEFAULT_DEVICE
    compute_type: str = DEFAULT_COMPUTE

    def __post_init__(self) -> None:
        if not isinstance(self.model, str) or not self.model.strip():
            raise InvalidConfigError("stt model must be a non-empty name or path")
        if self.device not in _ALLOWED_DEVICES:
            raise InvalidConfigError(
                f"stt device must be one of {', '.join(_ALLOWED_DEVICES)}; "
                f"got {self.device!r}"
            )
        if not isinstance(self.compute_type, str) or not self.compute_type.strip():
            raise InvalidConfigError("stt compute_type must be non-empty")

    @classmethod
    def from_env(cls, env: Optional[dict] = None) -> "SttSettings":
        env = os.environ if env is None else env
        return cls(
            model=env.get(ENV_MODEL, DEFAULT_MODEL),
            device=env.get(ENV_DEVICE, DEFAULT_DEVICE),
            compute_type=env.get(ENV_COMPUTE, DEFAULT_COMPUTE),
        )


def model_is_cached(model_name: str) -> bool:
    """True when the model files are already local. NEVER touches the network.

    Accepts faster-whisper size aliases, explicit HF repo ids (checked in
    the default HF cache) and local CT2 directory paths. A missing hub
    library is a typed ``MissingExtraError`` (pull cannot fix that); any
    other hub failure (offline, partial) means "not cached here".
    """
    if Path(model_name).exists():
        return True  # explicit local path
    repo_id = SIZE_ALIASES.get(model_name, model_name)
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError as exc:
        raise MissingExtraError(f"{EXTRA_HINT} (missing: {exc})") from exc
    try:
        for filename in _INFERENCE_FILES:
            if try_to_load_from_cache(repo_id, filename) is None:
                return False
        vocab = (
            try_to_load_from_cache(repo_id, "vocabulary.txt"),
            try_to_load_from_cache(repo_id, "vocabulary.json"),
        )
        return any(v is not None for v in vocab)
    except Exception:  # noqa: BLE001 — anything means "not cached here"
        return False


def _verified_model_dir(path) -> str:
    """Absolute, asset-verified model directory or a typed refusal.

    Symlinks in the path are resolved; the result must be a directory
    holding every offline-required asset so faster-whisper never reaches
    its hub-fetch fallbacks.
    """
    resolved = str(Path(path).resolve())
    missing = [name for name in _REQUIRED_ASSETS if not Path(resolved, name).is_file()]
    if missing:
        raise ModelUnavailableError(
            f"model directory {resolved} is incomplete (missing "
            f"{', '.join(missing)}; tokenizer.json is required so loading "
            f"never falls back to a hub fetch); download it explicitly "
            f"with `{PULL_COMMAND}`"
        )
    return resolved


def _hub_cache_dirs(env: dict) -> list:
    """HF hub cache roots, mirroring the hub's own precedence: when any HF
    cache env var is set, ONLY those roots are used (tests rely on this for
    machine isolation); otherwise the default user cache is scanned."""
    dirs = []
    for key in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        if env.get(key):
            dirs.append(Path(env[key]))
    if env.get("HF_HOME"):
        dirs.append(Path(env["HF_HOME"]) / "hub")
    if dirs:
        return dirs
    return [Path.home() / ".cache" / "huggingface" / "hub"]


def _disk_cached_snapshot(repo_id: str, env: dict) -> Optional[str]:
    """Finds a usable snapshot for ``repo_id`` directly in the local HF cache.

    The hub library's ``snapshot_download(local_files_only=True)`` enforces a
    repo-wide completeness verdict that rejects snapshots missing NON-model
    files (``.gitattributes``, ``README.md``) — a real cache produced by the
    brain's own pull fails it (2026-10-07 smoke test) even though everything
    faster-whisper needs is on disk. So: scan the cache layout here first
    (refs/main revision, else newest snapshot) and only fall back to the hub
    call when no disk candidate exists.
    """
    repo_dirname = "models--" + repo_id.replace("/", "--")
    for cache_dir in _hub_cache_dirs(env):
        repo_dir = cache_dir / repo_dirname
        snapshots = repo_dir / "snapshots"
        if not snapshots.is_dir():
            continue
        candidates = []
        ref = repo_dir / "refs" / "main"
        if ref.is_file():
            candidates.append(snapshots / ref.read_text().strip())
        try:
            candidates.extend(
                sorted(snapshots.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
            )
        except OSError:
            pass
        for snap in candidates:
            if all((snap / name).is_file() for name in _REQUIRED_ASSETS):
                return str(snap)
    return None


def resolve_local_model(model_name: str, env: Optional[dict] = None) -> str:
    """Resolves a model name to a LOCAL, verified directory. No network.

    An existing DIRECTORY is resolved to its absolute path and its assets
    are verified (a regular file is a typed config error — faster-whisper
    would treat it as a hub id). Aliases and repo ids resolve first from a
    direct disk scan of the local HF cache (see :func:`_disk_cached_snapshot`),
    then through a ``local_files_only=True`` snapshot lookup; anything
    absent, offline, or incomplete raises ``ModelUnavailableError`` naming
    the pull command. There is no code path from here to a network fetch: a
    cache/probe race can only end in this typed error.
    """
    env = os.environ if env is None else env
    candidate = Path(model_name)
    if candidate.exists():
        if not candidate.is_dir():
            raise InvalidConfigError(
                f"model path must be a directory with the CT2 model files, "
                f"got regular file {model_name!r}"
            )
        return _verified_model_dir(candidate)
    repo_id = SIZE_ALIASES.get(model_name, model_name)
    disk_snapshot = _disk_cached_snapshot(repo_id, env)
    if disk_snapshot is not None:
        return _verified_model_dir(disk_snapshot)
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise MissingExtraError(f"{EXTRA_HINT} (missing: {exc})") from exc
    try:
        snapshot_dir = snapshot_download(repo_id=repo_id, local_files_only=True)
    except Exception as exc:  # noqa: BLE001 — absent, offline, or corrupt cache
        raise ModelUnavailableError(
            f"model '{model_name}' is not available locally ({exc}); "
            f"download it explicitly with `{PULL_COMMAND}`"
        ) from exc
    return _verified_model_dir(snapshot_dir)


def _uid() -> int:
    return os.geteuid()


def default_snapshot_root(env: Optional[dict] = None) -> str:
    """Runtime snapshot root: explicit env override, else a private
    uid-specific directory under the platform temp root."""
    env = os.environ if env is None else env
    override = env.get(ENV_SNAPSHOT_DIR)
    if override:
        return override
    return os.path.join(tempfile.gettempdir(), f"agent-tts-stt-models-{_uid()}")


def ensure_snapshot_root(path: str) -> str:
    """Creates/validates the snapshot root with the same owner-only policy
    as the runtime dir: real dir (no symlink), owned by this uid, mode 0700."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        os.mkdir(path, 0o700)
        os.chmod(path, 0o700)  # beat umask explicitly
        return path
    problems = []
    if stat.S_ISLNK(st.st_mode):
        problems.append("it is a symlink")
    if not stat.S_ISDIR(st.st_mode):
        problems.append("it is not a directory")
    if st.st_uid != _uid():
        problems.append(f"it is owned by uid {st.st_uid}, not this process")
    if st.st_mode & 0o077:
        problems.append(f"mode {stat.S_IMODE(st.st_mode):o} is more permissive than 0700")
    if problems:
        raise SttError(
            f"refusing unsafe STT snapshot root {path}: " + "; ".join(problems)
        )
    return path


def snapshot_for_runtime(source_dir: str, root: Optional[str] = None) -> str:
    """Private, immutable-for-runtime model snapshot; the offline close.

    faster-whisper (1.2.1 transcribe.py:689-708) selects its tokenizer
    branch by FILE PRESENCE in the model directory — checked AFTER native
    construction — and its fallback
    (``Tokenizer.from_pretrained("openai/whisper-tiny")``) performs a hub
    fetch that ignores ``local_files_only``. This function removes the
    mutable input to that branch: every source entry is HARDLINKED into
    an owner-only 0700 snapshot directory (no data duplication on the
    same filesystem; a byte-copy fallback only for cross-filesystem
    caches), so concurrent deletion of the source cache cannot flip the
    branch — hardlinked inodes stay alive in our directory. A source that
    vanishes mid-snapshot raises the typed offline error, never fetches.
    """
    root = root or default_snapshot_root()
    source = Path(source_dir)
    key = hashlib.sha256(str(source.resolve()).encode("utf-8")).hexdigest()[:16]
    target = Path(ensure_snapshot_root(root)) / f"m-{key}"
    try:
        target.mkdir(mode=0o700)
        os.chmod(target, 0o700)
    except FileExistsError:
        pass  # snapshot reused across worker restarts
    entries = sorted(p.name for p in source.iterdir() if p.is_file() or p.is_symlink())
    for name in entries:
        dst = target / name
        if dst.is_symlink():
            # a previous run on the buggy path (or an adversarial entry)
            # left a link: replace it — snapshot entries must be real files
            dst.unlink()
        elif dst.exists():
            continue  # already snapshotted by a previous run
        # HF cache snapshot entries are RELATIVE symlinks into blobs/.
        # os.link(entry) would link the symlink itself, producing a broken
        # relative link in OUR directory (target resolved from the wrong
        # base) — the exact 2026-10-07 smoke-test failure. Resolve the
        # entry to its real file first so the link (or fallback copy)
        # always captures the blob inode/bytes.
        try:
            src_real = (source / name).resolve(strict=True)
        except OSError as res_exc:
            raise ModelUnavailableError(
                f"model is not available locally (snapshot of "
                f"{source_dir} failed: {name} does not resolve: {res_exc}); "
                f"download it explicitly with `{PULL_COMMAND}`"
            ) from res_exc
        if not src_real.is_file():
            raise ModelUnavailableError(
                f"model is not available locally (snapshot of "
                f"{source_dir} failed: {name} is not a regular file); "
                f"download it explicitly with `{PULL_COMMAND}`"
            )
        try:
            os.link(src_real, dst)  # hardlink the real blob inode
        except FileExistsError:
            continue  # concurrent cooperating worker already placed it
        except OSError as exc:
            if exc.errno not in (errno.EXDEV, errno.EPERM, errno.EMLINK):
                raise ModelUnavailableError(
                    f"model is not available locally (snapshot of "
                    f"{source_dir} failed: {exc}); download it explicitly "
                    f"with `{PULL_COMMAND}`"
                ) from exc
            # Cross-filesystem cache (or link-averse fs): byte copy. Small
            # files always; weights only in this rare layout — the price
            # of never re-reading the mutable cache directory.
            try:
                shutil.copyfile(src_real, dst)
            except OSError as copy_exc:
                raise ModelUnavailableError(
                    f"model is not available locally (snapshot of "
                    f"{source_dir} failed: {copy_exc}); download it "
                    f"explicitly with `{PULL_COMMAND}`"
                ) from copy_exc
    return _verified_model_dir(target)


def _download_model(**kwargs) -> str:
    """Default pull downloader: real snapshot_download (network allowed).
    The hub library check lives here so injected test seams never need it."""
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise MissingExtraError(f"{EXTRA_HINT} (missing: {exc})") from exc
    return snapshot_download(**kwargs)


def pull_model(
    settings: SttSettings,
    downloader: Optional[Callable[..., str]] = None,
) -> str:
    """The ONLY explicit model-download operation (tests always mock it).

    Local paths are returned untouched (nothing to download). Aliases are
    resolved to their repo id and downloaded with a bounded hub timeout so
    a stalled mirror fails instead of hanging.
    """
    downloader = downloader or _download_model
    if Path(settings.model).exists():
        return str(settings.model)
    # Fail fast on a stalled mirror instead of hanging forever.
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "30")
    repo_id = SIZE_ALIASES.get(settings.model, settings.model)
    return downloader(repo_id=repo_id)


class Transcriber:
    """Lazy faster-whisper wrapper with a health state.

    ``loader`` and ``cached`` are injection seams for tests: the default
    ``loader`` imports faster-whisper lazily AND resolves the model to a
    local-only path first; the default ``cached`` probes the local cache
    offline. The model is constructed at most once and stays resident for
    every later request.
    """

    def __init__(
        self,
        settings: SttSettings,
        loader: Optional[Callable[[], object]] = None,
        cached: Optional[Callable[[], bool]] = None,
        snapshot_dir: Optional[str] = None,
    ):
        self._settings = settings
        self._loader = loader or self._load_real_model
        self._cached = cached or (lambda: model_is_cached(settings.model))
        self._snapshot_dir = snapshot_dir
        self._model: Optional[object] = None
        self._model_path: Optional[str] = None
        self._model_lock = threading.Lock()
        self._error: Optional[str] = None
        self.state = STATE_LOADING

    def _load_real_model(self):
        try:
            from faster_whisper import WhisperModel  # imported lazily on purpose
        except ImportError as exc:
            raise MissingExtraError(f"{EXTRA_HINT} (missing: {exc})") from exc
        # HARD RULE (offline close): resolve+verify the cache source, then
        # load from a PRIVATE runtime snapshot this process owns. The
        # dependency picks its tokenizer branch by file presence in the
        # model dir (checked after native construction, and its fallback
        # fetches while ignoring local_files_only) — so the guarantee is
        # "the directory we hand over cannot lose files", which hardlinks
        # in an owner-only dir deliver. local_files_only=True stays as an
        # additional guard on the dependency's non-directory branch.
        local_path = resolve_local_model(self._settings.model)
        runtime_path = snapshot_for_runtime(
            local_path, self._snapshot_dir or default_snapshot_root()
        )
        self._model_path = runtime_path  # absolute path retained until close
        return WhisperModel(
            runtime_path,
            device=self._settings.device,
            compute_type=self._settings.compute_type,
            local_files_only=True,
        )

    def model_present(self) -> bool:
        return bool(self._cached())

    def _ensure_model(self) -> object:
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    try:
                        model = self._loader()
                    except Exception as exc:  # noqa: BLE001 — health must show it
                        self._error = str(exc)
                        self.state = STATE_UNAVAILABLE
                        raise
                    self._model = model
        # Actual load success refreshes health: a retry that succeeds
        # after a failed warmup clears the stale unavailable/error state.
        self.state = STATE_READY
        self._error = None
        return self._model

    def warmup(self) -> None:
        """Loads the model once; failure is recorded, never raised."""
        try:
            self._ensure_model()
            self.state = STATE_READY
        except Exception as exc:  # noqa: BLE001 — health must show the failure
            self._error = str(exc)
            self.state = STATE_UNAVAILABLE

    def maybe_warmup(self) -> bool:
        """Worker-boot warmup with the absence gates applied first.

        A missing extra (typed, from the cache probe) or an absent model
        flips state to unavailable with the actionable hint WITHOUT
        touching the loader — the load path itself cannot download, so
        running warmup only when the model is present keeps health honest
        and costs nothing. Returns True when a load was attempted.
        """
        try:
            present = self.model_present()
        except SttError as exc:
            self._error = str(exc)
            self.state = STATE_UNAVAILABLE
            return False
        if not present:
            self._error = (
                f"model '{self._settings.model}' is not available locally; "
                f"download it explicitly with `{PULL_COMMAND}`"
            )
            self.state = STATE_UNAVAILABLE
            return False
        self.warmup()
        return True

    def error(self) -> Optional[str]:
        return self._error

    def transcribe_bytes(self, data: bytes, suffix: str = ".webm") -> str:
        """Transcribes an encoded audio clip through a bounded temp file.

        Refuses (typed, no loader call) when the model is not present
        locally — transcription is never a download path. The temp file is
        removed on success AND failure.
        """
        if not self.model_present():
            raise ModelUnavailableError(
                f"model '{self._settings.model}' is not available locally; "
                f"download it explicitly with `{PULL_COMMAND}`"
            )
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
