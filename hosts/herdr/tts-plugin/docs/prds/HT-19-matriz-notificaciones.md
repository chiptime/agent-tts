**ID**: PRD-HT-19 · **Proyecto**: agent-tts (host plugin `herdr-tts` + motor)
**Prioridad**: P2 · **Estado**: **Aprobada y EJECUTADA (F1–F3)** en `feat/ht19-notification-matrix` (sin publicar; merge/push a decisión del maintainer). La matriz es normativa para todo push futuro. Pendiente: F4 (activación de N4 + F2 física de toda la matriz en Android).
**Dependencias**: HT-17 (sequence IDs, ya en `main`) para el "estado mudo"; HT-05 (maquinaria de atención: foco >2 s, snooze, debounce) para N2/N3; HT-18 (heartbeat) para N4.

# PRD-HT-19 — Matriz de notificaciones push (catálogo y momentos)

### Resumen ejecutivo

Hoy el sistema notifica **eventos** del agente (done/blocked/idle), y los eventos no coinciden con los **momentos que le importan al operador**. Resultado: llegan muchas y sirven poco. Esta PRD fija el catálogo cerrado de notificaciones push — qué existe, cuándo dispara cada una, con qué prioridad — y qué deja de notificarse. Principio rector: **interrumpir solo cuando hay que actuar, o cuando no actuar ahora va a costar más tarde; todo lo demás es estado visible, no sonido.**

### Semántica confirmada (decisión del maintainer, 2026-11-17)

"Enfriamiento del contexto" = **desatención progresiva del operador** sobre una conversación que lo necesita (NO compactación del contexto del agente). Frío = el punto donde re-entrar cuesta reconstruir el hilo. La notificación correcta llega **antes** de ese punto.

### El catálogo

| # | Notificación | Disparo exacto | Prioridad | Estado |
|---|---|---|---|---|
| **N1** | Acción requerida (pregunta / permiso / blocked) | Inmediato al crearse el gate | 4–5 + botones F5 + deep link al chat | **Ya existe** (F5 + pane deep link) |
| **N2** | Fin de turno | Turno completado + sobrevivió la ventana de coalescing existente + operador **sin atender** (foco observado >2 s reinicia la atención, como HT-05) | 3 | F2: gating por atención sobre el `done` actual |
| **N3** | Pre-enfriamiento | Ítem pendiente sin atender y acercándose al punto de re-entrada cara: UNA notificación a `T_cold − T_warn` que de ahí solo se **actualiza** (HT-17) mientras siga desatendido; escalada única a prioridad 5 en `T_cold` | 3 → 5 | F3: evolución de HT-05 — sus 3 pushes (5/7/12 min) colapsan en 1 mutante |
| **N4** | Host/daemon caído | Dead man's switch de HT-18 | 4 | **Ya existe** (opt-in, `TTS_HEARTBEAT=on`) |
| **N5** | Panel de flota (opcional) | Nunca interrumpe: un sequence ID por host con "2 working · 1 blocked", solo actualización | 1 (muda) | Sin planificar; agregar solo si el maintainer extraña el overview tras los cortes |

**Defaults de N3:** `T_cold ≈ 10 min`, `T_warn ≈ 3 min` → primer aviso a los ~7 min de desatención. Ajustables por config.

### Estado mudo (jamás push nuevo ni sonido)

`working` / `idle` / `done` como **cambios de estado** solo actualizan la notificación viva del agente (modo HT-17 `update`); ítem resuelto → auto-clear (`update+clear`). El estado se consulta abriendo el drawer; no interrumpe. No existe ni existirá push standalone de `working`.

### Cortes (decididos)

1. **Push standalone de `idle`** — FUERA del catálogo: hoy dispara en `working→idle` (bin/herdr-tts, rama `idle`). Se corta con knob de restauración `NTFY_IDLE_PUSH=off|on` (default `off`; `on` reproduce el comportamiento previo exacto). **Ejecutado en F1, este ciclo.**
2. **Los 3 pushes de escalación de HT-05** — absorbidos por N3 cuando F3 se implemente (no antes: N3 debe existir).
3. **Push de `working`** — nunca existió; la matriz lo prohíbe explícitamente.

### Fases

- **F1 (ejecutada, `feat/ht19-notification-matrix`):** corte de `idle` con knob + casos de test (RED→GREEN) + esta PRD. Activación recomendada en uso real: `NTFY_SEQUENCE=update`.
- **F2:** N2 — gating por atención del push de `done` (reuso de la maquinaria de foco de HT-05).
- **F2b (añadida tras la revisión de caché, 2026-11-17):** **modo medición de caché** — sondeo read-only del usage (`cached_tokens` y equivalentes) desde los transcripts que los conectores del motor ya leen; reporta hit/miss por sesión tras cada gap y el **TTL observado**; sin push, sin daemon. Si una fuente no expone usage, lo declara ("no medible para \<agente\>") en vez de adivinar. Con esos datos se decide si existe N3b/keepalive o mueren. Fundamento: el caché no es observable en vivo y el TTL varía por proveedor (Anthropic 5 min/1 h documentados; GLM no documentado, pero expone `prompt_tokens_details.cached_tokens` por response).
- **F3:** N3 — timer predictivo `T_cold − T_warn` con notificación mutante única + escalada única a 5; colapso del lado push de HT-05 (la voz mantiene su escalada propia).
- **F4:** activación de N4 en operación real y F2 física de todo (sesión Android: matriz completa en dispositivo).

### Decisiones de ejecución (2026-11-17, maintainer: "me cierra, dejémoslo así y termina el desarrollo")

- N2 con `NTFY_DONE_UNATTENDED_ONLY=on` **por defecto** (el corte de ruido es la feature aprobada; `off` restaura el push incondicional byte-a-byte). El push de `blocked` (N1) jamás se gatea.
- N3 con `NTFY_PRECOOL=off` por defecto (interrupción nueva ⇒ opt-in, convención del repo); `NTFY_PRECOOL_COLD_S=600`, `NTFY_PRECOOL_WARN_S=180`, validación `warn < cold`. Con `on`, el push final de alta de HT-05 difiere a N3 (sin duplicar); con `off`, HT-05 queda byte-idéntico.
- F2b vive en el motor (los conectores son del motor; "el motor es dueño del transcript"), read-only, sin push.

### Historias de usuario (US-HT-19-...)

- US-HT-19-1: Como operador, quiero que me interrumpan solo cuando hay que actuar (N1) o cuando esperar me va a costar caro (N3) — nada más.
- US-HT-19-2: Como operador distraído en otra cosa, quiero UN aviso antes de que se me enfríe el hilo, no tres recordatorios después.
- US-HT-19-3: Como operador, quiero ver el estado de la flota en el drawer sin que cada cambio de estado suene.
- US-HT-19-4: Como maintainer, quiero poder restaurar cualquier corte con un knob (regresión imposible por opt-out).

### Requisitos funcionales (RF-HT-19-...)

- RF-1: El push de `idle` no se emite por defecto; `NTFY_IDLE_PUSH=on` restaura el comportamiento previo byte-a-byte (misma request).
- RF-2: N2 solo dispara si el operador no atendió (foco del pane/terminal observado, umbral >2 s reutilizado de HT-05); atendido → sin push de fin de turno.
- RF-3: N3 emite exactamente una notificación nueva (a `T_cold − T_warn`); actualizaciones posteriores mientras siga desatendido usan el sequence ID (HT-17), sin nuevo push; exactamente una escalada a prioridad 5 en `T_cold`; atender/cortar detiene la secuencia.
- RF-4: Ningún cambio de estado working/idle/done genera push nuevo cuando `NTFY_SEQUENCE=update|update+clear` está activo (solo update in-place).
- RF-5: N4 sigue el diseño HT-18 sin cambios; su activación es decisión del maintainer.

### Riesgos y mitigaciones

- **Cortar idle puede esconder información** → knob RF-1 y N5 como válvula de escape futura.
- **T_cold/T_warn mal calibrados** → defaults conservadores, ajustables; medir en F2 física antes de tocar defaults.
- **F3 toca HT-05 (ya fusionado y probado)** → el colapso es solo del lado push; la escalación de voz y su lógica de atención quedan intactas; tests existentes de HT-05 deben seguir verdes.

### Plan de validación

F2 física (misma sesión pendiente de HT-17/18): matriz completa en Android — N1 con botones, N2 con/sin atención, N3 mutante con escalada única, idle silencioso, drawer como panel.
