# Tasks: HT-15 Markdown/HTML Reader Pipeline

strict_tdd: every GREEN task is preceded by its named RED scenario in `scripts/smoke-tests.sh` (flat labels `40a <desc>`; 40 is the next free block after 39). All 5 threat-matrix rows are N/A (design Threat Matrix) — no matrix RED tasks; real boundaries covered by 40d/40f.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~750 authored: U1 ~270 (smoke 90 + module 180), U2 ~230 (smoke 80 + anchors/sidecar 150), U3 ~250 (smoke 50 + bridge/CLI/i18n 80 + docs 120) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 U1 (40a–40d core) → PR 2 U2 (40e parity) → PR 3 U3 (40f/40g + docs) |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | 40a–40d RED→GREEN: sanitize, heuristics, escaping | PR 1 | `bash scripts/smoke-tests.sh` | scenario 40 fixtures via hermetic stubs | revert smoke 40a–40d slice + `lib/reader_pipeline.py` |
| 2 | 40e RED→GREEN: oracle parity F1–F9 + sidecar | PR 2 (base PR 1) | `bash scripts/smoke-tests.sh` | pinned-engine identity check + fixture lexicon | revert smoke 40e + anchors/render in module |
| 3 | 40f/40g RED→GREEN: bridge, CLI, docs | PR 3 (base PR 2) | `bash scripts/smoke-tests.sh` | `bin/herdr-tts --render-html in out` | revert CLI case + TT keys + tts_engine branch + docs |

OUT (do not build): modal UI (HT-16), ntfy/RSS notes, engine changes, new dependencies, contract bump. Maintainer decisions: none expected — sidecar `--map` default-off per design Open Questions.

## Phase 1: Sanitization & HTML Core (R1–R4; design Sequence, Module Layout)

- [x] 1.1 RED infra: scenario 40 header comment in `scripts/smoke-tests.sh`; hermetic guards — stub `AGENT_TTS_LEXICON` to a fixture file, isolated `HOME`/XDG (design Parity Fixture Strategy).
- [x] 1.2 RED 40a (R1): sanitize keeps fences/pipes/markers/URL verbatim; no ANSI, no `[bloque de código omitido]`/`enlace web`.
- [x] 1.3 RED 40b (R2): `token=ab12cd34ef56` inside a fence → placeholder in HTML and mapping, never the secret.
- [x] 1.4 RED 40c (R3): `• alpha\n• beta` → `- alpha\n- beta` deterministic; unclosed fence → exit 0, fully escaped, zero raw tags.
- [x] 1.5 RED 40d (R4): `<script>` escaped, no script element; `javascript:` href demoted to text.
- [x] 1.6 Observe RED: `bash scripts/smoke-tests.sh` — 40a–40d fail, suite otherwise unchanged.
- [x] 1.7 GREEN: create `lib/reader_pipeline.py` — `sanitize()` (strip_ansi→redact_secrets→structure-preserving chrome filter), `plain_to_markdown()`, `markdown_to_html()` (h1–h4, pre/code+language, tables, lists, quotes, bold/italic/inline-code, links; unconditional `html.escape`; http/https only).
- [x] 1.8 Observe GREEN: full suite 0 failed.

## Phase 2: Engine-Oracle Anchors & Sidecar (R5; design Alignment Problem, Decision, Parity Fixtures, Data Contracts)

- [x] 2.1 Harness safeguard: assert pinned engine identity before parity runs — the venv may hold `agent_tts` editable at dev HEAD; verify oracle file identity (`agent_tts/boundaries.py`, `cleaner.py`, `redact.py`) (read-only) against the pinned ref before fixtures execute.
- [x] 2.2 RED 40e (R5): F1–F9 fixtures — F1 3-sentence baseline; F2 fence `. ` 4→3; F3 inline fence→coverage; F4 URL swallows `.` 2→1; F5 `etc.`→etcétera 2→1; F6 `PR`→`pull request` (raw text, oracle index); F7 table cross-newline collapse, cont-spans; F8 empty input 1 anchor; F9 unclosed fence. Asserts: count, `data-sent-idx` 0..n-1 order, `data-para-idx` == oracle, `count(.tts-sent)` == mapping entries.
- [x] 2.3 Observe RED.
- [x] 2.4 GREEN: `SentenceAnchor`/`RenderResult` dataclasses; `build_anchors()` (oracle `estimate_boundaries_from_text(clean_agent_text(R, pre_extracted=True), 1.0)` as sole index authority; signature gate → exact/coverage two-pointer); `render()` with `lang`/`max_chars`; `mapping_json()` with version/contract/engine tuple+`lexicon_fp`.
- [x] 2.5 GREEN: sidecar JSON contract tests — schema keys, `alignment` recorded on gate failure, staleness tuple.
- [x] 2.6 Observe GREEN: full suite 0 failed.

## Phase 3: Bridge, CLI, i18n (R6–R7; design Module Layout integration, Exit codes, Performance)

- [x] 3.1 RED 40f (R6): `bin/herdr-tts --render-html in.txt out.html` writes anchored HTML, exit 0; `--contract-version` stays `1`; `--render-text`/`--speak` unchanged.
- [x] 3.2 RED 40g (R7): bootstrap pinned-ref passes, no new dep entries; process transient (exits, no daemon).
- [x] 3.3 Observe RED.
- [x] 3.4 GREEN: `lib/tts_engine.py` — `reader_pipeline` re-export + `--render-html` branch before engine `main()`; `bin/herdr-tts` `--render-html)` case beside `--render-text)` (L6658) delegating to `"$VENV_PYTHON" "$ENGINE_SCRIPT"`; exit codes 0/1 usage/2 input+redaction fail-closed/3 engine down; new keys in `TT_EN`+`TT_ES`: `usage.render_html`, `help.render_html`, `error.render_html.input`, `error.render_html.engine`.
- [x] 3.5 Observe GREEN: full suite 0 failed.

## Phase 4: Documentation

- [x] 4.1 README: `--render-html` usage, exit codes, sidecar contract.
- [x] 4.2 `docs/ARCHITECTURE.md`: reader-pipeline flow (Sequence, oracle authority).
- [x] 4.3 `docs/prds/`: Spanish HT-15 PRD + index entry (proposal Affected Areas).
- [x] 4.4 Final: `bash scripts/smoke-tests.sh` 0 failed; rollback slices verified deletable per design Rollback.
