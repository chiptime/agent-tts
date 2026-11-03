# PRD-06: Streaming LLM and TTS Downlink

| Field | Value |
|---|---|
| **Product** | `herdr-brain` (PWA / Service) |
| **Document Type** | PRD (Roadmap Phase 4 — streaming build) |
| **Status** | `draft` |
| **Date** | 2026-11-03 |
| **Depends on** | Decision gate (PRD-09, ROADMAP §7), PRD-02 (baseline + improvement measurement). Downlink-only: parallel to PRD-03..05; integrates with them for the full streaming call |
| **Composed Skills** | `prd`, `cognitive-doc-design` |
| **Canonical Entrypoint** | `hosts/herdr/brain/docs/live-call/prds/PRD-06-streaming-llm-and-tts.md` |
| **Target Repository Root** | `/home/bruno/Code/personal/agent-tts` |

---

## 1. Context & Problem

The answer path is synchronous end to end: `/ask` runs the full GLM tool loop, then TTS renders, then audio is delivered. The latency-hiding machinery already exists downstream:

- Sentence-split synthesis with segmented delivery ("protocol-2"): segments stream via `GET /speech/{id}/next` (`server.py:2270`, `server.py:2175-2176`) with an explicit cancel (`POST /speech/{id}/cancel`, `server.py:2244`).
- The client `segmentPlayer` long-polls segments into one sequential queue (`speech.js:167,322`, `app.js:3241`).

The remaining batch point is upstream: the LLM's tokens are not streamed, so the first sentence cannot be synthesized until the whole answer (and any tool loop iterations) finish. This PRD streams GLM tokens and starts TTS at the first sentence boundary, reusing protocol-2 untouched.

## 2. Objectives & Non-Objectives

### Objectives
- Stream GLM tokens server-side during `/ask`.
- Begin synthesis at the first sentence boundary; deliver over protocol-2 segments as they are ready.
- First audible audio well before the full answer exists; improvement target set against the PRD-02 baseline (aspirational: first audio < ~1 s after end of speech — ASSUMPTION until the baseline exists).

### Non-Objectives
- Changing TTS providers (edge/openai/elevenlabs/piper stay).
- Streaming the uplink or STT (PRD-03/04); this PRD works even on top of today's batched turns.
- Modifying the tool loop's semantics or the approval gate.
- Client-side segmentPlayer redesign (reuse as-is).

## 3. Functional Requirements

- **FR-01**: The server MUST stream GLM tokens as they arrive during answer generation (a streaming variant of `/ask`, additive; the existing non-streaming response contract stays for fallback and tests).
- **FR-02**: Sentence-chunked synthesis MUST begin as soon as the first complete sentence of the answer is available, reusing the protocol-2 job model: segments appear at `/speech/{id}/next` as they are synthesized (`server.py:2270`, `speech.js:167`).
- **FR-03**: The existing cancel semantics MUST hold unchanged: `POST /speech/{id}/cancel` (`server.py:2244`) aborts pending synthesis, and client `segmentPlayer.stop()` flushes the queue (`app.js:5700`).
- **FR-04**: The tool loop and approval gate MUST NOT loosen: tools that require approval still pause for the gate before their effects; streaming only changes the transport of text/audio, never the authorization path.
- **FR-05**: Sentence segmentation MUST be deterministic and safe for TTS boundaries (abbreviations, decimals, code spans must not split mid-token; a pure-function chunker with unit tests).
- **FR-06**: If token streaming fails mid-answer (provider error, disconnect), the server MUST degrade to delivering the already-generated text through the existing whole-answer path rather than losing the turn.
- **FR-07**: Per-turn PRD-02 stage timings MUST include first-token and first-segment times so the improvement against baseline is measurable.

## 4. Acceptance Criteria

1. On a long answer (≥ 5 sentences), the first audio segment starts playing before the LLM finishes generating the full answer (observable via FR-07 timings and the segmentPlayer's first-play event).
2. The same question produces the same answer text streaming and non-streaming (fixture-tested; generation nondeterminism excluded by stubbing the model).
3. Cancel mid-answer: synthesis stops, no orphan audio plays, `/speech/{id}/cancel` observed.
4. An approval-gated tool answer still pauses for approval exactly as today.

## 5. Verification & Acceptance Strategy (V1–V3)

- **V1 — unit (pytest)**: sentence chunker on adversarial text (abbreviations, decimals, code spans, lists, Spanish punctuation); degradation path (FR-06) with a stubbed failing stream.
- **V2 — integration (pytest)**: TestClient streaming `/ask` with a stubbed GLM emitting scripted tokens; assert segment availability ordering (segment N+1 only after N), cancel semantics, approval-gate pause, and fallback contract. Browser harness plays a streaming answer through the real segmentPlayer with fake audio and asserts first-play-before-completion (`tests/browser/conftest.py:496-517`).
- **V3 — manual on Pixel 7 Pro**: perceived latency on real GLM answers; PRD-02 metrics for ≥ 10 turns compared against baseline; subjective "does it feel live" note recorded.

| FR | V1 | V2 | V3 | Scenario |
|---|---|---|---|---|
| FR-01 | — | ✔ | ✔ | Token streaming |
| FR-02 | ✔ | ✔ | ✔ | First sentence synthesized early |
| FR-03 | — | ✔ | ✔ | Cancel semantics |
| FR-04 | — | ✔ | ✔ | Approval gate preserved |
| FR-05 | ✔ | — | — | Deterministic chunker |
| FR-06 | ✔ | ✔ | — | Stream-failure degradation |
| FR-07 | — | ✔ | ✔ | Stage timings |

## 6. Risks

- **Tool-loop interleaving**: an answer that triggers tools mid-generation may invalidate already-synthesized prefixes (tool results change the reply). Mitigation: streaming begins only after the tool loop reaches its final answer phase for the turn; tool-loop streaming is explicitly out of scope.
- **Provider streaming support**: GLM streaming availability/shape is a provider fact — verified in the V3 session, not assumed (flagged ASSUMPTION until then).
- **Segment pacing vs synthesis speed**: if TTS is slower than token arrival, behavior is simply protocol-2's existing queueing; no new mechanism needed.

## 7. Open Questions

1. Numeric first-audio target: set as "baseline minus X" once PRD-02's baseline exists; the ~1 s figure stays aspirational (ASSUMPTION).
2. Streaming `/ask` as a new endpoint or a mode flag on `/ask`? (Additive either way; decide for consistency with PRD-03's socket messages.)

## 8. Execution Boundaries

Downlink streaming only (FR-01..FR-07). No STT, no turn decisions, no barge-in server logic (PRD-07 composes with this). Out-of-scope discoveries go to `## Escalated / Out-of-Scope Issues`. This PRD is a `draft` behind the ROADMAP §7 gate; launching implementation is a separate explicit decision.
