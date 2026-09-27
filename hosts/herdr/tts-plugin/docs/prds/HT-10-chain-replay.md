**ID**: PRD-HT-10 · **Proyecto**: herdr-tts
**Prioridad final (revisión 2026-09-22)**: P5 · **Estado**: Aprobada
**Dependencias**: PRD-AT-08 (`--play-chain`)

# PRD-HT-10 — Chain replay contextual

> **Nota de revisión (2026-09-22)**: Aprobada con prioridad baja (P5); última en el orden de ataque.

**Prioridad**: Media · **Esfuerzo**: S-M

## Resumen ejecutivo
Acción "reproducir contexto" en paleta y dashboard que encadena los últimos N turnos del chat (default 3) desde el almacén de audio, en orden cronológico, usando `--play-chain` del motor (PRD-AT-08). Si la retención purgó algún audio, re-síntesis desde el transcript del conector. Para responder bien a un bloqueo, a veces hay que oír qué dijo el agente antes del último turno.

## Problema y flujo actual
`ctrl-r` en la paleta repone un único turno. Cuando el agente se bloquea con "¿continúo con el plan B?", la decisión depende de lo acordado dos turnos atrás: hoy hay que leer el transcript o reproducir turnos de uno en uno, rompiendo el flujo auditivo.

## Propuesta
Acción en dos superficies: en la paleta, tecla sobre un turno que reproduce los N turnos previos de ese chat terminando en el seleccionado; en el dashboard, tecla sobre el chat con los últimos N. Las rutas salen del campo de audio almacenado de `history.log`; los huecos por retención se re-sintetizan desde el conector con la misma vía `--agent`/`--session-id`; todo pasa por el mutex (un solo reproductor) con pausa/seek/stop operativos sobre la cadena completa.

## Historias de usuario (US-HT-10-1, ...)
- **US-HT-10-1** — Como operador ante un blocked, quiero escuchar los últimos turnos seguidos, para decidir con el contexto completo.
- **US-HT-10-2** — Como operador con retención activa, quiero que los turnos purgados se re-sinteticen al vuelo, para que la cadena nunca quede rota en silencio.
- **US-HT-10-3** — Como operador, quiero pausar y rebobinar dentro de la cadena como en un audio individual, para repasar el punto exacto.
- **US-HT-10-4** — Como operador, quiero ver por qué turno de la cadena va la reproducción, para saber dónde estoy.

## Requisitos funcionales (RF-HT-10-...)
- **RF-HT-10-1**: acción en paleta (tecla `ctrl-x` sobre un turno): reproduce los últimos N turnos de ese chat terminando en el seleccionado; `esc` interrumpe.
- **RF-HT-10-2**: acción en dashboard (tecla `x` sobre un chat): reproduce los últimos N turnos del chat seleccionado.
- **RF-HT-10-3**: N configurable: `TTS_CHAIN_TURNS` (default 3, máximo 10).
- **RF-HT-10-4**: resolución de audio: campo de ruta almacenada de `history.log` cuando el fichero existe; turnos sin audio almacenado → re-síntesis desde el transcript del conector, con marca visual "re-sintetizado"; turnos ni almacenados ni accesibles → omisión con aviso hablado breve.
- **RF-HT-10-5**: reproducción vía `--play-chain` del motor (PRD-AT-08); sin ella, secuencia de `--play-file` gestionada por el host bajo el mutex.
- **RF-HT-10-6**: pausa, seek y stop del motor funcionan sobre la cadena igual que sobre un audio individual.
- **RF-HT-10-7**: indicación visual durante la cadena: la línea de motor del dashboard muestra "contexto k/N".

## Requisitos no funcionales (RNF-HT-10-...)
- **RNF-HT-10-1**: inicio de la cadena < 1.5 s si todo el audio existe (sin re-síntesis).
- **RNF-HT-10-2**: la re-síntesis de huecos no bloquea la reproducción de los ya disponibles (encola en orden).
- **RNF-HT-10-3**: solo lectura del almacén existente: la feature no alterra la política de retención.

## Encaje en la arquitectura actual
`history.log` ya persiste la ruta del almacén por turno (sexto campo TSV) y la paleta ya repone con `ctrl-r` vía `--play-file` + `stop_audio`: la cadena es la generalización natural del replay existente. El motor ya tiene el almacén con retención y los conectores para re-sintetizar desde transcript.

## Prior art y diferenciación
Ningún comparado ofrece replay múltiple; el podcast RSS de la suite se acerca (episodios por sesión) pero no es interactivo ni por chat en caliente. Diferenciación: re-escucha conversacional in-situ con relleno automático de huecos por retención.

## Dependencias
- PRD-AT-08 (`--play-chain`); sin ella, fallback secuencial del host (válido pero con pausas entre turnos).

## Riesgos y mitigaciones
- Cadenas largas → cap de N (RF-HT-10-3) y stop siempre disponible.
- Re-síntesis cara en proveedores de pago → solo bajo demanda explícita del usuario.
- Confusión de orden → marcador hablado suave entre turnos (opt-in, open question).

## Métricas de éxito
1. Uso >= 1 vez por día en semanas de flota intensa.
2. Autoinforme de respuesta correcta al blocked tras escuchar el contexto (mejora percibida).
3. Cero solapamientos de audio (mutex intacto en todas las cadenas reproducidas).

## Fuera de alcance
Exportar la cadena como fichero o episodio (eso es `--render-pane`/podcast), cadenas entre chats distintos, mezcla de proveedores dentro de la cadena.

## Open questions
¿Marcador sonoro entre turnos (opt-in) o silencio separador? ¿N persistente por chat o global? ¿La re-síntesis respeta la voz por agente de HT-02?
