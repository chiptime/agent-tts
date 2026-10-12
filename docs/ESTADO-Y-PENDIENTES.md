# Monorepo agent-tts — Roadmap de Temas Pendientes

**Fecha de consolidación:** 11 de octubre de 2026

**Punto de anclaje:** Post-Fases F4 (STT Core), F5 (Botones ntfy approval), HT-03 (Radio mode), Karaoke Inline Fragments, AT-11 M2–M4 (wizard, doctor, escenarios 4–9, docs y checklist V3) y limpieza integral de rutas de máquina fusionados en `main` (commit `e85200d`).  
**Propósito:** Servir como brújula estricta y veraz de los únicos trabajos pendientes reales en el monorepo.

---

## 1. Mapa de la Realidad del Monorepo

> **Clarificación de estado:** Funcionalidades como **Karaoke Fragments** (`964f6b5`), **Open-Session Inventory**, **Radio Mode HT-03** (`dd496c3`), **Botones ntfy F5** (`0b615d2`), **Audición de voces HT-11** (`7108e6c`) y **Voice Stack Core VS0–VS4 / F4** ya están **100% implementadas y fusionadas en `main`**.

**HT-05 (BLOQUE-3 Hito 2):** implementada y entregada en `feat/ht-05-recordatorios-escalados`; todavía no fusionada en `main`. Incluye estado SQLite, backoff, voz corta/push prioritario final, resets de atención y cuenta atrás del dashboard. La integración y la validación física F2 siguen pendientes; no confundir implementación con aceptación en hardware.

El trabajo restante real se divide únicamente en dos frentes inmediatos y un backlog diferido:

```
┌────────────────────────────────────────────────────────────────────────┐
│                   FRENTE A: PRODUCTO & ONBOARDING (AT-11)              │
│   • M1–M4: IMPLEMENTADOS y fusionados (wizard, doctor, docs, V3 doc)   │
│   • Aceptación --milestone 4: 8 PASS · 0 FAIL · 1 BLOCKED (escen. 9)   │
│   • Pendiente: escenario 9 con servicios vivos + UAT V3 humano         │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│                   FRENTE B: VALIDACIÓN FÍSICA REAL (F2)                │
│   • Sesión con dispositivo real (Chrome Android + Micrófono host)      │
│   • Verificación en mano de: corte <0.3s (VS1), audio incremental (VS2)│
│     botones ntfy de aprobación (F5) y dictado PTT por voz (HT-01)      │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│                   BACKLOG DIFERIDO (HOST PLUGIN & MOTOR P3/P4)         │
│   • HT-10: Chain replay contextual (prioridad P5)                      │
│   • AT-05 (Prosodia); AT-06 Ducking M1 hecho (M2 winhost pendiente)    │
│   • AT-07 (Digest de audio) — IMPLEMENTADO (F2 u móvil pendiente)      │
└────────────────────────────────────────────────────────────────────────┘
```

---

### Frente A — Completar AT-11 (Producto Instalable y Onboarding)

- **Especificación:** `openspec/changes/at-11-instalable/tasks.md` y `docs/prds/AT-11-instalable-first-run.md`.
- **Estado actual (todo fusionado en `main`, merge `db660b0`):**
  - **Milestone 1 y 2:** COMPLETADOS (incluye 2.6, escenario 7 `reinstall-idempotent`; re-slice 9a/9b/9c de 2.1 registrado en historial).
  - **Milestone 3:** IMPLEMENTADO — wizard (`dc072c9`), credenciales no-argv (`4512eb8`), voz/keymap (`d718948`), consentimiento STT (`52e3790`), cableado de launchers + health gate (`2dfeee1`).
  - **Milestone 4:** IMPLEMENTADO — doctor con 6 chequeos y remediación (`c991c36`), escenarios 8/9 (`460784d`), docs canónicas + `docs/installation/V3-checklist.md` (`f1207e0`, `8e96f04`), reenvío de autorización en el harness (`d63f29d`).
  - **Aceptación `clean-install.sh --milestone 4`:** 8 PASS · 0 FAIL · 1 BLOCKED. El escenario 5 ejecutó una descarga STT real desde Hugging Face (`stt: ready`).
- **Pendiente para cerrar AT-11:**
  1. **Escenario 9 (`post-wizard-health`):** requiere brain/TTS/herdr vivos en el sandbox; hoy BLOCKED por diseño.
  2. **UAT V3 humano:** ejecutar `docs/installation/V3-checklist.md` en máquina limpia, con ambas lecturas de tiempo y firma.

---

### Frente B — Validación Física en Hardware Real (F2)

Todo el código de Voice Stack (VS0 a VS4), STT Core (F4) y botones ntfy (F5) está listo y testeado en software hermético. Solo resta la sesión física en vivo:
- **Dispositivo:** Teléfono Android con Chrome vía LAN o Tailscale.
- **Acciones:**
  1. Validar corte en altavoz del teléfono en <0.3s (VS1).
  2. Validar streaming de audio incremental en Chrome móvil (VS2).
  3. Validar botones interactivos `Approve`/`Stop` de ntfy y confirmación en `/approval`.
   4. Validar dictado Push-to-Talk (HT-01) en el host Windows con captura MCI.
   5. Validar HT-05 en uso real: recordatorios cortos, push ntfy final de prioridad alta y corte de recordatorios al atender, responder o silenciar el chat. Medir la baseline y la respuesta real del operador; los tests sintéticos no sustituyen esas métricas.
- **Cierre:** Anotar lecturas en `docs/voice-stack/archives/` y marcar `docs/voice-stack/MANUAL-TESTS.md`.

---

### Frente C — Host Plugin: Estado de Bloques (BLOQUE-1 a BLOQUE-4)

- **BLOQUE-1 (Paquete motor — AT-09 + AT-04 + AT-08):**
  - **Estado:** **COMPLETADO (100%)**
  - Canal de control (1.1), daemon persistente vía única (1.2), y cola con prioridades y cadena (1.3).
- **BLOQUE-2 (Identidad vocal — HT-02 + HT-11):**
  - **Estado:** **COMPLETADO (100%)**
  - Hito 0 (HT-02 voces por agente, `voices.json`) e Hito 1 (HT-11 audición de voces interactiva en fzf, preview bilingüe, auto-stop y caché LRU 20 MB).
- **BLOQUE-3 (Loop móvil bidireccional — HT-04 → HT-05):**
  - **Estado:** **IMPLEMENTADO EN SOFTWARE**; integración de HT-05 en main y validación física F2 pendientes.
  - **Hito 1 (HT-04 / F5):** COMPLETADO y fusionado en `main` (`0b615d2`) — botones `✅ Approve` / `❌ Stop` / `📱 Open` en push ntfy hacia `/approval/{gate}/action` del brain.
  - **Hito 2 (HT-05 — IMPLEMENTADO):** Recordatorios escalados sobre `pending_queue.py`, opt-in (`TTS_REMINDERS=off` por defecto), estado SQLite separado de VS3 y debounce sin pérdida de progresión. Por defecto avisa a los 5, 7 y 12 minutos, hasta 3 veces; el último añade push ntfy de prioridad alta. Foco observado durante >2 s, PTT, working, mute y snooze reinician la atención; el dashboard muestra `⏰ mm:ss` junto al snooze mediante lectura sin mutación. [Pack de ejecución](../odd/tasks/ht-05-recordatorios-escalados.md).
  - **Hito 3 (HT-04b):** DESCARTADO / BLOQUEADO (texto libre remoto descartado por diseño security-first).
- **BLOQUE-4 (Radio mode + verificación OpenCode — HT-03 + AT-03):**
  - **Estado:** **PARCIAL**
  - **Hito 0 (AT-03 verificación OpenCode):** COMPLETADO (lookup SQLite de OpenCode por ID).
  - **Hito 1 (HT-03 Radio mode core):** COMPLETADO y fusionado en `main` (`dd496c3`) — `herdr-tts --radio` y command id `radio`.
  - **Hito 2 (Mejoras progresivas y métricas — PENDIENTE):**
    - 2a (identidad hablada HT-02) y 2b (encolado priorizado con fallback): IMPLEMENTADOS.
    - 2c (resumen LLM `TTS_RADIO_LLM_SUMMARY=on`): PENDIENTE (opcional / backlog).
    - Medición de métricas de aceptación sobre uso real (tasa de abandono <30%, primer audio <1.5s): PENDIENTE.

---

### Frente D — Backlog Diferido (Motor Periférico P3/P4)

1. **AT-07 — Digest de audio (`--digest`):** P3. ✅ **IMPLEMENTADO EN SOFTWARE** (`feat/at-07-digest-audio`): ventana `--since/--date`, merge consciente de contenedor (MP3→join+CHAP; WAV→normaliza, sin capítulos), capítulos ID3v2 CHAP, integración podcast con aviso en la rama WAV. Validación móvil real (2 apps) pendiente. Es la base de `HT-08`. Pack: [`odd/tasks/at-07-digest-audio.md`](../odd/tasks/at-07-digest-audio.md).
2. **AT-06 — Ducking de audio:** P3. **M1 local ✅ implementada y fusionada** en `main` (detección `pactl` read-only, política `duck|pause|ignore` opt-in con default `ignore`, gain software con restauración exacta, `comm_active` en status; cero configuración del sistema). **M2 winhost ⚠️ transporte hecho** (consulta `activity` request-reply + `gain_db` en el header v1); la **clasificación WASAPI nativa queda pendiente** (Windows-only, validación manual M-01). Pack: [`odd/tasks/at-06-ducking-audio.md`](../odd/tasks/at-06-ducking-audio.md).
3. **AT-05 — Prosodia consciente de estado:** P4. Modulación de velocidad/tono según la urgencia del evento.
4. **HT-10 — Chain replay contextual:** P5. Replay de cadena de eventos en el host.
