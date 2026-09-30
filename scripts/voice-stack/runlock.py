#!/usr/bin/env python3
"""Run-lock management for the voice-stack execution loop.

Migration contract
------------------
A bootstrap step creates the run lock at
``~/.local/state/voice-stack-runs/run.lock`` using ``O_CREAT | O_EXCL`` so
creation is atomic and exclusive. The lock file contains JSON:
``{"pid", "started_ts", "token", "session"}``.

This tool MIGRATES to that lock by VALIDATING its token; it never re-acquires
an existing lock. The existence of the lock means the run directory is OWNED
by another session: the only clean response is to stop. Release happens only
when the caller presents the owner token. Stale recovery is explicit,
human-confirmed, and evidence-preserving: the original lock bytes are copied
to a dated ``.stale.<ts>`` sibling (fsynced and verified) before the original
is unlinked, and the recovery must be documented in the run manifest.

This tool never sends signals to any process. Auto-kill and auto-reclaim are
forbidden by design.

Stdlib only. Python 3.11+ (developed against 3.14).
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_LOCK = Path.home() / ".local" / "state" / "voice-stack-runs" / "run.lock"

EXIT_OK = 0
EXIT_ERROR = 2          # hard error (unreadable/malformed lock, IO failure)
EXIT_NO_LOCK = 3
EXIT_TOKEN_MISMATCH = 4
EXIT_CONFIRM_REQUIRED = 5
EXIT_OWNER_ALIVE = 6

STALE_INSTRUCTIONS = """\
Stale recovery requires explicit human confirmation. Before proceeding:
1. Verify the owner PID is truly dead: check /proc/<pid>, ps output, and the
   run-dir manifests for that run.
2. A live PID whose cmdline lacks "voice-stack" is NEVER proof of staleness:
   the legitimate owner may be the opencode session itself.
3. The rename must conserve a copy of the original lock bytes and the
   recovery must be documented in the run manifest.
Re-run with --confirm once verified."""


class RunlockError(Exception):
    """Hard error: unreadable, unparseable, or malformed lock state."""


def read_lock_bytes(lock: Path) -> bytes | None:
    """Raw lock bytes, or None when the lock does not exist."""
    try:
        return lock.read_bytes()
    except FileNotFoundError:
        return None


def parse_lock(data: bytes) -> dict:
    try:
        info = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunlockError(f"lock is not valid JSON: {exc}") from exc
    if not isinstance(info, dict) or not isinstance(info.get("pid"), int) \
            or not isinstance(info.get("token"), str):
        raise RunlockError("lock JSON lacks required pid (int) / token (str)")
    return info


def tokens_match(presented: str, lock_token: str) -> bool:
    return hmac.compare_digest(
        presented.encode("utf-8"), lock_token.encode("utf-8")
    )


def pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # process exists but is owned by another user
    except OSError:
        return False


def read_cmdline(pid: int) -> str | None:
    """Bytes of /proc/<pid>/cmdline decoded best-effort; None if unreadable."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    return raw.decode("utf-8", "replace").replace("\x00", " ").strip()


def acquire_lock(lock: Path, pid: int, token: str, session: str) -> None:
    """Create the lock with O_CREAT|O_EXCL (bootstrap-style). Selftest only."""
    payload = json.dumps(
        {"pid": pid, "started_ts": time.time(), "token": token,
         "session": session}
    ).encode("utf-8")
    fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)


def _print_json(payload: dict) -> None:
    print(json.dumps(payload))


def cmd_status(lock: Path) -> int:
    try:
        data = read_lock_bytes(lock)
        info = None if data is None else parse_lock(data)
    except (OSError, RunlockError) as exc:
        print(f"error: cannot read lock {lock}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if data is None:
        _print_json({"exists": False, "pid": None, "started_ts": None,
                     "session": None, "pid_alive": None, "cmdline": None})
        return EXIT_OK
    pid = info["pid"]
    _print_json({
        "exists": True,
        "pid": pid,
        "started_ts": info.get("started_ts"),
        "session": info.get("session"),
        "pid_alive": pid_is_alive(pid),
        "cmdline": read_cmdline(pid),
    })
    return EXIT_OK


def cmd_validate(lock: Path, token: str) -> int:
    """Validate the ownership token. NEVER writes anything."""
    try:
        data = read_lock_bytes(lock)
        info = None if data is None else parse_lock(data)
    except (OSError, RunlockError) as exc:
        print(f"error: cannot read lock {lock}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if data is None:
        _print_json({"validated": False, "reason": "no-lock"})
        return EXIT_NO_LOCK
    if not tokens_match(token, info["token"]):
        _print_json({"validated": False, "reason": "token-mismatch"})
        return EXIT_TOKEN_MISMATCH
    _print_json({"validated": True, "pid": info["pid"],
                 "session": info.get("session")})
    return EXIT_OK


def cmd_release(lock: Path, token: str) -> int:
    try:
        data = read_lock_bytes(lock)
        info = None if data is None else parse_lock(data)
    except (OSError, RunlockError) as exc:
        print(f"error: cannot read lock {lock}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if data is None:
        _print_json({"released": False, "reason": "no-lock"})
        return EXIT_NO_LOCK
    if not tokens_match(token, info["token"]):
        _print_json({"released": False, "reason": "token-mismatch"})
        return EXIT_TOKEN_MISMATCH
    try:
        os.unlink(lock)  # the exact path, only after a byte-level token match
    except OSError as exc:
        print(f"error: cannot unlink {lock}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    _print_json({"released": True})
    return EXIT_OK


def cmd_stale(lock: Path, confirm: bool, force_live_pid: bool) -> int:
    if not confirm:
        print(STALE_INSTRUCTIONS)
        return EXIT_CONFIRM_REQUIRED
    try:
        data = read_lock_bytes(lock)
        info = None if data is None else parse_lock(data)
    except (OSError, RunlockError) as exc:
        print(f"error: cannot read lock {lock}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if data is None:
        _print_json({"stale": False, "reason": "no-lock"})
        return EXIT_NO_LOCK
    forced = False
    if pid_is_alive(info["pid"]):
        if not force_live_pid:
            _print_json({"stale": False, "reason": "owner-pid-alive"})
            return EXIT_OWNER_ALIVE
        forced = True
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    copy_path = Path(f"{lock}.stale.{stamp}")
    suffix = 0
    # Never clobber prior evidence: if the exact name is taken by different
    # bytes (two recoveries within the same second), disambiguate.
    while copy_path.exists() and copy_path.read_bytes() != data:
        suffix += 1
        copy_path = Path(f"{lock}.stale.{stamp}-{suffix}")
    try:
        with open(copy_path, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if copy_path.read_bytes() != data:
            raise RunlockError("evidence copy does not match original bytes")
        os.unlink(lock)
    except (OSError, RunlockError) as exc:
        print(f"error: stale recovery failed (original left in place): {exc}",
              file=sys.stderr)
        return EXIT_ERROR
    payload = {"stale": True, "copy": str(copy_path),
               "note": "document this recovery in the run manifest"}
    if forced:
        payload["forced"] = True
    _print_json(payload)
    return EXIT_OK


def cmd_selftest(lock_default: Path) -> int:
    """Isolation selftest; never touches the real lock."""
    temp = Path(tempfile.mkdtemp(prefix="voice-stack-runlock-"))

    class _Fail(Exception):
        pass

    def check(condition: bool, what: str) -> None:
        if not condition:
            raise _Fail(what)

    def run_cli(*cli: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, os.path.abspath(__file__),
             "--lock", str(lock), *cli],
            capture_output=True, text=True,
        )

    lock = temp / "run.lock"
    try:
        check(lock.resolve() != lock_default.resolve(),
              "temp lock path collides with the real lock path")

        # 1. Acquire with token t1.
        acquire_lock(lock, pid=os.getpid(), token="t1", session="selftest")
        check(lock.exists(), "acquire t1 did not create the lock")
        # 2. Second acquire must fail with FileExistsError.
        try:
            acquire_lock(lock, pid=os.getpid(), token="t1x",
                         session="selftest")
            raised = False
        except FileExistsError:
            raised = True
        check(raised, "second acquire did not raise FileExistsError")
        # 3. Validate: right token -> 0; wrong token -> 4.
        check(run_cli("validate", "--token", "t1").returncode == 0,
              "validate t1 did not exit 0")
        check(run_cli("validate", "--token", "wrong").returncode == 4,
              "validate wrong token did not exit 4")
        # 4. Release with wrong token -> refused, lock intact.
        check(run_cli("release", "--token", "wrong").returncode == 4,
              "release wrong token did not exit 4")
        check(lock.exists(), "lock vanished on refused release")
        # 5. Release with t1 -> unlinked.
        check(run_cli("release", "--token", "t1").returncode == 0,
              "release t1 did not exit 0")
        check(not lock.exists(), "lock still present after successful release")

        # 6. Dead-owner stale recovery.
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"]
        )
        proc.kill()
        proc.wait()
        acquire_lock(lock, pid=proc.pid, token="t2", session="selftest")
        t2_bytes = lock.read_bytes()
        result = run_cli("stale")
        check(result.returncode == 5,
              "stale without --confirm did not exit 5")
        check(lock.exists(), "stale without --confirm modified the lock")
        result = run_cli("stale", "--confirm")
        check(result.returncode == 0, "stale --confirm (dead) did not exit 0")
        check(not lock.exists(), "original lock not unlinked by stale")
        copies = sorted(temp.glob("run.lock.stale.*"))
        check(len(copies) == 1, "expected exactly one evidence copy")
        check(copies[0].read_bytes() == t2_bytes,
              "evidence copy bytes differ from the original lock")

        # 7. Live-owner refusal and forced proceed.
        acquire_lock(lock, pid=os.getpid(), token="t3", session="selftest")
        result = run_cli("stale", "--confirm")
        check(result.returncode == 6,
              "stale --confirm with live pid did not exit 6")
        check(lock.exists(), "live-owner refusal unlinked the lock")
        result = run_cli("stale", "--confirm", "--force-live-pid")
        check(result.returncode == 0, "forced stale did not exit 0")
        check(not lock.exists(), "forced stale did not unlink the lock")
        check(len(sorted(temp.glob("run.lock.stale.*"))) == 2,
              "expected a second evidence copy after forced stale")
        check(json.loads(result.stdout).get("forced") is True,
              "forced stale output lacks forced=true")

        print("SELFTEST_OK")
        return EXIT_OK
    except _Fail as exc:
        print(f"SELFTEST_FAIL: {exc}")
        return 1
    except (OSError, RunlockError, subprocess.SubprocessError) as exc:
        print(f"SELFTEST_FAIL: unexpected error: {exc}")
        return 1
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="runlock.py",
        description=(
            "Manage the voice-stack run lock created by bootstrap. Validates "
            "the ownership token without ever re-acquiring an existing lock; "
            "release only by owner token; stale recovery is explicit, "
            "human-confirmed, and evidence-preserving. Never signals any "
            "process."
        ),
    )
    parser.add_argument(
        "--lock", metavar="PATH", default=str(DEFAULT_LOCK),
        help="lock file path (default: %(default)s)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "status",
        help="read-only JSON status: exists, pid, started_ts, session, "
             "pid_alive, cmdline",
    )

    p_validate = sub.add_parser(
        "validate",
        help="validate the ownership token; never writes anything",
    )
    p_validate.add_argument("--token", metavar="T", required=True,
                            help="token presented by the caller")

    p_release = sub.add_parser(
        "release",
        help="unlink the lock only when it exists and the token matches",
    )
    p_release.add_argument("--token", metavar="T", required=True,
                           help="owner token")

    p_stale = sub.add_parser(
        "stale",
        help="evidence-preserving stale recovery (needs --confirm)",
    )
    p_stale.add_argument(
        "--confirm", action="store_true",
        help="proceed after human verification of owner death",
    )
    p_stale.add_argument(
        "--force-live-pid", action="store_true", dest="force_live_pid",
        help="proceed even when the owner pid is alive (recorded as forced)",
    )

    sub.add_parser(
        "selftest",
        help="run the isolation selftest; never touches the real lock",
    )

    args = parser.parse_args(argv)
    lock = Path(args.lock)

    if args.command == "status":
        return cmd_status(lock)
    if args.command == "validate":
        return cmd_validate(lock, args.token)
    if args.command == "release":
        return cmd_release(lock, args.token)
    if args.command == "stale":
        return cmd_stale(lock, args.confirm, args.force_live_pid)
    if args.command == "selftest":
        return cmd_selftest(lock)
    parser.error(f"unknown command: {args.command}")
    return EXIT_ERROR  # unreachable


if __name__ == "__main__":
    sys.exit(main())
