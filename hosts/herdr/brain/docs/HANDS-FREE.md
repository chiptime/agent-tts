# Conversational voice call

Conversational endpointing now uses **800 ms of silence** by default in both
the browser transcript endpointer and the local-engine RMS VAD. Their existing
`silenceMs` option supports tuning, including 700–900 ms conversational values.
Explicit legacy thresholds remain supported.

The default utterance hard cap remains 15 seconds. The existing `hardCapMs`
option can increase it, but never beyond the **60-second safety ceiling**.
Silence before the first speech frame does not consume this budget.

## Barge-in (interrupting Collie by voice)

When the **server voice engine** plays an answer in-call, a lightweight VAD
monitor keeps reading the capture analyser — but **only when the real
MediaStream track reports echo cancellation enabled**. With AEC, the browser
strips the agent's own speaker output from the mic signal, so a speech onset
during playback is the user's voice.

On detection the client, in one tick:

1. **pauses the player** (within 100 ms of the detected frame — the pause runs
   synchronously in the 50 ms monitor tick),
2. **aborts the in-flight `/ask` fetch** through its per-turn
   `AbortController` (a deliberate abort is silent; no error banner),
3. **cancels the server speech job** (`POST /speech/{id}/cancel`, token-bound)
   and flushes only that job's queued audio — ambient announcements keep
   their turn and their records,
4. **recovers the mic** under the gate-aware base state, so the interrupting
   utterance is captured and `recording` follows speech onset.

A barge-in **never approves, rejects or dismisses an approval gate** (S14):
the card, the countdown and the `confirming` routing survive it. Without an
AEC report the call stays **safe half-duplex**: the mic is down during
playback and nothing fires.

Honest limits: the fake-device test flags prove the detection→pause→cancel
→recapture plumbing with native capture and playback, not acoustic echo
suppression quality, and not speech-recognition accuracy.

## Server-side disconnect cancellation (`/ask`)

`POST /ask` runs its whole turn in an executor while the request watches for
`http.disconnect`. When the client dies mid-turn the server stops the actual
work: the turn's provider rounds run through an **owned AsyncOpenAI channel**
(a private event loop and a per-turn client, never the shared sync client),
and firing the disconnect event **cancels the in-flight HTTP task itself** —
httpx aborts the active request — then closes that client's transport. The
speech job dies through the same token-verdict cancel path an explicit
`/speech/{id}/cancel` uses, so renders and child processes observe the same
abort. The loop's checks before every round, tool dispatch and memory write
mean an abandoned turn can never open a gate, run a further tool or land in
the ring; the unreadable response is closed with status 499. Concurrent
turns each own their channel, so cancelling one leaves connected requests
untouched.

What cancellation intentionally does **not** do:

- A `/speech/{id}/next` disconnect cancels nothing — only the explicit cancel
  endpoint may end a job (the lazy `expired-unconsumed` sweep reclaims
  vanished consumers).
- A connection closing **after** `/ask` returned never touches the
  conversation-mode remainder producer; it runs to completion in its own
  thread.
- An explicit `/speech/{id}/cancel` on a still-connected request never
  aborts the provider transport: the channel reacts only to the
  request-scoped disconnect event, so the full textual answer survives.
- The HTTP generation is aborted, but a **tool already dispatched** between
  rounds (a herdr CLI subprocess) still runs to its own completion — the
  loop checks only prevent any further work afterward. Injected
  synchronous test fakes that ignore cancellation cannot be killed in
  Python; the production client is the cancellable path.

## Autonomous audio proof

See [`tests/e2e/fixtures/README.md`](../tests/e2e/fixtures/README.md) for the
Chromium fake-device harness and its native input/output checks. Synthetic
capture is not proof of speech-recognition quality or physical-device echo
cancellation. The hands-free implementation logbook tracks the remaining call,
approval and desktop work separately.
