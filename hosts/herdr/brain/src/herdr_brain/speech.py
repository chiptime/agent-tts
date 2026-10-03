"""Speech job registry for cancellable /ask audio (voice-stack VS1.1).

/ask renders every answer to MP3; once a render starts there is no way
to stop it. The registry gives each speech-capable request a job id so
a cancel (VS1.3 endpoint) can suppress the audio between phases. It is
deliberately tiny: an in-process dict, no persistence — the server
drives every transition on the request path. The ONE deliberate thread
is the VS2.4 segmented producer, which the server spawns per job and
which drives the registry itself (guarded marks, terminal exactly once).

Security posture: the capability token is NEVER stored — only its
sha256 hex digest — and job reprs carry id + phase only, so logs and
tracebacks can leak neither the token nor session material.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

# Same-package reuse of the VS1.2 cancellable-render machinery (task
# constraint: tts.py is not editable — import its private helpers):
# the segmented producer rides the exact job-factory/teardown contract
# the cancellable full-file render established.
from .tts import _PopenRunner, _terminate_job, sanitize_for_speech

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


SEGMENTED_UNAVAILABLE = "segmented-unavailable"


def choose_speech_path(settings, protocols=None) -> tuple:
    """('segmented', None) when the host speaks protocol 2; otherwise the
    legacy full-file path with the VISIBLE degradation reason (contract
    tts-brain-v2.md: a v1-only host never blocks the textual answer)."""
    if protocols is None:
        from .tts import host_supported_protocols

        protocols = host_supported_protocols(settings)
    if 2 in protocols:
        return "segmented", None
    return "legacy", SEGMENTED_UNAVAILABLE


# -- segmented dispatch constants (VS2.4, task T11) --------------------------
# ALL of these are read AT USE TIME (module attribute lookups inside the
# functions/methods below), never captured into long-lived locals or
# defaults — tests monkeypatch the module attributes directly.
#
# Max RECEIVED-NOT-ACKED segments staged per job: playback (VS2.5 /next)
# acks segments to free this bound, keeping the producer honest about
# backpressure instead of buffering a whole answer in memory.
SPEECH_SEGMENT_BUFFER = 8
# How long a producer may block on a full buffer before the job degrades
# and the render is cancelled (a stuck consumer must not wedge the job).
SPEECH_PRODUCER_BLOCK_S = 30.0
# Hard cap on segments per job: a hostile/looping host can not flood the
# audio dir (nor the registry) with an unbounded sequence.
SPEECH_MAX_SEGMENTS = 512
# Manifest poll cadence of the producer loop (and the small sleep of the
# bounded buffer add).
SEGMENT_POLL_S = 0.05

# -- VS2.5 /next dual-watermark transport (design T5) -------------------------

# Long-poll hold of GET /speech/{id}/next when the requested cursor is
# not ready yet and the job is still ACTIVE. Read at use time —
# monkeypatch-friendly in tests.
SPEECH_NEXT_HOLD_S = 10.0
# How often a held /next re-checks the buffer for the wanted seq.
SPEECH_NEXT_POLL_S = 0.05
# A delivering job whose buffered segments stopped being ACKED for this
# long is swept to expired-unconsumed on its next touch (see
# sweep_unconsumed — lazy, no dedicated reaper thread).
SPEECH_UNCONSUMED_TIMEOUT_S = 300.0

# Wall-clock indirection over time.monotonic: sweep_unconsumed and the
# SegmentBuffer's sweep bookkeeping timestamps read THIS attribute, so
# tests monkeypatch it for deterministic clock control.
_monotonic = time.monotonic



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
    # VS2.4 segmented dispatch attachments (opaque here): the bounded
    # staging buffer and the producer driving this job's render, so the
    # /next endpoint (VS2.5) can serve from the registry. Both stay
    # None on every non-segmented path; the repr above ignores them.
    segments: object = None
    producer: object = None
    # VS2.5 playback watermark: the highest seq the consumer ACKED via
    # /next (evidence of playback progress, -1 = nothing acked yet).
    # Monotone by construction — the endpoint refuses any ack below it.
    ack_watermark: int = -1

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


class SegmentBuffer:
    """Thread-safe bounded staging of segment METADATA (VS2.4).

    Holds ``{seq, file, bytes}`` dicts — paths and accounting numbers,
    NEVER audio bytes: the audio itself lands in the audio dir via
    shutil.move before anything enters this buffer. The producer adds
    segments as they are received; /next (VS2.5) drains it with
    ``ack_upto`` which frees capacity for the producer to continue.
    """

    def __init__(self, capacity: Optional[int] = None) -> None:
        # ``capacity=None`` reads the SPEECH_SEGMENT_BUFFER module
        # constant on EVERY space check, so monkeypatching the module
        # attribute works even for buffers constructed earlier.
        self._capacity = capacity
        self._lock = threading.Lock()
        self._segments: list[dict] = []
        self._ack_watermark = -1
        # High-water mark of staged segments (tests pin the bound).
        self.peak = 0
        # Sweep bookkeeping (VS2.5, module ``_monotonic`` clock): when
        # the first segment was staged, and when an ack last ADVANCED
        # the release watermark. sweep_unconsumed ages a job by
        # max(last_ack_ts, first_add_ts, terminal_ts) — an idle
        # re-poll (equal ack) refreshes nothing.
        self.first_add_ts: Optional[float] = None
        self.last_ack_ts: Optional[float] = None

    def _bound(self) -> int:
        return self._capacity if self._capacity is not None else SPEECH_SEGMENT_BUFFER

    def add(self, segment: dict) -> bool:
        """Stages one segment, blocking while full.

        Blocks up to SPEECH_PRODUCER_BLOCK_S (read at call time) polling
        at SEGMENT_POLL_S cadence (read every iteration). True once the
        segment is staged; False on timeout — the caller degrades the
        job rather than buffer unboundedly.
        """
        deadline = time.monotonic() + SPEECH_PRODUCER_BLOCK_S
        while True:
            with self._lock:
                if len(self._segments) < self._bound():
                    self._segments.append(dict(segment))
                    if self.first_add_ts is None:
                        self.first_add_ts = _monotonic()
                    if len(self._segments) > self.peak:
                        self.peak = len(self._segments)
                    return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(SEGMENT_POLL_S)

    @property
    def last_ack(self) -> int:
        """Highest acked seq — the release watermark (VS2.5 name)."""
        return self._ack_watermark

    def ack_upto(self, seq: int) -> int:
        """Releases segments with ``seq <= seq`` (monotone contiguous).

        Frees capacity for the producer. Non-monotone input (a watermark
        at or below the previous one) is silently ignored — VS2.5 owns
        the typed 422 semantics on the wire; here it is a no-op. Returns
        the number of segments released.
        """
        with self._lock:
            if seq <= self._ack_watermark:
                return 0
            self._ack_watermark = seq
            self.last_ack_ts = _monotonic()
            kept = [segment for segment in self._segments if segment["seq"] > seq]
            released = len(self._segments) - len(kept)
            self._segments[:] = kept
            return released

    def snapshot(self) -> list:
        """Ordered copy of the staged segment metadata."""
        with self._lock:
            return [dict(segment) for segment in self._segments]

    def __len__(self) -> int:
        with self._lock:
            return len(self._segments)


def sweep_unconsumed(registry: SpeechRegistry, now: Optional[float] = None) -> int:
    """Lazily expires jobs whose consumer vanished mid-delivery (VS2.5).

    A job in ``delivering`` or ``complete`` whose buffer still holds
    UNACKED segments and whose ack-progress age —
    ``now - max(last_ack_ts, first_add_ts, terminal_ts if set)`` —
    exceeds SPEECH_UNCONSUMED_TIMEOUT_S is marked
    PHASE_EXPIRED_UNCONSUMED: terminal, guarded, exactly once (the
    registry's mark itself refuses any terminal reopen). The point is
    reclaiming ABANDONED ACTIVE jobs — delivering entries hold an
    active slot in a bounded registry that no TTL would ever free. A
    COMPLETE candidate is already terminal, so its guarded mark is a
    documented no-op and retention owns the cleanup instead.

    Honest limitation (design T5): there is NO dedicated reaper thread
    and none is spawned here — the sweep runs on touch (the /next
    endpoint entry, and it is safe to call from any other registry
    touchpoint), so a vanished consumer's job becomes visibly
    expired-unconsumed only on the FIRST touch after the deadline.
    Finite and visible beats prompt-and-threaded.

    ``now`` defaults to the module ``_monotonic`` clock so tests can
    drive time deterministically. Returns the number of jobs swept.
    """
    if now is None:
        now = _monotonic()
    swept = 0
    for job in list(registry._jobs.values()):
        if job.phase not in (PHASE_DELIVERING, PHASE_COMPLETE):
            continue
        buffer = job.segments
        if buffer is None:
            continue
        # UNACKED judged by the buffer's OWN release watermark (kept in
        # lockstep with job.ack_watermark by the /next endpoint).
        unacked = any(
            segment["seq"] > buffer.last_ack for segment in buffer.snapshot()
        )
        if not unacked:
            continue  # everything staged was consumed — no abandonment
        stamps = [
            stamp
            for stamp in (buffer.first_add_ts, buffer.last_ack_ts, job.terminal_ts)
            if stamp is not None
        ]
        if not stamps or (now - max(stamps)) <= SPEECH_UNCONSUMED_TIMEOUT_S:
            continue
        try:
            registry.mark(job.id, PHASE_EXPIRED_UNCONSUMED)
        except JobAlreadyTerminal:
            continue  # complete is retention's to reclaim, not ours
        swept += 1
    return swept


def _manifest_segment_count(manifest: Optional[dict]) -> int:
    """How many segments the manifest claims, tolerating garbage (0)."""
    if manifest is None:
        return 0
    entries = manifest.get("segments")
    return len(entries) if isinstance(entries, list) else 0


class SegmentedSpeechProducer:
    """Drives ONE job's protocol-2 segmented render (VS2.4).

    Owns a per-job daemon thread that launches the host subprocess
    (``--render-text-segmented``, text by FILE, opaque id in argv —
    contract tts-brain-v2.md), watches the atomically re-published
    ``manifest.json``, moves each new ``seg-<seq>.mp3`` into the audio
    dir as ``sr-<job.id>-<seq>.mp3`` and stages its metadata on the
    job's bounded SegmentBuffer. The subprocess machinery is the VS1.2
    cancellable render's (tts._PopenRunner / tts._terminate_job): the
    job owns its process group, and cancel signals ONLY that group.

    The producer thread is the sole phase driver of a segmented turn:
    rendering → delivering (first staged segment) → one terminal phase,
    each transition guarded so terminal lands exactly once.
    """

    # Contiguity watermark of the poll loop (instance state, single
    # producer thread per job).
    _last_seq = -1

    def __init__(
        self,
        settings,
        registry: SpeechRegistry,
        job: SpeechJob,
        text,
        buffer: Optional[SegmentBuffer] = None,
        runner=None,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._job = job
        self._text = text
        self.buffer = buffer if buffer is not None else SegmentBuffer()
        # Injectable Popen-like job factory (tts._PopenRunner contract);
        # the default is instantiated in _run so its per-producer stderr
        # temp file is closed on THIS producer's outcome.
        self._runner = runner
        # The worker thread (None until start()); public so tests (and
        # later /next) can join an observable producer.
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Marks rendering and spawns the daemon worker thread."""
        self._mark(PHASE_RENDERING)
        self.thread = threading.Thread(
            target=self._run, name=f"sr-{self._job.id}", daemon=True
        )
        self.thread.start()

    # -- guarded phase transitions (terminal exactly once) ------------------

    def _mark(self, phase: str) -> bool:
        """Registry mark that tolerates a JobAlreadyTerminal race — the
        exactly-once terminal promise stays structural, never fatal."""
        try:
            self._registry.mark(self._job.id, phase)
            return True
        except JobAlreadyTerminal:
            return False

    def _read_manifest(self, manifest_path: Path) -> Optional[dict]:
        """Latest manifest, or None while missing/invalid.

        Atomic publishes (tmp + replace) mean a missing or unparsable
        file is transient: keep polling, never trust a partial read.
        """
        try:
            raw = manifest_path.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            payload = json.loads(raw)
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None

    def _cleanup_dir(self, out_dir: Path) -> None:
        """Best-effort temp out-dir removal.

        Published sr-*.mp3 files in the audio dir are kept for
        accounting — only the host-side scratch dir goes away.
        """
        shutil.rmtree(out_dir, ignore_errors=True)

    def _cleanup_if_drained(self, out_dir: Path, manifest: Optional[dict]) -> None:
        """Removes the scratch dir only when no UNPROCESSED published
        segment remains in it (failure paths keep evidence otherwise)."""
        if self._last_seq + 1 >= _manifest_segment_count(manifest):
            self._cleanup_dir(out_dir)

    def _run(self) -> None:
        clean = sanitize_for_speech(self._text or "")
        if not clean:
            self._mark(PHASE_FAILED)
            return
        launch = self._runner if self._runner is not None else _PopenRunner()
        out_dir = Path(tempfile.mkdtemp(prefix="sr-"))
        try:
            # Text travels by FILE (never argv); argv carries only the
            # server paths and the opaque request id (contract v2).
            in_file = out_dir / "input.txt"
            in_file.write_text(clean, encoding="utf-8")
            argv = [
                str(self._settings.tts_bin),
                "--render-text-segmented",
                str(out_dir),
                str(in_file),
                "--speech-request-id",
                self._job.id,
                "--voice",
                self._settings.tts_voice,
                "--rate",
                self._settings.tts_rate,
            ]
            try:
                child = launch(argv)
            except OSError:
                self._mark(PHASE_FAILED)
                self._cleanup_dir(out_dir)
                return
            self._poll(out_dir, child)
        finally:
            closer = getattr(launch, "close", None)
            if closer is not None:
                closer()

    def _poll(self, out_dir: Path, child) -> None:
        """Watch manifest + child until one terminal condition lands."""
        manifest_path = out_dir / "manifest.json"
        deadline = time.monotonic() + self._settings.tts_timeout_s
        delivering = False
        # After the child exits, the manifest on disk is final (terminal
        # manifests publish BEFORE exit) — one extra pass re-reads it so
        # a stale in-flight read can never misclassify the outcome.
        final_pass = False
        manifest: Optional[dict] = None
        while True:
            if self._job.cancel_event is not None and self._job.cancel_event.is_set():
                _terminate_job(child)
                self._cleanup_dir(out_dir)
                self._mark(PHASE_CANCELLED)
                return
            manifest = self._read_manifest(manifest_path)
            if manifest is not None:
                degraded = self._process_segments(manifest, out_dir, child)
                if degraded:
                    return  # degrade sub-path already handled terminally
                if not delivering and self._last_seq >= 0:
                    delivering = True
                    self._mark(PHASE_DELIVERING)
            rc = child.poll()
            if manifest is not None:
                all_processed = (
                    self._last_seq + 1 >= _manifest_segment_count(manifest)
                )
                phase = self._terminal_phase(manifest, rc, all_processed)
                if phase is not None:
                    if phase != PHASE_COMPLETE and child.poll() is None:
                        # Cancel/degrade/failure with the host still
                        # alive: same teardown as the cancellable render
                        # (TERM → grace → KILL to the job's OWN group).
                        _terminate_job(child)
                    if phase == PHASE_FAILED:
                        self._cleanup_if_drained(out_dir, manifest)
                    else:
                        self._cleanup_dir(out_dir)
                    self._mark(phase)
                    return
            if rc is not None:
                if not final_pass:
                    final_pass = True
                    continue  # re-read the now-final manifest once
                # Child exited with NO terminal manifest even after the
                # final re-read: no manifest at all is the contract's
                # "early failure"; an unclassified exit (e.g. 0 without
                # is_complete) is a contract violation. Either way the
                # scratch dir keeps any segment it never gave us.
                self._cleanup_if_drained(out_dir, manifest)
                self._mark(PHASE_FAILED)
                return
            if time.monotonic() >= deadline:
                _terminate_job(child)
                self._cleanup_dir(out_dir)
                # Overall budget exhausted — the same honest outcome as
                # render_mp3_cancellable's timeout.
                self._mark(PHASE_FAILED)
                return
            time.sleep(SEGMENT_POLL_S)

    def _process_segments(self, manifest: dict, out_dir: Path, child):
        """Moves + stages every NEW published segment, in seq order.

        Returns True when a degrade sub-path (contiguity gap, hard-cap
        excess, buffer-block timeout) drove the job terminal and the
        poll loop must stop; None to keep polling. Malformed manifest
        entries are treated as transient garbage: nothing is processed
        this round, polling continues.
        """
        entries = manifest.get("segments")
        if not isinstance(entries, list):
            return None
        try:
            pending = sorted(
                (int(entry["seq"]), entry)
                for entry in entries
                if int(entry["seq"]) > self._last_seq
            )
        except (KeyError, TypeError, ValueError):
            return None  # malformed this round — keep polling
        for seq, entry in pending:
            if seq > self._last_seq + 1:
                # Contiguity gap: the host skipped a sequence number,
                # which the atomic-manifest contract forbids. There is
                # no honest way to serve a hole — degrade visibly.
                _terminate_job(child)
                self._cleanup_dir(out_dir)
                self._mark(PHASE_DEGRADED)
                return True
            if seq >= SPEECH_MAX_SEGMENTS:
                # Hard cap: a looping host can not flood the audio dir.
                _terminate_job(child)
                self._cleanup_dir(out_dir)
                self._mark(PHASE_DEGRADED)
                return True
            src = out_dir / str(entry.get("file") or f"seg-{seq:04d}.mp3")
            dst = self._settings.audio_dir / f"sr-{self._job.id}-{seq:04d}.mp3"
            try:
                self._settings.audio_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
            except OSError:
                # Manifest lists the segment but the file is not moveable
                # YET — transient; retry on the next poll without
                # advancing the watermark.
                return None
            if not self.buffer.add(
                {"seq": seq, "file": dst.name, "bytes": entry.get("bytes") or 0}
            ):
                # Buffer stayed full past the bounded block: nothing is
                # draining it (no /next yet, or a stuck consumer) —
                # cancel the render and degrade honestly. The segment
                # this loop JUST moved is discarded: it was never staged,
                # so no consumer can ever reach it — keeping it would
                # leave an orphan past the capacity the test contract
                # pins ("capacity-many sr files"). Every STAGED sr file
                # stays for accounting.
                try:
                    dst.unlink()
                except OSError:
                    pass
                _terminate_job(child)
                self._cleanup_dir(out_dir)
                self._mark(PHASE_DEGRADED)
                return True
            self._last_seq = seq
        return None

    def _terminal_phase(self, manifest: dict, rc, all_processed: bool) -> Optional[str]:
        """Maps the terminal manifest/exit-code contract to a phase.

        Returns None while no terminal condition holds. rc 3 or a
        ``cancelled: true`` manifest maps to OUR cancel only when the
        brain actually requested one; a host cancelling on its own is
        NOT the client's cancel — labelling it "cancelled" would hide a
        host-side anomaly, so it degrades VISIBLY instead. COMPLETE
        additionally requires every published segment to have been
        RECEIVED (moved + staged): a complete manifest with an
        unmovable segment file is a host contract violation, not a
        delivered answer.
        """
        cancelled = bool(manifest.get("cancelled"))
        error = manifest.get("error")
        complete = bool(manifest.get("is_complete"))
        if cancelled or rc == 3:
            if self._job.cancel_requested:
                return PHASE_CANCELLED
            return PHASE_DEGRADED
        if error is not None or (rc is not None and rc != 0):
            return PHASE_FAILED
        if complete and rc == 0 and all_processed:
            # Complete the moment the last segment was RECEIVED (moved +
            # staged) — /next acks (VS2.5) are delivery bookkeeping, not
            # a completeness condition.
            return PHASE_COMPLETE
        return None
