# Archive Report: toast-formatted-avisos

## Summary

Closed: the phone agent-status toast renders the announced response as reader HTML when a rendered snapshot exists for that exact text (content-keyed cache hit, zero extra renderer invocations); watcher template avisos and any failure keep the exact plain-textContent behavior. Excerpt is CSS-only (max-height + overflow, payload never string-cut). Implemented via the `general` worker (SDD dispatcher unavailable this session); strict TDD preserved.

## What shipped

- New UMD `static/toast.js` (+72): mounts formatted announcement bodies via `reader.mountTurn` (same scoped-insertion discipline; links hardened).
- `app.js`: `speakText` attaches `reader.htmlFor("assistant", text)` (full pre-truncation text, cache hit); `pumpAudio` forwards it; plain path untouched.
- `index.html`/CSS: `#toast .toast-content` clip container (max-height 8em, overflow hidden).
- `server.py` +8 (justified deviation from "server changes out"): version-stamped `/toast.js` route + no-cache header — without it the asset fell through to StaticFiles with no Cache-Control (stale-JS risk), verified with TestClient.

## Evidence

- RED: node 1 fail (`Cannot find module toast.js`), 105 pass. Final: pytest **429 passed / 0 failed**, node **109/109** (4 new: formatted mount, plain fallback, css-excerpt-only, links hardened).
- 8/9 tasks done; task 3.2 (device pass) maintainer-owned, pending.

## Commits (local master, NOT pushed)

- `d37e9d5` feat(toast): render formatted agent-status avisos from reader snapshot
- (this commit) archive + delta sync (ADDED requirement appended to openspec/specs/conversation-reader/spec.md)

## Residual notes

- Device pass CONFIRMED by maintainer (2026-09-25): "funciona perfecto".
- Service restart required (new route + assets).
- Deviations documented: htmlFor real signature is (role, text); no 4KB substring cap (spec forbids payload mutation — CSS-only excerpt); no unconditional ellipsis.
