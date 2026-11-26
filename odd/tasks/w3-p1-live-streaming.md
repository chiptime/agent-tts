# W3-P1 — Streaming LLM and TTS Downlink (PRD-06)

Branch: `feat/w3-p1-live-streaming`; base: `35a537a`; runner: pytest from `hosts/herdr/brain` (unit/API: `uv run --extra dev python -m pytest tests/ --ignore=tests/browser --ignore=tests/e2e`; browser and e2e in separate processes; JS: `node --test tests/js/`).

## Specs
- S1: Streaming de tokens del LLM (GLM) en el backend durante la generación de respuesta en llamada en vivo (`live_call*` / `/ask`), aditivo sobre el contrato síncrono previo.
- S2: Síntesis TTS por oraciones iniciada en el primer límite de frase completa y entrega progresiva sobre protocolo-2 (`/speech/{id}/next`), logrando primer audio audible en ~1–2 s antes de que termine la generación completa.
- S3: Cancelación y flushing intactos (`POST /speech/{id}/cancel` aborta síntesis pendiente y `segmentPlayer.stop()` vacía la cola).
- S4: Bucle de herramientas y approval gate intactos (las herramientas que requieren confirmación nunca se saltan el gate; streaming no modifica las decisiones de autorización).
- S5: Pruebas unitarias e integración en `tests/test_live*` / `tests/test_call_stream.py`.

## Invariantes transversales
- C1: Seguridad y auth intactos; secrets masking outbound intacto (`secrets_mask.py`). Requests al provider siguen enmascaradas (`_masked_for_provider`); el texto hablado sigue pasando por `sanitize_for_speech` en el seam TTS.
- C2: `EXPECTED_DIGESTS` repineado en `test_karaoke_harness.py` (app.js: `ebc9171a…`).
- C3: Harnesses browser fail-closed: el polling especulativo de `/next` solo se activa con el capability flag `streaming_ask` en `/call/context`; los stubs que no lo declaran no generan tráfico nuevo.
- C4: Respetado (no se listó en el doc original; ningún cambio de contrato roturo: todo aditivo).
- C5: Verificador independiente obligatorio al finalizar — lo despacha el lead del fleet (reportado `done` con diff y checks).

## Design decisions (final)
- D1 (PRD open question 2): mismo `POST /ask`; el server decide stream/no-stream por turno (marker `BrainLLM.takes_sentence_sink` + `ask(sentence_sink=)`); el cliente recibe el hint aditivo `streaming_ask: true` en GET y POST `/call/context`.
- D2 (PRD §6 tool-loop): solo la ronda de respuesta final alimenta el sink; una ronda con deltas de tool_calls suprime la alimentación (caso patológico contenido→tools conserva el prefijo ya hablado: riesgo documentado en el PRD).
- D3 (FR-05): `sentence_chunker.py`, puro y determinista. Guardias: abreviaturas ES/EN, iniciales de una letra, code spans (paridad de ticks), URLs, y bloques blindados `-----BEGIN…-----END` que quedan como UNA oración (C1: el camino batch enmascara el texto completo antes de trocear; el stream enmascara por oración en el seam TTS).
- D4 (FR-06): falla el setup del stream o el provider ignora `stream=True` → ronda legacy whole-answer troceada al sink; muere con prefijo usable → el prefijo ES la respuesta; muere sin contenido → 1 reintento legacy.
- D5: `StreamingSentenceSpeechProducer` (cola acotada `SPEECH_MAX_SEGMENTS`, worker propio, fases guardadas, `finish(closer)`, `abort()` sin fingir `complete`, timings FR-07). Respuesta: espera acotada `FIRST_SEGMENT_WAIT_S` por el segmento 0; `speech.{status, first_seq, first_is_final, first_sentence_ms, first_segment_ms}`.
- D6 (cliente): `serverStreamsAsk` desde `applySharedContext`; arranque especulativo `segmentPlayer.start({first:null})` al despachar `/ask` SOLO si `inCall && speech id && !keepLiveCapture`; dedupe en la respuesta (no reinicia un player ya polleando); ramas no-delivering detienen un player especulativo.
- D7 (S7 × audio temprano, corrección posterior a 7abc1eb): un dispatch de rejoin en vivo (`keepLiveCapture`) mantiene la captura armada durante thinking y su regla de eco exige captura abajo antes de cualquier audio → esos turnos NO reproducen temprano (D3 del PRD-06: "never cutting the speaker wins over speed"). Mientras un ask sigue generando detrás de su audio temprano, la llamada nunca rearma el micrófono: `earlyAskJob`; `ask()` rechaza un segundo turno; `onAudioEnded`/`stopAudio` devuelven la llamada a `thinking`; el finalizer del ask limpia y rearma.
- D8 (FR-07): timings solo se guardan si el turno ya reporta timings (sin reporte nuevo para turnos que no lo tenían); el log WS (`call_stream.py`) añade `first_sentence_ms`/`first_segment_ms`.
- Compatibilidad: dobles sin marker → ruta legacy intacta (`SentenceSpeechProducer`); `ask()` sin `sentence_sink` es byte-idéntico.

## Tasks
- [x] T1 S1, S2 — `d98d219` (chunker), `a87d357` (llm stream), `05495f9` (producer + /ask + flag + timings), `6197fa7` (chunker: bloques blindados, C1). Route: inline.
- [x] T2 S3, S4 — cubierto por tests en `a87d357`/`05495f9` (cancel mid-synthesis, gate + closer, disconnect, suites legacy verdes).
- [x] T3 S5 (cliente) — `7abc1eb` (arranque especulativo, dedupe, timings, test AC1), `1cb5959` (pins `/call/context`), `e8908d9` (disciplina S7/micrófono + 2 tests browser, digest repineado).
- [x] T4 — suites + reporte `done` al lead (m5). C5 verifier: pendiente de despacho del lead.

## Verification (evidence)
- Unit: `test_live_sentence_chunker.py` 22/22; `test_live_llm_stream.py` 9/9; `test_live_streaming.py` 9/9 (×5 estable).
- Python full suite (en 7abc1eb): 4792 pass, 2 fail = pins exactos de `/call/context` → corregidos en `1cb5959`; tras eso solo cambiaron app.js y tests → cubierto por lotes: 203 pass (`test_call_context`, `test_call_stream`, `test_live_*`, `pwa_assets`, `conversation_cancel`, `ask_split`, `speech_dispatch`) y 251 pass (chunker, llm, masking).
- JS: `node --test tests/js/` 812 pass.
- Browser: karaoke fragments 50/50 (3 W3-P1: AC1 first-play antes de la respuesta; hueco entre segmentos no rearma micrófono; Detener conserva el turno y difiere el micrófono — RED observado antes del fix), harness 12/12 (digest), barge_in/desktop_call/final_handover_guard/call_turns/call_session_browser/live_handover_regression/handover_correction_interleaves/announcement_priority/glance_sentence_replay 138 pass, voice_dialog/independent_* 82 pass.
- e2e real server + PWA (flag activo): `test_m2_stream.py` pass; `test_m4_fallback.py` 3 fail — idéntico en base `35a537a` (verificado en worktree scratch aislado; ese camino va sin llamada y es igual al de la base).
- `gentle-ai review mode status`: off (decidido por clone_local) → sin review nativa; assess informativo: medium, `review_due: false` (under_budget).

## Known limitations / escalated
- G1 (DECISIÓN, cross-feature PRD-05 × PRD-06): en turnos de rejoin del motor en vivo no hay audio temprano; "primer audio audible antes de que termine la generación" se cumple solo en dispatches sin rejoin (motores servidor/navegador; vivo sin watch de rejoin). Habilitarlo requiere acortar la ventana de rejoin al primer audio (regla de eco S7).
- G2: V3 (validación manual en Pixel) no realizada en este tab.
- G3: ronda patológica contenido→tool_calls conserva el prefijo hablado (riesgo documentado en PRD §6).
- G4: replay manual (tap en reader / sufijo del drawer) durante audio temprano que termine antes de la respuesta rearma el micrófono con el ask en vuelo (comportamiento preexistente del fin de replay; no abordado).
- Baseline failures (preexistentes en base, no regresión): `tests/browser/test_live_engine.py::test_socket_failure_falls_back_to_servidor_with_banner[chromium]`; `tests/e2e/test_m4_fallback.py::{test_fake_providers_e2e,test_cancel_during_fallback_zero_further_attempts,test_no_duplicate_submissions}[chromium]`.

## Risk
Risk: item 3 (contratos aditivos que otros consumen: respuesta `/ask` conversation, `/call/context`) + item 4 (thread de producer nuevo + disciplina de micrófono bajo ask en vuelo).

## Log
- L1: Hito **W3-P1 (Streaming LLM+TTS en llamada en vivo)** de Ola 3. Rama `feat/w3-p1-live-streaming` en worktree `../agent-tts-worktrees/w3-p1-live-streaming`.
- L2 (2026-11-25): Kickoff fleet — task end-to-end en el tab. Specs S1-S5 + contratos C1-C5 según brief del lead. Design decisions registradas arriba.
- L3 (2026-11-26): Lead confirma continuar. T1c evidence: 9/9 integración estable ×5; lote diferencial 333/333. Learned: el worker del producer marca cancelled al observar el cancel_event; el test de disconnect debe joinear el thread del producer antes de afirmar fase.
- L4 (2026-11-26): Auditoría S3 del cliente reveló que el audio temprano rompía dos invariantes S7 (captura armada en rejoin; rearme de micrófono en el hueco entre segmentos con el ask en vuelo). Corregido en `e8908d9` con tests browser RED→GREEN. Learned: en el harness karaoke `#state-pill` queda oculto (drawer abierto) y su clase es obsoleta — usar `recognitions` y `#pause-btn` como sondas de estado. RDD off en este clone.
- L5 (2026-11-26): Reporte `done` al lead (m5) con HEAD `e8908d9` y gaps G1-G4; luego hardening C1 del chunker (`6197fa7`) y sync de este doc.
