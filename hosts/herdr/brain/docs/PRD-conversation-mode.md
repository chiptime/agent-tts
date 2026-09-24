# PRD — Conversation Mode (Gemini-style natural voice calls)

| | |
|---|---|
| **Product** | herdr-brain (voice-assistant PWA) |
| **Status** | draft — pending user approval of D1–D5 |
| **Date** | 2026-09-24 |
| **Effort** | TBD (client + server, planning phase pending) |

---

## 1. Background & Problem

The goal: a herdr-brain voice call should feel like the Gemini app — a
persistent mic, barge-in over the agent's speech, natural turn-taking, no
OS audio artifacts (Bluetooth chirps), and minimal perceived latency. The
design is grounded in what EXISTS today — the v2 "servidor" engine — not
in a new transport.

What exists today (all citations verified in code):

- **Engine v2 ("servidor") is the default** (`app.js:93-98`) with a live
  in-call toggle (`app.js:2168-2197`). One `getUserMedia` acquisition per
  call with `echoCancellation: true, noiseSuppression: true`
  (`app.js:2053-2055`); the stream is kept live for the whole call
  (`app.js:2056-2057`). Each utterance wraps the live stream in a fresh
  `MediaRecorder` (`app.js:2000-2018`), and between utterances the
  recorder and the VAD loop are torn down "so the mic never hears the
  agent's own TTS; the stream itself stays live" (`app.js:1849-1861`,
  esp. `1857-1858`; teardown in `stopServerListening`, `app.js:2081-2090`).
- **VAD** (`vad.js`): adaptive noise floor (EMA over quiet frames,
  `vad.js:61-63`), hysteresis — gate opens above `floor × 2.5`, closes
  below `floor × 1.4` (`vad.js:26-29`, logic `50-70`) — utterance ends
  after 1200 ms of silence or a 15 s hard cap (`vad.js:26-27`,
  `76-88`). All params injectable (`vad.js:33-41`). The v1 endpointer
  shares the same 1200 ms / 15 s constants (`endpointing.js:36-37`,
  finalize logic `117-123`) and is equally injectable
  (`endpointing.js:39-43`).

What does NOT exist — the gaps this PRD addresses:

1. **No barge-in.** Before ANY playback the mic is stopped and the call
   enters `speaking` (`pumpAudio`, `app.js:1093-1126`, mic stop at
   `1103-1107`); VAD sampling is cleared through `stopListening()` →
   `stopServerListening()` → `releaseVadLoopOnly()`
   (`app.js:2102-2105`, `2081-2090`, `1986-1988`). After dispatch "the
   mic stays off through `/ask` + TTS; `onAudioEnded` restarts it"
   (`app.js:1685-1689`). The only interrupt is the manual ⏹ footer
   button → `stopAudio()` (`app.js:1142-1159`) — which is exactly the
   ready-made cancel primitive barge-in needs. The README claim that
   "tapping the state pill while speaking is barge-in"
   (`README.md:283-286`) is **stale**: the pill tap reopens the drawer
   and "barge-in stays on the footer ⏹" (`app.js:1161-1164`). This PRD
   cites code, not the README.
2. **Turn latency is structural.** `/ask` is a synchronous endpoint
   (`server.py:570-584`) that runs the blocking, non-streaming LLM tool
   loop (1–5 rounds: `range(max_tool_rounds + 1)` with
   `DEFAULT_MAX_TOOL_ROUNDS = 4`, `llm.py:203-240`, `config.py:43`;
   non-streaming `chat.completions.create`, `llm.py:204-209`) and then
   renders the WHOLE answer to one MP3 before responding
   (`speak_answer` → `render_mp3`, `server.py:474-486`, `tts.py:103-140`).
   The user hears nothing until model + full TTS finish. STT is batch
   per utterance (`stt.py:154-172`; client POSTs the clip,
   `app.js:1929-1950`).
3. **Endpointing is tuned for reliability, not conversation.** 1200 ms
   of silence before a turn dispatches (`vad.js:26`,
   `endpointing.js:36`) feels deliberate and slow against human
   turn-taking gaps.
4. **Announcements steal the floor.** Agent-transition audio arrives over
   SSE (`server.py:334-371`) and enters the SAME audio queue
   (`app.js:1283-1307`), so `pumpAudio` kills the user's mic mid-call to
   speak a status change (`app.js:1296`, `1304`, `1103-1106`).
5. **v1 engine root cause of BT chirps.** Chrome ends continuous
   recognition sessions every few seconds (`endpointing.js:3-6`), and
   each `onend` restart re-arms recognition with backoff
   (`app.js:1790-1806`, new `SR()` per start at `app.js:1744`) — each
   restart re-opens the mic input, which on a BT headset toggles the HFP
   SCO link: the chirp. v2 avoids it structurally with a single
   acquisition (`app.js:2053-2057`); additionally Android Chrome's Web
   Speech ignores BT headset mics entirely (`app.js:93-96`).

Hard constraint inherited from the shipped approval gate: the `/ask`
response carries `approval{}` (`shape_ask_response`,
`server.py:488-507`); while a gate is live the call sits in
`confirming` and utterances route to
`POST /approval/{gate_id}/resolve` — never `/ask`
(`app.js:1700-1705`, `approval.js:326-353`). `send_to_session` is the
only write path (`tools.py:301-328`) and never executes ungated.
**Barge-in must not bypass this gate.**

## 2. Design Notes — D1–D5 (ALL PENDING USER APPROVAL)

| # | Decision (one line) | Status |
|---|---|---|
| D1 | Conversation mode is v2-servidor-only; v1 stays legacy fallback | **PENDING** |
| D2 | Speech during playback cancels the in-flight answer + queue; route-gated | **PENDING** |
| D3 | First-sentence audio split within the existing herdr-tts surface v1 | **PENDING** |
| D4 | Conversation endpointing: ~700 ms silence proposal, 15 s cap kept | **PENDING** |
| D5 | Announcements never steal the floor; spoken only between turns | **PENDING** |

### D1 — Conversation mode is v2-servidor-only

v1 (Web Speech) is excluded from duplex: it exposes transcripts, not raw
audio (`recognition.onresult`, `app.js:1749-1757`), so there is nothing
for a VAD/barge-in monitor to sample; its session churn
(`endpointing.js:3-6`, `app.js:1790-1806`) is the BT-chirp generator;
and it ignores BT mics on Android Chrome (`app.js:93-96`). v1 remains
selectable as the legacy fallback it already is (`app.js:2168-2197`) —
conversation-mode affordances simply do not light up on it.

### D2 — Barge-in cancels the in-flight answer; route-gated

User speech during playback CANCELS immediately: client aborts the
in-flight `/ask` (an `AbortController` per request already exists in
`fetchWithTimeout`, `app.js:1311-1321`) and clears the audio queue +
player — i.e. the proven `stopAudio()` path (`app.js:1142-1155`). On the
server, FastAPI's `await request.is_disconnected()` is the candidate
mechanism to stop work early. Two honest caveats: `/ask` is a SYNC
endpoint today (`server.py:570-571`) running a blocking loop
(`llm.py:203-240`), so disconnect detection needs an async endpoint (or
executor polling), and until then an aborted request leaves **orphan
work** — the LLM rounds and TTS render complete and the response is
discarded, with the turn still appended to conversation memory
(`llm.py:245-246`). Route gating: barge-in ON by default on the phone
speaker (browser AEC available), OFF by default on BT HFP until
validated on-device (see §7 — HFP mics often get NO browser AEC, so the
open mic would hear the agent and false-barge-in or loop).

### D3 — Latency split without changing herdr-tts surface v1

The brain consumes herdr-tts ONLY through its versioned CLI surface:
`--contract-version` / `--render-text OUT.mp3 TEXT` — file in, file out
(`tts.py:1-17`, esp. `6-7`). That surface stays v1 this iteration. The
split happens at `/ask` perception: return the answer text plus
FIRST-SENTENCE audio as soon as the first render completes; the
remainder is synthesized client-side through the existing `POST /tts`
(`server.py:666-673` — the same re-echo pattern the approval flow
already uses, `approval.js:133-156`) and queued behind the first. The
`approval{}` contract is preserved **byte-for-byte**
(`shape_ask_response`, `server.py:488-507`; consumed as-is by
`approval.js:5-13`): when a gate opens, the response keeps today's
single whole-echo render — no split on gated answers. Streaming TTS
(surface v2) and streaming LLM are explicitly deferred (§9).

### D4 — Natural endpointing (values not final here)

Conversation mode tunes the already-injectable params
(`vad.js:33-41`, `endpointing.js:39-43`): silence threshold proposal
1200 ms → ~700 ms, and revisit the close ratio (2.5 open / 1.4 close,
`vad.js:28-29`) for faster gate closure. The 15 s hard cap stays
(`vad.js:27`, `endpointing.js:37`). Exact values are a task-planning
deliverable validated on-device (§10) — this PRD fixes the direction,
not the numbers.

### D5 — Announcements never steal the floor

Today a transition's audio enters the same queue as answers and kills
the mic mid-call (`app.js:1296`, `1103-1106`). In conversation mode
announcements degrade to visual: toast + anuncio bubble (already
rendered, `app.js:1098-1101`, `481`) with the existing replay button
(`app.js:517-520`; the watcher already treats spoken detail as
on-demand, `watcher.py:3-7`). Spoken only when the user is between
turns (idle mic, no playback); never enqueue over an active turn.

## 3. Product Decisions (PROPOSED — none user-approved yet)

1. **PROPOSED — Barge-in is cancel, not mute.** Speech during playback
   cancels the in-flight `/ask` and flushes the audio queue; the
   interrupting speech becomes the next utterance normally. No
   "pause-and-resume" audio machinery.
2. **PROPOSED — Route-gated by default.** Phone speaker: barge-in ON.
   BT HFP: OFF until the §7 validation passes; the manual ⏹ stays
   available everywhere (`app.js:1159`). Unknown route resolves toward
   the safe direction: OFF.
3. **PROPOSED — The approval gate is untouchable.** Barge-in cancels
   AUDIO, never a gate: while `confirming`, an interrupting utterance
   routes to `/resolve` exactly as today (`app.js:1700-1705`); no
   speech event ever executes `send_to_session` implicitly.
4. **PROPOSED — `callState` names unchanged.** The loop reuses
   `listening / recording / transcribing / thinking / speaking /
   paused / confirming` (`app.js:150-158`); conversation mode adds
   flags and hints, not states.
5. **PROPOSED — Client flags, additive request field.** `/ask` gains an
   optional `conversation: true` (enables the D3 split); barge-in
   enablement is a client-local, route-derived flag — no server
   round-trip on the hot path.

## 4. Proposed Flow

```
             ┌──────────────────────────────────────────────────────┐
             │           CONVERSATION MODE (v2 engine only)         │
             └──────────────────────────────────────────────────────┘
 call start ─► getUserMedia(AEC+NS) ONCE (app.js:2053-2057)
                     │
                     ▼
        ┌─── CONTINUOUS VAD (100 ms RMS ticks, app.js:2020) ◄────────┐
        │            │                                               │
        │            │ speech opens (floor×2.5, vad.js:54)           │
        │            ▼                                               │
        │   UTTERANCE: MediaRecorder on live stream                  │
        │            │  end: ~700 ms silence / 15 s cap (D4)         │
        │            ▼                                               │
        │   ROUTE — gate live?                                       │
        │       ├─ yes → POST /approval/{id}/resolve  (NEVER /ask;   │
        │       │         identical to today, app.js:1700-1705)      │
        │       └─ no  → POST /ask (conversation:true)               │
        │                    │                                      │
        │                    ▼                                      │
        │             answer text + FIRST-SENTENCE audio (D3)       │
        │                    │  remainder → POST /tts → queue       │
        │                    ▼                                      │
        │             SPEAKING (audio queue)                        │
        │              │        ▲                                   │
        │              │        └── announcements: VISUAL only      │
        │              │            (toast + bubble + replay, D5)   │
        │              │                                            │
        │   barge-in monitor: same VAD armed during playback,       │
        │   enabled iff route allows (D2)                           │
        │              │                                            │
        │     user speech ──► CANCEL: abort /ask (AbortController), │
        │              │           flush queue + stop player        │
        │              │           (stopAudio path, app.js:1142-55) │
        │              │                                           │
        │   playback ends ──────────────────────────────────────────┘
        └───────────────────────────────────────────────────────────┘
```

Failsafe rules (always resolve toward the safe direction):

- **Barge-in never bypasses the approval gate.** During `confirming`
  the mic routes to `/resolve` exactly as today; interrupting the echo
  audio cancels SOUND only — the gate stays live and only ever resolves
  through `/resolve`, the card buttons, or expiry (silent, toward
  cancel). No speech event implicitly approves, rejects, or executes.
- Unknown/undetectable audio route → barge-in OFF; the manual ⏹ remains
  the universal interrupt (`app.js:1159`).
- Barge-in misfire (AEC leak, §7) costs one cancelled answer turn and
  a re-ask — annoying, never destructive; nothing executes.
- Server never sees the cancel (v1 of D2) → orphan work completes and
  commits to conversation memory (`llm.py:245-246`); the next turn's
  context contains it. Acceptable; revisit in task planning.

## 5. API & State Contracts

### `/ask` cancellation semantics (D2)

- Client: `ask()`'s fetch gains an externally triggerable abort (extend
  the existing `fetchWithTimeout` controller pattern,
  `app.js:1311-1321`, currently fixed 30 s at `app.js:1641`). Barge-in
  fires it, then runs the `stopAudio()` flush (`app.js:1142-1155`) and
  returns the call to the VAD loop.
- Server: detect client disconnect and stop work — mechanism:
  `await request.is_disconnected()` (FastAPI `Request`), which requires
  making `/ask` async (it is sync today, `server.py:570-571`) or
  polling from the blocking loop via the executor. Known risk
  **orphan work**: disconnect does not stop a threadpool-bound blocking
  loop (`llm.py:203-240`); the render completes and is discarded
  (`server.py:474-486`). Task planning decides how deep the cancel
  reaches (LLM rounds vs TTS render vs nothing).

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
  (`server.py:488-507`, closer at `495-499`; locked client contract
  `approval.js:5-13`). This shape is load-bearing: `app.js:1652-1664`
  and `approval.js:326-353` branch on it.
- `POST /tts` (`server.py:666-673`) is reused as-is for the remainder
  render (precedent: approval re-echo, `approval.js:133-156`).

### Client flags (local)

- `conversationMode` — derived from engine (`servidor`, `app.js:98`) +
  call state; no UI toggle proposed this iteration.
- `bargeInEnabled` — route-derived (D2): speaker → true; BT HFP →
  false until §7 validation; unknown → false.

### callState — unchanged

No new states (`app.js:150-158`). `speaking` simply becomes
interruptible when `bargeInEnabled`; the mic watchdog
(`app.js:1400-1415`) and `micBaseState()` gate precedence
(`app.js:1396-1398`) keep their current meaning.

## 6. Timing

- **Endpointing (D4, values for task planning):** silence 1200 ms
  (`vad.js:26`, `endpointing.js:36`) → proposal **~700 ms** in
  conversation mode; hard cap stays **15 s** (`vad.js:27`,
  `endpointing.js:37`). Direction fixed here, numbers validated
  on-device.
- **Barge-in detection budget: ~300 ms** end to end — grounded in the
  existing 100 ms VAD tick (`app.js:2020`, `2045`): ≤ 2–3 frames to
  gate-open plus the synchronous cancel path. If measured worse than
  ~300 ms, tune tick/ratios before adding machinery.
- **First-sentence audio budget (D3):** target **≤ 1.5 s** render for
  the first sentence after the LLM answer lands (vs. waiting for the
  whole-answer render, `server.py:474-486`). The LLM loop itself
  (blocking, 1–5 rounds, `llm.py:203-240`) is out of scope this
  iteration — streaming LLM is a non-goal (§9).

## 7. Spoken Echo

- **Policy.** AEC is requested once at acquisition
  (`app.js:2053-2055`). On the phone speaker, browser AEC is uneven
  across devices; on BT HFP the mic is the SCO narrowband channel and
  browsers often apply NO AEC — an always-open mic would hear the
  agent's own TTS: self-hearing, false barge-in, or an echo loop.
- **Route gating.** Speaker: barge-in ON by default. BT HFP: OFF by
  default until validated. Route detection (e.g.
  `enumerateDevices`) is unreliable pre-acquisition on Android Chrome —
  an open implementation question (§10), with the safe default OFF.
- **On-device validation plan (gate to enabling D2 on BT):** with
  playback running and the user silent, measure VAD gate-open events
  attributable to TTS leakage (frames above `floor × 2.5`,
  `vad.js:54`); then with the user speaking, measure false-cancel
  rate and any echo loop. Pass criteria set in task planning; until
  then BT stays route-gated OFF.

## 8. Drawer Integration

- **Pill unchanged:** same states and copy (`app.js:150-158`);
  tap still reopens the drawer (`app.js:1161-1166`).
- **⏹ unchanged:** the footer stop keeps its semantics
  (`app.js:1142-1159`) and remains the always-available manual
  interrupt when barge-in is route-disabled.
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
- v1 (Web Speech) duplex — excluded by D1.
- Semantic endpointing (LLM-based end-of-turn detection).
- Auto-resume of the mic after reload — FR15 behavior stays
  (`app.js:2262-2269`).

## 10. Left for Task Planning

- **T1 — Endpointing + announcements (D4/D5):** inject conversation
  params into `createVad` (`vad.js:33-41`) / `createEndpointer`
  (`endpointing.js:39-43`); reroute announcement enqueueing
  (`app.js:1296`, `1304`) to visual-only during active turns.
- **T2 — Continuous VAD + barge-in, speaker route (D2):** keep the VAD
  loop armed during `speaking` (today cleared via
  `releaseVadLoopOnly`, `app.js:1986-1988`), wire the cancel path
  (abort + `stopAudio`), add the §8 hint.
- **T3 — First-sentence split (D3):** server field + client remainder
  render via `POST /tts`; keep gated answers whole.
- **T4 — Server-side cancel (D2):** `/ask` disconnect detection
  (`is_disconnected`), orphan-work depth decision.
- **Validation matrix:** phone speaker vs BT HFP — AEC self-hearing
  rate, false barge-in rate, BT-chirp regression (single acquisition
  must hold, `app.js:2053-2057`), endpointing feel, §6 budgets.
- **README refresh** (docs follow code): replace the stale barge-in
  description (`README.md:283-286`) once D2 ships.
