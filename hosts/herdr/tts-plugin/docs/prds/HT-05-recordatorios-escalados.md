**ID**: PRD-HT-05 · **Proyecto**: herdr-tts
**Prioridad final (revisión 2026-09-22)**: P1 · **Estado**: Aprobada
**Dependencias**: Ninguna

# PRD-HT-05 — Recordatorios escalados de atención

> **Nota de revisión (2026-09-22)**: Continuación directa de HT-04 en la misma lane P1: HT-04 cierra el loop, HT-05 hace que el loop persiga al operador.

**Prioridad**: Media-Alta · **Esfuerzo**: M

## Resumen ejecutivo
Si un chat permanece en done/blocked sin foco ni respuesta durante N minutos, el sistema re-recorda con backoff exponencial (2m → 5m → 15m), hasta M recordatorios (default 3); el último escala a push ntfy prioritario. Estado en el gating ledger, limpiado al enfocar o responder, silenciable por chat con el mute/snooze existente.

## Problema y flujo actual
El aviso suena una vez. Si el usuario está concentrado en otra cosa (head-down), el evento se pierde y el agente espera indefinidamente: ni voz ni push volverán a señalarlo. Hoy no existe ningún mecanismo de re-memoria.

## Propuesta
Extensión del gating ledger con estado por pane: `first_attention_ts`, `reminder_count`, `next_reminder_ts`. El sweep del daemon (~4 s) comprueba vencimientos con el agent list ya en mano. Cada recordatorio es una frase corta ("Recordatorio: <título> sigue esperando"); el último añade cabecera de prioridad alta al push ntfy. El estado se limpia al enfocar el pane, responder (HT-01/HT-04) o cuando el agente cambia de estado.

## Historias de usuario (US-HT-05-1, ...)
- **US-HT-05-1** — Como operador concentrado en otra tarea, quiero que un agente bloqueado insista de forma escalonada, para no perderlo de vista sin saturarme.
- **US-HT-05-2** — Como operador que se aleja del PC, quiero que el último recordatorio llegue como push prioritario al móvil, para enterarme aunque no oiga la voz local.
- **US-HT-05-3** — Como operador, quiero que enfocar o responder al chat corte los recordatorios, para que no suene lo ya atendido.
- **US-HT-05-4** — Como operador en reunión, quiero silenciar los recordatorios de un chat con el snooze/mute existente, sin aprender controles nuevos.

## Requisitos funcionales (RF-HT-05-...)
- **RF-HT-05-1**: config: `TTS_REMINDERS="off|on"` (default off), `TTS_REMINDER_START_MIN` (default 5), `TTS_REMINDER_BACKOFF="2,5,15"` (minutos), `TTS_REMINDER_MAX` (default 3).
- **RF-HT-05-2**: estado por pane en el fichero JSON del gating ledger (claves nuevas junto a snooze/mute/debounce, patrón incremental existente); sobrevive reinicios del daemon.
- **RF-HT-05-3**: reset del estado al: foco sostenido del pane (> 2 s), respuesta inyectada (HT-01/HT-04), cambio de estado del agente (vuelve a working), o mute/snooze del chat.
- **RF-HT-05-4**: canal del recordatorio: si las puertas locales lo permiten, voz corta diferenciada del evento original (no relee el turno); si no, únicamente push ntfy.
- **RF-HT-05-5**: el último recordatorio (índice M) envía push con cabecera de prioridad alta de ntfy.
- **RF-HT-05-6**: mute y snooze del chat suprimen también los recordatorios, sin excepciones.
- **RF-HT-05-7**: dashboard: cuenta atrás del próximo recordatorio en la línea del chat, junto al snooze.
- **RF-HT-05-8**: Texto accionable en el recordatorio: cada recordatorio (voz y push) incluye un snippet acotado (aprox. 120 caracteres) del turno pendiente —la pregunta o decisión que espera el agente—, saneado por el redactor de secretos del motor. Un recordatorio sin contexto de qué hacer no vale.

## Requisitos no funcionales (RNF-HT-05-...)
- **RNF-HT-05-1**: coste del sweep sin cambios: comprobación O(chats) en memoria, sin spawns extra salvo el propio aviso.
- **RNF-HT-05-2**: los recordatorios atraviesan el debounce anti-spam global (nunca dos avisos solapados del mismo pane).
- **RNF-HT-05-3**: pérdida del estado en `/tmp` tras reinicio de la máquina = aceptable (re-armado en el siguiente evento).

## Encaje en la arquitectura actual
El sweep ya visita todos los panes cada ~4 s con el agent list cargado: comprobar vencimientos es una comparación de timestamps en memoria. El estado vive en el mismo fichero del gating ledger (`herdr-tts-snooze.json`) con claves nuevas, el mismo patrón usado al añadir debounce. El push prioritario reusa `send_ntfy_push()` con una cabecera más.

## Prior art y diferenciación
`opencode-smart-voice-notify` implementa recordatorios con backoff exponencial para permisos/errores/idle: prior art directo y reconocido como tal. Diferenciación: estado integrado en el ledger por `pane_id` del ecosistema Herdr, reset por foco sostenido o respuesta inyectada, y escalado final a push prioritario reutilizando el fan-out existente.

## Dependencias
Ninguna dura. La integración con HT-04 (respuesta remota como reset) es opcional y posterior.

## Riesgos y mitigaciones
- Fatiga de notificación → default off, máximo M, frases cortas, silenciado por chat.
- Falsos resets al navegar por los panes sin intención → foco sostenido > 2 s como criterio.
- Acumulación de estado en flotas grandes → purga de entradas de panes cerrados en la poda existente del daemon.

## Métricas de éxito
1. Mediana bloqueo→respuesta con recordatorios activos menor que la baseline medida sin ellos (medir baseline primero).
2. < 1% de chats con recordatorios activos después de atendidos (limpieza correcta).
3. Cero recordatorios sobre chats con mute/snooze activo.

## Fuera de alcance
Llamadas telefónicas, integración con calendario, recordatorios programados por hora del día (eso es HT-08).

## Open questions
¿El aviso local del recordatorio usa frase prefijada o tono/voz distinta? ¿M por chat o global por sesión? ¿Contar el foco sostenido en la paleta como atención?
