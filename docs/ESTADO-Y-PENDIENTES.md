# Monorepo agent-tts — Roadmap de Temas Pendientes

**Fecha de consolidación:** 6 de octubre de 2026  
**Punto de anclaje:** Post-Reconciliación completa (R1 a R6 ejecutados, commit `9514e6c` en `origin/main`).  
**Propósito:** Servir como eje de navegación y brújula de priorización para las siguientes sesiones de trabajo en el monorepo.

---

## 1. Mapa de Frentes Pendientes

El trabajo restante se estructura en cuatro frentes bien delimitados:

```
┌────────────────────────────────────────────────────────────────────────┐
│                   FRENTE 0: EN VUELO INMEDIATO                         │
│   • Brain: open-session-inventory (listado ligero de sesiones)        │
│   • Brain: inline karaoke fragments (selección interactiva de texto)   │
└───────────────────┬────────────────────────────────┬───────────────────┘
                    │                                │
┌───────────────────▼─────────────┐   ┌──────────────▼───────────────────┐
│     FRENTE 1: VOICE STACK       │   │  FRENTE 2: PRODUCTO & ONBOARDING │
│   (Transversal: Engine+Host+Brain)│   │  (Distribución y primer arranque)│
│ • VS0: Gates y harness E2E web  │   │ • AT-11: Paquete instalable CLI  │
│ • VS1: Cancelación por solicitud│   │ • First-run onboarding wizard    │
│ • VS2: Audio incremental móvil  │   │ • Validación rama at-11-instalable│
│ • VS3: Anuncios pendientes      │   └──────────────────────────────────┘
│ • VS4: Fallback cross-proveedor │
└───────────────────┬─────────────┘
                    │
┌───────────────────▼────────────────────────────────────────────────────┐
│                  FRENTE 3: HOST PLUGIN (herdr-tts)                     │
│ • Bloque 4 (HT-03): Radio mode (triaje por voz con cola AT-08)         │
│ • Bloque 3: Loop móvil nativo (HT-04 reply/actions + HT-05 recordatorios)│
│ • HT-01: Push-to-Talk intercom en terminal (vía STT motor AT-02)       │
└───────────────────┬────────────────────────────────────────────────────┘
                    │
┌───────────────────▼────────────────────────────────────────────────────┐
│                 FRENTE 4: MOTOR PERIFÉRICO (agent-tts P3)              │
│ • AT-07: Digest de audio diario (--digest compilado)                   │
│ • AT-06: Ducking de audio y prioridad de reproducción                  │
│ • AT-05: Prosodia consciente de estado del agente                      │
│ • AT-02: Subcomando CLI motor `agent-tts --transcribe` (whisper.cpp)   │
└────────────────────────────────────────────────────────────────────────┘
```

---

### Frente 0 — Trabajo en Vuelo Inmediato (Local en `hosts/herdr/brain/`)

Cambios de código que ya cuentan con implementación y tests verdes en el árbol local:

1. **Open-Session Inventory (`open-session-inventory.md`)**:
   - **Problema que resuelve:** Permite al operador preguntar por voz *"¿qué sesiones tengo abiertas?"* y obtener un inventario metadata-only ligero de todos los agentes en Herdr sin leer turnos ni ejecutar el pesado `consult_work_status`.
   - **Estado:** Código y tests listos en `hosts/herdr/brain/src/` y `tests/` (**159 tests pasando**).
   - **Acción pendiente:** Revisar y commitear en su rama `fix/brain-open-session-inventory` o mergear a `main`.
2. **Selectable Inline Karaoke Fragments**:
   - **Problema que resuelve:** Selección interactiva y reproducción de fragmentos de turnos pasados con resaltado sincronizado.
   - **Estado:** PRD documentada en `docs/prds/herdr-brain-karaoke-fragments.md`, ramas `feat/brain-karaoke-fragments` y `fix/karaoke-deferred-media-load`.
   - **Acción pendiente:** Validación en navegador y consolidación.

---

### Frente 1 — Voice Stack Transversal (VS0 → VS4)

Plan integral definido en [`docs/voice-stack/`](voice-stack/README.md) bajo el contrato [`EXECUTION.md`](voice-stack/EXECUTION.md) para dotar de confiabilidad industrial al pipeline de audio transversal.

- **VS0 — Fundaciones e Instrumentación:**
  - Configuración de gates de cobertura estricta (D4: ≥90% Python/JS; D9: Bash líneas + matriz de decisiones).
  - Harness E2E de navegador determinista con STT/TTS simulados para la PWA.
- **VS1 — Cancelación de habla por solicitud (`prds/01`):**
  - `stop` cancela el habla de esa solicitud específica en PC y móvil sin afectar audios ajenos ni cortar la ejecución del agente.
- **VS2 — Audio incremental en el teléfono (`prds/02`):**
  - Primer segmento sonando en el navegador móvil antes de concluir la síntesis total del turno, en orden y sin duplicados.
- **VS3 — Anuncios pendientes atribuidos (`prds/03`):**
  - Eliminación de descartes silenciosos en momentos de ocupación: retención en SQLite atribuida (`pane_id`, `agent`, estado) y reproducción en cuanto el canal se libere. *(Deslindado formalmente de HT-05)*.
- **VS4 — Fallback entre proveedores (`prds/04`):**
  - Conmutación automática y transparente (OFF por defecto) hacia un proveedor secundario si el principal falla.

*Punto de partida:* Pegar la solicitud copiable de [`EXECUTION.md`](voice-stack/EXECUTION.md) §1 en una sesión dedicada.

---

### Frente 2 — Producto Instalable y Onboarding (`AT-11`)

- **Documento:** [`docs/prds/AT-11-instalable-first-run.md`](prds/AT-11-instalable-first-run.md) (Auditoría: [`AT-11-auditoria-instalacion.md`](prds/AT-11-auditoria-instalacion.md)).
- **Prioridad:** P1 (Aprobada 2026-09-30).
- **Problema que resuelve:** Elimina el acoplamiento a la máquina local del maintainer y provee una experiencia de instalación limpia ("first-run onboarding") para cualquier máquina nueva:
  - Asistente de configuración unificado (claves API, proveedores preferidos, modelos).
  - Integración nativa opcional con `systemd` para daemons en background.
  - Soporte de resolución de rutas y Tailscale en documentación.
- **Estado:** Trabajo explorado y validado en la rama remota `origin/validation/at-11-instalable`.
- **Acción pendiente:** Hand-off a SDD o consolidación de tareas de onboarding hacia `main`.

---

### Frente 3 — Host Plugin: Bloques Pendientes (`hosts/herdr/tts-plugin/`)

1. **BLOQUE 4 — Hito 1: Radio Mode (`HT-03`)**:
   - **PRD:** [`hosts/herdr/tts-plugin/docs/prds/HT-03-radio-mode.md`](../hosts/herdr/tts-plugin/docs/prds/HT-03-radio-mode.md).
   - **Prioridad:** P2.
   - **Qué hace:** Boletín hablado y priorizado (triaje por voz) de todos los chats que demandan atención. Consume directamente la cola prioritaria `AT-08` del motor y las identidades vocales `HT-02`/`HT-11`.
   - **Estado:** Hito 0 (verificación OpenCode) completado. El Hito 1 (comando `--radio`) está listo para implementar sobre la base de tests recién creada.
2. **BLOQUE 3 — Loop Móvil Nativo (`HT-04` + `HT-05`)**:
   - **PRD:** [`HT-04`](../hosts/herdr/tts-plugin/docs/prds/HT-04-control-movil-bidireccional.md) (control remoto ntfy en host) y [`HT-05`](../hosts/herdr/tts-plugin/docs/prds/HT-05-recordatorios-escalados.md) (recordatorios escalados).
   - **Prioridad:** P1.
   - **Qué hace:** Añade listener HTTP en el daemon de `herdr-tts` para acciones ntfy (Detener/Continuar) y recordatorios periódicos con backoff cuando un agente queda bloqueado o desatendido.
3. **HT-01 — Push-to-Talk Intercom**:
   - **PRD:** [`HT-01`](../hosts/herdr/tts-plugin/docs/prds/HT-01-push-to-talk-intercom.md).
   - **Prioridad:** P4 (depende de capa STT en el motor).

---

### Frente 4 — Motor Periférico (`engine/` P3/P4)

PRDs aprobadas y postergadas del roadmap del motor:

1. **AT-07 — Digest de audio (`--digest`)**: P3 (Aprobada). Compilado hablado de los eventos y avances del día para briefings rápidos. Habilita `HT-08`.
2. **AT-06 — Ducking de audio**: P3 (Aprobada). Atenuación automática del volumen de medios o agentes secundarios cuando habla un anuncio crítico (`blocked`).
3. **AT-05 — Prosodia consciente de estado**: P4 (Postergada). Modulación de velocidad/tono según la urgencia del evento.
4. **AT-02 — Capa STT local (`--transcribe`)**: P4 (Postergada). Exposición de `whisper.cpp` en el CLI del motor para dar servicio a `HT-01`.

---

## 2. Matriz de Dependencias Cruzadas

| Feature | Depende de | Habilita |
|---|---|---|
| **Voice Stack (VS1–VS4)** | `EXECUTION.md` §1, fundaciones VS0 | Robustez industrial en teléfono y PC, cero avisos perdidos |
| **HT-03 (Radio mode)** | `AT-08` (hecho), `HT-02` (hecho) | Triaje rápido de flotas por voz en terminal |
| **HT-05 (Recordatorios)** | `HT-04` (o daemon host) | Insistencia escalonada ante desatención |
| **AT-07 (Digest)** | `engine/` audio store | Resumen matinal del día (`HT-08`) |
| **AT-02 (STT CLI)** | Binario whisper.cpp | Intercom terminal push-to-talk (`HT-01`) |
| **AT-11 (Instalable)** | Packaging uv / scripts | Despliegue en 1 comando en nuevas máquinas |

---

## 3. Opciones para el Siguiente Paso (Rutas de Ataque Sugeridas)

Dependiendo del objetivo principal del maintainer, las rutas recomendadas son:

- **Ruta A (Cerrar cabos sueltos locales):**
  - Commitear e integrar `open-session-inventory` en `hosts/herdr/brain/` (159 tests pasando).
  - Dejar el árbol de trabajo completamente limpio.
- **Ruta B (Salto de calidad en experiencia de usuario diaria — Voice Stack):**
  - Autorizar y ejecutar `docs/voice-stack/EXECUTION.md` §1 (arrancando por VS0 y VS1: Cancelación de habla).
- **Ruta C (Terminar features del plugin host):**
  - Ejecutar el Hito 1 de BLOQUE-4: **HT-03 (Radio mode)**, aprovechando la red de tests de bash recién consolidada.
- **Ruta D (Portabilidad e instalación):**
  - Consolidar **AT-11** para que el paquete `agent-tts` sea 100% instalable y portable sin fricción.
