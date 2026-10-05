"""Speech job registry for cancellable /ask audio (voice-stack VS1.1).

/ask renders every answer to MP3; once a render starts there is no way
to stop it. The registry gives each speech-capable request a job id so
a cancel (VS1.3 endpoint) can suppress the audio between phases. It is
deliberately tiny: an in-process dict, no persistence, no threads —
the server drives every transition on the request path.

Security posture: the capability token is NEVER stored — only its
sha256 hex digest — and job reprs carry id + phase only, so logs and
tracebacks can leak neither the token nor session material.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

# Phase machine: waiting_llm -> rendering -> delivering -> complete.
# A cancel mark landing on any pre-terminal phase ends the job as
# cancelled; degraded / failed / expired-unconsumed are the remaining
# terminal outcomes. Terminal phases are final — a job is never
# reopened, only replaced by a fresh registration of the same id.
PHASE_WAITING_LLM = "waiting_llm"
PHASE_RENDERING = "rendering"
PHASE_DELIVERING = "delivering"
PHASE_COMPLETE = "complete"
PHASE_CANCELLED = "cancelled"
PHASE_DEGRADED = "degraded"
PHASE_FAILED = "failed"
PHASE_EXPIRED_UNCONSUMED = "expired-unconsumed"

_ALL_PHASES = frozenset(
    {
        PHASE_WAITING_LLM,
        PHASE_RENDERING,
        PHASE_DELIVERING,
        PHASE_COMPLETE,
        PHASE_CANCELLED,
        PHASE_DEGRADED,
        PHASE_FAILED,
        PHASE_EXPIRED_UNCONSUMED,
    }
)

TERMINAL_PHASES = frozenset(
    {
        PHASE_COMPLETE,
        PHASE_CANCELLED,
        PHASE_DEGRADED,
        PHASE_FAILED,
        PHASE_EXPIRED_UNCONSUMED,
    }
)

# Client-generated ids: printable filename-safe charset, 8-64 chars —
# the same alphabet family as the server's other external identifiers.
SPEECH_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

DEFAULT_MAX_JOBS = 32
DEFAULT_RETENTION_S = 300.0


def is_terminal_phase(phase: str) -> bool:
    return phase in TERMINAL_PHASES


def validate_speech_request_id(value: str) -> bool:
    """True when ``value`` matches the speech id charset/length contract."""
    return bool(SPEECH_ID_PATTERN.match(value))


def _token_digest(token: str) -> bytes:
    """sha256 hex digest of a capability token, as compare-ready bytes."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest().encode("ascii")


class DuplicateActiveJob(Exception):
    """An ACTIVE (non-terminal) job already holds this id."""


class RegistryFull(Exception):
    """The registry is at its max_jobs bound of ACTIVE jobs."""


class JobAlreadyTerminal(Exception):
    """Terminal jobs never reopen — mark() refused the transition."""


@dataclass
class SpeechJob:
    """One /ask speech turn. ``token_hash`` is sha256(token), never the token.

    ``cancel_event`` is the render-abort signal (VS1.3): set by
    request_cancel, polled by the cancellable renderer. It carries no
    secret material and never appears in repr — the registry still
    spawns no thread of its own.
    """

    id: str
    token_hash: str
    session_id: Optional[str] = None
    phase: str = PHASE_WAITING_LLM
    cancel_requested: bool = False
    created_ts: float = 0.0
    terminal_ts: Optional[float] = None
    cancel_event: Optional[threading.Event] = None

    def __repr__(self) -> str:  # noqa: D105 — redaction: id + phase only
        return f"SpeechJob(id={self.id!r}, phase={self.phase!r})"


class SpeechRegistry:
    """Bounded in-process speech job tracker (no persistence, no threads)."""

    def __init__(
        self,
        max_jobs: int = DEFAULT_MAX_JOBS,
        retention_s: float = DEFAULT_RETENTION_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_jobs = max_jobs
        self._retention_s = retention_s
        self._clock = clock
        self._jobs: dict[str, SpeechJob] = {}

    def __len__(self) -> int:
        return len(self._jobs)

    def active_count(self) -> int:
        return sum(1 for job in self._jobs.values() if not is_terminal_phase(job.phase))

    def register(
        self, job_id: str, token: str, session_id: Optional[str] = None
    ) -> SpeechJob:
        """Admits a job in ``waiting_llm``: hash the token, cap growth.

        Raises DuplicateActiveJob while an ACTIVE job holds the id (a
        terminal job frees it for reuse) and RegistryFull at the
        max_jobs bound of ACTIVE jobs. Stale terminal entries are
        purged first so retention actually reclaims the bound.
        """
        self.purge_expired()
        existing = self._jobs.get(job_id)
        if existing is not None and not is_terminal_phase(existing.phase):
            raise DuplicateActiveJob(job_id)
        if self.active_count() >= self._max_jobs:
            raise RegistryFull(f"{self._max_jobs} active speech jobs")
        job = SpeechJob(
            id=job_id,
            token_hash=_token_digest(token).decode("ascii"),
            session_id=session_id,
            created_ts=self._clock(),
            cancel_event=threading.Event(),
        )
        self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Optional[SpeechJob]:
        return self._jobs.get(job_id)

    def request_cancel(
        self, job_id: str, token: str, session_id: Optional[str] = None
    ) -> str:
        """Marks cancel on an ACTIVE job. A verdict, not a status code.

        "cancelled" (was active, cancel marked), "already-terminal"
        (job exists, terminal), "unknown" (no job), or "forbidden" on
        a token mismatch — the digest comparison is constant-time.

        Retention-expired jobs are purged FIRST (same reclaim rule as
        register): an expired cancel then answers "unknown" down the
        exact same code path — the caller cannot distinguish "never
        existed" from "aged out", so there is nothing to enumerate.

        The "cancelled" verdict ALSO sets the job's cancel_event so a
        render already in flight aborts mid-render; Event.set() is
        idempotent, so repeated cancels converge on the same state.
        """
        self.purge_expired()
        job = self._jobs.get(job_id)
        if job is None:
            return "unknown"
        if not hmac.compare_digest(
            job.token_hash.encode("ascii"), _token_digest(token)
        ):
            return "forbidden"
        if is_terminal_phase(job.phase):
            return "already-terminal"
        job.cancel_requested = True
        if job.cancel_event is not None:
            job.cancel_event.set()
        return "cancelled"

    def mark(self, job_id: str, phase: str) -> SpeechJob:
        """Advances a job's phase; terminal phases stamp terminal_ts.

        Unknown phases raise ValueError; a terminal job refuses every
        further mark (JobAlreadyTerminal) — reopen is never allowed.
        """
        if phase not in _ALL_PHASES:
            raise ValueError(f"unknown speech phase: {phase!r}")
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if is_terminal_phase(job.phase):
            raise JobAlreadyTerminal(f"speech job {job_id!r} is terminal ({job.phase})")
        job.phase = phase
        if is_terminal_phase(phase):
            job.terminal_ts = self._clock()
        return job

    def purge_expired(self) -> int:
        """Drops TERMINAL jobs past retention_s (aged by terminal_ts).

        ACTIVE jobs never expire by TTL — a lost client must not lose
        its cancel window. Returns the number of entries removed.
        """
        now = self._clock()
        stale = [
            job_id
            for job_id, job in self._jobs.items()
            if is_terminal_phase(job.phase)
            and job.terminal_ts is not None
            and (now - job.terminal_ts) > self._retention_s
        ]
        for job_id in stale:
            del self._jobs[job_id]
        return len(stale)


def build_registry() -> SpeechRegistry:
    """Production registry: default bounds, monotonic clock."""
    return SpeechRegistry()
