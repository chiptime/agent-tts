# Proposal: HT-15 Markdown/HTML Reader Pipeline

## Intent

Readers need structure synchronized with speech. ANSI provides no HTML; speech cleaning destroys formatting. Deliver a foundation for karaoke and HT-16, not a standalone converter.

## Scope

### In Scope
- Formatting-preserving sanitization, plain-to-Markdown heuristics, and GFM-subset HTML.
- Sentence/paragraph anchors and synchronization mapping with engine boundary parity.
- Host bridge, minimal opt-in CLI consumer/test surface, and `TT_EN`/`TT_ES` strings.
- Hermetic smoke scenarios, architecture/usage documentation, and Spanish HT-15 PRD/index.

### Out of Scope
- HT-16 interactive modal UI or live display loop.
- ntfy/RSS HTML notes, engine changes, new dependencies, or Surface Contract v1 bump.
- Full CommonMark/GFM compliance or changes to default speech behavior.

## Capabilities

### New Capabilities
- `reader-pipeline`: safe structured HTML, deterministic sentence/paragraph mapping, and opt-in host access for reading consumers.

### Modified Capabilities
None. Existing capabilities remain unchanged.

## Approach

Add on-demand `lib/reader_pipeline.py`, using stdlib plus existing `agent_tts` helpers: `strip_ansi` → `redact_secrets` → structure-preserving chrome filtering → plain-to-Markdown heuristics → HTML. Support headings h1–h4, paragraphs, fenced code, blockquotes, tables, lists, emphasis, inline code, and links. Escape text/attributes with `html.escape`; do not execute raw HTML; allow only safe link schemes.

Emit `data-sent-idx`/`data-para-idx` anchors and mapping through `lib/tts_engine.py`; expose minimal `--render-html` host access. Parity with `agent_tts.boundaries` is mandatory: matching its regex alone does not prove alignment after speech normalization. Specify/test correspondence for preserved code, tables, links, and acronym expansion before implementation.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `lib/reader_pipeline.py` | New | Transformation and sync |
| `lib/tts_engine.py`, `bin/herdr-tts` | Modified | Bridge, CLI, translations |
| `scripts/smoke-tests.sh` | Modified | RED-first scenarios |
| `README.md`, `docs/ARCHITECTURE.md`, `docs/prds/` | New/Modified | Usage, flow, PRD/index |

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Boundary drift | High | Engine-oracle parity fixtures across normalization |
| XSS | High | Escaping, URL allowlist, adversarial tests |
| Secret leakage | High | Redact before transformation; inspect HTML and mapping |
| Parser edge cases | Medium | Escaped-text fallback; malformed-input tests |

## Rollback Plan

Revert only HT-15 slices; remove its entrypoint/module. Preserve unrelated work, configuration, engine pin, and speech/IPC paths. No migration required.

## Dependencies

- Existing pinned `agent_tts` cleaning/boundary APIs; no engine release required.
- Strict scenario-level RED-GREEN-REFACTOR using `bash scripts/smoke-tests.sh`.

## Success Criteria

- [ ] Safe structured HTML and deterministic engine-aligned anchors pass fixtures.
- [ ] Existing smoke suite, CLI/IPC behavior, and contract version `1` remain unchanged.
- [ ] No new dependencies or resident daemon process.

## Review Forecast

Approximately 800 authored changed lines: module 250–350, bridge/CLI/i18n 60–100, tests 150–220, docs/SDD 200–270 (total 660–940). Exceeds 400; `ask-on-risk` requires a slicing/exception decision before apply, not now.
