**ID**: PRD-HT-09 · **Proyecto**: herdr-tts
**Estado**: **DESCARTADA en revisión 2026-09-22** (decisión del maintainer)
**Dependencias**: Extensión mínima del motor (`--pan`)

# PRD-HT-09 — Espacialización estéreo por pane

**Prioridad**: Baja-Media · **Esfuerzo**: S-M

## Resumen ejecutivo
Paneo estéreo según la posición X del pane en el layout de Herdr (columna izquierda = -30%, derecha = +30%, interpolado), aplicado por el motor con un parámetro de playback mínimo (`--pan <float>`), respetando los targets winhost/WASAPI. Opt-in con `TTS_SPATIAL="1"`; fallback mono limpio. El audio pasa a decir de dónde viene, no solo quién habla.

## Problema y flujo actual
En flota, el audio es mono y central: la identidad vocal (HT-02) dirá quién habla, pero no desde qué zona del layout. La posición es parte del mapa mental del operador: "el de la izquierda es el refactor, el de la derecha el deploy".

## Propuesta
El watcher calcula el pan desde la geometría X del pane (posición relativa al layout, disponible vía la API de Herdr) en el momento del evento, lo mapea a un rango acotado (±`TTS_SPATIAL_WIDTH`%, default 30) y lo pasa al motor como `--pan`. El motor aplica el paneo sobre el PCM decodificado antes de la salida (post-mix, agnóstico del proveedor y del transporte): local Pulse/PipeWire, WSLg y winhost/WASAPI reciben el PCM ya paneado sin cambios de protocolo de control.

## Historias de usuario (US-HT-09-1, ...)
- **US-HT-09-1** — Como operador de flota con auriculares, quiero que el audio venga desde la posición del pane, para localizarlo sin buscarlo.
- **US-HT-09-2** — Como operador sensible al audio, quiero un paneo suave que no mande la voz a un solo oído, para mantener el confort en jornadas largas.
- **US-HT-09-3** — Como operador en WSL2 con winhost, quiero la misma espacialización que en local, sin configurar nada por transporte.

## Requisitos funcionales (RF-HT-09-...)
- **RF-HT-09-1**: config: `TTS_SPATIAL="0|1"` (default 0), `TTS_SPATIAL_WIDTH="30"` (% máximo de paneo por lado).
- **RF-HT-09-2**: resolución de posición: geometría X del pane vía API de Herdr en el momento del evento; pane sin geometría resoluble → pan 0 (centro).
- **RF-HT-09-3**: paso del parámetro: `--pan <float>` en la llamada al motor; los audios on-demand usan el pan del chat enfocado/seleccionado.
- **RF-HT-09-4**: el paneo aplica igual en local (Pulse/PipeWire), WSLg y winhost/WASAPI: el PCM viaja ya en estéreo paneado.
- **RF-HT-09-5**: la paleta, el preview de voces (HT-11) y radio (HT-03) reproducen en mono centrado (pan 0) para no falsear la posición de una superficie agregada.

## Requisitos no funcionales (RNF-HT-09-...)
- **RNF-HT-09-1**: cero coste de CPU medible: paneo de dos canales sobre el buffer ya en memoria del driver miniaudio.
- **RNF-HT-09-2**: sin regresión de latencia: < 10 ms adicionales sobre el pipeline actual.
- **RNF-HT-09-3**: si el transporte no soporta dos canales (p. ej. wsl-ps en configuraciones restrictivas), fallback mono limpio con pan 0 y aviso único en `daemon.log`.

## Encaje en la arquitectura actual
La llamada al motor ya porta parámetros por evento (`--agent`, `--session-id`, `--voice` con HT-02): `--pan` es uno más. La implementación vive en el motor (extensión mínima del driver de playback) y el host solo calcula y propaga: exactamente el patrón ya usado con `TTS_PLAYBACK`. El pan se congela con el evento (no sigue al layout si este cambia durante la cola).

## Prior art y diferenciación
La espacialización por posición es práctica estándar en audio de escritorio (los roles de stream de PipeWire/WirePlumber la rozan con el ducking), citada como base conocida. Nadie en el ecosistema de notificaciones de agentes la aplica. Diferenciación: pequeña, sensorial y barata; refuerza el mapa mental de la flota junto a HT-02.

## Dependencias
- Extensión mínima del motor: parámetro `--pan` en el driver de playback (issue menor o PRD propia del repo hermano).

## Riesgos y mitigaciones
- Confusión si el layout cambia entre evento y reproducción (audio en cola) → pan congelado con el evento, documentado.
- Auriculares con balance raro o mono real → width configurable y opt-in.
- Transportes exóticos → fallback mono (RNF-HT-09-3).

## Métricas de éxito
1. Identificación correcta del origen (quién y desde dónde) sin mirar, >= 70% en autoinforme con HT-02 activo.
2. Cero regresión de latencia percibida.
3. Activación estable: la feature sigue encendida tras el primer mes (no se apaga por molestia).

## Fuera de alcance
HRTF/audio 3D, movimiento dinámico del pane durante la reproducción, espacialización en el push móvil (el MP3 viaja plano).

## Open questions
¿Posición X absoluta o relativa al layout visible (porcentaje)? ¿Cache de geometría por sweep en lugar de consulta por evento? ¿Interacción con el pan de la cadena de HT-10 (turnos antiguos, mismo pan del chat)?
