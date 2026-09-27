"""Voice model store and downloader for offline TTS providers (piper, kokoro).

Store root: ``~/.local/share/agent-tts/voices/`` (override with
``AGENT_TTS_VOICES_DIR``), following the same convention as
``AGENT_TTS_PODCAST_DIR``.

Layout (one directory per installed voice, plus a ``voice.json`` manifest):

    <store>/
      es_ES-davefx-medium/          (piper voice)
        voice.json
        es_ES-davefx-medium.onnx
        es_ES-davefx-medium.onnx.json
      kokoro/                       (kokoro-82M model bundle)
        voice.json
        model.onnx                  (fp32 export; model_quantized.onnx when
                                    installed with AGENT_TTS_KOKORO_VARIANT=quantized)
        config.json
        voices/ef_dora.bin
        voices/em_alex.bin
        voices/em_santa.bin
        voices/af_heart.bin

Sources:
- Piper voices follow the rhasspy/piper-voices URL pattern already used by the
  piper provider docs:
  ``https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/<lang>/<lang>_<COUNTRY>/<speaker>/<quality>/<name>.onnx``
- Kokoro-82M v1.0 ONNX (official export):
  ``https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX/resolve/main``
  (the original ``hexgrad/Kokoro-82M-v1.0-onnx`` release is gated behind HF
  auth as of 2026-09; ``AGENT_TTS_KOKORO_BASE_URL`` overrides the base).
  The phoneme vocab ships in ``config.json`` from the public
  ``hexgrad/Kokoro-82M`` model repo.

Installs are atomic: every file is downloaded to a temporary name and moved
into place, and the voice directory itself is renamed into the store only
after all files (and the manifest) are complete.
"""

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple


class VoiceStoreError(RuntimeError):
    """Raised for voice store validation, download, and filesystem errors."""


def get_store_root() -> str:
    """Returns the voice store root (env-overridable, read live)."""
    return os.environ.get(
        "AGENT_TTS_VOICES_DIR",
        os.path.expanduser("~/.local/share/agent-tts/voices"),
    )


# --- Source catalogs ---------------------------------------------------------

PIPER_VOICES_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0"

# Official ONNX export of Kokoro-82M v1.0 (hexgrad's own ONNX repo is gated).
KOKORO_ONNX_BASE = os.environ.get(
    "AGENT_TTS_KOKORO_BASE_URL",
    "https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX/resolve/main",
)
KOKORO_CONFIG_URL = "https://huggingface.co/hexgrad/Kokoro-82M/resolve/main/config.json"
# Default voice bundle: the Spanish set (sensible ES default for this repo)
# plus the flagship English voice.
KOKORO_VOICE_BINS = ("ef_dora", "em_alex", "em_santa", "af_heart")
KOKORO_ALIASES = {"kokoro", "kokoro-82m", "kokoro-82M"}

# Kokoro bundle variant selected via AGENT_TTS_KOKORO_VARIANT: "fp32" (the
# default ~325 MB export, installed as model.onnx) or "quantized" (the ~92 MB
# ONNX export, installed as model_quantized.onnx INSTEAD of model.onnx;
# config.json and the voice bins are identical for both variants).
ENV_KOKORO_VARIANT = "AGENT_TTS_KOKORO_VARIANT"
KOKORO_VARIANT_FP32 = "fp32"
KOKORO_VARIANT_QUANTIZED = "quantized"
KOKORO_VARIANTS = (KOKORO_VARIANT_FP32, KOKORO_VARIANT_QUANTIZED)
KOKORO_QUANTIZED_MODEL = "model_quantized.onnx"

# Piper voice name: <lang>_<COUNTRY>-<speaker>[-<quality>]; quality defaults
# to medium when omitted (e.g. es_ES-davefx -> es_ES-davefx-medium).
PIPER_NAME_RE = re.compile(r"^([a-z]{2,3})_([A-Za-z]{2})-([a-z0-9_]+)(?:-([a-z]{2,8}))?$")

PROGRESS_CHUNK = 1 << 16  # 64 KiB read steps
PROGRESS_MARK = 8 << 20   # progress line every 8 MiB


def piper_voice_urls(name: str) -> Tuple[str, str]:
    """Resolves a piper voice name to its (model, config) HuggingFace URLs."""
    match = PIPER_NAME_RE.match((name or "").strip())
    if not match:
        raise VoiceStoreError(
            f"Unknown voice '{name}'. Use a piper voice name like "
            "'es_ES-davefx-medium' (or 'es_ES-davefx'; quality defaults to medium), "
            "or 'kokoro' for the Kokoro-82M model bundle. "
            "Run 'agent-tts voice list' to see installed voices."
        )
    lang, country, speaker, quality = match.groups()
    quality = quality or "medium"
    base = f"{PIPER_VOICES_BASE}/{lang}/{lang}_{country.upper()}/{speaker}/{quality}"
    full_name = f"{lang}_{country.upper()}-{speaker}-{quality}"
    return f"{base}/{full_name}.onnx", f"{base}/{full_name}.onnx.json"


def kokoro_variant_from_env(env=None) -> str:
    """Reads the kokoro bundle variant from AGENT_TTS_KOKORO_VARIANT.

    Returns "fp32" when unset; "quantized" selects the ~92 MB export.
    Any other value raises VoiceStoreError with the accepted values.
    """
    env = os.environ if env is None else env
    raw = (env.get(ENV_KOKORO_VARIANT, "") or "").strip().lower()
    if not raw:
        return KOKORO_VARIANT_FP32
    if raw in KOKORO_VARIANTS:
        return raw
    raise VoiceStoreError(
        f"invalid {ENV_KOKORO_VARIANT}: {raw!r} (expected one of: {', '.join(KOKORO_VARIANTS)})"
    )


def _kokoro_plan(variant: str = KOKORO_VARIANT_FP32) -> List[Tuple[str, str]]:
    """Relative-path -> URL plan for the kokoro-82M model bundle.

    The quantized variant installs ``model_quantized.onnx`` (~92 MB) instead
    of the fp32 ``model.onnx`` (~325 MB); config.json and the voice bins are
    shared by both variants.
    """
    model_name = KOKORO_QUANTIZED_MODEL if variant == KOKORO_VARIANT_QUANTIZED else "model.onnx"
    plan: List[Tuple[str, str]] = [
        (model_name, f"{KOKORO_ONNX_BASE}/onnx/{model_name}"),
        ("config.json", KOKORO_CONFIG_URL),
    ]
    for voice in KOKORO_VOICE_BINS:
        plan.append((f"voices/{voice}.bin", f"{KOKORO_ONNX_BASE}/voices/{voice}.bin"))
    return plan


# --- Validation --------------------------------------------------------------

def _validate_voice_name(name: str) -> str:
    """Rejects names that could escape the store root or confuse the registry."""
    value = (name or "").strip()
    if not value:
        raise VoiceStoreError("Voice name is required (e.g. 'agent-tts voice install es_ES-davefx-medium')")
    if value.startswith("."):
        raise VoiceStoreError(f"Invalid voice name '{name}': must not start with '.'")
    if re.search(r"[\s/\\\0]", value):
        raise VoiceStoreError(f"Invalid voice name '{name}': path separators and whitespace are not allowed")
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*$", value):
        raise VoiceStoreError(f"Invalid voice name '{name}': only letters, digits, '.', '_' and '-' are allowed")
    return value


def _ensure_inside_store(root: str, target: str) -> str:
    """Realpath containment check: target must resolve inside the store root."""
    real_root = os.path.realpath(root)
    real_target = os.path.realpath(target)
    if real_target != real_root and not real_target.startswith(real_root + os.sep):
        raise VoiceStoreError(f"Refusing to operate outside the voice store: {target}")
    return real_target


# --- Download ----------------------------------------------------------------

def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(PROGRESS_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def download_file(
    url: str,
    dest: str,
    sha256: Optional[str] = None,
    progress: bool = True,
    timeout: int = 60,
) -> int:
    """Downloads ``url`` into ``dest`` atomically (tmp + move) with stdlib urllib.

    Validates integrity when the source provides it: a server-reported
    Content-Length must match the received byte count, and an explicit
    ``sha256`` is verified after the download. Progress goes to stderr.

    Returns the number of bytes written.
    """
    dest_dir = os.path.dirname(dest) or "."
    os.makedirs(dest_dir, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=dest_dir, prefix=".download-", suffix=".part")
    received = 0
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp, os.fdopen(tmp_fd, "wb") as out:
            length_header = resp.headers.get("Content-Length")
            expected_size = int(length_header) if length_header and length_header.isdigit() else None
            next_mark = PROGRESS_MARK
            while True:
                chunk = resp.read(PROGRESS_CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                received += len(chunk)
                if progress and received >= next_mark:
                    print(f"  {url.split('/')[-1]}: {received / (1 << 20):.1f} MB", file=sys.stderr)
                    next_mark += PROGRESS_MARK
        if expected_size is not None and received != expected_size:
            raise VoiceStoreError(
                f"Download truncated for {url}: received {received} bytes, expected {expected_size}"
            )
        if sha256:
            actual = _sha256_file(tmp_path)
            if actual != sha256:
                raise VoiceStoreError(f"Checksum mismatch for {url}: expected {sha256}, got {actual}")
        os.replace(tmp_path, dest)
    except VoiceStoreError:
        _discard(tmp_path)
        raise
    except urllib.error.HTTPError as e:
        _discard(tmp_path)
        raise VoiceStoreError(f"HTTP {e.code} downloading {url}: {e.reason}") from e
    except (urllib.error.URLError, OSError) as e:
        _discard(tmp_path)
        raise VoiceStoreError(f"Network error downloading {url}: {e}") from e
    if progress:
        print(f"Downloaded {url} -> {dest} ({received / (1 << 20):.2f} MB)", file=sys.stderr)
    return received


def _discard(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


# --- Install / list / remove -------------------------------------------------

def install_voice(
    name: str,
    store_root: Optional[str] = None,
    fetch: Optional[Callable[[str, str], None]] = None,
    progress: bool = True,
) -> str:
    """Downloads a voice into the store and returns the installed voice directory.

    ``fetch(url, dest_path)`` is injectable for tests; the default streams via
    :func:`download_file`. The voice directory is renamed into the store only
    after every file and the ``voice.json`` manifest are complete.
    """
    root = store_root or get_store_root()
    clean = _validate_voice_name(name)
    target = os.path.join(root, clean)
    if os.path.exists(target):
        raise VoiceStoreError(f"Voice '{clean}' is already installed at {target} (remove it first to reinstall)")

    variant: Optional[str] = None
    if clean.lower() in {a.lower() for a in KOKORO_ALIASES}:
        variant = kokoro_variant_from_env()
        display, provider, plan = "kokoro", "kokoro", _kokoro_plan(variant)
    else:
        display, provider = clean, "piper"
        onnx_url, json_url = piper_voice_urls(clean)
        plan = [(f"{clean}.onnx", onnx_url), (f"{clean}.onnx.json", json_url)]

    os.makedirs(root, exist_ok=True)
    tmp_dir = tempfile.mkdtemp(dir=root, prefix=f".tmp-{clean}-")
    try:
        for rel_path, url in plan:
            dest = os.path.join(tmp_dir, rel_path)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if fetch is not None:
                fetch(url, dest)
            else:
                download_file(url, dest, progress=progress)
        manifest = {
            "name": display,
            "provider": provider,
        }
        if variant is not None:
            # Kokoro-only: which model export this bundle carries.
            manifest["variant"] = variant
        manifest["installed_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        manifest["files"] = {rel: url for rel, url in plan}
        with open(os.path.join(tmp_dir, "voice.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        os.replace(tmp_dir, target)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    return target


def list_voices(store_root: Optional[str] = None) -> List[Dict]:
    """Returns installed voices as dicts: name, provider, path, files, default."""
    root = store_root or get_store_root()
    if not os.path.isdir(root):
        return []

    piper_default = os.environ.get("PIPER_MODEL", "")
    entries: List[Dict] = []
    for name in sorted(os.listdir(root)):
        voice_dir = os.path.join(root, name)
        if name.startswith(".") or not os.path.isdir(voice_dir):
            continue
        provider = None
        files: List[str] = []
        manifest_path = os.path.join(voice_dir, "voice.json")
        if os.path.isfile(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
                provider = manifest.get("provider")
                files = sorted(manifest.get("files", {}).keys())
            except (OSError, ValueError):
                provider = None
        if provider is None:
            # Fallback for manually installed models (no manifest).
            onnx = [f for f in sorted(os.listdir(voice_dir)) if f.endswith(".onnx")]
            if os.path.isfile(os.path.join(voice_dir, "model.onnx")) and os.path.isfile(
                os.path.join(voice_dir, "config.json")
            ):
                provider, files = "kokoro", sorted(
                    os.path.relpath(os.path.join(voice_dir, f), voice_dir)
                    for f in _walk_files(voice_dir)
                )
            elif onnx:
                provider, files = "piper", sorted(_walk_files(voice_dir))
            else:
                continue

        is_default = False
        if provider == "piper" and piper_default:
            # PIPER_MODEL may point at a store file or elsewhere; compare both
            # the raw basename and its .onnx-stripped stem against this voice.
            default_base = os.path.basename(piper_default)
            default_stem = default_base[: -len(".onnx")] if default_base.endswith(".onnx") else default_base
            is_default = default_base in files or default_stem == name
        if provider == "kokoro":
            is_default = name == "kokoro"
        entries.append(
            {
                "name": name,
                "provider": provider,
                "path": voice_dir,
                "files": files,
                "default": is_default,
            }
        )
    return entries


def _walk_files(directory: str) -> List[str]:
    found: List[str] = []
    for base, _dirs, names in os.walk(directory):
        for n in names:
            found.append(os.path.relpath(os.path.join(base, n), directory))
    return sorted(found)


def remove_voice(name: str, store_root: Optional[str] = None) -> str:
    """Deletes an installed voice directory after strict path-safety checks."""
    root = store_root or get_store_root()
    clean = _validate_voice_name(name)
    target = os.path.join(root, clean)
    real_target = _ensure_inside_store(root, target)
    if not os.path.isdir(real_target):
        raise VoiceStoreError(f"Voice '{clean}' is not installed (run 'agent-tts voice list')")
    shutil.rmtree(real_target)
    return real_target


def kokoro_model_dir(store_root: Optional[str] = None) -> str:
    """Default installed location of the kokoro-82M bundle inside the store."""
    return os.path.join(store_root or get_store_root(), "kokoro")


def kokoro_variant_model_path(bundle: str, env=None) -> str:
    """Returns the bundle's quantized model path when the variant requests it and the file exists.

    AGENT_TTS_KOKORO_VARIANT=quantized prefers ``<bundle>/model_quantized.onnx``.
    Any other value (including unset) resolves to "" so callers fall back to
    the fp32 ``model.onnx``; a requested-but-missing quantized export also
    returns "" so provider availability stays truthful.
    """
    env = os.environ if env is None else env
    raw = (env.get(ENV_KOKORO_VARIANT, "") or "").strip().lower()
    if raw != KOKORO_VARIANT_QUANTIZED:
        return ""
    path = os.path.join(bundle, KOKORO_QUANTIZED_MODEL)
    return path if os.path.isfile(path) else ""


# --- CLI ---------------------------------------------------------------------

def handle_voice_command(argv: List[str], store_root: Optional[str] = None, out=None) -> int:
    """Entry point for ``agent-tts voice <subcommand>``. Returns a process exit code."""
    import argparse

    parser = argparse.ArgumentParser(prog="agent-tts voice", description="Manage offline voice models")
    sub = parser.add_subparsers(dest="action", required=True)

    p_list = sub.add_parser("list", help="List installed voices and their provider")
    p_list.add_argument(
        "provider",
        nargs="?",
        help="Only list this provider's voices (see the catalog with --json)",
    )
    p_list.add_argument(
        "--json",
        action="store_true",
        help="Print the voice catalog as JSON (all providers, or one provider's list)",
    )

    p_install = sub.add_parser("install", help="Download a voice model into the store")
    p_install.add_argument("name", help="Voice name: 'kokoro' or a piper voice (e.g. es_ES-davefx-medium)")

    p_remove = sub.add_parser("remove", help="Delete an installed voice from the store")
    p_remove.add_argument("name", help="Installed voice name (see 'agent-tts voice list')")

    args = parser.parse_args(argv)
    out = out or sys.stdout

    if args.action == "list":
        # Catalog mode: with a provider argument and/or --json, print the
        # built-in catalog (provider_names/provider_voices) instead of the
        # installed-voice store listing.
        if args.provider or args.json:
            from agent_tts.providers import provider_names, provider_voices

            if args.provider:
                try:
                    names = provider_voices(args.provider)
                except ValueError as e:
                    print(f"Error: {e}", file=sys.stderr)
                    return 1
                if args.json:
                    print(json.dumps(names), file=out)
                else:
                    for name in names:
                        print(name, file=out)
                return 0
            names = provider_names()
            catalog = {"providers": names, "voices": {p: provider_voices(p) for p in names}}
            print(json.dumps(catalog), file=out)
            return 0

        entries = list_voices(store_root=store_root)
        if not entries:
            print(f"No voices installed in {store_root or get_store_root()}", file=out)
            print("Install one with: agent-tts voice install kokoro", file=out)
            return 0
        for entry in entries:
            marker = " [default]" if entry["default"] else ""
            print(f"{entry['name']}  ({entry['provider']}){marker}  {entry['path']}", file=out)
        return 0

    if args.action == "install":
        try:
            path = install_voice(args.name, store_root=store_root)
        except VoiceStoreError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        print(f"Installed '{args.name}' -> {path}", file=out)
        return 0

    if args.action == "remove":
        try:
            remove_voice(args.name, store_root=store_root)
        except VoiceStoreError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        print(f"Removed '{args.name}'", file=out)
        return 0

    parser.error(f"Unknown action {args.action!r}")
    return 2
