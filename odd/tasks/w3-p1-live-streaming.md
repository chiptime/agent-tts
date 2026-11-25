# W3-P1 — Streaming LLM and TTS Downlink (PRD-06)

Branch: `feat/w3-p1-live-streaming`; base: `35a537a`; runner: pytest from `hosts/herdr/brain` (unit/API: `uv run --extra dev python -m pytest tests/ --ignore=tests/browser --ignore=tests/e2e`; browser and e2e in separate processes).

## Specs
- S1: Streaming de tokens del LLM (GLM) en el backend durante la generación de respuesta en llamada en vivo (`live_call*` / `/ask`), aditivo sobre el contrato síncrono previo.
- S2: Síntesis TTS por oraciones iniciada en el primer límite de frase completa y entrega progresiva sobre protocolo-2 (`/speech/{id}/next`), logrando primer audio audible en ~1–2 s antes de que termine la generación completa.
- S3: Cancelación y flushing intactos (`POST /speech/{id}/cancel` aborta síntesis pendiente y `segmentPlayer.stop()` vacía la cola).
- S4: Bucle de herramientas y approval gate intactos (las herramientas que requieren confirmación nunca se saltan el gate; streaming no modifica las decisiones de autorización).
- S5: Pruebas unitarias e integración en `tests/test_live*` / `tests/test_call_stream.py`.

## Invariantes transversales
- C1: Seguridad y auth intactos; secrets masking outbound intacto (`secrets_mask.py`). El texto hablado sigue pasando por `sanitize_for_speech` (mask en el seam TTS), igual que hoy.
- C2: Repinear `EXPECTED_DIGESTS` en `test_karaoke_harness.py` si se tocan ficheros en `static/` (se toca `app.js` → repineo obligatorio).
- C3: Harnesses browser fail-closed en tráfico no declarado. La anticipación del polling `/next` del cliente se activa SOLO con el capability flag `streaming_ask` en `/call/context`; los stubs que no lo declaran no generan tráfico nuevo.
- C5: Verificador independiente obligatorio al finalizar (lo despacha el lead; yo reporto done con diff + checks).

## Design decisions (2026-11-25)
- PRD open question 2 (endpoint nuevo vs mode flag): resuelta — mismo `POST /ask`; el server decide internamente stream/no-stream por turno; el cliente recibe el hint aditivo `streaming_ask: true` en `GET /call/context` para iniciar el polling `/next` en especulativo apenas despacha `/ask`.
- PRD risk "tool-loop interleaving": streaming SOLO alimenta síntesis cuando la ronda produce contenido (sin deltas de tool_calls); si en una misma ronda aparecen tool_calls después de contenido (patológico), se suprime la alimentación restante y la ronda se trata como tool round (out of scope del PRD, documentado).
- FR-05 chunker: módulo nuevo `sentence_chunker.py`, función pura (`SentenceChunker.feed/flush`), boundaries en `[.!?…]+` + whitespace (guardias: abreviaturas ES/EN, iniciales de una letra mayúscula, code spans por paridad de ticks, URLs) y en `\n` (guardia code span). Determinista, unit-testeable.
- FR-06 degradación: (a) si `create(stream=True)` falla o el provider ignora `stream=True` (fakes sync) → ronda legacy whole-answer, texto completo troceado por el chunker hacia el sink; (b) si el stream muere a mitad con prefijo no vacío → el prefijo ES la respuesta (no se pierde el turno); (c) stream muerto sin contenido → 1 reintento legacy de la ronda.
- Producer nuevo `StreamingSentenceSpeechProducer` (speech.py): cola acotada (SPEECH_MAX_SEGMENTS), worker thread que renderiza secuencialemente con el mismo `speak_answer` seam y fases guardadas rendering→delivering→terminal exactamente una vez. El sink (turn thread) encola; `finish(closer)` añade el closer de approval como última oración hablada (igual que `speakable_answer` hoy); `abort()` para los handlers de error del turno.
- Respuesta /ask conversation-streaming: `shape_ask_response(speak=False)` + espera acotada (`FIRST_SEGMENT_WAIT_S`) por el segmento 0 para `audio_url`; `speech.{status, first_seq, first_is_final, first_sentence_ms, first_segment_ms}` — status `delivering` salvo terminal roto (cancel/failed/degraded) (contrato tts-brain-v2: audio_url puede ser None; los segmentos llegan por `/next`).
- FR-07: `first_sentence_ms` / `first_segment_ms` (ms desde inicio de turno, -1 si no llegó) en `speech` de la respuesta; el cliente los añade al reporte `turn_timing` reason=audio; el log del WS (`call_stream.py`) extiende su línea con ambos campos.
- Cliente (app.js): flag `streaming_ask` en `applySharedContext`; si está activo y el turno es in-call con speech id → `segmentPlayer.start({id, sessionId, first: null})` especulativo tras despachar `/ask`; en la respuesta, si el player ya corre para ese id no se re-inicia (dedupe); ramas no-delivering detienen un player especulativo activo. `speech.js` NO se toca (ya soporta `first: null`).
- Compatibilidad: `BrainLLM.takes_sentence_sink = True` marker (convención `takes_cancel_event`); `ask(sentence_sink=None)` legacy byte-comparable; fakes sin marker → ruta legacy intacta (SentenceSpeechProducer).

## Tasks
- [ ] T1 S1, S2: LLM token streaming + sentence-chunked protocol-2 delivery. Route: inline (S5 chunker unit RED→GREEN first).
  - T1a `sentence_chunker.py` + `tests/test_live_sentence_chunker.py` (FR-05). Commit.
  - T1b `llm.py` streaming round + provider `create_stream` (sync + owned-async) + `tests/test_live_llm_stream.py`. Commit.
  - T1c `speech.py` `StreamingSentenceSpeechProducer` + `server.py` conversation path + `/call/context` flag + timings + `tests/test_live_streaming.py`. Commit.
- [ ] T2 S3, S4: Cancel semantics, tool loop and gate preservation (covered by tests in T1b/T1c: cancel mid-synthesis, gate round + closer hablado, disconnect, replay approve legacy). No separate code unit.
- [ ] T3 S5: Client wiring (app.js speculative start + dedupe + timing stash, C2 digest re-pin) + browser AC1 test + `call_stream.py` turn_timing fields + `test_call_stream.py` timing test. Commit.
- [ ] T4 S1-S5: Full suite runs (unit/API, node --test, browser, e2e M2/M4 sanity) + report done to lead (lead dispatches C5 verification).

## Log
- L1: Hito **W3-P1 (Streaming LLM+TTS en llamada en vivo)** de Ola 3. Rama `feat/w3-p1-live-streaming` en worktree `../agent-tts-worktrees/w3-p1-live-streaming`.
- L2 (2026-11-25): Kickoff fleet — task end-to-end en el tab (investigate, implement, verify). Specs S1-S5 + contratos C1-C5 según brief del lead. Design decisions D1-D10 registradas arriba (resuelven open questions del PRD dentro del scope autorizado).
