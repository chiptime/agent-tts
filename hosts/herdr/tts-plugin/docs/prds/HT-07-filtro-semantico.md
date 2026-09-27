**ID**: PRD-HT-07 · **Proyecto**: herdr-tts
**Prioridad final (revisión 2026-09-22)**: P4 · **Estado**: Postergada
**Dependencias**: Opcional: cadena LLM ya existente del motor

# PRD-HT-07 — Filtro semántico de importancia

**Prioridad**: Media-Alta · **Esfuerzo**: M

## Resumen ejecutivo
Compuerta de contenido antes de sintetizar: heurística offline (longitud mínima, presencia de pregunta al usuario, keywords de decisión/bloqueo, diff de estado done→blocked) y opcionalmente clasificación LLM reutilizando la cadena existente del motor (`claude -p` → `codex exec` → `ollama`). Config `TTS_IMPORTANCE="all|smart|minimal"`: los turnos triviales del agente activo dejan de consumir síntesis y atención.

## Problema y flujo actual
`scope: focused` elimina el ruido de otros panes, pero el agente enfocado lee TODO turno: los triviales ("listo", "hecho", continuaciones de trabajo) también disparan síntesis completa, push y robo de atención. El settle window filtra parpadeos de estado, no contenido trivial de un turno real.

## Propuesta
Capa de gate de contenido insertada tras la adquisición de texto y antes del fan-out. Clasifica el turno extraído (conector o scrollback) en relevante/trivial. `smart` (heurística al activar), `minimal` (solo blocked y preguntas directas), `all` (comportamiento actual). Opcional `TTS_IMPORTANCE_LLM="on"` delega la clasificación binaria a la cadena LLM del motor con timeout corto y fallback a heurística. Los turnos filtrados no sintetizan ni empujan push, pero quedan en el historial marcados como omitidos y son reproducibles on-demand.

## Historias de usuario (US-HT-07-1, ...)
- **US-HT-07-1** — Como operador con el agente activo, quiero oír solo los turnos que requieren mi criterio, para reservar la atención.
- **US-HT-07-2** — Como operador, quiero que un turno trivial omitido siga siendo reproducible a mano, para no perder información.
- **US-HT-07-3** — Como operador con cadena LLM disponible, quiero clasificación semántica real como opción, para ir más lejos de la heurística.
- **US-HT-07-4** — Como operador, quiero ver cuántos turnos se han omitido hoy, para confiar en el filtro y ajustarlo.

## Requisitos funcionales (RF-HT-07-...)
- **RF-HT-07-1**: config: `TTS_IMPORTANCE="all|smart|minimal"` (default `all` = cero cambio de comportamiento), `TTS_IMPORTANCE_MIN_CHARS` (default 60), `TTS_IMPORTANCE_LLM="off|on"`.
- **RF-HT-07-2**: heurística `smart`: relevante si el turno contiene pregunta al usuario, keywords configurables de decisión/bloqueo/riesgo (lista por defecto ampliable en config), longitud >= mínimo, o el evento fue un diff done→blocked.
- **RF-HT-07-3**: `minimal`: solo eventos blocked y turnos con pregunta directa al usuario.
- **RF-HT-07-4**: los turnos filtrados no generan síntesis local ni push; sí quedan en `history.log` con marca de omitido (columna opcional adicional, sin reescribir filas existentes) y son reproducibles con play/TLDR on-demand.
- **RF-HT-07-5**: el filtro aplica solo a la voz automática; play, TLDR, radio y briefing on-demand siempre leen.
- **RF-HT-07-6**: clasificación LLM: prompt corto por stdin (sin superficie de inyección de comandos), timeout 3 s, fallback heurístico ante fallo o cadena ausente.
- **RF-HT-07-7**: contador de turnos omitidos del día visible en `--status` y en la línea global del dashboard.

## Requisitos no funcionales (RNF-HT-07-...)
- **RNF-HT-07-1**: la heurística añade < 50 ms por turno (regex pura o una pasada del intérprete del venv ya usado).
- **RNF-HT-07-2**: el modo LLM nunca retrasa el primer audio más de 300 ms sobre el baseline: si el timeout vence, sale la decisión heurística.
- **RNF-HT-07-3**: cero dependencias nuevas en modo heurístico.

## Encaje en la arquitectura actual
Se inserta en el punto exacto donde hoy el settle window ya decidió que el evento procede: tras adquisición de texto, antes del fan-out (síntesis/push/historial). El historial ya tolera columnas opcionales sin reescritura (patrón de campos 4/5/6). La cadena LLM es la ya existente en el motor (`--llm-summary`), reutilizada para una tarea más barata: clasificación binaria en lugar de resumen.

## Prior art y diferenciación
`opencode-voice` implementa lectura condicional por longitud: prior art directo, pero basado solo en longitud. Aquí la clasificación es semántica (preguntas, decisiones, bloqueos) con opción LLM. `herdr-bleatr` delega el resumen entero al LLM en cada evento (coste y latencia por evento); aquí la heurística es offline y gratuita, y el LLM es opcional.

## Dependencias
Ninguna para la heurística. La opción LLM reutiliza la cadena ya existente del motor.

## Riesgos y mitigaciones
- Falsos negativos (turno importante filtrado) → `smart` conservador, lista de keywords auditable, revisión semanal de omitidos al inicio, default `all`.
- Opacidad del filtro → marca en historial y contador visible (RF-HT-07-4/7).
- Deriva del prompt LLM → prompt versionado en el repo, salida acotada a clasificación binaria.

## Métricas de éxito
1. 30-60% de turnos auto omitidos en modo `smart` (rango esperado de triviales).
2. Falsos negativos reclamados por el usuario < 2% de los omitidos durante el primer mes.
3. Ahorro medible de llamadas de síntesis (contador de omitidos vs eventos totales).

## Fuera de alcance
Resumir el turno filtrado (eso es TL;DR), aprendizaje personalizado del clasificador, filtrado de eventos on-demand.

## Open questions
¿Umbral por agente (verbosidad distinta entre Claude y Codex)? ¿Debe el filtro aplicar también al push ntfy en `smart` (propuesta actual: sí)? ¿Keywords por defecto en español, inglés o ambas?
