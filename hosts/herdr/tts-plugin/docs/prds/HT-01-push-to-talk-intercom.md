**ID**: PRD-HT-01 · **Proyecto**: herdr-tts
**Prioridad final (revisión 2026-09-22)**: P4 · **Estado**: ACTIVA, diseño decidido (2026-10-07), pendiente de implementar
**Dependencias**: [PRD-AT-02 — Cliente STT del plugin](../../../../../docs/prds/AT-02-stt-whispercpp.md) (endpoint `POST /transcribe` del brain)

> **Nota de destino (2026-10-07 — decisiones F4 del maintainer):** diseño cerrado: keymap id `ptt` con modo **toggle** únicamente, captura de micrófono por PowerShell en el host Windows (patrón `wsl-ps`), transcripción delegada al brain vía AT-02, confirmación en overlay e inyección con `herdr pane send-text` (literal, sin Enter) seguido de Enter opcional. Brain caído: aviso visible, nada se inyecta, el plugin nunca arranca ni gestiona el brain. Interruptor propio; con ambos interruptores (AT-02 y HT-01) en `off`, comportamiento idéntico al actual. **Nada de esto está implementado todavía.**
>
> **Historia de esta nota:** el 2026-10-06 existió una nota de "cobertura dual" (Decisión D-R2) que mantenía esta PRD a la espera de una capa STT local (whisper.cpp) en el motor; esa directiva quedó **superada** por la decisión del 2026-10-07. El diseño previo (hold+toggle, `send-keys`, STT local del motor del 2026-09-22) se conserva resumido en el apéndice histórico.

# PRD-HT-01 — Push-to-Talk intercom (hablar al agente)

**Prioridad**: Alta (F4 del ROADMAP) · **Esfuerzo**: M-L

## Resumen ejecutivo (la decisión)

Un acorde registrado como command id estable `ptt` abre el micrófono en modo **toggle** (misma pulsación o timeout de silencio cortan), el audio se transcribe en el brain (`POST /transcribe` vía AT-02), el texto reconocido se muestra en un overlay de confirmación y, al aceptar, se inyecta en el pane enfocado con `herdr pane send-text <PANE_ID> <TEXT>` —texto literal, sin Enter— seguido de un Enter opcional según configuración. Reconocimiento y captura fallan de forma visible y sin romper nada.

## Flujo decidido

1. **Disparo:** acorde del command id `ptt` (sin acorde por defecto; asignable por el usuario en `keymap.json`).
2. **Captura:** micrófono vía PowerShell en el host Windows (analogía del destino de reproducción `wsl-ps`); en WSL2 la única vía nativa es `parecord`. Misma pulsación del acorde o timeout de silencio cortan la captura (modo `toggle`; `hold` descartado, ver supuesto pendiente).
3. **Transcripción:** el plugin envía el audio al brain `POST /transcribe` (campo multipart `audio`) y recibe `{"text": ...}` (AT-02).
4. **Confirmación:** overlay con el texto reconocido en una línea; `Enter` acepta e inyecta, `Esc` cancela, tecla de re-intento reabre la captura sin salir del overlay.
5. **Inyección:** `herdr pane send-text <PANE_ID> <TEXT>` (literal, sin Enter; verificado: `herdr 0.9.1`, `herdr pane send-text --help`) y, según `TTS_PTT_ENTER`, un Enter final explícito. `herdr pane run` (texto+Enter en una llamada) **no se usa** para el dictado; `send-keys` tampoco.

## Command id y keymap (mecanismo verificado)

| Aspecto | Comportamiento | Evidencia |
|---|---|---|
| Ids estables | El keymap valida contra la lista cerrada `KEYMAP_COMMAND_IDS`; un id desconocido es error de carga | `bin/herdr-tts:5836-5840`, `:6129` |
| Ciclo de vida | `keymap init` planta la plantilla, `adopt` aplica estilos, `check` valida (incl. shadowing de Herdr core), `emit` renderiza bloques TOML `[[keys.command]]`, `apply` los inyecta con backup/rollback | `bin/herdr-tts:6167-6384` |
| Nuevo id `ptt` | Alta en `KEYMAP_COMMAND_IDS` + comando, etiqueta y tratamiento en estilos, como el resto de ids | **Propuesta** (siguiendo el mecanismo existente) |
| Disparo press-only | El diseño asume que el keymap de Herdr dispara solo en **pulsación** (sin evento de liberación), lo que descarta `hold`. **No verificado** en el código de Herdr core; queda como supuesto a verificar durante la implementación | Sin evidencia en este repo |

## Configuración propuesta

Ninguno de estos nombres existe hoy en el código; todos son **propuestas** hasta implementarse. Convención seguida: `TTS_*="off|on"` con degradación estricta (como `TTS_READER_AUTO`, `bin/herdr-tts:263`), claves gestionadas por `config_set` (`bin/herdr-tts:1364-1366`) y superficie en el menú de ajustes (ciclado tipo `settings_cycle_value`, `bin/herdr-tts:5471-5475`).

| Nombre (propuesta) | Valores | Default | Función |
|---|---|---|---|
| `TTS_PTT` | `off\|on` | `off` | Interruptor de HT-01. Con `off`, el acorde `ptt` (si estuviera asignado) no dispara captura. |
| `TTS_STT` | `off\|on` | `off` | Interruptor del cliente STT de AT-02 (se cita aquí porque el flujo lo requiere). |
| `TTS_PTT_ENTER` | `ask\|always\|never` | `ask` | Enter tras la inyección con `send-text`: `ask` pregunta en el overlay, `always` lo envía siempre, `never` nunca. |
| `TTS_PTT_SILENCE_SECONDS` | segundos | por definir | Timeout de silencio que corta la captura en modo `toggle`. |

**Aceptación de los interruptores:** con `TTS_STT="off"` y `TTS_PTT="off"` (defaults), el comportamiento del plugin es **idéntico al actual**: nada de este diseño se activa, no hay procesos ni llamadas nuevas.

## Guards y comportamiento ante fallos

- **Dictado vacío o < 2 caracteres:** nunca se inyecta; aviso en el overlay (el brain ya responde `400` para audio vacío, `server.py:614-616`).
- **Brain caído / 503 / error de conexión:** aviso **visible** en el overlay (incluida la pista del `stt pull` cuando el 503 la trae, `server.py:603-607`), nada se inyecta, el daemon sigue (fail-open). El plugin **nunca** arranca ni gestiona el brain.
- **Fallo de captura de micrófono:** aviso accionable con diagnóstico (PowerShell no disponible, sin interop); fail-open.

## Logging y privacidad

Cada dictado queda en `daemon.log` con duración, número de caracteres y `pane_id`. **No se persisten ni el audio ni la transcripción.** La transcripción viaja solo por localhost hacia el brain.

## Encaje en la arquitectura actual

- **Captura por PowerShell (propuesta, por analogía verificada):** el destino `wsl-ps` demuestra hoy el patrón: un único `powershell.exe` persistente encontrado por interop WSL recibe grupos WAV prefijados por longitud por stdin (`engine/src/agent_tts/powershell_playback.py:88`, `:494`; disponibilidad vía `powershell.exe` en PATH, `:78-80`). El plugin modela los destinos en `SETTINGS_TARGETS` (`bin/herdr-tts:5265`). El análogo de **entrada** (PowerShell captura el micrófono y entrega audio) no existe: es diseño propuesto.
- **Overlay e inyección:** el overlay sigue el patrón de superficies efímeras del plugin; la inyección usa `herdr pane send-text` (verificado en `herdr 0.9.1`: envía texto literal sin Enter; `herdr pane run` añade Enter y no se usa; `send-keys` envía pulsaciones y no se usa para texto dictado).

## Fuera de alcance

Modo `hold`, dictado continuo, wake-word, STT en cloud, control de la UI de Herdr por voz, traducción automática, gestión del ciclo de vida del brain.

## Preguntas abiertas

1. **Comportamiento exacto de `send-text` con saltos de línea:** ¿qué hace `herdr pane send-text` si el texto dictado contiene un `\n`? ¿Se inyecta literal o se parte? Verificar con Herdr core antes de implementar.
2. **Valor del timeout de silencio** (`TTS_PTT_SILENCE_SECONDS`): default y unidad.
3. **Suposición press-only del keymap:** confirmar en Herdr core que no hay evento de liberación (hoy sostiene el descarte de `hold`).
4. **Formato y latencia de la captura de micrófono por PowerShell:** contenedor/codec, tamaño de fragmento y sobrecoste frente a `parecord`.
5. **Descubrimiento de la URL del brain:** variable `TTS_STT_URL` frente a reutilizar la precedencia `HERDR_BRAIN_PORT` → `config.env` → 8741 (`herdr_resolve_port`, `bin/herdr-tts:82-106`).
6. **UX del overlay en terminal:** popup de Herdr, mensaje efímero en el pane u otra superficie; afecta a confirmación, re-intento y avisos de brain caído.

## Métricas de éxito (cuando exista implementación)

1. Hablar → ver texto reconocido → llega al agente (DoD de F4).
2. Con ambos interruptores en `off`, comportamiento idéntico al actual (verificable con la batería existente).
3. Latencia p50 press-to-text razonable para dictados cortos, medida en `daemon.log` (umbral por definir tras las pruebas de captura).

## Apéndice histórico — diseño original (2026-09-22, superado)

El diseño original proponía: modos `hold` y `toggle` con default `hold`; transcripción 100% local vía whisper.cpp en el motor (PRD-AT-02 original); inyección con `herdr pane send-keys` o "el verbo equivalente" (por entonces inexistente en la API de Herdr); `TTS_PTT_MODE` y `HERDR_TTS_STT_MODEL`. Quedó postergada en P4 el 2026-09-22, pasó por la nota de cobertura dual del 2026-10-06 y quedó superada por las decisiones del 2026-10-07: solo `toggle`, STT del brain, `send-text` literal + Enter opcional, captura por PowerShell.

## Plan de implementación (T4 — todo pendiente)

Desglose con ids estables F4.1-F4.9. **Ningún ítem está implementado.** Orden de dependencias: cliente STT y manejo de brain caído primero, captura de micrófono segundo, inyección tercero, keymap y overlay al final, interruptores transversales. Verificación nombrando los harnesses existentes del plugin: casos en `hosts/herdr/tts-plugin/tests/*_cases.sh` con protocolo `CASE <name> OK`, suite combinada `hosts/herdr/tts-plugin/tests/all_bash_harnesses.sh` y matriz `hosts/herdr/tts-plugin/tests/matrix/`.

> **Regla del ROADMAP (F6, ya vigente):** un solo flujo edita `bin/herdr-tts` a la vez; F4 no se solapa con F6 ni con otros escritores del bash del plugin.

| ID | Comportamiento (uno por ítem) | Ficheros esperados | Verificación | Depende de |
|---|---|---|---|---|
| F4.1 | Cliente STT: función que envía audio a `POST /transcribe` (multipart `audio`) y devuelve el texto; resuelve la URL del brain con la precedencia `HERDR_BRAIN_PORT` → `config.env` → 8741 | `bin/herdr-tts` | Caso nuevo en `hosts/herdr/tts-plugin/tests/critical_cases.sh` (o harness dedicado) contra un stub HTTP del brain: 200 `{"text"}`, 503, 400 | — |
| F4.2 | Manejo de brain caído: error de conexión o 503 produce aviso visible y no inyecta nada; el plugin nunca arranca ni gestiona el brain | `bin/herdr-tts` | Caso nuevo: stub HTTP caído → aviso, salida limpia, daemon vivo | F4.1 |
| F4.3 | Interruptor `TTS_STT="off\|on"` (default `off`): clave gestionada, degradación estricta y fila en el menú de ajustes | `bin/herdr-tts` | Casos en `config_cases.sh` (set/invalid) + `keymap_cases.sh` intacto; con `off`, cero llamadas al brain | F4.1 |
| F4.4 | Captura de micrófono por PowerShell (patrón `wsl-ps`): captura hasta corte, entrega el audio al cliente STT; fallo de PowerShell = aviso accionable | `bin/herdr-tts` | Caso nuevo con stub de `powershell.exe`; negativo sin interop | F4.1-F4.3 |
| F4.5 | Timeout de silencio que corta la captura (`TTS_PTT_SILENCE_SECONDS`) | `bin/herdr-tts` | Caso nuevo: silencio simulado corta en el plazo | F4.4 |
| F4.6 | Inyección: `herdr pane send-text <PANE_ID> <TEXT>` (literal, sin Enter) + Enter final según `TTS_PTT_ENTER="ask\|always\|never"`; nunca `run` ni `send-keys` para el dictado; guards vacío/<2 caracteres | `bin/herdr-tts` | Caso nuevo con stub de `herdr`: literal sin Enter, Enter según modo, guard activo | F4.1 |
| F4.7 | Command id `ptt` en el keymap (alta en `KEYMAP_COMMAND_IDS`, comando, etiqueta, estilos), sin acorde por defecto; validar suposición press-only en Herdr core | `bin/herdr-tts` | `keymap_cases.sh` extendido: id conocido, emit/apply/rollback con `ptt` | F4.6 |
| F4.8 | Overlay de confirmación: texto reconocido, `Enter` inyecta, `Esc` cancela, re-intento; avisos de guards y brain caído | `bin/herdr-tts` | Caso nuevo de overlay (stubs) + verificación manual anotada | F4.2, F4.6 |
| F4.9 | Interruptor `TTS_PTT="off\|on"` (default `off`) y aceptación final: **con ambos interruptores en `off`, comportamiento idéntico al actual** (batería completa en verde) | `bin/herdr-tts` | `all_bash_harnesses.sh` completo sin fallos con `TTS_STT=off` y `TTS_PTT=off` | F4.3-F4.8 |

Progreso: **0 de 9**. Cada ítem se marca solo con su evidencia (caso OK en el harness nombrado).
