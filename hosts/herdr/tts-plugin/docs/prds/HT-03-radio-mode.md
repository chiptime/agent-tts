**ID**: PRD-HT-03 · **Proyecto**: herdr-tts
**Prioridad final (revisión 2026-09-22)**: P2 · **Estado**: Aprobada
**Dependencias**: PRD-AT-08 (cola prioritaria; fallback secuencial)

# PRD-HT-03 — Radio mode (triaje por voz)

> **Nota de revisión (2026-09-22)**: El resumen por chat usa por defecto el TL;DR heurístico offline (como está diseñado). Decisión del maintainer: esta feature no requiere LLM para existir; el resumen generado con LLM queda como modo experimental opcional (RF-HT-03-9).

**Prioridad**: Alta · **Esfuerzo**: M

## Resumen ejecutivo
Una tecla (nuevo command id `radio` en el keymap) reproduce un boletín hablado y priorizado de todos los chats que piden atención: blocked primero, done más recientes después, cada uno con título de chat y 1-2 frases de resumen heurístico, terminando con "N chats más silenciosos omitidos". Convierte el retorno al PC en un triaje de oídos, sin abrir nada.

## Problema y flujo actual
Volver al PC tras 20 minutos con cinco chats en done/blocked obliga hoy a leer el dashboard o escuchar eventos uno a uno. El roster del dashboard ya ordena needs-attention-first, pero exige mirar la pantalla; el canal auditivo solo entrega eventos sueltos, sin visión de conjunto ni prioridad.

## Propuesta
Command id `radio` sin acorde por defecto (sugerido y validado con `keymap check`). Al pulsarlo: computa el roster con la misma fusión del dashboard (`herdr agent list` + ledgers por `pane_id`), ordena blocked primero y done por recencia, sintetiza por chat "título + TL;DR de 1-2 frases" (heurística offline existente del motor) y lo encola en la cola prioritaria del motor (PRD-AT-08) o en secuencial como fallback. Cierra con el recuento de chats omitidos. Interrumpible con stop; re-pulsar re-evalúa con datos frescos.

## Historias de usuario (US-HT-03-1, ...)
- **US-HT-03-1** — Como operador que vuelve al PC, quiero un boletín hablado de todo lo que pide atención, para decidir por dónde entrar sin leer nada.
- **US-HT-03-2** — Como operador con agentes bloqueados, quiero que los blocked suenen primero, para desatascar antes lo que detiene el trabajo.
- **US-HT-03-3** — Como operador interrumpido a mitad del boletín, quiero cortarlo con stop o reiniciarlo, para que nunca sea una carga.
- **US-HT-03-4** — Como operador de flota grande, quiero saber cuántos chats quedaron fuera del boletín, para confiar en que no se oculta nada importante.

## Requisitos funcionales (RF-HT-03-...)
- **RF-HT-03-1**: command id `radio` en `keymap.json`, tratado por `keymap check/emit/apply` como cualquier otro id, sin acorde por defecto.
- **RF-HT-03-2**: selección: chats en `blocked` primero, luego `done` ordenados por evento/audio más reciente; excluye muted y snoozed; límite configurable de chats por boletín (`TTS_RADIO_MAX_CHATS`, default 6).
- **RF-HT-03-3**: por chat, frase de cabecera con el título truncado a 40 caracteres (regla del dashboard) + 1-2 frases de resumen heurístico (misma vía que `--tldr`).
- **RF-HT-03-4**: cierre con frase final: número de chats omitidos (working/idle) con la fórmula "N chats más silenciosos omitidos".
- **RF-HT-03-5**: reproducción vía la cola prioritaria del motor (PRD-AT-08) con los blocked en prioridad alta; sin ella, encadenado secuencial bajo el mutex existente.
- **RF-HT-03-6**: stop/s corta el boletín; nueva pulsación durante la reproducción descarta el boletín en curso y re-triaja con datos frescos.
- **RF-HT-03-7**: debounce propio del command: no re-dispara en menos de 10 s.
- **RF-HT-03-8**: si HT-02 está activo, la cabecera incluye el nombre del agente (identidad hablada).
- **RF-HT-03-9**: Modo experimental `TTS_RADIO_LLM_SUMMARY=on`: el resumen por chat se genera con la cadena `--llm-summary` ya existente en el motor (`claude -p` / `codex exec` / `ollama`), limitado a 1-2 proveedores como mucho, con timeout corto y fallback al heurístico. Nace experimental; no introduce infraestructura LLM nueva.

## Requisitos no funcionales (RNF-HT-03-...)
- **RNF-HT-03-1**: computar el boletín tarda < 2 s con 10 chats (reuso del roster cacheado en la cadencia del dashboard).
- **RNF-HT-03-2**: primer audio del boletín en < 1.5 s (streaming del motor).
- **RNF-HT-03-3**: el estado "radio en curso" es visible en dashboard y línea de motor.

## Encaje en la arquitectura actual
Reutiliza sin cambios de fuente la fusión por chat del dashboard v3 (misma clave `pane_id`, mismo orden needs-attention-first ya definido). El TL;DR por chat reusa la heurística offline del motor. La cola prioritaria es PRD-AT-08; sin ella, el mutex secuencial actual ya garantiza no-solapamiento.

## Prior art y diferenciación
Ninguno de los comparados ofrece triaje hablado multi-chat: `agentvoice` y `claude-code-tts` encolan eventos sueltos con worker pool anti-solapamiento, sin noción de boletín ni prioridad por atención. Diferenciación: boletín priorizado por estado de atención, exclusivo del contexto de flota de Herdr.

## Dependencias
- PRD-AT-08 (cola con prioridades del motor) — sin ella, fallback a reproducción secuencial (comportamiento actual del mutex).

## Riesgos y mitigaciones
- Boletines demasiado largos → límite de chats y de frases por ítem (RF-HT-03-2/3).
- TL;DR heurístico poco informativo en algunos agentes → open question sobre clasificación LLM opcional reutilizando la cadena del motor.
- Saturación por pulsación repetida → debounce del command (RF-HT-03-7).

## Métricas de éxito
1. Tiempo desde pulsar `radio` hasta decidir a qué chat entrar < 60 s con flota de 5+ chats.
2. Uso >= 1 vez por día en semanas de flota activa.
3. Tasa de abandono del boletín antes del final < 30%.

## Fuera de alcance
Boletín en texto/pantalla, envío del boletín a ntfy (eso corresponde a HT-08), orden personalizado por el usuario.

## Open questions
¿Incluir chats en `working` con evento muy reciente? ¿Cabecera con agent-type siempre que HT-02 exista, o solo si hay ambigüedad? ¿Longitud del ítem ajustable?
