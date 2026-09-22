**ID**: PRD-HT-08 · **Proyecto**: herdr-tts
**Prioridad final (revisión 2026-09-22)**: P4 · **Estado**: Postergada
**Dependencias**: PRD-AT-07 (`--digest`)

# PRD-HT-08 — Briefing matinal automático

**Prioridad**: Media · **Esfuerzo**: M

## Resumen ejecutivo
Al primer evento de actividad del día (o con `herdr-tts --briefing`), compilar un digest hablado de la actividad desde el último briefing: chats tocados, turnos completados, bloqueos vivos. Audio-resumen generado con `agent-tts --digest` (PRD-AT-07), entregado por voz local o como episodio ntfy/podcast. La "portada" hablada del periodo en que el usuario estuvo ausente.

## Problema y flujo actual
Agentes trabajando de noche o por la tarde: al volver, no se sabe qué pasó sin leer cinco chats. El dashboard muestra el presente (quién está done/blocked ahora) pero no la narrativa del periodo ausente; radio mode (HT-03) triaja el momento, no la historia.

## Propuesta
Timestamp `last_briefing_ts` persistido en el directorio de estado. Trigger: primer evento del pipeline del día (cualquier evento que pase el settle window) o invocación manual. Compilación desde `history.log` agrupado por chat + `herdr agent list`: chats con actividad, turnos completados, bloqueos aún vivos. El guion se sintetiza con `--digest` del motor; entrega configurable por voz local, push ntfy con MP3 o episodio del podcast existente.

## Historias de usuario (US-HT-08-1, ...)
- **US-HT-08-1** — Como operador que deja agentes trabajando de noche, quiero un resumen hablado al volver, para saber qué pasó sin leer nada.
- **US-HT-08-2** — Como operador que llega con el móvil, quiero el briefing también como push/podcast, para escucharlo de camino.
- **US-HT-08-3** — Como operador, quiero que el briefing sea corto y accionable (por dónde empezar), no una lectura completa de turnos.
- **US-HT-08-4** — Como operador, quiero poder pedirlo en cualquier momento, no solo por la mañana.

## Requisitos funcionales (RF-HT-08-...)
- **RF-HT-08-1**: comando manual `herdr-tts --briefing`; modo automático `TTS_BRIEFING="off|auto"` (default off) que dispara en el primer evento del pipeline del día.
- **RF-HT-08-2**: ventana del digest: desde `last_briefing_ts` hasta ahora; primera ejecución = últimas 12 h.
- **RF-HT-08-3**: contenido del guion: número de chats con actividad, top de chats por turnos completados, bloqueos vivos al momento del briefing, y sugerencia de por dónde empezar (primer blocked más antiguo).
- **RF-HT-08-4**: síntesis con `agent-tts --digest` (PRD-AT-07): el motor recibe sesiones/pane_ids y la ventana, y devuelve el audio-resumen; fallback documentado: concatenación local de TL;DRs por chat con aviso en el log.
- **RF-HT-08-5**: entrega: `TTS_BRIEFING_DELIVERY="voice|push|podcast"` (default `voice`); push usa el MP3 inline existente; podcast añade episodio al feed `:8844` existente.
- **RF-HT-08-6**: el briefing se encola tras el mutex como cualquier audio y es interrumpible con stop.
- **RF-HT-08-7**: dashboard y `--status` muestran "último briefing: hace Xh".

## Requisitos no funcionales (RNF-HT-08-...)
- **RNF-HT-08-1**: compilación del guion < 2 s: reuso de la pasada única de agrupación por chat que ya hace el dashboard (mismo intérprete del venv).
- **RNF-HT-08-2**: duración del audio <= 90 s.
- **RNF-HT-08-3**: el modo auto nunca dispara sin un evento real del pipeline (nada de briefings en máquinas idle).
- **RNF-HT-08-4**: el texto del briefing pasa por el redactor de secretos del motor antes de publicar a ntfy/podcast (ya existe).

## Encaje en la arquitectura actual
`history.log` por chat con duraciones y timestamps ya alimenta el dashboard: la compilación del guion es una variante de esa pasada. El push con MP3 inline y el podcast RSS ya existen como fan-out. Las piezas nuevas son solo el trigger, el guion y la llamada `--digest`.

## Prior art y diferenciación
No existe briefing hablado de flota en los comparados; los digests tipo "standup" de otras herramientas son texto plano. Diferenciación: portada hablada del periodo ausente entregada por los canales ya existentes (voz, ntfy, podcast), con narrativa priorizada hacia la acción.

## Dependencias
- PRD-AT-07 (`agent-tts --digest`) para el audio-resumen natural; sin ella, fallback a concatenación de TL;DRs.

## Riesgos y mitigaciones
- Historial vacío (retención 0) → requisito previo documentado: retención >= 1 día o conector accesible para TL;DR on-the-fly.
- Briefing largo → cap de 90 s (RNF-HT-08-2) y jerarquía fija del guion.
- Activación temprana con evento trivial → open question sobre el criterio de "primer evento" y sinergia con HT-07.

## Métricas de éxito
1. >= 3 briefings por semana de uso real.
2. Tiempo desde la llegada hasta decidir por dónde entrar < 2 min con briefing.
3. Duración media del briefing entre 45 y 90 s.

## Fuera de alcance
Briefing programado por hora fija, envío por email, resumen de diffs de código, multi-idioma del guion.

## Open questions
¿Primer evento del día = primer evento del pipeline o primer input humano? ¿Incluir consumo de tokens/coste por chat? ¿Debería HT-07 (filtro) alimentar el guion con solo lo relevante?
