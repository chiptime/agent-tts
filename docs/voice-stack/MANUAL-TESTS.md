# MANUAL-TESTS — Features del paquete voice-stack y cómo probarlas

> **Propósito:** manual de verificación para el operador. Cubre las features de los
> cuatro hitos en tres capas: **automatizada** (comandos exactos, ya ejecutados con
> evidencia en `~/.local/state/voice-stack-runs/20261001T084919Z-vs2c/`), **manual
> funcional** (curl/navegador/CLI, reproducible en tu máquina) y **manual física**
> (EXECUTION §8 — la única que ningún test puede sustituir).
>
> **Estado del paquete al escribir esto:** `automated_complete` (VS0→VSX, 2026-10-01).
> Este documento se añade DESPUÉS del cierre: cambia el inventario de `docs/voice-stack/`
> (identidad por hashes), no invalida evidencia ya registrada.

---

## Ruta rápida (15 minutos, happy path)

1. Arranca el stack del worktree (ver §Arranque) con `HERDR_TTS_HOME` apuntando al
   **worktree** → host protocolo-2.
2. Abre la PWA desde el teléfono (`http://<ip-del-pc>:8741`) y haz una pregunta larga
   → **el primer segmento suena en segundos**, no cuando termina todo (Hito 2).
3. Pulsa **Detener** a mitad de respuesta → el audio del teléfono para YA y el render
   server-side se aborta (Hito 1+2 integrados).
4. Deja que un agente termine en un panel vigilado mientras suena otra respuesta → el
   anuncio **no interrumpe**, queda pendiente y suena después (Hito 3).
5. (Opcional) configura `fallback.json` → si el proveedor primario falla, el secundario
   entrega sin repetir lo ya oído (Hito 4).

---

## Arranque del stack (desde el worktree)

El checkout canónico `~/Code/personal/agent-tts` **no se toca**; todo corre desde el
worktree. Dos terminales:

**Terminal 1 — brain** (realiza el render vía el host que le digas):

```bash
cd /home/bruno/Code/personal/agent-tts-worktrees/voice-stack/hosts/herdr/brain
export GLM_API_KEY="<tu clave>"                       # LLM real; sin clave, /ask responde 503
export HERDR_TTS_HOME=/home/bruno/Code/personal/agent-tts-worktrees/voice-stack/hosts/herdr/tts-plugin
export HERDR_BRAIN_HOST=0.0.0.0                        # para abrirlo desde el teléfono
export HERDR_BRAIN_PORT=8741
.venv/bin/python -m herdr_brain.server
```

**Terminal 2 — nada que arrancar para el teléfono** (el render telefónico es un
subprocess por solicitud). El **daemon del PC** es tu instalación habitual
(`~/.local/share/herdr-tts`). Ojo con este detalle:

> ⚠️ El venv del host (`~/.local/share/herdr-tts/venv`) tiene `agent_tts` instalado
> **editable apuntando al CANÓNICO**. Para que el host use el MOTOR del worktree
> (imprescindible para probar el fallback del Hito 4), arranca cualquier comando del
> host así:
>
> ```bash
> export WT=/home/bruno/Code/personal/agent-tts-worktrees/voice-stack
> PYTHONPATH="$WT/engine/src:$WT/hosts/herdr/tts-plugin/lib" \
>   ~/.local/share/herdr-tts/venv/bin/python "$WT/hosts/herdr/tts-plugin/lib/pending_queue.py" <subcomando>
> ```
>
> (Los gates del bucle usaron exactamente ese override.)

**Conmutador v1/v2 (negociación):** `HERDR_TTS_HOME` decide el protocolo:
- Worktree → host **protocolo-2** (`--render-text-segmented`, audio incremental).
- Canónico (`~/Code/personal/agent-tts/hosts/herdr/tts-plugin`) → host **v1-only**:
  las preguntas identificadas caen a fichero-completo con el marcador visible
  `speech.degraded: "segmented-unavailable"` y el audio llega completo al final.

**PWA en el teléfono:** mismo Wi-Fi/LAN → `http://<ip-del-pc>:8741`. Chromium con el
flag estándar de autoplay ya lo maneja la página; el primer tap habilita audio si el
navegador lo pide.

---

## Hito 1 — Cancelación de habla por solicitud

| # | Feature | Cómo probarla (manual) | Esperado | Automatizado |
|---|---------|------------------------|----------|--------------|
| 1.1 | Registro del job ANTES del LLM | `curl -s -XPOST localhost:8741/ask -H 'content-type: application/json' -d '{"text":"¿qué puedes hacer?","session_id":"s1","speech_request_id":"manual.t1.0001","speech_cancel_token":"token-manual-0001"}'` | Respuesta 200 con `speech:{"id":"manual.t1.0001","status":...}`; el id existía desde antes de que el LLM respondiera | `tests/test_speech_registry.py` (`test_ask_registers_speech_job_before_llm`) |
| 1.2 | Id duplicado activo ⇒ 409 | Repite el curl de 1.1 con el MISMO id mientras el job sigue activo | HTTP 409 | `test_duplicate_active_409` |
| 1.3 | Registro lleno ⇒ text-only | (Hard de provocar a mano: 32 jobs activos) | `speech:{"status":"degraded","reason":"registry-full"}`, SIN audio nuevo, respuesta textual intacta | `test_registry_full_text_only_no_audio` |
| 1.4 | Cancel con capability token | Durante un render: `curl -XPOST localhost:8741/speech/manual.t1.0001/cancel -d '{"session_id":"s1","speech_cancel_token":"token-manual-0001"}'` | `{"status":"cancelled"}`; el render aborta (TERM→KILL al pgid propio), sin MP3 parcial en `HERDR_BRAIN_AUDIO_DIR` | `tests/test_speech_cancel.py`, `tests/test_tts.py` (`test_render_cancellable_terminates_pgid_and_deletes_partial`) |
| 1.5 | Idempotencia y honestidad | Repite el cancel; prueba token erróneo; prueba id inexistente | 2º cancel ⇒ `already-complete`; token mal ⇒ **403**; id desconocido ⇒ `unknown-or-expired` (sin enumeración) | `test_cancel_idempotent_terminal_honest`, `test_capability_token_required_constant_time`, `test_unknown_id_noop_no_enumeration` |
| 1.6 | Botón stop del teléfono | Pregunta larga → pulsa **Detener** durante la respuesta | Audio local para al instante; POST `/speech/{id}/cancel` sale con el token (nunca en URL); funciona también tras haber oído ≥1 segmento | E2E `tests/e2e/test_m1_cancel.py`, `tests/e2e/test_m2_stream.py::test_stop_after_segment_ended_still_cancels` |
| 1.7 | Aislamiento entre solicitudes | Dos teléfonos/pestañas con preguntas cruzadas; cancela solo una | La otra sigue su curso; ni el LLM ni approvals se ven afectados jamás | E2E `test_crossed_requests_isolated` |
| 1.8 | Anuncio ajeno sobrevive | Suena una respuesta → llega anuncio SSE de otro agente → cancela la respuesta | El anuncio suena después sin pérdida | E2E `test_foreign_announcement_plays_after_cancel` |
| 1.9 | Fallo de red al cancelar | (Avanzado) desconecta red justo al pulsar stop | Estado visible `cancel-unconfirmed` tras 2 reintentos — nunca un falso "cancelado" | `tests/js/speech.test.js::test_cancel_net_retry_then_visible_unconfirmed` |

---

## Hito 2 — Audio incremental en el teléfono

| # | Feature | Cómo probarla (manual) | Esperado | Automatizado |
|---|---------|------------------------|----------|--------------|
| 2.1 | Primer segmento ANTES del total | Pregunta que dé respuesta larga (pide "explica X en detalle") | Primer audio en segundos; el teleprompter muestra texto mientras se sigue sintetizando | E2E `test_first_segment_plays_before_total` (medido: +0.12 s vs +4.45 s) |
| 2.2 | Orden y sin duplicados | Escucha una respuesta larga completa | Segmentos estrictamente en orden; ninguno dos veces | E2E `test_reconnect_no_duplicates_no_cancel_replay`, unit `tests/test_speech_transport.py` |
| 2.3 | Negociación v1 fail-soft | Cambia `HERDR_TTS_HOME` al canónico y reinicia el brain; pregunta CON id | Respuesta incluye `"degraded":"segmented-unavailable"`; audio llega completo (fichero); la respuesta textual JAMÁS se bloquea | `tests/test_speech_dispatch.py::test_v1_host_identified_turn_marks_degraded`, contrato `contracts/tts-brain-v2.md` |
| 2.4 | Cancelación a mitad de stream | Stop durante el streaming | Silencio inmediato; ningún fetch de segmentos después; job `cancelled` server-side | E2E `test_cancel_midstream_stops_segments` |
| 2.5 | Pressión del búfer (>8) | Respuesta larguísima escuchada completa | Nunca se degrada por búfer si vas escuchando (los acks liberan); con el teléfono en pausa prolongada el job acaba `degraded`/`expired-unconsumed` visible (300 s) | `test_gt8_segments_pressure_released_by_acks`, `test_disconnected_consumer_finite_unconsumed_timeout` |
| 2.6 | Reconexión de red | Activa/desactiva el wifi del teléfono a mitad de respuesta | Al volver, continúa donde iba SIN repetir ni saltar segmentos; sin cancelación fantasma | E2E `test_reconnect_no_duplicates_no_cancel_replay` |
| 2.7 | Legacy sin id intacto | `curl -XPOST /ask -d '{"text":"hola","session_id":"s1"}'` (sin id) | `audio_url` completo como siempre, sin clave `speech` — byte-paridad | `test_legacy_ask_full_file_path_intact` |

---

## Hito 3 — Anuncios pendientes atribuidos

| # | Feature | Cómo probarla (manual) | Esperado | Automatizado |
|---|---------|------------------------|----------|--------------|
| 3.1 | Busy ⇒ pendiente, no omitido | Suena una respuesta en el PC (daemon) y un agente termina en un panel vigilado | Log del watcher dice "admitted to the pending queue, not dropped"; suena al liberarse el altavoz, en orden FIFO | E2E `test_pc_never_muted_cross_channel`, `test_announcement_waits_then_plays_consolidated` |
| 3.2 | Consolidación de repetidos | El mismo agente repite done/blocked varias veces en <60 s | UNA reproducción consolidada; `repeat_count` sube como metadato (sin reabrir estados) | `tests/test_pending_queue.py::test_repeats_consolidate_metadata_only` |
| 3.3 | Época por `pane_pid` | Cierra y reabre el panel/tmux del agente vigilado | Los registros no terminales del pane viejo pasan a `expired` (visibles, no reproducibles) | `test_pane_pid_change_expires_old` |
| 3.4 | Cola/ledger acotados | (Provocación) 257 eventos con el canal ocupado | El más viejo ACTIVO pasa a `evicted` pero PERMANECE en el ledger; agotamiento de disco ⇒ `admission-blocked` con sobre `pending-overflow.json` (razón+atribución); el PC NUNCA se muta | `test_active_full_displaces_to_evicted_in_ledger`, `test_ledger_exhaustion_blocks_admission_visibly` |
| 3.5 | Crash del host | Ver E2E `test_kill9_host_pending_recovers_uncertain_no_replay` (kill -9 real a mitad de `announcing`) | Al rearrancar: `uncertain` visible, CERO auto-replay; solo `retry`/`resolve` deliberados lo mueven, con `resolved_by`/`resolved_ts` | mismo test + `test_orphan_announcing_becomes_uncertain_no_replay` |
| 3.6 | CLI del operador | `cd $WT/hosts/herdr/tts-plugin && ~/.local/share/herdr-tts/venv/bin/python lib/pending_queue.py list` (y `status`, `retry <id> --actor tu`, `resolve <id> announced --actor tu`, `regenerate <id>`) | Listados compactos; acciones deliberadas registradas con actor y timestamp; edges ilegales ⇒ rechazo tipado sin mutar | `test_resolve_records_actor_ts`, casos reales en `tests/host_cli_cases.sh` |
| 3.7 | Panel Pendientes del teléfono | Genera anuncios; abre la sección "📋 Pendientes" | Lista persistente (`localStorage`), badge `+N recortados` al pasar de 100, banner honesto si no persiste; acciones 🔊 Escuchar (regenera desde texto por el pipeline normal), ✓ Anunciado, ✕ Descartar; **Detener NO borra los registros** | `tests/js/pending.test.js` (4 tests), E2E `test_reload_restores_records`, `test_overflow_visible_regenerated` |
| 3.8 | Regeneración tras GC | Un registro cuyo mp3 ya fue recolectado (GC `ann-*` >1 h) → Escuchar | Se re-sintetiza DESDE EL TEXTO por la cadena local; el texto vive en el registro | `test_evicted_regenerates_after_gc` |

---

## Hito 4 — Fallback entre proveedores

> **OFF por defecto.** Sin `fallback.json` el comportamiento es idéntico al actual
> (verificado bit a bit). Las llamadas a proveedores reales son tu decisión explícita (D3):
> la prueba determinista del walker ya está automatizada con fakes.

| # | Feature | Cómo probarla (manual) | Esperado | Automatizado |
|---|---------|------------------------|----------|--------------|
| 4.1 | Sin config ⇒ idéntico a hoy | No hagas nada; usa el stack normal | Cero diferencia de comportamiento | `test_no_config_behavior_identical` (con bomba de monkeypatch: el walker ni se ejecuta) |
| 4.2 | Validación dura del schema | `~/.config/agent-tts/fallback.json` con `notes: ""` o mal formado | El motor NO se rompe: falla ABIERTO (una línea en stderr ⇒ disabled); un `fallback.json` roto jamás deja sin voz | `test_schema_requires_notes`, `test_invalid_config_typed_error` |
| 4.3 | Cadena real con primario caído | Config de prueba: `{"fallback_enabled":true,"chain":[{"provider":"openai","voice":"nova","notes":"cost:paid; data:cloud"},{"provider":"edge","voice":"es-ES-ElviraNeural","notes":"cost:free; data:cloud"}]}` + sin clave de openai | El eslabón sin clave se SALTA antes de submit (honesto); edge entrega. Con clave y proveedor caído: el secundario entrega SIN repetir lo ya oído | `test_missing_key_skips_before_submit`, E2E `test_fake_providers_e2e` (contadores de submits reales) |
| 4.4 | Cancel ⇒ cero intentos | Stop del teléfono durante un fallback en curso | Silencio inmediato; NINGÚN intento posterior a ningún proveedor (los contadores se congelan) | E2E `test_cancel_during_fallback_zero_further_attempts` |
| 4.5 | Presupuesto 4/2 sin duplicados | Contadores del E2E o log de intentos | ≤4 submits totales por intención, ≤2 por eslabón; el retry stream→batch del MISMO proveedor comparte ese presupuesto; cada intento queda en el log `{provider, outcome, ts}` | `test_same_provider_retry_reused_budget_shared`, `test_attempt_log_schema`, E2E `test_no_duplicate_submissions` |
| 4.6 | Parcial ya audible | (Escenario en tests) fallo tras oírse grupos | Estado visible `partial/uncertain`; la salida es replay DELIBERADO que relee desde el grupo fallido (duplicados reconocidos); JAMÁS auto-retry ni "resumen exacto" fingido | `test_partial_audible_stops_visible_deliberate_replay_acknowledged_duplicates`, `test_frames_mode_abstains_after_partial` |

Ejemplo de config mínima para 4.3/4.4 (fuera del repo, es tuya):

```json
{
  "fallback_enabled": true,
  "chain": [
    {"provider": "edge", "voice": "es-ES-ElviraNeural", "notes": "cost:free; data:cloud"},
    {"provider": "piper", "voice": "es_ES-sharvard-medium", "notes": "cost:free; data:local"}
  ]
}
```

---

## Transversales (cualquier hito)

- **Canales independientes:** el PC y el teléfono siempre activos; nada se muta por
  presión del otro canal (PRD03 FR-12, E2E `test_pc_never_muted_cross_channel`).
- **Stop de voz ≠ cancelar agente:** durante cualquier prueba de cancelación, la acción
  del agente, el LLM y los approvals siguen vivos (FR-07; aserciones negativas en cada hito).
- **Sin secretos en evidencia:** el capability token nunca aparece en URLs, logs ni respuestas
  (`test_token_never_in_logs`, `test_cancel_response_never_echoes_token`).

## Comandos automatizados completos (referencia)

```bash
WT=/home/bruno/Code/personal/agent-tts-worktrees/voice-stack

# Unit suites (como los gates, sin maquinaria de evidencia)
(cd $WT/hosts/herdr/brain && .venv/bin/python -m pytest tests/ -q --ignore=tests/e2e)   # 1256
(cd $WT/engine && .venv/bin/python -m pytest tests/ -q --ignore=tests/e2e)              # 954
(cd $WT/hosts/herdr/tts-plugin && PYTHONPATH="$WT/engine/src:lib" \
  ~/.local/share/herdr-tts/venv/bin/python -m pytest tests/ -q)                          # 133
(cd $WT/hosts/herdr/brain && node --test tests/js/)                                      # 233

# E2E navegador (Chromium real, fixtures decodificados)
(cd $WT/hosts/herdr/brain && .venv/bin/python -m pytest tests/e2e -q)                    # 20

# Bash: casos nombrados + humo
bash $WT/hosts/herdr/tts-plugin/tests/host_cli_cases.sh                                  # 26 OK
(cd $WT/hosts/herdr/tts-plugin && bash scripts/smoke-tests.sh)                           # 987/1 (40e preexistente)

# Frontera monorepo
(cd $WT && engine/.venv/bin/python -m pytest engine/tests/test_monorepo_boundaries.py -q) # 2
```

Los gates D4 de cobertura (comparador contra snapshots inmutables) requieren la
maquinaria del run-dir (`scripts/voice-stack/coverage_gate.py` + baselines); su última
ejecución íntegra está evidenciada en el manifiesto de VSX del run-dir citado arriba.

## Checklist manual-física (EXECUTION §8 — solo tú puedes confirmarla)

- [ ] Auricular/Bluetooth: la cancelación corta ESA voz sin cortar otros audios del teléfono.
- [ ] Acústica real PC+teléfono: pendientes audibles en orden, sin solapados ni duplicados.
- [ ] Proveedores reales (solo si configuras fallback): degradación audible sin doble reproducción.
- [ ] Latencia subjetiva del primer segmento en red móvil real (no LAN).
- [ ] Ninguna acción aprobada del agente se cancela al usar el stop de voz.

## Follow-ups conocidos (no confundir con defectos nuevos)

1. `G-SMOKE` 40e: `boundaries.py` difiere del pin `32e9bafb` del bootstrap — preexistente, adjudicado.
2. Cobertura <90 ramas de 6 módulos de tu trabajo paralelo (consult/evidence/queryfsm/reportstore/tools ×2) vs el baseline pre-paralelo — fuera del alcance voice-stack.
3. `speechCtl` maneja UN job activo (diseño VS1.7): un ask nuevo mientras suena el anterior deja al previo sin cancel por stop.
4. Commits/push/PR: pendientes de tu pedido expreso (el árbol quedó SIN commitear en `feat/voice-stack`).
