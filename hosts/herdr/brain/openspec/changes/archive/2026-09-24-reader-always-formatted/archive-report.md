# Archive Report: reader-always-formatted

## Summary

Mini change closed (MODIFIED delta on `conversation-reader`). The conversation reading surface now renders EVERY turn formatted by default — no expansion gesture involved. "ver más" is purely visual truncation for very long turns. Cold turns keep showing text and upgrade progressively across the existing 5s polls (fail-soft preserved). Implemented via the `general` worker after the SDD dispatcher latched into refusals; planned and applied under the same discipline (strict TDD, RED-first evidence).

## What changed

- `app.js`: rendered snapshot requested in `refreshConversation` (surface load + each 5s refresh); all turns mounted via `renderGlance -> mountReader -> reader.htmlFor`; "ver más" handler is a pure classList toggle.
- `reader.js`: `loadRendered()` (load/refresh mount reusing generation counter + turn_id unit replacement) and `htmlFor()` backed by a `byKey` (role+NUL+text) index; zero `document` references (static test enforced).
- `reader.py`: `READER_MAX_RENDERS_PER_REQUEST` 8 -> 16 (sanctioned by this spec delta per the module docstring rule); over-budget cold turns remain null-pair.

## Final evidence

- pytest **429 passed / exit 0**; node `--test tests/js/` **105/105, exit 0** (verify re-ran both at final tree).
- RED evidence: node 3 fail (`loadRendered is not a function`), pytest 2 fail (new budget test + old literal-8 pin) before implementation.
- Independent verify: 5/5 delta scenarios evidenced (all-turns-on-load, text-first-upgrade, expansion-not-gating, progressive-cold-fill, stale-discard) with test + file:line proof; commit scope clean (nothing foreign swept in); architecture conformance pass (zero document refs, scoped innerHTML, fail-soft invariant, CSP untouched, voice-watchdog block untouched).

## Commits (local master, NOT pushed)

- `451352c` feat(reader): mount formatted turns on surface load and refresh
- `f452d6f` feat(reader): raise per-request render budget to 16
- (this commit) archive + spec sync of the delta into `openspec/specs/conversation-reader/spec.md`

## Residual notes / follow-ups

- **Device visual pass pending** (maintainer): open a conversation on the phone — every turn should render formatted without tapping "ver más". First visit to a long conversation fills progressively (up to 16 cold renders per request, one-time per turn thanks to the content cache).
- **S1 (design note)**: production mounts through the `renderGlance -> mountReader -> htmlFor` adapter while the node suite also covers the `getPairs` branch of `loadRendered` — two mount paths, both tested; keep both green during refactors.
- **CSP routing follow-up** (from the main integration, still open): `GET /` responses serve StaticFiles-style headers without the pinned CSP; revisit in a small server patch.
- **Service restart** required on the running instance for the new static assets + budget to take effect.
- Process note: planning artifacts authored inline by the orchestrator and implementation delegated to a `general` worker due to persistent SDD dispatcher refusals in that session; discipline (strict TDD, RED-first, path-scoped commits) was preserved and independently verified.
