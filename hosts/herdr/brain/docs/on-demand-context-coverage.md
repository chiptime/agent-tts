# On-demand context — acceptance coverage map (T11)

Cluster-by-cluster map of the PRD *Requirement-to-Test Matrix*
(`docs/prds/herdr-brain-on-demand-context.md`) to the actual test suites,
plus the PRD *Verification Checklist* status. Honesty rules from FR-15
apply: nothing below was run unless stated under *Suite results*.

**Method:** survey of test names/classes (no source changes), two added
characterization tests (disclosed below), full suite runs.

**Suite results (2026-09-30, this task):**

- `uv run --extra dev python -m pytest -q` → **1160 passed** (baseline
  1158 + 2 added), 0 failed.
- `node --test tests/js/` → **192 passed**, 0 failed.

**Key:** ✅ covered · 🟡 partial (what is missing is stated) · ❌ gap.

## Cluster map

| # | Cluster (PRD) | Verdict | Covered by (representative tests) |
|---|---|---|---|
| 1 | False routing (FR-01..03) | ✅ | `test_queryfsm.py::TestFocusRouting` (`test_focus_returns_focus_rejected_without_touching_anything`, `test_focus_guard_fires_before_any_deadline_or_zone_work`), `TestRoutingMatrix::test_matrix_focus_global_historical`, `test_historical_without_period_never_reaches_the_engine`; `test_consult.py::TestIntentBuilders`. Clarify is terminal-per-turn: `test_tz_unavailable_asks_tz_and_touches_nothing`, `test_multi_project_rejected_with_split_hint`. |
| 2 | Tool off-path (FR-26, 28) | ✅ (by construction + tests) | The only serving path is the engine pipeline; reuse still re-inventories: `TestHappyPath::test_second_identical_call_reuses_without_collecting` asserts `inventory_calls` grew while `collect_calls` did not. Focus answers never touch providers/store. There is no alternate answer path to "detect" — the architecture prevents off-path production rather than detecting it after the fact. |
| 3 | Freshness: no-change (FR-28..31) | ✅ | `test_freshness.py::TestManifestStep::test_identical_everything_reuses`, `TestManifestDiff::test_state_only_difference_is_unchanged`, `test_mapping_missing_token_fails_loud`; `test_queryfsm.py::test_second_identical_call_reuses_without_collecting` (identical report_id/spoken/screen, detail `reuse-after-check`). |
| 4 | Freshness: deletions (FR-30, 32, 35) | ✅ | `test_freshness.py::test_source_removed_rebuilds`, `test_absent_provider_with_stored_sources_rebuilds_via_removed`, `TestPlanRebuild::test_full_current_source_set_with_full_scan`, `test_empty_diff_still_reads_every_current_source` (no append-only delta path exists; FR-32 by construction). Engram deletions detectable at token level: `test_evidence_engram.py::test_token_changes_on_update_insert_and_soft_delete`, `test_project_with_only_deleted_rows_still_listed`. |
| 5 | Time rollover (FR-08..10, 31) | ✅ | `test_periods.py`: DST battery (`test_spring_forward_span_is_167_hours_wall_clock_seven_days`, `test_fall_back_span_is_169_hours_wall_clock_seven_days`, `test_havana_nonexistent_midnight_takes_first_valid_instant`, `test_havana_ambiguous_midnight_takes_earliest`), Monday/midnight boundaries (`test_sunday_belongs_to_previous_monday`, `test_madrid_23h_day_midnight_still_resolves`), no-mtime rule (`test_signature_has_no_mtime_parameter`, `test_missing_timestamp_is_unknown`); `test_freshness.py::test_interval_movement_rebuilds_even_with_identical_manifests`. |
| 6 | Conflicts (FR-16) | 🟡 | Deterministic half covered: `test_report.py::TestValidateBriefConflictNotes` (conflict notes must cite real source ids, are screen-only), `TestRenderScreen::test_contains_prose_and_conflict_notes`; scaffold never prefers a source. **Missing:** *recognizing* disagreements is delegated to the LLM summarizer (T6 rule decision); no real-model eval has scored semantic conflict detection (see checklist item 2). |
| 7 | Timeout/incomplete (FR-17, 19, 20, 33) | ✅ | `test_queryfsm.py::TestBudget` (expiry before freshness/acquire, mid-acquire, before publish), `TestCollectFailure::test_collect_failure_mid_acquire_is_unable_no_partial`, `TestConsolidationFailures`, `test_consult.py::test_slow_model_consumes_budget_and_surfaces_unable`; `test_reportstore.py::TestRefreshFailed` (old report demoted, not current); `TestStaleAndCorrupt::test_stale_build_error_is_unable_without_retry`. |
| 8 | Volume (FR-21) | ✅ | `test_queryfsm.py::TestVolume::test_over_item_threshold_asks_to_narrow`, `test_over_char_threshold_asks_to_narrow`, `test_at_threshold_is_not_over`; `test_consult.py::test_narrow_ask_on_volume`, `TestComposition::test_settings_drive_thresholds_and_budget`. |
| 9 | Empty vs failed (FR-11, 22) | ✅ | `test_queryfsm.py::TestEmptyScope` (`test_no_evidence_scope_renders_no_work_brief`, `test_all_absent_scope_is_also_a_valid_no_work_brief`, `test_zero_providers_inventory_ok_is_not_failure`); `test_report.py::TestScaffoldEmptyReason`, `TestValidateBriefNoWork`; `test_evidence.py::test_ok_source_absent_and_failed_are_three_different_outcomes`. |
| 10 | Ref isolation (FR-14) | ✅ | `test_report.py::TestAssertNoReferences`, `TestRenderSpoken::test_spoken_never_includes_citations_field`; `test_queryfsm.py::test_spoken_artifacts_never_carry_source_ids`, `test_spoken_reference_leak_never_publishes` (pre-publish leak check), `test_unable_spoken_is_generic_never_names_sources`; `tests/js/consult.test.js` ("no event path ever constructs audio", panel renders screen text only). |
| 11 | Followup isolation (FR-23..26) | ✅ | `test_followup.py::TestPaneIsolation` (`test_no_pane_session_selection_field_in_any_spelling`, `test_persisted_schema_contains_no_pane_references`); `test_queryfsm.py::TestPaneIsolation::test_engine_cannot_send_to_any_pane_by_construction`; `test_consult.py` followup battery (`test_get_followup_context_match_returns_screen_summary`, `..._mismatch_expires`, `..._without_anchor`, `test_end_followup_clears`, `test_followup_context_id_scoped_per_session`). New-info refresh is enforced by exact-fingerprint expiry → re-consult. |
| 12 | Status truthfulness (FR-04, 12) | ✅ | `test_evidence.py::test_every_open_status_is_reported_truthfully`, `TestActiveOnly::test_constant_is_working_blocked_waiting`, `test_default_inventory_keeps_inactive_statuses_too`; `test_queryfsm.py::TestInventoryScoping` (`test_unqualified_global_keeps_idle_sessions`, `test_date_scoped_global_filters_to_active_inventory`, `test_historical_never_restricts_by_activity`). |
| 13 | Restart (FR-33, 43-runtime half) | ✅ (runtime) | `test_reportstore.py::TestRestart::test_reopen_restores_unexpired_published_and_refresh_failed`, `test_reopen_discards_leftover_building_rows` (no partial revision published); `test_followup.py::TestRestart`; `test_consult_ui.py::TestFollowupStoreFallback` (`test_registry_miss_falls_back_to_store_after_restart`, `test_restart_still_serves_the_followup_summary`). Loop-FSM checkpoint reconciliation is FR-43/future (cluster 14). |
| 14 | Worktree isolation — future loop (FR-42..45) | ❌ by design | Not built; the feature ledger scopes FR-42..45 to specification only. No tests exist because there is no loop. Needs: explicit authorization + implementation (D10). |
| 15 | Approval gates (FR-38) | ✅ | `test_consult.py::TestApprovalRegression`: `test_consult_tools_require_no_approval`, `test_send_to_session_still_gated_exactly_as_before`, `test_create_session_still_gated_exactly_as_before`, `test_gated_tool_set_unchanged`; pre-existing `test_approval*.py` suites green in the full run. |
| 16 | Historical provider coverage (FR-03, 05, 06, 35) | ✅ | Provider suites: `test_evidence_opencode.py` (enumerate/tokens/reads/deadline), `test_evidence_transcripts.py` (Claude + Antigravity classes: provenance, filters, caps, malformed-line accounting), `test_evidence_engram.py` (projects, exact filter, deleted rows, truncation). Evidence-choice per query is model-side. **Added (T11):** `test_consult.py::TestComposition::test_external_corpus_excludes_voice_call_history` pins the corpus to exactly the five non-voice-call kinds (FR-06 regression pin). |
| 17 | Timezone fallback (FR-08..12, 37) | ✅ | `test_periods.py::TestResolveTimezone` (`test_unavailable_never_falls_back_to_env_or_system_zone`, `test_posix_tz_string_is_rejected_not_guessed`); `test_queryfsm.py::test_tz_unavailable_asks_tz_and_touches_nothing`; `test_consult.py::test_timezone_candidates_from_tz_env_at_construction`, `test_ask_tz_when_candidates_empty`. Server-validated bounds: `TestValidateBounds` + `test_queryfsm.py::test_explicit_future_bounds_clarify`. |
| 18 | Consolidated output (FR-13..19) | ✅ | `test_report.py::TestScaffoldGrouping` (per-project bundles), `TestValidateBriefSections` (every scaffold project has a section), `TestValidateBriefCitations` (grounding), `TestRenderScreen` (references block); Consulting UI: `test_consult.py::test_consulting_events_emitted_start_then_end` + `tests/js/consult.test.js` (indicator shown on start, cleared on end). |
| 19 | Persistent context and retention (FR-07, 23, 24, 27, 34) | ✅ | `test_reportstore.py::TestKeyIsolation` (context/scope/interval/timezone), `TestRetention`, `TestPurge` — **added (T11):** `test_sustained_use_stays_bounded_and_never_drops_fresh_rows` (interleaved publish/purge soak; per-call bound, no fresh row ever dropped, bounded drain completes); `test_followup.py::TestContextIsolation`, `TestRetention`, `TestPurge`. |
| 20 | Revision race and cancellation (FR-17, 28..33, 41) | ✅ | `test_reportstore.py::TestRevisionRaces` (cancel discards build, older-sequence publish rejected, newer wins called second, double-publish rejected), `TestConcurrency`; mid-read token races with one re-read then failure: `test_evidence_opencode.py` and `test_evidence_transcripts.py` `test_single_change_triggers_one_reread_and_succeeds` / `test_second_change_during_reread_is_coverage_failed` / `test_deadline_expiry_before_reread_is_coverage_failed` per provider, `test_evidence_engram.py` same triple; shared deadline: `test_queryfsm.py::test_every_call_shares_one_deadline_object`. |
| 21 | Trust and authority (FR-25, 36..41) | ✅ | `test_consult.py::TestInjectionIsolation` (`test_scaffold_text_delivered_as_data_only`, `test_system_prompt_states_no_tools_and_schema`, `test_tool_layer_never_calls_any_write_path`, `test_model_obeying_injection_still_cannot_execute`, `test_consult_dispatch_entries_bind_read_only_handlers`); authority: `test_freshness.py::TestAuthorityStep::test_source_kind_outside_configured_raises`; untrusted text verbatim: per-provider `test_untrusted_text_roundtrips_verbatim`; provenance survives consolidation: `test_report.py::TestScaffoldProvenanceValidation::test_item_attributed_to_the_wrong_source_raises`; local-only is by construction (no network code in providers). |

## PRD Verification Checklist status

| Item | Status | Evidence |
|---|---|---|
| Deterministic harness green across every matrix cluster | ✅ (20/21 clusters; cluster 14 is unbuilt future work) | 1160 pytest / 192 node, this task; per-cluster rows above. |
| Optional real-model eval set executed with recorded scores | ❌ **NOT RUN** | Tests use deterministic doubles; no live model is exercised anywhere in the suite, so routing/consolidation quality and semantic conflict detection (cluster 6) remain unmeasured. No scores invented. |
| Milestone gates deterministic and recorded per milestone | ✅ | T0–T10 are individual commits with recorded RED→GREEN evidence in the ledger (`odd/tasks/herdr-brain-on-demand-context.md`): eb5d2b0 (T1 periods), 2459ace (T2 reportstore), e75fd75/e3674b3/cbd58ef (T3a/b/c evidence), 8309cf2 (T4 engram), 26fd393 (T5 freshness), f2a4770 (T6 report), 67b8e3b (T7 fsm), 98d0254 (T8 followup), 1fd41ed (T9 wiring), de2ae84 (T10 UI). |
| All-features acceptance checklist executed end-to-end | ✅ | This document + `ON-DEMAND-CONTEXT.md` + the ledger T11 entry (this task). |
| No mutation-approval regression | ✅ | Cluster 15 row: `TestApprovalRegression` (4 tests) + `test_approval.py` / `test_approval_gate_create.py` / `test_approval_lexicon.py` green in the full run. |
| Report store retention/cleanup verified bounded under sustained use | ✅ | `TestPurge` battery including the T11 soak test `test_sustained_use_stays_bounded_and_never_drops_fresh_rows`. |
| Followup pane-isolation asserted across the full followup battery | ✅ | Cluster 11 row: schema-level (dataclass + persisted schema), engine-level (cannot send by construction), and tool-level (5-test battery) assertions. |

## Tests added by this task (characterization — RED-first does not apply)

Both tests pin **existing** behavior; no production code changed.

1. `tests/test_consult.py::TestComposition::test_external_corpus_excludes_voice_call_history`
   — asserts the default consultation corpus is exactly
   `{herdr_session, opencode, claude, antigravity, engram}` and matches
   the freshness checker's configured kinds, i.e. voice-call history is
   never wired as an evidence source (FR-06).
2. `tests/test_reportstore.py::TestPurge::test_sustained_use_stays_bounded_and_never_drops_fresh_rows`
   — 24 interleaved publish/purge cycles: each `purge_expired` call
   bounded by its limit, no row inside its retention horizon ever
   removed, and a bounded drain empties the table after everything ages
   out (PRD checklist "bounded under sustained use").

## Gaps deliberately left open

| Gap | Needs |
|---|---|
| FR-42..45 autonomous implementation loop (cluster 14) | Explicit user authorization (D10) + a separate build; specification exists in the PRD only. |
| Semantic conflict detection quality (cluster 6) | A real-model evaluation set (checklist item 2) — product decision on whether/when to run it and record scores. |
| Real-model routing/consolidation eval (checklist item 2) | Same decision; deterministic matrix already covers structural routing. |
| Antigravity project identity (`conversation_summaries.db` authority) | Product decision on reading that db; would replace the cwd-fallback empty-project bucketing. |
| Semantic followup continuity | Product decision; current exact-fingerprint design is deliberate (T8/T9). |
| Multi-project fan-out | Product decision; currently a split-hint clarify (T7). |
| Threshold/timing tuning (`consult_narrow_*`, `max_span`, `future_tolerance`, purge cadence) | Field-use data; values are documented as provisional. |
