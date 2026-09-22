**ID**: PRD-AT-03 · **Proyecto**: agent-tts
**Prioridad final (revisión 2026-09-22)**: P2 · **Estado**: Recortada
**Dependencias**: Ninguna

> **Nota de revisión (22/09/2026)**: Alcance reducido en revisión: lo único activo es VERIFICAR que una sesión web real de OpenCode resuelve con el conector actual (verificación de schema realizada 22/09/2026: tabla única `session`, lookup por id sin filtro de workspace — probablemente ya cubierto; tarea de validación con un id web real, sin código nuevo). Quedan FUERA de alcance actual: conector gemini-cli, conector goose, auto-detección por /proc (RF-AT-03-1, RF-AT-03-2 y RF-AT-03-4 quedan fuera de alcance actual, marcados sin borrar).

# PRD-AT-03 — Conectores restantes y auto-detección del agente

**Prioridad**: Media · **Esfuerzo**: M

### Resumen ejecutivo

El roadmap de conectores deja abiertos gemini-cli, goose, opencode web y la auto-detección del agente corriendo en un pane. Esta PRD cierra los tres conectores con el contrato vigente (reverse-tail-scan con tope de 8 MB, validación de session id, fallback a scrollback) y añade sniffing de proceso/PTY para que `--session-id` sin `--agent` resuelva solo cuando el formato del id no baste.

### Problema y flujo actual

La capa de conectores es el contrato Vision B ("los hosts pasan identidad; el motor posee el transcript"): OpenCode via SQLite, Claude/Codex/Antigravity via JSONL con reverse tail, Aider via markdown fence-aware. gemini-cli (temp dirs bajo ~/.gemini), goose (session files) y opencode web no resuelven, y el fallback scrollback es más ruidoso que el transcript estructurado. Además, el sniffing actual por forma de session id (ses_* frente a UUID) es estático: no cubre formatos que colisionan ni el caso sin id.

### Propuesta

Tres conectores nuevos siguiendo el patrón `sources/<agent>.py` con registro en `sources/__init__.py`: cada uno implementa descubrimiento de fichero por session id, validación (el fichero corresponde a esa sesión), extracción del último mensaje de asistente con reverse tail de 8 MB, y no-modifica-nada (lectura). Para gemini-cli: resolución de los temp dirs bajo `~/.gemini` con selección por mtime y validación del id dentro del fichero. Para goose: parse de sus session files versionados. Para opencode web: mismo store SQLite con identificación de la sesión web. Auto-detección extendida: cuando `--agent` no viene y el id no basta, sniffing del proceso foreground del pane (via `/proc` del pid entregado por el host o PTY) comparando cmdline contra firmas de cada agente (binario node de opencode, claude, codex, agy, aider, gemini, goose).

### Historias de usuario (US-AT-03-1, ...)

- US-AT-03-1: Como usuario de gemini-cli, quiero `--agent gemini --session-id X` leyendo el último mensaje real, no scrollback.
- US-AT-03-2: Como orquestador (herdr), quiero pasar solo el pane/pid y que el motor infiera el agente sin configuración por herramienta.
- US-AT-03-3: Como usuario, quiero que un conector roto (formato cambiado) caiga al fallback scrollback sin abortar la reproducción.

### Requisitos funcionales (RF-AT-03-...)

- RF-AT-03-1: Conector gemini-cli: descubre candidatos bajo `~/.gemini`, valida el session id contra el contenido, extrae el último turno de asistente con reverse tail <= 8 MB. **[Fuera de alcance actual — revisión 22/09/2026]**
- RF-AT-03-2: Conector goose: soporta el formato de session files vigente documentado en la fecha de esta PRD; ante versión no reconocida, error claro y fallback scrollback. **[Fuera de alcance actual — revisión 22/09/2026]**
- RF-AT-03-3: Conector opencode web: consulta read-only del mismo SQLite con la sesión web identificada por id.
- RF-AT-03-4: Auto-detección: con `--pane-pid PID` (o variable del host), lee `/proc/PID/cmdline` y matchea firmas de agentes conocidos; resultado integrado al orden de sniffing existente (forma de id primero, proceso segundo). **[Fuera de alcance actual — revisión 22/09/2026]**
- RF-AT-03-5: El fallback scrollback se mantiene como último recurso invariable para cualquier conector que no resuelva.
- RF-AT-03-6: Ningún conector escribe ni crea ficheros en los stores de los agentes (solo lectura, URIs read-only donde aplique).

### Requisitos no funcionales (RNF-AT-03-...)

- RNF-AT-03-1: Resolución de conector (descubrimiento + validación + extracción) por debajo de 150 ms p95 con transcript de 5 MB.
- RNF-AT-03-2: El sniffing de proceso añade menos de 10 ms y no abre el proceso (solo /proc de lectura).
- RNF-AT-03-3: Cada conector tiene tests con fixtures versionadas del formato upstream; deriva de formato detectada por test roto, no en producción.
- RNF-AT-03-4: Cero dependencias nuevas; parsing con stdlib.

### Encaje en la arquitectura actual

Módulos nuevos en `src/agent_tts/sources/` espejo de `claude.py`/`codex.py`/`antigravity.py`, respetando el contrato de `sources/base.py` y el registro con orden de sniffing en `sources/__init__.py`. La auto-detección extiende la resolución de `--agent` en el CLI sin cambiar la firma pública (`--agent`/`--session-id` siguen siendo la entrada). El sniffing por forma de id existente se conserva como primera pasada.

### Prior art y diferenciación

TalkToCursor cubre Cursor/Claude Code/Codex/Antigravity con una tool MCP `speak` donde el modelo decide cuándo hablar: enfoque inverso (el agente coopera). agentvoice y claude-code-tts están atados a Claude. La diferenciación del motor sigue siendo la misma tesis: conectores neutrales poseídos por el motor, sin cooperación del agente y sin scraping de TUI; esta PRD la extiende en lugar de cambiarla.

### Dependencias

Ninguna. Comparte fixtures de formato con la suite existente (test_sources).

### Riesgos y mitigaciones

- Riesgo: formatos inestables de gemini-cli (temp dirs) y goose (versionado). Mitigación: validación estricta por sesión, fallback scrollback garantizado, fixtures versionadas que fallen ruidosamente en CI.
- Riesgo: fingerprinting de proceso frágil (wrappers, paths custom). Mitigación: firmas matchean nombre de binario y no ruta completa; lista ampliable por config.
- Riesgo: opencode web diverge del schema SQLite local. Mitigación: mismo acceso read-only y prueba de schema antes de consultar.

### Métricas de éxito

- Tasa de resolución por conector mayor o igual a 95% en sesiones reales de cada agente.
- Cero lecturas erróneas (mensaje de otra sesión) validado por test de aislamiento de session id.
- Latencia de sniffing completa menor de 10 ms.

### Fuera de alcance

Windows para sniffing de proceso (vía /proc no aplica; se deja para AT futura), conectores de agentes en la nube sin store local, streaming de transcript en vivo.

### Open questions

¿Firmas de proceso en código o en config user-editable (~/.config/agent-tts/agents.json)? ¿Goose expone estable el session id en el nombre de fichero o hay que abrir el contenido?
