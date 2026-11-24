# W3-U3 — One harness source of truth for /session/new and /session/roots

Objective: `POST /session/new` and `GET /session/roots` validate/publish harnesses from ONE shared source (opencode included, closed list kept).
Branch: `feat/w3-u3-session-new` (base `main` 0128877). Delivery: local work-unit commits, no push. Runner: `cd hosts/herdr/brain && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest tests/test_session*.py`.

## Specs

- S1: "Extraer/compartir una única fuente de verdad de harnesses válidos entre `POST /session/new` y `GET /session/roots` (incluyendo `opencode` y conservando validación cerrada)." Closed list = transcript-registry canonical names; aliases (`oa`, `omp`, `agy`) stay refused; auth/audit/roots gate of `/session/*` unchanged (C1); `static/` untouched (C2).
- S2: "Pruebas unitarias RED→GREEN cubriendo consistencia entre ambos endpoints y rechazo de harnesses no permitidos."

## Tasks

- [x] T1 (S1, S2, inline) — new `session_launcher.py` (`launchable_harnesses`, `resolve_launch_harness`), routes `/session/roots`, `/session/new`, `/session/resume` consume it; tests `tests/test_session_harness_source.py`. Commit: a553f28. RED: `ImportError: cannot import name 'session_launcher'` (collection error); GREEN: tests/test_session*.py + test_tools_create_session.py 153 passed; full unit/API suite 4733 passed (browser/e2e not run, no static touched).
- [ ] T2 (S1, S2) — independent verifier verdict per S#: PENDING. The fleet's dedicated verifier (`odd-verifier`) must be dispatched by the lead (this worker may not spawn it); verdicts to be appended to the Log.

Route: inline (small, one understood change, ~60 lines; no writer trigger). Risk: item 3? The closed list and its refusals are byte-identical; no contract changed -> `Risk: none` (see Log for the shared-code check).

## Log

- L1 (2026-11-25, brief): "Unificar validación de harness en `POST /session/new` con `/session/roots`" — task note: "verificación live 19/11 post-restart, POST aceptó harness=opencode que /session/roots no lista".
- L2: Finding at base `0128877`: both routes (and `/session/resume`) already call `server.launchable_harnesses()` (registry canonical names, includes `opencode` and `opencode2`); the divergence does NOT reproduce in code (the live observation predates the shared helper or hit an older build). `session_launcher.py` did not exist; it is created as the single named seam so the invariant is explicit and pinned by tests. No behaviour change expected.
- L3: Differential check: `/session/new`, `/session/roots`, `/session/resume` keep identical status codes/details (`unknown_harness`, `roots_unconfigured`, ...); the pre-existing 144 session tests pass unchanged. `Risk: none` (shared helper `launchable_harnesses` kept identical; only moved). Gap: `opencode2` is listed/launchable but goes through `herdr start_agent` (not the `oa` path) and its resume is unverified; out of scope here.
