"""``agent-tts-stt`` console entry point: explicit STT operations.

Operations: ``pull`` (the only model download), ``serve`` (resident
worker), ``status`` and ``transcribe`` (worker clients — no autostart,
the existing ``agent-tts`` positional-speech CLI is never intercepted),
and ``capture`` (local Windows-host microphone recording — no worker).

Request operations (``status``/``transcribe``/``capture``) print ONE
machine-readable JSON line on stdout; failures also print a JSON error
object on stdout plus a human hint on stderr, and exit with the typed
status code:

    0 ok · 1 error · 2 usage · 3 busy · 4 model_unavailable ·
    5 worker_unavailable · 6 missing_extra · 7 transport_unsupported ·
    8 capture_unavailable · 9 empty_capture
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Optional

from agent_tts.stt import worker as stt_worker
from agent_tts.stt.capture import CaptureConfig, PowerShellCapture
from agent_tts.stt.transcriber import (
    SttError,
    SttSettings,
    Transcriber,
    pull_model,
)

STATUS_TIMEOUT_SEC = 10.0


def _socket_arg(args) -> Optional[str]:
    return getattr(args, "socket", None)


def _settings_from(args) -> SttSettings:
    settings = SttSettings.from_env()
    overrides = {}
    if getattr(args, "model", None):
        overrides["model"] = args.model
    if getattr(args, "device", None):
        overrides["device"] = args.device
    if getattr(args, "compute", None):
        overrides["compute_type"] = args.compute
    return SttSettings(**{**settings.__dict__, **overrides}) if overrides else settings


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _run(args) -> int:
    if args.command == "pull":
        settings = _settings_from(args)
        path = pull_model(settings)
        print(f"model '{settings.model}' ready at {path}")
        return 0

    if args.command == "serve":
        settings = _settings_from(args)
        socket_path = stt_worker.resolve_serving_socket(
            explicit=args.socket, runtime_dir=args.runtime_dir
        )
        transcriber = Transcriber(settings)
        worker = stt_worker.SttWorker(transcriber, socket_path=socket_path)
        report = worker.serve()
        if report["clean"]:
            print(
                f"agent-tts-stt: worker stopped cleanly ({args.socket or 'default socket'})",
                file=sys.stderr,
            )
            return 0
        print(
            f"agent-tts-stt: worker stopped with {report['hung']} hung operation(s); "
            "a native inference call could not be cancelled — exit is not clean",
            file=sys.stderr,
        )
        return 1

    if args.command == "status":
        reply = stt_worker.stt_request(
            {"op": "status"},
            socket_path=_socket_arg(args),
            timeout=args.timeout,
        )
        _emit(reply)
        return 0

    if args.command == "shutdown":
        # The worker replies {"ok": true, "shutting_down": true}: the final
        # clean/hung truth belongs to the serve process (stderr + exit code).
        # The client polls until the socket disappears (bounded by --timeout).
        stt_worker.stt_request(
            {"op": "shutdown"},
            socket_path=_socket_arg(args),
            timeout=args.timeout,
        )
        socket_path = _socket_arg(args)
        deadline = time.monotonic() + max(0.0, float(args.timeout))
        while os.path.exists(socket_path):
            if time.monotonic() >= deadline:
                _emit({"ok": False, "stopped": False})
                return 1
            time.sleep(0.1)
        _emit({"ok": True, "stopped": True})
        return 0

    if args.command == "transcribe":
        audio_path = __import__("pathlib").Path(args.file)
        if not audio_path.is_file():
            raise SttError(f"audio file not found: {audio_path}")
        size = audio_path.stat().st_size
        if size > stt_worker.MAX_AUDIO_BYTES:
            raise stt_worker.InvalidRequestError(
                f"audio file of {size} bytes exceeds the "
                f"{stt_worker.MAX_AUDIO_BYTES}-byte cap"
            )
        suffix = args.suffix or (audio_path.suffix or ".webm")
        if suffix not in stt_worker.SUFFIX_WHITELIST:
            raise stt_worker.InvalidRequestError(
                f"suffix {suffix!r} not allowed "
                f"(one of {', '.join(sorted(stt_worker.SUFFIX_WHITELIST))})"
            )
        reply = stt_worker.stt_request(
            {
                "op": "transcribe",
                "audio_b64": __import__("base64").b64encode(audio_path.read_bytes()).decode(),
                "suffix": suffix,
            },
            socket_path=_socket_arg(args),
            timeout=args.timeout,
        )
        _emit({"ok": True, "text": reply.get("text", "")})
        return 0

    if args.command == "capture":
        # Local operation: no worker/socket — the recorder runs directly.
        overrides = {}
        if args.max_seconds is not None:
            overrides["max_seconds"] = args.max_seconds
        if args.silence_seconds is not None:
            overrides["silence_seconds"] = args.silence_seconds
        if args.device is not None:
            overrides["device"] = args.device
        config = CaptureConfig(**overrides)
        result = PowerShellCapture().run(config)
        out_path = __import__("pathlib").Path(args.out)
        try:
            out_path.write_bytes(result.wav_bytes)
        except OSError as exc:
            raise SttError(f"cannot write capture output to {out_path}: {exc}") from exc
        _emit(
            {
                "ok": True,
                "path": str(out_path),
                "duration_sec": round(result.duration, 3),
                "ended_by": result.ended_by,
                "trimmed": bool(result.stats.get("trimmed")),
                "bytes": len(result.wav_bytes),
            }
        )
        return 0

    raise SttError(f"unknown command: {args.command}")  # pragma: no cover


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-tts-stt",
        description=(
            "Speech-to-text model and resident worker for agent-tts "
            "(faster-whisper, offline by default)."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    pull = sub.add_parser(
        "pull", help="download the STT model explicitly (the only download path)"
    )
    pull.add_argument("--model", help="model alias or repo id (default: env/small)")

    serve = sub.add_parser("serve", help="run the resident STT worker")
    serve.add_argument("--model", help="model alias or repo id")
    serve.add_argument("--device", choices=("auto", "cpu", "cuda"), help="compute device")
    serve.add_argument("--compute", help="compute type (e.g. int8)")
    serve.add_argument("--socket", help="explicit socket path (parent dir trusted)")
    serve.add_argument(
        "--runtime-dir", help="application-owned runtime dir (validated 0700)"
    )

    status = sub.add_parser("status", help="ask the running worker for readiness")
    status.add_argument("--socket", help="socket path (default: env or runtime dir)")
    status.add_argument("--timeout", type=float, default=STATUS_TIMEOUT_SEC)

    shutdown = sub.add_parser(
        "shutdown",
        help="ask the running worker to stop "
        "(exit 1 when it does not stop within --timeout)",
    )
    shutdown.add_argument("--socket", help="socket path (default: env or runtime dir)")
    shutdown.add_argument("--timeout", type=float, default=STATUS_TIMEOUT_SEC)

    transcribe = sub.add_parser(
        "transcribe", help="transcribe one audio file through the running worker"
    )
    transcribe.add_argument("--file", required=True, help="path to the audio clip")
    transcribe.add_argument("--socket", help="socket path (default: env or runtime dir)")
    transcribe.add_argument("--suffix", help="container suffix (default: file extension)")
    transcribe.add_argument(
        "--timeout", type=float, default=stt_worker.DEFAULT_READ_TIMEOUT_SEC
    )

    capture = sub.add_parser(
        "capture",
        help="record the Windows-host microphone locally (no worker) "
        "into a trailing-silence-trimmed WAV",
    )
    capture.add_argument("--out", required=True, help="output WAV path")
    capture.add_argument(
        "--max-seconds", type=float, help="recording window in seconds (default 30)"
    )
    capture.add_argument(
        "--silence-seconds",
        type=float,
        help="trailing silence that ends an utterance (default 1.2)",
    )
    capture.add_argument(
        "--device", help="capture device (v1: only 'default' is supported)"
    )

    args = parser.parse_args(argv)
    try:
        return _run(args)
    except SttError as exc:
        _emit({"ok": False, "error": {"kind": exc.kind, "message": str(exc)}})
        print(f"agent-tts-stt: {exc.kind}: {exc}", file=sys.stderr)
        return exc.exit_code
    except Exception as exc:  # noqa: BLE001 — no raw traceback on stdout
        _emit(
            {
                "ok": False,
                "error": {"kind": "internal_error", "message": type(exc).__name__},
            }
        )
        print(f"agent-tts-stt: internal error ({type(exc).__name__})", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover - module entry
    raise SystemExit(main())
