# Archive Report: ht-15-markdown-html-pipeline

**Change**: HT-15 Markdown/HTML Reader Pipeline
**Archived**: 2026-09-24 → `openspec/changes/archive/2026-09-24-ht-15-markdown-html-pipeline/`
**Artifact store**: hybrid (OpenSpec files in repo + Engram report `sdd/ht-15-markdown-html-pipeline/archive-report`, project `herdr-tts`)

## Final Task State

**23/23 tasks complete** (persisted `tasks.md`, all `[x]` across Phases 1–4). Task Completion Gate passed at archive time: no unchecked implementation tasks.

## What Shipped

- `lib/reader_pipeline.py` — formatting-preserving sanitization (`strip_ansi` → `redact_secrets` → structure-preserving chrome filter), deterministic plain-to-Markdown heuristics, GFM-subset HTML with unconditional `html.escape` and http/https-only link allowlist.
- Sentence/paragraph anchors with engine-oracle parity (`agent_tts.boundaries` as sole index authority, exact/coverage two-pointer behind a signature gate) plus sidecar JSON mapping (`--map`-style contract with version/contract/engine tuple, staleness, `fragment_texts`).
- `bin/herdr-tts --render-html in out` opt-in host access via `lib/tts_engine.py` bridge; exit codes 0/1 (usage)/2 (input+redaction fail-closed)/3 (engine down); contract version stays `1`; new `TT_EN`/`TT_ES` keys.
- Smoke block 40 scenarios **40a–40g** (RED-first, strict TDD) proving R1–R7; README/ARCHITECTURE docs and Spanish HT-15 PRD.

## Verification State (Final-State Authority)

The `verify-report.md` in this archive (937/0, pass-with-notes, exit 0, two byte-identical runs in private `SMOKE_ROOT`s, `test_output_hash 2a4079…`) is a **valid snapshot of its time** and is superseded on the points below by post-verify work. Per the final-state hierarchy, the figures below are authoritative at close:

- **Final suite: 942 passed / 0 failed, exit 0**, run twice in private isolated roots (`SMOKE_ROOT`), including all post-verify corrections.
- **W-1 (WARNING) — RESOLVED post-verify**: the redaction-failure fail-closed branch (exit 2, no file written) is now covered by **5 new smoke assertions in block 40f** via a scenario-local venv-python wrapper (no production seam; `reader_pipeline.py`/`tts_engine.py` untouched). Outcome: **green immediately** — the branch was correct; the finding was a pure coverage gap. Commit `df5c9b0` "test(read): cover HT-15 redaction-failure fail-closed exit 2".
- **S-1 (SUGGESTION) — RESOLVED post-verify**: stale `39a–39g` labels synced to real block `40a–40g` across `spec.md` (7 requirement titles), `design.md` (16 lines), and `tasks.md` (16 lines). Commit `aa223c2` "docs(openspec): sync HT-15 smoke labels to real block 40a-40g".
- **CRITICAL findings: 0** at all times (verify-report and final state agree). No CRITICAL ever blocked archive.
- Zero CRITICAL issues at close; the WARNING W-1 is resolved as above.

## Deviations Audit (from verify-report)

All six deviations documented by apply were judged **conformant** by verification:

- D1 smoke block 40a–40g instead of 39 (HT-16 owns block 39) — cosmetic; label sync completed post-verify (S-1 resolution).
- D2 `SentenceAnchor.fragment_texts` additive superset — strengthens R2 mapping visibility.
- D3 F3 lands `exact` (F7 covers the `coverage` path) — stronger alignment.
- D4 fragments are md-coordinate ranges (documented in `SentenceAnchor` docstring; sidecar emits `fragment_texts`).
- D5 heading keeps trailing `:` — preserves oracle gate exactness.
- D6 agent-tts pin bump by concurrent session — 40e oracle-identity safeguard read the pin dynamically and PASSED at runtime.

## Size Exception

Maintainer **re-approved** `size:exception`: authored ~1465 lines (original forecast/approval ~750; apply exceeded the forecast). Delivered as work-unit commits (see below), all on local branch `feat/ht-14-light-theme`. The 400-line review policy exception is maintainer-owned and explicit.

## Commits (HT-15 change, this branch)

- `218202c` feat(read): HT-15 pipeline core — sanitize, markdown heuristics, escaped GFM HTML
- `76b03da` feat(read): HT-15 engine-oracle sentence anchors + sidecar map
- `0a00c27` feat(read): HT-15 --render-html host access — CLI, bridge, bilingual strings
- `502d859` docs(read): HT-15 --render-html usage, exit codes, sidecar and architecture flow
- `df5c9b0` test(read): cover HT-15 redaction-failure fail-closed exit 2 (post-verify W-1)
- `aa223c2` docs(openspec): sync HT-15 smoke labels to real block 40a-40g (post-verify S-1)
- Archive move + synced specs: one `docs(chore)` commit at archive time (this report's commit).

## Residual Notes (non-blocking, informational)

- **S-2** (informational): design.md Parity Fixtures table predicted F3 → `coverage`; implementation lands F3 → `exact` (F7 exercises coverage). Documented; accepted.
- **S-3** (informational): sidecar fragment coordinate space (md text) documented in `SentenceAnchor` module docstring rather than the contract example. Documented; accepted.
- **S-5** (informational): `render()` uses `extract_last_turn` (raw-scrollback path with swallow fallback) rather than the spec-named `read_last_agent_message` — by design.
- **HT-16 dependency**: the consumer must accept **multi-fragment highlights** (a sentence may span multiple `fragment_texts` ranges).
- **Branch topology user-owned**: all work sits on local `feat/ht-14-light-theme`, NOT on main, NOT pushed. Delivery (push/merge/PR) is a separate explicit maintainer decision — the archive phase did not touch branches.

## Delta Spec Sync Outcome

- Capability `reader-pipeline` is **NEW** — no main spec existed, so the delta spec (a full spec: **7 requirements**, 12 scenarios) was copied mechanically to `openspec/specs/reader-pipeline/spec.md`.
- Composition command not required (no `sdd-archive-compose` path: canonical spec absent). Native composition remains mandatory for future changes that MODIFY/REMOVE these requirements.
- Mechanical evidence: `diff -r` delta vs. staged copy — **empty** (no differences).
- Non-destructive merge: no requirements removed or modified anywhere; other capabilities (`installer`, `plugin-bootstrap`, `settings-navigation`, `terminal-theme`) untouched.

## Mechanical Archive Evidence

- Move: `git mv` of the tracked change folder to `openspec/changes/archive/2026-09-24-ht-15-markdown-html-pipeline/` (rename staged; untracked `design.md`/`tasks.md`/`verify-report.md` moved with the directory and included in the archive commit).
- Readback: recursive pre-move snapshot vs. archived tree — **empty** `diff -r` (no differences; this report is additive and was written after the readback).
- Active changes directory no longer contains this change.

## Artifacts in Archive

- `proposal.md` — present
- `exploration.md` — present
- `specs/reader-pipeline/spec.md` — present (delta copy, byte-identical to synced main spec)
- `design.md` — present
- `tasks.md` — present, 23/23 complete
- `verify-report.md` — present (verification-time snapshot; final state recorded in this report)
- `archive-report.md` — this report

## Source of Truth Updated

- `openspec/specs/reader-pipeline/spec.md` — **created** (new capability spec, 7 requirements)
- All other capability specs — untouched

## Engram Traceability

Archive report mirrored to Engram: project `herdr-tts`, topic_key `sdd/ht-15-markdown-html-pipeline/archive-report`, type `architecture`. Phase inputs read directly from the OpenSpec file store (proposal, exploration, delta spec, design, tasks, verify-report, config.yaml); no Engram observations were read as source material.
