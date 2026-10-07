# ROADMAP — voice-stack y monorepo (post-consolidación)

**Fecha:** 5 de octubre de 2026
**Base:** `main` @ `5e833fe` (todas las ramas y worktrees históricos ya son ancestros de `main`).
**Sustituye en la práctica a:** el orden lineal de [RECONCILIATION-PLAN.md](RECONCILIATION-PLAN.md) (2/10), que quedó parcialmente obsoleto. Ese documento sigue siendo válido como inventario de huecos; este define el orden de trabajo.

---

## 1. Qué cambió desde el plan del 2/10

El plan asumía "VS1-VS4 sin empezar". **No es así.** Verificado contra `main`:

| Afirmación del plan | Realidad hoy | Evidencia |
|---|---|---|
| H8/R6: VS1-VS4 sin empezar; `EXECUTION.md` §1 sin autorizar | **Implementados y cerrados `automated_complete` (VS0→VSX, 2026-10-01).** Solo queda la verificación física | `speech.py`, `pending_queue.py`, `segmented_render.py`, `fallback.py`; commits `182ed76`, `def986a`, `962e542`, `36a8997`, `507e761`; `MANUAL-TESTS.md` líneas 3-12 |
| D-R6a: autorizar `EXECUTION.md` §1 | **Obsoleta.** La ejecución ya ocurrió; `EXECUTION.md` y `TASKS.md` siguen diciendo DRAFT / `[FUTURE]` | `EXECUTION.md:3`, `TASKS.md` (marcadores FUTURE) |
| H5: `tests/` del plugin con 4 entradas; sin G-BASH-MATRIX | **Parcial.** Existen `all_bash_harnesses.sh`, `host_cli_cases.sh`, `matrix/`, `bash_matrix.py` y 5 ficheros pytest. Falta portar los escenarios críticos del legado (`4c572a4`: `smoke-tests.sh`, 4.143 líneas, 462 aserciones; el plan decía ~520) | `hosts/herdr/tts-plugin/tests/`, `scripts/voice-stack/` |
| H6: `herdr-tts` de 7.189 líneas | Sigue abierto; el fichero tiene **7.419** líneas y `voice.picker` solo tiene binds de aplicar/cancelar | `bin/herdr-tts` |
| H7: `src/` y `dist/` en la raíz con `git rm` | Estaban **sin trackear** (`git ls-files src dist` vacío), así que bastaba borrarlas del disco. **Hecho el 2026-10-05** (D5) | raíz |
| H2: fichas de bloques | Abierto. BLOQUE-1 está en `docs/prds/archivadas/bloques/`; **BLOQUE-2/3/4 están en `hosts/herdr/tts-plugin/docs/prds/bloques/`**, no donde dice el plan | rutas |
| H1, H3, H4, R1, R2 | Siguen abiertos tal cual. H1 es parcial: AT-03 y AT-10 ya dicen `Ejecutada` pero sin commit de referencia | cabeceras de PRDs |

Estados de documentos que también mienten y el plan no recoge: `odd/tasks/bloque-1.3-cola-y-cadena.md` (`En curso`, código hecho) y, en el brain, `call-history-persistence.md` y `action-approval-gate.md` (código implementado, doc dice planificado/no iniciado).

## 2. Idea rectora

El problema ya no es "construir voice-stack", sino **cerrar con honestidad lo construido, proteger el bash antes de tocarlo, y entonces avanzar producto**. El orden sale de tres restricciones reales:

1. **Documentación veraz primero:** es barato y evita que el siguiente agente o humano parta de información falsa.
2. **Red de tests antes de editar `bin/herdr-tts`:** HT-01, HT-04 (ntfy) y HT-11 viven en 7.419 líneas de bash.
3. **La validación física es una sola sesión con tres beneficiarios:** voice-stack (`EXECUTION.md` §8), validación de la PWA `/approval` en Chrome Android (H3/R2) y `announcements-without-call` T5. Se hace una vez, en el teléfono real.

## 3. Decisiones del maintainer (F0, cerrada el 2026-10-05)

| # | Decisión | Resultado |
|---|---|---|
| D1a | AT-02 (STT) | **Reenfocada, no archivada** (decisión original arriba, 2026-10-05: el plugin consume el STT del brain en vez de montar whisper.cpp). **Actualización de propiedad (2026-10-07, posterior y vigente):** el STT y la captura de micrófono pertenecen al **motor** — `faster-whisper` reutilizado por extracción del brain (`stt.py`), extra opcional, descarga de modelo siempre explícita; pasó antes por el diseño intermedio de cliente HTTP del plugin (mismo día, commit `ed2fbfe`, superado). Debe poder **activarse y desactivarse**. |
| D1b | HT-01 (push-to-talk) | **Sigue viva** (original 2026-10-05: reconocimiento en el brain vía AT-02). **Actualización (2026-10-07, posterior y vigente):** acorde, pane y overlay en el plugin; **captura y STT desde la capacidad del motor** (AT-02) por su interfaz pública; el dictado funciona con el brain apagado. Debe poder **activarse y desactivarse**. |
| D1c | HT-04 (control móvil) | **Sigue viva, reenfocada.** Botones ntfy como atajo ligero que llama a los endpoints del brain (`/approval/*`, `/ask`); no es una vía independiente. |
| D2 | Recuperación de tests (R4) | **Opción B:** auditar el legado y portar solo lo crítico sobre la matriz existente. |
| D3 | HT-05 frente a VS3 | **Separadas.** HT-05 va al backlog y, cuando se haga, se reescribe sobre `pending_queue.py` (sin segundo ledger). |
| D4 | Prioridad de HT-11 | **Baja.** HT-01/AT-02 pasan por delante. |
| D5 | Limpieza | **Hecha** el 2026-10-05: `src/`, `dist/`, 3 worktrees y 7 ramas locales ya mergeadas. Los ficheros ignorados eran regenerables salvo `pending_queue.py` de M1, ya archivado byte a byte en `docs/voice-stack/archives/m1-quality/`. |

Retirada: D-R6a del plan (obsoleta).

## 4. Fases

```
F1 base veraz ─┬─► F3 red de tests (R4-B) ─► F4 AT-02 + HT-01 ─► F5 HT-04/ntfy ─► F6 HT-11
               │                                    ▲
               ├─► F2 validación física (teléfono) ─┘ (F2 antes de F5: valida /approval)
               └─► F7 producto paralelo (AT-11, brain), en sus propias ramas
```

### F1 — Base veraz (solo documentación, 1 PR, ~2-3 h, riesgo cero)

> **Estado (2026-10-07): HECHA** e integrada en `main` local.

- R1.1 Cabeceras de AT-01, AT-03, AT-04, AT-08, AT-09, AT-10: `Estado: EJECUTADA` + fecha + commit.
- R1.2 Fichas de bloques en sus rutas reales (§1): BLOQUE-1 `COMPLETADO`, BLOQUE-2 `PARCIAL`, BLOQUE-3 `SIN EJECUTAR`, BLOQUE-4 `PARCIAL`.
- R1.3 `docs/prds/README.md` y el índice de voice-stack con el estado real, incluidas las features fuera de roadmap.
- R1.4 + nuevos: estados del brain (`approval-gate`, `call-history-persistence`) y de `bloque-1.3`.
- `EXECUTION.md`, `TASKS.md` y `docs/voice-stack/README.md` reflejan `automated_complete`; se retiran los marcadores `[FUTURE]` cumplidos.
- Notas de destino según D1: AT-02 `REENFOCADA` (consumir el STT del brain, con interruptor), HT-01 `VIVA` (depende de AT-02 reenfocada, con interruptor), HT-04 `VIVA, REENFOCADA` (ntfy sobre los endpoints del brain), HT-05 `BACKLOG` (sobre `pending_queue.py`).
- **Riesgo de conflicto:** el checkout principal tiene cambios sin commit en `docs/prds/README.md` (trabajo del brain). Rebasar sobre ellos antes de abrir la PR.
- **DoD:** ningún documento declara pendiente algo ejecutado, ni al revés.

### F2 — Validación física (1 sesión, teléfono real)

> **Estado (2026-10-07): PREPARADA, NO EJECUTADA.** Manual y checklist listos en `MANUAL-TESTS.md` y `odd/tasks/f2-physical-validation.md`; todas las casillas del dispositivo siguen sin marcar. El fallback con proveedores reales depende de que el owner configure `fallback.json`.

- Capa "manual física" de `EXECUTION.md` §8 / `MANUAL-TESTS.md`.
- En la misma sesión: PWA `/approval/*` en Chrome Android y `announcements-without-call` T5.
- **Depende de:** nada de código; puede ir en paralelo a F3/F4. Debe cerrarse antes de F5, que se apoya en `/approval`.
- La evidencia vive en `~/.local/state/voice-stack-runs/` (local a la máquina): copiar el resumen al repo bajo `docs/voice-stack/archives/`.

### F3 — Red de tests del plugin (R4, opción B)

> **Estado (2026-10-07): HECHA** e integrada en `main` local (`6838f29`, `a707f44`; seguimiento en `odd/tasks/f3-plugin-critical-tests.md`). Verificación independiente: batería completa del plugin sin fallos (97 casos OK tras integrar nuevos harnesses). Limitaciones documentadas: CI remoto no ejecutado; la limpieza de procesos de prueba cubre fixtures registrados y la señalización por pid conserva una ventana TOCTOU inherente.

- **R4a** Auditar `smoke-tests.sh` de `4c572a4` (4.143 líneas, 462 aserciones): qué cubría y qué sigue siendo crítico (gating, snooze, mute, debounce, voces, keymap apply/rollback, ciclo de vida del daemon).
- **R4b** Portar solo lo crítico al formato existente (`tests/matrix/bash-decisions.json`, `host_cli_cases.sh`, `all_bash_harnesses.sh`). Esfuerzo estimado ≈ 1 día.
- **DoD:** `all_bash_harnesses.sh` cubre los escenarios críticos y corre en CI.

### F4 — AT-02 reenfocada + HT-01 (hablar al agente desde el plugin)

> **Estado (2026-10-07): U1-U3 IMPLEMENTADOS** en `main` local (commits `2d1cb7d`, `0a488a9`, U2/U3 y docs). STT + captura en el motor, worker residente, plugin cableado (`ptt` con interruptores `TTS_STT`/`TTS_PTT`, por defecto OFF). Probado real: transcripción end-to-end `"Thanks for watching!"`. Pendiente: U4 validación en dispositivo real, verificación independiente de U1 (límite de runtime del verificador), F4.10 migración del brain (opcional, autorización aparte).

> **Rediseño de propiedad (2026-10-07, decisión vigente):** el STT y la captura de micrófono pertenecen al motor. El diseño intermedio de este mismo día (plugin → `POST /transcribe` del brain, commit `ed2fbfe`) quedó superado; ver cronología en D1a/D1b.

- **AT-02 (motor):** capacidad STT genérica — extracción de `Transcriber`/política de modelo del brain (`stt.py`), extra opcional (`agent-tts[stt]`, precedente kokoro), descarga de modelo siempre explícita, captura de micrófono PowerShell (análogo de entrada de `powershell_playback.py`). Configuración genérica del motor, no atada a ajustes de host. Interruptor propio.
- **HT-01 (plugin):** acorde que abre el micrófono en modo **toggle** (misma pulsación o timeout de silencio cortan; `hold` descartado por la suposición press-only del keymap, a verificar), STT y captura desde la interfaz pública del motor, texto reconocido con confirmación visual breve e inyección en el panel enfocado; interruptor propio. Motor sin dependencia/modelo o captura caída: aviso visible, nada se inyecta, sin descarga implícita ni arranque de servicios. El dictado **no requiere el brain** (puede estar apagado).
- **Verbo de inyección decidido (2026-10-07):** `herdr pane send-text <PANE_ID> <TEXT>` (texto literal, sin Enter) + Enter final según `TTS_PTT_ENTER` (`ask|always|never`). No se usan `herdr pane run` ni `send-keys` para el dictado.
- **Orden de implementación (ids estables F4.1-F4.10 y mapeos en la PRD HT-01):** extracción al motor + política de modelo + red de tests del motor primero (F4.1-F4.3); **gate de decisión sin código** de transporte/residencia (CLI one-shot vs daemon vs API pública por puente `lib/`) y superficie de contrato (`ipc-v2.md` congelado: extender es breaking) (F4.4); captura y silencio después (F4.5); cableado del plugin — inyección, keymap, overlay, interruptores, texto literal de confirmación — a continuación (F4.6-F4.9); migración del brain como unidad posterior **separada y opcional con autorización propia** (F4.10), preservando el comportamiento del navegador y del servidor.
- **Decisiones abiertas que F4 no silencia:** transporte/residencia y contrato (F4.4), umbrales de silencio, formato/latencia de captura PowerShell, manejo de saltos de línea en `send-text`, suposición press-only.
- PRDs reescritas el 2026-10-07 (AT-02 como capacidad del motor; HT-01 con desglose F4.1-F4.10).
- **Depende de:** F3 (se toca el bash de 7.419 líneas). **DoD:** hablar → ver texto reconocido → llega al agente **con el brain apagado**; con ambos interruptores en off, comportamiento idéntico al actual.

### F5 — HT-04 reenfocada (botones ntfy sobre el brain)

- Acciones en la notificación ntfy (continuar/detener) que llaman a los endpoints del brain; sin listener propio duplicado.
- **Depende de:** F2 (`/approval` validado en Chrome Android) y F3. Reescribir la PRD antes de implementar.

### F6 — HT-11 audición de voces (prioridad baja, tras F4/F5)

- Binds de preview/asignación sobre el picker `voice.picker.*`: preview bilingüe, auto-stop <0,3 s, caché LRU 20 MB, asignación global/chat.
- Un solo escritor sobre `bin/herdr-tts`; no solapar con F4/F5.

### F7 — Producto en paralelo (ramas independientes, sin dependencias de F1-F6)

| Línea | Estado | Siguiente paso |
|---|---|---|
| AT-11 instalable | M1 hecho; M2 parcial (tarea 2.1 hecha); 14 tareas pendientes de M2–M4 en `openspec/changes/at-11-instalable/tasks.md` | Retomar en M2 |
| brain: `open-session-inventory` | Activo (WIP sin commit en el checkout principal) | Terminar y mergear |
| brain: `karaoke-fragments`, `create-session-tool` | Planificados | Priorizar tras lo anterior |
| brain: `transcription-duplication` | Residual abierto | Cerrar o descartar |
| `autoloop` | Solo bootstrap | Decidir si sigue vivo |
| Backlog diferido: HT-03, HT-05 (sobre `pending_queue.py`), AT-05/06/07, BLOQUE-3 y BLOQUE-4 | Postergado | Reevaluar tras F2 y F4 |

## 5. Fuera de alcance

Implementar el backlog diferido, y cualquier push/PR/merge: siguen siendo decisiones del maintainer.

## 6. Criterio de éxito

1. Cualquier lector del repo obtiene el estado real leyendo solo los índices.
2. voice-stack, `/approval` y los anuncios están validados en dispositivo real.
3. El bash del plugin tiene red de tests antes de recibir HT-01, HT-04 y HT-11.
4. AT-02 y HT-01 se pueden activar y desactivar sin afectar al resto.
