# BLOQUE 3 — Loop móvil bidireccional (HT-04 → HT-05)

**Alcance**: PRD-HT-04 + PRD-HT-05 · **Prioridad**: P1 · **Esfuerzo agregado**: L + M
**Repositorio**: herdr-tts · **Estado**: Aprobado (revisión 22/09/2026)

### Objetivo del bloque

Cerrar el loop móvil del operador: convertir el push ntfy (hoy de solo lectura) en un canal bidireccional de acciones acotadas (HT-04) y hacer que el sistema insista de forma escalonada cuando el aviso inicial se pierde (HT-05). Juntas, las dos PRD completan el ciclo atención → respuesta → re-memoria sin que el móvil se convierta nunca en un shell remoto.

Este fichero es la capa de orquestación del paquete: define el orden, el rationale y los criterios de aceptación por hito. Los requisitos funcionales y no funcionales completos viven en las PRD fuente y aquí solo se referencian; nada de este documento sustituye a aquellas.

### Por qué secuencial y no paralelo (rationale)

1. **Mismo builder de notificaciones.** HT-04 reescribe el builder del push para añadir `Actions` http; HT-05 lo extiende para añadir la cabecera de prioridad del último recordatorio. Son dos reescrituras de la misma función (`send_ntfy_push()`): en paralelo obligan a resolver dos veces el mismo merge.
2. **Mismo gating ledger.** HT-04 añade el reset por respuesta remota; HT-05 añade el estado de recordatorios por pane (`first_attention_ts`, `reminder_count`, `next_reminder_ts`). Ambos tocan el mismo fichero JSON con el patrón incremental de claves nuevas: en secuencia, el segundo construye sobre el esquema ya cerrado por el primero.
3. **Continuidad de producto en la lane P1 (decisión del maintainer).** HT-05 es la continuación natural de HT-04: HT-04 cierra el loop atención→respuesta; HT-05 hace que ese loop persiga al operador cuando este se despista. Entregar HT-05 antes dejaría recordatorios que empujan hacia una respuesta que aún no puede darse desde el móvil.

Dos frentes en paralelo sobre esas superficies compartidas equivalen a merge hell. La secuencia Hito 1 → Hito 2 → Hito 3 maximiza el valor por iteración y solo el tramo final queda bloqueado por el prerrequisito externo.

### Secuencia de ejecución: hitos con criterios de aceptación

**Hito 1 — HT-04a: control de botones security-first (no requiere inyección de texto)**

Endpoint HTTP dentro del daemon existente (`POST /herdr-tts/reply`) con token aleatorio obligatorio, bind `127.0.0.1` por defecto (LAN como opt-in explícito), rate-limit de 10 peticiones/minuto, TTL de 10 min sobre el pane referenciado, auditoría por acción en `daemon.log` y acción "Detener" en la notificación, que reutiliza la vía de `--stop`. Funciona tras `TTS_REMOTE_CONTROL="off|on"`, default `off`. Detalle completo en `../HT-04-control-movil-bidireccional.md` (RF-HT-04-1 a 9).

Criterios de aceptación:
- Cero acciones ejecutadas con token inválido.
- Con `TTS_REMOTE_CONTROL="off"`, el push se comporta byte a byte igual que el actual.
- Latencia acción→pane < 1 s en LAN.

**Hito 2 — HT-05: recordatorios escalados**

Estado por pane en el gating ledger (`first_attention_ts`, `reminder_count`, `next_reminder_ts`), backoff 2m → 5m → 15m, máximo 3 recordatorios; el último escala a push ntfy prioritario reutilizando el builder de prioridad estrenado en el Hito 1. Cada recordatorio incluye texto accionable acotado (~120 caracteres, saneado por el redactor de secretos del motor) conforme a RF-HT-05-8: un recordatorio sin contexto de qué hacer no vale. Reset del estado por foco sostenido > 2 s, respuesta inyectada, cambio de estado del agente o mute/snooze del chat (RF-HT-05-3). Detalle completo en `../HT-05-recordatorios-escalados.md`.

Criterios de aceptación:
- Mediana bloqueo→respuesta menor que la baseline, medida ANTES de activar la feature.
- Cero recordatorios sobre chats atendidos o con mute/snooze activo.
- Cuenta atrás del próximo recordatorio visible en el dashboard.

**Hito 3 — HT-04b: texto libre (bloqueado por prerrequisito externo)**

Inyección de respuestas y prompts como texto en el pane notificado, con las semánticas de RF-HT-04-4/5/6 y la misma regla de Enter que HT-01. Estado: BLOQUEADO por el prerrequisito externo de la sección siguiente. Se planifica dentro del bloque; no se compromete fecha.

### Prerrequisito externo y acción día 1

El verbo de inyección de texto en Herdr core (`herdr pane send-keys` o equivalente) NO existe: la superficie disponible hoy es solo `pane read/focus/rename` y `agent wait/get/list/focus`. Sin ese verbo, el Hito 3 no puede implementarse.

Acción del día 1 del bloque: abrir la negociación (issue o conversación) con Herdr core sobre el verbo de inyección y documentar su estado en este fichero y en el índice del roadmap. Los Hitos 1 y 2 NO dependen de esa negociación y arrancan de inmediato.

### Qué NO se incluye

- App nativa.
- Comandos arbitrarios desde el móvil: lista blanca estricta (confirmar / detener / texto inyectado); el texto libre jamás se interpreta como comando de shell.
- Streaming bidireccional de audio.
- Exposición fuera de LAN/Tailscale.

El detalle completo de fuera de alcance está en las secciones correspondientes de ambas PRD fuente.

### Superficies compartidas y zonas de conflicto

- **Builder de notificaciones** (`send_ntfy_push()`): Actions (Hito 1) y cabecera de prioridad (Hito 2) son extensiones del mismo builder; el Hito 2 reutiliza lo estrenado en el Hito 1, por eso el orden importa.
- **Gating ledger** (`herdr-tts-snooze.json`): el reset por respuesta remota (HT-04) y el estado de recordatorios (HT-05) conviven en el mismo fichero; el Hito 2 define sus claves sabiendo que el Hito 1 ya movió el esquema.
- **Bucle del daemon**: el listener HTTP best-effort (Hito 1) y la comprobación de vencimientos O(chats) del sweep (Hito 2) comparten el patrón fail-open ya usado por la purga de retención.
- **Topic ntfy**: control y recordatorios comparten topic; ver riesgos para la opción de separarlo.

### Riesgos específicos del paquete

- **Topic ntfy compartido**: token obligatorio en todas las Actions; considerar un topic de control separado del topic de notificación (open question de HT-04).
- **Soporte de Actions http en iOS**: verificar en las apps ntfy realmente utilizadas (Android e iOS) ANTES de arrancar el Hito 1; si no bastan, evaluar view-actions.
- **Auditoría sin datos sensibles**: el log remoto registra timestamp, acción, `pane_id` y solo los primeros 40 caracteres del texto; nunca audio ni texto completo.
- **Seguridad como invariante transversal**: el móvil jamás es un shell remoto — sin ejecución de comandos, sin lectura arbitraria de estado, sin escritura de configuración (RNF-HT-04-4). Cualquier hito que rompa este invariante se detiene hasta remediarse.

### Definición de done del bloque

- Hitos 1 (HT-04a) y 2 (HT-05) en producción, con sus métricas de éxito medidas sobre uso real; la baseline de bloqueo→respuesta se toma antes de activar los recordatorios.
- Hito 3 (HT-04b) planificado, con el estado de la negociación del verbo de inyección con Herdr core documentado.
- Índice `docs/prds/README.md` actualizado reflejando este bloque y el estado de sus hitos.

### Referencias

- `../HT-04-control-movil-bidireccional.md` — PRD-HT-04: RF/RNF completos, métricas y open questions.
- `../HT-05-recordatorios-escalados.md` — PRD-HT-05: RF/RNF completos, métricas y open questions.
- `../README.md` — índice del roadmap y orden de ataque (Fase 1: HT-04 → HT-05).
