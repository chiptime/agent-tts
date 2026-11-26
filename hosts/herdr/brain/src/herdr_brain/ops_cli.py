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

import json
import os
import sys
import time
from pathlib import Path
from typing import IO, Callable, Optional, Sequence
from urllib.parse import quote, urlencode, urlsplit

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

DEFAULT_TAIL_LINES = 20

# POST-05: the headless SDK client. One device credential (a paired
# bearer like the PWA's), held in a 0600 file under the state dir —
# never argv, never logs, never stdout.
SDK_TOKEN_FILE = "sdk-device-token"
SDK_DEFAULT_TIMEOUT_S = 30

SDK_USAGE = """usage: herdr-brain sdk <verb> [args]

  pair CODE [--label L] [--url URL]
                      spend a pairing code against POST /pair and store
                      the device bearer 0600 in <state_dir>/sdk-device-token
                      (the token is never printed)
  herd                the whole herd as JSON (GET /sdk/v1/herd)
  view [PANE_ID] [--modal]
                      one pane's view (GET /sdk/v1/view)
  ask TEXT [--pane ID] [--session ID] [--reset]
                      one conversational turn (POST /sdk/v1/ask)
  approval-current    the live gate or {"approval": null}
  approval-action GATE_ID {approve|reject}
                      resolve a gate (POST /sdk/v1/approval/{id}/action)

The client talks to the pinned /sdk/v1 surface — same handlers, auth
and audit as the PWA's HTTP routes. Base URL: --url, HERDR_BRAIN_SDK_URL,
or http://127.0.0.1:<HERDR_BRAIN_PORT|8741>. Bearer: HERDR_BRAIN_SDK_TOKEN
or the file `sdk pair` wrote. Exit: 0 ok, 1 failure, 2 usage."""

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
  sdk <verb>          headless client for the pinned /sdk/v1 surface
                      (pair, herd, view, ask, approval-current,
                      approval-action) — see `herdr-brain sdk` alone

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


def _tailscale_serve_url(environ: dict, status_text: Optional[str] = None) -> str:
    """Discovers the public/tailnet URL pointing to this brain's port in tailscale serve."""
    port = _brain_port(environ)
    target = f"http://127.0.0.1:{port}"
    raw = status_text
    if raw is None:
        try:
            import subprocess

            proc = subprocess.run(
                ["tailscale", "serve", "status", "--json"],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
            if proc.returncode == 0:
                raw = proc.stdout
        except (OSError, subprocess.SubprocessError):
            pass
    if not raw or not raw.strip():
        return ""
    try:
        import json

        data = json.loads(raw)
        web = data.get("Web", {})
        candidates = []
        for host_port, config in web.items():
            handlers = config.get("Handlers", {})
            for path, handler in handlers.items():
                if handler.get("Proxy") == target:
                    host, _, p = host_port.partition(":")
                    p_num = int(p) if p.isdigit() else 443
                    url = f"https://{host}" if p_num == 443 else f"https://{host}:{p_num}"
                    candidates.append((0 if p_num == 443 else 1, url))
        if candidates:
            candidates.sort(key=lambda c: c[0])
            return candidates[0][1]
    except (ValueError, TypeError, AttributeError):
        pass
    return ""


def _remote_url(environ: dict, serve_status: Optional[str] = None) -> str:
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
    if base is not None:
        path = base / "herdr-brain" / "config.env"
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.split("#", 1)[0].strip()
                if stripped.startswith("HERDR_BRAIN_REMOTE_URL="):
                    raw = stripped[len("HERDR_BRAIN_REMOTE_URL="):].strip()
                    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
                        raw = raw[1:-1]
                    if raw:
                        return raw
        except OSError:
            pass
    return _tailscale_serve_url(environ, serve_status)


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
        _print("  tailscale serve --https=443 off")
        _print("  (this unsets only the root HTTPS 443 mapping for the brain)")
        return EXIT_OK
    https_port = getattr(args, "https", None) or 8443
    _print("[herdr-brain] The brain never runs tailscale — run this yourself:")
    _print(f"  tailscale serve --https={https_port} off")
    _print("  (adjust the port if you published a different one)")
    return EXIT_OK


def cmd_qr(args, environ: dict) -> int:
    status_value = getattr(args, "serve_status", None)
    serve_status = None
    if status_value:
        serve_status = _read_serve_status(status_value)
    url = _remote_url(environ, serve_status)
    if not url:
        _print(
            "no remote URL is configured — set HERDR_BRAIN_REMOTE_URL, "
            "persist it in config.env, or publish with `herdr-brain serve`",
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


# ── the sdk client (POST-05) ────────────────────────────────────────────────


def _sdk_url(args, environ: dict) -> str:
    """Base URL precedence: --url, HERDR_BRAIN_SDK_URL, then loopback on
    the brain's own port (the same default `herdr-brain status` uses)."""
    explicit = getattr(args, "url", None)
    if explicit:
        return explicit.rstrip("/")
    direct = str(environ.get("HERDR_BRAIN_SDK_URL", "") or "").strip()
    if direct:
        return direct.rstrip("/")
    return f"http://127.0.0.1:{_brain_port(environ)}"


def _sdk_token_path(environ: dict) -> Path:
    return _state_dir(environ) / SDK_TOKEN_FILE


def _read_sdk_token(environ: dict) -> Optional[str]:
    """The device bearer: the env override first, then the 0600 file
    `sdk pair` wrote. Neither is ever echoed anywhere."""
    env_token = str(environ.get("HERDR_BRAIN_SDK_TOKEN", "") or "").strip()
    if env_token:
        return env_token
    try:
        token = _sdk_token_path(environ).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


def _store_sdk_token(environ: dict, token: str) -> Path:
    """Persist the pair credential ATOMICALLY and owner-only, following
    pairing.py's discipline: a unique sibling temp (O_EXCL, 0600) plus
    os.replace. A pre-existing file never keeps looser bits for even
    one instant (no O_TRUNC on the target, no window before a chmod),
    readers never see a half-written credential, and the replace never
    follows a symlink left in the token's place."""
    import secrets

    from .pairing import ensure_private_dir

    path = _sdk_token_path(environ)
    ensure_private_dir(path.parent)
    tmp = path.with_name(
        f"{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    )
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token)
        os.replace(str(tmp), str(path))
    except BaseException:
        try:
            os.unlink(str(tmp))
        except OSError:
            pass
        raise
    # Belt and suspenders: the temp was born 0600 and replace carries
    # the inode, but an exotic filesystem must not silently widen it.
    os.chmod(path, 0o600)
    return path


def _sdk_base_url_error(base_url: str) -> Optional[str]:
    """Fail-closed base URL validation: the client never turns a bad
    base into a request. ``file://`` (SSRF-shaped), host-less and
    control-character URLs are refused before any credential or
    connection exists — a clean stderr line, never a traceback."""
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in base_url):
        return "base URL contains control characters"
    try:
        parsed = urlsplit(base_url)
    except ValueError:
        return "malformed base URL"
    if parsed.scheme not in ("http", "https"):
        return (
            f"unsupported scheme {parsed.scheme or '(none)'} in base URL "
            f"{base_url!r} — use http or https"
        )
    if not parsed.netloc:
        return f"base URL {base_url!r} needs a host"
    return None


def _sdk_origin(base_url: str) -> str:
    """The strict ``scheme://netloc`` Origin the doc-01 same-origin rule
    compares against — a base URL carrying a subpath must not leak it
    into the Origin header."""
    try:
        parsed = urlsplit(base_url)
    except ValueError:
        return base_url
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else base_url


def _sdk_request_object(
    method: str,
    base_url: str,
    path: str,
    *,
    token: Optional[str] = None,
    body: Optional[dict] = None,
):
    """The pure request builder every sdk verb goes through. Every
    state-changing method mirrors the base URL's scheme://netloc as its
    Origin — exactly the header the PWA itself sends, so the doc-01
    same-origin write rule passes without any matrix relaxation
    (loopback needs no Origin; mirroring is honest there too)."""
    import urllib.request

    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if method not in ("GET", "HEAD"):
        headers["Origin"] = _sdk_origin(base_url)
    return urllib.request.Request(
        f"{base_url}{path}", data=data, headers=headers, method=method
    )


def _sdk_http_request(
    method: str,
    base_url: str,
    path: str,
    *,
    token: Optional[str] = None,
    body: Optional[dict] = None,
    timeout: float = SDK_DEFAULT_TIMEOUT_S,
) -> tuple:
    """The I/O seam of the sdk client (tests stub this): one request in,
    a ``(status, body bytes)`` tuple out. Transport errors (OSError and
    urllib's URLError) propagate — the verb handlers turn them into the
    exit-1 path."""
    import urllib.error
    import urllib.request

    request = _sdk_request_object(
        method, base_url, path, token=token, body=body
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        finally:
            exc.close()
        return exc.code, body


def _sdk_query(params: dict) -> str:
    live = {k: v for k, v in params.items() if v is not None}
    return f"?{urlencode(live)}" if live else ""


def _sdk_call(args, environ: dict, method: str, path: str,
              body: Optional[dict] = None) -> int:
    """Shared client path: resolve and VALIDATE the base URL, resolve
    the credential, one request, verbatim JSON to stdout on success,
    `error: HTTP <status>: <detail>` on stderr with exit 1 otherwise."""
    base_url = _sdk_url(args, environ)
    url_error = _sdk_base_url_error(base_url)
    if url_error is not None:
        _print(f"error: {url_error}", sys.stderr)
        return EXIT_FAIL
    token = _read_sdk_token(environ)
    if token is None:
        _print(
            "error: no sdk device credential — run `herdr-brain sdk pair "
            "<code>` first (or set HERDR_BRAIN_SDK_TOKEN)",
            sys.stderr,
        )
        return EXIT_FAIL
    try:
        status, payload = _sdk_http_request(
            method, base_url, path, token=token, body=body
        )
    except OSError as exc:
        _print(f"error: cannot reach the brain at {base_url}: {exc}",
               sys.stderr)
        return EXIT_FAIL
    except ValueError as exc:
        _print(f"error: sdk request failed: {exc}", sys.stderr)
        return EXIT_FAIL
    if status >= 400:
        _print(
            f"error: HTTP {status}: "
            f"{payload.decode('utf-8', 'replace').strip()}",
            sys.stderr,
        )
        return EXIT_FAIL
    text = payload.decode("utf-8", "replace")
    _print(text)
    return EXIT_OK


def _sdk_verb_pair(args, environ: dict) -> int:
    base_url = _sdk_url(args, environ)
    url_error = _sdk_base_url_error(base_url)
    if url_error is not None:
        _print(f"error: {url_error}", sys.stderr)
        return EXIT_FAIL
    body = {"code": args.code, "label": args.label or "sdk-cli"}
    try:
        status, payload = _sdk_http_request(
            "POST", base_url, "/pair", body=body
        )
    except OSError as exc:
        _print(f"error: cannot reach the brain at {base_url}: {exc}",
               sys.stderr)
        return EXIT_FAIL
    except ValueError as exc:
        _print(f"error: sdk request failed: {exc}", sys.stderr)
        return EXIT_FAIL
    if status >= 400:
        _print(
            f"error: HTTP {status}: "
            f"{payload.decode('utf-8', 'replace').strip()}",
            sys.stderr,
        )
        return EXIT_FAIL
    try:
        outcome = json.loads(payload.decode("utf-8"))
    except ValueError:
        outcome = None
    token = (outcome or {}).get("token")
    if not isinstance(token, str) or not token.strip():
        _print("error: /pair answer carried no usable token", sys.stderr)
        return EXIT_FAIL
    label = (outcome or {}).get("label") or body["label"]
    path = _store_sdk_token(environ, token)
    _print(f"sdk device paired: {label}")
    _print(f"  token stored in {path} (0600)")
    return EXIT_OK


def _sdk_verb_herd(args, environ: dict) -> int:
    return _sdk_call(args, environ, "GET", "/sdk/v1/herd")


def _sdk_verb_view(args, environ: dict) -> int:
    path = "/sdk/v1/view" + _sdk_query({
        "pane_id": getattr(args, "pane_id", None),
        "modal": 1 if getattr(args, "modal", False) else None,
    })
    return _sdk_call(args, environ, "GET", path)


def _sdk_verb_ask(args, environ: dict) -> int:
    body = {
        "text": args.text,
        "pane_id": getattr(args, "pane", None),
        "session_id": getattr(args, "session", None),
        "reset": bool(getattr(args, "reset", False)),
    }
    return _sdk_call(args, environ, "POST", "/sdk/v1/ask", body=body)


def _sdk_verb_approval_current(args, environ: dict) -> int:
    return _sdk_call(args, environ, "GET", "/sdk/v1/approval/current")


def _sdk_verb_approval_action(args, environ: dict) -> int:
    if args.decision not in ("approve", "reject"):
        _print(
            "usage: herdr-brain sdk approval-action GATE_ID {approve|reject}",
            sys.stderr,
        )
        return EXIT_USAGE
    # The gate id interpolates into a single path segment: one segment,
    # no separators, no traversal — a malformed id is a usage error,
    # and a valid-but-odd one is percent-quoted (route params decode it
    # back server-side).
    gate_id = args.gate_id.strip()
    if (
        not gate_id
        or "/" in gate_id
        or ".." in gate_id
        or any(ch.isspace() for ch in gate_id)
    ):
        _print(
            "error: gate_id must be a single path segment without slashes "
            "or whitespace",
            sys.stderr,
        )
        return EXIT_USAGE
    path = f"/sdk/v1/approval/{quote(gate_id, safe='')}/action"
    return _sdk_call(
        args, environ, "POST", path, body={"decision": args.decision}
    )


_SDK_VERBS = {
    "pair": _sdk_verb_pair,
    "herd": _sdk_verb_herd,
    "view": _sdk_verb_view,
    "ask": _sdk_verb_ask,
    "approval-current": _sdk_verb_approval_current,
    "approval-action": _sdk_verb_approval_action,
}


def cmd_sdk(args, environ: dict) -> int:
    verb = getattr(args, "sdk_verb", None)
    handler = _SDK_VERBS.get(verb)
    if handler is None:
        _print(SDK_USAGE, sys.stderr)
        return EXIT_USAGE
    return handler(args, environ)


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
    unserve.add_argument("--https", type=int, default=None)
    qr = sub.add_parser("qr")
    qr.add_argument("--serve-status", default=None)
    logs = sub.add_parser("logs")
    logs.add_argument("-n", type=int, default=None)
    logs.add_argument("-f", action="store_true")
    config = sub.add_parser("config")
    config.add_argument("sub", nargs="?", default="")
    doc = sub.add_parser("doctor")
    doc.add_argument("--serve-status", default=None)
    sdk_parser = sub.add_parser("sdk")
    sdk_sub = sdk_parser.add_subparsers(dest="sdk_verb")
    sdk_pair = sdk_sub.add_parser("pair")
    sdk_pair.add_argument("code")
    sdk_pair.add_argument("--label", default=None)
    sdk_pair.add_argument("--url", default=None)
    sdk_sub.add_parser("herd").add_argument("--url", default=None)
    sdk_view = sdk_sub.add_parser("view")
    sdk_view.add_argument("pane_id", nargs="?", default=None)
    sdk_view.add_argument("--modal", action="store_true")
    sdk_view.add_argument("--url", default=None)
    sdk_ask = sdk_sub.add_parser("ask")
    sdk_ask.add_argument("text")
    sdk_ask.add_argument("--pane", default=None)
    sdk_ask.add_argument("--session", default=None)
    sdk_ask.add_argument("--reset", action="store_true")
    sdk_ask.add_argument("--url", default=None)
    sdk_sub.add_parser("approval-current").add_argument("--url", default=None)
    sdk_action = sdk_sub.add_parser("approval-action")
    sdk_action.add_argument("gate_id")
    sdk_action.add_argument("decision")
    sdk_action.add_argument("--url", default=None)
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
    if verb == "sdk":
        return cmd_sdk(args, environ)
    _print(USAGE, sys.stderr)
    return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover - module entry
    sys.exit(main())
