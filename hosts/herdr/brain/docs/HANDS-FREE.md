# Conversational voice call

Conversational endpointing now uses **800 ms of silence** by default in both
the browser transcript endpointer and the local-engine RMS VAD. Their existing
`silenceMs` option supports tuning, including 700–900 ms conversational values.
Explicit legacy thresholds remain supported.

The default utterance hard cap remains 15 seconds. The existing `hardCapMs`
option can increase it, but never beyond the **60-second safety ceiling**.
Silence before the first speech frame does not consume this budget.

## Autonomous audio proof

See [`tests/e2e/fixtures/README.md`](../tests/e2e/fixtures/README.md) for the
Chromium fake-device harness and its native input/output checks. Synthetic
capture is not proof of speech-recognition quality or physical-device echo
cancellation. The hands-free implementation logbook tracks the remaining call,
approval and desktop work separately.
