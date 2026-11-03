# PRD-09: Build-vs-Buy Spike — Gemini Live API / OpenAI Realtime

| Field | Value |
|---|---|
| **Product** | `herdr-brain` (PWA / Service) |
| **Document Type** | PRD (Roadmap — decision gate) |
| **Status** | `draft` |
| **Date** | 2026-11-03 |
| **Depends on** | PRD-02 (latency baseline to compare against) |
| **Gates** | PRD-03, PRD-04, PRD-05, PRD-06, PRD-07 (ROADMAP §7) |
| **Composed Skills** | `prd`, `cognitive-doc-design` |
| **Canonical Entrypoint** | `hosts/herdr/brain/docs/live-call/prds/PRD-09-build-vs-buy-spike.md` |
| **Target Repository Root** | `/home/bruno/Code/personal/agent-tts` |

---

## 1. Context & Problem

The streaming build track (PRD-03..07) is a multi-PRD investment to recreate, on local infrastructure, what commercial real-time APIs now sell as a product: Gemini Live and OpenAI Realtime stream audio both ways, do server-side VAD with semantic turn end, and support barge-in out of the box — exactly the four gaps in ROADMAP §3.

Buying has structural tradeoffs that must be weighed explicitly, not by vibe:

- **Cost**: per-minute pricing on two audio streams plus model turns (numbers gathered in the spike; never invented here).
- **Privacy**: the user's raw voice audio and transcripts leave to a third party; today the whole pipeline is local-first.
- **Lock-in**: a vendor function-call format replaces the brain's tool loop; the existing hands-free PRD names "cloud-only proprietary vendor lock-in" as a non-goal (`docs/PRD-hands-free-voice-call.md` §2) — a buy decision must override that principle consciously.
- **Tool fit**: the brain's tools (`read_transcript`, `list_open_sessions`, `switch_session`, and the approval gate) must map onto vendor function calling, including the gated-write semantics.
- **Web/Android viability**: whether these APIs are usable from an installed web PWA on Android at all is an ASSUMPTION to verify first — a "no" decides the gate by itself.

This PRD is a **time-boxed spike**: it produces a written comparison, one throwaway prototype, and a recorded decision. It builds no production integration.

## 2. Objectives & Non-Objectives

### Objectives
- A written comparison of three options: Gemini Live API, OpenAI Realtime, and the build track (PRD-03..07).
- One working throwaway prototype of the most plausible vendor option speaking to stubs of the brain tools.
- A decision — build / buy / hybrid — recorded in ROADMAP §7 against explicit criteria.

### Non-Objectives
- Production integration of any vendor (no auth in the app, no keys in the repo).
- Multi-vendor prototypes (one is enough to ground the comparison).
- Any change to the live call product during the spike.

## 3. Functional Requirements

- **FR-01**: The spike MUST produce a comparison document (linked from ROADMAP §7) covering, per option: pricing model with cited current numbers, streaming/turn/barge-in capabilities vs ROADMAP §3's table, Android-web-PWA usability (cited docs + prototype evidence), function-calling fit for the four brain tools, and privacy/data-flow (what leaves the device).
- **FR-02**: The spike MUST include a throwaway prototype: vendor session from a web page, voice in, voice out, at least two brain tools wired as vendor function calls (stubbed server side), including one approval-gated tool demonstrating that gating can be preserved (the vendor model asks, the user's gate UI decides).
- **FR-03**: The prototype MUST run on the Pixel 7 Pro PWA context (even if via a plain page) — desktop-only evidence does not answer the gate question.
- **FR-04**: The decision MUST be evaluated against explicit, pre-registered criteria (§5), each scored with evidence; the outcome recorded in ROADMAP §7 with date and rationale.
- **FR-05**: Cost of buy MUST be modeled for the user's actual usage pattern (call minutes per day — gathered from the user or call history; ASSUMPTION marked until gathered).
- **FR-06**: All vendor numbers (prices, capabilities) MUST carry a retrieval date and source link; the spike doc MUST NOT state unverified capabilities as fact.
- **FR-07**: The spike MUST be time-boxed (budget: a few working days, ASSUMPTION set by the user if they want a different box) and MUST end with the decision recorded even if some comparison cells remain "unknown" (unknowns are then risks of the chosen path, listed in ROADMAP §8).

## 4. Acceptance Criteria

1. Comparison document exists with every cell filled or explicitly marked "not verified".
2. Prototype demonstrates: live voice round trip, two function calls, one approval-gated call — on-device.
3. ROADMAP §7 contains the decision, date, and the criteria values that decided it.
4. No vendor credentials or keys anywhere in the repository.

## 5. Decision Criteria (pre-registered)

| # | Criterion | Floor / note |
|---|---|---|
| C1 | Android web PWA usability | Hard floor: must work without a native app; a "no" on both vendors = build by default |
| C2 | Monthly cost at real usage | Compared against the (sunk-time) cost of PRD-03..07; user decides the ceiling |
| C3 | Privacy: audio + transcripts to third party | User's explicit call; must be an informed opt-in, not a default |
| C4 | Tool & approval-gate fidelity | All four tools + gated writes must be expressible; partial fit = hybrid at best |
| C5 | Latency vs PRD-02 baseline | Measured on the prototype; buy must beat or match the build target (~1 s first audio aspirational) |
| C6 | Exit cost | How much of the build track remains reusable if the vendor is later dropped |

## 6. Verification & Acceptance Strategy (V1–V3)

- **V1/V2 — not applicable**: this PRD produces documents and a throwaway prototype, not shipped code; no unit/integration verification applies (stated exception per the test policy). Prototype code lives outside the product tree or clearly marked disposable and adds no product tests.
- **V3 — manual on Pixel 7 Pro**: the prototype session of FR-02/FR-03, plus a user review of the comparison doc before the decision is recorded.

| FR | Verified by |
|---|---|
| FR-01, FR-06 | Comparison doc review (cited sources, dated) |
| FR-02, FR-03 | Prototype demo on device |
| FR-04, FR-07 | ROADMAP §7 entry + time-box respected |
| FR-05 | Usage model with marked ASSUMPTIONs |

## 7. Risks

- **Vendor marketing vs web reality**: capabilities as documented may not match PWA/Android reality (autoplay policies, audio capture constraints, codec support); the prototype exists precisely to check this.
- **Pricing changes** between spike and decision: numbers are dated (FR-06); the decision notes volatility.
- **Spike scope creep**: the prototype is throwaway; any "while we're here" product change is out of scope by definition.

## 8. Open Questions

1. Actual call-minutes/day for the cost model (from user or call history; ASSUMPTION until gathered).
2. Which vendor to prototype first (Gemini Live, per the user's reference point in this roadmap's origin; confirm at spike start).
3. Is a hybrid acceptable if C3 fails but C1/C2/C5 pass (e.g. vendor for transport/VAD, local for tools)? — Pre-agreed with the user before the decision, not invented during it.

## 9. Execution Boundaries

Spike only (FR-01..FR-07): documents, prototype, decision. No product integration, no keys, no transport work in the product tree. Out-of-scope discoveries go to `## Escalated / Out-of-Scope Issues`. This PRD is a `draft`; launching it is a separate explicit decision.
