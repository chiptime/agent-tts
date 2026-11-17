**ID**: PRD-HT-17 · **Proyecto**: agent-tts (host plugin `herdr-tts`)
**Prioridad**: P2 · **Estado**: Borrador (documento de intención; NO autoriza implementación ni commits)
**Dependencias**: Recomendado después del corte en vivo del brain (Frente 0 / F2). Requiere ntfy server ≥ v2.18 (o ntfy.sh) para sequence IDs.

# PRD-HT-17 — Notificaciones ntfy actualizables por agente (sequence IDs)

### Resumen ejecutivo

Hoy cada evento de agente (`done`/`blocked`/`idle`) emite una notificación nueva e independiente. Con N agentes en flota, el drawer del teléfono se llena de notificaciones muertas y un `blocked` ya resuelto sigue pidiendo atención eternamente. ntfy ≥ v2.18 soporta **secuencias**: publicar con un *sequence ID* estable reemplaza la notificación previa *in-place* en el cliente. Esta PRD propone una sola notificación viva por agente que muta al último estado, con auto-dismiss opcional al resolverse.

### Problema y flujo actual

`bin/herdr-tts` publica pushes one-shot por evento (títulos `✅ %s finished`, `🔴 %s needs attention`, `💤 %s idle`) sin identidad de secuencia. Consecuencias:

- El drawer acumula el historial completo de eventos en vez de reflejar el estado actual.
- Un `blocked` atendido desde el brain (F5) deja la notificación huérfana rogando atención.
- HT-05 escala sobre pushes que nunca se cancelan solos.

### Propuesta

1. **Sequence ID estable por agente**: derivar del `pane_id`/agent ya usado por el plugin, saneado al charset de ntfy (`[-_A-Za-z0-9]`, ≤ 64). Publicación vía `POST /<topic>/<seq>` o header `X-Sequence-ID`.
2. **Update por defecto en cada evento**: la notificación del agente se reemplaza in-place; su contenido/prioridad/botones son los del último evento. En el server el cache queda append-only (el historial no se pierde).
3. **Clear/delete opt-in al resolverse**: tras `done`, `PUT /<topic>/<seq>/clear` (marca leída y la descarta del drawer) o `DELETE /<topic>/<seq>` (la elimina). Default inicial: solo update, sin borrado.
4. **Botones F5 intactos**: los `http` actions de HT-04 viajan dentro del propio mensaje update; nada cambia en `/approval/{gate}/action`.
5. **Fallback**: si el servidor rechaza sequence IDs (self-host < v2.18), degradar al one-shot actual, una línea de log, sin error visible.
6. **Knob**: `NTFY_SEQUENCE=off|update|update+clear` (default `off` hasta validación física; luego `update`).

### Historias de usuario (US-HT-17-...)

- US-HT-17-1: Como operador de flota, quiero una sola notificación por agente que muestre su último estado, para leer el drawer como un panel y no como un log.
- US-HT-17-2: Como operador, quiero que un `blocked` resuelto deje de pedir atención (actualizado a `done` o descartado), para no atender fantasmas.
- US-HT-17-3: Como maintainer, quiero un knob que restaure el comportamiento one-shot exacto (regresión imposible).

### Requisitos funcionales (RF-HT-17-...)

- RF-1: La identidad de secuencia se deriva del pane/agent id, saneada (`[-_A-Za-z0-9]`, ≤ 64) y estable entre eventos del mismo agente.
- RF-2: Cada evento publica con ese sequence ID; el cliente reemplaza la notificación previa (semántica update ntfy ≥ 2.18; cache del server append-only).
- RF-3: `clear`/`delete` tras `done` solo en modo `update+clear`, y solo si hubo notificación previa del mismo seq.
- RF-4: Botones HT-04 y pushes HT-05 preservan su comportamiento (los actions van dentro del mensaje update; el push de prioridad alta de HT-05 puede optar por no usar seq para no silenciarse).
- RF-5: Ante rechazo del seq por el server, fallback silencioso a one-shot con una línea de log.
- RF-6: `NTFY_SEQUENCE=off` reproduce el comportamiento actual exacto.

### Requisitos no funcionales (RNF-HT-17-...)

- RNF-1: Sin dependencias nuevas (bash + curl existentes en `bin/herdr-tts`).
- RNF-2: Misma latencia de publicación (un header o path extra).
- RNF-3: Casos `ntfy_*` de `tests/host_cli_cases.sh` cubren seq, fallback y knob.

### Encaje en la arquitectura actual

Funciones ntfy de `hosts/herdr/tts-plugin/bin/herdr-tts` (ramas done/blocked/idle de `send_ntfy_push`), configuración `NTFY_*` existente (`DEFAULT_NTFY_SERVER`, topic). Sin cambios en el brain ni en el motor.

### Prior art y diferenciación

El barrido previo (agentvoice, claude-code-tts, opencode-smart-voice-notify) emite notificaciones one-shot; ninguno usa sequence IDs para estado vivo por agente. La capacidad existe en ntfy desde v2.18 ([docs: Updating + deleting notifications](https://docs.ntfy.sh/publish/#updating-deleting-notifications)).

### Riesgos y mitigaciones

- **Server self-host < 2.18**: fallback RF-5; el doctor de AT-11 puede chequear la versión del server ntfy.
- **Reuso de seq tras reinicio de pane**: pane ids ya son estables en herdr; si colisionan, gana el último estado (colisión benigna, documentada).
- **Soporte del cliente iOS**: el replace in-place está validado en Android/web; verificar comportamiento de la app iOS en F2. Degradación = notificaciones adicionales (comportamiento actual), nunca error.

### Plan de validación (F2)

Sesión física Android: verificar replace in-place con 3 agentes activos, botones F5 tras update, y fallback contra un server viejo. Métrica: notificaciones acumuladas en drawer antes/después.
