**ID**: PRD-AT-06 · **Proyecto**: agent-tts
**Prioridad final (revisión 2026-09-22)**: P3 · **Estado**: Aprobada
**Dependencias**: AT-04 para winhost

> **Requisito duro del maintainer (22/09/2026)**: Cero configuración del sistema: toda la detección y política debe vivir dentro de agent-tts (consultas read-only vía pactl y reporte del receptor --winhost propio). Si alguna vía exigiera configurar WirePlumber/pulse.rules, no es válida.

# PRD-AT-06 — Ducking y prioridad de audio

**Prioridad**: Media · **Esfuerzo**: M

### Resumen ejecutivo

Cuando Bruno está en una llamada o escuchando música, la voz del agente pisa todo. Esta PRD añade detección de fuentes de audio activas y una política configurable por playback target: `duck` (bajar el volumen propio mientras dure la fuente), `pause` (auto-pausa con reanudación), `ignore`. La detección es un sweep barato antes de cada reproducción y periódico ligero durante ella, nunca un polling agresivo.

### Problema y flujo actual

El motor reproduce a volumen pleno sin conciencia de lo que ya suena en el sistema. En la topología real del maintainer (WSL2 + winhost), la voz entra por el host Windows mientras la llamada o la música viven del lado del host: la inspección de PulseAudio/PipeWire dentro de WSL solo ve el servidor de WSLg y no refleja las fuentes del host. Por tanto, la detección útil depende del playback target: local mira el servidor de audio local; winhost/windows necesita reporte del receptor en el host (WASAPI).

### Propuesta

Capa de detección con dos backends: Linux local (enumeración de sink-inputs de PulseAudio/PipeWire via `pactl`/bus, buscando role `communication` y streams de captura activos) y host Windows (el proceso `--winhost` enumera sesiones WASAPI y reporta actividad de comunicación/captura al cliente como campo de estado). Política declarativa por target en config: `duck` reduce el volumen del stream propio del motor en 12 dB (default) mientras la fuente esté activa y lo restaura al terminar; `pause` pausa la reproducción (reanudación con el auto-rewind inteligente ya existente) y retoma cuando la fuente libera; `ignore` es el comportamiento actual. El sweep corre antes de empezar cada reproducción y como chequeo ligero (p. ej. cada 2 s) solo mientras el motor está sonando; coste por chequeo acotado en milisegundos.

### Historias de usuario (US-AT-06-1, ...)

- US-AT-06-1: Como usuario en llamada, quiero que la voz del agente se atenúe o espere en vez de pisar mi llamada.
- US-AT-06-2: Como usuario que escucha música en el host Windows, quiero que el ducking funcione también con playback winhost, no solo local.
- US-AT-06-3: Como usuario, quiero política distinta por contexto (pausa en llamadas, duck en música) sin tocar reglas de sistema.

### Requisitos funcionales (RF-AT-06-...)

- RF-AT-06-1: Detección local: enumera sink-inputs con role `communication` y fuentes de captura activas del servidor de audio por defecto; resultado cacheado con TTL del sweep.
- RF-AT-06-2: Detección winhost: el servidor en el host reporta `communication_active` y `capture_active` via el canal de control existente; el cliente aplica la política antes de enviar PCM y durante la reproducción.
- RF-AT-06-3: Políticas `duck|pause|ignore` configurables globalmente y por playback target (`AGENT_TTS_DUCK_POLICY`, override por flag); `duck` aplica atenuación configurable (default 12 dB) al stream del motor, no al de otras apps.
- RF-AT-06-4: `pause` integra con la sesión existente: pausa via el mismo estado interno de `AudioSession` y reanuda con auto-rewind configurado.
- RF-AT-06-5: Los eventos de detección se exponen en el `status` IPC (`comm_active=true/false`) para dashboards y para herdr.
- RF-AT-06-6: Fallo de detección (pactl ausente, bus caído) degrada a `ignore` con una línea de aviso, nunca bloquea la reproducción.

### Requisitos no funcionales (RNF-AT-06-...)

- RNF-AT-06-1: Coste del sweep menor de 20 ms por chequeo y frecuencia máxima de 0.5 Hz solo mientras suena; cero actividad en reposo.
- RNF-AT-06-2: Detección de una llamada activa antes de iniciar reproducción en menos de 2 s desde que la llamada existe en el peor caso (un sweep).
- RNF-AT-06-3: Falsos positivos menores de 1%: reproducir audio normal del motor no dispara pausa/duck espurios.
- RNF-AT-06-4: La restauración de volumen es inmediata y exacta (vuelve al nivel previo, verificado por test).

### Encaje en la arquitectura actual

Capa nueva `ducking.py` consultada desde el orquestador de reproducción justo antes de crear la `AudioSession` y desde un temporizador ligero mientras vive la sesión. La mutación de volumen se aplica al stream del motor (gain de la sesión local; encabezado de volumen en el protocolo winhost si hace falta un campo v2). Para winhost, el servidor `--winhost` (que ya mantiene conexiones de control cortas para pause/resume/stop) gana un comando de consulta de actividad. La resolución de target (`playback_target.py`) decide qué backend de detección aplica. La política por target respeta la matriz local/winhost/wsl-ps/windows/auto ya documentada.

### Prior art y diferenciación

WirePlumber ya hace ducking role-based nativo en Linux (y pulse.rules tiene sus quirks documentados): esa vía exige configurar el sistema, no el motor, y no existe equivalente simple en la topología WSL2-a-host-Windows. El valor aquí es una política explícita, portable y poseída por el motor, por playback target, incluyendo winhost, sin tocar reglas del sistema. Frente al prior art de conectores/notificaciones (agentvoice y demás), ninguno trata ducking.

### Dependencias

AT-04 (daemon) para winhost: el reporte continuo de actividad del host es natural en el daemon; sin daemon, la detección winhost se limita al chequeo inicial por reproducción.

### Riesgos y mitigaciones

- Riesgo: diferencias PulseAudio vs PipeWire rompen la enumeración. Mitigación: parseo de `pactl` (estable en ambos) como camino base y fallo a `ignore`.
- Riesgo: mezclar música y llamada en el host confunde la política. Mitigación: distinguir por role de sesión (communication frente a media) y permitir política diferenciada por clase.
- Riesgo: bucle propio (el motor detecta su propio stream). Mitigación: excluir por definición el sink-input del motor (match de application name/process).

### Métricas de éxito

- Detección correcta de llamada activa en mayor o igual a 99% de los chequeos con llamada real.
- Cero solapamientos de voz-sobre-llamada con política `duck` (atenuación aplicada antes del primer frame audible).
- Coste CPU total de la capa por debajo de 0.1% sostenido durante reproducción.

### Fuera de alcance

Ducking de aplicaciones de terceros (eso es rol de WirePlumber), cambio de perfil de headset Bluetooth, mezcla multizona, detección de música versus voz del usuario hablando solo.

### Open questions

¿El protocolo winhost necesita campo de gain en el header v1 o se empaqueta en v2 junto a la cola de AT-08? ¿Exponer la política también por evento (herdr decide que un blocked nunca se duck-ea)?
