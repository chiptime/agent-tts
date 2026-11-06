# PRD-04: Streaming STT — Partials and Finals over the Socket

| Field | Value |
|---|---|
| **Product** | `herdr-brain` (PWA / Service) |
| **Document Type** | PRD (Roadmap Phase 2 — streaming build) |
| **Status** | `approved — milestone 1 (2026-11-04)` |
| **Date** | 2026-11-03 |
| **Depends on** | PRD-03 (WebSocket uplink); benefits from PRD-01 (interim strip already shows server-side preview text) |
| **Composed Skills** | `prd`, `cognitive-doc-design` |
| **Canonical Entrypoint** | `hosts/herdr/brain/docs/live-call/prds/PRD-04-streaming-stt.md` |
| **Target Repository Root** | `/home/bruno/Code/personal/agent-tts` |

---

## 1. Context & Problem

STT is whole-blob today: `/transcribe` receives a finished webm segment and the resident faster-whisper model transcribes it end to end in an executor (`server.py:1459-1460`; `engine/src/agent_tts/stt/transcriber.py:519-543`). Text exists only after a blob closes. PRD-01 patches a preview over this, but true streaming needs the transport of PRD-03 plus a streaming transcription strategy:

- The engine backend already speaks a CLI contract with streaming output passthrough (`src/herdr_brain/stt_engine.py:4,12`), so a streaming-capable engine is a natural fit later.
- For the local builtin backend, the established approach is a **sliding-window local-agreement** loop over faster-whisper (whisper_streaming-style): feed a growing window, emit as final only the token prefix that stops changing across consecutive runs, and emit the unstable remainder as partials.

## 2. Objectives & Non-Objectives

### Objectives
- Consume the PRD-03 socket audio stream and emit `partial` and `final` transcript messages back over the same socket.
- Deterministic local-agreement finals for the local builtin backend; a streaming provider behind `stt_engine.py` as a follow-up.
- Finals byte-compatible with today's accumulated transcript so `/ask` behavior is unchanged.

### Non-Objectives
- Turn decisions (PRD-05 owns end of turn; here a final is just stable text).
- Diarization, speaker labels, punctuation restyling, translation.
- Changing `/transcribe` (segment fallback path stays for PRD-03's recovery mode).

## 3. Functional Requirements

- **FR-01**: The server MUST consume PCM16 frames from `/call/stream` and emit JSON messages on the same socket: `{type: "partial", text}` and `{type: "final", text}`.
- **FR-02**: For the local builtin backend, finals MUST be produced by a local-agreement rule over a sliding window (a token sequence becomes final only when it is identical across consecutive transcription passes); the algorithm MUST be deterministic — same audio in, same partial/final sequence out.
- **FR-03**: Partials MUST reach the client with low transport+compute latency (target ASSUMPTION: ≤ 1 s behind live audio; replace with a measured budget once PRD-02 tooling exists).
- **FR-04**: Finals MUST concatenate into exactly the same accumulated transcript contract the client builds today from `/transcribe` results (`app.js:5231`), so `/ask` dispatch logic is untouched.
- **FR-05**: The streaming loop MUST bound its own compute: one transcription pass at a time per call; window length capped (cap ASSUMPTION: ~5 s of audio per pass, consistent with local-agreement implementations).
- **FR-06**: The engine backend (`stt_engine.py`) SHOULD expose the same partial/final contract; if its streaming passthrough is used, local agreement MAY be delegated to the engine.
- **FR-07**: A socket-level error in the STT consumer MUST NOT kill the call: the client degrades to the PRD-03 fallback (segment POST), emitting a debug event.

## 4. Acceptance Criteria

1. While speaking over the socket, partial text appears in the interim strip in near real time; stable prefixes become finals.
2. A/B check: for the same recorded utterances, the concatenated finals match the whole-blob `/transcribe` output (minor whitespace differences tolerated; wording differences are a defect).
3. Local-agreement behavior is deterministic under test: replaying a fixture produces an identical partial/final sequence.
4. One `/ask` per turn is preserved; no partial ever dispatches `/ask`.

## 5. Verification & Acceptance Strategy (V1–V3)

- **V1 — unit (pytest)**: the local-agreement module with scripted PCM/synthetic-transcript fixtures — pure logic, no model; determinism, window caps, final-stability rules.
- **V2 — integration (pytest)**: TestClient `websocket_connect` loopback with a stubbed whisper backend returning scripted segment texts; assert message ordering, final/partial correctness, and `/ask` parity with the batch path. Browser harness replays a fake-audio fixture through the socket and asserts the interim strip updates and one `/ask` fires per turn (`tests/browser/conftest.py:496-517`).
- **V3 — manual on Pixel 7 Pro**: live conversation — partials visibly track speech, finals stable, answers equivalent to the batch path on the same phrases; timings captured via PRD-02.

| FR | V1 | V2 | V3 | Scenario |
|---|---|---|---|---|
| FR-01 | — | ✔ | ✔ | partial/final messages |
| FR-02 | ✔ | ✔ | — | Deterministic local agreement |
| FR-03 | — | ✔ (stubbed) | ✔ | Partial latency budget |
| FR-04 | — | ✔ | ✔ | Finals match batch transcript |
| FR-05 | ✔ | ✔ | — | Compute bounds |
| FR-06 | ✔ | ✔ | — | Engine backend contract |
| FR-07 | — | ✔ | ✔ | Consumer error → fallback |

## 6. Risks

- **Compute load** of repeated whisper passes over a sliding window on the serving host: bounded by FR-05; measured via PRD-02 before/after; if the host cannot keep up, the engine backend path (FR-06) or a smaller model is the escalation — recorded, not silently switched.
- **Final/whole-blob divergence** on noisy audio (local agreement freezes early): the A/B acceptance criterion exists to catch exactly this.
- **Message congestion** if partials outpace the socket: partials are lossy by design (latest wins); finals are not.

## 7. Closed Decisions (grill 2026-11-04, milestone 1)

Decision ids refer to the ledger in ROADMAP §11.

- **Model (D7, D12)**: faster-whisper `large-v3`, reusing the model files already in llm-hub's cache (`data/whisper-cache`, `models--Systran--faster-whisper-large-v3`) by local path — no download. llm-hub's `whisper-service` itself is NOT reused: it runs on CPU int8 (moved off the GPU for Qwen) and is too slow for live partials.
- **Placement (D13)**: on the RTX 4090, sharing VRAM with llm-hub's Qwen; the lowest-memory GPU compute type that keeps live partials. When VRAM is unavailable the live call degrades to `small` with a visible notice instead of failing. VRAM headroom with Qwen loaded is measured before the milestone closes.
- **Language and vocabulary (D8)**: the transcription language is forced to the app locale, and an operator-editable list of technical terms (commit, push, PR, rama, herdr, opencode, …) is passed as a recognition hint.
- **Privacy (D9)**: partial text is shown live while the user speaks; audio is never stored — only each turn's final text enters the call history, as today.
- **Window size and cadence (was open question 1)**: measured (D6).
- **Engine passthrough (was open question 2)**: settled by the implementation spike (D6).

## 8. Execution Boundaries

STT streaming only (FR-01..FR-07). End-of-turn semantics, LLM, and TTS are untouched. Discoveries outside scope go to `## Escalated / Out-of-Scope Issues`. The ROADMAP §7 gate is decided (build, 2026-11-04) and this PRD is launched as part of milestone 1.
