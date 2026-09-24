# Proposal: Always-Formatted Reader

## Intent

Users read conversations with formatted Markdown only after tapping "ver más". The product owner wants the formatted reading experience to be the DEFAULT: every turn in the viewed conversation renders with the anchored HTML as soon as it is available, without any expansion gesture. This is a MODIFIED delta on the `conversation-reader` capability (archived change `2026-09-24-reader-html-integration`); no new capability is introduced.

## Why it is now cheap

The v1 on-demand gate existed to protect the CPU during 5s polling before any caching existed. The content-addressed cache (LRU 128 + disk envelopes) now guarantees each turn renders at most once per (text, lang); every subsequent encounter is a cache hit with zero subprocesses. Cold cost is bounded by the per-request budget and fills progressively across existing polls.

## Approach

1. Client: on conversation load (and on each background refresh), request the rendered endpoint for the viewed pane and mount formatted HTML for ALL turns — no expansion gesture involved. "ver más" remains purely as visual truncation for very long turns, never as the render gate.
2. Progressive cold fill: turns over the per-request budget keep showing text and upgrade on later polls as budget frees (text-first discipline unchanged).
3. Budget: raise `READER_MAX_RENDERS_PER_REQUEST` from 8 to 16 (poll-safety: measured 0.25-0.28 s per cold render; 16 cold renders amortized across 5 s polls stay well under one core; cache ensures it is one-time per turn). Docstring updated — the code docstring itself requires a spec delta for value changes; this proposal is that delta.
4. Unchanged: fail-soft null-pair invariant, snapshot/stale-token discipline, cache mechanics, CSP, security rules, anchors@1 contract.

## Scope

### In Scope
- Client load-time/refresh-time rendered fetch for all viewed turns (app.js + reader.js wiring).
- "ver más" demoted to visual truncation only.
- Budget constant 8 -> 16 + docstring/tests.
- Progressive cold-fill behavior tests (pytest + node).

### Out of Scope
- Live audio sync, player IPC, markdown editing, new dependencies, upstream changes.

## Rollback

Revert the single client-wiring commit (and, if desired, the budget constant). No migration, no data changes; cache remains valid.

## Changed-lines forecast

~180-240 authored lines including tests (client wiring ~60-90, budget+docstring ~15, tests ~100-140). Under the 400-line policy: no size exception needed.
