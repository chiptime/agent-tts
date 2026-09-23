# Delta: reader-pipeline (HT-15 Markdown/HTML Reader Pipeline)

New capability: an on-demand host-layer pipeline (`lib/reader_pipeline.py`) turning agent output — structured transcript via `agent_tts.sources.base.read_last_agent_message` (base.py:38; `None` → scrollback fallback) or raw scrollback — into sanitized structure-preserving Markdown and GFM-subset HTML with anchors aligned 1:1 with the pinned engine's boundary enumeration. No UI here.

## ADDED Requirements

### Requirement: Formatting-Preserving Sanitization (Smoke: 39a)

The pipeline MUST compose, in order: `strip_ansi` (cleaner.py:224, removes ANSI escape codes) → `redact_secrets` (redact.py:107-119, ordered credential families) → chrome filtering that preserves Markdown structure. Fences (```), table pipes (`|`), heading and list markers MUST survive verbatim. The speech mutations of `clean_agent_text` (cleaner.py:541-565: code-block omission, `|`→` — `, URL→`enlace web`, acronym expansion) MUST NOT reach reader output.

#### Scenario: Chrome stripped, structure kept, speech mutations absent

- GIVEN scrollback with ANSI codes, a fenced code block, a `|`-table, and an https URL
- WHEN the pipeline sanitizes it
- THEN no ANSI escapes remain, fences/pipes/markers and the URL survive verbatim, AND `[bloque de código omitido]`/`enlace web` never appear

### Requirement: Redaction Before Transformation (Smoke: 39b)

Secret redaction MUST complete before any Markdown/HTML transformation. Placeholders MUST appear verbatim in both the HTML text and the anchor mapping; raw secrets MUST NOT appear in either, including inside fenced code.

#### Scenario: Credential inside a code fence

- GIVEN a fenced block containing `token=ab12cd34ef56`
- WHEN HTML and mapping are produced
- THEN both carry the redaction placeholder and never `ab12cd34ef56`

### Requirement: Deterministic Plain-to-Markdown Heuristics (Smoke: 39c)

Unformatted text MUST convert deterministically (identical input → identical output): trailing-`:`/section lines become headings; Unicode bullets (`•◦▪▸`) and numbered items (`1.`, `2)`) become Markdown lists; blank-line clusters become paragraphs. Malformed input MUST degrade to fully-escaped text — never crash, never emit raw HTML.

#### Scenario: Unicode bullets normalize

- GIVEN `• alpha\n• beta`, WHEN plain-to-Markdown runs, THEN output is `- alpha\n- beta`

#### Scenario: Unclosed fence degrades safely

- GIVEN an unclosed code fence, WHEN the pipeline runs, THEN it exits 0 with fully escaped output and zero raw HTML tags

### Requirement: GFM-Subset HTML With Total Escaping (Smoke: 39d)

The converter MUST support h1–h4, paragraphs, fenced code with language tag (`<pre><code class="language-X">`), blockquotes, GFM tables, `ul`/`ol`, bold/italic/inline-code, links. It MUST unconditionally `html.escape` all text and attribute values and MUST NOT pass raw input HTML through. Links MUST allow only `http`/`https`; other schemes render as plain text.

#### Scenario: Script injection neutralized

- GIVEN `<script>alert(1)</script>` in input, WHEN HTML renders, THEN tags appear as escaped text and no `<script>` element exists

#### Scenario: Unsafe link scheme demoted

- GIVEN `[x](javascript:alert(1))`, WHEN HTML renders, THEN no anchor carries a `javascript:` href

### Requirement: Sentence Anchors With Engine-Oracle Parity (Smoke: 39e)

Every text span MUST carry `data-sent-idx`, `data-para-idx`, `id="tts-sent-<idx>"`. Indices MUST match the enumeration pinned in `estimate_boundaries_from_text` (boundaries.py:286-307): paragraphs split on `\n\s*\n+` (stripped, empties dropped); sentences split per paragraph on `(?<=[.!?])\s+`; sentence index 0-based, continuous across paragraphs; paragraph index 0-based. The pipeline MUST return a mapping whose entry `i` corresponds to `data-sent-idx="i"` with the span's redacted text. Parity acceptance MUST run the pinned `agent_tts` package as oracle (`estimate_boundaries_from_text` over the `clean_agent_text`-normalized source), asserting equal count, order, and paragraph mapping; fixtures MUST cover preserved code, tables, links, and acronym expansion (`PR`→`pull request`). Shared-regex claims alone do not qualify.

#### Scenario: Multi-sentence paragraph aligns

- GIVEN a 3-sentence paragraph, WHEN HTML and oracle run, THEN anchor sequence 0,1,2 matches oracle sentences with constant `data-para-idx`

#### Scenario: Normalization drift case aligns

- GIVEN input whose code block contains sentence-splitting punctuation, WHEN compared against the oracle over speech-normalized text, THEN anchor count and paragraph mapping still match

#### Scenario: Mapping mirrors spans

- GIVEN rendered HTML with N spans, WHEN the mapping is inspected, THEN entry `i` text equals span `i` text for all i

### Requirement: Opt-In Host Access Without Contract Change (Smoke: 39f)

Host access MUST be on-demand: a Python bridge via `lib/tts_engine.py` plus a minimal CLI `--render-html <in> <out>`. New user-facing strings MUST exist in both `TT_EN` and `TT_ES`. Surface Contract v1 (`--contract-version` stays `1`), `--render-text`, `--speak`, and default speech behavior MUST remain unchanged.

#### Scenario: render-html produces a file

- GIVEN an input file, WHEN `bin/herdr-tts --render-html in.txt out.html` runs, THEN `out.html` contains anchored HTML and exit is 0

#### Scenario: Existing surface untouched

- GIVEN the hermetic suite, WHEN contract and legacy flags are exercised, THEN version prints `1` and `--render-text`/`--speak` behave as before

### Requirement: Zero-Dependency, On-Demand Operation (Smoke: 39g)

The pipeline MUST use only the Python stdlib plus the already-pinned `agent_tts` package: no new pip installs; `scripts/bootstrap.sh` and its pin untouched. Execution MUST be transient per invocation — no resident daemon process or resident memory.

#### Scenario: Bootstrap and pin untouched

- GIVEN the suite's bootstrap checks, WHEN they run, THEN the pinned-ref check passes with no new dependency entries AND the pipeline runs as a transient process that exits

## Out of Scope (non-goals, not requirements)

Modal UI (HT-16); ntfy/RSS HTML notes; engine (`agent-tts`) changes; full CommonMark/GFM compliance; default speech behavior changes.
