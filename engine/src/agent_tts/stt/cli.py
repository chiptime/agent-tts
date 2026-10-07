"""``agent-tts-stt`` console entry point: explicit STT operations.

Operations: ``pull`` (the only model download), ``serve`` (resident
worker), ``status`` and ``transcribe`` (worker clients — no autostart,
the existing ``agent-tts`` positional-speech CLI is never intercepted).

Request operations (``status``/``transcribe``) print ONE machine-readable
JSON line on stdout; failures also print a JSON error object on stdout
plus a human hint on stderr, and exit with the typed status code:

    0 ok · 1 error · 2 usage · 3 busy · 4 model_unavailable ·
    5 worker_unavailable · 6 missing_extra · 7 transport_unsupported
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from agent_tts.stt import worker as stt_worker
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

    transcribe = sub.add_parser(
        "transcribe", help="transcribe one audio file through the running worker"
    )
    transcribe.add_argument("--file", required=True, help="path to the audio clip")
    transcribe.add_argument("--socket", help="socket path (default: env or runtime dir)")
    transcribe.add_argument("--suffix", help="container suffix (default: file extension)")
    transcribe.add_argument(
        "--timeout", type=float, default=stt_worker.DEFAULT_READ_TIMEOUT_SEC
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
