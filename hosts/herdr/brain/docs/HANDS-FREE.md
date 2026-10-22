# Conversational voice call

## Announcements and executive progress

During a call, SSE announcements remain in the existing visual ledger and audio
queue until the conversation is idle. Recording, buffered browser speech,
transcription, a pending answer or speech segment, playback, paused calls and
approval confirmation take priority. The queue can play an answer ahead of a
deferred announcement; natural completion or stopping the answer releases the
next announcement only when the idle-state guard permits it. Out-of-call mute,
autoplay blocking and gesture-unlock behavior is unchanged.

The provider's voice policy requests at most three short sentences: problem
impact, verified test outcome, a blocking decision when relevant, and an
explicit recommended next action. Verbatim terminal choices must not be hidden
to shorten a response. Tests prove this policy reaches the provider; they do
not claim that every live model response will obey it.

## Endpointing

Conversational endpointing now uses **800 ms of silence** by default in both
the browser transcript endpointer and the local-engine RMS VAD. Their existing
`silenceMs` option supports tuning, including 700–900 ms conversational values.
Explicit legacy thresholds remain supported.

The default utterance hard cap remains 15 seconds. The existing `hardCapMs`
option can increase it, but never beyond the **60-second safety ceiling**.
Silence before the first speech frame does not consume this budget.

## Barge-in (interrupting Panel by voice)

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

## Spoken terminal dialogs (S8)

When the selected agent is **blocked** at a question tool, every `/ask`
turn's live context receives a deterministic speakable line (question +
every numbered option label, verbatim from `parse_dialog` — never
paraphrased, never with an option dropped or invented) plus the
instruction to read it aloud and wait. A leading selection marker
(`❯ 1.`) on an option is presentation, not content: it is stripped from
the spoken label and recorded as the dialog's visible selection instead,
so marked menus neither lose the selected option nor read the marker
aloud. The line is repeated only when the
dialog is new or has changed; the same waiting question is not re-read on
every turn. Option descriptions stay unspoken (concise by design; the
labels carry the choice).

## Voice answering of terminal dialogs (S9)

A spoken choice ("dile que sí", "selecciona la opción 1") rides the normal
`/ask` pipeline — no second transport, no client-side key dispatch. The
model forwards the user's words to the `answer_dialog` tool, but
**authorization is bound to the actual user turn**: the dispatch layer
hands the tool the /ask turn's real text, and the choice is resolved from
THAT — a model paraphrase or fabricated argument can never press anything
the user did not say. The mapping is otherwise deterministic and
server-side:

1. the pane's dialog must have been **explained in an earlier turn** (the
   user's words must postdate the explanation — a choice arriving in the
   same turn a dialog first appeared is refused);
2. the dialog **currently on the pane** must be the one that was spoken —
   the fingerprint covers the question, every option label **and its
   description** (descriptions are meaning-bearing: a changed destination
   or scope is a different action), with only whitespace normalized;
   changed or vanished dialogs refuse, and a mid-turn swap re-arms the
   explanation before any key may move;
3. **no keys may have been sent** to the pane since the explanation — the
   manual keypad keeps working exactly as before, but after any key the
   base position is unknown and voice answering refuses;
4. the dialog's **selection must be visible** — a `❯`-style marker or the
   single ANSI-backgrounded option line. Keys move `up`/`down` from the
   ACTUAL highlighted option to the requested one, then `enter`
   (allowlisted and budget-capped). With no visible highlight the tool
   **refuses**: guessing an initial position could confirm a different
   action, and that is never acceptable;
5. the user's words must name **exactly one unambiguous, un-negated
   choice**: negations ("no selecciones la opción 1"), multiple numbers
   ("opción 1 o 2"), self-corrections ("selecciona 2, no, mejor 1"),
   duplicated option numbers in the dialog and unknown numbers all
   refuse.

Every refusal presses nothing and tells the user why.

## Spoken approval binding (S14)

**Intentional contract tightening.** «Adelante» authorizes *only the exact
action Panel just explained*, and the proof must be verifiable
server-side. There is **no bypass**: the reconfirm shortcut was removed
after independent verification showed a failed restate (or another
client) could inherit authority.

- The `/ask` turn that opens a gate records **which speech job** carried
  the explanation, paired with **the gate revision** it explained. A
  spoken approve must present that exact pair, the job must be
  **complete**, and — for segmented delivery — **every segment consumed**
  (the client's ack watermark reached the final seq, so the whole
  explanation was played, not a first sentence).
- The client presents the receipt only after real completion: a
  **full-file** item's own native `ended` event, or the segment player's
  **final completion status**. Barge-in flushes, play failures, held or
  interrupted remainders, momentarily empty queues and missing audio
  never promote spoken authority.
- A **revised** gate (PATCH) is a different action: the revision voids
  the receipt, and the bind itself states its revision, so a PATCH racing
  the /ask response or the restate leaves the gate unbound rather than
  mis-bound.
- An unproven approve word never executes: the endpoint answers
  **`clarify`**, restating the exact current frozen action. Only when
  that restate **really rendered** does it carry `explain_speech_id` — a
  fresh speech identity bound to this revision — so the confirm spoken
  **after the client played the restate** is provable. A silent clarify
  (TTS failure) binds nothing: confirming again just clarifies again.
- The proven (speech id, revision) pair is re-validated **atomically with
  the approve transition** under the store's lock; a PATCH interleaved
  between validation and execution yields a still-proposed gate and a
  fresh clarify. Replay always uses the frozen approved snapshot, and no
  lock is held over LLM/TTS work.
- Legacy voice approves with no receipt used to execute; they now clarify
  first. **Manual** approve/reject/edit (buttons, ntfy actions) are
  unchanged explicit direct authorization, as are reject and re-dictation
  by voice.

The gate card, countdown, manual keypad and M1 transport cancellation are
untouched.

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
