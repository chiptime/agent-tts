"""Operations CLI (S8, doc 11 fase 2 + S3 config check):

    herdr-brain serve [--root|--https=PORT] [--serve-status FILE|-]
    herdr-brain unserve [--root]
    herdr-brain qr
    herdr-brain logs [-n N] [-f]
    herdr-brain config check
    herdr-brain doctor [--serve-status FILE|-]

The terminal half of the cutover. One hard rule shapes ``serve`` and
``unserve``: the brain NEVER runs tailscale (L4) — the verbs PRINT the
exact commands for the operator to run, with an honest warning when
the recorded ``tailscale serve status --json`` shows another service
on the HTTPS root. ``qr`` prints the PWA URL; a scannable ASCII QR
needs a QR encoder, which this stdlib-only unit does not carry — the
verb says so instead of printing an unscannable block. Exit contract
mirrors the other CLIs: 0 did the job, 1 failure, 2 usage.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import IO, Callable, Optional, Sequence

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

DEFAULT_TAIL_LINES = 20

USAGE = """usage: herdr-brain <verb> [args]

  serve [--root|--https=PORT] [--serve-status FILE|-]
                     PRINT the tailscale serve command for the brain
                     (8443 by default; --root prints the post-cutover
                     root 443 form) with a warning when the recorded
                     status shows another service on 443
  unserve [--root]  PRINT the tailscale serve unset command
  qr                print the PWA's public URL (remote URL)
  logs [-n N] [-f]  tail the daemon log (default 20 lines; -f follows)
  config check      per-file report of the operator TOML surfaces
  doctor [--serve-status FILE|-]
                     brain-side health rows (ports, root 443, VAPID,
                     push store, crew roster, operator config)

The brain never executes tailscale itself: serve/unserve print the
exact commands for the operator to run."""


def _print(text: str, stream: Optional[IO] = None) -> None:
    print(text, file=stream if stream is not None else sys.stdout)


def _env_dict(env: Optional[dict]) -> dict:
    return dict(os.environ if env is None else env)


def _brain_port(environ: dict) -> str:
    return str(environ.get("HERDR_BRAIN_PORT", "") or "8741").strip()


def _state_dir(environ: dict) -> Path:
    from .config import load_settings

    override = str(environ.get("HERDR_BRAIN_STATE_DIR", "") or "").strip()
    if override:
        return Path(override)
    return Path(load_settings(environ).state_dir)


def _remote_url(environ: dict) -> str:
    direct = str(environ.get("HERDR_BRAIN_REMOTE_URL", "") or "").strip()
    if direct:
        return direct
    # The persisted config.env carries the operator-declared remote URL
    # (the launcher's remote_url resolver, mirrored here read-only).
    config_home = environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(config_home).expanduser() if config_home else (
        Path(environ.get("HOME", "")).expanduser() / ".config"
        if str(environ.get("HOME", "")).strip() else None
    )
    if base is None:
        return ""
    path = base / "herdr-brain" / "config.env"
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.split("#", 1)[0].strip()
            if stripped.startswith("HERDR_BRAIN_REMOTE_URL="):
                raw = stripped[len("HERDR_BRAIN_REMOTE_URL="):].strip()
                if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
                    raw = raw[1:-1]
                return raw
    except OSError:
        return ""
    return ""


def _read_serve_status(value: str) -> Optional[str]:
    """The recorded ``tailscale serve status --json``: a file path, or
    ``-`` for stdin. The operator ran tailscale — this process did
    not."""
    if value == "-":
        return sys.stdin.read()
    try:
        return Path(value).read_text(encoding="utf-8")
    except OSError as exc:
        _print(f"error: cannot read the serve status file: {exc}", sys.stderr)
        return None


# ── verbs ───────────────────────────────────────────────────────────────────


def cmd_serve(args, environ: dict) -> int:
    port = _brain_port(environ)
    root = getattr(args, "root", False)
    https_port = getattr(args, "https", None)
    if root and https_port:
        _print("error: --root and --https are mutually exclusive", sys.stderr)
        return EXIT_USAGE
    command = (
        f"tailscale serve --bg http://127.0.0.1:{port}"
        if root
        else f"tailscale serve --bg --https={https_port or 8443} "
             f"http://127.0.0.1:{port}"
    )
    _print("[herdr-brain] The brain never runs tailscale — run this yourself:")
    _print(f"  {command}")
    status_value = getattr(args, "serve_status", None)
    if status_value:
        text = _read_serve_status(status_value)
        if text is None:
            return EXIT_FAIL
        from .doctor import check_root_443

        row = check_root_443(text, int(port) if port.isdigit() else 8741)
        _print(f"  root 443: {row.detail}")
        if row.hint:
            _print(f"  fix: {row.hint}")
    else:
        _print(
            "  verify first with: tailscale serve status --json "
            "(pass it via --serve-status FILE|- for the root-443 hint)"
        )
    _print(
        "  then point the phone at the URL it prints (and `herdr-brain qr` "
        "shows it)."
    )
    return EXIT_OK


def cmd_unserve(args, environ: dict) -> int:
    root = getattr(args, "root", False)
    if root:
        _print("[herdr-brain] The brain never runs tailscale — run this yourself:")
        _print("  tailscale serve off")
        _print(
            "  (this clears EVERY serve mapping on the node — check "
            "`tailscale serve status` first)"
        )
        return EXIT_OK
    _print("[herdr-brain] The brain never runs tailscale — run this yourself:")
    _print("  tailscale serve --https=8443 off")
    _print("  (adjust the port if you published a different one)")
    return EXIT_OK


def cmd_qr(args, environ: dict) -> int:
    url = _remote_url(environ)
    if not url:
        _print(
            "no remote URL is configured — set HERDR_BRAIN_REMOTE_URL or "
            "persist it in config.env (see `herdr-brain url`)",
            sys.stderr,
        )
        return EXIT_FAIL
    _print(f"PWA URL: {url}")
    _print(
        "(no ASCII QR: printing a scannable QR needs a QR encoder and this "
        "unit is stdlib-only — copy the URL, or run any local `qrencode -t "
        "ANSI` yourself)"
    )
    return EXIT_OK


def tail_lines(path: Path, n: int) -> list[str]:
    """The last ``n`` lines of a file, without loading it whole."""
    if n <= 0:
        return []
    lines: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            lines.append(line.rstrip("\n"))
            if len(lines) > n:
                lines.pop(0)
    return lines


def follow_tail(path: Path, n: int, sleep: Callable[[float], None]) -> None:
    """Prints appended lines as they land, until interrupted."""
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        # Skip to the tail start, then stream.
        handle.seek(0, os.SEEK_END)
        try:
            while True:
                line = handle.readline()
                if line:
                    _print(line.rstrip("\n"))
                    continue
                sleep(0.5)
        except KeyboardInterrupt:
            return


def cmd_logs(args, environ: dict, _sleep: Optional[Callable] = None) -> int:
    log_path = _state_dir(environ) / "server.log"
    if not log_path.is_file():
        _print(
            f"error: no daemon log at {log_path} — is the brain running "
            "(`herdr-brain status`)?",
            sys.stderr,
        )
        return EXIT_FAIL
    count = getattr(args, "n", None) or DEFAULT_TAIL_LINES
    for line in tail_lines(log_path, count):
        _print(line)
    if getattr(args, "f", False):
        follow_tail(log_path, count, _sleep or time.sleep)
    return EXIT_OK


def cmd_config(args, environ: dict) -> int:
    if (args.sub or "") != "check":
        _print("usage: herdr-brain config check", sys.stderr)
        return EXIT_USAGE
    from .operator_config import OperatorConfig

    catalog = OperatorConfig()
    any_issue = False
    for file_report in catalog.check():
        if not file_report.present:
            _print(f"{file_report.file}: absent (the {file_report.surface} "
                   "surface is empty)")
            continue
        status = "ok" if not file_report.issues else "ISSUES"
        _print(
            f"{file_report.file}: present, {file_report.row_count} row(s), {status}"
        )
        for issue in file_report.issues:
            any_issue = True
            _print(f"  {issue.describe()}")
    return EXIT_FAIL if any_issue else EXIT_OK


def cmd_doctor(args, environ: dict) -> int:
    from . import doctor

    state = _state_dir(environ)
    port = _brain_port(environ)
    crew_port = str(environ.get("HERDR_BRAIN_CREW_PORT", "") or "8747").strip()
    status_value = getattr(args, "serve_status", None)
    serve_status = None
    if status_value:
        serve_status = _read_serve_status(status_value)
        if serve_status is None:
            return EXIT_FAIL
    rows = doctor.run_all(
        state,
        int(port) if port.isdigit() else 8741,
        int(crew_port) if crew_port.isdigit() else 8747,
        serve_status_text=serve_status,
    )
    return doctor.report(rows)


# ── the parser ──────────────────────────────────────────────────────────────


def build_parser():
    import argparse

    parser = argparse.ArgumentParser(prog="herdr-brain", add_help=False)
    sub = parser.add_subparsers(dest="verb")
    serve = sub.add_parser("serve")
    serve.add_argument("--root", action="store_true")
    serve.add_argument("--https", type=int, default=None)
    serve.add_argument("--serve-status", default=None)
    unserve = sub.add_parser("unserve")
    unserve.add_argument("--root", action="store_true")
    sub.add_parser("qr")
    logs = sub.add_parser("logs")
    logs.add_argument("-n", type=int, default=None)
    logs.add_argument("-f", action="store_true")
    config = sub.add_parser("config")
    config.add_argument("sub", nargs="?", default="")
    doc = sub.add_parser("doctor")
    doc.add_argument("--serve-status", default=None)
    return parser


def main(
    argv: Optional[Sequence[str]] = None,
    env: Optional[dict] = None,
    _sleep: Optional[Callable[[float], None]] = None,
) -> int:
    args = build_parser().parse_args(
        list(sys.argv[1:] if argv is None else argv)
    )
    environ = _env_dict(env)
    verb = args.verb
    if verb == "serve":
        return cmd_serve(args, environ)
    if verb == "unserve":
        return cmd_unserve(args, environ)
    if verb == "qr":
        return cmd_qr(args, environ)
    if verb == "logs":
        return cmd_logs(args, environ, _sleep=_sleep)
    if verb == "config":
        return cmd_config(args, environ)
    if verb == "doctor":
        return cmd_doctor(args, environ)
    _print(USAGE, sys.stderr)
    return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover - module entry
    sys.exit(main())
