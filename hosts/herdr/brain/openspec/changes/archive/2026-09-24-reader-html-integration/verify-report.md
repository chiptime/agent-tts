# Verify Report — reader-html-integration

- **Status**: completed
- **Verdict**: pass-with-notes
- **Executor**: sdd-verify (phase worker)
- **Tree**: `master` @ `d85cd28` (impl commits `9df521c` backend+cache, `96f2e7a` CSP, `5e857c6` client, `d85cd28` docs confirmations)
- **Strict TDD**: active (`openspec/config.yaml` `strict_tdd: true`). Apply-progress evidence (Engram topic `sdd/reader-html-integration/apply-progress`, obs #9514) claims every GREEN preceded by an observed RED. I did not re-run historical RED; I verified final-state suites and RED/GREEN test existence.

## Executive Summary

All 14 requirements and 35/35 claimed tasks are evidenced in the final tree. Both release-blocking suites pass from clean state (pytest **424 passed**, exit 0; node `--test tests/js/` **102 pass**, exit 0). Architecture conformance holds: two-layer runner injection is the single prod/test path, cache hits provably spawn no subprocess, publish is `os.replace`-atomic, LRU/budget/semaphore/memo are bounded, the generation counter discards stale snapshots, the CSP lands verbatim on the index only, and `reader.js` has zero `document` references. A live E2E spot check through the real `herdr-tts --render-html` binary reproduced the apply-time smoke (0.235 s render, real anchored HTML + valid `reader-pipeline/anchors@1` sidecar, `validate_sidecar=True`). Three documented deviations were audited and all judged conformant; no unknown deviations found.

## Suite Results

| Command | Result | Exit | Notes |
|---|---|---|---|
| `.venv/bin/python -m pytest -q` | **424 passed**, 1 warning (starlette deprecation, unrelated) | 0 | no flakes; single clean run |
| `node --test tests/js/` | **102 pass**, 0 fail | 0 | 23 reader tests + 79 pre-existing |

No flake was observed in either suite; the single run is stable (matches apply-time recorded 424/102).

## Architecture Conformance

| Check | Evidence | Verdict |
|---|---|---|
| Runner injection, one path (prod + tests) | `tts.py:184` `run = runner or subprocess.run`; `server.py:222` `ReaderCache(cfg, renderer=reader_renderer or render_html)`; tests inject `FakeHtmlRunner`/`FakeReaderRenderer` | ✅ |
| Cache hit spawns no subprocess | `tests/test_server.py:1573` `test_cache_hit_spawns_no_subprocess` (counter flat on warm request against the real stub bin) | ✅ |
| Atomic publish via `os.replace` | `reader.py:196` `os.replace(tmp, cdir/f"{key}.json")`; `test_publish_is_atomic_single_file` | ✅ |
| LRU bound 128 | `reader.py:67,259-260`; `test_lru_evicts_beyond_128` (129 cold → 130 calls) | ✅ |
| Semaphore / budget / memo | `reader.py:142,69,70` + `render_turn` memo (210-234); `test_concurrency_semaphore_bound`, `test_render_budget_per_request`, `test_failure_memo_suppresses_respawn` | ✅ |
| Generation counter discards stale | `reader.js:46-53,121-140` token guard; `stale_response_discarded_on_pane_change` / `_on_session_change` | ✅ |
| CSP verbatim, index-only | `server.py:114-119,301`; `test_index_carries_exact_csp` + `test_csp_absent_on_asset_routes` | ✅ |
| `reader.js` zero `document` refs | grep count `0`; static test `reader_source_has_no_global_document` | ✅ |
| Length-prefix cache key | `reader.py:78-93`; `test_length_prefix_prevents_forged_key` | ✅ |
| Turn identity (index ⇒ no collision) | `reader.py:96-103`; `test_duplicate_text_distinct_ids`, `test_turn_ids_stable_across_identical_requests` | ✅ |

## Live E2E Spot Check

Reproduced once through `render_html` with the real binary at `~/Code/personal/herdr-tts/bin/herdr-tts` (reachable, supports `--render-html <in> <out>` + `--map`):

```
LATENCY_S=0.235
SIDECAR_CONTRACT=reader-pipeline/anchors@1
SIDECAR_ENGINE={"lang":"es","max_chars":0,"summarize":false,"lexicon_fp":""}
VALIDATE=True
SPAN_COUNT=1  CONTRACT_MATCHES_TOTAL=True
```

Real anchored HTML emitted with `tts-sent` / `data-sent-idx` / `data-para-idx` / `id="tts-sent-0"`; sidecar passes `validate_sidecar`; `count(.tts-sent) == total_sents` invariant holds. Consistent with apply-time 6.3/6.4 evidence (0.25–0.28 s, cold 2.16 s / warm 0.26 s).

## Requirement Coverage (14/14)

| # | Requirement | Verdict | Evidence |
|---|---|---|---|
| 1 | Rendered Conversation Endpoint | pass | `server.py:491-525`; `test_rendered_contract_shape`, `test_turns_preserve_source_order`, `test_empty_conversation_returns_empty_list`, `test_unknown_pane_returns_404`, `test_no_map_url_field` |
| 2 | Turn Identity Within a Snapshot | pass | `reader.py:96-103`; `test_duplicate_text_distinct_ids`, `test_turn_id_deterministic`, `test_turn_ids_stable_across_identical_requests` |
| 3 | Inline Anchored Map + Staleness | pass | `reader.py:110-130` `validate_sidecar`; `test_map_travels_inline_complete`, `test_wrong_contract_rejected`, `test_summarize_true_rejected`, `selection_emits_no_playback_call` |
| 4 | Content-Addressed Render Cache | pass | `reader.py` (cache_key/cache_dir/ReaderCache); `test_cache_hit_spawns_no_subprocess`, `test_changed_text_changes_key`, `test_corrupt_artifact_is_miss`, `test_concurrency_semaphore_bound`, `test_namespace_bump_invalidates`, `test_unwritable_cache_still_200`, `test_lru_evicts_beyond_128` |
| 5 | Bounded Renderer Invocation | pass | `tts.py:165-236`; `test_argv_matches_cli_surface`, `test_text_never_enters_argv`, `test_whole_document_preserves_leading_heading`, `test_timeout_terminates_and_nulls_pair`, `test_tempdir_removed_on_every_outcome` |
| 6 | Fail-Soft Rendered Fields | pass | `test_missing_binary_nulls_pair`, `test_exit_codes_null_pair[1-2-3]`, `test_timeout_nulls_pair`, `test_malformed_sidecar_nulls_pair`, `test_warning_omits_transcript_payload`, `test_speech_unaffected_by_reader_failure` |
| 7 | On-Demand Loading + Snapshot Discipline | pass (1 note) | `reader.js` token guard; `stale_response_discarded_on_*`, `generation_increments_on_identity_change`, `current_snapshot_applies_and_replaces_as_unit`; "viewed surface only" is by construction (`app.js:951` click-only fetch) — see SUGGESTION S1 |
| 8 | Reader Mounting + Scoped Insertion | pass | `mounts_html_into_reader_content_only`, `never_assigns_innerHTML_outside_reader_content`, `text_rendering_uses_textContent`, `test_reader_js_reference_is_versioned` |
| 9 | Strict Plain-Text Fallback | pass | `null_html_falls_back_to_text`, `network_failure_keeps_text`, `insertion_throw_falls_back_to_text`, `container_never_left_empty` |
| 10 | Turn-Scoped Sentence Selection | pass | `selects_primary_and_continuations`, `selection_scoped_to_owning_turn`, `container_anchor_selectable`, `empty_virtual_span_tolerated`, `coverage_alignment_selectable` |
| 11 | Hardened Reader Links | pass | `http_links_hardened`, `https_links_hardened`, `non_http_scheme_not_anchor` |
| 12 | CSP on PWA Index | pass | `server.py:114-119,301`; `test_index_carries_exact_csp`, `test_csp_absent_on_asset_routes` |
| 13 | Non-Executable Metadata / No Inline Handlers | pass | `test_no_map_url_field`, `no_inline_handlers_introduced`, `reader_source_has_no_global_document`; map never consumed client-side (app.js stores only `html`) |
| 14 | Test Suite Obligations | pass | both gates green; `malicious_markdown_no_script_vector` (py + node); zero new deps (`pyproject.toml` untouched, no `package.json`) |

## Deviation Audit

| Deviation | Verdict | Basis |
|---|---|---|
| (1) Size 2,390 authored vs 545–665 forecast | conformant | maintainer re-approved `size:exception`; tests ≈1,300 lines, no padding; honest reporting in apply-progress |
| (2) Task 5.6 rollback rehearsal skipped | conformant | maintainer-confirmed skip ("already merged"); rollback plan remains documented (`proposal.md` §Rollback Plan, `design.md` §Rollback) |
| (3) Traversal RED test asserts routing-level 404, not tools-level observation | conformant | `test_pane_id_traversal_does_not_escape` (`test_server.py:1520`) asserts 404 with `tools_cls.requested == []` — strictly stronger than the design's "reaches only tools.conversation" expectation; path never touches resolution or filesystem |

No unknown deviations discovered. The design's own benign refinement (new `reader.py` module, Decision 3) and the three apply-time open-question confirmations (budget null-cause, `READER_PROFILE_VERSION` release step, `reader_timeout_s=30`) are all documented in `reader.py:21-38` + `README.md:132-143` and consistent with evidence.

## Security Pass

- `innerHTML` boundary: single assignment in `reader.js:94`, gated on `container.classList.contains("reader-content")`; write-tracker test proves no write outside `.reader-content` (`never_assigns_innerHTML_outside_reader_content`).
- Link hardening: `reader.js:72-86` http/https ⇒ `target="_blank"` + `rel="noopener noreferrer"`; other schemes ⇒ replaced with `doc.createTextNode` (no anchor survives).
- No inline handlers: `reader.js` wires via `addEventListener` only; `neutralize()` (55-70) strips 42 `on*` attributes post-insertion; `no_inline_handlers_introduced` + `malicious_markdown_no_script_vector` regression.
- Map consumed as data: API JSON only; never embedded in inline `<script>`; client does not consume `map` at all (stores `html` only).

## Findings

### CRITICAL

None.

### WARNING

None.

### SUGGESTION

- **S1** (Req 7, "viewed surface only"): the guarantee that background `refreshConversation()` polling issues zero rendered requests is enforced by construction only — the fetch lives solely in the `.ver-mas` click handler (`src/herdr_brain/static/app.js:463-475` and `951`) and `refreshConversation`'s poll path (`app.js:1112-1131`) never calls `requestReaderSnapshot`. There is no automated negative test asserting this (the node suite loads only `reader.js`, not `app.js`). Consider a lightweight source-level or integration assertion if this becomes a regression risk. Non-blocking.
- **S2** (Req 8, formatted elements): the stub success path (`tests/conftest.py:59`) emits flat `<p><span class="tts-sent">…</span></p>` only, so heading/emphasis/code/list/table rendering is proven by node tests against hand-authored HTML plus scoped CSS (`index.html:436-499`), not end-to-end through a real renderer producing those elements. `e2e: none automated` per `config.yaml`, so this is acceptable and consistent with the change's own stated test posture. Non-blocking.
- **S3** (observation, upstream): the live E2E rendered a turn whose text began with `# Título del informe`, and the real renderer's output HTML omitted the leading heading (output began at the body paragraph). This is upstream `herdr-tts` behavior, out of scope for this change, and does not violate the spec (Req 5 binds only the *input* document, which is preserved — `test_whole_document_preserves_leading_heading`). Noted for whoever owns the reader surface's visual expectations.

## Residual Risks

| Risk | Severity | Note |
|---|---|---|
| First HTML-insertion boundary (XSS) | Low (mitigated) | upstream escaping + `neutralize` + `hardenLinks` + CSP + regression tests |
| 400-line review policy vs ~2,390-line delivery | Medium (accepted) | re-approved `size:exception`; archive should record the overshoot honestly |
| `reader_timeout_s=30` unvalidated at p95 on cold/loaded engine | Low | measured 0.235 s local; 30 s is a ~127× margin |
| Visual phone-PWA pass (tap `ver-mas` on device) | Low | inherently human; flagged in apply-progress as remaining |

## Next Recommended

**archive** — the change is complete, both gates green, all requirements evidenced, deviations conformant. Archive should record the actual state (2,390 lines, size:exception re-approved) without synthesizing a PASS that erases the documented overshoot.
