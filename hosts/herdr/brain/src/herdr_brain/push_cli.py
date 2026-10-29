"""CLI for Web Push: ``push import``, ``push list``, ``push test`` (S5).

Usage:
    herdr-brain push import <dotenv-path>
    herdr-brain push list
    herdr-brain push test

The operator's half of the push channel. Every storage, crypto and
delivery decision lives in :mod:`herdr_brain.webpush`; this module is
the terminal, the state dir, and the words an operator reads.

The brain creates its own VAPID pair on first start, so no verb is
needed to enable push. ``import`` is optional: it replaces that pair with
one the operator already holds (restart the service afterwards). Browsers
still enable notifications explicitly; Activar registers the subscription
with this server and replaces one bound to a different application-server
key. The dotenv reader matches ``*_VAPID_*`` keys by
suffix (the other product keeps its own prefix — brain never writes it
down) and prints ONLY the public key, because the private half must never
land in a terminal transcript. ``test`` sends one round-trip
notification per subscribed device — run it from the phone's Ajustes
card or from here; both drive the same service.

Exit contract (mirrors the msg CLI): 0 the verb did its job, 1 failure
(unreadable file, bad keys, corrupt store, push off), 2 usage error.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import IO, Callable, Optional, Sequence

from .config import Settings, load_settings
from .webpush import StoreError, WebPushService
from .webpush.vapid import VapidError, import_keys_from_env_file, save_keys

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

USAGE = """usage: herdr-brain push <verb> [args]

  import <dotenv-path>   import a VAPID pair from an operator export; prints the public key
  list                   list subscribed push endpoints (oldest first)
  test                   send one test notification per subscribed device

Keys live in <state-dir>/vapid.json (0600) and are created on first
start; import replaces them (restart the service afterwards)."""

_VERBS = ("import", "list", "test")


def _print(text: str, stream: IO) -> None:
    print(text, file=stream)


def _state_dir(state_dir: Optional[Path], env) -> Path:
    if state_dir is not None:
        return Path(state_dir)
    settings: Settings = load_settings(dict(os.environ if env is None else env))
    return Path(settings.state_dir)


def main(
    argv: Optional[Sequence[str]] = None,
    env: Optional[dict] = None,
    state_dir: Optional[Path] = None,
    transport: Optional[Callable] = None,
    stdout: Optional[IO] = None,
    stderr: Optional[IO] = None,
) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr

    verb = args[0] if args else ""
    if verb not in _VERBS:
        _print(USAGE, err)
        return EXIT_USAGE

    environ = dict(os.environ if env is None else env)
    try:
        root = _state_dir(state_dir, environ)
    except Exception as exc:  # noqa: BLE001 — config errors are exit 1 words
        _print(f"ERROR: cannot load settings: {exc}", err)
        return EXIT_FAIL

    if verb == "import":
        return _cmd_import(args[1:], root, out, err)
    try:
        service = WebPushService(root, transport=transport)
    except VapidError as exc:
        _print(f"ERROR: the VAPID configuration is broken: {exc}", err)
        return EXIT_FAIL
    except StoreError as exc:
        _print(f"ERROR: the subscription store is unusable: {exc}", err)
        return EXIT_FAIL
    if verb == "list":
        return _cmd_list(service, out, err)
    return _cmd_test(service, out, err)


def _cmd_import(args: Sequence[str], root: Path, out: IO, err: IO) -> int:
    if len(args) != 1:
        _print("usage: herdr-brain push import <dotenv-path>", err)
        return EXIT_USAGE
    try:
        keys = import_keys_from_env_file(Path(args[0]))
        save_keys(root, keys)
    except VapidError as exc:
        _print(f"ERROR: {exc}", err)
        return EXIT_FAIL
    except OSError as exc:
        _print(f"ERROR: could not write {root / 'vapid.json'}: {exc}", err)
        return EXIT_FAIL
    # ONLY the public key: the private half never crosses the terminal.
    _print(keys.public_b64url, out)
    return EXIT_OK


def _cmd_list(service: WebPushService, out: IO, err: IO) -> int:
    try:
        endpoints = service.subscriptions()
    except StoreError as exc:
        # Loud, never a reset: the file holds per-device delivery keys.
        _print(f"ERROR: the subscription store is unusable: {exc}", err)
        return EXIT_FAIL
    if not endpoints:
        _print("no hay suscripciones push (empareja un teléfono desde Ajustes)", out)
        return EXIT_OK
    for endpoint in endpoints:
        _print(endpoint, out)
    return EXIT_OK


def _cmd_test(service: WebPushService, out: IO, err: IO) -> int:
    if not service.enabled:
        _print(
            "ERROR: web push está apagado — no se pudieron crear las claves VAPID "
            "(¿se puede escribir en el directorio de estado?).",
            err,
        )
        return EXIT_FAIL
    try:
        result = service.test_notification()
    except StoreError as exc:
        _print(f"ERROR: the subscription store is unusable: {exc}", err)
        return EXIT_FAIL
    if result["sent"] == 0:
        _print(
            "no se envió nada (¿hay suscripciones? comprueba con `herdr-brain push list`)",
            err,
        )
        return EXIT_FAIL
    _print(f"notificación de prueba enviada a {result['sent']} dispositivo(s)", out)
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - manual entrypoint
    raise SystemExit(main())
