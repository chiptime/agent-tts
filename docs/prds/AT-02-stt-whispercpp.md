**ID**: PRD-AT-02 · **Proyecto**: agent-tts
**Prioridad final (revisión 2026-09-22)**: P4 · **Estado**: REENFOCADA (2026-10-07) — cliente STT del plugin sobre el STT del brain; whisper.cpp descartado
**Dependencias**: endpoint `POST /transcribe` del brain (`hosts/herdr/brain/src/herdr_brain/server.py`)

> **Nota de destino (2026-10-07 — decisiones F4 del maintainer):** AT-02 deja de ser una capa STT local en el motor y pasa a ser el **cliente STT del plugin** (`herdr-tts`) que consume el STT ya existente en el brain (`faster-whisper`, `POST /transcribe`). Sin whisper.cpp, sin jerarquía `stt/` en el motor. Debe poder activarse y desactivarse.
>
> **Historia de esta nota:** el 2026-10-06 existió una nota de "cobertura dual" (Decisión D-R2) que mantenía la capa STT local en el roadmap del motor para dar servicio a HT-01; esa directiva quedó **superada** por la decisión del 2026-10-07 registrada aquí. El diseño original con whisper.cpp (2026-09-22) se conserva resumido en el apéndice histórico.

# PRD-AT-02 — Cliente STT del plugin sobre el STT del brain

**Prioridad**: Alta (F4 del ROADMAP) · **Esfuerzo**: M

## Resumen ejecutivo (la decisión)

El plugin (`herdr-tts`) dicta audio, lo envía al endpoint `POST /transcribe` del brain y recibe texto. El reconocimiento vive íntegramente en el brain (`faster-whisper`); el plugin no añade ningún motor de STT, no gestiona modelos y jamás arranca ni supervisa el brain. Si el brain no está disponible, el dictado falla de forma visible y no se inyecta nada. Todo lo de esta PRD es diseño pendiente de implementar: **nada está implementado ni verificado más allá de lo citado con fichero y línea.**

## Flujo decidido

1. El usuario dispara la captura (ver HT-01: command id `ptt`, modo `toggle`).
2. El plugin captura audio del micrófono (en WSL2, vía PowerShell en el host Windows, por analogía con el destino de reproducción `wsl-ps`).
3. El plugin hace `POST /transcribe` al brain con el audio como campo multipart `audio`.
4. El brain responde `{"text": "..."}`; el plugin muestra la confirmación y HT-01 decide la inyección.

## Contrato real del endpoint (verificado en código)

| Aspecto | Comportamiento | Evidencia |
|---|---|---|
| Ruta y campo | `POST /transcribe`, multipart campo `audio`; devuelve `{"text": ...}` | `hosts/herdr/brain/src/herdr_brain/server.py:598-624` |
| Modelo no disponible | `503` con pista que nombra `python -m herdr_brain.stt pull` | `server.py:603-607`, `stt.py:38-41` |
| Modelo cargando | `503` "se está cargando — prueba de nuevo" | `server.py:608-613` |
| Audio vacío | `400` "Audio vacío" | `server.py:614-616` |
| Fallo de decodificación | `503` con el detalle del error | `server.py:620-623` |
| Formatos de entrada | Cualquier contenedor/codec que `faster-whisper` decodifique vía PyAV (hoy webm/opus del navegador) | `stt.py:154-172` |
| Descarga del modelo | Nunca automática; acción explícita del operador (`python -m herdr_brain.stt pull`) | `stt.py:1-19`, `stt.py:37` |
| Salud | `GET /health` expone `"stt": "loading|ready|unavailable"` | `server.py:594`, `stt.py:33-35` |
| Host y puerto | Host: `HERDR_BRAIN_HOST` o `127.0.0.1`. Puerto: `HERDR_BRAIN_PORT` → `<config>/herdr-brain/config.env` → `8741` | `server.py:1229`, `config.py:24` y `config.py:205-209` |

## Configuración propuesta

Ninguno de estos nombres existe hoy en el código; todos son **propuestas** hasta que se implementen.

| Nombre (propuesta) | Valores | Default | Función |
|---|---|---|---|
| `TTS_STT` | `off\|on` | `off` | Interruptor del cliente STT. Patrón del plugin: `TTS_*="off\|on"` con degradación estricta al valor inválido (como `TTS_READER_AUTO`, `bin/herdr-tts:263`). |
| `TTS_STT_URL` | URL | `http://127.0.0.1:8741` | Base URL del brain. El default debería resolverse con la misma precedencia que el brain aplica a su puerto (env `HERDR_BRAIN_PORT` → `config.env` → 8741); el plugin ya tiene el resolvedor `herdr_resolve_port` (`bin/herdr-tts:82-106`), hoy solo ejercitado por tests. |
| `TTS_STT_TIMEOUT` | segundos | por definir | Límite de la llamada HTTP a `/transcribe`. |

**Descubrimiento de la URL del brain — estado actual:** el plugin **no** hace hoy ninguna llamada HTTP al brain (sus únicos `curl` son ntfy y el servidor de podcast). El resolvedor `herdr_resolve_port` existe y replica la precedencia de puerto del brain, pero no está cableado a ningún cliente HTTP. Cualquier integración nueva es propuesta, no integración existente.

## Comportamiento ante fallos (brain caído)

- Error de conexión o `503`: aviso **visible** en el overlay de HT-01, nada se inyecta, el flujo del daemon no se rompe (fail-open, como el resto del plugin).
- El plugin **nunca** arranca, reinicia ni gestiona el brain; tampoco descarga modelos (el pull es acción del operador sobre el brain).
- `400` (audio vacío): tratado como guard de HT-01 (dictado vacío), no como error de red.

## Encaje en la arquitectura actual

El cliente vive en el plugin (`bin/herdr-tts`), junto al keymap y el overlay de HT-01. La captura de micrófono sigue el patrón del destino de reproducción `wsl-ps`: un `powershell.exe` persistente alcanzable por interop WSL recibe/envía datos por stdin (hoy WAV prefijado por longitud para reproducción, `engine/src/agent_tts/powershell_playback.py:88`). El análogo de captura (PowerShell graba micrófono y entrega audio al plugin) es **propuesta sin implementar**; el formato y la latencia quedan como pregunta abierta. El plugin ya modela los destinos de reproducción en `SETTINGS_TARGETS=(local winhost wsl-ps windows auto)` (`bin/herdr-tts:5265`) y los pasa al motor con `--playback` (`bin/herdr-tts:1787-1788`).

## Fuera de alcance

- whisper.cpp y cualquier capa STT en el motor (diseño superado; ver apéndice).
- Gestión de modelos desde el plugin (el pull es del brain).
- Streaming con parciales, diarización, wake-word, STT en cloud.
- Modo `hold` de captura (ver HT-01).

## Preguntas abiertas

1. **Comportamiento exacto de `send-text` con saltos de línea:** ¿qué hace `herdr pane send-text` si el texto dictado contiene un `\n`? ¿Se inyecta literal o se parte? Verificar con Herdr core antes de implementar la inyección.
2. **Valor del timeout de silencio** que corta la captura en modo `toggle` (nombre y default de la variable, p. ej. `TTS_PTT_SILENCE_SECONDS`).
3. **Suposición press-only del keymap:** el diseño asume que el keymap de Herdr solo dispara en pulsación (no en liberación), lo que descarta el modo `hold`. No verificado en el código de Herdr core; verificar durante la implementación.
4. **Formato y latencia de la captura de micrófono por PowerShell:** qué contenedor/codec produce el script de PowerShell, tamaño del fragmento y sobrecoste frente a `parecord` (única vía de captura nativa en WSL2).
5. **Descubrimiento de la URL del brain:** ¿variable nueva (`TTS_STT_URL`), reutilización de la precedencia `HERDR_BRAIN_PORT` → `config.env` → 8741 vía `herdr_resolve_port`, o ambas?
6. **UX del overlay de confirmación en terminal:** popup de Herdr, mensaje efímero en el pane u otra superficie; afecta a la presentación del aviso de brain caído.

## Apéndice histórico — diseño original whisper.cpp (2026-09-22, superado)

El diseño original (conservado como historia, no como plan) proponía una capa STT local del motor: subcomando `agent-tts --transcribe` con transcripción de fichero (`--transcribe FILE`) y captura en vivo (`--mic`); jerarquía `stt/` espejo de `providers/` con clase base `STTProvider` y primer proveedor `WhisperCppSTT` que resolvía el binario (`whisper-cli`/`whisper-cpp`/`main`) y el modelo GGML desde el store de voces; captura con `arecord` (Linux ALSA) y fallback `ffmpeg`; zero-cloud verificable; extras `agent-tts[stt]` opcionales; códigos de salida documentados. Quedó postergada en P4 el 2026-09-22, sobrevivió como "cobertura dual" el 2026-10-06 y quedó superada por la decisión del 2026-10-07: el STT se reutiliza del brain y esta PRD pasa a describir el cliente del plugin.
