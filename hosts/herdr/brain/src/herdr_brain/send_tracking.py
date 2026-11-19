"""Send tracking (parity-f7g): every pane send reaches a visible outcome.

A receipt's outcome says what the TERMINAL did with the text at send
time; it says nothing about the turn that followed. This module closes
that gap: one bounded, in-memory tracker per receipt, driven by the
watcher's EXISTING poll (never a second herdr timer) plus — for
harnesses that keep a local store, opencode first — a per-harness
signals seam (S2).

State machine (S1/S3)::

    sent ──(pane seen working)──► started ──(sustained)──► working
      │                                                            │
      └──────────── one terminal: done | blocked | error ──────────┘
         no_start (15 s without work/completion)   pane_gone (agent
         stalled  (flag, opencode only: 3 min       vanished)
         working without store activity; clears
         on activity)
         superseded (a newer send to the pane)
         expired    (the 30 min hard cap — the chosen
                     terminal word for S4's "hard cap")

An ``effective`` receipt (the turn completed inside the 3 s send
window) is BORN terminal: state ``done``.

Push rules (S7): ``no_start``, ``stalled`` episodes and store-sourced
errors (opencode errors read while Herdr still says ``working``, S6c)
dispatch a WebPush through the coordinator's tracked-send slot. Herdr
-derived errors, ``done`` and ``blocked`` never push from here — the
watcher's announcement (SSE + audio + herd push) already covers them;
the tracker never double-notifies.

Clocks: the tracker lives in the WALL-clock domain (``time.time``), the
only domain comparable with the opencode store's epoch stamps and the
receipt store's ``created_at``. The watcher keeps its own monotonic
clock; it only forwards agent observations.

Completion evidence (S1 correction): a status the pane ALREADY had
when the send started is not completion — Herdr keeps ``done`` until
the pane is viewed, so a pane that never picked the send up still
reports its pre-existing ``done``. The pane's status is pinned at the
tracker's first observation; ``done`` requires work seen after the
send, a status the pane REACHED after leaving its start status, or
harness evidence (``assistant_after``, S2).

Transport (S4): every state change publishes ONE SSE event
(:data:`SSE_EVENT_TYPE`) on the announcement hub and ``GET
/pane/receipts`` items gain a ``track`` object — additively: receipts
without trackers serialize exactly as before. Every payload and the
snapshot carry ``seq``, a per-receipt monotonically increasing counter
bumped on each state/flag change. Events are computed under the
registry lock in seq order but delivered through a single emitter
condition (:meth:`SendTracker._publish_ordered`) so a later seq is
never published before an earlier one — while the registry lock itself
is never held during a publish. Consumers ignore updates whose seq is
lower than the one they already hold for the receipt. Error text is
masked before it leaves this module.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Dict, Mapping, Optional

from .secrets_mask import mask_text
from .watcher import agent_label

#: Receipt outcomes that start a tracker (S1). ``refused`` wrote
#: nothing, ``typed_not_submitted``/``discarded`` never left the input
#: line — nothing is in flight for any of them.
TRACKED_OUTCOMES = ("sent", "effective", "unknown")

#: The outcome that already saw the turn complete inside the send
#: window: its tracker is born terminal (S1).
EFFECTIVE_OUTCOME = "effective"

#: The closed terminal set. ``expired`` is the chosen word for S4's
#: hard-cap end (see :data:`TRACK_CAP_S`).
TERMINAL_STATES = frozenset({
    "done",
    "blocked",
    "error",
    "no_start",
    "pane_gone",
    "superseded",
    "expired",
})

# ── S3 thresholds (accepted defaults, L2) ─────────────────────────────────────

#: A send whose pane was never seen working/blocked and shows no
#: completion evidence after this many seconds never started.
NO_START_S = 15.0

#: opencode only: a pane that keeps claiming ``working`` with no new
#: store activity for this long is flagged ``stalled`` (non-terminal;
#: the flag clears when activity resumes).
STALLED_S = 180.0

#: Hard cap (S4): whatever the state, a tracker ends ``expired`` after
#: 30 minutes. Trackers never leak.
TRACK_CAP_S = 1800.0

#: How long a computed event waits for its predecessor's DELIVERY
#: before publishing anyway (S4 correction): liveness over ordering
#: after a grace period — a publish callback stuck forever must not
#: freeze the receipt's whole emission chain.
EMIT_WAIT_S = 5.0

#: Defensive bound for the registry itself. One tracker per receipt and
#: the receipt store already caps at 64; this catches orphans whose
#: receipt was evicted (oldest dropped first).
MAX_TRACKERS = 64

#: The ONE SSE event type every tracker state change rides (S4).
SSE_EVENT_TYPE = "send.track"


class _Track:
    """One in-flight (or retained-terminal) send tracker."""

    __slots__ = (
        "receipt_id", "pane_id", "state", "sent_at", "last_activity_at",
        "stalled", "error", "ever_working", "label", "seq", "start_status",
    )

    def __init__(self, receipt_id: str, pane_id: str, state: str, sent_at: float):
        self.receipt_id = receipt_id
        self.pane_id = pane_id
        self.state = state
        self.sent_at = sent_at
        self.last_activity_at: Optional[float] = None
        self.stalled = False
        self.error: Optional[dict] = None
        self.ever_working = False
        self.label = pane_id  # replaced by the speakable agent label
        # Monotonic per-receipt counter, bumped on every state/flag
        # change (S4 correction): consumers drop lower seqs.
        self.seq = 0
        # The pane's status pinned at the FIRST observation after the
        # send (S1 correction): a status equal to it is not completion
        # evidence — Herdr keeps ``done`` until the pane is viewed.
        self.start_status: Optional[str] = None


class SendTracker:
    """Bounded registry of send trackers, keyed by receipt id.

    Thread-safe by one lock: :meth:`start` runs on HTTP threads,
    :meth:`observe` on the watcher's poll thread. Events are COMPUTED
    under that lock (in seq order) and delivered through a single
    emitter condition (:meth:`_publish_ordered`) that re-establishes
    the order at delivery time — the registry lock is never held
    during a publish. Hooks (``publish``, ``notify``) can never break
    the state machine — a raising hook is swallowed like the watcher
    swallows a raising notifier.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.time,
        publish: Optional[Callable[[dict], None]] = None,
        notify: Optional[Callable] = None,
        harness_signals: Optional[Callable[[str], object]] = None,
    ):
        self._clock = clock
        self._publish = publish
        self._notify = notify
        self._signals_for = harness_signals or default_harness_signals
        self._lock = threading.Lock()
        self._tracks: Dict[str, _Track] = {}  # receipt_id -> track
        self._by_pane: Dict[str, str] = {}  # pane_id -> newest receipt id
        # Delivery ledger (S4 correction): receipt_id -> last delivered
        # seq. Guarded by its own condition, never by the registry
        # lock, so a blocking publish parks the chain without parking
        # the state machine.
        self._order = threading.Condition()
        self._delivered: Dict[str, int] = {}

    # -- lifecycle ------------------------------------------------------------

    def start(
        self,
        receipt_id: str,
        pane_id: str,
        outcome: str,
        sent_at: Optional[float] = None,
        pane_status: Optional[str] = None,
    ) -> dict:
        """One tracker per receipt; a newer send to the pane supersedes
        the older non-terminal one. Returns the initial snapshot.

        ``pane_status`` is the pane's last-known status AT send time
        (the watcher's baseline) when the caller has it: the S1
        evidence rule then measures transitions from the status the
        send actually interrupted. Without it the tracker pins the
        pane's status at its first observation instead.
        """
        now = self._clock()
        evicted: list = []
        with self._lock:
            events = []
            previous_id = self._by_pane.get(pane_id)
            if previous_id is not None and previous_id != receipt_id:
                previous = self._tracks.get(previous_id)
                if previous is not None and previous.state not in TERMINAL_STATES:
                    previous.state = "superseded"
                    events.append((self._event(previous, now), None))
            state = "done" if outcome == EFFECTIVE_OUTCOME else "sent"
            track = _Track(
                receipt_id, pane_id, state,
                float(now if sent_at is None else sent_at),
            )
            if pane_status:
                track.start_status = pane_status
            self._tracks[receipt_id] = track
            self._by_pane[pane_id] = receipt_id
            evicted = self._evict_locked()
            events.append((self._event(track, now), None))
        self._prune_delivered(evicted)
        self._emit(events)
        return self._snapshot_unlocked(track)

    def attach_event_sink(self, publish: Callable[[dict], None]) -> None:
        """Wires (or replaces) the SSE sink — the same attach-after-
        construction seam the consult service uses."""
        self._publish = publish

    def snapshot(self, receipt_id: str) -> Optional[dict]:
        """The tracker's current view (state, elapsed, activity, error),
        or None when no tracker exists for the receipt."""
        with self._lock:
            track = self._tracks.get(receipt_id)
            if track is None:
                return None
            return self._snapshot_unlocked(track)

    def has_active(self) -> bool:
        """Whether any tracker is still non-terminal (cadence signal)."""
        with self._lock:
            return any(
                t.state not in TERMINAL_STATES for t in self._tracks.values()
            )

    def active_panes(self) -> frozenset:
        """Panes with a non-terminal tracker right now (S6b evidence)."""
        with self._lock:
            return frozenset(
                t.pane_id for t in self._tracks.values()
                if t.state not in TERMINAL_STATES
            )

    # -- the watcher feed ------------------------------------------------------

    def observe(
        self,
        current: Mapping[str, object],
        errors: Mapping[str, dict],
    ) -> None:
        """One watcher poll: panes with their (derived) statuses.

        ``current`` maps pane_id → AgentInfo (status ``error`` rows are
        the watcher's derived ones, with the matching entry in
        ``errors``); panes missing from it have vanished.
        """
        now = self._clock()
        events = []
        with self._lock:
            for track in list(self._tracks.values()):
                if track.state in TERMINAL_STATES:
                    continue
                self._step(track, current, errors, now, events)
        self._emit(events)

    # -- internals -------------------------------------------------------------

    def _step(self, track, current, errors, now, events) -> None:
        agent = current.get(track.pane_id)
        if agent is None:
            track.state = "pane_gone"
            events.append((self._event(track, now), None))
            return
        try:
            track.label = agent_label(agent)
        except Exception:  # noqa: BLE001 — a label never breaks tracking
            pass

        read = self._read_signals(agent, track.sent_at)
        completed = bool(read is not None and read.get("assistant_after"))

        if read is not None:
            activity = read.get("activity_at")
            if (
                isinstance(activity, (int, float))
                and not isinstance(activity, bool)
                and activity > (track.last_activity_at or float("-inf"))
            ):
                track.last_activity_at = float(activity)
            error = read.get("error")
            if error:
                # S6c/S2: the store's error row counts EVEN while Herdr
                # still says working — and nothing else announces it, so
                # this is the one error path that pushes (S7).
                track.state = "error"
                track.error = _masked_error(error)
                events.append((
                    self._event(track, now),
                    (track.pane_id, track.label, "error", dict(track.error)),
                ))
                return

        status = getattr(agent, "status", "")
        # S1 correction: pin the pane's status at the FIRST observation.
        # A status equal to it later is something the pane already had
        # when the send started — not completion evidence.
        if track.start_status is None:
            track.start_status = status

        if status == "error":
            track.state = "error"
            track.error = _masked_error(errors.get(track.pane_id) or {})
            # Herdr-derived: the watcher's announcement (SSE + audio +
            # herd push) already covers it — no tracker push (S7).
            events.append((self._event(track, now), None))
            return
        if status == "blocked":
            track.state = "blocked"
            events.append((self._event(track, now), None))
            return
        if status in ("done", "idle"):
            # Completion evidence, one of three (S1 correction): work
            # seen after the send; a status the pane REACHED after
            # leaving its pinned start status (a fast turn can skip
            # every working poll); or harness evidence (S2). A pane
            # still sitting at its pre-existing done/idle falls through
            # to the thresholds — no_start surfaces at 15 s.
            left_start = status != track.start_status
            if track.ever_working or left_start or completed:
                track.state = "done"
                events.append((self._event(track, now), None))
                return
        elif status == "working":
            track.ever_working = True
            if track.state == "sent":
                track.state = "started"
                events.append((self._event(track, now), None))
            elif track.state == "started":
                track.state = "working"
                events.append((self._event(track, now), None))

        # Thresholds (only reachable while still non-terminal).
        elapsed = now - track.sent_at
        if elapsed >= TRACK_CAP_S:
            track.state = "expired"
            events.append((self._event(track, now), None))
            return
        if not track.ever_working and elapsed >= NO_START_S and not completed:
            track.state = "no_start"
            events.append((
                self._event(track, now),
                (track.pane_id, track.label, "no_start", None),
            ))
            return
        if read is not None and track.state in ("started", "working"):
            self._step_stalled(track, now, events)

    def _step_stalled(self, track, now, events) -> None:
        """S3: opencode only — 3 min working without new store activity."""
        effective_activity = (
            track.last_activity_at if track.last_activity_at is not None
            else track.sent_at
        )
        quiet_s = max(0.0, now - effective_activity)
        if quiet_s >= STALLED_S and not track.stalled:
            track.stalled = True
            events.append((
                self._event(track, now),
                (track.pane_id, track.label, "stalled", None),
            ))
        elif track.stalled and quiet_s < STALLED_S:
            track.stalled = False
            events.append((self._event(track, now), None))

    def _read_signals(self, agent, since_s: float) -> Optional[dict]:
        """One harness-seam read; any failure reads as no signals."""
        try:
            signals = self._signals_for(getattr(agent, "agent", ""))
        except Exception:  # noqa: BLE001 — the seam never breaks tracking
            return None
        if signals is None:
            return None
        try:
            return signals.read(getattr(agent, "session_value", ""), since_s)
        except Exception:  # noqa: BLE001
            return None

    def _evict_locked(self) -> list:
        evicted_ids: list = []
        while len(self._tracks) > MAX_TRACKERS:
            oldest = next(iter(self._tracks))
            evicted = self._tracks.pop(oldest)
            evicted_ids.append(oldest)
            if self._by_pane.get(evicted.pane_id) == oldest:
                del self._by_pane[evicted.pane_id]
        return evicted_ids

    def _prune_delivered(self, receipt_ids: list) -> None:
        """Drops the delivery ledger of evicted receipts (bounded
        memory). Called with the registry lock RELEASED — the emitter
        condition is always the innermost lock."""
        if not receipt_ids:
            return
        with self._order:
            for receipt_id in receipt_ids:
                self._delivered.pop(receipt_id, None)

    def _snapshot_unlocked(self, track: _Track) -> dict:
        now = self._clock()
        snap = {
            "state": track.state,
            "seq": track.seq,
            "sent_at": track.sent_at,
            "elapsed_s": round(max(0.0, now - track.sent_at), 1),
            "stalled": track.stalled,
        }
        if track.last_activity_at is not None:
            snap["last_activity_at"] = track.last_activity_at
        if track.error is not None:
            snap["error"] = dict(track.error)
        return snap

    def _event(self, track: _Track, now: float) -> dict:
        # Every event IS a state/flag change: bumping here is exactly
        # the monotonic seq contract (S4 correction).
        track.seq += 1
        event = {
            "type": SSE_EVENT_TYPE,
            "receipt_id": track.receipt_id,
            "pane_id": track.pane_id,
            "seq": track.seq,
            "state": track.state,
            "elapsed_s": round(max(0.0, now - track.sent_at), 1),
        }
        if track.last_activity_at is not None:
            event["last_activity_at"] = track.last_activity_at
        if track.stalled:
            event["stalled"] = True
        if track.error is not None:
            event["error"] = dict(track.error)
        return event

    def _emit(self, events) -> None:
        for event, push in events:
            self._publish_ordered(event)
            if push is not None and self._notify is not None:
                pane_id, label, state, error = push
                try:
                    self._notify(pane_id, label, state, error=error)
                except Exception:  # noqa: BLE001 — push is advisory
                    pass

    def _publish_ordered(self, event: dict) -> None:
        """Delivers ONE event so a later seq is never published before
        an earlier one (S4 correction).

        Events leave the registry lock as an ordered list, but two
        threads can still race between lock release and delivery. This
        method re-establishes the order: an event waits (bounded by
        :data:`EMIT_WAIT_S`) until its receipt's predecessor has been
        delivered, then publishes under the condition and advances the
        ledger. The registry lock is never held here, so a blocking
        publish callback parks only the emission chain, never the
        state machine.
        """
        key = event["receipt_id"]
        seq = event["seq"]
        with self._order:
            deadline = time.monotonic() + EMIT_WAIT_S
            while self._delivered.get(key, 0) < seq - 1:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break  # predecessor lost: deliver rather than freeze
                self._order.wait(remaining)
            if self._publish is not None:
                try:
                    self._publish(event)
                except Exception:  # noqa: BLE001 — the hub never breaks tracking
                    pass
            if seq > self._delivered.get(key, 0):
                self._delivered[key] = seq
            self._order.notify_all()


def _masked_error(error: Mapping) -> dict:
    """{name, message} with the message masked BEFORE it leaves (S14)."""
    name = str(error.get("name") or "")
    message = mask_text(str(error.get("message") or ""))
    return {"name": name, "message": message}


# ── the per-harness signals seam (S2) ────────────────────────────────────────


class OpencodeSendSignals:
    """opencode store signals: activity, error-after, completion-after.

    Read-only, never raises, constructed per read so ``OPENCODE_DB``
    changes apply without a restart — the same discipline as the
    transcript connectors.
    """

    kind = "opencode"

    def read(self, session_id: str, since_s: float) -> dict:
        from .transcripts import OpencodeTranscript

        return OpencodeTranscript().read_send_signals(session_id, since_s)


def default_harness_signals(agent_kind: str) -> Optional[object]:
    """The seam: opencode reads its store; every other harness tracks on
    Herdr status alone (S2 — nothing here is opencode-specific)."""
    if (agent_kind or "").strip().lower() == "opencode":
        return OpencodeSendSignals()
    return None
