# POST-09 — Investigación: soporte completo de modelo y cambio de modelo para `agy` (Antigravity CLI)

- **Fecha:** 2026-11-26 · **Modo:** solo lectura (código de producto y entorno `agy` intactos)
- **Worker:** `agymodel` (fleet `parity-post`) · **Entorno verificado:** `agy` 1.5.18 (`/home/bruno/.local/bin/agy`, ELF 280 MB, build 2026-11-26)
- **Método:** lectura de código (`ruta:línea`), ejecución de comandos de solo lectura (`agy --help`, `agy models`, `sqlite3 file:...?mode=ro`), inspección de transcripts reales locales, docs oficiales (`antigravity.google/docs/cli/reference.md`, `changelog.md`) y `strings` sobre el binario.

---

## 0. Resumen ejecutivo

1. **La detección del modelo activo en `agy` YA es implementable hoy**: el transcript JSONL real lleva `model` + tokens por request desde ~2026-11-17. El extracto del engine (`probe_antigravity`) es un **stub** que devuelve "no measurable" sin leer el transcript — su docstring está desactualizada.
2. **Existe un catálogo completo de modelos**: `agy models` (subcomando CLI) imprime `id<TAB>Nombre` de los ~27 modelos conectados — el equivalente exacto de `opencode models`.
3. **El comando de cambio existe y está documentado**: `/model` en la TUI (persistente) y `/model <model> <prompt>` one-shot; `/effort` para el nivel de razonamiento; flags `--model`/`--effort`.
4. **El picker de `/model` NO es una lista plana simple**: el changelog y el binario (`EffortSelectorModel`, `renderGauge`) indican filas por familia con **gauge de effort** (Tab/Shift+Tab, ←/→). La gramática exacta en pantalla **requiere una captura live** (comando propuesto al operador en §6) antes de implementar detección/cambio verificado.
5. El botón "Cambiar modelo" no aparece hoy porque `antigravity` falta en las dos tablas de comandos (`tools.py:135` y `static/dialogs.js:756`).

---

## 1. Q1 — Cómo muestra y cambia de modelo `agy` en el mundo real

### Hechos observados

| # | Hecho | Evidencia |
|---|---|---|
| F1.1 | `agy --help` expone `--model` ("Model for the current CLI session") y `--effort` (`low\|medium\|high\|xhigh\|max`) | salida real de `agy --help` (2026-11-26) |
| F1.2 | La TUI tiene slash command **`/model`**: "Choose your preferred reasoning model (persists across sessions). To run a single prompt on another model without changing your default, use `/model <model> <prompt>`" | `https://antigravity.google/docs/cli/reference.md` (tabla Core slash commands) |
| F1.3 | Existe **`/effort`**: "Set or inspect the active model's reasoning effort level (`low` through `max`, depending on the model)" | misma referencia, fila `/effort` |
| F1.4 | El changelog describe el picker con **filas por familia y effort embebido**: la fila Orchestrator permite elegir Planner/Executor y su Effort con `Tab`/`Shift+Tab` para mover el foco y `←`/`→` para cambiar la elección; se guarda en `"orchestrator"` de `settings.json` | `changelog.md` líneas 1835, 1867 |
| F1.5 | "reasoning effort level … selectable with `--effort` or from the **effort gauge in `/effort` and `/model`**" | `changelog.md` línea 3065 |
| F1.6 | "`--model` and `/model` accept a base model name without `--effort`: … the CLI now picks the model's **default thinking level** (the server's default for that model, otherwise medium, otherwise the lowest available)" | `changelog.md` línea 2780 |
| F1.7 | El binario contiene los símbolos `EffortSelectorModel`, `selectEffort`, `renderGauge`, `EffortSuffix`, `EffortHighlighted`, el literal `Switch Model` y un fragmento de status line `Model: %s` | `grep -a` sobre `/home/bruno/.local/bin/agy` |
| F1.8 | Existen pies de familia "…esc to cancel" en el binario (la misma familia de footer que detecta `AntigravityAdapter`) | `grep -a "esc to cancel"` sobre el binario |
| F1.9 | El default global elegido con `/model` se persiste **por su label** en `settings.json`: el equipo real tiene `"model": "Gemini 4 Neon (Medium)"`; la doc confirma: "The model you chose with `/model`, saved by its label" | `~/.gemini/antigravity-cli/settings.json` + reference.md (settings key `model`) |
| F1.10 | El changelog registra un fix de "**queued `/model` switching**" y el App 2.35.2 permite cambiar modelo con el turno corriendo (superficie App, no CLI) | `changelog.md` líneas 3196, 15 |

### Hipótesis (no verificadas — requieren captura live)

- **H1.1**: el picker de `/model` pinta **una fila por familia** (Gemini 4 Neon, Carbon, Argon, Hydrogen, Orchestrator, Claude Opus/Sonnet 5.5, GPT-OSS…) con un gauge de effort por fila, y **no** una fila por id `gemini-4-neon-high`. El catálogo CLI sí separa efforts como ids distintos, pero eso no prueba el layout del picker.
- **H1.2**: tras abrir `/model` el panel queda **`idle`** (overlay tipo `/resume` o `/config`) — como opencode — o **`blocked`**; sin captura no se sabe, y determina si el flujo necesita `modal=1` (server.py:2529 ya lo soporta de forma genérica).
- **H1.3**: el footer del picker coincide con la familia `enter select · esc cancel` que ya detecta `AntigravityAdapter` (fixture `tests/fixtures/dialogs/antigravity-select.txt`), pero quizá con navegación extra (←/→ para el gauge), que el adapter actual no declara (`nav_keys=("up","down")`, `commit_keys=("enter")` — antigravity.py:52-53).

### Consecuencia de diseño si H1.1 es cierta

"Seleccionar una fila" cambia la familia al **effort default de esa fila** (F1.6); subir/bajar effort exigiría teclas `left`/`right` sobre el gauge — hoy fuera del vocabulario del adapter y de `answer_structured_dialog`. Alternativa honesta: tratar cada entrada del catálogo (`familia+effort`) como un modelo y, si el picker no pinta efforts como filas, limitar el switch por voz a la familia y reportar el effort efectivo tras el cambio (leído del transcript siguiente), nunca adivinarlo.

---

## 2. Q2 — Detección del modelo activo (`last-model`) y ventana de contexto

### Hechos observados

**F2.1 — Por qué falló hoy el comando.** `python -m agent_tts.cli last-model --agent antigravity …` devuelve `Error: no model recorded … transcript carries no token usage data` porque `probe_antigravity` es un **stub de línea única** que ni lee el transcript:

- `engine/src/agent_tts/cache_probe.py:354-365`: construye `ProbeResult(status=STATUS_NOT_MEASURABLE, reason="transcript carries no token usage data")` incondicionalmente; solo comprueba si el fichero existe para rellenar `source_path`.
- `cache_probe.py:801-810`: `handle_last_model_command` traduce ese estado al error observado.
- La docstring (`cache_probe.py:35-49`) afirma "no model identity in their transcripts: … verified against real local artifacts, 2026-11-17" — **desactualizada**.

**F2.2 — El transcript real SÍ lleva modelo y tokens.** Sesión real `~/.gemini/antigravity-cli/brain/39af4a38-f706-4f47-97c2-d00102f576ac/.system_generated/logs/transcript.jsonl` (688 eventos):

- Cada `PLANNER_RESPONSE` lleva `input_tokens`, `cache_read_tokens`, `output_tokens`, `created_at`, `status`, `step_index`.
- El campo `model` aparece en 37/288 PLANNER_RESPONSE de ESA sesión: **primera aparición 2026-11-17T18:40:13Z** (`gemini-4-hydrogen-medium`); los eventos previos (2026-11-10) no lo llevan → el campo llegó con una actualización de `agy` (~v1.4.x/1.5.x; el campo no es específico de una config).
- Último evento: `model="gemini-4-hydrogen-high"`, `input_tokens=29658`, `cache_read_tokens=314493`, `output_tokens=5260`, `status="DONE"`.
- Sesiones 100 % recientes lo llevan: `b9eb8fec…` → 12/12 `gemini-4-neon-high`; `a07572d4…` → 12/12 `gemini-4-neon-medium`.
- `transcript_full.jsonl` = mismos tipos/keys con contenido completo (sin `truncated_fields`); `model` presente también.

**F2.3 — Cadena del chip en Brain.** `PaneModelResolver._lookup` (`pane_model.py:453-467`) ejecuta `python -m agent_tts.cli last-model --agent antigravity --session-id …`; `ENGINE_AGENTS` ya mapea `antigravity`/`agy` → engine `antigravity` (`pane_model.py:48-55`). El chip se pinta en `app.js:2144-2157` (`agent.model` + `agent.context` → `#pane-picker-sub`) y `app.js:5685-5700` (`#tab-model-tag`). Con el stub, `model` nunca llega → chip vacío. **Arreglar el extractor del engine arregla el chip de modelo sin tocar Brain.**

**F2.4 — Ventana de contexto.** `_limit_for` (`pane_model.py:443-451`) devuelve `None` para todo harness ≠ opencode ("never a guess"). No existe hoy fuente honesta de *limit* para `agy`:
- `agy models` no imprime ventanas (salida real §3); no tiene `--json`.
- El panel `/context` de la TUI pinta capacidad (changelog: umbrales de auto-compaction, línea 1830), pero es pintura en pantalla, no un catálogo.
- El **used** sí es derivable igual que opencode (`_probe_used`, `pane_model.py:406-441`): `input + cache_read + output` del último request del transcript (F2.2).

**F2.5 — Lo que NO sirve para detección por sesión.** `settings.json` → `"model"` es el **default global** (label), no el modelo por sesión. La BD `conversations/<sid>.db` es SQLite con blobs protobuf (`steps.metadata`, `step_payload`) — parsearlos sería frágil y opaco; el JSONL es la fuente estable que ya consumen `AntigravitySource` (`engine/src/agent_tts/sources/antigravity.py:39-66`) y `AntigravityTranscript` (`hosts/herdr/brain/src/herdr_brain/transcripts.py:418-443`).

### Qué hay que arreglar (extracto)

Reescribir `probe_antigravity` para: localizar `transcript.jsonl` (mismo path que hoy), escanear en reversa con el scan-cap existente, y devolver el **último** `PLANNER_RESPONSE` que lleve `model` no vacío → `STATUS_OK` con `TurnUsage(model=…, input/cached/cache_write tokens…)`. Fail-closed honesto: eventos sin `model` (transcripts pre-2026-11-17) → seguir retrasiéndose; si ninguno lo lleva → `NOT_MEASURABLE` con razón nueva ("transcript predates model field" o similar). Docstrings F2.1 corregidas. Tests que hoy **fijan el stub** y deben reescribirse: `engine/tests/test_cache_probe.py:351` (`test_antigravity_is_not_measurable`) y `engine/tests/test_last_model.py:342` (`test_antigravity_has_no_model`) — con fixtures reales saneados (líneas PLANNER_RESPONSE con y sin `model`).

---

## 3. Q3 — Catálogo de modelos de `agy`

### Hechos observados

**F3.1 — `agy models` existe y devuelve el catálogo completo** (salida real, exit 0, 2026-11-26):

```
gemini-4-neon-high	Gemini 4 Neon (High)
gemini-4-neon-xhigh	Gemini 4 Neon (XHigh)
gemini-4-neon-medium	Gemini 4 Neon (Medium)
gemini-4-neon-low	Gemini 4 Neon (Low)
gemini-4-carbon-medium	Gemini 4 Carbon (Medium)   … (Low/High)
gemini-4-argon-medium	Gemini 4 Argon (Medium)    … (Low/High)
gemini-4-hydrogen-high	Gemini 4 Hydrogen (High)   … (XHigh/Medium/Low)
gemini-pro-orchestrator-neon	Orchestrator (Neon)
gemini-pro-orchestrator	Orchestrator (Carbon)
gemini-pro-orchestrator-argon	Orchestrator (Argon)
gemini-3.8-flash-high	Gemini 3.8 Flash (High)    … (Medium/Low)
claude-opus-5-5-low/medium/high	Claude Opus 5.5 (…)
claude-sonnet-5-5-low/medium/high	Claude Sonnet 5.5 (…)
gpt-oss-120b-medium	GPT-OSS 120B (Medium)
```

Formato: **una línea por modelo, `id<TAB>Display Name`** — el paralelo exacto de `opencode models` (`OpencodeModelCatalog`, `pane_model.py:495-598`). Sin `--json` (probado: "flags provided but not defined: -json").

**F3.2 — El catálogo varía por login/proyecto** (igual que opencode por proveedor): el changelog documenta ocultar modelos de terceros según el proyecto GCP (líneas 717, 1882, 2782), aparición de Neon/Hydrogen según `GEMINI_API_KEY` vs `AGY_ADC_AUTH` (1904, 1938, 2081), y `modelProvider`/`customModelsConfig` en settings (reference.md). ⇒ El catálogo debe LEERSE en runtime con TTL (patrón opencode), nunca hardcodearse.

**F3.3 — Sin ventanas de contexto ni metadatos extra** en la salida CLI (solo id+nombre). El nombre para buscar ("Gemini 4 Neon (High)") viene de la 2ª columna; el id para el chip viene del transcript.

**F3.4 — Mapeo id ↔ label:** transcript ⇒ id (`gemini-4-neon-high`); `settings.json` ⇒ label (`Gemini 4 Neon (Medium)`); `agy models` ⇒ ambos. El chip puede mostrar el id crudo o resolverlo al label vía catálogo (decisión menor, §7).

---

## 4. Q4 — Paridad con `opencode`: piezas exactas a tocar

| Pieza | Fichero(s) | Estado hoy | Cambio necesario |
|---|---|---|---|
| Extractor de modelo (`last-model`) | `engine/src/agent_tts/cache_probe.py` (`probe_antigravity` :354-365, docstrings :31-49) | stub "not measurable" | Parsear transcript (§2). Fixes chip de modelo + used. **Fase 1, sin captura previa.** |
| Tests del extractor | `engine/tests/test_cache_probe.py:351`, `engine/tests/test_last_model.py:342` | fijan el stub | Reescribir con fixtures reales (con/sin `model`). |
| Comando que abre el picker (server) | `hosts/herdr/brain/src/herdr_brain/tools.py:135-143` (`MODEL_COMMANDS`) | sin `antigravity` | `+ "antigravity": "/model"` **SOLO tras verificar la gramática del picker en vivo** (comentario del dict ya exige "VERIFIED per harness"). |
| Comando que abre el picker (PWA) | `hosts/herdr/brain/src/herdr_brain/static/dialogs.js:756-764` (`MODEL_SWITCH_COMMANDS`) + mirror en `tools.py:128` | sin `antigravity` | Ídem; **C2: re-pinear digests en `tests/browser/test_karaoke_harness.py`**. |
| Detector del picker | `hosts/herdr/brain/src/herdr_brain/dialogs/adapters/antigravity.py` (:29-57, solo `kind="select"`) | no detecta picker | Nuevo `detect_model_picker` (`kind="model_picker"`) fail-closed sobre captura real; ampliar `nav_keys` (¿`left`/`right` para el gauge?). Fixture `.txt` nuevo. |
| Respuesta del picker | `tools.py:2047-2192` (`switch_model`), `answer_structured_dialog` :1159 | path genérico sirve (walk+enter) | Verificar confirm post-switch: `agy` NO muestra "Switch model?" estilo claude (sin evidencia); el re-probe del transcript (`note_switched`, :325) aplica igual. |
| Catálogo completo | `pane_model.py` (`OpencodeModelCatalog` :495) → nuevo p.ej. `AntigravityModelCatalog`; `tools.py:3181-3226` (`pane_models`, gate `OPENCODE_SEARCH_PICKERS` :146); `static/dialogs.js:678` (`CATALOG_HARNESSES`) | opencode-only | `-d` `AntigravityModelCatalog` (subprocess `agy models`, binario: `which agy` / `~/.local/bin/agy`, parse `id\tName`, TTL igual); abrir gate a `antigravity`; `CATALOG_HARNESSES + antigravity` (+C2). **Útil solo cuando exista el detector (orden F3→F2).** |
| Chip de contexto (`used/limit`) | `pane_model.py:_limit_for` :443-451 | `None` para agy | Decisión del operador (§7-D3): sin fuente honesta de *limit* hoy; `used` sí sale gratis de F1. |
| Variantes/effort | `detect_variant_picker` (opencode), `/effort` agy | n/a | Fuera de alcance mínimo; ver §7-D2. |
| Estado del panel tras abrir picker | `server.py:2529-2550` (`/view?modal=1` genérico) | listo | Solo decidir si agy exige `modal=1` (H1.2) tras la captura. |

---

## 5. Propuesta de plan por fases

- **F1 — Detección real (engine, ya implementable):** extractor + tokens + tests con fixtures reales. Entrega: `last-model --agent antigravity` funciona; chip de modelo vivo en `#pane-picker-sub` y `#tab-model-tag`; `used` sin `limit`. *No toca `static/` (sin C2).*
- **F2 — Captura live del picker (operador, §6):** comando exacto propuesto; con el `.ansi` resultante se construye fixture + detector.
- **F3 — Detector `model_picker` de agy:** adapter fail-closed + fixture; decidir `modal=1` según H1.2; `switch_model` (voz) VERIFIED solo entonces.
- **F4 — Catálogo + tarjeta completa PWA:** `AntigravityModelCatalog` + gates + `CATALOG_HARNESSES` + digests (C2) + suites separadas (C4).
- **F5 (opcional) — Effort por voz:** requiere teclas de gauge (`left`/`right`) o `/effort` como segundo comando; solo si el operador lo quiere (§7-D2).

---

## 6. Captura live propuesta (requiere autorización del operador)

**No se envió ninguna tecla a ningún panel del usuario.** Para verificar la gramática del picker (H1.1–H1.3), propongo este comando autocontenido, en una sesión tmux desechable bajo `/tmp` (no toca sesiones ajenas; crea una conversación nueva efímera de `agy`):

```bash
mkdir -p /tmp/opencode && tmux new-session -d -s agy-probe -x 120 -y 40 'cd /tmp/opencode && agy' \
 && sleep 10 \
 && tmux send-keys -t agy-probe '/model' Enter && sleep 3 \
 && tmux capture-pane -t agy-probe -p -e > /tmp/opencode/agy-model-picker.ansi \
 && tmux capture-pane -t agy-probe -p > /tmp/opencode/agy-model-picker.txt \
 && tmux send-keys -t agy-probe Escape && sleep 1 && tmux send-keys -t agy-probe '/exit' Enter && sleep 2 \
 && tmux kill-session -t agy-probe && echo CAPTURA-OK
```

Con ambos ficheros puedo derivar fixture + detector verificado (y ver si el status del panel es `idle` o `blocked` durante el overlay, capturando además `tmux display-message -p -t agy-probe '#{pane_current_command}'` si se desea).

---

## 7. Decisiones abiertas para el operador

- **D1 — Autorizar la captura live (§6):** bloquea F3/F4 (picker + switch + tarjeta). Sin ella, solo F1 es implementable.
- **D2 — Alcance del cambio:** ¿solo cambiar de modelo/familia por voz y tarjeta (mínimo paridad), o también **effort** (`/effort`, gauge ←/→) como primera clase? El gauge añade teclas nuevas al vocabulario del adapter y riesgo de gramática.
- **D3 — Chip de ventana de contexto:** ¿dejar `limit=None` (chip solo modelo+used, honesto) o buscar fuente de ventanas (p. ej. captura de `/context`, que es pintura en pantalla — menos fiable)? Por defecto recomiendo **solo modelo+used**.
- **D4 — Etiqueta del chip:** ¿id crudo (`gemini-4-neon-high`) o resolver a label humano vía catálogo (`Gemini 4 Neon (High)`)? Recomendaría label con fallback al id.
- **D5 — Sesiones viejas:** los transcripts pre-2026-11-17 no llevan `model` → el extractor debe responder "no model recorded" honesto (sin adivinar desde `settings.json` global). Confirmo que ese comportamiento es el deseado.

## 8. Riesgos

- **Deriva de versión de `agy`:** el campo `model` apareció con una actualización reciente; un futuro cambio de formato rompería el extractor → tests de caracterización con fixtures reales + fail-closed.
- **Gauge del picker (H1.1):** si el effort vive dentro de la fila, "elegir modelo" puede alterar el effort implícitamente (F1.6); el switch debe reportar el modelo **efectivo** (id leído del siguiente request), nunca el asumido.
- **Catálogo variable por login** (F3.2): TTL corto y reason codes como opencode; nunca cachear éxitos eternos.
- **C2/C4:** cualquier cambio en `dialogs.js` exige re-pinar digests y suites en procesos separados.
- **Quota:** `agy models` es ligero y sin coste de modelo; la captura live abre una sesión real (login del operador) — solo con su permiso.
