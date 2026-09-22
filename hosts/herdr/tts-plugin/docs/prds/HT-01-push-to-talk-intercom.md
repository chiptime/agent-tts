**ID**: PRD-HT-01 · **Proyecto**: herdr-tts
**Prioridad final (revisión 2026-09-22)**: P4 · **Estado**: Postergada
**Dependencias**: PRD-AT-02 (capa STT del motor)

# PRD-HT-01 — Push-to-Talk intercom (hablar al agente)

> **Nota de revisión (2026-09-22)**: El maintainer la quiere, pero va al final: dos candados — AT-02 (STT del motor) está postergada a P4, y `herdr pane send-keys` no existe aún en la API de Herdr (prerrequisito externo a negociar con Herdr core). Norte estratégico del ciclo bidireccional.

**Prioridad**: Alta · **Esfuerzo**: M-L

## Resumen ejecutivo
Permitir hablar directamente al agente del pane enfocado: un acorde abre el micrófono, la transcripción es 100% local (whisper.cpp vía la futura capa STT del motor `agent-tts`, PRD-AT-02), se muestra una confirmación visual breve del texto reconocido y este se inyecta como prompt en el pane, con Enter opcional. Cierra el ciclo de voz bidireccional y es el único ítem abierto del roadmap actual.

## Problema y flujo actual
Hoy la comunicación es asimétrica: el agente habla (TTS) y el usuario teclea. Durante la revisión de un diff, o cuando las manos están ocupadas escribiendo en otro chat, dictar una instrucción corta ("aplica la opción b", "corrige el test que falla") sería más rápido que teclearla. No existe ninguna vía de entrada de voz.

## Propuesta
Nuevo command id estable `ptt` en `keymap.json` (sin acorde obligatorio; se sugiere `prefix+c`, validado con `keymap check`). Dos modos de captura: hold (micrófono abierto mientras se mantiene) y toggle. Al soltar o pulsar de nuevo: transcripción local, overlay de confirmación con el texto reconocido (Enter acepta e inyecta, Esc cancela, re-intento sin salir del overlay), e inyección en el pane enfocado mediante el mecanismo de escritura de Herdr, con Enter final configurable.

## Historias de usuario (US-HT-01-1, ...)
- **US-HT-01-1** — Como operador de flota, quiero dictar una instrucción corta al agente enfocado sin teclear, para mantener el ritmo de revisión.
- **US-HT-01-2** — Como usuario de STT local, quiero ver el texto reconocido antes de inyectarlo, para detectar alucinaciones del modelo.
- **US-HT-01-3** — Como usuario en WSL2, quiero usar el micrófono del host Windows sin instalar nada adicional, como ya hago con la salida de audio.
- **US-HT-01-4** — Como usuario consciente de privacidad, quiero transcripción 100% local sin cloud, para no filtrar prompts fuera de la máquina.

## Requisitos funcionales (RF-HT-01-...)
- **RF-HT-01-1**: command id `ptt` registrado en el keymap declarativo, gestionado por `keymap init/adopt/check/emit/apply` como cualquier otro id, sin acorde por defecto.
- **RF-HT-01-2**: modos `hold` y `toggle` vía `TTS_PTT_MODE="hold|toggle"` (default `hold`).
- **RF-HT-01-3**: transcripción delegada a la capa STT del motor (`agent-tts`, PRD-AT-02); modelo configurable con `HERDR_TTS_STT_MODEL` y default conservador (whisper base/small cuantizado).
- **RF-HT-01-4**: overlay de confirmación: texto reconocido en una línea, Enter inyecta, Esc cancela, tecla de re-intento reabre la captura sin salir.
- **RF-HT-01-5**: inyección en el pane enfocado con `herdr pane send-keys` o el verbo equivalente de la API de Herdr; Enter final según `TTS_PTT_ENTER="ask|always|never"` (default `ask`).
- **RF-HT-01-6**: guard de seguridad: nunca inyecta texto vacío ni menor de 2 caracteres; muestra aviso en el overlay.
- **RF-HT-01-7**: cada dictado queda en `daemon.log` (duración, caracteres, pane_id); no se persisten ni audio ni transcripción.

## Requisitos no funcionales (RNF-HT-01-...)
- **RNF-HT-01-1**: latencia press-to-text <= 1.5 s (p50) para un dictado de 5 s en CPU moderno con el modelo default.
- **RNF-HT-01-2**: cero peticiones de red durante captura y transcripción (verificable con monitor de red).
- **RNF-HT-01-3**: el proceso de STT vive en el venv del motor; el host no añade dependencias nuevas.
- **RNF-HT-01-4**: un fallo del STT nunca bloquea el terminal: fail-open con mensaje accionable.

## Encaje en la arquitectura actual
El command id nuevo usa la infraestructura de keymap existente. La captura y transcripción se delegan al motor (PRD-AT-02), siguiendo el patrón thin host: el host solo orquesta (dispara captura, muestra confirmación, inyecta). La inyección usa la vía de escritura de Herdr, análoga a como la paleta usa `herdr pane focus`. En WSL2, el micrófono llega por la misma ruta de audio existente (WSLg/Pulse o winhost), igual que la salida.

## Prior art y diferenciación
`voice-to-code`, `VoxCode` y los scripts whisper.cpp + `tmux send-keys` demuestran el patrón dictado→inyección en tmux genérico; `opencode-voice` aporta STT whisper-cpp local. Nuestra diferenciación: integración nativa con el concepto de chat de Herdr (inyección al pane enfocado, no a un terminal arbitrario), confirmación visual previa a la inyección anti-alucinación, y convivencia con el gating y el keymap declarativo existentes. TalkToCursor (MCP server con tool `speak`, donde el modelo decide cuándo hablar) es el enfoque complementario inverso; se cita como referencia y no se implementa.

## Dependencias
- PRD-AT-02 (capa STT del motor `agent-tts`) — bloqueante.
- Confirmar el verbo exacto de inyección de teclas en la API de Herdr (`send-keys` o equivalente).

## Riesgos y mitigaciones
- Alucinaciones del STT → confirmación visual obligatoria (RF-HT-01-4) y guard de longitud mínima.
- Conflicto del acorde sugerido con Herdr core → `keymap check` lo detecta; el acorde es editable por el usuario.
- Latencia alta con modelos grandes → default conservador y guía de elección de modelo en la documentación.
- Micrófono no expuesto en WSL2 → documentar la ruta WSLg/winhost; fail-open con diagnóstico claro.

## Métricas de éxito
1. >= 5 dictados por día de trabajo real tras dos semanas de adopción (autoinforme del maintainer).
2. Tasa de cancelación en la confirmación < 15% (texto reconocido suficientemente usable).
3. Latencia p50 press-to-text <= 1.5 s medida en `daemon.log`.
4. Cero salidas de red durante el flujo completo.

## Fuera de alcance
Dictado continuo siempre activo, wake-word, STT en cloud, control de la UI de Herdr por voz, traducción automática del dictado.

## Open questions
¿Hold o toggle como default? ¿El overlay de confirmación vive en un popup de Herdr o como mensaje efímero en el pane? ¿Whisper.cpp como binario externo o binding dentro del motor? ¿Enter automático por defecto (`ask`) o nunca?
