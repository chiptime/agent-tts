# PRD — Conversation Mode (Gemini-style natural voice calls)

| | |
|---|---|
| **Product** | herdr-brain (voice-assistant PWA) |
| **Status** | draft — reorganized per user decision 2026-09-24 (engine-agnostic first, v2 capabilities phased); pending user approval of D2–D5 |
| **Date** | 2026-09-24 |
| **Effort** | TBD (client + server, planning phase pending) |

---

## 1. Background & Problem

The goal: a herdr-brain voice call should feel like the Gemini app — a
persistent mic, barge-in over the agent's speech, natural turn-taking, no
OS audio artifacts (Bluetooth chirps), and minimal perceived latency. The
design is grounded in what EXISTS today, not in a new transport — and per
the 2026-09-24 user decision, everything implementable TODAY on EITHER
voice engine ships first (**Fase 1**); the one capability that genuinely
requires the v2 "servidor" engine — duplex barge-in — is phased after it
and clearly marked engine-conditional (**Fase 2**).

What exists today (all citations verified in code):

- **Engine v2 ("servidor") is the default** (`app.js:93-98`) with a live
  in-call toggle (`app.js:2233-2262`). One `getUserMedia` acquisition per
  call with `echoCancellation: true, noiseSuppression: true`
  (`app.js:2118-2120`); the stream is kept live for the whole call
  (`app.js:2121-2122`). Each utterance wraps the live stream in a fresh
  `MediaRecorder` (`app.js:2065-2083`), and between utterances the
  recorder and the VAD loop are torn down "so the mic never hears the
  agent's own TTS; the stream itself stays live" (`app.js:1914-1926`,
  esp. `1922-1923`; teardown in `stopServerListening`, `app.js:2146-2155`).
- **VAD** (`vad.js`): adaptive noise floor (EMA over quiet frames,
  `vad.js:61-63`), hysteresis — gate opens above `floor × 2.5`, closes
  below `floor × 1.4` (`vad.js:26-31`, logic `50-70`) — utterance ends
  after 1200 ms of silence or a 15 s hard cap (`vad.js:26-27`,
  `76-88`). All params injectable (`vad.js:33-41`). The v1 endpointer
  shares the same 1200 ms / 15 s constants (`endpointing.js:36-37`,
  finalize logic `117-123`) and is equally injectable
  (`endpointing.js:39-43`).

What does NOT exist — the gaps this PRD addresses:

1. **No barge-in.** Before ANY playback the mic is stopped and the call
   enters `speaking` (`pumpAudio`, `app.js:1158-1191`, mic stop at
   `1168-1172`); VAD sampling is cleared through `stopListening()` →
   `stopServerListening()` → `releaseVadLoopOnly()`
   (`app.js:2167-2170`, `2146-2155`, `2051-2053`). After dispatch "the
   mic stays off through `/ask` + TTS; `onAudioEnded` restarts it"
   (`app.js:1750-1754`). The only interrupt is the manual ⏹ footer
   button → `stopAudio()` (`app.js:1207-1224`) — which is exactly the
   ready-made cancel primitive barge-in needs. The README claim that
   "tapping the state pill while speaking is barge-in"
   (`README.md:283-286`) is **stale**: the pill tap reopens the drawer
   and "barge-in stays on the footer ⏹" (`app.js:1226-1229`). This PRD
   cites code, not the README.
2. **Turn latency is structural.** `/ask` is a synchronous endpoint
   (`server.py:633-647`) that runs the blocking, non-streaming LLM tool
   loop (1–5 rounds: `range(max_tool_rounds + 1)` with
   `DEFAULT_MAX_TOOL_ROUNDS = 4`, `llm.py:203-240`, `config.py:43`;
   non-streaming `chat.completions.create`, `llm.py:204-209`) and then
   renders the WHOLE answer to one MP3 before responding
   (`speak_answer` → `render_mp3`, `server.py:537-549`,
   `tts.py:115-152`). The user hears nothing until model + full TTS
   finish. STT is batch per utterance (`stt.py:154-172`; client POSTs
   the clip, `app.js:1994-2015`).
3. **Endpointing is tuned for reliability, not conversation.** 1200 ms
   of silence before a turn dispatches (`vad.js:26-31`,
   `endpointing.js:36-37`) feels deliberate and slow against human
   turn-taking gaps — on BOTH engines.
4. **Announcements steal the floor.** Agent-transition audio arrives over
   SSE (`server.py:361-398`) and enters the SAME audio queue
   (`app.js:1348-1372`), so `pumpAudio` kills the user's mic mid-call to
   speak a status change (`app.js:1361`, `1369`, `1168-1172`).
5. **v1 engine root cause of BT chirps.** Chrome ends continuous
   recognition sessions every few seconds (`endpointing.js:3-6`), and
   each `onend` restart re-arms recognition with backoff
   (`app.js:1855-1871`, new `SR()` per start at `app.js:1809`) — each
   restart re-opens the mic input, which on a BT headset toggles the HFP
   SCO link: the chirp. v2 avoids it structurally with a single
   acquisition (`app.js:2118-2122`); additionally Android Chrome's Web
   Speech ignores BT headset mics entirely (`app.js:93-96`).

Hard constraint inherited from the shipped approval gate: the `/ask`
response carries `approval{}` (`shape_ask_response`,
`server.py:551-570`); while a gate is live the call sits in
`confirming` and utterances route to
`POST /approval/{gate_id}/resolve` — never `/ask`
(`app.js:1765-1770`, `approval.js:326-353`). `send_to_session` is the
only write path (`tools.py:301-328`) and never executes ungated.
**Barge-in must not bypass this gate.**

Priority spine (user decision 2026-09-24): **Fase 1** — engine-agnostic
quick wins, implementable now on either engine (D4, D5, D3).
**Fase 2** — the v2-conditional barge-in capability, a graceful no-op on
v1 with no engine switch forced (D2).

## 2. Design Notes — D2–D5 (ALL PENDING USER APPROVAL)

> **D1 retired — folded into D2 capability note.** Per the 2026-09-24
> user decision there is no v2-only gate on conversation mode: duplex /
> barge-in is a *capability requirement* of the v2 servidor engine, not a
> mode precondition. Decision labels D2–D5 stay stable (numbering
> unchanged) because prior discussions reference them.

| # | Decision (one line) | Phase | Status |
|---|---|---|---|
| D4 | Conversation endpointing: ~700 ms silence proposal, 15 s cap kept | **Fase 1** — engine-agnostic | **PENDING** |
| D5 | Announcements never steal the floor; spoken only between turns | **Fase 1** — engine-agnostic | **PENDING** |
| D3 | First-sentence audio split within the existing herdr-tts surface v1 | **Fase 1** — engine-agnostic | **PENDING** |
| D2 | Speech during playback cancels the in-flight answer + queue; requires v2; route-gated | **Fase 2** — v2-conditional | **PENDING** |

### Fase 1 — engine-agnostic quick wins (implementable now, either engine)

#### D4 — Natural endpointing (values not final here)

Conversation mode tunes the already-injectable params on BOTH surfaces —
the v2 VAD (`vad.js:33-41`) and the v1 endpointer
(`endpointing.js:39-43`) — which today share the same constants
(silence 1200 ms, hard cap 15 s, hysteresis ratios 2.5 open / 1.4 close;
`vad.js:26-31`, `endpointing.js:36-37`). Proposal: silence threshold
1200 ms → ~700 ms, and revisit the close ratio (2.5 open / 1.4 close,
`vad.js:28-29`) for faster gate closure. The 15 s hard cap stays
(`vad.js:27`, `endpointing.js:37`). Exact values are a task-planning
deliverable validated on-device (§10) — this PRD fixes the direction,
not the numbers. Works identically on either engine; no server change.

#### D5 — Announcements never steal the floor

Today a transition's audio enters the same queue as answers and kills
the mic mid-call (`app.js:1361`, `1369`; mic kill in `pumpAudio`,
`app.js:1168-1172`). In conversation mode announcements degrade to
visual: toast + anuncio bubble (already rendered, `app.js:1163-1166`,
`532`) with the existing replay button (`app.js:568-571`; the watcher
already treats spoken detail as on-demand, `watcher.py:3-7`). Spoken
only when the user is between turns (idle mic, no playback); never
enqueue over an active turn. Pure client-side queue policy —
engine-agnostic.

#### D3 — Latency split without changing herdr-tts surface v1

The brain consumes herdr-tts ONLY through its versioned CLI surface:
`--contract-version` / `--render-text OUT.mp3 TEXT` — file in, file out
(`tts.py:1-17`, esp. `6-7`). That surface stays v1 this iteration. The
split happens at `/ask` perception: return the answer text plus
FIRST-SENTENCE audio as soon as the first render completes; the
remainder is synthesized client-side through the existing `POST /tts`
(`server.py:729-736` — the same re-echo pattern the approval flow
already uses, `approval.js:133-156`) and queued behind the first. The
`approval{}` contract is preserved **byte-for-byte**
(`shape_ask_response`, `server.py:551-570`; consumed as-is by
`approval.js:5-13`): when a gate opens, the response keeps today's
single whole-echo render — no split on gated answers. Streaming TTS
(surface v2) and streaming LLM are explicitly deferred (§9).
Engine-agnostic: the split lives in the `/ask` response and the shared
client audio queue, not in the voice engine.

### Fase 2 — v2-conditional capability (graceful no-op on v1, no engine switch forced)

#### D2 — Barge-in cancels the in-flight answer; route-gated (v2 servidor engine required)

**Capability requirement (absorbs retired D1):** duplex / barge-in
REQUIRES the v2 servidor engine. Web Speech v1 exposes transcripts, not
raw audio (`recognition.onresult`, `app.js:1814-1822`), so a
VAD / barge-in monitor has no stream to sample during playback; its
session churn (`endpointing.js:3-6`; `onend` restarts with backoff,
`app.js:1855-1871`) makes continuous sampling during playback
impractical on top of that. On v1, barge-in is a **graceful no-op** —
the manual ⏹ keeps working (`app.js:1224`) — and nothing forces an
engine switch; every Fase 1 improvement still applies. (v1 also ignores
BT mics on Android Chrome, `app.js:93-96`.)

User speech during playback CANCELS immediately: client aborts the
in-flight `/ask` (an `AbortController` per request already exists in
`fetchWithTimeout`, `app.js:1376-1386`) and clears the audio queue +
player — i.e. the proven `stopAudio()` path (`app.js:1207-1220`). On the
server, FastAPI's `await request.is_disconnected()` is the candidate
mechanism to stop work early. Two honest caveats: `/ask` is a SYNC
endpoint today (`server.py:633-634`) running a blocking loop
(`llm.py:203-240`), so disconnect detection needs an async endpoint (or
executor polling), and until then an aborted request leaves **orphan
work** — the LLM rounds and TTS render complete and the response is
discarded, with the turn still appended to conversation memory
(`llm.py:245-246`). **Open question (flagged for task planning):**
whether an orphaned turn is kept in or dropped from conversation memory.
Route gating: barge-in ON by default on the phone speaker (browser AEC
available), OFF by default on BT HFP until validated on-device (see §7 —
HFP mics often get NO browser AEC, so the open mic would hear the agent
and false-barge-in or loop).

## 3. Product Decisions (PROPOSED — none user-approved yet)

1. **PROPOSED — Barge-in is cancel, not mute.** Speech during playback
   cancels the in-flight `/ask` and flushes the audio queue; the
   interrupting speech becomes the next utterance normally. No
   "pause-and-resume" audio machinery.
2. **PROPOSED — Route-gated by default.** Phone speaker: barge-in ON.
   BT HFP: OFF until the §7 validation passes; the manual ⏹ stays
   available everywhere (`app.js:1224`). Unknown route resolves toward
   the safe direction: OFF.
3. **PROPOSED — The approval gate is untouchable.** Barge-in cancels
   AUDIO, never a gate: while `confirming`, an interrupting utterance
   routes to `/resolve` exactly as today (`app.js:1765-1770`); no
   speech event ever executes `send_to_session` implicitly.
4. **PROPOSED — `callState` names unchanged.** The loop reuses
   `listening / recording / transcribing / thinking / speaking /
   paused / confirming` (`app.js:150-158`); conversation mode adds
   flags and hints, not states.
5. **PROPOSED — Client flags, additive request field.** `/ask` gains an
   optional `conversation: true` (enables the D3 split — engine-agnostic);
   barge-in enablement is a client-local flag derived from engine
   (`servidor`) + route — no server round-trip on the hot path.

## 4. Proposed Flow

```
             ┌─────────────────────────────────────────────────────┐
             │  CONVERSATION MODE                                  │
             │  Fase 1 (D4/D5/D3): EITHER engine, doable now       │
             │  Fase 2 (D2): v2 servidor only, no-op on v1         │
             └─────────────────────────────────────────────────────┘
 call start ─► getUserMedia(AEC+NS) ONCE (v2, app.js:2118-2122)
                     │
                     ▼
        ┌─── CONTINUOUS VAD (100 ms ticks, app.js:2085) ◄────────┐
        │            │                                          │
        │            │ speech opens (floor×2.5, vad.js:54)      │
        │            ▼                                          │
        │   UTTERANCE: MediaRecorder on live stream             │
        │            │  end: ~700 ms silence / 15 s cap         │
        │            │  (D4 · F1 — engine-agnostic; same tune   │
        │            │   applies to the v1 endpointer)          │
        │            ▼                                          │
        │   ROUTE — gate live?                                  │
        │       ├─ yes → POST /approval/{id}/resolve (NEVER     │
        │       │         /ask; identical to today,             │
        │       │         app.js:1765-1770)                     │
        │       └─ no  → POST /ask (conversation:true)          │
        │                    │                                 │
        │                    ▼                                 │
        │   answer text + FIRST-SENTENCE audio                  │
        │   (D3 · F1 — engine-agnostic)                         │
        │                    │  remainder → POST /tts → queue   │
        │                    ▼                                 │
        │             SPEAKING (audio queue)                    │
        │              │        ▲                               │
        │              │        └── announcements: VISUAL only  │
        │              │            (toast + bubble + replay,   │
        │              │             D5 · F1 — engine-agnostic) │
        │              │                                        │
        │   barge-in monitor (D2 · F2 — v2 ONLY, no-op on v1):  │
        │   same VAD armed during playback, enabled iff         │
        │   engine = servidor AND route allows                  │
        │              │                                        │
        │     user speech ─► CANCEL: abort /ask                 │
        │              │       (AbortController), flush queue   │
        │              │       + stop player (stopAudio path,   │
        │              │         app.js:1207-1220)              │
        │              │                                        │
        │   playback ends ──────────────────────────────────────┘
        └────────────────────────────────────────────────────────┘
```

The pipeline drawn is the v2 servidor engine. On v1 (navegador) the same
Fase 1 decision points apply at their equivalent stages — D4 through the
endpointer loop, D5/D3 through the shared audio queue — and the barge-in
box is simply absent (no-op): the manual ⏹ remains the interrupt.

Failsafe rules (always resolve toward the safe direction):

- **Barge-in never bypasses the approval gate.** During `confirming`
  the mic routes to `/resolve` exactly as today; interrupting the echo
  audio cancels SOUND only — the gate stays live and only ever resolves
  through `/resolve`, the card buttons, or expiry (silent, toward
  cancel). No speech event implicitly approves, rejects, or executes.
- Unknown/undetectable audio route → barge-in OFF; the manual ⏹ remains
  the universal interrupt (`app.js:1224`).
- Barge-in misfire (AEC leak, §7) costs one cancelled answer turn and
  a re-ask — annoying, never destructive; nothing executes.
- Server never sees the cancel (first cut of D2) → orphan work completes
  and commits to conversation memory (`llm.py:245-246`); the next turn's
  context contains it. Acceptable as a first cut; the keep-or-drop
  question is flagged in D2 and decided in task planning.

## 5. API & State Contracts

### `/ask` cancellation semantics (D2)

- Client: `ask()`'s fetch gains an externally triggerable abort (extend
  the existing `fetchWithTimeout` controller pattern,
  `app.js:1376-1386`, currently fixed 30 s at `app.js:1706`). Barge-in
  fires it, then runs the `stopAudio()` flush (`app.js:1207-1220`) and
  returns the call to the VAD loop.
- Server: detect client disconnect and stop work — mechanism:
  `await request.is_disconnected()` (FastAPI `Request`), which requires
  making `/ask` async (it is sync today, `server.py:633-634`) or
  polling from the blocking loop via the executor. Known risk
  **orphan work**: disconnect does not stop a threadpool-bound blocking
  loop (`llm.py:203-240`); the render completes and is discarded
  (`server.py:537-549`). Task planning decides how deep the cancel
  reaches (LLM rounds vs TTS render vs nothing) and settles the
  orphan-turn keep-or-drop question.

### `/ask` response — additive only, approval untouched (D3)

```json
{
  "answer": "…full text…",
  "audio_url": "…first sentence mp3…",
  "audio_pending_text": "…remainder to re-render via POST /tts…",
  "pane_id": "…", "agent": "…", "session_id": "…",
  "approval": null
}
```

- New optional field `audio_pending_text`; absent = today's behavior
  (whole-answer `audio_url`, no client re-render).
- **When `approval` is non-null the response is byte-for-byte today's
  shape** — whole echo + deterministic closer, single render, no split
  (`server.py:551-570`, closer at `558-562`; locked client contract
  `approval.js:5-13`). This shape is load-bearing: `app.js:1717-1729`
  and `approval.js:326-353` branch on it.
- `POST /tts` (`server.py:729-736`) is reused as-is for the remainder
  render (precedent: approval re-echo, `approval.js:133-156`).

### Client flags (local)

- `conversationMode` — derived from call state (in-call);
  engine-agnostic: Fase 1 improvements apply on both engines. No UI
  toggle proposed this iteration.
- `bargeInEnabled` — engine AND route-derived (D2): engine
  `servidor` (`app.js:98`) + speaker → true; BT HFP → false until §7
  validation; unknown → false. On v1 the flag is simply false and the
  affordance no-ops.

### callState — unchanged

No new states (`app.js:150-158`). `speaking` simply becomes
interruptible when `bargeInEnabled`; the mic watchdog
(`app.js:1465-1480`) and `micBaseState()` gate precedence
(`app.js:1461-1463`) keep their current meaning.

## 6. Timing

- **Endpointing (D4, values for task planning):** silence 1200 ms
  (`vad.js:26-31`, `endpointing.js:36-37`) → proposal **~700 ms** in
  conversation mode; hard cap stays **15 s** (`vad.js:27`,
  `endpointing.js:37`). Direction fixed here, numbers validated
  on-device.
- **Barge-in detection budget: ~300 ms** end to end — grounded in the
  existing 100 ms VAD tick (`app.js:2085`, `2110`): ≤ 2–3 frames to
  gate-open plus the synchronous cancel path. If measured worse than
  ~300 ms, tune tick/ratios before adding machinery.
- **First-sentence audio budget (D3):** target **≤ 1.5 s** render for
  the first sentence after the LLM answer lands (vs. waiting for the
  whole-answer render, `server.py:537-549`). The LLM loop itself
  (blocking, 1–5 rounds, `llm.py:203-240`) is out of scope this
  iteration — streaming LLM is a non-goal (§9).

## 7. Spoken Echo

- **Policy.** AEC is requested once at acquisition
  (`app.js:2118-2120`). On the phone speaker, browser AEC is uneven
  across devices; on BT HFP the mic is the SCO narrowband channel and
  browsers often apply NO AEC — an always-open mic would hear the
  agent's own TTS: self-hearing, false barge-in, or an echo loop.
- **Route gating.** Speaker: barge-in ON by default. BT HFP: OFF by
  default until validated. Route detection (e.g.
  `enumerateDevices`) is unreliable pre-acquisition on Android Chrome —
  an open implementation question (§10, T4), with the safe default OFF.
- **On-device validation plan (gate to enabling D2 on BT):** with
  playback running and the user silent, measure VAD gate-open events
  attributable to TTS leakage (frames above `floor × 2.5`,
  `vad.js:54`); then with the user speaking, measure false-cancel
  rate and any echo loop. Pass criteria set in task planning; until
  then BT stays route-gated OFF.

## 8. Drawer Integration

- **Pill unchanged:** same states and copy (`app.js:150-158`);
  tap still reopens the drawer (`app.js:1226-1231`).
- **⏹ unchanged:** the footer stop keeps its semantics
  (`app.js:1207-1224`) and remains the always-available manual
  interrupt when barge-in is engine-unavailable or route-disabled.
- **Barge-in visual hint (PROPOSED):** while `speaking` with
  `bargeInEnabled`, the pill sub-line shows a hint (product copy ES,
  e.g. "Puedes hablar para cortar") so users discover the affordance;
  no new surface.

## 9. Non-goals

- Streaming ASR / WebSocket transport — batch per-utterance STT stays
  (`stt.py:154-172`).
- herdr-tts surface v2 (streaming/chunked audio) — surface stays v1
  (`tts.py:1-17`).
- Streaming LLM responses — the blocking loop stays (`llm.py:203-240`).
- v1 (Web Speech) duplex — excluded by the D2 capability note (no raw
  stream to monitor, `app.js:1814-1822`); v1 keeps every Fase 1
  improvement and the manual ⏹.
- Forcing an engine switch to get conversation mode — Fase 1 lands on
  both engines; on v1 the Fase 2 affordance simply no-ops.
- Semantic endpointing (LLM-based end-of-turn detection).
- Auto-resume of the mic after reload — FR15 behavior stays
  (`app.js:2327-2334`).

## 10. Left for Task Planning

- **T1 — Quick wins, both engines (D4+D5):** inject conversation
  params into `createVad` (`vad.js:33-41`) / `createEndpointer`
  (`endpointing.js:39-43`) — same values on both surfaces; reroute
  announcement enqueueing (`app.js:1361`, `1369`) to visual-only during
  active turns.
- **T2 — First-sentence split (D3):** server field + client remainder
  render via `POST /tts` (`server.py:729-736`); keep gated answers
  whole.
- **T3 — Barge-in, v2 + route gating (D2):** keep the VAD loop armed
  during `speaking` (today cleared via `releaseVadLoopOnly`,
  `app.js:2051-2053`), wire the cancel path (abort + `stopAudio`,
  `app.js:1207-1220`), graceful no-op when engine ≠ servidor, add the
  §8 hint.
- **T4 — Server-side cancel (D2) + speaker-vs-BT validation matrix:**
  `/ask` disconnect detection (`is_disconnected`), orphan-work depth +
  keep-or-drop decision; validation matrix — phone speaker vs BT HFP:
  AEC self-hearing rate, false barge-in rate, BT-chirp regression
  (single acquisition must hold, `app.js:2118-2122`), endpointing
  feel, §6 budgets.
- **README refresh** (docs follow code): replace the stale barge-in
  description (`README.md:283-286`) once D2 ships.
