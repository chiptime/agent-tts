**ID**: PRD-HT-01 · **Proyecto**: herdr-tts
**Prioridad final (revisión 2026-09-22)**: P4 · **Estado**: ACTIVA, diseño decidido (2026-10-07, 2.ª revisión del día), pendiente de implementar
**Dependencias**: [PRD-AT-02 — STT y captura como capacidad del motor](../../../../../docs/prds/AT-02-stt-whispercpp.md) (interfaz pública del motor; **no** depende del brain)

> **Nota de destino (2026-10-07, decisión final del maintainer):** diseño cerrado: keymap id `ptt` con modo **toggle** únicamente, captura de micrófono y transcripción como capacidad del **motor** (AT-02, `faster-whisper` reutilizado), consumida por el plugin vía su interfaz pública; confirmación en overlay e inyección con `herdr pane send-text` (literal, sin Enter) seguido de Enter opcional. El dictado **no requiere el brain**; puede estar apagado. Motor sin dependencia/modelo STT o captura no disponible: aviso visible, nada se inyecta, sin descarga implícita ni arranque de servicios. Interruptor propio; con ambos interruptores (AT-02 y HT-01) en `off`, comportamiento idéntico al actual. **Nada de esto está implementado todavía.**
>
> **Historia de esta nota:** 2026-09-22 diseño original (whisper.cpp local + `send-keys`); 2026-10-06 "cobertura dual" (D-R2); 2026-10-07 primera revisión (STT del brain vía `POST /transcribe`, commit `ed2fbfe`) — superada el mismo día por la decisión vigente: el motor es dueño del STT y de la captura, el plugin consume su interfaz pública.

# PRD-HT-01 — Push-to-Talk intercom (hablar al agente)

**Prioridad**: Alta (F4 del ROADMAP) · **Esfuerzo**: M-L

## Resumen ejecutivo (la decisión)

Un acorde registrado como command id estable `ptt` abre el micrófono en modo **toggle** (misma pulsación o timeout de silencio cortan), el motor captura y transcribe el audio (capacidad de AT-02), el texto reconocido se muestra en un overlay de confirmación y, al aceptar, se inyecta en el pane enfocado con `herdr pane send-text <PANE_ID> <TEXT>` —texto literal, sin Enter— seguido de un Enter opcional según configuración. Todo fallo (captura, dependencia, modelo) es visible y no rompe nada.

## Reparto de responsabilidades (decisión 2026-10-07)

| Capa | Dueño | Contenido |
|---|---|---|
| Plugin (`bin/herdr-tts`) | Específico de host | Keymap/acrode `ptt`, pane objetivo, overlay de confirmación, inyección `send-text`, interruptor `TTS_PTT`, guards |
| Motor (`agent-tts`) | Genérico | Captura de micrófono (PowerShell), STT `faster-whisper`, gestión/estados del modelo, política de descarga explícita |

## Flujo decidido

1. **Disparo:** acorde del command id `ptt` (sin acorde por defecto; asignable por el usuario en `keymap.json`).
2. **Captura:** micrófono vía PowerShell en el host Windows (capacidad genérica del motor, AT-02; análogo de entrada del destino de reproducción `wsl-ps`); en WSL2 la única vía nativa es `parecord`. Misma pulsación del acorde o timeout de silencio cortan la captura (modo `toggle`; `hold` descartado, ver supuesto pendiente).
3. **Transcripción:** el motor transcribe el audio (AT-02) y devuelve el texto al plugin por la interfaz pública que se acuerde (transporte = decisión abierta, ver abajo).
4. **Confirmación:** overlay con el texto reconocido en una línea; `Enter` acepta e inyecta, `Esc` cancela, tecla de re-intento reabre la captura sin salir del overlay.
5. **Inyección:** `herdr pane send-text <PANE_ID> <TEXT>` (literal, sin Enter; verificado: `herdr 0.9.1`, `herdr pane send-text --help`) y, según `TTS_PTT_ENTER`, un Enter final explícito. `herdr pane run` (texto+Enter en una llamada) **no se usa** para el dictado; `send-keys` tampoco.

## Command id y keymap (mecanismo verificado)

| Aspecto | Comportamiento | Evidencia |
|---|---|---|
| Ids estables | El keymap valida contra la lista cerrada `KEYMAP_COMMAND_IDS`; un id desconocido es error de carga | `bin/herdr-tts:5836-5840`, `:6129` |
| Ciclo de vida | `keymap init` planta la plantilla, `adopt` aplica estilos, `check` valida (incl. shadowing de Herdr core), `emit` renderiza bloques TOML `[[keys.command]]`, `apply` los inyecta con backup/rollback | `bin/herdr-tts:6167-6384` |
| Nuevo id `ptt` | Alta en `KEYMAP_COMMAND_IDS` + comando, etiqueta y tratamiento en estilos, como el resto de ids | **Propuesta** (siguiendo el mecanismo existente) |
| Disparo press-only | El diseño asume que el keymap de Herdr dispara solo en **pulsación** (sin evento de liberación), lo que descarta `hold`. **No verificado** en el código de Herdr core; supuesto a verificar durante la implementación | Sin evidencia en este repo |

## Configuración propuesta

Ninguno de estos nombres existe hoy en el código; todos son **propuestas** hasta implementarse. Convención seguida: `TTS_*="off|on"` con degradación estricta (como `TTS_READER_AUTO`, `bin/herdr-tts:263`), claves gestionadas por `config_set` (`bin/herdr-tts:1364-1366`) y superficie en el menú de ajustes (ciclado tipo `settings_cycle_value`, `bin/herdr-tts:5471-5475`). La configuración de la capacidad del motor es genérica y vive en el motor (AT-02), no en los ajustes del plugin.

| Nombre (propuesta) | Valores | Default | Función |
|---|---|---|---|
| `TTS_PTT` | `off\|on` | `off` | Interruptor de HT-01. Con `off`, el acorde `ptt` (si estuviera asignado) no dispara captura. |
| `TTS_PTT_ENTER` | `ask\|always\|never` | `ask` | Enter tras la inyección con `send-text`: `ask` pregunta en el overlay, `always` lo envía siempre, `never` nunca. |
| `TTS_PTT_SILENCE_SECONDS` | segundos | por definir | Timeout de silencio que corta la captura en modo `toggle`. |

**Interacción de interruptores y semántica de errores:** AT-02 (capacidad del motor) y HT-01 (PTT) son independientes. Con cualquiera de los dos en `off` no hay captura. Con ambos en `on` pero dependencia STT ausente, modelo ausente o captura no disponible: aviso **visible** en el overlay con pista accionable (instalación del extra / comando explícito de descarga / diagnóstico de PowerShell), nada se inyecta, el daemon sigue (fail-open). Con ambos en `off` (defaults), el comportamiento del plugin es **idéntico al actual**: sin procesos ni llamadas nuevas. El brain puede estar apagado en todos los casos.

## Guards y comportamiento ante fallos

- **Dictado vacío o < 2 caracteres:** nunca se inyecta; aviso en el overlay.
- **Dependencia STT del motor no instalada / modelo ausente:** aviso visible con la pista accionable (AT-02 porta la política: nunca descarga implícita, `stt.py:1-19`); nada se inyecta; fail-open. El plugin no instala dependencias ni descarga modelos.
- **Fallo de captura de micrófono:** aviso accionable con diagnóstico (PowerShell no disponible, sin interop); fail-open.
- El plugin **nunca** arranca ni gestiona el brain ni el daemon del motor para dictar; la residencia del modelo es decisión abierta de AT-02.

## Logging y privacidad

Cada dictado queda en `daemon.log` con duración, número de caracteres y `pane_id`. **No se persisten ni el audio ni la transcripción.**

## Encaje en la arquitectura actual

- **Consumo del motor (transporte = decisión abierta):** el plugin ya consume hoy la API pública Python del motor vía el puente `lib/` (`hosts/herdr/tts-plugin/lib/tts_engine.py:68`), patrón citado como normativo en `contracts/tts-brain-v2.md:36-37`; también usa el CLI/IPC del motor (`ipc-v2.md`). Qué superficie usa el dictado (CLI one-shot, daemon residiente o puente `lib/`) **no está decidido**; el test de bordes permite contratos e interfaces públicas (`engine/tests/test_monorepo_boundaries.py:3-5`). Ningún contrato prohíbe la alternativa HTTP plugin→brain (gobernada por nadie hoy): la decisión de motor es preferencia arquitectónica, no obligación contractual.
- **Captura por PowerShell (propuesta del motor, por analogía verificada):** el destino `wsl-ps` demuestra hoy el patrón de salida: un único `powershell.exe` persistente encontrado por interop WSL recibe grupos WAV prefijados por longitud por stdin (`engine/src/agent_tts/powershell_playback.py:88`, `:494`; disponibilidad vía `powershell.exe` en PATH, `:78-80`). El análogo de **entrada** no existe: es diseño propuesto en AT-02.
- **Overlay e inyección:** el overlay sigue el patrón de superficies efímeras del plugin; la inyección usa `herdr pane send-text` (verificado en `herdr 0.9.1`: envía texto literal sin Enter; `herdr pane run` añade Enter y no se usa; `send-keys` envía pulsaciones y no se usa para texto dictado).

## Fuera de alcance

Modo `hold`, dictado continuo, wake-word, STT en cloud, control de la UI de Herdr por voz, traducción automática, gestión del ciclo de vida del brain, migración del brain a la capacidad del motor (F4.10, autorización separada).

## Preguntas abiertas

1. **Transporte y residencia del modelo** (gate F4.4): CLI one-shot vs daemon vs API pública por puente `lib/`; y superficie de contrato (`ipc-v2.md` congelado: extender es breaking; ¿nuevo contrato, nueva versión, o superficie sin wire?).
2. **Comportamiento exacto de `send-text` con saltos de línea:** ¿qué hace `herdr pane send-text` si el texto dictado contiene un `\n`? ¿Se inyecta literal o se parte? Verificar con Herdr core antes de implementar.
3. **Valor del timeout de silencio** (`TTS_PTT_SILENCE_SECONDS`): default y unidad.
4. **Suposición press-only del keymap:** confirmar en Herdr core que no hay evento de liberación (hoy sostiene el descarte de `hold`).
5. **Formato y latencia de la captura de micrófono por PowerShell:** contenedor/codec, tamaño de fragmento y sobrecoste frente a `parecord`.
6. **UX del overlay en terminal:** popup de Herdr, mensaje efímero en el pane u otra superficie; afecta a confirmación, re-intento y avisos.

## Métricas de éxito (cuando exista implementación)

1. Hablar → ver texto reconocido → llega al agente (DoD de F4), **con el brain apagado**.
2. Con ambos interruptores en `off`, comportamiento idéntico al actual (verificable con la batería existente).
3. Latencia p50 press-to-text razonable para dictados cortos, medida en `daemon.log` (umbral por definir tras las pruebas de captura).

## Apéndice histórico — diseños superados (2026-09-22 y 2026-10-07)

Original (2026-09-22): modos `hold`+`toggle`, STT 100% local whisper.cpp en el motor, `send-keys` o "el verbo equivalente", `TTS_PTT_MODE` y `HERDR_TTS_STT_MODEL`. Primera revisión (2026-10-07, commit `ed2fbfe`): transcripción delegada al brain vía `POST /transcribe` con `TTS_STT`/`TTS_STT_URL`/`TTS_STT_TIMEOUT` propuestas. Ambos superados por la decisión vigente: solo `toggle`, STT y captura del motor (AT-02), `send-text` literal + Enter opcional, independencia del brain.

## Plan de implementación (F4 — todo pendiente)

Desglose con ids estables F4.1-F4.10. **Ningún ítem está implementado.** Orden: extracción al motor y política de modelo primero, red de tests del motor, **gate de decisión de transporte/residencia** (sin código hasta acordar), luego captura y silencio, después cableado del plugin (inyección, keymap, overlay, interruptores), y la migración del brain al final como unidad separada opcional. Verificación nombrando harnesses existentes: casos en `hosts/herdr/tts-plugin/tests/*_cases.sh` (protocolo `CASE <name> OK`), suite `tests/all_bash_harnesses.sh`, matriz `tests/matrix/`, y `pytest` del motor (`engine/tests/`).

> **Mapeo de ids (revisión 2026-10-07, 2.ª):** F4.1 era "cliente HTTP del brain" → ahora extracción al motor. F4.2 era "brain caído" → ahora extras opcionales y errores accionables. F4.3 era "interruptor `TTS_STT`" → ahora red de tests del motor (el interruptor pasa a F4.9 y a la config del motor, AT-02). F4.4/F4.5 antiguos (captura, silencio) se fusionan en el nuevo F4.5; el nuevo F4.4 es el gate de decisión. F4.6-F4.9 conservan su significado. F4.10 es nuevo.

> **Regla del ROADMAP (F6, ya vigente):** un solo flujo edita `bin/herdr-tts` a la vez; F4 no se solapa con F6 ni con otros escritores del bash del plugin.

| ID | Comportamiento (uno por ítem) | Ficheros esperados | Verificación | Depende de |
|---|---|---|---|---|
| F4.1 | Extracción STT al motor: port de `Transcriber` (estados, carga perezosa, `transcribe_bytes`) y política de modelo (nunca descarga automática; descarga explícita; calentamiento solo local) desde el brain | `engine/src/agent_tts/` (nombre de módulo propuesto: `stt.py`) | `pytest` nuevo en `engine/tests/` contra stub del modelo; sin red | — |
| F4.2 | Extra opcional y dependencias perezosas: `agent-tts[stt]` (precedente kokoro), import perezoso, error visible con pista de instalación; sin dependencias nuevas en la instalación base | `engine/pyproject.toml`, módulo STT | `pytest`: sin el extra, error accionable y cero import de `faster_whisper` | F4.1 |
| F4.3 | Red de tests del motor (test seam): estados `unavailable/loading/ready`, modelo ausente/presente, transcripción determinista con stub, sin red ni descargas | `engine/tests/` | `pytest` verde, aislado de modelos reales | F4.1 |
| F4.4 | **GATE de decisión (sin código):** transporte/residencia (CLI one-shot vs daemon `--serve` vs API pública por puente `lib/`) y superficie de contrato (`ipc-v2.md` congelado: extender es breaking; ¿nuevo contrato, nueva versión, superficie sin wire?) | documento/contrato que el maintainer apruebe | decisión registrada y fechada del maintainer | F4.1-F4.3 |
| F4.5 | Captura de micrófono genérica en el motor (PowerShell, análogo de entrada de `powershell_playback.py`) + timeout de silencio configurable; fallo de PowerShell = aviso accionable | `engine/src/agent_tts/` (captura), silencio (lugar por decidir en F4.4) | `pytest` con stub de `powershell.exe`; negativo sin interop | F4.4 |
| F4.6 | Inyección: `herdr pane send-text <PANE_ID> <TEXT>` (literal, sin Enter) + Enter final según `TTS_PTT_ENTER="ask\|always\|never"`; nunca `run` ni `send-keys` para el dictado; guards vacío/<2 caracteres | `bin/herdr-tts` | Caso nuevo con stub de `herdr`: literal sin Enter, Enter según modo, guard activo | F4.4 |
| F4.7 | Command id `ptt` en el keymap (alta en `KEYMAP_COMMAND_IDS`, comando, etiqueta, estilos), sin acorde por defecto; validar suposición press-only en Herdr core | `bin/herdr-tts` | `keymap_cases.sh` extendido: id conocido, emit/apply/rollback con `ptt` | F4.6 |
| F4.8 | Overlay de confirmación: texto reconocido, `Enter` inyecta, `Esc` cancela, re-intento; avisos de guards, dependencia/modelo ausentes y captura caída | `bin/herdr-tts` | Caso nuevo de overlay (stubs) + verificación manual anotada | F4.6 |
| F4.9 | Interruptores independientes: `TTS_PTT` (plugin) y el interruptor de la capacidad del motor (AT-02, nombre propuesto `AGENT_TTS_STT`); aceptación final: **ambos en `off` = comportamiento idéntico al actual** (batería completa en verde) | `bin/herdr-tts` + config del motor | `all_bash_harnesses.sh` completo sin fallos con ambos interruptores en `off`; dictado OK con el brain apagado | F4.5-F4.8 |
| F4.10 | **OPCIONAL, autorización separada posterior:** migración del brain a la capacidad STT del motor preservando el comportamiento actual del navegador y del servidor (`POST /transcribe`, `/health`) | `hosts/herdr/brain/` | tests existentes del brain en verde; `/transcribe` y `/health` sin cambios observables | F4.1-F4.9 + autorización |

Progreso: **0 de 10**. Cada ítem se marca solo con su evidencia (caso OK en el harness nombrado o decisión registrada para el gate).
