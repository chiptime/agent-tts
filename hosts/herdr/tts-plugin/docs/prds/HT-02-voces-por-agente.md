**ID**: PRD-HT-02 · **Proyecto**: herdr-tts
**Prioridad final (revisión 2026-09-22)**: P1 · **Estado**: Aprobada
**Dependencias**: Ninguna (`--voice` ya existe por llamada)

# PRD-HT-02 — Voces por agente (identidad vocal)

**Prioridad**: Alta · **Esfuerzo**: S-M

## Resumen ejecutivo
Mapeo persistente pane→voz o agent-type→voz, resuelto por el watcher al construir cada llamada al motor (`--voice` ya existe por llamada hoy). Identidad visible (glifo o inicial de voz en dashboard y paleta) y audible (prefijo hablado opcional "Claude: ..."). En flota, responde la pregunta "¿quién está hablando?" sin mirar la pantalla.

## Problema y flujo actual
Con 4-5 agentes en paralelo, todas las notificaciones usan `TTS_VOICE` global: no hay forma de saber qué agente habló sin levantar la vista y buscar el pane. El dashboard ordena por atención, pero el canal auditivo es anónimo.

## Propuesta
Fichero dedicado `~/.config/herdr-tts/voices.json` con reglas por `agent_type` y override por `pane_id`, precedencia pane > agent_type > voz global. El watcher resuelve la voz al invocar el motor en cada evento. Las superficies muestran la identidad (inicial o glifo de la voz en la línea del roster y en el preview de la paleta) y, opcionalmente, la voz anuncia su nombre corto antes del texto.

## Historias de usuario (US-HT-02-1, ...)
- **US-HT-02-1** — Como operador de flota, quiero que cada agente tenga su propia voz, para identificar al que habla sin mirar la pantalla.
- **US-HT-02-2** — Como operador con dos chats del mismo agent_type, quiero sobrescribir la voz de un pane concreto, para distinguirlos entre sí.
- **US-HT-02-3** — Como usuario del dashboard, quiero ver qué voz tiene cada chat, para gestionar la identidad sin recordar el fichero.
- **US-HT-02-4** — Como usuario que escucha de lejos, quiero un prefijo hablado opcional con el nombre del agente, para identificar incluso con voces parecidas.

## Requisitos funcionales (RF-HT-02-...)
- **RF-HT-02-1**: `voices.json` con esquema `{ "agent": { "<agent_type>": "<voz>" }, "pane": { "<pane_id>": "<voz>" }, "prefix": <bool> }`, validado al cargar; error de esquema = aviso accionable y fallback a voz global.
- **RF-HT-02-2**: precedencia resuelta pane_id > agent_type > `TTS_VOICE`; la resolución se aplica a eventos automáticos y a las lecturas on-demand del chat (play/TLDR).
- **RF-HT-02-3**: el watcher pasa `--voice <resuelta>` en cada llamada al motor; voz resuelta inválida = fail-open a voz global con una línea en `daemon.log`.
- **RF-HT-02-4**: dashboard: la línea del roster muestra la inicial o glifo de la voz del chat; paleta: igual en la cabecera del preview.
- **RF-HT-02-5**: prefijo hablado opcional (`prefix: true`, default off): sintetiza el nombre corto del agent_type antes del texto, con la propia voz del agente.
- **RF-HT-02-6**: gestión por CLI: `herdr-tts --voice-for agent <tipo> <voz>` y `herdr-tts --voice-for pane <pane_id> <voz>` (conecta con la asignación desde paleta de HT-11).
- **RF-HT-02-7**: rotación automática opt-in: agent_type sin voz asignada toma la siguiente voz de una paleta corta determinista de voces del proveedor activo (`"auto_assign": true`, default off).

## Requisitos no funcionales (RNF-HT-02-...)
- **RNF-HT-02-1**: coste por evento nulo: resolución en memoria del watcher con el fichero cacheado por mtime.
- **RNF-HT-02-2**: cero spawns adicionales en el sweep del daemon.
- **RNF-HT-02-3**: sin regresión de huella: el host sigue por debajo de ~2.5 MB RAM.

## Encaje en la arquitectura actual
El watcher ya construye la llamada al motor con `--agent`/`--session-id`; añadir `--voice` es un parámetro más de esa misma llamada. El roster del dashboard ya fusiona `herdr agent list` con los ledgers bajo la clave `pane_id`: la identidad vocal es un campo más de esa fusión. La paleta ya muestra cabecera por chat en el preview.

## Prior art y diferenciación
Ninguno de los proyectos comparados (`herdr-bleatr`, `herdr-tts` de Aktrov, `herdr-announcer`) parametriza identidad por agente: una sola voz global. `agentvoice` y `claude-code-tts` (Claude Code) encolan eventos con worker pool pero tampoco asignan identidad vocal por agente. Diferenciación: identidad persistente ligada al concepto de chat de Herdr y visible en las superficies interactivas existentes.

## Dependencias
Ninguna: el motor acepta `--voice` por llamada hoy mismo.

## Riesgos y mitigaciones
- Dos chats del mismo agent_type seguidos → override por pane (RF-HT-02-2) + prefijo hablado (RF-HT-02-5).
- Rotación automática que asigna voces de proveedores sin instalar (piper/kokoro) → solo rotar entre voces del proveedor activo.
- Deriva del fichero editado a mano → validación al cargar con aviso accionable, nunca crash del watcher.

## Métricas de éxito
1. Identificación correcta del agente que habla sin mirar la pantalla en >= 80% de los eventos (autoinforme tras dos semanas).
2. Cero regresión de RAM (< 2.5 MB) y cero spawns extra por sweep.
3. Al menos un override por `pane_id` en uso real tras dos semanas (valida el nivel fino del mapeo).

## Fuera de alcance
Clonación de voz, voces por proyecto/workspace, mezcla de proveedores por agente (cada evento usa el proveedor activo; solo cambia la voz).

## Open questions
¿Fichero dedicado `voices.json` o claves en `config.env`? ¿El prefijo hablado usa el `agent_type` tal cual o un alias configurable por agente?
