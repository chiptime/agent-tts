"""CLI for device pairing: ``pair``, ``devices list|revoke|set-expiry|
clear-expiry``.

Usage:
    python -m herdr_brain.pairing_cli pair [--label LABEL] [--expires DUR]
    python -m herdr_brain.pairing_cli devices list
    python -m herdr_brain.pairing_cli devices revoke <label>
    python -m herdr_brain.pairing_cli devices set-expiry <label> <dur>
    python -m herdr_brain.pairing_cli devices clear-expiry <label>

The operator's half of device pairing (doc 01 §7). The pure decisions
all live in ``pairing.py``; this module is the terminal, the two files
under the state dir, and the words an operator reads.

WHY THE TERMINAL MINTS THE CODE, AND NOT THE WEB UI: enrolment is a
bootstrap problem — the phone asking to be paired is the one party that
cannot yet prove anything. A "pair this device" button in the brain's
own UI would be authorised by nothing and would hand a write credential
to whoever loaded the page. The operator's shell on the host IS the
proof, so the code is minted where that proof already exists and
carried out of band — eyes, from a terminal to a phone keyboard.

The same reasoning runs the other way for revocation: a lost phone is
revoked from the machine, not from the phone. ``devices revoke`` needs
no service restart — the server re-reads ``paired-devices.json`` per
request (``PairingStore.registry``), so the device loses write access
on its very next call. The same holds for ``set-expiry`` and
``clear-expiry`` (S11): the deadline the server checks is the one on
disk, re-read per request.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from .config import load_settings
from .pairing import (
    CODE_TTL_S,
    DEVICES_FILENAME,
    PENDING_FILENAME,
    PairingStore,
    coerce_registry,
    normalize_label,
    parse_duration,
)

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2


def _stamp(seconds: float) -> str:
    """Epoch seconds → ISO-8601 UTC, or 'never' for a zero stamp."""
    if seconds and seconds > 0:
        import datetime

        return (
            datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
    return "never"


def _store(state_dir: Optional[Path] = None) -> PairingStore:
    if state_dir is None:
        state_dir = load_settings().state_dir
    return PairingStore(state_dir)


def _read_registry(state_dir: Path) -> dict:
    """The registry as it is on disk. Absent, unreadable or malformed
    all read as "nothing paired"."""
    try:
        raw = json.loads((state_dir / DEVICES_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = None
    return coerce_registry(raw)


# ── pair ─────────────────────────────────────────────────────────────────────


def cmd_pair(args, state_dir: Optional[Path] = None) -> int:
    # An EXPLICIT label that cannot be pinned is an error, not a silent
    # fall-back to the phone's label (T4 B4): the operator asked for a
    # revoke handle and must hear that it cannot be minted — validated
    # before anything is written. Same for --expires: the spelling is
    # validated here so the enrolment can never carry a guessed
    # deadline (S11).
    if args.label is not None and normalize_label(args.label) is None:
        print(
            "error: --label must be 1-48 characters after trimming "
            "(newlines and tabs collapse to spaces)",
            file=sys.stderr,
        )
        return EXIT_USAGE

    expires_in_s = None
    if getattr(args, "expires", None) is not None:
        expires_in_s = parse_duration(args.expires)
        if expires_in_s is None:
            print(
                "error: --expires must be a positive duration with one unit, "
                "e.g. 30d, 12h, 45m, 90s",
                file=sys.stderr,
            )
            return EXIT_USAGE

    store = _store(state_dir)
    pending_path = store.state_dir / PENDING_FILENAME
    replaced = pending_path.exists()
    try:
        minted = store.mint(label=args.label, expires_in_s=expires_in_s)
    except OSError as exc:
        print(f"error: could not write {pending_path} — {exc}", file=sys.stderr)
        return EXIT_FAIL

    code = minted["code"]
    minutes = round(CODE_TTL_S / 60)
    print(code)
    print("")
    print(f"  single-use · expires {_stamp(minted['expiresAt'])} ({minutes} minutes)")
    if args.label:
        print(f"  the claiming device will be enrolled as «{args.label}»")
    if expires_in_s is not None:
        print(f"  the device token caduca {args.expires} después de emparejar")
    print("  Open herdr-brain on the phone, go to Ajustes, and enter this code there.")
    print("  Shown once — only its hash is stored, and the server picks it up without a restart.")
    if replaced:
        print("  A code from an earlier `herdr-brain pair` was still pending; it is now dead.")
    return EXIT_OK


# ── devices ──────────────────────────────────────────────────────────────────


def cmd_devices_list(args, state_dir: Optional[Path] = None) -> int:
    resolved = _resolve_state_dir(state_dir)
    registry = _read_registry(resolved)
    devices = registry["devices"]
    if not devices:
        print("no devices paired — pairing is not enforced, and writes pass on the access gates alone.")
        print("Run `herdr-brain pair` to enrol the first device; that is also what turns the requirement on.")
        return EXIT_OK
    import time as time_module

    now = time_module.time()
    width = max(len(d["label"]) for d in devices)
    for device in devices:
        line = (
            f"{device['label'].ljust(width)}  "
            f"created {_stamp(device['createdAt'])}  "
            f"last seen {_stamp(device['lastSeenAt'])}"
        )
        # B7: a row WITHOUT a deadline (every pre-S11 registry, and any
        # device whose expiry was cleared) keeps the exact legacy line —
        # no trailing column — so stdout pinned before S11 still holds.
        # The expiry is shown only where one actually exists.
        expires_at = device.get("expiresAt")
        if expires_at is not None:
            state = "CADUCADO" if expires_at <= now else "caduca"
            line += f"  {state} {_stamp(expires_at)}"
        print(line)
    return EXIT_OK


def cmd_devices_revoke(args, state_dir: Optional[Path] = None) -> int:
    label = args.label
    if not label:
        print("usage: herdr-brain devices revoke <label>", file=sys.stderr)
        return EXIT_USAGE
    resolved = _resolve_state_dir(state_dir)
    registry = _read_registry(resolved)
    store = _store(resolved)
    try:
        if not store.revoke(label):
            print(f"error: no paired device labelled \"{label}\"", file=sys.stderr)
            _print_paired_hint(registry)
            return EXIT_FAIL
    except OSError as exc:
        print(f"error: could not write {store.registry_path} — {exc}", file=sys.stderr)
        return EXIT_FAIL
    remaining = _read_registry(resolved)["devices"]
    print(f"revoked \"{label}\" — it loses write access on its next request (no restart needed).")
    if not remaining:
        print("  That was the last paired device, so pairing is no longer enforced at all.")
    return EXIT_OK


def _print_paired_hint(registry: dict) -> None:
    """The unknown-label companion: name what IS paired, if anything."""
    if not registry["devices"]:
        print("  nothing is paired on this machine — `herdr-brain devices list`.", file=sys.stderr)
    else:
        paired = ", ".join(d["label"] for d in registry["devices"])
        print(f"  paired: {paired}", file=sys.stderr)


def _audit(state_dir: Path) -> "object":
    """The audit trail the server appends to, from the CLI process.

    The registry verbs that run here (set-expiry, clear-expiry) change
    a device's AUTHORITY, so they belong in the same trail as
    ``device.revoke`` — and the trail is a plain append-only JSONL
    file, which another process can append to as safely (AuditLog
    appends with O_APPEND behind a lock, never rewrites). The server
    holds no exclusivity on it. ``record()`` never raises, so a failed
    audit line can never fail the CLI verb either.
    """
    from .audit import AuditLog

    return AuditLog(state_dir / "audit.log", content=load_settings().audit_content)


def cmd_devices_set_expiry(args, state_dir: Optional[Path] = None) -> int:
    label = args.label
    duration = args.duration
    if not label or not duration:
        print("usage: herdr-brain devices set-expiry <label> <dur>", file=sys.stderr)
        return EXIT_USAGE
    seconds = parse_duration(duration)
    if seconds is None:
        print(
            f"error: <dur> must be a positive duration with one unit, "
            f"e.g. 30d, 12h, 45m, 90s (got \"{duration}\")",
            file=sys.stderr,
        )
        return EXIT_USAGE
    resolved = _resolve_state_dir(state_dir)
    registry = _read_registry(resolved)
    store = _store(resolved)
    try:
        if not store.set_expiry(label, seconds):
            print(f"error: no paired device labelled \"{label}\"", file=sys.stderr)
            _print_paired_hint(registry)
            return EXIT_FAIL
    except OSError as exc:
        print(f"error: could not write {store.registry_path} — {exc}", file=sys.stderr)
        return EXIT_FAIL
    _audit(resolved).record({
        "action": "device.set_expiry",
        "detail": {"label": label, "expires_in_s": seconds},
    })
    print(f"\"{label}\" caduca en {duration} — its token is refused with "
          "«dispositivo caducado» from the next request (no restart needed).")
    return EXIT_OK


def cmd_devices_clear_expiry(args, state_dir: Optional[Path] = None) -> int:
    label = args.label
    if not label:
        print("usage: herdr-brain devices clear-expiry <label>", file=sys.stderr)
        return EXIT_USAGE
    resolved = _resolve_state_dir(state_dir)
    registry = _read_registry(resolved)
    store = _store(resolved)
    try:
        if not store.clear_expiry(label):
            print(f"error: no paired device labelled \"{label}\"", file=sys.stderr)
            _print_paired_hint(registry)
            return EXIT_FAIL
    except OSError as exc:
        print(f"error: could not write {store.registry_path} — {exc}", file=sys.stderr)
        return EXIT_FAIL
    _audit(resolved).record({
        "action": "device.clear_expiry",
        "detail": {"label": label},
    })
    print(f"\"{label}\" ya no caduca — the token works until revoked (no restart needed).")
    return EXIT_OK


def _resolve_state_dir(state_dir: Optional[Path]) -> Path:
    if state_dir is None:
        return load_settings().state_dir
    return Path(state_dir)


def main(argv: Optional[Sequence[str]] = None, state_dir: Optional[Path] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m herdr_brain.pairing_cli",
        description="Device pairing for herdr-brain (mint codes, list and revoke devices).",
    )
    sub = parser.add_subparsers(dest="verb")

    pair_parser = sub.add_parser("pair", help="mint a one-time code the phone spends on POST /pair")
    pair_parser.add_argument(
        "--label",
        help="pin the device label for the claim (the revoke handle; "
        "1-48 characters, else the command refuses); "
        "without it the phone chooses the label",
    )
    pair_parser.add_argument(
        "--expires",
        help="the device token this code mints runs out after this "
        "duration (e.g. 30d, 12h, 45m, 90s), counted from pairing; "
        "without it the token never expires",
    )

    devices_parser = sub.add_parser("devices", help="manage paired devices")
    devices_sub = devices_parser.add_subparsers(dest="subverb")
    devices_sub.add_parser(
        "list",
        help="the paired devices, with when each was paired, last seen, "
        "and when its token runs out",
    )
    revoke_parser = devices_sub.add_parser("revoke", help="drop one device by label")
    revoke_parser.add_argument("label", nargs="?", default="")
    set_expiry_parser = devices_sub.add_parser(
        "set-expiry",
        help="arm (or re-arm) one device's token deadline: <label> <dur>",
    )
    set_expiry_parser.add_argument("label", nargs="?", default="")
    set_expiry_parser.add_argument("duration", nargs="?", default="")
    clear_expiry_parser = devices_sub.add_parser(
        "clear-expiry",
        help="remove one device's token deadline (the token goes back to never expiring)",
    )
    clear_expiry_parser.add_argument("label", nargs="?", default="")

    args = parser.parse_args(argv)

    if args.verb == "pair":
        return cmd_pair(args, state_dir)
    if args.verb == "devices":
        if args.subverb == "list":
            return cmd_devices_list(args, state_dir)
        if args.subverb == "revoke":
            return cmd_devices_revoke(args, state_dir)
        if args.subverb == "set-expiry":
            return cmd_devices_set_expiry(args, state_dir)
        if args.subverb == "clear-expiry":
            return cmd_devices_clear_expiry(args, state_dir)
        print(
            "usage: herdr-brain devices {list|revoke|set-expiry|clear-expiry}",
            file=sys.stderr,
        )
        print("  list          the paired devices, with pairing/seen/expiry stamps", file=sys.stderr)
        print("  revoke        drop one device by label: `devices revoke <label>`", file=sys.stderr)
        print("  set-expiry    arm a token deadline: `devices set-expiry <label> <dur>` (30d, 12h…)", file=sys.stderr)
        print("  clear-expiry  remove the deadline: `devices clear-expiry <label>`", file=sys.stderr)
        return EXIT_USAGE
    parser.print_help(sys.stderr)
    return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
