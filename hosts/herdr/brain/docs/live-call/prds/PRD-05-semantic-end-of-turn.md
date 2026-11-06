# PRD-05: Semantic End of Turn — Silero VAD + Turn-Completion Detector

| Field | Value |
|---|---|
| **Product** | `herdr-brain` (PWA / Service) |
| **Document Type** | PRD (Roadmap Phase 3 — streaming build) |
| **Status** | `approved — milestone 1 (2026-11-04)` |
| **Date** | 2026-11-03 |
| **Depends on** | PRD-03 (socket audio at the server), PRD-04 (partials feed the detector) |
| **Composed Skills** | `prd`, `cognitive-doc-design` |
| **Canonical Entrypoint** | `hosts/herdr/brain/docs/live-call/prds/PRD-05-semantic-end-of-turn.md` |
| **Target Repository Root** | `/home/bruno/Code/personal/agent-tts` |

---

## 1. Context & Problem

Turn end is decided today by an energy VAD on the phone: `vad.js` (adaptive noise floor, hysteresis) with a silence window of 1.2 s growing to 2.5 s (`app.js:219-221`), plus a 1.5 s continuation window that merges resumed speech into the same turn (`app.js:224-230`). Energy cannot distinguish "paused mid-thought" from "finished the question", so the window must be conservative — long enough to avoid cutting the user ("me corta mientras hablo, haciendo que tengo que hacer mas de un turno"), which makes every turn-end slow, and it still misfires on both sides.

Gemini Live's contrast (ROADMAP §3): the server sees the audio and decides turn end from **what was said**, semantically. This PRD moves turn decisions to the server in two layers:

1. **Silero VAD** (neural, small, runs as client WASM or server-side over the socket) for reliable speech/silence framing instead of raw energy.
2. **Turn-completion detector** on the streaming partials (a small model or a constrained LLM call) that says "this utterance is a complete turn" — adaptive: quick for clean completions, patient for trailing pauses.

## 2. Objectives & Non-Objectives

### Objectives
- Replace energy-VAD turn decisions with Silero VAD framing + a semantic turn-completion detector.
- Make end-of-turn adaptive instead of fixed windows.
- Eliminate the user-reported failure modes: mid-sentence cuts and multi-turn fragmentation.
- Keep the energy VAD as a degraded fallback.

### Non-Objectives
- Prosody/emotion modeling, intent classification beyond "complete / incomplete".
- Changing `/ask` semantics or the approval gate.
- Voice activity during playback (barge-in detection) — that is PRD-07, though it reuses this PRD's VAD.

## 3. Functional Requirements

- **FR-01**: Speech/silence framing for turn decisions MUST come from Silero VAD, either as client-side WASM or server-side over the PRD-03 socket (placement is a spike decision inside this PRD; see Open Questions).
- **FR-02** (amended 2026-11-05, D18): A turn-completion detector MUST decide end of turn locally. Its input MAY be the stream of partial transcripts (PRD-04) OR the raw utterance audio (for example a bundled local ONNX turn detector); the implementation is chosen by measurement against FR-03/FR-04 and D3, and the two may be compared side by side before one is adopted. Either way the decision MUST be local — no partial text or audio leaves the machine by default — and MUST produce a `complete` / `incomplete` verdict per evaluated point.
- **FR-03**: End of turn MUST be adaptive: a clear semantic completion with trailing silence dispatches at the short end (target ASSUMPTION: ≤ 700 ms of silence, matching the hands-free PRD's endpointing goal, `docs/PRD-hands-free-voice-call.md` FR-01), while an incomplete-looking utterance keeps waiting up to today's ceiling (2.5 s, `app.js:221`) and honors the continuation-window concept (`app.js:230`).
- **FR-04**: The detector MUST NOT dispatch on a pause the user experiences as mid-sentence — the acceptance session replays the user's failure report ("me corta mientras hablo, haciendo que tengo que hacer mas de un turno") as an explicit test case.
- **FR-05**: Detector decisions MUST be logged with the deciding partial (text excluded from any exported metrics — durations and labels only, per PRD-02 FR-06 privacy shape).
- **FR-06**: When Silero VAD is unavailable (load failure, unsupported device), the system MUST fall back to today's energy VAD path (`vad.js`) with its current windows, and the debug panel MUST show which VAD is active.
- **FR-07**: The 10-minute utterance cap (`app.js:223`) MUST remain a hard cap regardless of detector opinion.

## 4. Acceptance Criteria

1. A 20-turn natural conversation on the Pixel 7 Pro including deliberate mid-sentence pauses (list phrasing, thinking pauses): zero premature dispatches, zero user-perceived truncation.
2. Clear question completions dispatch faster than today's fixed window (measured via PRD-02; the baseline comparison is the PRD's success number).
3. With Silero disabled by flag, the call behaves exactly as today (energy fallback).
4. No partial transcript ever reaches `/ask` as a turn by itself.

## 5. Verification & Acceptance Strategy (V1–V3)

- **V1 — unit (pytest + node)**: the decision policy as pure logic — recorded VAD frames + scripted partials → dispatch/wait decisions; determinism (same inputs, same decisions); fallback switch logic. Client-side framing glue in `tests/js/` if the WASM placement wins.
- **V2 — integration (pytest)**: TestClient websocket loopback with scripted audio fixtures: utterances with mid-sentence pauses must not dispatch early; complete utterances dispatch within the adaptive budget. Browser harness drives the same fixtures through fake capture (`tests/browser/conftest.py:496-517`) end to end.
- **V3 — manual on Pixel 7 Pro**: the 20-turn session of §4.1, timed turns, barge-free; results recorded against the PRD-02 baseline.

| FR | V1 | V2 | V3 | Scenario |
|---|---|---|---|---|
| FR-01 | ✔ | ✔ | ✔ | Silero framing live |
| FR-02 | ✔ | ✔ | — | Detector contract |
| FR-03 | ✔ | ✔ | ✔ | Adaptive dispatch timing |
| FR-04 | ✔ | ✔ | ✔ | No mid-sentence cuts |
| FR-05 | ✔ | — | — | Decision logging, no text export |
| FR-06 | ✔ | ✔ | ✔ | Energy fallback |
| FR-07 | ✔ | ✔ | — | Hard cap preserved |

## 6. Risks

- **WASM VAD footprint/latency on mid-range phones** (ASSUMPTION: Silero is small enough; verify on device in the placement spike).
- **LLM-as-detector cost and latency**: one extra constrained call per partial adds up; a rule/small-model pre-filter SHOULD gate LLM calls (cost ceiling decided after measuring; see Open Questions).
- **False patience**: over-waiting on actually-complete turns regresses latency — the acceptance session measures both directions, not just accuracy.
- **Model drift**: the detector's behavior must be fixture-pinned so updates are visible.

## 7. Closed Decisions (grill 2026-11-04, milestone 1)

Decision ids refer to the ledger in ROADMAP §11.

- **Detector (D2)**: a LOCAL turn-completion detector decides first; GLM is consulted only as a tie-breaker for ambiguous partials, and only if measurements show the local detector needs it. No half-finished text leaves the machine by default.
- **Priority (D3)**: never cutting the speaker wins over speed whenever they conflict. Target: first audio ≤ 1.5 s after the user really finished.
- **Speech after a close (D10)**: speech that resumes after the turn closed but BEFORE the answer starts rejoins the same turn (the in-flight ask is cancelled and the whole utterance re-dispatched). Speech while the answer plays is an interruption, owned by PRD-07 (milestone 2).
- **Screen (D5)**: milestone 1 targets a call with the screen on (wake lock); screen-off belongs to PRD-08.
- **Acceptance (D11)**: the operator signs off on the Pixel 7 Pro: 20 natural turns with deliberate pauses and zero cuts, the first phrase never lost, first audio ≤ 1.5 s, a 5-minute call with no lost turn.
- **Silero placement, detector implementation, call budget (were open questions 1-3)**: measured within the D2 constraint (D6).
- **Detector input (D18)**: the local detector MAY take raw audio instead of partial transcripts. A candidate audio-based local ONNX model was evaluated on generated signals only (local, CPU); padding/normalisation sensitivity, reference-preprocessing parity and Spanish real-speech quality are still unproven, so audio input is an allowed alternative, not an adopted behaviour. Adoption still requires the FR-03/FR-04 acceptance evidence on the operator's device.

## 8. Execution Boundaries

Turn decisions only (FR-01..FR-07): VAD framing, detector, adaptive windows, fallback. Barge-in, LLM streaming, and TTS belong to PRD-06/07. Out-of-scope discoveries go to `## Escalated / Out-of-Scope Issues`. The ROADMAP §7 gate is decided (build, 2026-11-04) and this PRD is launched as part of milestone 1.
