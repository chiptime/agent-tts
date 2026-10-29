"""The operator's crew verbs (task S7):

    crew init [--name NAME] [--secret -|@FILE]
    crew join-token
    crew join --name NAME --lead-name NAME --lead-url URL --lead-cert @FILE
              --url URL --cert @FILE --secret -|@FILE --token TOKEN
    crew list
    crew remove <MEMBER>
    crew promote [<MEMBER>]
    crew rotate-secret [--secret -|@FILE]

The pure decisions live in ``roster``/``enrol``; this module is the
terminal, the two files under the state dir, and the words an
operator reads — the ``pairing_cli`` stance.

One hard rule with its own error: a crew SECRET typed as a command
argument is refused. argv is world-readable through /proc for the
lifetime of the process; the secret arrives on stdin (``-``) or from
a file (``@path``), never on the line. ``init`` without ``--secret``
MINTS one and prints it exactly once — the only moment it exists in
clear text anywhere.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from .enrol import JoinTokens
from .roster import (
    ROTATE_GRACE_S,
    CrewRoster,
    CrewRosterCorrupt,
    CrewStateError,
    default_self_name,
    mint_crew_secret,
    normalize_name,
)
from .tls import CrewTlsError, ensure_host_certificate

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

#: The printed marker for the one-time secret reveal. Deterministic so
#: tooling (and tests) can find exactly that line.
SECRET_MARK = "crew-secret:"


class SecretArgumentError(CrewStateError):
    """A secret was passed as an argument. The message names the two
    allowed shapes — it must teach, not just refuse."""


def _read_source(value: str, *, what: str, argv_refused: str) -> str:
    """Read a value from stdin (``-``) or a file (``@path``) — never
    from the argument line itself."""
    if value == "-":
        line = sys.stdin.readline()
        return line.strip()
    if value.startswith("@"):
        path = Path(value[1:])
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SecretArgumentError(f"cannot read {what} file {path}: {exc}") from exc
    raise SecretArgumentError(argv_refused)


def read_secret_source(value: str) -> str:
    """The crew secret: stdin or file, NEVER an argument (argv is
    world-readable through /proc for the process's lifetime)."""
    return _read_source(
        value,
        what="secret",
        argv_refused=(
            "refusing a secret passed as an argument: use '-' to type it "
            "on stdin, or '@file' to read it from a file"
        ),
    )


def read_pem_source(value: str) -> str:
    """A certificate PEM: stdin or file (public material, same shape
    for symmetry — PEM blobs do not belong on a command line)."""
    return _read_source(
        value,
        what="certificate",
        argv_refused=(
            "refusing a certificate PEM passed as an argument: use '-' "
            "or '@file'"
        ),
    )


def _default_state_dir() -> Path:
    from ..config import load_settings

    return Path(load_settings().state_dir)


def _load_roster(state_dir: Path) -> CrewRoster:
    """Fail closed and SAY so: a corrupt roster is an error the
    operator must see, never a silent solo reset."""
    try:
        return CrewRoster.load(state_dir)
    except CrewRosterCorrupt as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_FAIL) from exc


def _reveal_secret(secret: str) -> None:
    """Print the secret exactly once, marked."""
    print(f"{SECRET_MARK} {secret}")
    print("(shown once — carry it to each member; only its hash is stored)")


def secret_from_line(out: str) -> Optional[str]:
    """The secret the CLI just revealed, from the marked line. Used by
    tests; production callers hold the value directly."""
    for line in out.splitlines():
        if line.startswith(f"{SECRET_MARK} "):
            return line[len(SECRET_MARK) + 1 :].strip()
    return None


# ── verbs ────────────────────────────────────────────────────────────────────


def cmd_init(args, state_dir: Path) -> int:
    roster = _load_roster(state_dir)
    name = args.name or default_self_name()
    if normalize_name(name) is None:
        print(f"error: not a usable crew member name: {name!r}", file=sys.stderr)
        return EXIT_FAIL

    try:
        ensure_host_certificate(state_dir / "crew-tls", name)
    except CrewTlsError as exc:
        print(f"warning: {exc}", file=sys.stderr)

    if args.secret is None:
        secret = mint_crew_secret()
    else:
        try:
            secret = read_secret_source(args.secret)
        except SecretArgumentError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_USAGE
    try:
        roster.init_lead(name, secret)
    except CrewStateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAIL
    roster.persist()

    if args.secret is None:
        _reveal_secret(secret)
    print(f"crew lead: {name} (roster: {roster.path})")
    return EXIT_OK


def cmd_join_token(args, state_dir: Path) -> int:
    roster = _load_roster(state_dir)
    if roster.role != "lead":
        print("error: only the lead mints join tokens", file=sys.stderr)
        return EXIT_FAIL
    token = JoinTokens(state_dir).mint()
    print(token)
    print("(one use, 10 minutes — carry it to the new member)")
    return EXIT_OK


def cmd_join(args, state_dir: Path, transport=None) -> int:
    try:
        lead_cert = read_pem_source(args.lead_cert)  # @file or -
        own_cert_pem = read_pem_source(args.cert)
        secret = read_secret_source(args.secret)
    except SecretArgumentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    name = args.name or default_self_name()
    # present ourselves to the lead FIRST: a refused token joins nothing
    try:
        if transport is None:
            from .client import UrllibTransport
            from .tls import pinned_client_context

            transport = UrllibTransport(pinned_client_context(lead_cert))
        url = args.lead_url.rstrip("/") + "/crew/v1/enrol"
        response = transport.request(
            "POST",
            url,
            headers={"authorization": f"Bearer {args.token}"},
            json_body={
                "name": name,
                "url": args.url,
                "cert_pem": own_cert_pem,
            },
            timeout_s=8.0,
        )
    except OSError as exc:
        print(f"error: cannot reach the lead: {exc}", file=sys.stderr)
        return EXIT_FAIL
    if response.status != 200:
        print(f"error: the lead refused enrolment ({response.status})", file=sys.stderr)
        return EXIT_FAIL

    roster = _load_roster(state_dir)
    try:
        roster.join_as_peer(name, args.lead_name, args.lead_url, lead_cert, secret)
    except CrewStateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAIL
    roster.persist()
    try:
        ensure_host_certificate(state_dir / "crew-tls", name)
    except CrewTlsError as exc:
        print(f"warning: {exc}", file=sys.stderr)
    print(f"crew peer: {name} following {args.lead_name} at {args.lead_url}")
    return EXIT_OK


def cmd_list(args, state_dir: Path) -> int:
    roster = _load_roster(state_dir)
    if not roster.has_crew:
        print("solo — no crew (see: crew init)")
        return EXIT_OK
    print(f"role:  {roster.role}")
    print(f"self:  {roster.self_name}")
    print(f"lead:  {roster.lead_name}")
    for name, entry in sorted(roster.peers.items()):
        print(f"peer:  {name}  {entry['url']}  pin {entry['cert_sha256_pin'][:16]}…")
    return EXIT_OK


def cmd_remove(args, state_dir: Path) -> int:
    roster = _load_roster(state_dir)
    if not roster.remove(args.member):
        print(f"error: no such crew member: {args.member}", file=sys.stderr)
        return EXIT_FAIL
    roster.persist()
    if args.member == roster.self_name or roster.role == "solo":
        print("left the crew (solo again)")
    else:
        print(f"removed {args.member}")
    return EXIT_OK


def cmd_promote(args, state_dir: Path) -> int:
    roster = _load_roster(state_dir)
    member = args.member or roster.self_name
    try:
        roster.promote(member)
    except CrewStateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAIL
    roster.persist()
    print(f"promoted {member} to lead (manual; restart the crew listener)")
    return EXIT_OK


def cmd_rotate_secret(args, state_dir: Path) -> int:
    roster = _load_roster(state_dir)
    if args.secret is None:
        new_secret = mint_crew_secret()
    else:
        try:
            new_secret = read_secret_source(args.secret)
        except SecretArgumentError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_USAGE
    try:
        roster.rotate_secret(new_secret)
    except CrewStateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAIL
    roster.persist()
    if args.secret is None:
        _reveal_secret(new_secret)
    print(
        f"crew secret rotated; the previous one stays valid {ROTATE_GRACE_S:.0f}s "
        "— run rotate-secret with the SAME new secret on every member"
    )
    return EXIT_OK


# ── the terminal ─────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="herdr-brain crew",
        description="Multi-machine federation: enrol, list, remove, promote, rotate.",
    )
    sub = parser.add_subparsers(dest="verb", required=True)

    p_init = sub.add_parser("init", help="make this host the crew lead")
    p_init.add_argument("--name", help="member name (default: this hostname)")
    p_init.add_argument(
        "--secret", metavar="SRC",
        help="crew secret source: '-' (stdin) or '@file'; omit to mint one",
    )

    sub.add_parser("join-token", help="mint a one-use join token (lead only)")

    p_join = sub.add_parser("join", help="enrol this host as a peer")
    p_join.add_argument("--name", help="this member name (default: hostname)")
    p_join.add_argument("--lead-name", required=True, help="the lead's member name")
    p_join.add_argument("--lead-url", required=True, help="the lead's crew https url")
    p_join.add_argument(
        "--lead-cert", required=True, metavar="SRC",
        help="the lead's certificate PEM: '@file' or '-'",
    )
    p_join.add_argument("--url", required=True, help="this host's crew https url")
    p_join.add_argument(
        "--cert", required=True, metavar="SRC",
        help="this host's certificate PEM: '@file' or '-'",
    )
    p_join.add_argument(
        "--secret", required=True, metavar="SRC",
        help="crew secret: '-' (stdin) or '@file'",
    )
    p_join.add_argument("--token", required=True, help="the join token from the lead")

    sub.add_parser("list", help="show the roster")

    p_remove = sub.add_parser("remove", help="drop a peer, or leave the crew")
    p_remove.add_argument("member")

    p_promote = sub.add_parser("promote", help="declare a new lead (manual)")
    p_promote.add_argument("member", nargs="?", help="default: this host")

    p_rotate = sub.add_parser("rotate-secret", help="rotate the crew secret")
    p_rotate.add_argument(
        "--secret", metavar="SRC",
        help="new secret: '-' (stdin) or '@file'; omit to mint one",
    )
    return parser


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    state_dir: Optional[Path] = None,
    transport=None,
) -> int:
    args = build_parser().parse_args(argv)
    resolved = Path(state_dir) if state_dir is not None else _default_state_dir()
    if args.verb == "init":
        return cmd_init(args, resolved)
    if args.verb == "join-token":
        return cmd_join_token(args, resolved)
    if args.verb == "join":
        return cmd_join(args, resolved, transport=transport)
    if args.verb == "list":
        return cmd_list(args, resolved)
    if args.verb == "remove":
        return cmd_remove(args, resolved)
    if args.verb == "promote":
        return cmd_promote(args, resolved)
    if args.verb == "rotate-secret":
        return cmd_rotate_secret(args, resolved)
    build_parser().error(f"unknown verb: {args.verb}")
    return EXIT_USAGE  # unreachable
