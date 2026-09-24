# Archive Report: reader-html-integration

## Summary

Change `reader-html-integration` is closed. herdr-brain now renders the conversation reading surface as formatted Markdown (headings, emphasis, code, lists, tables) with sentence anchors, consuming the stable upstream herdr-tts `reader-pipeline/anchors@1` contract. Strict fail-soft to plain text is preserved in every failure path; nothing about the existing speech or transcript behavior changed.

## What shipped

- **Backend**: `src/herdr_brain/tts.py::render_html` CLI wrapper (`--render-html <in> <out> --map <map>`, bounded invocation), `src/herdr_brain/reader.py` (content-addressed cache: length-prefixed SHA-256 keys over an unambiguous (text, lang) serialization, LRU 128, single JSON envelope per entry published via `os.replace`, corrupt = miss, semaphore 2 + budget 8 renders/request + 256-entry failure memo), `GET /conversation/{pane_id}/rendered` endpoint (per-turn `{turn_id, role, text, html, map}`; `html`/`map` null together on failure; `text` always present).
- **Security**: Content-Security-Policy pinned on the PWA index response (`default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'`); hardened reader links (`target="_blank" rel="noopener noreferrer"`); no inline handlers; scoped `innerHTML` insertion restricted to the server reader payload inside dedicated reader containers.
- **Client**: dependency-free UMD `src/herdr_brain/static/reader.js` (generation counter for stale-snapshot discard, replacement by `turn_id`, sentence selection via `[data-sent-idx]` incl. `.tts-sent-cont` continuations, zero `document` references — injected DOM surface), scoped CSS, versioned `/reader.js` route, wiring on "ver más" expansion only.
- **Config**: `Settings` gained reader fields appended WITH defaults (frozen dataclass constraint): `reader_timeout_s` (30), `reader_cache_dir`, `READER_PROFILE_VERSION` namespace constant.

## Final evidence

- pytest: **424 passed / 0 failed, exit 0**; node `--test tests/js/`: **102 pass, exit 0** (both at final tree, no flakes; verify re-ran clean).
- Live E2E through the real `herdr-tts --render-html` binary: reproduced by verify — 0.235 s render, valid `reader-pipeline/anchors@1` sidecar, span invariant held.
- 14/14 spec requirements evidenced (verify-report requirement_coverage); 0 CRITICAL, 0 WARNING findings.

## Deviations (all judged conformant by verify)

1. Size: ~2,390 authored lines (~1,300 tests) vs initial ~545-665 approval — **maintainer re-approved** the size:exception at the real figure.
2. Task 5.6 rollback rehearsal skipped as maintainer-confirmed; per-slice rollback remains documented in proposal/design (revert slice commit; Slice C alone restores textContent behavior).
3. Traversal RED test asserts routing-level 404 (stronger than the tools-level observation originally sketched).

## Phase-6 confirmations (apply-time evidence)

- Budget null-cause (renders over budget 8 → `html: null`) documented in `reader.py` docstring; deferral-as-null kept (fail-soft superset).
- `READER_PROFILE_VERSION` bump documented as a release step (module docstring + README deployment section).
- `reader_timeout_s=30` kept: real CLI measured 0.25-0.28 s (p95 ≈ 0.28 s) on a 7,692-char / 64-sentence turn (~100× margin).

## Commits (implementation, local master, NOT pushed)

- `9df521c` feat(reader): rendered-conversation backend with content-addressed cache
- `96f2e7a` feat(reader): pinned Content-Security-Policy on the PWA index
- `5e857c6` feat(reader): formatted reading surface in the PWA client
- `d85cd28` docs(reader): confirm open questions with apply-time evidence

## Residual notes / follow-ups

- **S3 re-test**: the verify spot-check observed the real renderer omitting a leading heading once; upstream herdr-tts commit `76aedd6` fixed heading preservation and its demo shows `<h1>` present — re-test the E2E against current herdr-tts before trusting S3 as real. Informational; not a brain-side defect.
- **Phone visual pass pending** (inherently human): tap "ver más" on a device, confirm formatted render + sentence selection.
- **Service restart required** for CSP/cache changes to take effect on a running instance.
- Suite drift guard: any `static/*.js` change must run BOTH gates (`pytest` + `node --test tests/js/`).
- Sync v1 is static by design: no live audio highlighting (sidecar has no timestamps); revisit when upstream ships timing metadata.
