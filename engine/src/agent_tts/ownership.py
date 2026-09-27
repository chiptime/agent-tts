"""Control-channel ownership: atomic election over the lock marker files.

The process holding the exclusive lock owns the control channel: it is the
only one allowed to expose the IPC transport and to remove the channel
files (RF-AT-09-1). POSIX elects with flock(2) on LOCK_FILE — the kernel
releases the lock when the process dies, SIGKILL included, so ownership
never depends on clean termination. Windows has no flock: the equivalent
exclusive byte-range lock (msvcrt.locking) guards a sentinel byte of
IPC_PORT_FILE placed far past the port digits clients read, because
Windows byte locks are mandatory and must not overlap the content
(RF-AT-09-5). PID_FILE keeps its role as an informational marker only: it
has no mutex power and only the owner writes it.
"""

import os
import sys

from agent_tts.constants import IPC_PORT_FILE

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows lacks fcntl
    fcntl = None

if sys.platform == "win32":  # pragma: no cover - exercised only on Windows
    import msvcrt
else:
    msvcrt = None

# Sentinel region for the Windows election: one byte far beyond the port
# digits, so mandatory byte-range locking never blocks clients reading the
# marker content.
_WINDOWS_LOCK_OFFSET = 4096

_lock_handle = None  # holding this open file == owning the channel


def owns_channel() -> bool:
    """Returns True while this process holds the control-channel lock."""
    return _lock_handle is not None


def acquire_channel_ownership(lock_file: str) -> bool:
    """Elects the control-channel owner atomically (RF-AT-09-1, RF-AT-09-5).

    POSIX flocks ``lock_file`` (LOCK_FILE); Windows takes an exclusive lock
    on a sentinel byte of IPC_PORT_FILE. Returns True when this process
    wins — or already holds — the ownership, False when a live owner exists.
    Re-entering within the same process (sequential playbacks) reuses the
    held lock instead of deadlocking against itself.
    """
    global _lock_handle
    if owns_channel():
        return True
    handle = _acquire_windows() if msvcrt is not None else _acquire_posix(lock_file)
    if handle is None:
        return False
    _lock_handle = handle
    return True


def release_channel_ownership() -> None:
    """Drops the ownership lock; closing the handle is enough.

    The kernel also drops it on process death (SIGKILL included), which is
    what makes a crashed owner reclaimable without manual cleanup.
    """
    global _lock_handle
    if _lock_handle is not None:
        try:
            _lock_handle.close()
        except OSError:
            pass
        _lock_handle = None


def _acquire_posix(lock_file: str):
    """Tries a non-blocking exclusive flock on the lock file."""
    try:
        f = open(lock_file, "a+")
    except OSError:
        return None
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def _acquire_windows():
    """Tries a non-blocking exclusive byte-range lock on the port marker."""
    f = None
    try:
        f = open(IPC_PORT_FILE, "a+")
        f.seek(_WINDOWS_LOCK_OFFSET)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        if f is not None:
            try:
                f.close()
            except OSError:
                pass
        return None
    return f
