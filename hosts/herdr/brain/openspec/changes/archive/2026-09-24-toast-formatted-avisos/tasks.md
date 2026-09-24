# Tasks: toast-formatted-avisos

Strict TDD. Gates: pytest (must stay green, no new pytest tests strictly required) + node --test (new RED tests). Forecast ~80-140 lines. No size exception needed.

## Phase 1: RED (node)

- [x] 1.1 `tests/js/toast.test.js` (new): `renders_formatted_html_when_snapshot_available` — toast with html payload mounts formatted content inside the toast container (injected DOM).
- [x] 1.2 `tests/js/toast.test.js`: `falls_back_to_textcontent_without_html` — no payload → plain text exactly as today (regression guard for watcher avisos).
- [x] 1.3 `tests/js/toast.test.js`: `css_excerpt_only` — formatted toast uses the clip classes; payload string never mutated (no regex/substring on html).
- [x] 1.4 `tests/js/toast.test.js`: `links_hardened_in_toast` — mounted links carry target=_blank + rel=noopener noreferrer.

## Phase 2: GREEN

- [x] 2.1 app.js: toast show path accepts optional html (from `reader.htmlFor(text)` when available); plain path untouched; watcher avisos unchanged.
- [x] 2.2 index.html + CSS: toast formatted-content container with max-height/overflow clip classes.
- [x] 2.3 reader.js: expose a minimal `htmlFor(text)` reuse helper if not already public (no structural change; zero document refs preserved).

## Phase 3: Verification

- [x] 3.1 Both gates green; existing reader + toast-related tests untouched-green.
- [ ] 3.2 (maintainer) Device pass: trigger an agent response aviso → formatted toast; trigger a watcher aviso → plain toast.
