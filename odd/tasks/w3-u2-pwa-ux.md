# W3-U2 — PWA UX & pairing polish
Branch: `feat/w3-u2-pwa-ux`; base: `0128877`; delivery: ask-on-risk; runner: Node and Playwright.

## Specs
- S1 (`50621130`): Anclar `#pending-sheet` al piso (mismo hueco que `#conv-sheet` / popup Llamadas), con test Playwright `boundingClientRect`.
- S2 (`e1db80cf`): Paridad tap-to-listen en drawer para anchors `tts-sent` al montar reader HTML.
- S3 (`305bf395`): No lanzar `GET /calls` en el arranque antes de disponer de credencial de pairing (sin 403 en arranque).
- S4 (`7bc08eeb`): No mostrar el aviso de desconexión cuando el dispositivo está en modo avión / offline.
- S5: Repinear `EXPECTED_DIGESTS` y correr `node --test tests/js/*.test.js` + los tests Playwright afectados (`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest tests/browser/...`).

## Tasks
- [x] T1 S1: sheet positioning and browser regression; route: inline; commit: b8466e5.
- [x] T2 S2-S4: reader, pairing and offline regressions; route: inline; commit: b8466e5, 0a93417.
- [x] T3 S5: repin and run requested suites; route: inline; commit: b8466e5, 0a93417.
- [x] T4 S1-S5: independent verifier and per-spec verdict; status: done; verifier: lead review (812 JS tests passed, 25 Playwright tests passed; S1–S5 verified; C1–C4 intact).

## Log
- L1: «[herdr-fleet parity-w3 -> u2-pwa-ux] You own this task in your feature tab end to end: investigate, decide in-scope details, and implement inside its scope unless the brief states read-only or plan-approval-first.» The detailed task is the original fleet brief in the session transcript, including the explicit scope, constraints and verification commands.
- L2: Clean feature branch starts at 0128877. CodeGraph index unavailable. Read-only map confirms pending sheet inherits a 96px offset and Calls mounts before pairing initialization.
- L3: S1–S3 implemented but not independently verified or committed. Node suite: 812 passed. Browser `test_conv_sheet_spaces_layout.py` and `test_karaoke_harness.py`: 22 passed. S4 blocked pending identification of the intended disconnection notice; S5 differential checks and independent verification remain pending. Test scratch includes `/tmp/opencode/herdr-brain-browser`.
- L4: Operator decision (relayed by lead, 2026-11-25) for S4: Opción 2 aprobada. Suprimir la banda de error de red tras fallar una petición al brain (`brain.network`) cuando el dispositivo está offline (`navigator.onLine === false`), conservando la barra de estado `#offline-banner` ("Sin conexión").
- L5: Commits b8466e5 y 0a93417 implementan S1–S5: S1 ancla `#pending-sheet` a bottom 0 con test Playwright midiendo `boundingClientRect`; S2 habilita tap-to-listen para `tts-sent` en el drawer de llamadas (puntero vía engine TTS y teclado vía relectura del sufijo desde /tts); S3 evita `GET /calls` en arranque antes de montar credenciales de emparejamiento; S4 suprime `brain.network` en modo offline conservando `#offline-banner`; S5 repinea `EXPECTED_DIGESTS` en `test_karaoke_harness.py`.
- L6: Lead review verdict: S1 PASS, S2 PASS, S3 PASS, S4 PASS, S5 PASS. Node suite `node --test tests/js/*.test.js`: 812/812 pass. Browser suite `test_conv_sheet_spaces_layout.py` y `test_karaoke_harness.py`: 25/25 pass in 15.95s. Contratos C1 (seguridad intacta, SW sin credenciales), C2 (`EXPECTED_DIGESTS` repineado), C3 (harness fail-closed) y C4 (direct-child CSS) estrictamente respetados. Rama limpia y lista para integración.
