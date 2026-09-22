**ID**: PRD-AT-02 · **Proyecto**: agent-tts
**Prioridad final (revisión 2026-09-22)**: P4 · **Estado**: Postergada
**Dependencias**: Ninguna (store de voces opcional)

> **Nota de revisión (22/09/2026)**: Postergada por decisión del maintainer: primero el núcleo de salida potente y componentizable; la bidireccionalidad (STT) llega en fases posteriores. Es la llave de HT-01 (herdr-tts) cuando llegue el momento.

# PRD-AT-02 — Capa STT local (`--transcribe`) con whisper.cpp

**Prioridad**: Alta · **Esfuerzo**: M

### Resumen ejecutivo

El motor hoy solo habla: no existe ninguna vía de entrada de audio. Esta PRD añade una capa STT (speech-to-text) local expuesta como servicio neutro del motor mediante `agent-tts --transcribe`, con una arquitectura de proveedores espejo de la de TTS y whisper.cpp como primer backend, invocado como binario local. Zero-cloud por defecto: ninguna transcripción sale de la máquina.

### Problema y flujo actual

Bruno dirige flotas de agentes en tmux/Herdr y cualquier intervención manual obliga a volver al teclado. Los flujos push-to-talk existentes en el ecosistema resuelven esto con scripts acoplados (whisper.cpp + `tmux send-keys` en `voice-to-code`, VoxCode) o con herramientas que casan STT y TTS en un solo binario para un solo agente (opencode-voice). El motor ya es la capa de voz neutra a la salida; le falta el espejo a la entrada para que herdr-tts pueda construir el intercom (HT-01) sin acoplar otro motor de STT.

### Propuesta

Subcomando `agent-tts --transcribe` con dos modos: transcripción de fichero (`--transcribe FILE`) y captura en vivo (`--mic`). Nueva jerarquía `stt/` espejo de `providers/`: clase base `STTProvider` con contrato `transcribe(wav_path) -> str` y atributo `name`; primer proveedor `WhisperCppSTT` que resuelve el binario (`whisper-cli`, `whisper-cpp` o `main` de una build local) y el modelo GGML. Los modelos viven en el store existente (`agent-tts voice install whisper-base` reutiliza el gestor atómico de descargas). Captura: `arecord` en Linux ALSA, `ffmpeg -f avfoundation/-dshow` como fallback multiplataforma; finalización con Ctrl-C o `--duration`. Salida a stdout, o a fichero con `--output`. Extra opcional `agent-tts[stt]` solo si se necesita binding Python; el camino por defecto es binario externo, sin dependencias nuevas.

### Historias de usuario (US-AT-02-1, ...)

- US-AT-02-1: Como maintainer de herdr, quiero `agent-tts --transcribe turn.wav` devolviendo texto en stdout para enchufar el intercom HT-01 sin otra dependencia.
- US-AT-02-2: Como usuario de terminal, quiero `agent-tts --transcribe --mic` con Ctrl-C de corte para dictar un comando sin instalar nada más que whisper.cpp.
- US-AT-02-3: Como usuario privado, quiero garantía de que ninguna muestra de audio sale de la máquina (sin claves configuradas, no hay red).

### Requisitos funcionales (RF-AT-02-...)

- RF-AT-02-1: `--transcribe FILE` acepta WAV/MP3/OGG; decodifica a WAV 16 kHz mono s16le (vía `ffmpeg` si el contenedor no es WAV nativo) antes de invocar el proveedor.
- RF-AT-02-2: `--transcribe --mic` graba con `arecord` (o `ffmpeg` si arecord no existe) y transcribe al corte; imprime exactamente el texto en stdout, un nivel de log por línea en stderr.
- RF-AT-02-3: Selección de proveedor con `--stt-provider` (default `whisper.cpp`) y de modelo con `--stt-model`; sin modelo instalado, error accionable que nombra el comando de instalación.
- RF-AT-02-4: `agent-tts voice install whisper-[tiny|base|small]` descarga el GGML al store con la misma validación atómica (Content-Length/sha256) que las voces TTS.
- RF-AT-02-5: `--lang es|en|auto` se pasa al backend; `auto` usa la detección del propio whisper.
- RF-AT-02-6: Códigos de salida documentados: 0 transcripción vacía o correcta, 2 binario ausente, 3 modelo ausente, 4 error de audio de entrada.

### Requisitos no funcionales (RNF-AT-02-...)

- RNF-AT-02-1: Zero-cloud verificable: sin claves API configuradas, el flujo completo no abre conexiones de red (verificable con pruebas sin salida de red).
- RNF-AT-02-2: Importación perezosa del módulo STT: `pip install agent-tts` sin extra no cambia el arranque del CLI TTS.
- RNF-AT-02-3: Un clip de 10 s con `whisper-base` en CPU transcribe en menos de 4 s en el hardware de referencia.
- RNF-AT-02-4: El proveedor se prueba con un binario stub en la suite de tests; ninguna prueba necesita whisper.cpp real.

### Encaje en la arquitectura actual

Paquete nuevo `src/agent_tts/stt/` con `base.py` (contrato) y `whisper_cpp.py`, registrado en `stt/__init__.py` igual que `providers/__init__.py` hace con `get_provider`. El subcomando vive en `cli.py` como rama temprana que no toca `speak()` ni `AudioSession`. El store de voces (`voices.py`) se extiende con una familia de modelos `whisper-*` manteniendo la validación de nombres actual.

### Prior art y diferenciación

opencode-voice acopla STT (whisper-cpp) y TTS (piper) en una herramienta para OpenCode con lectura condicional por longitud. Aquí STT es un servicio neutro del motor, independiente de agente y de consumidor: herdr-tts (intercom HT-01), scripts de push-to-talk propios, o cualquier harness. Los scripts whisper.cpp + `tmux send-keys` demuestran el patrón pero viven fuera del motor y duplican gestión de modelos; esta PRD los convierte en cliente de una sola pieza instalable.

### Dependencias

Ninguna interna. Externas: binario whisper.cpp (o extra `agent-tts[stt]`), `ffmpeg`/`arecord` para captura y decodificación.

### Riesgos y mitigaciones

- Riesgo: fragmentación de nombres de binario whisper.cpp (`main`, `whisper-cli` según versión). Mitigación: lista de candidatos ordenada y flag `--stt-bin` de escape.
- Riesgo: deriva de formatos GGML entre versiones de whisper.cpp. Mitigación: validar el modelo en la instalación y en el primer uso, con error accionable.
- Riesgo: captura de micrófono en WSL2 (sin arecord). Mitigación: `ffmpeg` con `pipewire`/`pulse` como fuente y documentación del requisito WSLg.

### Métricas de éxito

- WER menor o igual a 12% en un set de referencia de 20 clips es/en con whisper-base.
- Tiempo de transcripción menor que 0.4x la duración del clip (factor de tiempo real).
- Cero sockets de red abiertos durante el flujo completo (prueba automatizada).

### Fuera de alcance

Proveedores STT en la nube, transcripción en streaming con parciales, diarización de hablantes, wake-word detection (eso vive en herdr-tts, HT-01).

### Open questions

¿Modelo por defecto `base` o `small` (calidad vs RAM)? ¿Incluir VAD (silero) para auto-corte en `--mic`, o queda en Ctrl-C por ahora?
