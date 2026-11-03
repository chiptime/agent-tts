# PRD-01: Live Transcript Preview in Servidor Mode

| Field | Value |
|---|---|
| **Product** | `herdr-brain` (PWA / Service) |
| **Document Type** | PRD (Roadmap — quick win) |
| **Status** | `draft` |
| **Date** | 2026-11-03 |
| **Depends on** | PRD-00 (stable engine-choice surface; same turn semantics) |
| **Composed Skills** | `prd`, `cognitive-doc-design` |
| **Canonical Entrypoint** | `hosts/herdr/brain/docs/live-call/prds/PRD-01-live-transcript-preview.md` |
| **Target Repository Root** | `/home/bruno/Code/personal/agent-tts` |

---

## 1. Context & Problem

In Navegador mode the call shows live interim text (Web Speech interims, `app.js:487-494`). In Servidor mode — the default and the only mode on Android today — the user sees a level meter and a recording indicator: nothing of what was said until the whole turn closes and `/ask` answers (`app.js:487-494`, `app.js:5228-5231`). Long utterances feel like talking into a void; there is no way to notice a misheard word until the answer comes back.

Transcription itself is batched: webm/opus segments rotate every 45 s and are POSTed whole to `/transcribe` (`app.js:212-222`), which transcribes the whole blob in an executor with the resident faster-whisper model (`server.py:1459-1460`).

This PRD adds a **preview**, not streaming: periodically transcribe the in-flight recording and show it as interim text, reusing the existing interim strip.

## 2. Objectives & Non-Objectives

### Objectives
- Show live interim text during a Servidor-mode utterance, derived from periodic partial transcription of the in-flight audio.
- Never slow down or block the real turn pipeline (segments, `/ask` dispatch).
- Bound the CPU cost of previewing on the single resident whisper model.

### Non-Objectives
- Streaming STT with partial/final protocol (PRD-04).
- Semantic end of turn (PRD-05).
- Server changes beyond an optional preview endpoint; the whole-blob `/transcribe` contract stays.

## 3. Functional Requirements

- **FR-01**: In Servidor mode, the interim strip (`renderInterimStrip`, `app.js:491`) MUST display preview text derived from periodic partial transcription of the current utterance's audio, styled as interim (same treatment as Navegador interims).
- **FR-02**: Partial transcription MUST be periodic and configurable (default cadence ASSUMPTION: every 3–5 s of speech, tuned after PRD-02 measurements; segment rotation at 45 s remains unchanged, `app.js:222`).
- **FR-03**: Preview requests MUST NOT delay turn dispatch: the silence-close → `/ask` path never waits for an in-flight or queued preview result.
- **FR-04**: At most ONE preview transcription MUST be in flight per call; if the previous has not finished when the timer fires, that preview is skipped (guard against queueing behind the resident model, `Transcriber` is a single resident instance).
- **FR-05**: To bound cost on long utterances, each preview SHOULD transcribe at most the last N seconds of in-flight audio (N ASSUMPTION: ~20 s; the full audio is still transcribed authoritatively at turn close/segment rotation).
- **FR-06**: Preview text MUST be visibly marked as interim and replaced without history (it may revise as more audio arrives), never appended to the turn's authoritative transcript.
- **FR-07**: When the turn closes, the preview MUST be discarded in favor of the authoritative accumulated transcript (`app.js:5231`).

## 4. Acceptance Criteria

1. During a > 10 s utterance in Servidor mode on desktop, interim text appears and updates at least twice before the turn closes.
2. Turn dispatch latency (silence close → `/ask` sent) with previewing enabled is indistinguishable from without (verified in V2 with stubbed timings).
3. A 60 s utterance (one segment rotation) still produces the full authoritative transcript; preview never corrupts it.
4. Navegador mode interim behavior is unchanged.

## 5. Verification & Acceptance Strategy (V1–V3)

- **V1 — unit (node)**: preview scheduling policy as a pure module test (timer fires, one-in-flight guard, skip behavior, discard-on-close) in `tests/js/` following `tests/js/speech.test.js` patterns.
- **V2 — integration (pytest + Playwright fake audio)**: with a stub transcriber that records call timestamps, assert preview requests occur during speech, never block `/ask`, and the authoritative transcript equals the no-preview baseline on the same fake-audio fixture (`tests/browser/conftest.py:496-517`).
- **V3 — manual on Pixel 7 Pro**: speak a long sentence; watch text appear while speaking; confirm no perceived slowdown of answers.

| FR | V1 | V2 | V3 | Scenario |
|---|---|---|---|---|
| FR-01 | — | ✔ | ✔ | Interim strip shows preview |
| FR-02 | ✔ | ✔ | — | Configurable cadence |
| FR-03 | ✔ | ✔ | ✔ | Dispatch never waits for preview |
| FR-04 | ✔ | ✔ | — | One in flight |
| FR-05 | ✔ | ✔ | — | Bounded preview window |
| FR-06 / FR-07 | ✔ | ✔ | — | Interim marked, discarded at close |

## 6. Risks

- **CPU contention** with the resident whisper model on the phone-serving host: mitigated by FR-04/FR-05; measured, not assumed, once PRD-02 lands.
- **Growing-blob cost**: re-transcribing from the start of a long utterance each preview is wasteful; FR-05's tail window caps it (ASSUMPTION: a tail window is acoustically sufficient for a useful preview).
- **Preview/authority mismatch** (preview shows different text than the final transcript): acceptable by design — preview is explicitly interim (FR-06).

## 7. Open Questions

1. Does the preview reuse `/transcribe` with the partial blob, or a lighter `/transcribe?preview=1` variant with its own executor slot? (Decide in implementation; the contract change must be additive.)
2. Final cadence and tail-window N — set from PRD-02 measurements, defaults are ASSUMPTIONs.

## 8. Execution Boundaries

Implementation is limited to FR-01..FR-07 on top of the current segment architecture. Discoveries that argue for full streaming STT belong to PRD-04, not to scope creep here. This PRD is a `draft`; launching implementation is a separate explicit decision.
