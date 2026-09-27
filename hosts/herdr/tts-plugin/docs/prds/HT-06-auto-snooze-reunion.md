**ID**: PRD-HT-06 · **Proyecto**: herdr-tts
**Prioridad final (revisión 2026-09-22)**: P4 · **Estado**: Postergada
**Dependencias**: Extensión menor de winhost (solo WSL2)

# PRD-HT-06 — Auto-snooze contextual (modo reunión)

**Prioridad**: Media · **Esfuerzo**: M

## Resumen ejecutivo
Detectar micrófono en captura activa (PipeWire/PulseAudio en Linux; en WSL2, estado del dispositivo reportado por winhost) para activar automáticamente un global-snooze de voz local etiquetado como "reunión" más un modo "solo móvil" (los eventos siguen llegando a ntfy sin voz local). Al liberar el micrófono, se restaura exactamente el estado previo. `TTS_MEETING_MODE="auto|off"` e histéresis anti-flicker de 5 s.

## Problema y flujo actual
En una llamada, la voz local molesta (se mezcla con la reunión) y el push del móvil vibra igual que siempre. Hoy el usuario debe acordarse de `prefix+Z` (snooze global) y de deshacerlo después; olvidar el segundo paso deja la flota muda durante horas, un fallo clásico de los snoozes manuales.

## Propuesta
Monitor de captura activa consultado en el sweep con cadencia relajada. Detección sostenida durante la histéresis activa un snooze global especial, separado del manual y etiquetado "reunión", más el modo solo-móvil: sin voz local, push intacto. Al cesar la captura, restauración exacta del estado previo. La voz on-demand del usuario nunca se bloquea: solo la automática.

## Historias de usuario (US-HT-06-1, ...)
- **US-HT-06-1** — Como operador con llamadas frecuentes, quiero que la voz local se silencie sola durante la reunión y vuelva sola después, sin tocar nada.
- **US-HT-06-2** — Como operador en reunión, quiero que los eventos importantes sigan llegando al móvil, para no perder la flota de vista.
- **US-HT-06-3** — Como operador que dicta con HT-01, quiero que mi propio uso del micrófono no dispare el modo reunión.
- **US-HT-06-4** — Como operador, quiero distinguir en el dashboard un snooze automático de uno manual, para no confundir estados.

## Requisitos funcionales (RF-HT-06-...)
- **RF-HT-06-1**: config: `TTS_MEETING_MODE="auto|off"` (default off), `TTS_MEETING_HYSTERESIS_S` (default 5), `TTS_MEETING_MOBILE_ONLY="1"` (default: push ntfy sigue activo).
- **RF-HT-06-2**: detección en Linux nativo: consulta de source-outputs de captura activa (pactl/PipeWire) con allowlist/denylist de aplicaciones; la captura del STT propio (HT-01) queda exenta mientras dicta.
- **RF-HT-06-3**: detección en WSL2: winhost reporta el estado del micrófono del host Windows (extensión mínima del protocolo); ausencia de señal = no-reunión (fail-open).
- **RF-HT-06-4**: estado "reunión" separado del snooze global manual: el dashboard muestra la etiqueta de auto con indicador propio; el snooze manual del usuario nunca se pisa ni se auto-deshace.
- **RF-HT-06-5**: restauración exacta al cesar la captura (tras histéresis): la voz local vuelve al estado on/off que tenía antes.
- **RF-HT-06-6**: antiflicker: transiciones más rápidas que la histéresis se ignoran.
- **RF-HT-06-7**: override manual: las lecturas on-demand (play, TLDR, radio) funcionan durante la reunión; solo la voz automática se suprime.

## Requisitos no funcionales (RNF-HT-06-...)
- **RNF-HT-06-1**: chequeo de micrófono integrado al sweep con cadencia cada N ticks (no cada tick), sin spawns adicionales por tick.
- **RNF-HT-06-2**: detección fallida = el modo auto se desactiva solo con una línea en `daemon.log` (fail-open); la voz vuelve.
- **RNF-HT-06-3**: privacidad: nunca se graba audio; solo se consulta el estado del servidor de sonido.

## Encaje en la arquitectura actual
El daemon ya aplica y quita global-snooze (`cycle_snooze` con scope global) y ya tiene bucle de sweep: el modo reunión es un segundo origen del mismo efecto con etiqueta propia en el ledger. winhost ya mantiene un canal TCP vivo con el host Windows para el transporte de audio: añadir un mensaje de estado de captura es una extensión mínima del protocolo existente.

## Prior art y diferenciación
El ducking por roles de stream de PipeWire/WirePlumber y la detección estándar de micrófono en captura son la base técnica conocida, citada como tal. Ningún proyecto del ecosistema aplica esto al auto-mute de notificaciones de agentes. Diferenciación: el puente WSL→Windows vía winhost, exclusivo de la suite, y la separación voz local/móvil durante la reunión.

## Dependencias
Ninguna dura. La detección en WSL2 requiere la extensión menor de winhost (motor); sin ella, el modo auto solo opera en Linux nativo/WSLg.

## Riesgos y mitigaciones
- Falsos positivos (apps que mantienen el micrófono abierto) → denylist configurable + opt-in del modo.
- Coste de polling de pactl → cadencia N ticks y caché del resultado.
- Oscilación rápida reunión/silencio → histéresis de 5 s (RF-HT-06-6).

## Métricas de éxito
1. Cero voz local durante reuniones reales en dos semanas de uso (autoinforme).
2. Cero voz perdida por falso negativo grave (reunión no detectada) tras ajustar la denylist.
3. Menos de 10 activaciones/desactivaciones por día (sin flicker).

## Fuera de alcance
Ducking del volumen de la reunión, transcripción de la reunión, detección por calendario, supresión del push móvil.

## Open questions
¿Criterio de exención del STT propio: pid del proceso, puerto, o ventana temporal de dictado? ¿Winhost reporta el estado o WSL lo consulta con un powershell ligero?
