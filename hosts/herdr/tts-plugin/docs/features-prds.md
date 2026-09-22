# herdr-tts — PRDs de features 2026

**Fecha:** 22 de septiembre de 2026
**Estado:** Borrador para revisión del maintainer
**Alcance:** Plugin host `herdr-tts` (capa de orquestación Herdr). Las capacidades del motor se referencian como PRDs del repo hermano `agent-tts` con prefijo AT (PRD-AT-02, PRD-AT-07, PRD-AT-08).

Este documento define doce propuestas de features (HT-01 a HT-12) para evolucionar `herdr-tts` desde una capa de notificación y consumo auditivo hacia una interfaz de voz completa para flotas de agentes en Herdr. La metodología combina dos insumos: (1) investigación del ecosistema — los proyectos comunitarios analizados en `docs/community-comparison.md` y las herramientas externas citadas en cada PRD como prior art, con atribución honesta de qué existe ya y qué aporta nuestra suite de distinto — y (2) el criterio rector del maintainer: **flow-first**, cada feature debe mejorar el flujo diario real de un operador de flota (Claude Code, OpenCode, Codex, Pi, Antigravity bajo WSL2, audio en el host Windows vía winhost, notificaciones móviles por ntfy); la diferenciación de mercado es un bonus, nunca el motivo.

Cada PRD sigue una plantilla uniforme con requisitos concretos y verificables, encaje explícito en la arquitectura actual (watcher con settle window de 5 s, gating ledger, adquisición de texto con forward `--agent`/`--session-id`, keymap declarativo JSON, dashboard TUI v3.1, paleta fzf, push ntfy, almacén de audio con retención) y dependencias cruzadas marcadas. Nada de lo aquí propuesto rompe el comportamiento existente: toda feature nace opt-in salvo indicación expresa.

## Índice

| ID | Feature | Prioridad | Esfuerzo | Dependencias |
|---|---|---|---|---|
| HT-01 | Push-to-Talk intercom (hablar al agente) | Alta | M-L | PRD-AT-02 (capa STT del motor) |
| HT-02 | Voces por agente (identidad vocal) | Alta | S-M | Ninguna (`--voice` ya existe por llamada) |
| HT-03 | Radio mode (triaje por voz) | Alta | M | PRD-AT-08 (cola prioritaria; fallback secuencial) |
| HT-04 | Control bidireccional desde el móvil | Alta | L | Comparte inyección de texto con HT-01 |
| HT-05 | Recordatorios escalados de atención | Media-Alta | M | Ninguna |
| HT-07 | Filtro semántico de importancia | Media-Alta | M | Opcional: cadena LLM ya existente del motor |
| HT-06 | Auto-snooze contextual (modo reunión) | Media | M | Extensión menor de winhost (solo WSL2) |
| HT-08 | Briefing matinal automático | Media | M | PRD-AT-07 (`--digest`) |
| HT-10 | Chain replay contextual | Media | S-M | PRD-AT-08 (`--play-chain`) |
| HT-12 | Watchers personalizados de texto | Media | M | Ninguna |
| HT-09 | Espacialización estéreo por pane | Baja-Media | S-M | Extensión mínima del motor (`--pan`) |
| HT-11 | Audición de voces en la paleta | Baja | S | HT-02 para asignación por chat |

---

## PRD-HT-01 — Push-to-Talk intercom (hablar al agente)
**Prioridad**: Alta · **Esfuerzo**: M-L

### Resumen ejecutivo
Permitir hablar directamente al agente del pane enfocado: un acorde abre el micrófono, la transcripción es 100% local (whisper.cpp vía la futura capa STT del motor `agent-tts`, PRD-AT-02), se muestra una confirmación visual breve del texto reconocido y este se inyecta como prompt en el pane, con Enter opcional. Cierra el ciclo de voz bidireccional y es el único ítem abierto del roadmap actual.

### Problema y flujo actual
Hoy la comunicación es asimétrica: el agente habla (TTS) y el usuario teclea. Durante la revisión de un diff, o cuando las manos están ocupadas escribiendo en otro chat, dictar una instrucción corta ("aplica la opción b", "corrige el test que falla") sería más rápido que teclearla. No existe ninguna vía de entrada de voz.

### Propuesta
Nuevo command id estable `ptt` en `keymap.json` (sin acorde obligatorio; se sugiere `prefix+c`, validado con `keymap check`). Dos modos de captura: hold (micrófono abierto mientras se mantiene) y toggle. Al soltar o pulsar de nuevo: transcripción local, overlay de confirmación con el texto reconocido (Enter acepta e inyecta, Esc cancela, re-intento sin salir del overlay), e inyección en el pane enfocado mediante el mecanismo de escritura de Herdr, con Enter final configurable.

### Historias de usuario (US-HT-01-1, ...)
- **US-HT-01-1** — Como operador de flota, quiero dictar una instrucción corta al agente enfocado sin teclear, para mantener el ritmo de revisión.
- **US-HT-01-2** — Como usuario de STT local, quiero ver el texto reconocido antes de inyectarlo, para detectar alucinaciones del modelo.
- **US-HT-01-3** — Como usuario en WSL2, quiero usar el micrófono del host Windows sin instalar nada adicional, como ya hago con la salida de audio.
- **US-HT-01-4** — Como usuario consciente de privacidad, quiero transcripción 100% local sin cloud, para no filtrar prompts fuera de la máquina.

### Requisitos funcionales (RF-HT-01-...)
- **RF-HT-01-1**: command id `ptt` registrado en el keymap declarativo, gestionado por `keymap init/adopt/check/emit/apply` como cualquier otro id, sin acorde por defecto.
- **RF-HT-01-2**: modos `hold` y `toggle` vía `TTS_PTT_MODE="hold|toggle"` (default `hold`).
- **RF-HT-01-3**: transcripción delegada a la capa STT del motor (`agent-tts`, PRD-AT-02); modelo configurable con `HERDR_TTS_STT_MODEL` y default conservador (whisper base/small cuantizado).
- **RF-HT-01-4**: overlay de confirmación: texto reconocido en una línea, Enter inyecta, Esc cancela, tecla de re-intento reabre la captura sin salir.
- **RF-HT-01-5**: inyección en el pane enfocado con `herdr pane send-keys` o el verbo equivalente de la API de Herdr; Enter final según `TTS_PTT_ENTER="ask|always|never"` (default `ask`).
- **RF-HT-01-6**: guard de seguridad: nunca inyecta texto vacío ni menor de 2 caracteres; muestra aviso en el overlay.
- **RF-HT-01-7**: cada dictado queda en `daemon.log` (duración, caracteres, pane_id); no se persisten ni audio ni transcripción.

### Requisitos no funcionales (RNF-HT-01-...)
- **RNF-HT-01-1**: latencia press-to-text <= 1.5 s (p50) para un dictado de 5 s en CPU moderno con el modelo default.
- **RNF-HT-01-2**: cero peticiones de red durante captura y transcripción (verificable con monitor de red).
- **RNF-HT-01-3**: el proceso de STT vive en el venv del motor; el host no añade dependencias nuevas.
- **RNF-HT-01-4**: un fallo del STT nunca bloquea el terminal: fail-open con mensaje accionable.

### Encaje en la arquitectura actual
El command id nuevo usa la infraestructura de keymap existente. La captura y transcripción se delegan al motor (PRD-AT-02), siguiendo el patrón thin host: el host solo orquesta (dispara captura, muestra confirmación, inyecta). La inyección usa la vía de escritura de Herdr, análoga a como la paleta usa `herdr pane focus`. En WSL2, el micrófono llega por la misma ruta de audio existente (WSLg/Pulse o winhost), igual que la salida.

### Prior art y diferenciación
`voice-to-code`, `VoxCode` y los scripts whisper.cpp + `tmux send-keys` demuestran el patrón dictado→inyección en tmux genérico; `opencode-voice` aporta STT whisper-cpp local. Nuestra diferenciación: integración nativa con el concepto de chat de Herdr (inyección al pane enfocado, no a un terminal arbitrario), confirmación visual previa a la inyección anti-alucinación, y convivencia con el gating y el keymap declarativo existentes. TalkToCursor (MCP server con tool `speak`, donde el modelo decide cuándo hablar) es el enfoque complementario inverso; se cita como referencia y no se implementa.

### Dependencias
- PRD-AT-02 (capa STT del motor `agent-tts`) — bloqueante.
- Confirmar el verbo exacto de inyección de teclas en la API de Herdr (`send-keys` o equivalente).

### Riesgos y mitigaciones
- Alucinaciones del STT → confirmación visual obligatoria (RF-HT-01-4) y guard de longitud mínima.
- Conflicto del acorde sugerido con Herdr core → `keymap check` lo detecta; el acorde es editable por el usuario.
- Latencia alta con modelos grandes → default conservador y guía de elección de modelo en la documentación.
- Micrófono no expuesto en WSL2 → documentar la ruta WSLg/winhost; fail-open con diagnóstico claro.

### Métricas de éxito
1. >= 5 dictados por día de trabajo real tras dos semanas de adopción (autoinforme del maintainer).
2. Tasa de cancelación en la confirmación < 15% (texto reconocido suficientemente usable).
3. Latencia p50 press-to-text <= 1.5 s medida en `daemon.log`.
4. Cero salidas de red durante el flujo completo.

### Fuera de alcance
Dictado continuo siempre activo, wake-word, STT en cloud, control de la UI de Herdr por voz, traducción automática del dictado.

### Open questions
¿Hold o toggle como default? ¿El overlay de confirmación vive en un popup de Herdr o como mensaje efímero en el pane? ¿Whisper.cpp como binario externo o binding dentro del motor? ¿Enter automático por defecto (`ask`) o nunca?

---

## PRD-HT-02 — Voces por agente (identidad vocal)
**Prioridad**: Alta · **Esfuerzo**: S-M

### Resumen ejecutivo
Mapeo persistente pane→voz o agent-type→voz, resuelto por el watcher al construir cada llamada al motor (`--voice` ya existe por llamada hoy). Identidad visible (glifo o inicial de voz en dashboard y paleta) y audible (prefijo hablado opcional "Claude: ..."). En flota, responde la pregunta "¿quién está hablando?" sin mirar la pantalla.

### Problema y flujo actual
Con 4-5 agentes en paralelo, todas las notificaciones usan `TTS_VOICE` global: no hay forma de saber qué agente habló sin levantar la vista y buscar el pane. El dashboard ordena por atención, pero el canal auditivo es anónimo.

### Propuesta
Fichero dedicado `~/.config/herdr-tts/voices.json` con reglas por `agent_type` y override por `pane_id`, precedencia pane > agent_type > voz global. El watcher resuelve la voz al invocar el motor en cada evento. Las superficies muestran la identidad (inicial o glifo de la voz en la línea del roster y en el preview de la paleta) y, opcionalmente, la voz anuncia su nombre corto antes del texto.

### Historias de usuario (US-HT-02-1, ...)
- **US-HT-02-1** — Como operador de flota, quiero que cada agente tenga su propia voz, para identificar al que habla sin mirar la pantalla.
- **US-HT-02-2** — Como operador con dos chats del mismo agent_type, quiero sobrescribir la voz de un pane concreto, para distinguirlos entre sí.
- **US-HT-02-3** — Como usuario del dashboard, quiero ver qué voz tiene cada chat, para gestionar la identidad sin recordar el fichero.
- **US-HT-02-4** — Como usuario que escucha de lejos, quiero un prefijo hablado opcional con el nombre del agente, para identificar incluso con voces parecidas.

### Requisitos funcionales (RF-HT-02-...)
- **RF-HT-02-1**: `voices.json` con esquema `{ "agent": { "<agent_type>": "<voz>" }, "pane": { "<pane_id>": "<voz>" }, "prefix": <bool> }`, validado al cargar; error de esquema = aviso accionable y fallback a voz global.
- **RF-HT-02-2**: precedencia resuelta pane_id > agent_type > `TTS_VOICE`; la resolución se aplica a eventos automáticos y a las lecturas on-demand del chat (play/TLDR).
- **RF-HT-02-3**: el watcher pasa `--voice <resuelta>` en cada llamada al motor; voz resuelta inválida = fail-open a voz global con una línea en `daemon.log`.
- **RF-HT-02-4**: dashboard: la línea del roster muestra la inicial o glifo de la voz del chat; paleta: igual en la cabecera del preview.
- **RF-HT-02-5**: prefijo hablado opcional (`prefix: true`, default off): sintetiza el nombre corto del agent_type antes del texto, con la propia voz del agente.
- **RF-HT-02-6**: gestión por CLI: `herdr-tts --voice-for agent <tipo> <voz>` y `herdr-tts --voice-for pane <pane_id> <voz>` (conecta con la asignación desde paleta de HT-11).
- **RF-HT-02-7**: rotación automática opt-in: agent_type sin voz asignada toma la siguiente voz de una paleta corta determinista de voces del proveedor activo (`"auto_assign": true`, default off).

### Requisitos no funcionales (RNF-HT-02-...)
- **RNF-HT-02-1**: coste por evento nulo: resolución en memoria del watcher con el fichero cacheado por mtime.
- **RNF-HT-02-2**: cero spawns adicionales en el sweep del daemon.
- **RNF-HT-02-3**: sin regresión de huella: el host sigue por debajo de ~2.5 MB RAM.

### Encaje en la arquitectura actual
El watcher ya construye la llamada al motor con `--agent`/`--session-id`; añadir `--voice` es un parámetro más de esa misma llamada. El roster del dashboard ya fusiona `herdr agent list` con los ledgers bajo la clave `pane_id`: la identidad vocal es un campo más de esa fusión. La paleta ya muestra cabecera por chat en el preview.

### Prior art y diferenciación
Ninguno de los proyectos comparados (`herdr-bleatr`, `herdr-tts` de Aktrov, `herdr-announcer`) parametriza identidad por agente: una sola voz global. `agentvoice` y `claude-code-tts` (Claude Code) encolan eventos con worker pool pero tampoco asignan identidad vocal por agente. Diferenciación: identidad persistente ligada al concepto de chat de Herdr y visible en las superficies interactivas existentes.

### Dependencias
Ninguna: el motor acepta `--voice` por llamada hoy mismo.

### Riesgos y mitigaciones
- Dos chats del mismo agent_type seguidos → override por pane (RF-HT-02-2) + prefijo hablado (RF-HT-02-5).
- Rotación automática que asigna voces de proveedores sin instalar (piper/kokoro) → solo rotar entre voces del proveedor activo.
- Deriva del fichero editado a mano → validación al cargar con aviso accionable, nunca crash del watcher.

### Métricas de éxito
1. Identificación correcta del agente que habla sin mirar la pantalla en >= 80% de los eventos (autoinforme tras dos semanas).
2. Cero regresión de RAM (< 2.5 MB) y cero spawns extra por sweep.
3. Al menos un override por `pane_id` en uso real tras dos semanas (valida el nivel fino del mapeo).

### Fuera de alcance
Clonación de voz, voces por proyecto/workspace, mezcla de proveedores por agente (cada evento usa el proveedor activo; solo cambia la voz).

### Open questions
¿Fichero dedicado `voices.json` o claves en `config.env`? ¿El prefijo hablado usa el `agent_type` tal cual o un alias configurable por agente?

---

## PRD-HT-03 — Radio mode (triaje por voz)
**Prioridad**: Alta · **Esfuerzo**: M

### Resumen ejecutivo
Una tecla (nuevo command id `radio` en el keymap) reproduce un boletín hablado y priorizado de todos los chats que piden atención: blocked primero, done más recientes después, cada uno con título de chat y 1-2 frases de resumen heurístico, terminando con "N chats más silenciosos omitidos". Convierte el retorno al PC en un triaje de oídos, sin abrir nada.

### Problema y flujo actual
Volver al PC tras 20 minutos con cinco chats en done/blocked obliga hoy a leer el dashboard o escuchar eventos uno a uno. El roster del dashboard ya ordena needs-attention-first, pero exige mirar la pantalla; el canal auditivo solo entrega eventos sueltos, sin visión de conjunto ni prioridad.

### Propuesta
Command id `radio` sin acorde por defecto (sugerido y validado con `keymap check`). Al pulsarlo: computa el roster con la misma fusión del dashboard (`herdr agent list` + ledgers por `pane_id`), ordena blocked primero y done por recencia, sintetiza por chat "título + TL;DR de 1-2 frases" (heurística offline existente del motor) y lo encola en la cola prioritaria del motor (PRD-AT-08) o en secuencial como fallback. Cierra con el recuento de chats omitidos. Interrumpible con stop; re-pulsar re-evalúa con datos frescos.

### Historias de usuario (US-HT-03-1, ...)
- **US-HT-03-1** — Como operador que vuelve al PC, quiero un boletín hablado de todo lo que pide atención, para decidir por dónde entrar sin leer nada.
- **US-HT-03-2** — Como operador con agentes bloqueados, quiero que los blocked suenen primero, para desatascar antes lo que detiene el trabajo.
- **US-HT-03-3** — Como operador interrumpido a mitad del boletín, quiero cortarlo con stop o reiniciarlo, para que nunca sea una carga.
- **US-HT-03-4** — Como operador de flota grande, quiero saber cuántos chats quedaron fuera del boletín, para confiar en que no se oculta nada importante.

### Requisitos funcionales (RF-HT-03-...)
- **RF-HT-03-1**: command id `radio` en `keymap.json`, tratado por `keymap check/emit/apply` como cualquier otro id, sin acorde por defecto.
- **RF-HT-03-2**: selección: chats en `blocked` primero, luego `done` ordenados por evento/audio más reciente; excluye muted y snoozed; límite configurable de chats por boletín (`TTS_RADIO_MAX_CHATS`, default 6).
- **RF-HT-03-3**: por chat, frase de cabecera con el título truncado a 40 caracteres (regla del dashboard) + 1-2 frases de resumen heurístico (misma vía que `--tldr`).
- **RF-HT-03-4**: cierre con frase final: número de chats omitidos (working/idle) con la fórmula "N chats más silenciosos omitidos".
- **RF-HT-03-5**: reproducción vía la cola prioritaria del motor (PRD-AT-08) con los blocked en prioridad alta; sin ella, encadenado secuencial bajo el mutex existente.
- **RF-HT-03-6**: stop/s corta el boletín; nueva pulsación durante la reproducción descarta el boletín en curso y re-triaja con datos frescos.
- **RF-HT-03-7**: debounce propio del command: no re-dispara en menos de 10 s.
- **RF-HT-03-8**: si HT-02 está activo, la cabecera incluye el nombre del agente (identidad hablada).

### Requisitos no funcionales (RNF-HT-03-...)
- **RNF-HT-03-1**: computar el boletín tarda < 2 s con 10 chats (reuso del roster cacheado en la cadencia del dashboard).
- **RNF-HT-03-2**: primer audio del boletín en < 1.5 s (streaming del motor).
- **RNF-HT-03-3**: el estado "radio en curso" es visible en dashboard y línea de motor.

### Encaje en la arquitectura actual
Reutiliza sin cambios de fuente la fusión por chat del dashboard v3 (misma clave `pane_id`, mismo orden needs-attention-first ya definido). El TL;DR por chat reusa la heurística offline del motor. La cola prioritaria es PRD-AT-08; sin ella, el mutex secuencial actual ya garantiza no-solapamiento.

### Prior art y diferenciación
Ninguno de los comparados ofrece triaje hablado multi-chat: `agentvoice` y `claude-code-tts` encolan eventos sueltos con worker pool anti-solapamiento, sin noción de boletín ni prioridad por atención. Diferenciación: boletín priorizado por estado de atención, exclusivo del contexto de flota de Herdr.

### Dependencias
- PRD-AT-08 (cola con prioridades del motor) — sin ella, fallback a reproducción secuencial (comportamiento actual del mutex).

### Riesgos y mitigaciones
- Boletines demasiado largos → límite de chats y de frases por ítem (RF-HT-03-2/3).
- TL;DR heurístico poco informativo en algunos agentes → open question sobre clasificación LLM opcional reutilizando la cadena del motor.
- Saturación por pulsación repetida → debounce del command (RF-HT-03-7).

### Métricas de éxito
1. Tiempo desde pulsar `radio` hasta decidir a qué chat entrar < 60 s con flota de 5+ chats.
2. Uso >= 1 vez por día en semanas de flota activa.
3. Tasa de abandono del boletín antes del final < 30%.

### Fuera de alcance
Boletín en texto/pantalla, envío del boletín a ntfy (eso corresponde a HT-08), orden personalizado por el usuario.

### Open questions
¿Incluir chats en `working` con evento muy reciente? ¿Cabecera con agent-type siempre que HT-02 exista, o solo si hay ambigüedad? ¿Longitud del ítem ajustable?

---

## PRD-HT-04 — Control bidireccional desde el móvil
**Prioridad**: Alta · **Esfuerzo**: L

### Resumen ejecutivo
Convertir el push ntfy (hoy de solo lectura) en control remoto: acciones de respuesta en la propia notificación (ntfy `Actions` con postback HTTP a un endpoint local del daemon, autenticado con token en el topic) y un listener de replies al topic. Lista blanca estricta (continuar/detener) más texto libre que se inyecta como prompt en el pane del chat notificado. Jamás ejecuta comandos arbitrarios.

### Problema y flujo actual
Si un agente pregunta algo y el usuario no está en el PC, el flujo se detiene. Hoy el push permite escuchar el MP3 y copiar `herdr agent focus <pane_id>` al portapapeles, pero responder exige volver al teclado: el loop atención→decisión→respuesta está roto en el paso final.

### Propuesta
Tres piezas: (a) endpoint HTTP en el daemon `POST /herdr-tts/reply` con token aleatorio y bind local/LAN configurable; (b) notificaciones con `Actions` http ("Continuar", "Detener") que postean al endpoint con el token y el `pane_id` de origen; (c) suscripción al topic (stream JSON de ntfy) para respuestas de texto libre: confirmaciones cortas inyectan la respuesta; "stop" detiene reproducción/evento; el texto libre se inyecta como prompt en el pane del chat notificado.

### Historias de usuario (US-HT-04-1, ...)
- **US-HT-04-1** — Como operador fuera del PC, quiero confirmar o detener a un agente desde la propia notificación, para no cortar mi desplazamiento.
- **US-HT-04-2** — Como operador en el móvil, quiero escribir una respuesta corta y que llegue como prompt al chat notificado, para desbloquear al agente en remoto.
- **US-HT-04-3** — Como operador preocupado por seguridad, quiero token obligatorio, rate-limit y lista blanca de acciones, para que el móvil nunca sea una puerta a comandos.
- **US-HT-04-4** — Como operador multitarea, quiero que cada acción remota quede registrada, para auditar qué se hizo en mi nombre.

### Requisitos funcionales (RF-HT-04-...)
- **RF-HT-04-1**: listener HTTP en el daemon con `TTS_REMOTE_CONTROL="off|on"` (default off), `TTS_REMOTE_BIND` (default `127.0.0.1`; LAN como opt-in explícito) y puerto documentado.
- **RF-HT-04-2**: token aleatorio generado en la primera activación (`TTS_REMOTE_TOKEN_FILE`), incrustado en las Actions y exigido en el listener; token inválido = 401 + línea en `daemon.log`.
- **RF-HT-04-3**: Actions en la notificación: "Continuar" y "Detener" (postback HTTP); "Detener" ejecuta la misma vía que `--stop` (audio local y evento en curso).
- **RF-HT-04-4**: los mensajes de texto al topic solo se procesan si referencian un `pane_id` de una notificación reciente (TTL configurable, default 10 min) o si existe exactamente un chat pendiente.
- **RF-HT-04-5**: semánticas de texto: "si"/"sí"/"continue"/"ok" = confirmación inyectada; "stop" = detener; texto libre = prompt inyectado en el pane de origen con la misma regla de Enter que HT-01.
- **RF-HT-04-6**: lista blanca: solo el catálogo anterior es ejecutable; el texto libre jamás se interpreta como comando de shell.
- **RF-HT-04-7**: rate-limit de 10 peticiones/minuto por origen; exceso = 429 y silencio temporal.
- **RF-HT-04-8**: auditoría en `daemon.log` por acción remota: timestamp, acción, `pane_id`, primeros 40 caracteres del texto; nunca audio.
- **RF-HT-04-9**: la función es aditiva: con `off`, el push se comporta exactamente como hoy.

### Requisitos no funcionales (RNF-HT-04-...)
- **RNF-HT-04-1**: el listener vive dentro del daemon existente: cero procesos nuevos.
- **RNF-HT-04-2**: latencia acción→pane < 1 s en LAN.
- **RNF-HT-04-3**: un listener caído nunca afecta al bucle del daemon (fail-open, igual que la purga de retención).
- **RNF-HT-04-4**: superficie mínima: sin ejecución de comandos, sin lectura arbitraria de estado, sin escritura de configuración.

### Encaje en la arquitectura actual
`send_ntfy_push()` ya construye las cabeceras del push: las Actions son una extensión del mismo builder. "Detener" reutiliza `--stop`; la inyección de texto comparte mecanismo con HT-01; el listener se integra al bucle del daemon con el patrón best-effort ya usado por la purga horaria del almacén de audio.

### Prior art y diferenciación
`herdr-announcer` expone IPC pero local; ningún comparado ofrece respuesta desde el móvil. Los puentes de llamada (`herdr-call` en la comparativa del README) cubren voz humana pero no acciones estructuradas. Diferenciación: cerrar el loop completo atención→respuesta sin exponer un shell remoto, sobre la infraestructura ntfy ya existente.

### Dependencias
- Comparte el mecanismo de inyección de texto con HT-01 (implementar tras o junto a él).
- Verificar soporte de `Actions` http en las apps ntfy de Android e iOS utilizadas.

### Riesgos y mitigaciones
- Topic público en ntfy.sh → token obligatorio + TTL corto de referencia + opción de servidor self-hosted (`NTFY_SERVER` ya existe).
- Host inalcanzable fuera de casa → alcance documentado: LAN o Tailscale; nunca exponer a internet abierto.
- Abuso del texto libre → solo inyección en pane (RF-HT-04-6), rate-limit (RF-HT-04-7) y auditoría (RF-HT-04-8).

### Métricas de éxito
1. > 3 respuestas remotas con éxito por semana tras un mes.
2. Cero acciones ejecutadas con token inválido.
3. Latencia p50 acción→pane < 1 s en LAN.

### Fuera de alcance
App nativa, ejecución de comandos arbitrarios, streaming de audio bidireccional, control desde fuera de LAN/Tailscale sin puerta de enlace explícita.

### Open questions
¿Las Actions http bastan en iOS o se necesitan view-actions? ¿Topic dedicado de control separado del topic de notificación? ¿TTL por tipo de evento (blocked vs done)?

---

## PRD-HT-05 — Recordatorios escalados de atención
**Prioridad**: Media-Alta · **Esfuerzo**: M

### Resumen ejecutivo
Si un chat permanece en done/blocked sin foco ni respuesta durante N minutos, el sistema re-recorda con backoff exponencial (2m → 5m → 15m), hasta M recordatorios (default 3); el último escala a push ntfy prioritario. Estado en el gating ledger, limpiado al enfocar o responder, silenciable por chat con el mute/snooze existente.

### Problema y flujo actual
El aviso suena una vez. Si el usuario está concentrado en otra cosa (head-down), el evento se pierde y el agente espera indefinidamente: ni voz ni push volverán a señalarlo. Hoy no existe ningún mecanismo de re-memoria.

### Propuesta
Extensión del gating ledger con estado por pane: `first_attention_ts`, `reminder_count`, `next_reminder_ts`. El sweep del daemon (~4 s) comprueba vencimientos con el agent list ya en mano. Cada recordatorio es una frase corta ("Recordatorio: <título> sigue esperando"); el último añade cabecera de prioridad alta al push ntfy. El estado se limpia al enfocar el pane, responder (HT-01/HT-04) o cuando el agente cambia de estado.

### Historias de usuario (US-HT-05-1, ...)
- **US-HT-05-1** — Como operador concentrado en otra tarea, quiero que un agente bloqueado insista de forma escalonada, para no perderlo de vista sin saturarme.
- **US-HT-05-2** — Como operador que se aleja del PC, quiero que el último recordatorio llegue como push prioritario al móvil, para enterarme aunque no oiga la voz local.
- **US-HT-05-3** — Como operador, quiero que enfocar o responder al chat corte los recordatorios, para que no suene lo ya atendido.
- **US-HT-05-4** — Como operador en reunión, quiero silenciar los recordatorios de un chat con el snooze/mute existente, sin aprender controles nuevos.

### Requisitos funcionales (RF-HT-05-...)
- **RF-HT-05-1**: config: `TTS_REMINDERS="off|on"` (default off), `TTS_REMINDER_START_MIN` (default 5), `TTS_REMINDER_BACKOFF="2,5,15"` (minutos), `TTS_REMINDER_MAX` (default 3).
- **RF-HT-05-2**: estado por pane en el fichero JSON del gating ledger (claves nuevas junto a snooze/mute/debounce, patrón incremental existente); sobrevive reinicios del daemon.
- **RF-HT-05-3**: reset del estado al: foco sostenido del pane (> 2 s), respuesta inyectada (HT-01/HT-04), cambio de estado del agente (vuelve a working), o mute/snooze del chat.
- **RF-HT-05-4**: canal del recordatorio: si las puertas locales lo permiten, voz corta diferenciada del evento original (no relee el turno); si no, únicamente push ntfy.
- **RF-HT-05-5**: el último recordatorio (índice M) envía push con cabecera de prioridad alta de ntfy.
- **RF-HT-05-6**: mute y snooze del chat suprimen también los recordatorios, sin excepciones.
- **RF-HT-05-7**: dashboard: cuenta atrás del próximo recordatorio en la línea del chat, junto al snooze.

### Requisitos no funcionales (RNF-HT-05-...)
- **RNF-HT-05-1**: coste del sweep sin cambios: comprobación O(chats) en memoria, sin spawns extra salvo el propio aviso.
- **RNF-HT-05-2**: los recordatorios atraviesan el debounce anti-spam global (nunca dos avisos solapados del mismo pane).
- **RNF-HT-05-3**: pérdida del estado en `/tmp` tras reinicio de la máquina = aceptable (re-armado en el siguiente evento).

### Encaje en la arquitectura actual
El sweep ya visita todos los panes cada ~4 s con el agent list cargado: comprobar vencimientos es una comparación de timestamps en memoria. El estado vive en el mismo fichero del gating ledger (`herdr-tts-snooze.json`) con claves nuevas, el mismo patrón usado al añadir debounce. El push prioritario reusa `send_ntfy_push()` con una cabecera más.

### Prior art y diferenciación
`opencode-smart-voice-notify` implementa recordatorios con backoff exponencial para permisos/errores/idle: prior art directo y reconocido como tal. Diferenciación: estado integrado en el ledger por `pane_id` del ecosistema Herdr, reset por foco sostenido o respuesta inyectada, y escalado final a push prioritario reutilizando el fan-out existente.

### Dependencias
Ninguna dura. La integración con HT-04 (respuesta remota como reset) es opcional y posterior.

### Riesgos y mitigaciones
- Fatiga de notificación → default off, máximo M, frases cortas, silenciado por chat.
- Falsos resets al navegar por los panes sin intención → foco sostenido > 2 s como criterio.
- Acumulación de estado en flotas grandes → purga de entradas de panes cerrados en la poda existente del daemon.

### Métricas de éxito
1. Mediana bloqueo→respuesta con recordatorios activos menor que la baseline medida sin ellos (medir baseline primero).
2. < 1% de chats con recordatorios activos después de atendidos (limpieza correcta).
3. Cero recordatorios sobre chats con mute/snooze activo.

### Fuera de alcance
Llamadas telefónicas, integración con calendario, recordatorios programados por hora del día (eso es HT-08).

### Open questions
¿El aviso local del recordatorio usa frase prefijada o tono/voz distinta? ¿M por chat o global por sesión? ¿Contar el foco sostenido en la paleta como atención?

---

## PRD-HT-07 — Filtro semántico de importancia
**Prioridad**: Media-Alta · **Esfuerzo**: M

### Resumen ejecutivo
Compuerta de contenido antes de sintetizar: heurística offline (longitud mínima, presencia de pregunta al usuario, keywords de decisión/bloqueo, diff de estado done→blocked) y opcionalmente clasificación LLM reutilizando la cadena existente del motor (`claude -p` → `codex exec` → `ollama`). Config `TTS_IMPORTANCE="all|smart|minimal"`: los turnos triviales del agente activo dejan de consumir síntesis y atención.

### Problema y flujo actual
`scope: focused` elimina el ruido de otros panes, pero el agente enfocado lee TODO turno: los triviales ("listo", "hecho", continuaciones de trabajo) también disparan síntesis completa, push y robo de atención. El settle window filtra parpadeos de estado, no contenido trivial de un turno real.

### Propuesta
Capa de gate de contenido insertada tras la adquisición de texto y antes del fan-out. Clasifica el turno extraído (conector o scrollback) en relevante/trivial. `smart` (heurística al activar), `minimal` (solo blocked y preguntas directas), `all` (comportamiento actual). Opcional `TTS_IMPORTANCE_LLM="on"` delega la clasificación binaria a la cadena LLM del motor con timeout corto y fallback a heurística. Los turnos filtrados no sintetizan ni empujan push, pero quedan en el historial marcados como omitidos y son reproducibles on-demand.

### Historias de usuario (US-HT-07-1, ...)
- **US-HT-07-1** — Como operador con el agente activo, quiero oír solo los turnos que requieren mi criterio, para reservar la atención.
- **US-HT-07-2** — Como operador, quiero que un turno trivial omitido siga siendo reproducible a mano, para no perder información.
- **US-HT-07-3** — Como operador con cadena LLM disponible, quiero clasificación semántica real como opción, para ir más lejos de la heurística.
- **US-HT-07-4** — Como operador, quiero ver cuántos turnos se han omitido hoy, para confiar en el filtro y ajustarlo.

### Requisitos funcionales (RF-HT-07-...)
- **RF-HT-07-1**: config: `TTS_IMPORTANCE="all|smart|minimal"` (default `all` = cero cambio de comportamiento), `TTS_IMPORTANCE_MIN_CHARS` (default 60), `TTS_IMPORTANCE_LLM="off|on"`.
- **RF-HT-07-2**: heurística `smart`: relevante si el turno contiene pregunta al usuario, keywords configurables de decisión/bloqueo/riesgo (lista por defecto ampliable en config), longitud >= mínimo, o el evento fue un diff done→blocked.
- **RF-HT-07-3**: `minimal`: solo eventos blocked y turnos con pregunta directa al usuario.
- **RF-HT-07-4**: los turnos filtrados no generan síntesis local ni push; sí quedan en `history.log` con marca de omitido (columna opcional adicional, sin reescribir filas existentes) y son reproducibles con play/TLDR on-demand.
- **RF-HT-07-5**: el filtro aplica solo a la voz automática; play, TLDR, radio y briefing on-demand siempre leen.
- **RF-HT-07-6**: clasificación LLM: prompt corto por stdin (sin superficie de inyección de comandos), timeout 3 s, fallback heurístico ante fallo o cadena ausente.
- **RF-HT-07-7**: contador de turnos omitidos del día visible en `--status` y en la línea global del dashboard.

### Requisitos no funcionales (RNF-HT-07-...)
- **RNF-HT-07-1**: la heurística añade < 50 ms por turno (regex pura o una pasada del intérprete del venv ya usado).
- **RNF-HT-07-2**: el modo LLM nunca retrasa el primer audio más de 300 ms sobre el baseline: si el timeout vence, sale la decisión heurística.
- **RNF-HT-07-3**: cero dependencias nuevas en modo heurístico.

### Encaje en la arquitectura actual
Se inserta en el punto exacto donde hoy el settle window ya decidió que el evento procede: tras adquisición de texto, antes del fan-out (síntesis/push/historial). El historial ya tolera columnas opcionales sin reescritura (patrón de campos 4/5/6). La cadena LLM es la ya existente en el motor (`--llm-summary`), reutilizada para una tarea más barata: clasificación binaria en lugar de resumen.

### Prior art y diferenciación
`opencode-voice` implementa lectura condicional por longitud: prior art directo, pero basado solo en longitud. Aquí la clasificación es semántica (preguntas, decisiones, bloqueos) con opción LLM. `herdr-bleatr` delega el resumen entero al LLM en cada evento (coste y latencia por evento); aquí la heurística es offline y gratuita, y el LLM es opcional.

### Dependencias
Ninguna para la heurística. La opción LLM reutiliza la cadena ya existente del motor.

### Riesgos y mitigaciones
- Falsos negativos (turno importante filtrado) → `smart` conservador, lista de keywords auditable, revisión semanal de omitidos al inicio, default `all`.
- Opacidad del filtro → marca en historial y contador visible (RF-HT-07-4/7).
- Deriva del prompt LLM → prompt versionado en el repo, salida acotada a clasificación binaria.

### Métricas de éxito
1. 30-60% de turnos auto omitidos en modo `smart` (rango esperado de triviales).
2. Falsos negativos reclamados por el usuario < 2% de los omitidos durante el primer mes.
3. Ahorro medible de llamadas de síntesis (contador de omitidos vs eventos totales).

### Fuera de alcance
Resumir el turno filtrado (eso es TL;DR), aprendizaje personalizado del clasificador, filtrado de eventos on-demand.

### Open questions
¿Umbral por agente (verbosidad distinta entre Claude y Codex)? ¿Debe el filtro aplicar también al push ntfy en `smart` (propuesta actual: sí)? ¿Keywords por defecto en español, inglés o ambas?

---

## PRD-HT-06 — Auto-snooze contextual (modo reunión)
**Prioridad**: Media · **Esfuerzo**: M

### Resumen ejecutivo
Detectar micrófono en captura activa (PipeWire/PulseAudio en Linux; en WSL2, estado del dispositivo reportado por winhost) para activar automáticamente un global-snooze de voz local etiquetado como "reunión" más un modo "solo móvil" (los eventos siguen llegando a ntfy sin voz local). Al liberar el micrófono, se restaura exactamente el estado previo. `TTS_MEETING_MODE="auto|off"` e histéresis anti-flicker de 5 s.

### Problema y flujo actual
En una llamada, la voz local molesta (se mezcla con la reunión) y el push del móvil vibra igual que siempre. Hoy el usuario debe acordarse de `prefix+Z` (snooze global) y de deshacerlo después; olvidar el segundo paso deja la flota muda durante horas, un fallo clásico de los snoozes manuales.

### Propuesta
Monitor de captura activa consultado en el sweep con cadencia relajada. Detección sostenida durante la histéresis activa un snooze global especial, separado del manual y etiquetado "reunión", más el modo solo-móvil: sin voz local, push intacto. Al cesar la captura, restauración exacta del estado previo. La voz on-demand del usuario nunca se bloquea: solo la automática.

### Historias de usuario (US-HT-06-1, ...)
- **US-HT-06-1** — Como operador con llamadas frecuentes, quiero que la voz local se silencie sola durante la reunión y vuelva sola después, sin tocar nada.
- **US-HT-06-2** — Como operador en reunión, quiero que los eventos importantes sigan llegando al móvil, para no perder la flota de vista.
- **US-HT-06-3** — Como operador que dicta con HT-01, quiero que mi propio uso del micrófono no dispare el modo reunión.
- **US-HT-06-4** — Como operador, quiero distinguir en el dashboard un snooze automático de uno manual, para no confundir estados.

### Requisitos funcionales (RF-HT-06-...)
- **RF-HT-06-1**: config: `TTS_MEETING_MODE="auto|off"` (default off), `TTS_MEETING_HYSTERESIS_S` (default 5), `TTS_MEETING_MOBILE_ONLY="1"` (default: push ntfy sigue activo).
- **RF-HT-06-2**: detección en Linux nativo: consulta de source-outputs de captura activa (pactl/PipeWire) con allowlist/denylist de aplicaciones; la captura del STT propio (HT-01) queda exenta mientras dicta.
- **RF-HT-06-3**: detección en WSL2: winhost reporta el estado del micrófono del host Windows (extensión mínima del protocolo); ausencia de señal = no-reunión (fail-open).
- **RF-HT-06-4**: estado "reunión" separado del snooze global manual: el dashboard muestra la etiqueta de auto con indicador propio; el snooze manual del usuario nunca se pisa ni se auto-deshace.
- **RF-HT-06-5**: restauración exacta al cesar la captura (tras histéresis): la voz local vuelve al estado on/off que tenía antes.
- **RF-HT-06-6**: antiflicker: transiciones más rápidas que la histéresis se ignoran.
- **RF-HT-06-7**: override manual: las lecturas on-demand (play, TLDR, radio) funcionan durante la reunión; solo la voz automática se suprime.

### Requisitos no funcionales (RNF-HT-06-...)
- **RNF-HT-06-1**: chequeo de micrófono integrado al sweep con cadencia cada N ticks (no cada tick), sin spawns adicionales por tick.
- **RNF-HT-06-2**: detección fallida = el modo auto se desactiva solo con una línea en `daemon.log` (fail-open); la voz vuelve.
- **RNF-HT-06-3**: privacidad: nunca se graba audio; solo se consulta el estado del servidor de sonido.

### Encaje en la arquitectura actual
El daemon ya aplica y quita global-snooze (`cycle_snooze` con scope global) y ya tiene bucle de sweep: el modo reunión es un segundo origen del mismo efecto con etiqueta propia en el ledger. winhost ya mantiene un canal TCP vivo con el host Windows para el transporte de audio: añadir un mensaje de estado de captura es una extensión mínima del protocolo existente.

### Prior art y diferenciación
El ducking por roles de stream de PipeWire/WirePlumber y la detección estándar de micrófono en captura son la base técnica conocida, citada como tal. Ningún proyecto del ecosistema aplica esto al auto-mute de notificaciones de agentes. Diferenciación: el puente WSL→Windows vía winhost, exclusivo de la suite, y la separación voz local/móvil durante la reunión.

### Dependencias
Ninguna dura. La detección en WSL2 requiere la extensión menor de winhost (motor); sin ella, el modo auto solo opera en Linux nativo/WSLg.

### Riesgos y mitigaciones
- Falsos positivos (apps que mantienen el micrófono abierto) → denylist configurable + opt-in del modo.
- Coste de polling de pactl → cadencia N ticks y caché del resultado.
- Oscilación rápida reunión/silencio → histéresis de 5 s (RF-HT-06-6).

### Métricas de éxito
1. Cero voz local durante reuniones reales en dos semanas de uso (autoinforme).
2. Cero voz perdida por falso negativo grave (reunión no detectada) tras ajustar la denylist.
3. Menos de 10 activaciones/desactivaciones por día (sin flicker).

### Fuera de alcance
Ducking del volumen de la reunión, transcripción de la reunión, detección por calendario, supresión del push móvil.

### Open questions
¿Criterio de exención del STT propio: pid del proceso, puerto, o ventana temporal de dictado? ¿Winhost reporta el estado o WSL lo consulta con un powershell ligero?

---

## PRD-HT-08 — Briefing matinal automático
**Prioridad**: Media · **Esfuerzo**: M

### Resumen ejecutivo
Al primer evento de actividad del día (o con `herdr-tts --briefing`), compilar un digest hablado de la actividad desde el último briefing: chats tocados, turnos completados, bloqueos vivos. Audio-resumen generado con `agent-tts --digest` (PRD-AT-07), entregado por voz local o como episodio ntfy/podcast. La "portada" hablada del periodo en que el usuario estuvo ausente.

### Problema y flujo actual
Agentes trabajando de noche o por la tarde: al volver, no se sabe qué pasó sin leer cinco chats. El dashboard muestra el presente (quién está done/blocked ahora) pero no la narrativa del periodo ausente; radio mode (HT-03) triaja el momento, no la historia.

### Propuesta
Timestamp `last_briefing_ts` persistido en el directorio de estado. Trigger: primer evento del pipeline del día (cualquier evento que pase el settle window) o invocación manual. Compilación desde `history.log` agrupado por chat + `herdr agent list`: chats con actividad, turnos completados, bloqueos aún vivos. El guion se sintetiza con `--digest` del motor; entrega configurable por voz local, push ntfy con MP3 o episodio del podcast existente.

### Historias de usuario (US-HT-08-1, ...)
- **US-HT-08-1** — Como operador que deja agentes trabajando de noche, quiero un resumen hablado al volver, para saber qué pasó sin leer nada.
- **US-HT-08-2** — Como operador que llega con el móvil, quiero el briefing también como push/podcast, para escucharlo de camino.
- **US-HT-08-3** — Como operador, quiero que el briefing sea corto y accionable (por dónde empezar), no una lectura completa de turnos.
- **US-HT-08-4** — Como operador, quiero poder pedirlo en cualquier momento, no solo por la mañana.

### Requisitos funcionales (RF-HT-08-...)
- **RF-HT-08-1**: comando manual `herdr-tts --briefing`; modo automático `TTS_BRIEFING="off|auto"` (default off) que dispara en el primer evento del pipeline del día.
- **RF-HT-08-2**: ventana del digest: desde `last_briefing_ts` hasta ahora; primera ejecución = últimas 12 h.
- **RF-HT-08-3**: contenido del guion: número de chats con actividad, top de chats por turnos completados, bloqueos vivos al momento del briefing, y sugerencia de por dónde empezar (primer blocked más antiguo).
- **RF-HT-08-4**: síntesis con `agent-tts --digest` (PRD-AT-07): el motor recibe sesiones/pane_ids y la ventana, y devuelve el audio-resumen; fallback documentado: concatenación local de TL;DRs por chat con aviso en el log.
- **RF-HT-08-5**: entrega: `TTS_BRIEFING_DELIVERY="voice|push|podcast"` (default `voice`); push usa el MP3 inline existente; podcast añade episodio al feed `:8844` existente.
- **RF-HT-08-6**: el briefing se encola tras el mutex como cualquier audio y es interrumpible con stop.
- **RF-HT-08-7**: dashboard y `--status` muestran "último briefing: hace Xh".

### Requisitos no funcionales (RNF-HT-08-...)
- **RNF-HT-08-1**: compilación del guion < 2 s: reuso de la pasada única de agrupación por chat que ya hace el dashboard (mismo intérprete del venv).
- **RNF-HT-08-2**: duración del audio <= 90 s.
- **RNF-HT-08-3**: el modo auto nunca dispara sin un evento real del pipeline (nada de briefings en máquinas idle).
- **RNF-HT-08-4**: el texto del briefing pasa por el redactor de secretos del motor antes de publicar a ntfy/podcast (ya existe).

### Encaje en la arquitectura actual
`history.log` por chat con duraciones y timestamps ya alimenta el dashboard: la compilación del guion es una variante de esa pasada. El push con MP3 inline y el podcast RSS ya existen como fan-out. Las piezas nuevas son solo el trigger, el guion y la llamada `--digest`.

### Prior art y diferenciación
No existe briefing hablado de flota en los comparados; los digests tipo "standup" de otras herramientas son texto plano. Diferenciación: portada hablada del periodo ausente entregada por los canales ya existentes (voz, ntfy, podcast), con narrativa priorizada hacia la acción.

### Dependencias
- PRD-AT-07 (`agent-tts --digest`) para el audio-resumen natural; sin ella, fallback a concatenación de TL;DRs.

### Riesgos y mitigaciones
- Historial vacío (retención 0) → requisito previo documentado: retención >= 1 día o conector accesible para TL;DR on-the-fly.
- Briefing largo → cap de 90 s (RNF-HT-08-2) y jerarquía fija del guion.
- Activación temprana con evento trivial → open question sobre el criterio de "primer evento" y sinergia con HT-07.

### Métricas de éxito
1. >= 3 briefings por semana de uso real.
2. Tiempo desde la llegada hasta decidir por dónde entrar < 2 min con briefing.
3. Duración media del briefing entre 45 y 90 s.

### Fuera de alcance
Briefing programado por hora fija, envío por email, resumen de diffs de código, multi-idioma del guion.

### Open questions
¿Primer evento del día = primer evento del pipeline o primer input humano? ¿Incluir consumo de tokens/coste por chat? ¿Debería HT-07 (filtro) alimentar el guion con solo lo relevante?

---

## PRD-HT-10 — Chain replay contextual
**Prioridad**: Media · **Esfuerzo**: S-M

### Resumen ejecutivo
Acción "reproducir contexto" en paleta y dashboard que encadena los últimos N turnos del chat (default 3) desde el almacén de audio, en orden cronológico, usando `--play-chain` del motor (PRD-AT-08). Si la retención purgó algún audio, re-síntesis desde el transcript del conector. Para responder bien a un bloqueo, a veces hay que oír qué dijo el agente antes del último turno.

### Problema y flujo actual
`ctrl-r` en la paleta repone un único turno. Cuando el agente se bloquea con "¿continúo con el plan B?", la decisión depende de lo acordado dos turnos atrás: hoy hay que leer el transcript o reproducir turnos de uno en uno, rompiendo el flujo auditivo.

### Propuesta
Acción en dos superficies: en la paleta, tecla sobre un turno que reproduce los N turnos previos de ese chat terminando en el seleccionado; en el dashboard, tecla sobre el chat con los últimos N. Las rutas salen del campo de audio almacenado de `history.log`; los huecos por retención se re-sintetizan desde el conector con la misma vía `--agent`/`--session-id`; todo pasa por el mutex (un solo reproductor) con pausa/seek/stop operativos sobre la cadena completa.

### Historias de usuario (US-HT-10-1, ...)
- **US-HT-10-1** — Como operador ante un blocked, quiero escuchar los últimos turnos seguidos, para decidir con el contexto completo.
- **US-HT-10-2** — Como operador con retención activa, quiero que los turnos purgados se re-sinteticen al vuelo, para que la cadena nunca quede rota en silencio.
- **US-HT-10-3** — Como operador, quiero pausar y rebobinar dentro de la cadena como en un audio individual, para repasar el punto exacto.
- **US-HT-10-4** — Como operador, quiero ver por qué turno de la cadena va la reproducción, para saber dónde estoy.

### Requisitos funcionales (RF-HT-10-...)
- **RF-HT-10-1**: acción en paleta (tecla `ctrl-x` sobre un turno): reproduce los últimos N turnos de ese chat terminando en el seleccionado; `esc` interrumpe.
- **RF-HT-10-2**: acción en dashboard (tecla `x` sobre un chat): reproduce los últimos N turnos del chat seleccionado.
- **RF-HT-10-3**: N configurable: `TTS_CHAIN_TURNS` (default 3, máximo 10).
- **RF-HT-10-4**: resolución de audio: campo de ruta almacenada de `history.log` cuando el fichero existe; turnos sin audio almacenado → re-síntesis desde el transcript del conector, con marca visual "re-sintetizado"; turnos ni almacenados ni accesibles → omisión con aviso hablado breve.
- **RF-HT-10-5**: reproducción vía `--play-chain` del motor (PRD-AT-08); sin ella, secuencia de `--play-file` gestionada por el host bajo el mutex.
- **RF-HT-10-6**: pausa, seek y stop del motor funcionan sobre la cadena igual que sobre un audio individual.
- **RF-HT-10-7**: indicación visual durante la cadena: la línea de motor del dashboard muestra "contexto k/N".

### Requisitos no funcionales (RNF-HT-10-...)
- **RNF-HT-10-1**: inicio de la cadena < 1.5 s si todo el audio existe (sin re-síntesis).
- **RNF-HT-10-2**: la re-síntesis de huecos no bloquea la reproducción de los ya disponibles (encola en orden).
- **RNF-HT-10-3**: solo lectura del almacén existente: la feature no alterra la política de retención.

### Encaje en la arquitectura actual
`history.log` ya persiste la ruta del almacén por turno (sexto campo TSV) y la paleta ya repone con `ctrl-r` vía `--play-file` + `stop_audio`: la cadena es la generalización natural del replay existente. El motor ya tiene el almacén con retención y los conectores para re-sintetizar desde transcript.

### Prior art y diferenciación
Ningún comparado ofrece replay múltiple; el podcast RSS de la suite se acerca (episodios por sesión) pero no es interactivo ni por chat en caliente. Diferenciación: re-escucha conversacional in-situ con relleno automático de huecos por retención.

### Dependencias
- PRD-AT-08 (`--play-chain`); sin ella, fallback secuencial del host (válido pero con pausas entre turnos).

### Riesgos y mitigaciones
- Cadenas largas → cap de N (RF-HT-10-3) y stop siempre disponible.
- Re-síntesis cara en proveedores de pago → solo bajo demanda explícita del usuario.
- Confusión de orden → marcador hablado suave entre turnos (opt-in, open question).

### Métricas de éxito
1. Uso >= 1 vez por día en semanas de flota intensa.
2. Autoinforme de respuesta correcta al blocked tras escuchar el contexto (mejora percibida).
3. Cero solapamientos de audio (mutex intacto en todas las cadenas reproducidas).

### Fuera de alcance
Exportar la cadena como fichero o episodio (eso es `--render-pane`/podcast), cadenas entre chats distintos, mezcla de proveedores dentro de la cadena.

### Open questions
¿Marcador sonoro entre turnos (opt-in) o silencio separador? ¿N persistente por chat o global? ¿La re-síntesis respeta la voz por agente de HT-02?

---

## PRD-HT-12 — Watchers personalizados de texto
**Prioridad**: Media · **Esfuerzo**: M

### Resumen ejecutivo
`~/.config/herdr-tts/watchers.json`: lista de reglas `{pane_pattern, regex, acción: speak|push|both, cooldown}` evaluada en el sweep existente de ~4 s sobre el scrollback de los panes. Mismas puertas de gating (mute/snooze/debounce), limpieza al cerrar el pane. Cubre eventos que importan y que el watcher de estados no ve: build fallido en un pane de shell, un error concreto en un log, un patrón en un deploy.

### Problema y flujo actual
El watcher reacciona a estados de agente (working/done/blocked), pero el trabajo real también produce señales de texto: "build FAILED" en un pane de CI local, un test que revienta tras 20 minutos, un deploy que termina. Esos panes no son agentes (o el estado no cambia) y hoy son invisibles para la capa de voz.

### Propuesta
Fichero declarativo de watchers. El sweep del daemon ya visita los panes cada ~4 s: para los que matcheen `pane_pattern`, lee el scrollback (`herdr pane read`, ya usado en adquisición) y evalúa las regex sobre las líneas nuevas desde el último offset por pane. Un match dispara el pipeline estándar (síntesis + push según acción) pasando por todas las puertas del gating ledger, con cooldown por watcher.

### Historias de usuario (US-HT-12-1, ...)
- **US-HT-12-1** — Como operador con CI local, quiero oír cuando un build falla en un pane de shell, sin mirar la terminal.
- **US-HT-12-2** — Como operador con suites de test largas, quiero un aviso solo con el veredicto (pass/fail), no cada línea de progreso.
- **US-HT-12-3** — Como operador que lanza deploys, quiero push al móvil cuando aparece el patrón de éxito o error, para no vigilar el pane.
- **US-HT-12-4** — Como operador, quiero que estos avisos respeten mute/snooze/debounce como cualquier otro, para tener una sola maquinaria de ruido.

### Requisitos funcionales (RF-HT-12-...)
- **RF-HT-12-1**: fichero `watchers.json`: array de `{name, pane_pattern (regex sobre título o agent_type del pane), regex, action: "speak"|"push"|"both", cooldown_s (default 300), enabled}`; validado al cargar con error accionable por regla.
- **RF-HT-12-2**: evaluación incremental: offset de scrollback por pane; solo se evalúan las líneas nuevas (nunca se re-procesa lo ya leído).
- **RF-HT-12-3**: disparo: el texto que matchea (con una línea de contexto previa opcional) entra por el pipeline estándar: gating ledger completo (mute/snooze/debounce del pane), síntesis con la voz vigente y push según `action`; el settle window no aplica (no es transición de estado de agente).
- **RF-HT-12-4**: cooldown por watcher y pane: re-match dentro del cooldown se ignora.
- **RF-HT-12-5**: ciclo de vida: pane cerrado → offset y estado del watcher purgados (misma poda del daemon que glifos y cachés de título).
- **RF-HT-12-6**: comando de verificación: `herdr-tts --watchers-test "<texto>"` (dry-run que imprime qué reglas dispararían y con qué acción).
- **RF-HT-12-7**: límites: máximo 20 watchers activos; presupuesto de evaluación por sweep (p. ej. últimos 200 saltos de scrollback por pane).

### Requisitos no funcionales (RNF-HT-12-...)
- **RNF-HT-12-1**: sin watchers configurados, coste cero: el sweep no cambia.
- **RNF-HT-12-2**: la evaluación no añade spawns por sweep más allá de la lectura de scrollback que ya hace para panes relevantes; regex evaluadas en el intérprete ya usado por el daemon.
- **RNF-HT-12-3**: fallo de un watcher (regex inválida, pane ilegible) nunca rompe el sweep: fail-open por regla con línea en `daemon.log`.

### Encaje en la arquitectura actual
El sweep ya ejecuta un `herdr agent list` por barrido y ya lee scrollback para shells genéricos: el watcher reusa ambas vías. El gating y el fan-out son los existentes; la poda de panes cerrados ya existe para glifos y caché de títulos. El texto matcheado pasa por el sanitizador y el redactor de secretos del motor antes de sintetizar o pushear.

### Prior art y diferenciación
Los comparados no observan contenido de pane; las soluciones ad hoc existentes son wrappers de campana de terminal sin gating ni voz. Diferenciación: watchers declarativos sobre la misma maquinaria de gating/síntesis/push de la suite, sin procesos nuevos y con cooldown.

### Dependencias
Ninguna.

### Riesgos y mitigaciones
- Regex costosas sobre scrollbacks grandes → presupuesto por sweep y evaluación incremental (RF-HT-12-2/7).
- Falsos positivos por líneas repetidas (spinners, barras de progreso) → cooldown + exigencia de línea nueva.
- Privacidad: texto de pane viajando al push → redactor de secretos del motor ya en el pipeline (obligatorio en la ruta).

### Métricas de éxito
1. Latencia evento real → voz < 10 s (un sweep + síntesis).
2. Cero regresión de CPU del daemon en reposo con watchers activos (comparativa de ticks antes/después).
3. >= 2 casos de uso reales (CI local, deploy) operativos en la primera semana.

### Fuera de alcance
Watchers sobre ficheros de log externos (solo panes), condiciones multi-línea con estado, acciones arbitrarias (solo speak/push/both), watchers con captura de grupos interpolada en la voz.

### Open questions
¿`pane_pattern` sobre título del pane, agent_type, o ambos combinados? ¿Debería el watcher heredar la voz del chat si el pane resulta ser un agente (HT-02)? ¿Push con MP3 inline o solo texto para matches cortos?

---

## PRD-HT-09 — Espacialización estéreo por pane
**Prioridad**: Baja-Media · **Esfuerzo**: S-M

### Resumen ejecutivo
Paneo estéreo según la posición X del pane en el layout de Herdr (columna izquierda = -30%, derecha = +30%, interpolado), aplicado por el motor con un parámetro de playback mínimo (`--pan <float>`), respetando los targets winhost/WASAPI. Opt-in con `TTS_SPATIAL="1"`; fallback mono limpio. El audio pasa a decir de dónde viene, no solo quién habla.

### Problema y flujo actual
En flota, el audio es mono y central: la identidad vocal (HT-02) dirá quién habla, pero no desde qué zona del layout. La posición es parte del mapa mental del operador: "el de la izquierda es el refactor, el de la derecha el deploy".

### Propuesta
El watcher calcula el pan desde la geometría X del pane (posición relativa al layout, disponible vía la API de Herdr) en el momento del evento, lo mapea a un rango acotado (±`TTS_SPATIAL_WIDTH`%, default 30) y lo pasa al motor como `--pan`. El motor aplica el paneo sobre el PCM decodificado antes de la salida (post-mix, agnóstico del proveedor y del transporte): local Pulse/PipeWire, WSLg y winhost/WASAPI reciben el PCM ya paneado sin cambios de protocolo de control.

### Historias de usuario (US-HT-09-1, ...)
- **US-HT-09-1** — Como operador de flota con auriculares, quiero que el audio venga desde la posición del pane, para localizarlo sin buscarlo.
- **US-HT-09-2** — Como operador sensible al audio, quiero un paneo suave que no mande la voz a un solo oído, para mantener el confort en jornadas largas.
- **US-HT-09-3** — Como operador en WSL2 con winhost, quiero la misma espacialización que en local, sin configurar nada por transporte.

### Requisitos funcionales (RF-HT-09-...)
- **RF-HT-09-1**: config: `TTS_SPATIAL="0|1"` (default 0), `TTS_SPATIAL_WIDTH="30"` (% máximo de paneo por lado).
- **RF-HT-09-2**: resolución de posición: geometría X del pane vía API de Herdr en el momento del evento; pane sin geometría resoluble → pan 0 (centro).
- **RF-HT-09-3**: paso del parámetro: `--pan <float>` en la llamada al motor; los audios on-demand usan el pan del chat enfocado/seleccionado.
- **RF-HT-09-4**: el paneo aplica igual en local (Pulse/PipeWire), WSLg y winhost/WASAPI: el PCM viaja ya en estéreo paneado.
- **RF-HT-09-5**: la paleta, el preview de voces (HT-11) y radio (HT-03) reproducen en mono centrado (pan 0) para no falsear la posición de una superficie agregada.

### Requisitos no funcionales (RNF-HT-09-...)
- **RNF-HT-09-1**: cero coste de CPU medible: paneo de dos canales sobre el buffer ya en memoria del driver miniaudio.
- **RNF-HT-09-2**: sin regresión de latencia: < 10 ms adicionales sobre el pipeline actual.
- **RNF-HT-09-3**: si el transporte no soporta dos canales (p. ej. wsl-ps en configuraciones restrictivas), fallback mono limpio con pan 0 y aviso único en `daemon.log`.

### Encaje en la arquitectura actual
La llamada al motor ya porta parámetros por evento (`--agent`, `--session-id`, `--voice` con HT-02): `--pan` es uno más. La implementación vive en el motor (extensión mínima del driver de playback) y el host solo calcula y propaga: exactamente el patrón ya usado con `TTS_PLAYBACK`. El pan se congela con el evento (no sigue al layout si este cambia durante la cola).

### Prior art y diferenciación
La espacialización por posición es práctica estándar en audio de escritorio (los roles de stream de PipeWire/WirePlumber la rozan con el ducking), citada como base conocida. Nadie en el ecosistema de notificaciones de agentes la aplica. Diferenciación: pequeña, sensorial y barata; refuerza el mapa mental de la flota junto a HT-02.

### Dependencias
- Extensión mínima del motor: parámetro `--pan` en el driver de playback (issue menor o PRD propia del repo hermano).

### Riesgos y mitigaciones
- Confusión si el layout cambia entre evento y reproducción (audio en cola) → pan congelado con el evento, documentado.
- Auriculares con balance raro o mono real → width configurable y opt-in.
- Transportes exóticos → fallback mono (RNF-HT-09-3).

### Métricas de éxito
1. Identificación correcta del origen (quién y desde dónde) sin mirar, >= 70% en autoinforme con HT-02 activo.
2. Cero regresión de latencia percibida.
3. Activación estable: la feature sigue encendida tras el primer mes (no se apaga por molestia).

### Fuera de alcance
HRTF/audio 3D, movimiento dinámico del pane durante la reproducción, espacialización en el push móvil (el MP3 viaja plano).

### Open questions
¿Posición X absoluta o relativa al layout visible (porcentaje)? ¿Cache de geometría por sweep en lugar de consulta por evento? ¿Interacción con el pan de la cadena de HT-10 (turnos antiguos, mismo pan del chat)?

---

## PRD-HT-11 — Audición de voces en la paleta
**Prioridad**: Baja · **Esfuerzo**: S

### Resumen ejecutivo
Tecla de preview en la paleta fzf que sintetiza una frase fija corta multilingüe con la voz seleccionada, usando el motor y respetando el mutex; auto-stop al navegar. Acción para asignar la voz como global o del chat (HT-02). Caché del sample por (proveedor, voz) para re-preview instantáneo y sin gasto de cuota.

### Problema y flujo actual
Elegir voz a ciegas leyendo nombres (`--voice alvaro`) es una experiencia pobre: el coste de probar una voz (editar config, reiniciar daemon, escuchar un evento real) desincentiva explorar y deja al usuario anclado a la voz default.

### Propuesta
Vista de voces dentro de la paleta (que ya lista chats y audios): lista las voces del proveedor activo, una tecla de preview sintetiza la frase fija con la voz bajo el cursor, mover el cursor corta el preview, y dos acciones de asignación: voz global o voz del chat seleccionado (con HT-02). La caché por (proveedor, voz) hace que repetir un preview sea instantáneo.

### Historias de usuario (US-HT-11-1, ...)
- **US-HT-11-1** — Como usuario que elige voz, quiero escucharla antes de asignarla, para decidir con criterio y sin ritual de reinicio.
- **US-HT-11-2** — Como usuario que compara voces, quiero navegar la lista escuchando ráfagas cortas, para comparar de oído en segundos.
- **US-HT-11-3** — Como usuario con HT-02, quiero asignar la voz escuchada al chat seleccionado o como global, sin salir de la paleta.
- **US-HT-11-4** — Como usuario de proveedores de pago, quiero que repetir un preview no vuelva a sintetizar, para no quemar cuota.

### Requisitos funcionales (RF-HT-11-...)
- **RF-HT-11-1**: entrada a la vista de voces desde la paleta (tecla `v`); la vista lista las voces del proveedor activo (edge/openai/elevenlabs/piper/kokoro) vía el listado del motor/voice manager.
- **RF-HT-11-2**: preview: frase fija corta (dos frases: español e inglés) sintetizada on-demand con la voz bajo el cursor; pasa por el mismo camino de reproducción y mutex que el daemon (nunca un segundo reproductor).
- **RF-HT-11-3**: auto-stop: mover el cursor corta el preview en < 0.3 s; salir de la vista también.
- **RF-HT-11-4**: caché: un sample por (proveedor, voz) en el directorio de caché del plugin; purga LRU por tamaño (umbral 20 MB); la caché nunca sustituye la voz real de los eventos.
- **RF-HT-11-5**: asignación: `enter` = voz global (persiste `TTS_VOICE` con escritura gestionada); tecla dedicada = voz del chat seleccionado (persiste en `voices.json` de HT-02; sin HT-02, aviso accionable).
- **RF-HT-11-6**: preview de voz de un proveedor sin claves/modelo instalado: aviso claro y ninguna llamada de red.

### Requisitos no funcionales (RNF-HT-11-...)
- **RNF-HT-11-1**: preview con caché: reproducción inicia en < 0.3 s.
- **RNF-HT-11-2**: preview sin caché: una síntesis normal del motor (presupuesto 3-6 s, indicado en pantalla).
- **RNF-HT-11-3**: cero dependencias nuevas: fzf ya es requisito de la paleta.

### Encaje en la arquitectura actual
La paleta ya es la superficie fzf del plugin y ya dispara reproducciones (`ctrl-r`) por el camino del mutex; el preview es una variante más de ese camino. La asignación global persiste en `config.env` con la escritura gestionada atómica (tmp + mv) ya usada por Ajustes; la asignación por chat delega en `voices.json` (HT-02).

### Prior art y diferenciación
Ningún comparado ofrece audición previa de voces; los selectores de voz del sistema operativo no aplican a un contexto de flota. Diferenciación: pequeña en alcance pero elimina la mayor fricción de adopción de HT-02 y del cambio de voz en general.

### Dependencias
- HT-02 para la asignación por chat; la asignación global funciona de forma autónoma.

### Riesgos y mitigaciones
- Síntesis de preview en proveedores de pago gasta cuota → caché obligatoria, frase corta fija, solo bajo demanda.
- fzf y audio asíncrono → auto-stop robusto con identificador de job (RF-HT-11-3).
- Inconsistencia de listado entre proveedores → la lista viene del propio motor (voice manager), no se mantiene en el host.

### Métricas de éxito
1. > 80% de los cambios de voz van precedidos de audición previa.
2. > 70% de previews servidos desde caché tras la primera semana.
3. Cero solapamientos de audio entre preview y daemon (mutex intacto).

### Fuera de alcance
Parametrizar la frase de preview desde la UI (basta config por fichero), fine-tuning o mezcla de voces, preview de rate/velocidad.

### Open questions
¿Frase de preview fija por idioma o configurable en `config.env`? ¿Preview también desde el menú de voz de una tecla o solo desde la paleta?

---

## Próximos pasos sugeridos

Orden de implementación recomendado, respetando dependencias con el repo hermano `agent-tts` y maximizando valor de flujo por semana de trabajo:

**Fase 0 — desbloquear el motor (repo hermano, en paralelo a todo lo demás)**
1. AT-02 (capa STT): bloquea HT-01, el ítem insignia y único abierto del roadmap actual; empezar cuanto antes.
2. AT-08 (cola con prioridades + `--play-chain`): desbloquea HT-03 en plenitud y HT-10.
3. AT-07 (`--digest`): desbloquea HT-08.
4. Issue menor: parámetro `--pan` en playback para HT-09.

**Fase 1 — valor inmediato sin dependencias (host)**
5. HT-02 (voces por agente): pequeño, transforma la experiencia de flota desde el día 1 y es prerrequisito blando de HT-11.
6. HT-05 (recordatorios escalados): cierra el agujero "sonó una vez y se perdió"; medir la baseline antes de activar.
7. HT-12 (watchers personalizados): amplía el perímetro de utilidad más allá de los agentes.

**Fase 2 — control y filtrado**
8. HT-04 (control bidireccional móvil): empezar pronto por su esfuerzo L; listener + Actions con lista blanca primero, texto libre cuando exista la inyección de HT-01.
9. HT-03 (radio mode): funciona ya con el fallback secuencial y mejora sola al aterrizar AT-08.
10. HT-07 (filtro semántico): heurística primero; validar la tasa de omitidos antes de tocar defaults.
11. HT-06 (auto-snooze): Linux nativo/WSLg primero; la extensión de winhost puede ir después.

**Fase 3 — con el motor listo**
12. HT-01 (push-to-talk): en cuanto AT-02 aterrice; cierra la bidireccionalidad.
13. HT-10 (chain replay): tras AT-08.
14. HT-08 (briefing): tras AT-07.
15. HT-09 (espacialización) y HT-11 (audición de voces): cierre de la experiencia sensorial; HT-11 depende de HT-02.

**Regla transversal:** toda feature nace apagada por defecto (opt-in) salvo que sustituya explícitamente un comportamiento existente; ninguna deja el flujo actual roto si se desactiva. Las métricas de cada PRD se miden sobre el uso real del maintainer, no sobre escenarios sintéticos.
