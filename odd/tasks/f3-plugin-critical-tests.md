# F3 — Red de tests crítica del plugin (R4, opción B)

**Feature id:** `f3-plugin-critical-tests` · **Worktree:** `agent-tts-worktrees/f3-tests` · **Branch:** `test/plugin-critical-bash-net` (from `main` @ `5e833fe`)
**Fuente:** `docs/voice-stack/ROADMAP.md` §4-F3, decisión D2 (portar solo lo crítico).

## Objetivo
Antes de que HT-01/HT-04/HT-11 toquen `hosts/herdr/tts-plugin/bin/herdr-tts` (7.419 líneas), los escenarios críticos del legado (`smoke-tests.sh` @ `4c572a4`, 462 aserciones) tienen cobertura ejecutable en el formato actual (`tests/host_cli_cases.sh`, `tests/matrix/`, `tests/all_bash_harnesses.sh`).

## Alcance autorizado
Tests y su documentación dentro de `hosts/herdr/tts-plugin/tests/`. Sin cambios en `bin/herdr-tts` ni código de producto (si un test revela un bug, se documenta, no se corrige aquí). Commits en la rama de feature (política de horas del maintainer). Sin push.

## Tareas
- [x] **T1 (R4a)** Auditar el legado: qué cubría cada bloque y clasificar crítico / no crítico / obsoleto. Entregable: tabla en este documento.
- [x] **T2 (R4b)** Portar los críticos: gating, snooze, mute, debounce, voces, keymap apply/rollback, ciclo de vida del daemon.
- [x] **T3** Registrar lo no portado y por qué (cubierto por matriz / obsoleto / fuera de alcance).
- [x] **T4** Ejecutar la batería completa y confirmar verde; engancharla a CI si no lo está.

## Ruta
Delegated direct: un explorador (T1) y un escritor (T2-T4). TDD: los tests portados ejercen comportamiento existente; evidencia = ejecución en verde y, cuando sea posible, fallo observado al romper el comportamiento (mutación manual en copia, nunca en producto).

## Progreso
Creado 2026-10-05.

2026-10-06: auditoría contrastada con `git show 4c572a4:hosts/herdr/tts-plugin/scripts/smoke-tests.sh` (4.143 líneas) y las funciones actuales. Batería inicial sin cambios: **29 host + 3 bootstrap OK, 0 FAIL**. Escritura acotada a los cinco archivos autorizados; producto y matriz intactos. Temporales autorizados por el padre en `/tmp/opencode/f3-runtime/`, con `TMPDIR` redirigido y `PYTHONDONTWRITEBYTECODE=1`.

Implementados **40 casos** independientes en `critical_cases.sh`: gate 9, mute/snooze 5, voces 6, keymap 10, daemon 6 y writer 4. Mismo protocolo `CASE`, filtro `CASES=`, sandbox e `in_host`, sin copiar helpers legacy. `host_cli_cases.sh` solo añade pins de rutas; conserva todas sus aserciones. Wrapper y job CI Ubuntu añadidos; no se han hecho commits ni push. Se supera la heurística de 400 líneas: el aislamiento independiente, los guards explícitos, el reap de hijos y seis controles de mutación justifican el tamaño, sin comprimir tests para cuadrar un presupuesto.

**T4 completado y verificado:** Batería completa ejecutada en verde (**31 host + 42 critical + 3 bootstrap = 76 OK, 0 FAIL**, rc 0). Enganchado a CI en `.github/workflows/ci.yml` (job `plugin-bash-tests` en Ubuntu latest). Contención del watcher y del cleanup del harness corregida el 2026-10-07 (ver «Corrección de contención» abajo): la afirmación anterior de «contención completa validada» era incompleta — el watcher usaba una plantilla `mktemp` absoluta y un log global de errores que `TMPDIR` no redirige, y el cleanup crítico podía esperar sin límite ante un hijo que ignora TERM.

## Auditoría T1

El legado tenía aproximadamente 462 aserciones, no 462 casos independientes. La matriz actual vincula alternativas modificadas a casos existentes; no demuestra cobertura del comportamiento crítico completo. Los watchers actuales sustituyen `gate_agent_event`: **no ejercen el gate real**.

| Bloque legado @ `4c572a4` | Clasificación / cobertura previa | Decisión F3 |
|---|---|---|
| s20, 253–290: takeover y guard de identidad | Crítico; descubierto | PID y takeover sobre hijos propios, proceso no relacionado preservado |
| s26, 1925–2007: stop/restart y fallback | Crítico; descubierto | Stop por pidfile y fail-open; restart y sweep real excluidos (ver T3) |
| s29, 2137–2229: supervisor, flag, relaunch, TERM | Crítico; descubierto | Decisión, consumo de flag, flag obsoleto, dos lanzamientos deterministas y TERM con hijo propio |
| s27a–c, 2008–2092: settle del watcher | Crítico opcional; basado en tiempo | No portar sleeps/flippers; conservar cobertura actual de anuncio con settle=0 |
| s18, 1318–1543: keymap apply/adopt/rollback | Crítico; descubierto | Preservación, idempotencia, null, backups, dry-run, rechazo, rollback, ausencia de Herdr, adopt |
| s17g/h/i, dentro de 1157–1317: validación | Crítico; descubierto | IDs desconocidos, duplicados canónicos y sintaxis inválida; añadir negativas estructurales |
| s19, 1544–1585: keymap autostart | Crítico; descubierto | Corrupto no fatal y archivo ausente; rutas válidas mediante apply |
| s31, 2300–2363: voces | Crítico; descubierto | Precedencia, corrupción/log, cache por mtime, reglas/off, prefix y auto_assign |
| s25/s30, 1800–1924 y 2230–2299: config_set | Crítico solo el writer; descubierto | Upsert/dedupe, admisión, `.bak`, directorio no escribible; no portar UI |
| gate_agent_event (actual 3039), load/save (2754/2762), CLI 6939 | Mayor hueco: el legado nunca llamó directamente al gate | Casos nuevos por CLI y source: mute, snooze, debounce, persistencia y fail-open |
| Dashboard/overlays, paleta, menús, themes/i18n, layer audit, reader, skill, retention | No crítico para esta tarea | Excluido; inventario T3 abajo |
| s33/s34: bootstrap/install | Obsoleto frente a onboarding portable | No trasladar supuestos antiguos; cambios corroborados en `c577041` y `7e41bd0`; conservar harness bootstrap actual |

Corrección respecto al legado: `voice_auto_assign` actual usa el catálogo del engine (primeras cuatro voces), no una paleta estática; sin catálogo o con proveedor no rotatorio vuelve a la voz global. El test inyecta un catálogo conocido y prueba también su ausencia.

## No portado / límites T3

- s26b/c-2 y `daemon_sweep_legacy`: no ejecutar el recorrido real de `/proc`. El guard actual comprueba presencia de `HERDR_TTS_SMOKE` en el entorno, no propiedad única de esta batería; podría alcanzar otra sesión marcada. Se sustituye solo el fallback en el caso de stop; ningún PID ajeno se señala.
- s26d/f (`daemon_restart`, spawn detached, espera de pidfile) y s26e (tecla R): fuera de la selección acotada de seis casos de lifecycle; evita crear sesiones detached. Supervisor y stop se ejercen por separado, no se afirma cobertura de restart.
- s29f: wiring de manifest/dispatch y startup/cleanup de `run_daemon` no portados; ejecutar ese loop tocaría locks watcher hardcoded en `/tmp`. INT y supervisor sin hijo no seleccionados; TERM con hijo, relaunch y log de stop sí probados. Lifecycle de `prune_snooze_state`/títulos queda fuera de esta selección.
- s27a: saneamiento del knob settle no añadido; s27b/c: sleeps, flipper y timeout relativos, omitidos por determinismo. Los casos existentes de watcher prueban anuncio con settle=0, no el settle temporal.
- s17a–f/j/k y s18c/l: todas las variantes init/emit/styles, lockstep de emit, modo JSON y textos de help no reproducidos. Los tres hard errors y negativas estructurales sí se seleccionan. No afirmar equivalencia con cada aserción legacy.
- s18f: no reproducir tres applies con sleeps de un segundo; probar prune con cinco backups de nombres absolutos deterministas. Sin cobertura específica de colisiones de timestamp/`$RANDOM`.
- s19b/c: no se reproduce el notice de autostart válido ni su rc en re-apply; la idempotencia del writer se prueba directamente. Archivo ausente y corrupto sí seleccionados.
- s31e/f: picker fzf y bind ctrl-v no portados (UI). Setter, CLI `off`, flags y resolución sí seleccionados.
- s1–16, s21, s25b–d, s27d/e, s28, s32, s36: rendering, overlays, títulos, previews, palette/menu/settings e i18n y sus controles fuera de alcance. Themes también fuera de alcance, sin atribuir bloques s38/s42 que no aparecen en la búsqueda del snapshot legacy. No equivalen a gating probado.
- s14/15, s23/24: history y retención de audio fuera de alcance; s22 playback no portado de nuevo: el harness host actual ya ejercita passthrough identificado y rechazo de archivo ausente, sin afirmar cobertura del mutex completo.
- s33/s34: bootstrap/install antiguo obsoleto; conservar tres casos bootstrap actuales. s35 skill install y s37 layer audit: fuera de alcance. s39–41/s43 reader popup/pipeline/auto-open: fuera de alcance.
- No modificar `matrix/bash-decisions.json`; batería funcional adicional, sin inventar porcentaje de cobertura de branches.

## Evidencia de verificación
Todos los runners se ejecutaron en foreground desde el worktree F3. Para los comandos relativos a `tests/`, cwd = `hosts/herdr/tts-plugin`; usar cwd equivale al `cd ... &&` solicitado sin acceder a otro worktree.

| Comando | Resultado observado |
|---|---|
| `TMPDIR=/tmp/opencode/f3-runtime PYTHONDONTWRITEBYTECODE=1 bash tests/all_bash_harnesses.sh` (antes de cambios) | 29 host + 3 bootstrap OK; 0 FAIL |
| Mismo comando, con F3 conectado | **29 host + 40 critical + 3 bootstrap = 72 OK, 0 FAIL**, rc 0 |
| `TMPDIR=/tmp/opencode/f3-runtime PYTHONDONTWRITEBYTECODE=1 bash tests/critical_cases.sh` | GREEN inicial 40/0; tres repeticiones consecutivas posteriores 40/0, 40/0, 40/0, rc 0; mismo conjunto y orden de veredictos `CASE` |
| `TMPDIR=/tmp/opencode/f3-runtime PYTHONDONTWRITEBYTECODE=1 RED_PROOFS=1 bash tests/critical_cases.sh` | 6 controles RED/ GREEN observados; copias sintácticamente válidas, rc del modo 0; detalle abajo |
| `bash -n hosts/herdr/tts-plugin/tests/host_cli_cases.sh` | rc 0 |
| `bash -n hosts/herdr/tts-plugin/tests/critical_cases.sh` | rc 0 |
| `bash -n hosts/herdr/tts-plugin/tests/all_bash_harnesses.sh` | rc 0 |
| `shellcheck hosts/herdr/tts-plugin/tests/critical_cases.sh hosts/herdr/tts-plugin/tests/all_bash_harnesses.sh` | rc 0; SC2016 documentado como expansión intencionada en el hijo / needles literales |
| `shellcheck` sobre los tres scripts editados | rc 1, diagnósticos host preexistentes; nueva instrumentación corregida sin tocar aserciones antiguas |
| `git show HEAD:hosts/herdr/tts-plugin/tests/host_cli_cases.sh \| shellcheck --format=json - \| python3 -c 'import collections,json,sys; rows=json.load(sys.stdin); print("Baseline host diagnostics:", dict(collections.Counter(r["code"] for r in rows)))'` | HEAD: SC2329 ×32, SC2016 ×12, SC1010 ×5 |
| `shellcheck --format=json hosts/herdr/tts-plugin/tests/host_cli_cases.sh \| python3 -c 'import collections,json,sys; rows=json.load(sys.stdin); print("Current host diagnostics:", dict(collections.Counter(r["code"] for r in rows)))'` | Archivo editado: mismos códigos y cantidades que HEAD; sin nuevos diagnósticos host |
| `python3 -c 'import yaml; data=yaml.safe_load(open(".github/workflows/ci.yml")); job=data["jobs"]["plugin-bash-tests"]; assert job["runs-on"] == "ubuntu-latest"; assert job["steps"][-1]["run"] == "bash hosts/herdr/tts-plugin/tests/all_bash_harnesses.sh"; print("YAML parsed; Ubuntu Bash job and command verified")'` | YAML parseado; job y comando confirmados, rc 0. CI remoto **no ejecutado** |
| `git status --short` | Solo los cinco paths autorizados: CI, wrapper y host modificados; critical nuevo y documento de tarea preexistente sin tracking |
| `git diff --stat` | Tres archivos tracked: 37 inserciones, 5 borrados; los dos untracked no aparecen en este comando |
| `git diff --check` | rc 0; sin errores de whitespace en el diff tracked |
| `find hosts/herdr/tts-plugin odd/tasks -type d \( -name __pycache__ -o -name .f3-runtime \) -print` | Sin resultados; no directorios cache/runtime en las superficies de tests/documentación |
| `rg -c '^' hosts/herdr/tts-plugin/tests/critical_cases.sh odd/tasks/f3-plugin-critical-tests.md && rg -c '^case__' hosts/herdr/tts-plugin/tests/critical_cases.sh` | Harness: 757 líneas, 40 casos; documento: 107 líneas en ese checkpoint (antes de añadir estas filas) |

No hubo fallos de tests preexistentes ni se usó otro worktree para comparar: la ejecución inicial fue antes de cualquier edición. Los FAIL de las copias RED son intencionados; no son bugs observados del launcher real.

### Controles RED por familia

| Familia / caso | Mutación aislada en copia | RED observado / GREEN real |
|---|---|---|
| gate / `critical_gate_mute` | Invertir condición muted del gate | FAIL (rc esperado 0, obtenido 1); OK real |
| toggle / `critical_mute_focus_roundtrip` | Unmute escribe true en lugar de false | FAIL; OK real |
| voces / `critical_voice_precedence` | Lookup pane usa un ID inexistente | FAIL; OK real |
| keymap / `critical_keymap_failed_check_rolls_back` | Sustituir el mv de rollback por no-op | FAIL en comparación de bytes; OK real |
| daemon / `critical_daemon_supervisor_stop_flag` | Invertir presencia del stop flag | FAIL; OK real |
| config / `critical_config_upsert_dedupes` | Escribir cada duplicado en vez de solo la primera ocurrencia | FAIL en comparación del rewrite; OK real |

Cada copia está en `/tmp/opencode/f3-runtime/red/`; `bash -n` pasó antes del control. `HERDR_TTS_TEST_LAUNCHER` cambia solo el objetivo del harness. No se modifica ni ejecuta `_daemon` del launcher: el supervisor usa children stub. Las copias y outputs RED quedan para cleanup por el orquestador (restricción de no borrado terminal del writer).

### Estado de máquina: prueba limitada, no certificación global

Se ejecutó antes y después el comando pedido `ls -l /tmp/herdr-tts-* ~/.config/herdr 2>&1 | head` (mediante `bash -c` para el glob). Las nueve entradas no-snooze del listado mantuvieron tamaños/mtimes; **snooze pasó de 125 bytes / Oct 6 00:11 a 179 bytes / Oct 6 00:29 (intermedio) y 199 bytes / Oct 6 00:46 (final)**. Por tanto el listado **no es idéntico** y no permite declarar cumplida la prueba global. El `head` tampoco incluye el contenido de `~/.config/herdr`.

Readback adicional solo de metadatos: `ls -ld /home/bruno/.config/herdr && ls -l /home/bruno/.config/herdr`. `config.toml`: 8.463 bytes, mtime Oct 5 14:37 (anterior al trabajo). Logs/session y mtime del directorio son de esta sesión: no son evidencia de config quieta ni se inspeccionan sus contenidos. No se detiene ni señala ningún daemon ajeno para conseguir un snapshot artificialmente estable.

Contención verificable en código: cada source crítico valida rutas resueltas bajo `$SB`; todos los procesos usan HOME/XDG, snooze, daemon PID/log/flag, voices, keymap, config y playback en el sandbox, con entorno vacío y externals stub. Los casos host antiguos también pinnean los archivos de gating/config, HOME y XDG; el caso que comprueba defaults playback solo imprime strings, no abre esos paths.

Verificación final repetida tras los guards de contención y de orden flag-before-kill: batería 72/0; tres críticos consecutivos 40/0; seis RED + seis GREEN; `bash -n` crítico y ShellCheck crítico/wrapper rc 0. Todos los roots privados del harness se limpiaron mediante sus traps; el readback de `/tmp/opencode/f3-runtime/` muestra únicamente `red/`, pendiente de cleanup por el padre.

## Corrección de contención (2026-10-07, seguimiento del hallazgo independiente)

**Hallazgos confirmados contra el producto (sin modificarlo):** `watch_agent_pane` real usa `bin/herdr-tts:3355` `mktemp "/tmp/herdr-tts-synth-err.XXXXXX"` (plantilla absoluta que `TMPDIR` no redirige) y, ante fallo del renderer, `report_tts_error` añade a `/tmp/herdr-tts.log` hardcoded (`:209`). Además, `critical_cases.sh` usaba `timeout 15` sin kill-after y su cleanup esperaba sin límite a hijos que ignoran TERM.

**Corrección TEST-ONLY (producto intacto):**
- `run_watcher_announce` redefine tras el source, solo en ese sandbox, `mktemp` (acepta únicamente la plantilla del watcher y la redirige a `$SB/tmp/...`; cualquier otra plantilla falla rc 97) y `report_tts_error` (valida prefijo sandbox, registra la llamada y vuelca a `$SB/tts-errors.log`). Un tripwire rehusa ejecutar el reporter de producto mientras declare -f aún contenga `/tmp/herdr-tts.log`. Parámetro nuevo `$5` = rc del renderer para forzar la rama de error.
- `critical_cases.sh`: `timeout --kill-after=5 15` en `in_host`/`in_cli`; registro de procesos propios con identidad (pid + starttime de `/proc/<pid>/stat`); `finish_owned` ya no señaliza — falla si la operación del producto dejó un superviviente (oráculo de ciclo de vida sin rescate que enmascare fallos). *(Superseded: la ronda 2 abajo sustituye el descubrimiento recursivo por registro explítico y corrige la afirmación de inmunidad.)*

**TDD observado (sin escritura real en `/tmp`):** RED inicial con capa de observación segura (shim PATH que redirigía antes de allocar + preflight que bloqueaba el reporter global): `watcher_synth_scratch_is_sandboxed` FAIL (plantilla `/tmp/herdr-tts-synth-err.XXXXXX` registrada como insegura), `watcher_render_failure_log_is_sandboxed` FAIL (reporter global rehusado), `critical_harness_reaps_term_ignoring_child` y `critical_harness_reaps_stubborn_supervisor_child` FAIL de preflight. Tras la corrección: los 4 casos OK; el de error ejecuta la rama real `report_tts_error` con renderer forzado a fallar.

| Comando (cwd `hosts/herdr/tts-plugin`, `TMPDIR=/tmp/opencode/f3-runtime`, `PYTHONDONTWRITEBYTECODE=1`) | Resultado |
|---|---|
| `CASES='watcher_synth_scratch_is_sandboxed watcher_render_failure_log_is_sandboxed' bash tests/host_cli_cases.sh` | RED 0/2 → GREEN 2/2 |
| `CASES='critical_harness_reaps_term_ignoring_child critical_harness_reaps_stubborn_supervisor_child' bash tests/critical_cases.sh` | RED 0/2 → GREEN 2/2 |
| Familia daemon completa + 2 nuevos (8 casos) | 8 OK / 0 FAIL |
| `bash tests/all_bash_harnesses.sh` | **76 OK / 0 FAIL** (31 host + 42 critical + 3 bootstrap) |
| `bash tests/critical_cases.sh` ×3 consecutivos | 42/0, 42/0, 42/0 |
| `RED_PROOFS=1 bash tests/critical_cases.sh` | 6 RED + 6 GREEN (12 líneas RED/GREEN), rc 0 |
| `bash -n` en los dos scripts editados | rc 0 |
| `shellcheck critical_cases.sh all_bash_harnesses.sh` | rc 0 |
| `shellcheck` host editado vs `HEAD` | mismos códigos preexistentes; +2 SC2329 (info) por los 2 casos nuevos del mismo protocolo dinámico |

Casos: 31 host (29+2), 42 critical (40+2). El drift global previo del snooze sigue sin atribución; no se afirma estado global inmutable a partir de metadatos. El directorio runtime `/tmp/opencode/f3-runtime/` queda para cleanup del padre.


## Corrección de contención — ronda 2 (2026-10-07, hallazgos de revisión independiente)

**Hallazgos confirmados en la ronda anterior:** (1) el starttime se comprobaba ANTES del descubrimiento de descendientes y el TERM no revalidaba la raíz → ventana de pid reemplazado; (2) `stop_owned_tree` saltaba el traversal si el padre ya había salido → hueco de huérfanos; (3) los hijos se validaban solo por pid padre numérico; (4) el documento afirmaba «inmune a reciclaje de PID» — demasiado fuerte.

**Modelo final (TEST-ONLY, producto intacto):** sin descubrimiento recursivo de `/proc`. Solo identidades registradas explícitamente: hijos directos (`register_owned`, exige padre == shell) y descendientes de fixture que se anuncian vivos (`register_handshake_owned`: el fixture escribe «pid starttime» leído con `read -r < /proc/self/stat` SIN fork, y el registro exige coincidencia exacta contra `/proc` en ese momento). Cada señal revalida pid+starttime EN la frontera (`signal_owned`, rechazos registrados en `signal.log`). `cleanup_owned` fase única: TERM a todo el registro → espera/KILL acotados recorriendo el registro EN ORDEN INVERSO (los dependientes se registran después de su padre; un padre bloqueado en `wait(child)` queda liberado por la muerte del hijo y sale por su propio manejo de TERM, no por KILL de rescate que enmascararía el rc del oráculo) → reap. `finish_owned` nunca señaliza y falla si el superviviente sigue vivo; las aserciones de ciclo de vida del producto fallan ANTES del cleanup de rescate.

**Límite de plataforma, reconocido sin rodeos:** entre la lectura de `/proc/<pid>/stat` y `kill(2)` queda una ventana TOCTOU inherente a la señalización por pid sin pidfd; la comprobación pid+starttime no la cierra ni garantiza su duración. No se afirma inmunidad absoluta al reciclaje de PID. Los nombres `f3-*` son etiquetas diagnósticas, no pruebas de propiedad. La limpieza cubre fixtures registrados, no descendientes arbitrarios.

**Bug de fixture encontrado y corregido (raíz del flake intermitente):** `stat_rest=$(cat /proc/self/stat)` lee el stat del proceso `cat` FORKEADO, no del propio script: su starttime difiere del real siempre que el fork cae en un tick de reloj distinto (por eso fallaba ~10% bajo carga y 0% en reposo, y explica el «stale» transitorio de la ronda 1). Corregido con `IFS= read -r ... < /proc/self/stat` (builtin, sin fork) en ambos fixtures (stubborn y orphan). Durante la ronda también se encontraron y corrigieron: array disperso por `unset` indexado (unbound bajo `set -u` en el loop inverso), un apóstrofe en comentario que cortaba el string de `bash -c`, y el orden de KILL que escalaba al supervisor bloqueado antes de parar su hijo.

**Casos nuevos:** `critical_harness_cleanup_reaps_orphan_after_parent_exit` (RED observado con el modelo de la ronda 1: hijo huérfano sobrevivía y fugaba, auto-limitado a 5s; GREEN tras registro explícito) y `critical_harness_stale_identity_refuses_signal` (entrada muerta → no-op sin señal; identidad viva con birth erróneo → señal rehusada y el proceso sobrevive; identidad correcta → stop limpio; verde desde su introducción, sin RED fabricado).

| Comando (cwd `hosts/herdr/tts-plugin`, `TMPDIR=/tmp/opencode/f3-runtime`, `PYTHONDONTWRITEBYTECODE=1`) | Resultado |
|---|---|
| `CASES='critical_harness_cleanup_reaps_orphan_after_parent_exit' bash tests/critical_cases.sh` (modelo ronda 1) | **RED**: FAIL con hijo vivo tras cleanup (leak auto-limitado, pid observado) |
| Mismo caso, modelo final (registro explícito) | GREEN |
| Lote de 3 casos de harness-safety ×15 iteraciones | 45/45 OK, sin procesos residuales `f3-*` |
| Batería completa `bash tests/all_bash_harnesses.sh` | **78 OK / 0 FAIL** (31 host + 44 critical + 3 bootstrap) |
| `bash tests/critical_cases.sh` ×3 consecutivos | 44/0, 44/0, 44/0 |
| `RED_PROOFS=1 bash tests/critical_cases.sh` | 6 RED + 6 GREEN (12 líneas), rc 0 |
| `bash -n` en los scripts editados | rc 0 |
| `shellcheck tests/critical_cases.sh tests/all_bash_harnesses.sh` | rc 0 |
| `shellcheck` host editado vs `HEAD` | códigos preexistentes iguales; +2 SC2329 (info, protocolo dinámico de casos) |
| `pgrep -af '^f3-'` tras todas las corridas | sin procesos residuales |

Casos: 31 host, 44 critical (42+2), 3 bootstrap = 78. El drift global previo del snooze sigue sin atribución; no se afirma estado global inmutable a partir de metadatos. El directorio runtime `/tmp/opencode/f3-runtime/` (incluye repros de diagnóstico) queda para cleanup del padre.

## Siguiente paso

Verificación independiente final: batería completa **78 OK / 0 FAIL** y regresiones de identidad obsoleta y padre terminado **2 OK / 0 FAIL**. Veredicto técnico PASS con las limitaciones documentadas; CI remoto no ejecutado. El oráculo de huérfano prueba ausencia de proceso vivo, no `waitpid` sobre un no-hijo adoptado. RDD desactivado por preferencia clone-local, sin revisión nativa.

F3 verificada localmente (T1–T4 cerrados, batería conectada a CI). Las correcciones posteriores a `6838f29` permanecen sin commit. No se ha realizado push. F4 (`AT-02` reenfocada + `HT-01`) es el siguiente desarrollo de producto; F2 sigue pendiente de prueba física.

## Delivery strategy

- Human selected option 1: `ask-on-risk` with `stacked-to-main`. Each review slice targets `main`; dependent slices wait for their predecessors. No PR creation, push, or merge is authorized by this selection.
- Existing work-unit boundary: `6838f29` (critical test net and CI, 914 authored changed lines). The existing commit is retained; no history rewrite or size exception is inferred. A bounded cohesive slicing pass is still required before preparing PRs.
- Pending work unit: sandbox containment and owned-fixture cleanup corrections, with the observed independent 78/0 verification and this task record. Current tracked diff before this strategy entry: 389 authored changed lines.
- One-time commit exception: the maintainer explicitly authorized this local F3 correction commit outside the usual time window on 2026-10-07 at approximately 01:47 CEST. Use the real clock; no date fabrication, push, or PR is authorized. The normal time policy remains unchanged for later commits.
