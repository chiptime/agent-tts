# Proposal: Formatted Agent-Status Toast

## Intent

The phone toast that announces agent status/responses ("aviso de estado de agente") shows plain truncated text via `textContent`. The product owner wants it formatted like the reading surface: when the announced text has reader HTML available, the toast renders it (bold, code, links visible even in the excerpt); otherwise it stays exactly as today.

## Approach

- Reuse the existing rendered snapshot: `reader.htmlFor(text)` (content-keyed; cache hit = zero extra renders). No new endpoint, no server changes.
- Toast accepts an optional `html` payload; when present, mount via the same scoped-insertion rules as the reader (pipeline output only, links hardened). When absent (watcher template avisos, renderer unavailable, fail-soft), `textContent` exactly as today.
- Excerpt via CSS (max-height + overflow hidden + ellipsis block): never truncate HTML by hand.
- Security unchanged: scoped container, upstream total escaping, CSP already pinned; the toast inherits `.reader-content` insertion discipline (own class, same guards).

## Scope

In: `app.js` toast path, `reader.js` helper reuse (no structural change), `static/index.html` + CSS for the clipped formatted toast, node tests (formatted mount, plain fallback, no-expansion coupling, zero new document refs). Out: server changes, new dependencies, watcher template changes, live audio sync.

## Rollback

Revert the single commit; toast returns to textContent-only.

## Forecast

~80-140 authored lines incl. tests. Under 400: no exception. Gates: pytest (unchanged suite must stay green) + node (new tests).
