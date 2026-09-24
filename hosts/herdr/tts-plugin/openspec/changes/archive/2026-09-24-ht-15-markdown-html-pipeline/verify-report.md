```yaml
change: ht-15-markdown-html-pipeline
phase: verify
mode: hybrid
strict_tdd: true
verdict: pass-with-notes
requirement_count: 7
scenario_count: 12
requirements_verified: 7
requirements_verified_at_runtime: 7
test_command: bash scripts/smoke-tests.sh
test_exit_code: 0
test_pass: 937
test_fail: 0
test_output_hash: 2a40793d14b18d172962840bf63a3dbb466eb85f01d2c35c49b590e305f97631
build_command: bash scripts/bootstrap.sh
build_exit_code: 0
build_output_hash: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
isolated_root_run1: /tmp/opencode/herdr-tts-smoke-verify-ht15-4087688
isolated_root_run2: /tmp/opencode/herdr-tts-smoke-verify-ht15-run2-4115233
flaky_rerun: false
critical_findings: 0
warning_findings: 1
suggestion_findings: 6
```

# Verification Report — ht-15-markdown-html-pipeline

## Scope

- **Change**: ht-15-markdown-html-pipeline (Markdown/HTML reader pipeline).
- **Mode**: hybrid (openspec file + Engram mirror).
- **Strict TDD**: active; test runner `bash scripts/smoke-tests.sh`.
- **Tree verified**: local `main` @ `502d85951b8412bcc55f317a786a38798168b347`.
- **Implementation commits**: 218202c (core), 76b03da (anchors+sidecar), 0a00c27 (CLI/bridge), 502d859 (docs).
- **Artifacts read**: spec (reader-pipeline/spec.md), tasks.md (23/23 checked), design.md, apply-progress Engram #9451, implementation (lib/reader_pipeline.py, lib/tts_engine.py, bin/herdr-tts, scripts/smoke-tests.sh block 40), scripts/bootstrap.sh.

## Observed Progress

- tasks.md: 23/23 implementation tasks checked `[x]` (all four phases + docs).
- No unchecked tasks remain. Full verification (not focused) is therefore legitimate.

## Checks

| Check | Command | Exit | Result |
|-------|---------|------|--------|
| Smoke suite (run 1) | `SMOKE_ROOT=/tmp/opencode/herdr-tts-smoke-verify-ht15-4087688 bash scripts/smoke-tests.sh` | 0 | 937 passed / 0 failed |
| Smoke suite (run 2) | `SMOKE_ROOT=/tmp/opencode/herdr-tts-smoke-verify-ht15-run2-4115233 bash scripts/smoke-tests.sh` | 0 | 937 passed / 0 failed |
| Build/bootstrap | `bash scripts/bootstrap.sh` | 0 | fast-path no-op (healthy venv) |
| Coverage tool | `coverage_command` empty in config.yaml | — | skipped (no coverage tool declared) |
| Lint / typecheck | `lint_command`/`typecheck_command` empty | — | skipped (none declared) |

Both smoke runs are byte-identical (same `test_output_hash`), confirming no cross-run pollution and no flake. The suite was run in a **private** `SMOKE_ROOT`, not the shared `/tmp/opencode/herdr-tts-smoke` default, because concurrent sessions share that root (apply documented pollution there).

## Spec Compliance Matrix

| Req | Requirement (spec) | Runtime proof (smoke) | Code | Status |
|-----|--------------------|----------------------|------|--------|
| R1 | Formatting-preserving sanitization (strip_ansi→redact→chrome) | 40a (8 asserts) | `lib/reader_pipeline.py:124-155` `sanitize()` | ✅ PASS |
| R2 | Redaction before transformation, placeholder in HTML **and** mapping | 40b (2 asserts) + 40e mapping (2 asserts) | `sanitize()` L135 redact first; `render()` L719-722 | ✅ PASS |
| R3 | Deterministic plain-to-markdown; safe degradation | 40c (5 asserts) | `plain_to_markdown()` L172-202 | ✅ PASS |
| R4 | GFM-subset HTML, total escaping, link allowlist | 40d (4 asserts) | `markdown_to_html()` L405-510, `_inline()` L224-239, `_SAFE_SCHEME_RE` L221 | ✅ PASS |
| R5 | Sentence anchors with engine-oracle parity | 40e (63 checks, F1-F9 + sidecar) | `_build()` L577-698 (oracle L585-587), `mapping_json()` L732-757 | ✅ PASS |
| R6 | Opt-in host access, contract v1 untouched | 40f (23 asserts) | `lib/tts_engine.py:16-60`, `bin/herdr-tts:2127-2155,6707-6710`, `TT_EN` L251-254, `TT_ES` L746-749 | ✅ PASS |
| R7 | Zero-dependency, transient process | 40g (4 asserts) | imports L31-45 (stdlib+agent_tts), no daemon | ✅ PASS |

Every requirement is proven by a passing covering smoke scenario at runtime — no requirement relies on static inspection alone.

## Correctness (R1-R7 acceptance criteria)

| Check | Evidence | Result |
|-------|----------|--------|
| Sanitize preserves fences/pipes/markers/URL, no ANSI, no speech mutation | smoke 40a L3414-3432; `sanitize()` L124-155 | ✅ |
| Secret redacted before transform; placeholder in HTML + mapping; raw secret absent | smoke 40b L3434-3446 + 40e L3653-3658 | ✅ |
| Unicode bullets → `- `, numbered markers verbatim, unclosed fence exits 0 fully escaped | smoke 40c L3448-3479 | ✅ |
| `<script>` escaped, `javascript:` demoted, `https` kept as anchor | smoke 40d L3481-3495 | ✅ |
| Anchor count == oracle count; `data-sent-idx` 0..n-1; `data-para-idx` == oracle; one primary per sentence | smoke 40e L3573-3580 (9 fixtures) | ✅ |
| Sidecar schema (version/contract/alignment/totals/staleness tuple) | smoke 40e L3633-3641 | ✅ |
| `--contract-version` == 1; `--render-text`/`--speak` untouched | smoke 40f L3726-3735; `SURFACE_CONTRACT_VERSION="1"` L53 | ✅ |
| Exit codes 0/1/2/3 | smoke 40f L3691-3714 | ✅ |
| Bilingual `TT_EN` + `TT_ES` for all 4 render-html keys | `TT_EN` L251-254, `TT_ES` L746-749; smoke 40f L3693-3713 | ✅ |
| Bootstrap pin still SHA-pinned, no new deps, transient process | smoke 40g L3737-3759 | ✅ |

## Design Coherence

| Design claim | Implementation | Verdict |
|--------------|----------------|---------|
| Indices verbatim from `estimate_boundaries_from_text(N, 1.0)`, never recomputed raw-side | `_build()` L585-587 uses oracle objects as `S`; `_sentence_spans` (L542) used only for span geometry, counts always from `S` | ✅ conformant |
| Redaction before transform, visible in HTML **and** mapping | `sanitize()` L135 before `plain_to_markdown`/`markdown_to_html`; `fragment_texts` field (L84) carries redacted text into sidecar | ✅ conformant |
| `html.escape` everywhere; no raw HTML passthrough | `_inline()` L226 escapes first; fence body L448, lang L450, table cells L466-467 | ✅ conformant |
| Link allowlist http/https only | `_SAFE_SCHEME_RE` L221; `_link_sub` L231-236 demotes other schemes | ✅ conformant |
| Hermeticity (lexicon stub + engine identity assert) effective | smoke 40e sets `AGENT_TTS_LEXICON` fixture + stub `HOME` (L3406-3409) and asserts oracle file identity vs pin (L3513-3544); identity check PASSED at runtime | ✅ conformant |
| Signature gate → exact vs coverage two-pointer | `_build()` L605-639 | ✅ conformant |
| Fail-closed on redaction raise (exit 2, no file) | `render_to_files()` L766-780 catches Exception → return 2, no file | ✅ code present, **untested** (see W-1) |

## Deviations Audit

| # | Deviation (documented by apply) | Judgment |
|---|---------------------------------|----------|
| D1 | Smoke block 40a-40g, not 39 (HT-16 took block 39) | **Conformant** (cosmetic; tests correct). Leaves stale `Smoke: 39a` labels in spec.md/tasks.md/design.md → S-1 |
| D2 | `SentenceAnchor.fragment_texts` additive + sidecar `fragment_texts` | **Conformant** (superset; strengthens R2 "placeholder in mapping" for non-primary fragments) |
| D3 | F3 lands `exact`, not `coverage` (F7 covers genuine coverage) | **Conformant** (stronger alignment; F7 exercises coverage path). Stale design.md F3 prediction → S-2 |
| D4 | Fragments are md-coordinate ranges, not raw `R` ranges | **Conformant** (documented); sidecar also emits `fragment_texts` (unambiguous). Coordinate space not in contract example → S-3 |
| D5 | Heading keeps trailing `:` (projection neutrality) | **Conformant** (keeps oracle gate exactness; exercised via F7 "Head:") |
| D6 | agent-tts pin bumped 846915c by concurrent session mid-implementation | **Conformant** (40e identity safeguard reads pin dynamically; oracle file identity == pin PASSED at runtime) |

No **undocumented** deviations that violate a spec requirement were found.

## Issues

### CRITICAL
None. No spec requirement is violated; no scenario fails; no task is incomplete.

### WARNING
- **W-1** — `lib/reader_pipeline.py:766-780` + `lib/tts_engine.py:48-56`: the **redaction-failure** fail-closed branch (exit code 2, no file written) is implemented but not exercised by any smoke assertion. The exit-2 *input-unreadable* branch is tested (40f L3709-3714), but the design Failure-Modes "`redact_secrets` raises → fail closed, exit 2" branch has no covering test. Defensive path; the primary redaction behavior *is* proven (40b/40e).

### SUGGESTION
- **S-1** — `spec.md` (Smoke: 39a–39g), `tasks.md` (39a labels), `design.md` (Test Strategy 39a–39g) all reference block 39; implementation uses 40a–40g (HT-16 owns block 39). Archive should sync the traceability labels.
- **S-2** — `design.md` Parity Fixtures table (L140) predicts F3 → coverage; implementation lands F3 → exact. Update the row.
- **S-3** — `design.md` Data Contracts example (`"fragments": [[0, 16]]`) does not state the coordinate space; `SentenceAnchor` docstring (reader_pipeline.py:71-72) says "md text". Document it explicitly for HT-16 consumers.
- **S-4** — apply-progress reports TDD evidence at commit level (RED 14→GREEN 839/0, etc.), not in the structured 5-column "TDD Cycle Evidence" table the strict module expects. Adopt the table format.
- **S-5** — `render()` uses `extract_last_turn` (cleaner.py:407) rather than the spec-named `read_last_agent_message` (base.py:38). Behavior is the "raw scrollback" path with a swallow-fallback; reconcile spec wording.
- **S-6** — Authored ~1415 lines vs ~750 forecast exceeds the 400-line review policy (forecast "High"). Delivered as 4 per-unit work-unit commits; flag for the reviewer.

## Final Verdict

**PASS WITH NOTES** (no spec violations; all 7 requirements proven by passing runtime smoke scenarios).

---

## TDD Compliance (Strict TDD)

| Check | Result | Details |
|-------|--------|---------|
| TDD Evidence reported | ⚠️ | Commit-level RED→GREEN in apply-progress #9451; not the 5-column table |
| All tasks have tests | ✅ | 7 smoke sub-scenarios 40a-40g cover R1-R7 |
| RED confirmed (tests exist) | ✅ | 40a-40g present in `scripts/smoke-tests.sh`; RED observation documented (14/driver/20 fails) |
| GREEN confirmed (tests pass) | ✅ | 63 checks in block 40e + 40a/40b/40c/40d/40f/40g all pass on execution |
| Triangulation adequate | ✅ | Multiple asserts per requirement; 40e = 63 checks across F1-F9 + sidecar |
| Safety Net for modified files | ✅ | Full suite 937/0 — no regression in blocks 0-41 |

**TDD Compliance**: 5/6 checks passed (1 format note — evidence present, table shape differs).

## Test Layer Distribution

| Layer | Tests | Files | Tools |
|-------|-------|-------|-------|
| Unit | 0 | 0 | — |
| Integration (scenario-based) | 63 (block 40) | scripts/smoke-tests.sh | custom hermetic bash (config.yaml `test_layers: integration`) |
| E2E | 0 | 0 | — |

## Changed File Coverage

Coverage analysis skipped — `coverage_command` empty in `openspec/config.yaml` (no coverage tool declared). Not a failure.

## Assertion Quality

✅ All block-40 assertions verify real behavior (exact strings, counts, exit codes, oracle parity). No tautologies, no ghost loops (40e fixture loop iterates a non-empty literal; matrix length is asserted `>= 40`), no orphan-empty checks (F8 empty input is paired with a meaningful "exactly 1 anchor" assertion), no smoke-only renders.

## Quality Metrics

- **Linter**: ➖ not available (`lint_command` empty).
- **Type Checker**: ➖ not available (`typecheck_command` empty).
