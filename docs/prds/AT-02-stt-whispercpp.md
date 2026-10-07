**ID**: PRD-AT-02 · **Proyecto**: agent-tts
**Prioridad final (revisión 2026-09-22)**: P4 · **Estado**: REENFOCADA (2026-10-07, 2.ª revisión del día) — STT y captura de micrófono como capacidad genérica del motor; whisper.cpp y el cliente HTTP del brain descartados
**Dependencias**: ninguna del brain. El motor es dueño de la capacidad; los hosts la consumen por su interfaz pública.

> **Nota de destino (2026-10-07, decisión final del maintainer):** la capacidad STT compartida **y** la captura de micrófono pertenecen al motor `agent-tts`, reutilizando la implementación `faster-whisper` ya existente en el brain (extracción, no segunda pila whisper.cpp). El plugin consume la interfaz pública del motor; el dictado **no** requiere que el brain esté arrancado. La migración del brain a esta capacidad del motor es una rebanada posterior, separada y opcional.
>
> **Cronología de la decisión:** (1) 2026-09-22 diseño original whisper.cpp local; (2) 2026-10-06 directiva de "cobertura dual" (D-R2); (3) 2026-10-07 primera revisión: cliente HTTP del plugin sobre `POST /transcribe` del brain (commit `ed2fbfe`); (4) 2026-10-07 decisión vigente: el motor es dueño del STT y de la captura. Los diseños (1) y (3) quedan como historia en los apéndices.

# PRD-AT-02 — STT y captura de micrófono como capacidad del motor

**Prioridad**: Alta (F4 del ROADMAP) · **Esfuerzo**: M

## Resumen ejecutivo (la decisión)

El motor expone una capacidad genérica de STT (`faster-whisper` reutilizado por extracción del brain) y una vía genérica de captura de micrófono (PowerShell en el host Windows, por analogía con el destino de reproducción `wsl-ps`). La dependencia STT es **opcional** (extra, siguiendo el precedente `kokoro`), la descarga del modelo es **siempre explícita** y la configuración del motor **no** queda atada a los ajustes de ningún host. Si la dependencia o el modelo faltan: error visible con pista accionable, nada se transcribe, sin descarga implícita ni arranque de servicios. Todo lo de esta PRD es diseño pendiente de implementar: **nada está implementado ni verificado más allá de lo citado con fichero y línea.**

## Qué gana el motor (propuestas hasta acordar nombres)

| Pieza | Diseño | Evidencia / precedente verificado |
|---|---|---|
| Módulo STT (nombre propuesto: `engine/src/agent_tts/stt.py`) | Extracción de la clase `Transcriber` del brain: estados `loading/ready/unavailable`, carga perezosa del modelo, `transcribe_bytes` sobre fichero temporal | `hosts/herdr/brain/src/herdr_brain/stt.py:33-35` (estados), `:108-109` (`from faster_whisper import WhisperModel` perezoso), `:154` y `:165` (`transcribe_bytes`, `model.transcribe(tmp_path, language=None)`) |
| Política de modelo (regla dura, portada literal) | Nunca descarga automática en boot/import; descarga como acción explícita del operador con timeout de red; si falta, estado `unavailable` con pista del comando de descarga; si existe, hilo de calentamiento que solo lee ficheros locales | `stt.py:1-19` (política), `:37` (`PULL_COMMAND`), `:56` (`model_is_cached` nunca toca red) |
| Aliases de modelo | `tiny/base/small/medium/large-v2/large-v3` → repos HF, sin importar ctranslate2 para responder "¿está en caché?" | `stt.py:46-53` |
| Extra opcional (nombre propuesto: `agent-tts[stt]`) | Import perezoso con error visible que nombra el comando de instalación; la instalación base del motor no gana dependencias | Precedente kokoro: `engine/pyproject.toml:31-41` (solo `kokoro` y `dev` hoy), `engine/src/agent_tts/providers/__init__.py:14-33` (PEP 562 + pista `pip install 'agent-tts[kokoro]'`) |
| Captura de micrófono (propuesta) | Análogo de **entrada** del patrón `wsl-ps`: un `powershell.exe` persistente alcanzable por interop WSL entrega audio; formato/latencia abiertos | Salida verificada hoy: `engine/src/agent_tts/powershell_playback.py:78-80` (disponibilidad), `:88` (sesión persistente, grupos WAV prefijados por longitud), `:490-500` (spawn perezoso) |

## Configuración (propuestas)

Ningún nombre existe hoy en el código del motor; todos son **propuestas** hasta que se implementen. La configuración de STT del motor es **genérica e independiente de los ajustes del host** (p. ej. de `Settings` del plugin): el motor no lee la configuración de herdr ni del brain.

| Nombre (propuesta) | Valores | Default | Función |
|---|---|---|---|
| `AGENT_TTS_STT` | `off\|on` | `off` | Interruptor de la capacidad STT del motor (AT-02). |
| `AGENT_TTS_STT_MODEL` | alias o repo HF | por decidir | Modelo a usar (aliases de `stt.py:46-53`). |

El descubrimiento del brain (`HERDR_BRAIN_PORT` → `config.env` → 8741, `config.py:24` y `:205-209`) **ya no aplica**: no hay URL del brain en este diseño.

## Independencia del brain y semántica de errores

- El dictado funciona con el brain **apagado** (consecuencia intencional de la decisión).
- Dependencia STT no instalada: error visible con la pista de instalación (patrón kokoro); nada se transcribe; sin instalación implícita.
- Modelo ausente: estado `unavailable` + pista del comando explícito de descarga (patrón `stt.py:1-19`); sin descarga implícita, sin arranque de servicios.
- Captura no disponible (sin PowerShell/interop): aviso accionable con diagnóstico; fail-open.
- Interruptores independientes: AT-02 (capacidad del motor) y HT-01 (PTT del plugin) se activan por separado; cualquiera en `off` desactiva el flujo completo; **ambos en `off` = comportamiento idéntico al actual**.

## Límites arquitectónicos (verificados, no supuestos)

- Ningún contrato del monorepo prohíbe llamadas HTTP plugin→brain: `contracts/tts-brain-v1.md:11` restringe la dirección **brain→plugin** (superficie CLI) y `contracts/README.md:8` rige hosts→motor vía `ipc-v2.md` **y artefactos públicos**. La decisión de mover el STT al motor es una **preferencia arquitectónica** (dueño único de la capacidad, reutilización), no una obligación contractual.
- El test de bordes es unidireccional: el motor no puede importar `hosts/*`; los hosts **pueden** depender del motor vía contratos e interfaces públicas (`engine/tests/test_monorepo_boundaries.py:3-5`).
- La API pública Python del motor ya es consumida por el plugin hoy vía el puente `lib/` (`hosts/herdr/tts-plugin/lib/tts_engine.py:68`, `from agent_tts import ...`), patrón citado como normativo en `contracts/tts-brain-v2.md:36-37`. Por tanto "CLI/IPC obligatorios para todo host" **no** es cierto; el transporte exacto de STT queda como decisión abierta (ver abajo).

## Decisiones NO tomadas (no silenciar en implementación)

1. **Transporte y residencia del modelo:** CLI one-shot (proceso por dictado) vs residencia en el daemon existente (`--serve`, AT-04) vs API pública Python (patrón puente `lib/`). Sin elección registrada.
2. **Superficie de contrato:** `contracts/ipc-v2.md` está **congelado** ("Post-freeze changes are breaking", `ipc-v2.md:3-4`); extender el canal de control es un cambio breaking: ¿nuevo fichero de contrato, nueva versión de protocolo, o superficie CLI/Python sin contrato de wire? Sin elección registrada.
3. **Ciclo de vida y latencia:** coste de arranque del modelo por dictado vs memoria residente; umbral aceptable p50 press-to-text.
4. **Formato y latencia de la captura PowerShell:** contenedor/codec, tamaño de fragmento, sobrecoste frente a `parecord` (única vía nativa en WSL2).
5. **Umbrales de silencio** que cortan la captura (nombre, default y unidad de la variable).
6. **Manejo exacto de saltos de línea** en el texto dictado (afecta a la inyección de HT-01).
7. **Suposición press-only del keymap** (descarta el modo `hold`): no verificada en Herdr core.

## Fuera de alcance

- whisper.cpp (apéndice A) y el cliente HTTP sobre el brain (apéndice B): diseños superados.
- Gestión del ciclo de vida del brain por parte de motor o plugin.
- Streaming con parciales, diarización, wake-word, STT en cloud, VAD refactorizada.
- Migración del brain a esta capacidad (rebanada posterior separada; ver HT-01/F4.10).

## Apéndice A — diseño original whisper.cpp (2026-09-22, superado)

Capa STT local del motor: subcomando `agent-tts --transcribe` con transcripción de fichero (`--transcribe FILE`) y captura en vivo (`--mic`); jerarquía `stt/` espejo de `providers/` con `STTProvider` y `WhisperCppSTT` resolviendo binario (`whisper-cli`/`whisper-cpp`/`main`) y modelo GGML; captura con `arecord`/`ffmpeg`; extras `agent-tts[stt]`. Postergada P4 el 2026-09-22; superada.

## Apéndice B — cliente HTTP del plugin sobre el brain (2026-10-07, commit `ed2fbfe`, superado el mismo día)

El plugin dictaba audio y lo enviaba a `POST /transcribe` del brain (multipart `audio`, `{"text"}`), con `TTS_STT_URL`/`TTS_STT_TIMEOUT` propuestas y aviso visible con el brain caído. Contrato del endpoint verificado entonces en `server.py:598-624` (503 unavailable/loading, 400 vacío). Superado horas después por la decisión de que el motor es dueño del STT y la captura; el brain conserva hoy ese endpoint (la migración es la F4.10 opcional).
