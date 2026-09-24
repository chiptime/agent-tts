# Tasks: reader-always-formatted

Strict TDD active. Gates: `.venv/bin/python -m pytest -q` AND `node --test tests/js/` — both release-blocking. Every implementation task is GREEN-only after an observed RED.

## Review Workload Forecast

- Estimated total: ~180-240 authored lines (client wiring 60-90, budget+docstring 15, tests 100-140). `Chained PRs recommended: No` — `400-line budget risk: Low` — `Decision needed before apply: No`. Single local delivery on master.

## Phase 1: RED tests

- [x] 1.1 (pytest) `tests/test_server.py::test_rendered_without_expansion_flag` — endpoint already serves all turns; assert the response shape carries html for cached turns with no client-side flag (guards the contract the client will now rely on by default). RED check: test exists and passes (contract unchanged) — mark as regression guard, not a behavior RED. DONE: passes at RED phase and at final tree (guard, no behavior RED).
- [x] 1.2 (node) `tests/js/reader.test.js` — new RED: `mounts formatted turns on surface load without expansion gesture` (reader.js API accepts an explicit load-time mount call; fails today because no such call path exists). DONE: RED observed (loadRendered is not a function) -> GREEN.
- [x] 1.3 (node) new RED: `expansion does not gate rendering` — a truncated turn still gets its rendered fetch/mount call. DONE: RED observed -> GREEN.
- [x] 1.4 (node) new RED: `cold turns upgrade on subsequent refresh` — first pass leaves text (budget), second pass mounts formatted (progressive fill; use injected fake renderer/queue). DONE: RED observed -> GREEN.
- [x] 1.5 (pytest) new RED: `tests/test_reader.py::test_budget_allows_sixteen_cold_renders` — budget constant 16 accepted; over-budget cold turns still null-pair (fail-soft unchanged). DONE: RED observed (constant 8) -> GREEN.

## Phase 2: GREEN implementation

- [x] 2.1 (node) reader.js: expose load-time mount path honoring the generation counter and turn_id replacement (reuse existing primitives; no document references). DONE: `loadRendered()` + `htmlFor()` reusing `requestSnapshot`; zero-document test still green.
- [x] 2.2 app.js: call the rendered fetch for the viewed pane on conversation load and on each surface refresh; mount via reader.js; keep textContent until html arrives; keep stale-token discard. DONE: `requestReaderSnapshot()` wired in `refreshConversation().then`.
- [x] 2.3 app.js: decouple "ver más" from rendering — truncation-only behavior; expansion never triggers or suppresses the rendered fetch. DONE.
- [x] 2.4 reader.py: `READER_MAX_RENDERS_PER_REQUEST` 8 -> 16 + docstring update (spec delta reference: this change). DONE.
- [x] 2.5 (pytest) `tests/test_reader.py` budget test GREEN; full fail-soft matrix unchanged (run existing suite). DONE: 429 passed.

## Phase 3: Release verification

- [x] 3.1 Both gates green at final tree; malicious-Markdown regression test still green (XSS unchanged). DONE: pytest 429 passed; node 105/105; `malicious_markdown_no_script_vector`, `no_inline_handlers_introduced`, `reader_source_has_no_global_document`, argv/shell guards all green.
- [ ] 3.2 Manual device pass: open conversation on phone -> turns formatted without expansion (maintainer visual check).
