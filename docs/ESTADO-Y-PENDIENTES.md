# Monorepo agent-tts — Roadmap de Temas Pendientes

**Fecha de consolidación:** 9 de octubre de 2026  
**Punto de anclaje:** Post-Fases F4 (STT Core motor/PTT), F5 (Botones ntfy approval), HT-03 (Radio mode) y AT-11 M2 (Tareas 2.1–2.5) fusionados en `main` (commit `a4b23d7`).  
**Propósito:** Servir como eje de navegación y brújula de priorización para las siguientes sesiones de trabajo en el monorepo.

---

## 1. Mapa de Frentes Pendientes

El trabajo restante se estructura en cuatro frentes bien delimitados:

```
┌────────────────────────────────────────────────────────────────────────┐
│                   FRENTE 0: HERDR BRAIN EN VUELO                       │
│   • Brain: inline karaoke fragments (selección interactiva de texto)   │
│   • Brain: create-session-tool (creación de sesión desacoplada)        │
└───────────────────┬────────────────────────────────┬───────────────────┘
                    │                                │
┌───────────────────▼─────────────┐   ┌──────────────▼───────────────────┐
│     FRENTE 1: VOICE STACK       │   │  FRENTE 2: PRODUCTO & ONBOARDING │
│   (Transversal: Engine+Host+Brain)│   │  (Distribución y primer arranque)│
│ • F2: Validación física real    │   │ • AT-11 M2.6: Reinstall idempotent│
│   (Teléfono Android + PWA + PTT)│   │ • AT-11 M3: First-run wizard     │
│ • Cierre formal de VS0–VS4      │   │ • AT-11 M4: Doctor y empaquetado │
└───────────────────┬─────────────┘   └──────────────────────────────────┘
                    │
┌───────────────────▼────────────────────────────────────────────────────┐
│                  FRENTE 3: HOST PLUGIN (herdr-tts)                     │
│ • Bloque 4 (HT-03): Medición de métricas reales y LLM summary (Hito 2) │
│ • Bloque 3 (HT-05): Recordatorios escalados con backoff diferido       │
└───────────────────┬────────────────────────────────────────────────────┘
                    │
┌───────────────────▼────────────────────────────────────────────────────┐
│                 FRENTE 4: MOTOR PERIFÉRICO (agent-tts P3/P4)           │
│ • AT-07: Digest de audio diario (--digest compilado)                   │
│ • AT-06: Ducking de audio y prioridad de reproducción                  │
│ • AT-05: Prosodia consciente de estado del agente                      │
└────────────────────────────────────────────────────────────────────────┘
```

---

### Frente 0 — Herdr Brain: Mejoras de Interfaz y Sesiones

1. **Selectable Inline Karaoke Fragments**:
   - **Problema que resuelve:** Selección interactiva y reproducción de fragmentos de turnos pasados con resaltado sincronizado en la interfaz web de conversación.
   - **Estado:** PRD documentada en `docs/prds/herdr-brain-karaoke-fragments.md`, ramas `feat/brain-karaoke-fragments` y `fix/karaoke-deferred-media-load`.
   - **Acción pendiente:** Validación en navegador y consolidación hacia `main`.
2. **Create Session Tool**:
   - **Problema que resuelve:** Herramienta para que el operador por voz o el agente puedan abrir nuevas sesiones de forma desacoplada.
   - **Estado:** Planificada en roadmap de Herdr Brain.

---

### Frente 1 — Voice Stack: Validación Física en Hardware Real (F2)

Todo el software de Voice Stack (VS0 a VS4), la integración STT Core del motor (F4), la audición de voces (HT-11) y las acciones interactivas ntfy (F5) están implementados y verificados con tests automatizados herméticos.

- **F2 — Validación Física (1 sesión, teléfono real y micrófono)**:
  - **Manual de referencia:** `docs/voice-stack/MANUAL-TESTS.md` y `odd/tasks/f2-physical-validation.md`.
  - **Pruebas en dispositivo real:**
    1. **Cancelación de habla por solicitud (VS1)**: Corte en altavoz móvil < 0.3 s sin abortar la ejecución del agente.
    2. **Audio incremental en el teléfono (VS2)**: Primer segmento sonando en el navegador móvil antes de terminar la síntesis.
    3. **Action Approval Gate (`/approval/*`) y ntfy**: Recepción de botones `Approve`/`Stop` en push móvil y confirmación remota.
    4. **Dictado Push-to-Talk (HT-01)**: Captura por voz desde micrófono Windows real vía script MCI con el worker residente.
  - **Cierre:** Recopilar evidencia en `docs/voice-stack/archives/` y marcar casillas en `MANUAL-TESTS.md`.

---

### Frente 2 — Producto Instalable y Onboarding (`AT-11`)

- **Documento:** `docs/prds/AT-11-instalable-first-run.md` y plan de tareas `openspec/changes/at-11-instalable/tasks.md`.
- **Progreso:**
  - **M1 (Higiene y arnés de instalación limpia):** COMPLETADO (1.1 a 1.11).
  - **M2 (Desacoplamiento de rutas de máquina):** Tareas 2.1 a 2.5 ENTREGADAS y fusionadas en `main` (`4ba2efb`, `66ce2ad`, `8f58172`, `425deef`). Re-slice 9a/9b/9c registrado en `feat/at-11-slices-9abc`.
- **Trabajo Pendiente:**
  1. **Tarea 2.6 (Escenario 7 `reinstall-idempotent`):** Autorizar y verificar que reinstalar sobre un entorno existente preserva configuración, keymaps y credenciales. Cierra Milestone 2.
  2. **Milestone 3 (First-run wizard interactivo):**
     - 3.1 Esqueleto del wizard (`agent-tts setup` / onboarding CLI).
     - 3.2 Captura y almacenamiento atómico de credenciales y API keys.
     - 3.3 Pasos de voz y adopción de keymaps.
     - 3.4 Consentimiento de STT y modelos locales.
     - 3.5 Punto de entrada unificado y verificación de salud.
  3. **Milestone 4 (Diagnóstico y empaquetado):**
     - 4.1 Comando `doctor` (chequeo de entorno y remediación).
     - 4.2 Escenarios 8 y 9 de instalación limpia.
     - 4.3 Pase final de documentación y release.

---

### Frente 3 — Host Plugin: Bloques Pendientes (`hosts/herdr/tts-plugin/`)

- **Estado de Bloques:**
  - **BLOQUE-1 (Paquete motor):** COMPLETADO.
  - **BLOQUE-2 (Identidad vocal):** COMPLETADO (HT-02 voces por agente + HT-11 audición interactiva en fzf).
  - **BLOQUE-4 (Radio y verificación):** Hito 1 (HT-03 Radio mode `--radio`) IMPLEMENTADO y fusionado en `main`. Pendiente medición de métricas reales de uso y el Hito 2 opcional (`TTS_RADIO_LLM_SUMMARY=on`).
- **Trabajo Pendiente en Host Plugin:**
  - **BLOQUE-3 — Hito 2: Recordatorios Escalados (`HT-05`)**:
    - **PRD:** `hosts/herdr/tts-plugin/docs/prds/HT-05-recordatorios-escalados.md`.
    - **Prioridad:** P2 (Backlog diferido).
    - **Qué hace:** Insistencia periódica con backoff (2m → 5m → 15m) cuando un agente queda bloqueado o desatendido, reescrito limpiamente sobre `pending_queue.py` sin solaparse con VS3.

---

### Frente 4 — Motor Periférico (`engine/` P3/P4)

PRDs aprobadas y postergadas del roadmap del motor:

1. **AT-07 — Digest de audio (`--digest`)**: P3. Compilado hablado de los eventos y avances del día para briefings rápidos. Habilita `HT-08`.
2. **AT-06 — Ducking de audio**: P3. Atenuación automática del volumen de medios o agentes secundarios cuando habla un anuncio crítico (`blocked`).
3. **AT-05 — Prosodia consciente de estado**: P4. Modulación de velocidad/tono según la urgencia del evento.

---

## 2. Matriz de Dependencias Cruzadas

| Feature | Depende de | Habilita |
|---|---|---|
| **F2 (Validación Física)** | Teléfono Android, red LAN/Tailscale | Cierre formal de Voice Stack y experiencia móvil |
| **AT-11 M2.6 + M3 (Wizard)** | Tareas 2.1–2.5 (hechas en `main`) | Despliegue en 1 comando y onboarding en cualquier máquina |
| **HT-05 (Recordatorios)** | `pending_queue.py` de Voice Stack | Insistencia escalonada ante desatención prolongada |
| **AT-07 (Digest)** | `engine/` audio store | Resumen matinal del día (`HT-08`) |

---

## 3. Opciones para el Siguiente Paso (Rutas de Ataque Sugeridas)

1. **Ruta A — Validación Física en Dispositivo Real (F2)**:
   - Conectar Chrome Android por Tailscale y verificar en hardware real el corte de voz (VS1), audio incremental (VS2) y los botones ntfy de aprobación (F5).
2. **Ruta B — Cierre de M2 y Avance del Wizard de Onboarding (AT-11 M2.6 / M3)**:
   - Implementar el escenario 7 (`reinstall-idempotent`) para cerrar Milestone 2 y construir el wizard interactivo de primer arranque en `tools/herdr_onboarding/`.
3. **Ruta C — Herdr Brain UX (Karaoke & Sessions)**:
   - Retomar `karaoke-fragments` para selección de texto hablado y resaltado en la interfaz web.
4. **Ruta D — Loop de Recordatorios (HT-05)**:
   - Implementar los recordatorios escalados del Bloque 3 sobre la cola persistente del motor.
