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

The call uses an adaptive silence window in both the browser transcript
endpointer and the server-engine RMS VAD: it starts at **3.2 seconds**
and grows with the utterance to at most **4.5 seconds** (operator
decision 2026-11-04, "ampliar los tiempos en al menos 2 segundos": while
the Android recognizer restarts, no interim results arrive and that dead
air used to finish the turn mid-speech — "corta instantáneo al hablar").
Longer turns therefore allow longer thinking pauses, at the cost of a
longer wait before a reply. The standalone helpers retain their previous
defaults and explicit options.

The server voice engine rotates its recorder every 45 seconds of continuous
speech, transcribes the segments in order and sends one combined turn after
the user stops. If a segment cannot be transcribed, the incomplete turn is
not sent; the call asks the user to repeat it. An uninterrupted turn has a
10-minute safety limit, which is announced when reached.

When silence closes a phrase, the server engine keeps capturing for a
**1.5-second continuation window** while that phrase is already being
transcribed. If the user speaks again inside the window, the new speech
joins the same turn instead of starting a second one; otherwise the turn is
sent and the quiet captured during the window is discarded. Because the
window overlaps the transcription, it adds little reply latency. A phrase
that transcribes to nothing (a cough or breath before resuming) does not
void the turn; an empty segment cut by the 45-second rotation still does.

The call transcription engine (Settings → "Call transcription":
Server or Browser) is the user's choice on every platform and only affects
how the call hears the user; reading the screen aloud is a separate
feature. Android's Web Speech recognizer plays a system beep on every
start and ends its session at each short pause, and a web page cannot
silence that beep. With the Browser engine on Android, an idle mic is
therefore watched by a silent getUserMedia speech gate, and recognition
starts only when the user begins to speak: the beep marks the start of a
turn (and a session Android ends mid-turn), never an idle loop. The first
syllable can be clipped while the recognizer opens; the Server engine
never beeps. Independent segment
transcriptions can still misrecognize a word at a segment boundary; real
device testing is needed to assess recognition and acoustic performance.

On a real Android device (Pixel, 2026-11) the gate handoff proved
unusable: the recognizer opens late, Android ends its session at each
short pause, and a speaker who keeps talking cycles gate ↔ recognizer
roughly every two seconds — the mic indicator jumps, utterance onsets
are clipped and phrases are lost. Server is therefore the default and
the recommended engine for calls on Android; the Browser engine remains
a manual choice for platforms where Web Speech behaves.

The Browser recognizer listens in the app's language (Settings →
language; Spanish by default), not the phone's system language: an
English phone keeps recognizing a Spanish call as `es-ES`, while a
system already in the same language keeps its region (`es-AR`). When
Android ends a session at a short pause while the utterance is still
open, the recognizer reopens at once instead of waiting through the
error backoff, so the gap loses as little speech as possible.

The idle diagnostics overlay (long-press the call button, ≥800 ms, not
during a call) ends with the **browser-dictation timeline**: the last
recorded actions — gate armed/open/speech, recognizer start, first
result, final, end, restart, dispatch — each stamped with the gap to the
previous action. It records durations only (no transcript text) and
clears at each call start, so a failed call can be read back after
hanging up: where the time went and which action ate the speech.

### Live streaming engine ("Dictado: En vivo", PRD-03)

An opt-in third voice engine (`live` / "Dictado: En vivo") streams continuous PCM16 16 kHz mono frames over WebSocket `/call/stream` from an AudioWorklet:
- **In-memory brain ring buffer**: Audio is buffered in memory (bounded to 120 seconds, dies with the connection). The hub service currently writes each transcription window to a temporary file inside its container and deletes it afterwards. Utterance windows include ~500 ms pre-roll to catch speech onset without clipping.
- **Security & Perimeter**: Protected by Host allowlist, Origin same-origin/allowlist, and Tailscale identity checks. Device token is authenticated via the first WebSocket message fail-closed (preventing credentials in access logs).
- **Graceful Fallback & Input Loss**: If the WebSocket fails or drops, the client automatically falls back to Servidor segment-POST transport with a visible notice without dropping the call. Microphones disconnecting mid-call trigger input loss signaling.
- **One microphone source per call**: the client creates a single `MediaStreamSource` per call and shares it between the level analyser and every uplink arm (AudioWorklet, or the ScriptProcessor fallback), routing the worklet through a zero-gain node to the destination so the graph is always pulled. A second source on the same stream is the working hypothesis for a phone whose analyser heard speech while the uplink streamed silence; this is verified only in desktop Chromium, not on an Android device.

By default, both live and `/transcribe` delegate to llm-hub-whisper. Each live window uses an independent WebSocket; the brain loads no Whisper, Silero or Smart Turn models. Configure the service endpoints with `HERDR_BRAIN_WHISPER_WS_URL` and `HERDR_BRAIN_WHISPER_HEALTH_URL`. The legacy in-process model settings apply only with `HERDR_BRAIN_STT_BACKEND=builtin`; `engine` preserves the external batch worker and uses the hub for live audio.

#### Live partial transcription (PRD-04, T3)

While you speak, the socket streams **partial transcript text** into the interim strip:

- **Utterance gating**: the client's VAD onset sends `{"type":"stream","action":"start"}`; the silence close sends `{"type":"stream","action":"stop"}` immediately before the turn-end `transcribe` command. Partials run only while speaking — idle silence and the agent's own playback never reach the GPU.
- **Local agreement (FR-02)**: roughly once per second of new audio the server transcribes the growing window from the utterance origin (each pass capped at ~5 s of audio, FR-05) and emits `{"type":"partial"}` with the current best guess plus `{"type":"final"}` for the token prefix that stopped changing across consecutive passes. The sequence is deterministic for the same audio.
- **One dispatch per turn (FR-04/AC4)**: `final` messages are display-only. The turn-end `transcript` message remains the single dispatch source — it routes through the same voice-turn path exactly once, and no partial ever triggers `/ask`. The window resets at every turn boundary, so a new utterance's partials never re-emit the finished turn's text, and late results from a pass that outlived its utterance are dropped server-side.
- **One inference in flight per call (FR-05)**: partial passes and the turn-end transcription share a single per-connection lock; audio arriving mid-pass is picked up by the next pass, never silently lost.
- **Language and vocabulary**: the hub service controls its language globally and does not support the app's per-call locale. The operator-editable `HERDR_BRAIN_LIVE_STT_VOCABULARY` is sent as the initial prompt (the service retains its last 200 characters).
- **No inference on silence**: Whisper echoes its recognition hint (the transcript `PR rama herdr opencode`) or hallucinates a word (`you`) when fed silence, so the server never calls the model on a window without speech. Both the streaming passes and the turn-end transcribe measure the int16 RMS of the loudest 20 ms frame and require at least 300 ms of audio; below `SILENCE_FRAME_RMS_THRESHOLD = 100` (≈ −50 dBFS) the window is skipped — passes settle as no-ops (a full silent window slides the origin so later speech is still heard) and the turn-end answers `{"type":"transcript","text":""}`, which the client treats as "not understood". The threshold sits above digital silence and sensor noise (0–~40) and well below whispered speech (≈180–600) and the client's own speech onset (≥ ~330), so quiet real speech still passes; the loudest-frame measure keeps a short word inside seconds of silence from being diluted. The vocabulary hint itself is unchanged.

#### Speech after the close rejoins the turn (PRD-05/D10, S7)

Between a turn's silence close and the answer starting, the live capture stays armed on the same socket: speech that resumes in that window — while the turn's transcript is still being computed or while its `/ask` is in flight — **rejoins the same turn** instead of becoming a second one. The in-flight ask is cancelled (its late response is discarded, never rendered), and when the resumed speech closes, the *whole* utterance (both segments' text, joined) dispatches as one turn with one user bubble.

- The window spans `transcribing` and `thinking`; it closes the moment the answer arrives — audio playback, an approval gate or a failed ask. Speech once the answer plays is interruption/barge-in, which is milestone 2 (PRD-07) and intentionally not implemented.
- Capture through the window streams into the same in-memory ring (D9: nothing is written to disk, and no partial passes run unless speech actually resumes).
- Honest failures: if the socket drops while a rejoined utterance is still un-dispatched, the lost phrase is surfaced with the existing notice and the Servidor fallback arms; a resume that transcribes to nothing still re-dispatches the cancelled segment's words — the user's words are never dropped silently.

#### Silero speech/silence framing (PRD-05 FR-01/FR-06, S6 — partial)

The default hub backend uses the existing client energy endpointer and reports `energy` with reason `local_models_disabled`. The local Silero and Smart Turn models are available only with the explicit `builtin` backend. There is no semantic turn-completion decision in hub mode.

- **Builtin model**: the live socket reuses the `silero_vad_v6.onnx` weights bundled inside the installed `faster-whisper` package, run with the installed `onnxruntime` on `CPUExecutionProvider`.
- **Opt-in and truthful**: the client sends `"vad": true` in the auth message; the server answers one `{"type":"vad_status","engine":"silero"|"energy","reason":…}` and then `{"type":"vad"}` events (`speech_start`, `speech_end`, `heartbeat` ≈1/s while speech is active, `unavailable`). Clients that do not opt in see no new messages. The idle diagnostics overlay (long-press 📞) shows the VAD actually in use (`vad framing:`), and shows energy — never silero — when the runtime, weights or session are unavailable or fail mid-call.
- **State isolation**: one builtin framer per connection owns its backlog and h/c recurrence; only the loaded ONNX session is shared. The hub's separate temporary-file behavior is described above.
- **Safe integration only**: the client's energy endpointer still owns the turn close. Silero's single power is to **defer an energy silence close** while its fresh opinion (heartbeat ≤ 2.5 s old) still says speech, for at most 12 s of energy silence (placeholder bound, D6 — to be measured on a device). It can never close or shorten a turn, a stale/failed opinion hands the close back to the energy windows, and the 10-minute cap passes through.
- **Fallback (FR-06)**: missing runtime, missing weights, a failed session or any mid-call error fails closed to the energy endpointer with today's windows (3.2–4.5 s); the socket and the call are never dropped because of framing.
- **Not proven**: accuracy and latency on real speech, soft-speech/noise behaviour and the ≤ 1.5 s first-audio goal need the Pixel acceptance (S9); the semantic detector remains the S6 blocker.

## Open-pane context

The selected pane remains the default for questions and actions. For a
question about another open Herdr agent pane, the assistant can list the
open sessions and call `read_transcript` with that pane's ID. Unknown,
closed or ambiguous IDs are refused rather than redirected to the selected
pane. A read does not change the selected pane or the target of later
actions, and other transcripts are not inserted into every prompt.

## In-call session switching

By voice, "cámbiate al panel de pagos" retargets the call: the
`switch_session` tool resolves the named pane against the CURRENT open
listing (fail-closed on unknown, stale, ambiguous or malformed ids),
makes it the request target for the rest of the turn — a same-turn
gated send freezes against the new pane — and publishes the shared
selection exactly like a chip tap, so the other device follows. The
/ask-shaped response carries an additive `switch_pane` field the client
applies, confirming out loud (and via toast) where the call points now.
An approved `create_session` replay retargets the call to the new panel
through the same field. Switching executes nothing: sends, dialogs and
stops keep their approval gates, and a live gate stays bound to the
pane it was proposed for.

## Call history

Each call is recorded separately: `POST /call/start` opens a record
(started_at, and any call left open by a crash or restart is closed as
`interrupted` on the next start), every turn of the active call is
tagged with its call id, and `POST /call/end` finalizes duration and
turn count. The PWA's drawer gains a "Llamadas" section (static/
calls.js): a list of past calls (date, duration, turns, status) that
opens each call's masked transcript, paginated. The LLM's conversation
memory is unchanged — still the single global compacted ring — and the
legacy `GET /call-history` keeps serving it. Honest limits: a server
restart mid-call loses the active-call link (those turns land untagged
and the record closes as interrupted at the next start).

### Resuming a previous call

The transcript detail offers **"Retomar llamada"**: `POST /calls/{call_id}/resume`
reopens that record as the ACTIVE call and reseeds the conversation ring
from the call's raw turns — the agent answers with that call's context
and new turns append to it. The call KEEPS its original `started_at` (a
retake is not a new call): the retake moment is recorded in `resumed_at`
(shown in the detail as "Retomada …") and the accumulated duration is
frozen, so the final `duration_s` counts ACTIVE time across sessions —
never the gap between them. Any other open call closes as `interrupted`.
A resume that fails client-side falls back to a fresh
`POST /call/start`; the call itself is never blocked by history.

## Barge-in (interrupting the assistant by voice)

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
action the assistant just explained*, and the proof must be verifiable
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

The same cancellation holds when the disconnect lands **with the answer
already in hand** — after the provider loop finished but before the turn was
persisted. A turn that can be rejected never writes the shared ring
directly: it **stages** its user/assistant pair (identities reserved up
front, ring untouched) and hands the staging to the `/ask` caller, whose
post-answer re-check is the turn's **commit boundary**. On rejection the
staging is aborted — since nothing ever landed, **nothing is evicted**: even
at the ring's full capacity (16 messages) a cancelled turn cannot cost an
earlier one, and the ring reads exactly as if the rejected turn had never
existed. The answer is dropped instead of persisted, no history rows are
written, no explanation is bound, and a gate the cancelled turn had proposed
is retired (`superseded`) under the approval store's lock, scoped to that
exact gate id and the revision the turn observed. An approval that already
landed, a gate the user patched, or a newer turn's gate is never touched —
and no already-executed action is ever claimed undone. When the boundary
passes instead, the staged pair lands in one lock-held commit (bounded
eviction of the oldest messages happens there, only for accepted turns) and
the history rows follow immediately, so the in-memory ring and SQLite agree
on both sides of the line.

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
- Turn-opening effects that already ran before the disconnect was observed
  are not rolled back: a `reset` that cleared the ring, and the legacy
  supersede of a still-unanswered gate at turn start, stay done. The
  re-dispatched turn of a resumed utterance supersedes identically, so
  restoring them would only invite races.
- Detection is cooperative and polled: the server reacts once ASGI surfaces
  the disconnect (poll cadence 250 ms), so a network that dies silently is
  only noticed at the next poll, and work already past a check completes to
  its next boundary.

## Desktop call and handover (S12/S13)

`herdr brain call` (plugin action or `bin/herdr-brain call`) opens the
existing PWA in the workstation's browser after a health check — the
desktop is another client of the SAME service, with the same
getUserMedia/VAD/STT pipeline and the same speech queue the phone uses.

- **Browser consent is native**: the command never bypasses the
  microphone prompt or autoplay policy. If the browser blocks the mic,
  the page says so and falls back to the keyboard; nothing is
  auto-granted.
- **One shared call**: at boot AND before the microphone arms (call
  start, resume-from-pause) every client re-adopts the shared call
  identity from `GET /call/context` (the default session — the same
  namespace the global call history and the boot ring seed use) and
  publishes its explicit agent selection via `POST /call/context`
  (last writer wins, monotonic revision). A mic-arm barrier recovers a
  gate the OTHER client opened after this page booted, before any
  utterance can route, so handover keeps the conversation, the selected
  agent and the pending approval gate even for already-open pages. A
  stale async snapshot (revision older than what this client already
  applied, e.g. a delayed boot response) never overwrites a newer
  explicit selection; hanging up or pausing voids a pending barrier so
  a late result never reopens the microphone.
- **S14 survives handover**: a gate recovered by the second client
  carries no playback authority — the recovery payload never includes
  the explanation speech identity — so its spoken «adelante» gets the
  clarify restate and only the restate's own receipt confirms. A
  revised gate needs a fresh explanation from whoever confirms it.
  The server enforces the same rule at the /ask supersession boundary:
  a voice utterance the approval lexicon recognizes as a gate decision
  («adelante», «no», a replace intent) that reaches /ask while a live
  gate is pending re-explains that gate instead of discarding it —
  atomically under the approval store's lock — while deliberate new
  work keeps the legacy supersede. If the live shared-context check
  itself fails, the client drops the utterance with an honest notice
  instead of sending it to a stale target. A protected decision turn
  that cannot register a speech job (the 32-slot registry is full)
  keeps the ordinary text-only degraded contract — no render, no new
  explanation authority, `speech: {status: degraded, reason:
  registry-full}` — while the gate stays protected.

## Autonomous audio proof

See [`tests/e2e/fixtures/README.md`](../tests/e2e/fixtures/README.md) for the
Chromium fake-device harness and its native input/output checks. Synthetic
capture is not proof of speech-recognition quality or physical-device echo
cancellation. The hands-free implementation logbook tracks the remaining call,
approval and desktop work separately.
