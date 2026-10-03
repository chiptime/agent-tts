"""Cross-provider fallback chain (VS4.1 config + VS4.2 walker).

The engine's orchestration (cli.synthesize — the funnel every synthesis
job goes through: batch jobs once, pipelined streaming jobs per sentence
group, daemon requests via cli._play_speech) consults this module before
each synthesis:

- No config file, or ``fallback_enabled: false``  ⇒ the caller takes the
  exact historical code path (nothing in this module runs). The guard
  lives at the wrap point in cli.synthesize.
- Enabled config ⇒ :func:`run_fallback_synthesis` walks the chain:
  the job's requested provider first, then each configured link.

Locked design decisions (T9):

- Config: env ``AGENT_TTS_FALLBACK_CONFIG`` (default
  ``~/.config/agent-tts/fallback.json`` — the generic agent-tts user
  config pattern, no host paths inside the engine), strict schema
  ``{fallback_enabled, chain: [{provider, voice, notes}]}`` with
  non-empty mandatory ``notes``. Invalid files raise
  :class:`FallbackConfigError`; the orchestration boundary
  (:func:`resolve_active_fallback_config`) fails open to the disabled
  behavior with one stderr line so a broken config can never make
  speech unavailable.

- Budget: ONE shared counter per synthesis intent.
  ``FALLBACK_TOTAL_ATTEMPTS`` (4) covers TOGETHER the same-provider
  retry (the walker's in-link re-submit after a retryable failure —
  each elevenlabs/openai submit internally includes the locked
  stream-then-full-response same-provider retry) AND the cross-provider
  attempts. ``FALLBACK_LINK_ATTEMPTS`` (2) caps the attempts charged to
  a single link. The accounting unit is the walker-visible submit:
  providers swallow their internal errors into ``b""`` and edge's batch
  path proves ``supports_stream`` is not an internal-retry marker, so
  charging anything finer would require coupling the orchestration to
  provider internals (forbidden by T9 FR-06).

- Classification (failure ⇒ what next): network errors, HTTP 5xx,
  HTTP 429 and provider timeouts are retryable in-link with the fixed
  backoff schedule (2 s, then 4 s for every later retry); HTTP 4xx
  (except 429), missing auth/key and unsupported-voice errors skip the
  link immediately; anything unclassified skips too (unknown failures
  never burn retry budget). Advancing to the NEXT link after a
  retryable exhaustion is immediate — the backoff protects a rate-limited
  or flaky endpoint being resubmitted, not a different service.

- Cancellation: ``stop_checker`` is consulted before EVERY attempt
  (including between links, after failures and before every backoff
  sleep). A cancelled walk records one ``cancelled`` outcome, returns
  ``b""`` (the engine's historical cancel semantics) and NEVER falls
  back.

- Audible partials (VS4.3, T9 locked): automatic fallback may only
  re-synthesize bytes that were NEVER audible. File/no-play/batch jobs
  own no audio until they complete, so their walks fall back freely
  (each attempt is a FULL restart of the intent's text). Once ANY bytes
  of a playback job became audible (the pipelined path commits groups
  to the playback session as they are produced), a synthesis failure
  stops the walk in the ``partial/uncertain`` state: no automatic
  retry, no cross-fallback in any mode — the way out is a DELIBERATE
  replay intent started by the caller, which re-reads from the start of
  the failed group and may duplicate audio that was already heard; an
  "exact resume" is impossible once bytes reached the speaker and is
  never faked. The walker learns audibility through the job-scoped
  :class:`AudibilityProbe` (the pipelined path's single played-group
  accounting, never a second bookkeeping).

- Attempt log: every attempt appends exactly ``{provider, outcome,
  ts}`` with outcome in ``ok | retryable-fail | skip | cancelled |
  partial-uncertain`` — the VS4.4 reporting schema, recorded from day
  one.
"""

import asyncio
import json
import os
import re
import sys
import time
import urllib.error
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from agent_tts.providers import TTSProvider, get_provider

__all__ = [
    "AGENT_TTS_FALLBACK_CONFIG_ENV",
    "ATTEMPT_OUTCOMES",
    "FALLBACK_TOTAL_ATTEMPTS",
    "FALLBACK_LINK_ATTEMPTS",
    "FALLBACK_BACKOFF_SCHEDULE_SEC",
    "PARTIAL_UNCERTAIN",
    "AttemptLog",
    "AttemptRecord",
    "AudibilityProbe",
    "FallbackConfig",
    "FallbackConfigError",
    "FallbackLink",
    "SynthesisIntent",
    "WalkResult",
    "classify_exception",
    "fallback_config_path",
    "link_key_missing",
    "load_fallback_config",
    "resolve_active_fallback_config",
    "run_fallback_synthesis",
]

# --- Locked constants (T9) -----------------------------------------------------

#: Env override for the fallback chain config file (AGENT_TTS_* pattern).
AGENT_TTS_FALLBACK_CONFIG_ENV = "AGENT_TTS_FALLBACK_CONFIG"
#: Default config location: the generic agent-tts user config directory.
DEFAULT_FALLBACK_CONFIG_PATH = "~/.config/agent-tts/fallback.json"
#: One shared budget per synthesis intent, covering together the
#: same-provider retry and every cross-provider attempt.
FALLBACK_TOTAL_ATTEMPTS = 4
#: Maximum attempts charged to a single chain link.
FALLBACK_LINK_ATTEMPTS = 2
#: Fixed backoff before in-link resubmits: 2 s first, then 4 s.
FALLBACK_BACKOFF_SCHEDULE_SEC = (2.0, 4.0)


# --- VS4.1: configuration ------------------------------------------------------

class FallbackConfigError(Exception):
    """Raised when the fallback config file exists but is invalid.

    Carries a machine-readable ``reason`` (and the ``path`` it came
    from) so callers can report exactly which schema rule failed.
    """

    def __init__(self, reason: str, path: str = ""):
        self.reason = reason
        self.path = path
        location = f" ({path})" if path else ""
        super().__init__(f"invalid fallback config{location}: {reason}")


@dataclass(frozen=True)
class FallbackLink:
    """One chain link: the provider+voice to try, with a human note.

    ``notes`` is mandatory and non-empty (D3): a fallback chain is an
    operational document — whoever edits it must say why the link is
    there (cost, latency, offline availability, ...).
    """

    provider: str
    voice: str
    notes: str


@dataclass(frozen=True)
class FallbackConfig:
    """Validated fallback configuration (or the disabled representation)."""

    enabled: bool
    chain: Tuple[FallbackLink, ...]
    source: str

    @classmethod
    def disabled(cls, source: str) -> "FallbackConfig":
        """The absent/disabled representation: exactly today's behavior."""
        return cls(enabled=False, chain=(), source=source)


_TOP_LEVEL_KEYS = {"fallback_enabled", "chain"}
_LINK_KEYS = {"provider", "voice", "notes"}


def fallback_config_path(path: Optional[str] = None) -> str:
    """Resolves the config path: explicit arg, then env, then the default."""
    if path:
        return os.path.expanduser(path)
    env_path = os.environ.get(AGENT_TTS_FALLBACK_CONFIG_ENV, "")
    if env_path:
        return os.path.expanduser(env_path)
    return os.path.expanduser(DEFAULT_FALLBACK_CONFIG_PATH)


def _require_nonempty_str(value: Any, what: str, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FallbackConfigError(f"{what} must be a non-empty string", path)
    return value


def _validate_mapping(data: Any, path: str) -> FallbackConfig:
    """Strict schema validation: exactly the locked keys, nothing else."""
    if not isinstance(data, dict):
        raise FallbackConfigError("config must be a JSON object", path)
    unknown = set(data) - _TOP_LEVEL_KEYS
    if unknown:
        keys = ", ".join(sorted(unknown))
        raise FallbackConfigError(
            f"unknown field(s) {keys}; expected exactly 'fallback_enabled' and 'chain'", path
        )
    missing = _TOP_LEVEL_KEYS - set(data)
    if missing:
        keys = ", ".join(sorted(missing))
        raise FallbackConfigError(f"missing required field(s) {keys}", path)

    enabled = data["fallback_enabled"]
    if not isinstance(enabled, bool):
        raise FallbackConfigError("'fallback_enabled' must be a boolean", path)

    raw_chain = data["chain"]
    if not isinstance(raw_chain, list):
        raise FallbackConfigError("'chain' must be a list of link objects", path)

    links: List[FallbackLink] = []
    for idx, raw_link in enumerate(raw_chain):
        if not isinstance(raw_link, dict):
            raise FallbackConfigError(f"chain[{idx}] must be an object", path)
        unknown = set(raw_link) - _LINK_KEYS
        if unknown:
            keys = ", ".join(sorted(unknown))
            raise FallbackConfigError(f"chain[{idx}]: unknown field(s) {keys}", path)
        missing = _LINK_KEYS - set(raw_link)
        if missing:
            keys = ", ".join(sorted(missing))
            raise FallbackConfigError(f"chain[{idx}]: missing field(s) {keys}", path)
        links.append(
            FallbackLink(
                provider=_require_nonempty_str(raw_link["provider"], f"chain[{idx}].provider", path),
                voice=_require_nonempty_str(raw_link["voice"], f"chain[{idx}].voice", path),
                notes=_require_nonempty_str(raw_link["notes"], f"chain[{idx}].notes", path),
            )
        )
    return FallbackConfig(enabled=enabled, chain=tuple(links), source=path)


def load_fallback_config(path: Optional[str] = None) -> FallbackConfig:
    """Loads and validates the fallback chain config.

    A missing file is not an error: it returns the disabled
    representation (the caller keeps exactly today's behavior). A file
    that exists but is unreadable or violates the strict schema raises
    :class:`FallbackConfigError`.
    """
    resolved = fallback_config_path(path)
    if not os.path.isfile(resolved):
        return FallbackConfig.disabled(resolved)
    try:
        with open(resolved, "r", encoding="utf-8") as f:
            data = json.load(f)
    except ValueError as e:
        raise FallbackConfigError(f"invalid JSON: {e}", resolved) from e
    except OSError as e:
        raise FallbackConfigError(f"could not read file: {e}", resolved) from e
    return _validate_mapping(data, resolved)


# --- Config cache ---------------------------------------------------------------
# resolve_active_fallback_config sits on the synthesis hot path (once per
# cli.synthesize call, i.e. per batch job or per streaming sentence group),
# so the loaded config is cached keyed by resolved path + (mtime, size).
# Tests get an explicit reset hook; editing the file invalidates the cache.

_CONFIG_CACHE: Dict[str, Tuple[Optional[Tuple[int, int]], FallbackConfig]] = {}


def _config_signature(path: str) -> Optional[Tuple[int, int]]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def resolve_active_fallback_config() -> FallbackConfig:
    """Load-once-with-invalidation config for the orchestration seat.

    Fail-open policy: an INVALID file logs one English stderr line and
    yields the disabled config — a broken fallback.json must never make
    speech unavailable (the typed error stays available to direct
    ``load_fallback_config`` callers and tests).
    """
    path = fallback_config_path()
    signature = _config_signature(path)
    cached = _CONFIG_CACHE.get(path)
    if cached is not None and cached[0] == signature:
        return cached[1]
    if signature is None:
        config = FallbackConfig.disabled(path)
    else:
        try:
            config = load_fallback_config(path)
        except FallbackConfigError as e:
            print(f"agent-tts: {e}; fallback disabled", file=sys.stderr)
            config = FallbackConfig.disabled(path)
    _CONFIG_CACHE[path] = (signature, config)
    return config


def _reset_fallback_config_cache() -> None:
    """Test hook: drops the resolved-config cache."""
    _CONFIG_CACHE.clear()


# --- Failure classification (locked table) --------------------------------------

RETRYABLE = "retryable"
SKIP_LINK = "skip"

_AUTH_ERROR_RE = re.compile(r"api[ _-]?key|auth|credential|unauthorized", re.IGNORECASE)


def classify_exception(exc: BaseException) -> str:
    """Maps one failure to its locked disposition.

    HTTPError must be tested before URLError (it subclasses it).
    Network errors, provider timeouts, HTTP 5xx and 429 retry in-link;
    other 4xx, auth/key problems and bad-argument/voice errors skip the
    link; unclassified failures also skip — an unknown error never burns
    retry budget.
    """
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 429 or exc.code >= 500:
            return RETRYABLE
        return SKIP_LINK
    if isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError, OSError)):
        return RETRYABLE
    if isinstance(exc, ValueError):
        return SKIP_LINK
    if isinstance(exc, RuntimeError) and _AUTH_ERROR_RE.search(str(exc)):
        return SKIP_LINK
    return SKIP_LINK


#: Provider name → (intent attribute carrying the explicit key, env var).
#: Providers absent from this table have no key concept (edge, piper,
#: kokoro) and are always submittable.
_KEY_REQUIREMENTS: Dict[str, Tuple[str, str]] = {
    "openai": ("openai_key", "OPENAI_API_KEY"),
    "elevenlabs": ("eleven_key", "ELEVENLABS_API_KEY"),
    "eleven": ("eleven_key", "ELEVENLABS_API_KEY"),
}


def link_key_missing(provider: str, intent: "SynthesisIntent") -> bool:
    """True when the link's provider needs an API key nobody supplied.

    The check is name-based and happens BEFORE any submit: a link
    without a key is not-configured, an honest skip — not a doomed
    network round trip.
    """
    requirement = _KEY_REQUIREMENTS.get((provider or "").strip().lower())
    if requirement is None:
        return False
    attr, env_var = requirement
    if getattr(intent, attr, None):
        return False
    return not os.environ.get(env_var, "")


# --- VS4.2: the chain walker -----------------------------------------------------

@dataclass
class SynthesisIntent:
    """The current job's synthesis request, immutable per walk.

    Mirrors the cli.synthesize parameters: the primary link uses
    ``provider``/``voice``; chain links replace both with their own
    entry values. Every other parameter rides along unchanged so each
    attempt runs through the exact historical single-attempt path.
    """

    text: str
    voice: str
    rate: str
    volume: str
    pitch: str
    output_file: Optional[str]
    provider: str
    openai_key: Optional[str]
    openai_base_url: Optional[str]
    openai_model: Optional[str]
    eleven_key: Optional[str]
    eleven_model: Optional[str]
    piper_model: Optional[str]
    auto_lang: bool = False


#: Outcome of an attempt that failed while job audio was already audible:
#: the walk stops honestly — no automatic retry, no cross-fallback (VS4.3).
PARTIAL_UNCERTAIN = "partial-uncertain"
#: The exact VS4.4 attempt-log outcome vocabulary — nothing else is a
#: recordable outcome.
ATTEMPT_OUTCOMES = frozenset(
    {"ok", "retryable-fail", "skip", "cancelled", PARTIAL_UNCERTAIN}
)


@dataclass(frozen=True)
class AttemptRecord:
    """One attempt's log entry — exactly the VS4.4 schema."""

    provider: str
    outcome: str  # ok | retryable-fail | skip | cancelled | partial-uncertain
    ts: float


class AttemptLog:
    """Accumulates :class:`AttemptRecord` entries (VS4.4 schema, built now)."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self.records: List[AttemptRecord] = []

    def record(self, provider: str, outcome: str) -> AttemptRecord:
        if outcome not in ATTEMPT_OUTCOMES:
            raise ValueError(f"unknown attempt outcome: {outcome!r}")
        entry = AttemptRecord(provider=provider, outcome=outcome, ts=self._clock())
        self.records.append(entry)
        return entry

    def counts(self) -> Dict[str, int]:
        """Outcome → number of records (diagnostics/tests)."""
        counts: Dict[str, int] = {}
        for entry in self.records:
            counts[entry.outcome] = counts.get(entry.outcome, 0) + 1
        return counts


@dataclass
class WalkResult:
    """Outcome of one fallback walk."""

    audio: Any = b""
    ok: bool = False
    cancelled: bool = False
    exhausted: bool = False
    attempts: List[AttemptRecord] = field(default_factory=list)
    failure_reason: str = ""
    #: True when the walk stopped because a synthesis failed while job
    #: audio was already audible (VS4.3): the pending text was NOT
    #: synthesized and only a deliberate replay intent can recover it.
    partial_uncertain: bool = False


class AudibilityProbe:
    """Job-scoped audibility accounting for the audible-partial rules (VS4.3).

    The pipelined orchestration creates ONE probe per playback job and
    marks it at the exact points where produced bytes are committed to
    the playback session (the played-group discipline: the first
    segment preload and every successful live append) — this is the
    single audibility truth for the job, never a parallel accounting.
    The fallback walker consults the probe after every synthesis
    failure: audio already audible + failure ⇒ the walk stops in the
    ``partial/uncertain`` state (no automatic retry, no cross-fallback
    in any mode). The way out is a DELIBERATE replay intent started by
    the caller: the replay re-reads from the start of the failed group,
    so audio that was already heard MAY BE DUPLICATED — that is the
    honest behavior; a fake "exact resume" is not possible once bytes
    reached the speaker.

    The probe also carries the job's evidence: ``walks`` keeps every
    completed walk's :class:`WalkResult` (its ``attempts`` list is the
    per-intent attempt log, VS4.4) and ``partial_uncertain`` flips the
    moment any intent stops in the partial/uncertain state.
    """

    def __init__(self) -> None:
        self._audible = False
        self.partial_uncertain = False
        self.walks: List[WalkResult] = []

    def mark_audible(self) -> None:
        """Marks that synthesized bytes were committed to the playback
        session: they are (or inevitably will be) heard and can never be
        un-heard."""
        self._audible = True

    def audible(self) -> bool:
        """True once any bytes of this job were committed to playback."""
        return self._audible

    def __call__(self) -> bool:
        # Callable form: the walker accepts any ``audibility() -> bool``.
        return self._audible

    def note_partial_uncertain(self) -> None:
        """Records that the job stopped in the partial/uncertain state."""
        self.partial_uncertain = True

    def record_walk(self, result: WalkResult) -> None:
        """Keeps one walk result per synthesis intent (evidence retrieval)."""
        self.walks.append(result)


def _build_link_engine(
    factory: Callable[..., TTSProvider],
    intent: SynthesisIntent,
    provider_name: str,
    log: AttemptLog,
) -> Optional[TTSProvider]:
    """Builds one link's provider with the job's provider options.

    A construction failure (e.g. an optional dependency is missing)
    means the link is not configured: recorded as a skip, never a
    submit.
    """
    try:
        return factory(
            provider_name=provider_name,
            openai_key=intent.openai_key,
            openai_base_url=intent.openai_base_url,
            openai_model=intent.openai_model,
            eleven_key=intent.eleven_key,
            eleven_model=intent.eleven_model,
            piper_model=intent.piper_model,
        )
    except Exception as e:
        print(
            f"Fallback: provider '{provider_name}' could not be built ({e}); skipping link",
            file=sys.stderr,
        )
        log.record(provider_name, "skip")
        return None


# Actions returned by the retryable-failure step.
_RESUBMIT = "resubmit"
_NEXT_LINK = "next-link"
_CANCELLED = "cancelled"
_EXHAUSTED = "exhausted"


async def run_fallback_synthesis(
    single_attempt: Callable[[SynthesisIntent, FallbackLink, TTSProvider], Awaitable[Any]],
    intent: SynthesisIntent,
    config: FallbackConfig,
    primary_engine: Optional[TTSProvider] = None,
    engine_factory: Optional[Callable[..., TTSProvider]] = None,
    stop_checker: Optional[Callable[[], bool]] = None,
    sleeper: Optional[Callable[[float], Awaitable[None]]] = None,
    clock: Optional[Callable[[], float]] = None,
    audibility: Optional[Callable[[], bool]] = None,
) -> WalkResult:
    """Walks the fallback chain around the existing single-attempt path.

    ``single_attempt(intent, link, engine)`` runs ONE synthesis through
    the exact historical pipeline (cli._synthesize_single) with the
    link's provider+voice; this walker owns stop checks, key checks,
    classification, budget, backoff and the attempt log.

    Budget: one shared counter — at most FALLBACK_LINK_ATTEMPTS submits
    per link and FALLBACK_TOTAL_ATTEMPTS submits for the whole walk,
    counting together the same-provider in-link retries and the
    cross-provider attempts.

    Audibility (VS4.3): ``audibility()`` reports whether ANY bytes of
    the playback job this intent belongs to already became audible.
    After a synthesis failure, an audible job stops the walk in the
    ``partial/uncertain`` state: the failed attempt is logged as
    ``partial-uncertain``, zero further submits happen (no in-link
    retry, no next link), and ``WalkResult.partial_uncertain`` is set.
    The recovery contract is a DELIBERATE replay: the caller starts a
    NEW synthesis intent for the pending text, which re-reads from the
    start of the failed group — audio already heard may be duplicated,
    and that is acknowledged in the state rather than hidden behind a
    fake "exact resume". A job that owns no audio (file/no-play/batch)
    passes no probe and keeps full-restart fallback semantics.
    """
    factory = engine_factory if engine_factory is not None else get_provider
    sleep_async = sleeper if sleeper is not None else asyncio.sleep
    log = AttemptLog(clock=clock if clock is not None else time.time)

    links: List[FallbackLink] = [
        FallbackLink(provider=intent.provider, voice=intent.voice, notes="primary request")
    ]
    links.extend(config.chain)

    state = {"charged_total": 0, "retry_transitions": 0}

    def _backoff_delay() -> float:
        # Fixed schedule (2 s, then 4 s), saturating at the last value.
        index = min(state["retry_transitions"], len(FALLBACK_BACKOFF_SCHEDULE_SEC) - 1)
        return FALLBACK_BACKOFF_SCHEDULE_SEC[index]

    def _cancelled() -> WalkResult:
        return WalkResult(
            audio=b"",
            ok=False,
            cancelled=True,
            exhausted=False,
            attempts=log.records,
            failure_reason="cancelled",
        )

    def _exhausted() -> WalkResult:
        return WalkResult(
            audio=b"",
            ok=False,
            cancelled=False,
            exhausted=True,
            attempts=log.records,
            failure_reason=(
                f"all links failed or were skipped after {state['charged_total']} attempt(s)"
            ),
        )

    def _audible_partial(link: FallbackLink) -> Optional[WalkResult]:
        """VS4.3 stop: a failure while job audio is already audible.

        Automatic machinery can no longer know exactly what the user
        heard, so it must stop: no automatic retry, no cross-fallback,
        no fake "exact resume". Returns the partial/uncertain result, or
        None when nothing was audible (the walk continues normally).
        """
        if audibility is None or not audibility():
            return None
        print(
            f"Fallback: provider '{link.provider}' failed with audio already "
            "audible; stopping in partial/uncertain state (no automatic "
            "retry, no cross-fallback; deliberate replay may duplicate)",
            file=sys.stderr,
        )
        log.record(link.provider, PARTIAL_UNCERTAIN)
        return WalkResult(
            audio=b"",
            ok=False,
            cancelled=False,
            exhausted=False,
            attempts=log.records,
            failure_reason=(
                "partial-uncertain: synthesis failed after audio was already "
                "audible; deliberate replay from the failed group start is "
                "required (duplicates possible)"
            ),
            partial_uncertain=True,
        )

    def _after_retryable(link: FallbackLink, link_submits: int) -> str:
        """Records one retryable failure and decides the next action.

        The shared budget is charged here: same-provider in-link retries
        and cross-provider attempts draw on the same TOTAL counter. A
        cancelled job never resubmits (and never falls back).
        """
        log.record(link.provider, "retryable-fail")
        state["charged_total"] += 1
        if state["charged_total"] >= FALLBACK_TOTAL_ATTEMPTS:
            return _EXHAUSTED
        if stop_checker is not None and stop_checker():
            log.record(link.provider, "cancelled")
            return _CANCELLED
        if link_submits >= FALLBACK_LINK_ATTEMPTS:
            return _NEXT_LINK
        return _RESUBMIT

    first_link = True
    for link in links:
        link_engine: Optional[TTSProvider] = None
        link_submits = 0
        while True:
            # (1) stop check before EVERY attempt, including between
            # links and after backoff sleeps.
            if stop_checker is not None and stop_checker():
                log.record(link.provider, "cancelled")
                return _cancelled()

            # (5) key check before ANY submit: a link without a key is
            # not-configured, an honest skip.
            if link_key_missing(link.provider, intent):
                print(
                    f"Fallback: provider '{link.provider}' has no API key configured; skipping link",
                    file=sys.stderr,
                )
                log.record(link.provider, "skip")
                break

            if link_engine is None:
                if first_link and primary_engine is not None:
                    # The orchestration's already-built engine (the
                    # daemon's warm instance) serves the primary link.
                    link_engine = primary_engine
                else:
                    link_engine = _build_link_engine(factory, intent, link.provider, log)
                    if link_engine is None:
                        break  # build failure already recorded as skip

            # (2) the attempt itself: the existing single-attempt path
            # (for elevenlabs/openai submissions this includes the
            # providers' own stream→batch same-provider retry).
            link_submits += 1
            try:
                result = await single_attempt(intent, link, link_engine)
            except Exception as e:
                disposition = classify_exception(e)
                print(
                    f"Fallback: provider '{link.provider}' failed ({disposition}): {e}",
                    file=sys.stderr,
                )
                # VS4.3: audible job + failure ⇒ honest stop before any
                # retry or fallback decision.
                stopped = _audible_partial(link)
                if stopped is not None:
                    return stopped
                if disposition != RETRYABLE:
                    # Locked skip-link classes: advance immediately, no
                    # in-link retry, no backoff.
                    log.record(link.provider, "skip")
                    break
                action = _after_retryable(link, link_submits)
            else:
                if result:
                    log.record(link.provider, "ok")
                    return WalkResult(
                        audio=result,
                        ok=True,
                        cancelled=False,
                        exhausted=False,
                        attempts=log.records,
                    )
                # Empty audio. If the job was stopped, the providers'
                # b""-on-cancel semantics apply: never fall back.
                if stop_checker is not None and stop_checker():
                    log.record(link.provider, "cancelled")
                    return _cancelled()
                # The provider already exhausted its own same-provider
                # options inside this submit (locked elevenlabs/openai
                # stream→batch contract): an empty result is retryable.
                print(f"Fallback: provider '{link.provider}' returned no audio", file=sys.stderr)
                # VS4.3: audible job + failure ⇒ honest stop before any
                # retry or fallback decision.
                stopped = _audible_partial(link)
                if stopped is not None:
                    return stopped
                action = _after_retryable(link, link_submits)

            if action == _EXHAUSTED:
                return _exhausted()
            if action == _CANCELLED:
                return _cancelled()
            if action == _NEXT_LINK:
                break  # link budget exhausted → next link (no backoff)
            # Resubmit the same link: wait out the fixed backoff first.
            await sleep_async(_backoff_delay())
            state["retry_transitions"] += 1
            continue
        first_link = False

    return _exhausted()
