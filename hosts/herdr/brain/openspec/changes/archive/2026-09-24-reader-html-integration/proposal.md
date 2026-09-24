# Proposal: Formatted Conversation Reader

## Intent

Render the conversation text users listen to as formatted Markdown in `#glance-turns`, preserving headings, emphasis, code, lists, tables, and sentence anchors. Today the reading surface uses plain `textContent`. The stable upstream `reader-pipeline/anchors@1` contract makes structured reading possible without introducing a browser Markdown parser or changing phone audio playback.

This is a progressive enhancement: reader failures must never break text answers or speech. No existing fail-soft guarantee is tightened.

## Scope

### In Scope
- A server-side `tts.py` `render_html` wrapper using the existing CLI surface with `--render-html INPUT OUTPUT --map MAP` and bounded execution.
- SHA-256 content caching over an unambiguous serialization of turn text and language, with a bounded memory LRU and disk artifacts.
- One rendered-conversation endpoint with per-turn HTML, validated anchor metadata, and original text for fallback.
- A dependency-free UMD `reader.js` module mounted in dedicated reader containers inside `#glance-turns`; scoped styling and manual sentence selection, including continuation fragments.
- Scoped HTML insertion, hardened links, an index-response CSP, and strict plain-text fallback.
- Mocked Python tests and independent Node tests covering rendering, caching, security, and failure paths.

### Out of Scope
- Live audio synchronization or audio-driven highlighting: the sidecar has no timestamps, and the phone plays MP3s client-side. Revisit only when upstream provides timing metadata.
- Host player IPC proxying, playback seeking, or estimated sentence timing.
- Markdown editing, a new full-screen reader, or changes to the call drawer and conversation switcher.
- New runtime or development dependencies and changes to the upstream repository.

## Capabilities

### New Capabilities
- `conversation-reader`: On-demand formatted conversation rendering, cached server artifacts, sentence selection, safe PWA presentation, and fail-soft text fallback. Create `specs/conversation-reader/spec.md` under this change during the spec phase.

### Modified Capabilities
None. `openspec/specs/` is currently empty; this is the first change. Existing conversation and speech behavior remains compatible.

## Approach

### Pinned API

`GET /conversation/{pane_id}/rendered` (URL-encoded pane identifier; no query parameters in v1) returns HTTP 200 JSON for a resolved conversation:

```json
{
  "pane_id": "w1:p2",
  "session_id": "ses_example",
  "turns": [
    {
      "turn_id": "opaque-snapshot-turn-id",
      "role": "assistant",
      "text": "# Result\nCompleted.",
      "html": "<h1>...</h1>",
      "map": {"version": 1, "contract": "reader-pipeline/anchors@1"}
    }
  ]
}
```

The illustrated `map` is abbreviated: successful responses contain the complete upstream sidecar. `html` and `map` are both nullable and become `null` together on renderer, cache-validation, timeout, or sidecar failure; `text` remains available. Metadata travels as JSON, never as an inline script. There is no `map_url` or separate sidecar endpoint in v1. Empty conversations return `turns: []`; preserve existing conversation resolution/error semantics rather than converting unrelated request errors into reader failures.

Turns retain source order. `turn_id` identifies a turn within the returned conversation snapshot, not a durable transcript ID: current `Turn` records expose only role and text. Derive deterministic IDs from the resolved session, snapshot position, role, and content; duplicate text must not collide within a snapshot. Replace the snapshot as a unit and discard stale responses after pane/session changes rather than joining independent polling responses by array position.

### Rendering and Cache

Pass the complete available turn text as a temporary UTF-8 document to the CLI, preserving leading headings; clean up temporary inputs on every outcome. Use an injectable renderer seam, matching existing server test patterns. Do not import upstream private Python modules or invoke its daemon/player.

Use language `es` and the full-document reader profile (`max_chars=0`, `summarize=false`) in v1; validate the sidecar engine metadata rather than claiming speech-index equivalence. Cache under `settings.audio_dir.parent / "reader_cache"` with a memory LRU of 128 entries and paired HTML/map disk artifacts. Include the text and language in the SHA-256 key and isolate artifacts by reader profile/contract version. Invalidate the namespace on renderer upgrades. Publish complete pairs atomically, bound disk retention and concurrent rendering, and prevent repeated failing polls from spawning unbounded subprocesses. Missing or corrupt artifacts are cache misses, not application failures.

Request formatted content only for the viewed reading surface, not every background herd refresh. Display existing text immediately while rendering is pending. Warm repeated requests for unchanged turns reuse artifacts; changed text produces a new key.

### PWA Safety and Presentation

Load `reader.js` as a versioned asset using existing no-cache discipline. Restrict `innerHTML` to dedicated `.reader-content` descendants of `#glance-turns` and only insert validated upstream renderer output. Upstream escapes raw HTML and permits only HTTP(S) links; additionally harden all inserted links with `target="_blank"` and `rel="noopener noreferrer"`. Keep fallback assignments on `textContent`, never insert raw Markdown as HTML.

Add an index-response CSP based on `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'`. Verify existing scripts, audio, SSE, and PWA loading remain functional. CSP is defense in depth, not a substitute for upstream escaping and safe insertion.

Scope sentence lookup to the owning turn, since upstream IDs restart at `tts-sent-0` per document. Handle primary anchors on containers, continuation fragments, empty virtual spans, and both `exact` and `coverage` alignment. Manual selection must never imply playback synchronization. Style headings, bold text, code, lists, and horizontally scrollable tables within the reader boundary. Keep any UI labels Spanish and code/tests English.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/herdr_brain/tts.py` | Modified | Fail-soft HTML/map CLI wrapper and cleanup. |
| `src/herdr_brain/server.py` | Modified | Injectable renderer, cache, rendered endpoint, CSP. |
| `src/herdr_brain/static/reader.js` | New | UMD mounting, anchor selection, link hardening, fallback. |
| `src/herdr_brain/static/app.js` | Modified | On-demand snapshot loading and stale-response protection. |
| `src/herdr_brain/static/index.html` | Modified | Versioned reader asset and scoped CSS. |
| `tests/conftest.py`, `tests/test_tts.py`, `tests/test_server.py` | Modified | CLI stub, failure/cache/API/CSP coverage. |
| `tests/js/reader.test.js` | New | Anchor, insertion, selection, and fallback coverage. |

## Risks

| Risk | Severity / Likelihood | Mitigation |
|------|-----------------------|------------|
| CPU thrash from repeated polls or concurrent cold misses | High / High | Lazy loading, content-hash LRU/disk reuse, bounded concurrency and failed-render retry behavior. |
| XSS at the first client HTML insertion boundary | High / Medium | Upstream escaping and scheme restriction, scoped insertion, hardened links, CSP, malicious-input regression tests. |
| Missing CLI, exit 1/2/3, timeouts, corrupt maps or unwritable cache | Medium / Medium | Bounded wrapper, warnings without transcript payloads, nullable rendered fields, immediate `textContent` fallback. |
| Wrong-turn selection, repeated DOM IDs, or stale cached anchors | Medium / Medium | Per-turn lookup, snapshot replacement, profile/version invalidation; no audio correspondence claim. |
| CSP or asset changes disrupt existing PWA behavior | High / Medium | Preserve versioning/no-cache; exercise audio, SSE, page loading and fallback before release. |
| Python/Node suite drift | Medium / Medium | Require both independent suites; do not reuse exploration test counts as current proof. |
| Persisted transcript-derived content grows or leaks | Medium / Medium | State-directory-only cache, bounded retention, restrictive access, temporary-input cleanup; never log content. |
| Review budget exceeded | High / High | Resolve delivery slicing with the parent before apply; do not omit tests to meet the threshold. |

## Rollback Plan

Revert the reader client integration and its versioned asset/CSS together to restore the existing `textContent` reading surface; then revert the rendered endpoint/cache wrapper and CSP addition if necessary. Redeploy the previous build through the normal deployment workflow and reload the PWA, retaining the existing no-cache policy. Verify `/conversation`, `/ask`, `/tts`, client MP3 playback, and both test suites. Reader cache files are disposable: remove only `reader_cache` when safe, never transcript stores or audio files. No data migration or upstream rollback is required. Independent commits should allow the client enhancement to be reverted while unused backend support remains harmless.

## Dependencies

- Existing `herdr-tts` CLI supporting whole-document `--render-html` plus `--map` and `reader-pipeline/anchors@1`; absence must degrade only formatted reading.
- No new packages. Verified `pyproject.toml`, README, and `openspec/config.yaml`: the repository uses Python/FastAPI and vanilla JS with Node's built-in runner. These sources do not state a blanket dependency ban; zero new dependencies is an explicit constraint of this change, achievable with current libraries and standard-library caching/filesystem tools.
- Reference: authoritative `exploration.md` and read-only `/home/bruno/Code/personal/herdr-tts/docs/reader-pipeline-contract.md`.

## Changed-Lines Forecast

| Work | Estimated authored additions + deletions |
|------|-----------------------------------------|
| `tts.py` wrapper | 40–60 |
| Server endpoint and cache | 80–120 |
| `reader.js` | 100–150 |
| Python and JS tests | 150–250 |
| Scoped CSS | 40–60 |
| App wiring, asset references, CSP integration overhead | 30–60 |
| **Implementation subtotal** | **440–700 (planning estimate: 570)** |

The exploration's original components alone total 410–640 lines. Documentation/spec artifacts add further authored lines beyond this implementation estimate. The 400-line budget is therefore crossed, not merely approached. Under `ask-on-risk`, proposal/spec work can proceed, but delivery needs parent resolution before oversized apply. Recommend independently reviewable backend-contract/cache and frontend-integration slices, each carrying its own tests and rollback; this is a recommendation, not consent to create PRs.

## Success Criteria

- [ ] The pinned endpoint returns ordered turns and validated full sidecars; rendering failures preserve text with nullable rendered fields.
- [ ] The viewed turn displays headings, bold, code, lists, and tables, with sentence selection contained to that turn, including continuation fragments.
- [ ] Warm identical requests invoke no renderer; text changes miss the cache; corrupt disk entries and concurrent cold requests remain bounded and fail-soft.
- [ ] Missing backend, exit 1/2/3, timeout, invalid sidecar, network failure, and client insertion failure never erase a readable message or break speech/text answers.
- [ ] Malicious Markdown cannot execute scripts; inserted links are hardened; CSP and versioned asset loading preserve existing PWA behavior.
- [ ] `.venv/bin/python -m pytest -q` and `node --test tests/js/` pass with mocked subprocesses and new regression cases; no new dependencies are introduced.
- [ ] No live synchronization, player IPC calls, or inferred timestamp behavior is introduced.
- [ ] The parent resolves the above-400-line delivery risk before implementation begins.
