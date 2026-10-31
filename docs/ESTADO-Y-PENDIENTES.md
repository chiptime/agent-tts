# Monorepo agent-tts — Roadmap de Temas Pendientes

**Fecha de consolidación:** 30 de octubre de 2026 (anterior: 11 de octubre de 2026)

**Punto de anclaje:** `main` = `origin/main` = `2c550cf` (remapeo de ids citados tras reescribir el historial). Fusionados en `main`: F4 (STT Core), F5 (botones ntfy), HT-03 (Radio mode), Karaoke Inline Fragments, AT-11 M1–M4, **HT-05** (recordatorios escalados), **AT-07** (digest de audio), **AT-06** (ducking M1 + transporte winhost), el fix de carrera de shutdown del daemon (`07ae48f`) y el **programa de paridad del brain F1–F6** (merge `2c6f712`, claves push automáticas `254dc83`). CI de `main` en verde en los cuatro jobs (fixture del motor corregido en `7548398`).  
**Propósito:** Servir como brújula estricta y veraz de los únicos trabajos pendientes reales en el monorepo.

---

## 1. Mapa de la Realidad del Monorepo

> **Clarificación de estado:** Funcionalidades como **Karaoke Fragments** (`61c4f10`), **Open-Session Inventory**, **Radio Mode HT-03** (`97bc94d`), **Botones ntfy F5** (`ee09756`), **Audición de voces HT-11** (`402fb35`), **Voice Stack Core VS0–VS4 / F4** y el **programa de paridad del brain F1–F6** ya están **100% implementadas y fusionadas en `main`**.

**HT-05 (BLOQUE-3 Hito 2):** ✅ **implementada y fusionada en `main`**. Solo queda la **validación física F2**; no confundir implementación con aceptación en hardware.

El trabajo restante real se divide en decisiones de publicación/corte, dos frentes de validación y un backlog diferido:

```
┌────────────────────────────────────────────────────────────────────────┐
│          FRENTE 0: PARIDAD DEL BRAIN — PUBLICACIÓN Y CORTE             │
│   • F1–F6, mensajes entre hosts de crew y salida de Zen en main local  │
│     (merges 2f9e855 y zen-exit, sin publicar)                          │
│   • Pendiente: push, validaciones doc 11, dos máquinas, corte en vivo  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
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
│     botones ntfy (F5), dictado PTT (HT-01) y métricas reales de HT-05  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│                   BACKLOG DIFERIDO (HOST PLUGIN & MOTOR P3/P5)         │
│   • HT-10: Chain replay contextual (prioridad P5)                      │
│   • AT-05 (Prosodia); AT-06 M2 clasificación WASAPI pendiente          │
│   • AT-07 validación móvil pendiente; motor: Piper pipelining,         │
│     CI Windows                                                         │
└────────────────────────────────────────────────────────────────────────┘
```

---

### Frente 0 — Paridad del brain: publicación y corte

- **Fusionado en `main` (F1–F6):** historial paginado y espejo de pane, diálogos por harness con botones, configuración TOML del operador (respuestas, comandos, presets de teclas, launchers por allowlist), shell offline + aviso de actualización + i18n `es`/`en`, Web Push VAPID (sobre `cryptography`) junto a ntfy, puerto de multiplexer (herdr, tmux, zellij), federación crew lead/peer con mTLS pineado y secreto de crew, tooling de corte (`serve`/`unserve`/`qr`/`logs`, chequeos de `doctor`, unidad launchd), lecturas con token de dispositivo + caducidad y rate limit de `/pair`, envío verificado con recibos + Stop/Steer, mensajes entre panes locales, enmascarado de secretos y generación automática de claves VAPID al primer arranque.
- **Fusionado en `main` local (merge `2f9e855` de `feat/brain-crew-messages`, 7 commits sobre `254dc83`; sin publicar):** `herdr-brain msg` entre hosts del crew — direcciones `<host>:<pane>#<token>`, entrega en el host dueño con la misma compuerta, identidad de origen atestiguada por transporte, límites antilazo en emisor y receptor, `status`/`cancel` remotos, auditoría en cada host. Incluye el fix de certificado cliente en llamadas crew salientes (`5d3ddd4`), un defecto previo de F5 que rompía todo handshake real lead→peer. Verificación independiente: S1–S11 cumplidas tras una corrección (`88febc3`) y recheck.
- **Pendiente (decisiones del maintainer):**
  1. Push de `main` (incluye el merge de mensajes entre hosts; suite unitaria del brain 3349 OK y JS 576 OK tras el merge).
  2. Validación en vivo con dos máquinas reales del crew (hoy solo probado con mTLS en loopback).
  3. **Corte en vivo** al brain (tomar 443, importar/activar push, retirar el panel anterior). Lo ejecuta el usuario; ningún agente lo hace.
- **Fallos conocidos fuera de este trabajo:** 1 test de navegador y 4 e2e fallan también en la base `254dc83` (banner offline en red aislada y motor no disponible en el entorno de pruebas).

---

### Frente A — Completar AT-11 (Producto Instalable y Onboarding)

- **Especificación:** `openspec/changes/at-11-instalable/tasks.md` y `docs/prds/AT-11-instalable-first-run.md`.
- **Estado actual (todo fusionado en `main`, merge `c16e398`):**
  - **Milestone 1 y 2:** COMPLETADOS (incluye 2.6, escenario 7 `reinstall-idempotent`; re-slice 9a/9b/9c de 2.1 registrado en historial).
  - **Milestone 3:** IMPLEMENTADO — wizard (`7c86502`), credenciales no-argv (`e83bc21`), voz/keymap (`bf3cbe1`), consentimiento STT (`c63faf0`), cableado de launchers + health gate (`09ba15f`).
  - **Milestone 4:** IMPLEMENTADO — doctor con 6 chequeos y remediación (`d7aee90`), escenarios 8/9 (`aa6fe1e`), docs canónicas + `docs/installation/V3-checklist.md` (`0255935`, `f76b834`), reenvío de autorización en el harness (`dc9401b`).
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
  6. Con el brain nuevo: Web Push en el teléfono emparejado (suscripción, coalescing y retracción) y recibos de envío verificado.
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
  - **Estado:** **IMPLEMENTADO Y FUSIONADO EN `main`**; validación física F2 pendiente.
  - **Hito 1 (HT-04 / F5):** COMPLETADO y fusionado en `main` (`ee09756`) — botones `✅ Approve` / `❌ Stop` / `📱 Open` en push ntfy hacia `/approval/{gate}/action` del brain.
  - **Hito 2 (HT-05 — IMPLEMENTADO):** Recordatorios escalados sobre `pending_queue.py`, opt-in (`TTS_REMINDERS=off` por defecto), estado SQLite separado de VS3 y debounce sin pérdida de progresión. Por defecto avisa a los 5, 7 y 12 minutos, hasta 3 veces; el último añade push ntfy de prioridad alta. Foco observado durante >2 s, PTT, working, mute y snooze reinician la atención; el dashboard muestra `⏰ mm:ss` junto al snooze mediante lectura sin mutación. [Pack de ejecución](../odd/tasks/ht-05-recordatorios-escalados.md).
  - **Hito 3 (HT-04b):** DESCARTADO / BLOQUEADO (texto libre remoto descartado por diseño security-first).
- **BLOQUE-4 (Radio mode + verificación OpenCode — HT-03 + AT-03):**
  - **Estado:** **PARCIAL**
  - **Hito 0 (AT-03 verificación OpenCode):** COMPLETADO (lookup SQLite de OpenCode por ID).
  - **Hito 1 (HT-03 Radio mode core):** COMPLETADO y fusionado en `main` (`97bc94d`) — `herdr-tts --radio` y command id `radio`.
  - **Hito 2 (Mejoras progresivas y métricas — PENDIENTE):**
    - 2a (identidad hablada HT-02) y 2b (encolado priorizado con fallback): IMPLEMENTADOS.
    - 2c (resumen LLM `TTS_RADIO_LLM_SUMMARY=on`): PENDIENTE (opcional / backlog).
    - Medición de métricas de aceptación sobre uso real (tasa de abandono <30%, primer audio <1.5s): PENDIENTE.

---

### Frente D — Backlog Diferido (Motor Periférico P3/P5)

1. **AT-07 — Digest de audio (`--digest`):** P3. ✅ **IMPLEMENTADO EN SOFTWARE**: ventana `--since/--date`, merge consciente de contenedor (MP3→join+CHAP; WAV→normaliza, sin capítulos), capítulos ID3v2 CHAP, integración podcast con aviso en la rama WAV. Validación móvil real (2 apps) pendiente. Es la base de `HT-08`. Pack: [`odd/tasks/at-07-digest-audio.md`](../odd/tasks/at-07-digest-audio.md).
2. **AT-06 — Ducking de audio:** P3. **M1 local ✅ implementada y fusionada** en `main` (detección `pactl` read-only, política `duck|pause|ignore` opt-in con default `ignore`, gain software con restauración exacta, `comm_active` en status; cero configuración del sistema). **M2 winhost ⚠️ transporte hecho** (consulta `activity` request-reply + `gain_db` en el header v1); la **clasificación WASAPI nativa queda pendiente** (Windows-only, validación manual M-01). Pack: [`odd/tasks/at-06-ducking-audio.md`](../odd/tasks/at-06-ducking-audio.md).
3. **AT-05 — Prosodia consciente de estado:** P4. Modulación de velocidad/tono según la urgencia del evento.
4. **HT-10 — Chain replay contextual:** P5. Replay de cadena de eventos en el host.
5. **Motor (ítems abiertos del roadmap del README):** pipelining por proveedor para Piper (TD-01), cobertura CI en Windows nativo (WASAPI y loopback TCP) e integración con Orca (TD-03, a la espera de requisitos del maintainer).

Deuda técnica, residuales aceptados y mediciones abiertas: [`docs/deuda-tecnica.md`](deuda-tecnica.md).

> El streaming MP3 por frames (AT-01) está **EJECUTADO** y en `main` (`196ed56`, PRD archivada en `docs/prds/archivadas/AT-01-streaming-frames-mp3.md`); figuraba aquí como pendiente por error.
