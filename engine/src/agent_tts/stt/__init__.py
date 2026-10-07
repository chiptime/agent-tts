"""agent-tts STT subpackage: generic offline-safe speech-to-text.

Importing this package is cheap (stdlib only): faster-whisper and
huggingface_hub are imported lazily inside the seams that use them, so a
base TTS install carries no STT burden. Public names resolve lazily too.
"""

from typing import Any

_LAZY_EXPORTS = {
    "SttSettings": "transcriber",
    "Transcriber": "transcriber",
    "SttError": "transcriber",
    "ModelUnavailableError": "transcriber",
    "MissingExtraError": "transcriber",
    "InvalidConfigError": "transcriber",
    "pull_model": "transcriber",
    "model_is_cached": "transcriber",
    "SttWorker": "worker",
    "stt_request": "worker",
    "CaptureConfig": "capture",
    "PowerShellCapture": "capture",
    "CaptureUnavailableError": "capture",
    "CaptureOversizeError": "capture",
    "EmptyCaptureError": "capture",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> Any:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(f".{module_name}", __name__), name)
    globals()[name] = value
    return value
