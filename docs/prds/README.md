# agent-tts — PRDs 2026 (roadmap consolidado y ecosistema monorepo)

**Fecha:** 22 de septiembre de 2026 (actualizado al 2 de octubre de 2026 tras migración a monorepo AT-10 y auditoría de reconciliación).

**Metodología:** Cada PRD se justifica primero por cómo mejora el flujo diario del maintainer (dirigir flotas de agentes de código por voz en Herdr bajo WSL2, audio al host Windows via `winhost`) y solo después por su valor competitivo, anclada en el código real del motor y contrastada con el prior art del ecosistema. Tras la migración AT-10, el monorepo unifica el motor (`engine/`), el plugin orquestador de Herdr (`hosts/herdr/tts-plugin/`) y el asistente conversacional móvil (`hosts/herdr/brain/`).

---

## 1. Resumen consolidado del estado

La migración a monorepo absorbió tres repositorios independientes, implementando 5 de las 20 features del roadmap original y ~10 nuevas capacidades fuera de él:

- **20 features del roadmap original (revisión 22/09/2026):**
  - **5 Ejecutadas / Implementadas:** AT-01 (streaming frames MP3), AT-03 (conectores recortada a OpenCode), AT-04 (daemon persistente), AT-08 (cola con prioridades), HT-02 (voces por agente).
  - **3 Supersedidas / Cubiertas por brain (decisión D-R2, 2026-10-06):** AT-02 (cubierta por brain via faster-whisper), HT-01 (supersedida por brain conversation-mode), HT-04 (supersedida por brain PWA `/approval/*` + `/ask`).
  - **6 Aprobadas pendientes:** AT-06 (ducking), AT-07 (digest audio), HT-03 (radio mode, hito 0 completado), HT-05 (recordatorios escalados), HT-10 (chain replay), HT-11 (audición de voces en paleta).
  - **4 Postergadas:** AT-05 (prosodia), HT-06 (auto-snooze), HT-07 (filtro semántico), HT-08 (briefing matinal).
  - **2 Descartadas:** HT-09 (espacialización estéreo), HT-12 (watchers de texto).
- **10 features nuevas fuera de roadmap (post-revisión / monorepo):**
  - **AT-09:** Propiedad del canal de control (flock, socket, framing) — **EJECUTADA** (BLOQUE 1.1).
  - **AT-10:** Monorepo del ecosistema (engine + contracts + hosts) — **EJECUTADA**.
  - **HT-13:** Consumo de la API pública del motor en el host — **EJECUTADA / IMPLEMENTADA**.
  - **HT-14:** Tema claro configurable (`TTS_THEME` en `bin/herdr-tts`) — **EJECUTADA / IMPLEMENTADA**.
  - **HT-15:** Transformación Markdown/HTML para lectura sincronizada — **EJECUTADA / IMPLEMENTADA**.
  - **HT-16:** Popup de lectura en vivo (karaoke) — **EJECUTADA / IMPLEMENTADA**.
  - **Podcast RSS local:** Generador y feed RSS de podcast en el motor (`engine/src/agent_tts/podcast.py`, flag `--podcast`) — **EJECUTADA / IMPLEMENTADA**.
  - **Brain on-demand-context:** Consultas, reportes y seguimiento multi-sesión (`hosts/herdr/brain/docs/ON-DEMAND-CONTEXT.md`) — **EJECUTADA / IMPLEMENTADA**.
  - **Brain action approval-gate:** Puerta interactiva de confirmación de acciones mutantes (`hosts/herdr/brain/docs/PRD-action-approval-gate.md`) — **EJECUTADA / IMPLEMENTADA** con tests (`server.py` `/approval/*`, `approval.py`).
  - **VS0 (Voice Stack fundaciones):** Harness E2E, instrumentación de cobertura D4/D9 y gates transversales (`docs/voice-stack/TASKS.md`) — **Planificada / En preparación**.
- **Otras PRDs activas post-migración:**
  - **AT-11:** Producto instalable independiente con first-run onboarding (`AT-11-instalable-first-run.md`) — **Aprobada** (P1, 2026-09-30).
  - **Brain karaoke fragments:** Fragmentos seleccionables inline de karaoke (`herdr-brain-karaoke-fragments.md`) — **Aprobada (documentación)**.

---

## 2. Índice del Roadmap Original (20 features: 8 AT + 12 HT)

### Motor (`agent-tts`)

| ID | Fichero | Feature | Prioridad final | Estado real | Esfuerzo |
|---|---|---|---|---|---|
| AT-01 | [archivadas/AT-01-streaming-frames-mp3.md](archivadas/AT-01-streaming-frames-mp3.md) | Streaming incremental por frames de MP3 | P3 | **EJECUTADA** (2026-09-29, commits `510ceaa` / `a3ee900`) | L |
| AT-02 | [AT-02-stt-whispercpp.md](AT-02-stt-whispercpp.md) | Capa STT local (`--transcribe`) con whisper.cpp | P4 | **Cubierta por brain** (faster-whisper, 2026-10-06) | M |
| AT-03 | [archivadas/AT-03-conectores-autodeteccion.md](archivadas/AT-03-conectores-autodeteccion.md) | Conectores restantes y auto-detección del agente | P2 | **EJECUTADA (recortada a OpenCode)** (2026-09-28, commit `3bb5e1b`) | M |
| AT-04 | [archivadas/AT-04-daemon-persistente.md](archivadas/AT-04-daemon-persistente.md) | Modo daemon persistente del motor (`--serve`) | P1 | **EJECUTADA (BLOQUE 1.2)** (2026-09-24/25, commits `64681be` / `c8d5665`) | L |
| AT-05 | [AT-05-prosodia-estado.md](AT-05-prosodia-estado.md) | Prosodia consciente de estado | P4 | **Postergada** | M |
| AT-06 | [AT-06-ducking-audio.md](AT-06-ducking-audio.md) | Ducking y prioridad de audio | P3 | **Aprobada** (pendiente) | M |
| AT-07 | [AT-07-digest-audio.md](AT-07-digest-audio.md) | Compilado de audio / digest (`--digest`) | P3 | **Aprobada** (pendiente) | S-M |
| AT-08 | [archivadas/AT-08-cola-prioridades.md](archivadas/AT-08-cola-prioridades.md) | Cola con prioridades y reproducción encadenada | P1 | **EJECUTADA (BLOQUE 1.3)** (2026-09-25, commits `7231f9e` / `c8d5665`) | M-L |

### Host Plugin (`herdr-tts`)

| ID | Fichero | Feature | Prioridad final | Estado real | Esfuerzo |
|---|---|---|---|---|---|
| HT-01 | [../../hosts/herdr/tts-plugin/docs/prds/HT-01-push-to-talk-intercom.md](../../hosts/herdr/tts-plugin/docs/prds/HT-01-push-to-talk-intercom.md) | Push-to-Talk intercom (hablar al agente) | P4 | **Supersedida por brain** (conversation-mode, 2026-10-06) | M-L |
| HT-02 | [../../hosts/herdr/tts-plugin/docs/prds/archivadas/HT-02-voces-por-agente.md](../../hosts/herdr/tts-plugin/docs/prds/archivadas/HT-02-voces-por-agente.md) | Voces por agente (identidad vocal) | P1 | **EJECUTADA / IMPLEMENTADA** (Bloque 2 Hito 0) | S-M |
| HT-03 | [../../hosts/herdr/tts-plugin/docs/prds/HT-03-radio-mode.md](../../hosts/herdr/tts-plugin/docs/prds/HT-03-radio-mode.md) | Radio mode (triaje por voz) | P2 | **Aprobada** (PARCIAL: verificación Hito 0 hecha, radio pendiente) | M |
| HT-04 | [../../hosts/herdr/tts-plugin/docs/prds/HT-04-control-movil-bidireccional.md](../../hosts/herdr/tts-plugin/docs/prds/HT-04-control-movil-bidireccional.md) | Control bidireccional desde el móvil | P1 | **Supersedida por brain** (PWA `/approval/*` + `/ask`, 2026-10-06) | L |
| HT-05 | [../../hosts/herdr/tts-plugin/docs/prds/HT-05-recordatorios-escalados.md](../../hosts/herdr/tts-plugin/docs/prds/HT-05-recordatorios-escalados.md) | Recordatorios escalados de atención | P1 | **Aprobada** (pendiente Bloque 3) | M |
| HT-06 | [../../hosts/herdr/tts-plugin/docs/prds/HT-06-auto-snooze-reunion.md](../../hosts/herdr/tts-plugin/docs/prds/HT-06-auto-snooze-reunion.md) | Auto-snooze contextual (modo reunión) | P4 | **Postergada** | M |
| HT-07 | [../../hosts/herdr/tts-plugin/docs/prds/HT-07-filtro-semantico.md](../../hosts/herdr/tts-plugin/docs/prds/HT-07-filtro-semantico.md) | Filtro semántico de importancia | P4 | **Postergada** | M |
| HT-08 | [../../hosts/herdr/tts-plugin/docs/prds/HT-08-briefing-matinal.md](../../hosts/herdr/tts-plugin/docs/prds/HT-08-briefing-matinal.md) | Briefing matinal automático | P4 | **Postergada** | M |
| HT-09 | [../../hosts/herdr/tts-plugin/docs/prds/descartadas/HT-09-espacializacion-estereo.md](../../hosts/herdr/tts-plugin/docs/prds/descartadas/HT-09-espacializacion-estereo.md) | Espacialización estéreo por pane | — | **Descartada** | — |
| HT-10 | [../../hosts/herdr/tts-plugin/docs/prds/HT-10-chain-replay.md](../../hosts/herdr/tts-plugin/docs/prds/HT-10-chain-replay.md) | Chain replay contextual | P5 | **Aprobada (baja)** (pendiente) | S-M |
| HT-11 | [../../hosts/herdr/tts-plugin/docs/prds/HT-11-audicion-voces.md](../../hosts/herdr/tts-plugin/docs/prds/HT-11-audicion-voces.md) | Audición de voces en la paleta | P1 | **Aprobada** (pendiente Bloque 2 Hito 1) | S |
| HT-12 | [../../hosts/herdr/tts-plugin/docs/prds/descartadas/HT-12-watchers-texto.md](../../hosts/herdr/tts-plugin/docs/prds/descartadas/HT-12-watchers-texto.md) | Watchers personalizados de texto | — | **Descartada** | — |

---

## 3. Features nuevas fuera de roadmap (post-revisión / monorepo)

| ID / Componente | Fichero / Ubicación | Feature | Prioridad / Tipo | Estado real |
|---|---|---|---|---|
| AT-09 | [archivadas/AT-09-canal-de-control.md](archivadas/AT-09-canal-de-control.md) | Propiedad del canal de control (socket, lock y framing) | P1 (motor) | **EJECUTADA (BLOQUE 1.1)** (2026-09-24, commits `1dde8f2` / `c8d5665`) |
| AT-10 | [archivadas/AT-10-monorepo-ecosistema.md](archivadas/AT-10-monorepo-ecosistema.md) | Monorepo del ecosistema: engine + contracts + hosts | P2 (arquitectura) | **EJECUTADA** (2026-09-28, commits `b972e4b` / `29429e3`) |
| AT-11 | [AT-11-instalable-first-run.md](AT-11-instalable-first-run.md) | Producto instalable independiente con first-run onboarding | P1 (producto) | **Aprobada** (2026-09-30, no arrancada) |
| HT-13 | [../../hosts/herdr/tts-plugin/docs/prds/archivadas/HT-13-consumo-api-publica-motor.md](../../hosts/herdr/tts-plugin/docs/prds/archivadas/HT-13-consumo-api-publica-motor.md) | Consumo de la API pública del motor (de-duplicación) | P1 (host) | **EJECUTADA / IMPLEMENTADA** (2026-09-28) |
| HT-14 | [../../hosts/herdr/tts-plugin/docs/prds/archivadas/HT-14-tema-claro.md](../../hosts/herdr/tts-plugin/docs/prds/archivadas/HT-14-tema-claro.md) | Tema claro configurable (`TTS_THEME` en `bin/herdr-tts`) | P2 (host) | **EJECUTADA / IMPLEMENTADA** |
| HT-15 | [../../hosts/herdr/tts-plugin/docs/prds/archivadas/HT-15-markdown-html-pipeline.md](../../hosts/herdr/tts-plugin/docs/prds/archivadas/HT-15-markdown-html-pipeline.md) | Transformación Markdown/HTML para lectura sincronizada | P2 (host) | **EJECUTADA / IMPLEMENTADA** |
| HT-16 | [../../hosts/herdr/tts-plugin/docs/prds/archivadas/HT-16-reader-popup.md](../../hosts/herdr/tts-plugin/docs/prds/archivadas/HT-16-reader-popup.md) | Popup de lectura en vivo (karaoke) | P2 (host) | **EJECUTADA / IMPLEMENTADA** |
| Podcast RSS | `engine/src/agent_tts/podcast.py` (`agent-tts --podcast`) | Feed y generador RSS de audio local | Motor | **EJECUTADA / IMPLEMENTADA** |
| Brain Context | [../../hosts/herdr/brain/docs/ON-DEMAND-CONTEXT.md](../../hosts/herdr/brain/docs/ON-DEMAND-CONTEXT.md) | On-Demand Context (consultas, reportes, seguimiento) | Brain | **EJECUTADA / IMPLEMENTADA** |
| Brain Approval | [../../hosts/herdr/brain/docs/PRD-action-approval-gate.md](../../hosts/herdr/brain/docs/PRD-action-approval-gate.md) | Puerta de aprobación interactiva para acciones mutantes | Brain | **EJECUTADA / IMPLEMENTADA** con tests (`server.py` `/approval/*`, `approval.py`) |
| VS0 | [../voice-stack/TASKS.md](../voice-stack/TASKS.md) | Fundaciones mínimas Voice Stack (harness, gates D4/D9) | Transversal | **Planificada / En preparación** |
| Brain Fragments | [herdr-brain-karaoke-fragments.md](herdr-brain-karaoke-fragments.md) | Selectable inline karaoke fragments | Brain | **Aprobada (documentación)** |

---

## 4. Estado de los Bloques de Ejecución

1. **BLOQUE 1 — Paquete motor (AT-09 + AT-04 + AT-08):**
   - **Estado:** **COMPLETADO (2026-09-25)** en tres sub-bloques secuenciales archivados en [`archivadas/bloques/`](archivadas/bloques/):
     - 1.1 Canal de control (AT-09): flock atómico y framing de socket.
     - 1.2 Daemon persistente por vía única (AT-04): auto-arranque transparente y ciclo de vida.
     - 1.3 Cola y cadena (AT-08): prioridades y `--play-chain` sobre el daemon.
   - Contrato IPC congelado: [`../../contracts/ipc-v2.md`](../../contracts/ipc-v2.md).
2. **BLOQUE 2 — Identidad vocal (HT-02 + HT-11):**
   - **Estado:** **PARCIAL (1 de 2 hitos)** en [`../../hosts/herdr/tts-plugin/docs/prds/bloques/BLOQUE-2-identidad-vocal.md`](../../hosts/herdr/tts-plugin/docs/prds/bloques/BLOQUE-2-identidad-vocal.md).
   - Hito 0 (HT-02): implementado (`voices.json`, selector y prefijo de agente).
   - Hito 1 (HT-11): audición de voces en paleta fzf pendiente (P1 huérfana, paquete R3 de reconciliación).
3. **BLOQUE 3 — Loop móvil (HT-04 → HT-05):**
   - **Estado:** **SIN EJECUTAR** en [`../../hosts/herdr/tts-plugin/docs/prds/bloques/BLOQUE-3-loop-movil.md`](../../hosts/herdr/tts-plugin/docs/prds/bloques/BLOQUE-3-loop-movil.md).
   - Nota: HT-04 supersedida por brain PWA (`/approval/*` y `/ask`, decisión D-R2 confirmada 2026-10-06). HT-05 pendiente.
4. **BLOQUE 4 — Radio mode y verificación OpenCode (HT-03 + AT-03 recortada):**
   - **Estado:** **PARCIAL (hito 0 hecho, radio pendiente)** en [`../../hosts/herdr/tts-plugin/docs/prds/bloques/BLOQUE-4-radio-y-verificacion.md`](../../hosts/herdr/tts-plugin/docs/prds/bloques/BLOQUE-4-radio-y-verificacion.md).
   - Hito 0: Verificación de AT-03 contra SQLite de OpenCode completada y validada (2026-09-28).
   - Hito 1: Radio mode (HT-03) pendiente.

---

## 5. Coordinación Inter-Componente

- **AT-08 habilita HT-03/HT-10:** La cola con `enqueue` + `play-chain` sostiene el radio mode (HT-03) y las capacidades de orquestación de audio de herdr (HT-10).
- **AT-07 habilita HT-08:** El digest de audio es la base del briefing matinal de herdr.
- **AT-02 / Brain STT y HT-01:** La capacidad STT está provista hoy en `herdr-brain` mediante `faster-whisper` (`/transcribe`), superando la necesidad de whisper.cpp directo en el motor para el flujo de interacción continua.
- **Canal de control y PWA:** `herdr-brain` interactúa con el host vía contratos versionados (`contracts/tts-brain-v1.md`), disponiendo de puertas de aprobación interactivas (`/approval/*`) para operaciones del asistente.
