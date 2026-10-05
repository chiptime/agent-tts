# F1 — Reconciliación documental (base veraz)

**Feature id:** `f1-doc-reconciliation` · **Worktree:** `agent-tts-worktrees/roadmap` · **Branch:** `docs/voice-stack-roadmap` (from `main` @ `5e833fe`)
**Fuente:** `docs/voice-stack/ROADMAP.md` §4-F1 y `docs/voice-stack/RECONCILIATION-PLAN.md` (R1, R2).

## Objetivo
Ningún documento declara pendiente algo ya ejecutado, ni ejecutado algo pendiente. Solo documentación (`.md`).

## Alcance autorizado
Solo ficheros Markdown listados en el writer prompt (allowed edit surfaces). Sin código, tests ni configuración. **Sin commits**: política de horas del maintainer (lun-jue después de 19:00 España; vie desde 16:00; fin de semana libre). Se deja el árbol modificado para commit posterior.

## Restricciones
- Estado real verificado contra código/git, no contra otros docs.
- Idioma: el de cada documento (español). Sin comentarios de persona.
- Decisiones D1-D5 en `ROADMAP.md` §3 son vinculantes (AT-02 reenfocada, HT-01 viva, HT-04 viva-reenfocada, HT-05 backlog sobre `pending_queue.py`).

## Tareas
- [x] **T1** R1.1 Cabeceras de AT-01, AT-03, AT-04, AT-08, AT-09, AT-10: `EJECUTADA` + fecha + commit de referencia.
- [x] **T2** R1.2 Fichas de bloques (BLOQUE-1 y 1.x en `docs/prds/archivadas/bloques/`; BLOQUE-2/3/4 en `hosts/herdr/tts-plugin/docs/prds/bloques/`).
- [x] **T3** R1.3 Índices: `docs/prds/README.md`, `hosts/herdr/tts-plugin/docs/prds/README.md` (si procede) y `docs/voice-stack/README.md`.
- [x] **T4** R1.4 Estados stale: brain `PRD-action-approval-gate.md`, `odd/tasks/action-approval-gate.md`, `call-history-persistence.md`; `odd/tasks/bloque-1.3-cola-y-cadena.md`.
- [x] **T5** `EXECUTION.md`, `TASKS.md`: reflejar `automated_complete` (2026-10-01) y retirar marcadores `[FUTURE]` cumplidos; `RECONCILIATION-PLAN.md`: nota de que `ROADMAP.md` define el orden.
- [x] **T6** Notas de destino D1: AT-02 `REENFOCADA`, HT-01 `VIVA`, HT-04 `VIVA, REENFOCADA`, HT-05 `BACKLOG`.
- [x] **T7** Verificación estructural: grep de estados obsoletos, enlaces relativos válidos, `git diff --stat` solo `.md`.

## Ruta y evidencia de delegación
- Ruta: **delegated direct** (un escritor). Trigger: >2 ficheros no triviales + lectura previa de preparación.
- TDD: no aplica (documentación pasiva); verificación = lectura estructural + grep.
- Disposición y ciclo de revisión: propiedad del orquestador. El escritor solo aporta implementación documental y verificación estructural; no ejecuta herramientas de review.

## Progreso
Creado y reconciliado el 2026-10-05 en `docs/voice-stack-roadmap`. T1–T7 terminadas dentro de las 28 rutas Markdown autorizadas; sin código ni operaciones de entrega Git. El checkout principal no se ha inspeccionado ni modificado. Espejo de conclusiones verificadas: Engram, proyecto `agent-tts` (sin copiar el documento completo).

| Tarea | Resultado y evidencia de fuente |
|---|---|
| T1 | Cabeceras cerradas: AT-01 `c2b8175`/`a3ee900` (29/09); AT-03 `3bb5e1b` (28/09, solo alcance OpenCode); AT-04 `43bf5d8`/`8e9ce26`/`ce49cdf` (24/09); AT-08 `3a9c59e`/`ec1b541`/`7231f9e`/`0d3f340` (25/09); AT-09 `05624a1`/`16f11aa` (23/09, refuerzo `1dde8f2` el 24/09); AT-10 `d53a333`/`9454732`/`a3f9f3e`/`90db9b9`/`29429e3` (28/09). Fechas y hashes contrastados con Git; código correspondiente leído. |
| T2 | BLOQUE-1 y 1.x completados en el alcance acordado, sin certificar métricas/residuales. BLOQUE-2 parcial: `resolve_voice`/`voice_map_set` implementados (`d94edf7`), picker sin preview HT-11. BLOQUE-3 sin ejecutar: ntfy solo view/copy, sin recordatorios escalados. BLOQUE-4 parcial: AT-03 cerrada, sin comando/configuración radio. |
| T3 | Tres índices con las 20 originales y extras, puntero al ROADMAP. AT-11 parcial: M1 y tarea 2.1 (`c577041`) hechas; 14 tareas restantes. HT-14 se describe config-only, no como knob de Apariencia entregado. |
| T4 | Approval: rutas implementadas (`server.py`, `2abb17e`), popup flotante (`22e2fc2`), tests presentes; T8/Chrome Android abierta. Historial SQLite y GET paginado presentes (`856d857`, `89ba080`). BLOQUE-1.3 integrado, sin duplicado pendiente T8; registro previo conservado como historia. |
| T5 | Banners de cierre según MANUAL-TESTS (01/10), distintos de la fecha de commits (03/10). Rutas de scripts/tests comprobadas con `ls` antes de retirar marcadores. Contrato DRAFT y solicitud §1 intactos; se conservan marcadores genéricos históricos, runner opcional sin ruta e instalación del tooling no verificada. Plan de reconciliación conservado con nota superior. |
| T6 | Cuatro notas de destino del 05/10: AT-02 cliente del STT brain; HT-01 viva; ambas con on/off. HT-04 atajo sobre brain, sin listener propio. HT-05 backlog separado de VS3, sobre `pending_queue.py`, con reset dependiente de HT-01/04. |
| T7 | Solo Markdown permitido modificado; dos untracked preexistentes conservados. Enlaces nuevos con destino existente. Los 13 estados hallados son diferidos válidos o historia fechada; la evidencia del comando añade una autocoincidencia (14 resultados), no una cabecera falsa. |

### Segunda pasada (2026-10-05)

Reconciliadas las cabeceras y notas pendientes de la primera pasada, dentro de las nueve rutas autorizadas para esta continuación. Se conservan el registro previo, los cuerpos de PRD, D1–D5 y el orden de fases; en los índices solo cambian celdas de estado/prioridad. Se resuelven las cinco cabeceras señaladas y AT-11 en F7; el inventario previo a F1 de ROADMAP §1 permanece intacto, fuera de los cambios pedidos. La lista de «Límites y documentos fuera de superficie» se mantiene como registro histórico.

| Tarea de segunda pasada | Resultado |
|---|---|
| 1 | AT-11: PARCIAL; M1 completado y tarea 2.1 hecha (`af8680d`, `c577041`, 2026-10-02). Recuento: 12 tareas hechas de 26, 14 pendientes (5 en M2, 5 en M3, 4 en M4). El «Siguiente paso» original se identifica como previo al inicio. |
| 2 | Brain: PARCIAL para el conjunto de la PRD; contexto e informes implementados (`d7de194`, `2cb3a50`), bucle autónomo FR-42..45 futuro, no habilitado. Nota fechada con enlaces al registro y cobertura; cuerpo intacto. |
| 3 | HT-02: EJECUTADA desde el 2026-09-22 (`d94edf7`); asignación persistente verificada. HT-11 sigue pendiente como compañera de audición, con prioridad baja según D4. |
| 4 | HT-11: prioridad baja (D4, 2026-10-05), F6; AT-02/HT-01 antes. La nota del 22/09 permanece como historia. |
| 5 | HT-14: EJECUTADA en alcance config-only (`TTS_THEME`); Apariencia retirada el 2026-09-24 (`e75d55e`), adopción del tema en `d0de97d`. El archivo del 23/09 refleja el diseño anterior, no un selector actual. |
| 6 | ROADMAP §4-F7: M1 hecho, M2 parcial por tarea 2.1 y 14 tareas pendientes; sin cambios de decisiones ni secuencia. |
| 7 | Ambos índices coherentes con las cinco PRDs; preservados los cambios de primera pasada y el resto del árbol. No se inspecciona ni modifica el checkout principal. |

### Límites y documentos fuera de superficie (sin editar)

- `docs/voice-stack/ROADMAP.md`: §1 conserva huecos anteriores a F1; F7 dice M2–M4 pendientes aunque la tarea 2.1 está implementada. Propuesta al orquestador: anotar F1 reconciliada y precisar AT-11 como M2 parcial, sin sustituir el resto del roadmap.
- `docs/prds/AT-11-instalable-first-run.md`: cabecera aprobada y nota «el desarrollo NO arranca todavía» anteriores a M1/2.1.
- `docs/prds/herdr-brain-on-demand-context.md`: DRAFT previo al contexto implementado; distinguir esa entrega del bucle autónomo aún no habilitado.
- `hosts/herdr/tts-plugin/docs/prds/HT-02-voces-por-agente.md`: cabecera Aprobada aunque identidad vocal integrada.
- `hosts/herdr/tts-plugin/docs/prds/HT-14-tema-claro.md`: Borrador y propuesta de Apariencia, frente a implementación config-only.
- `hosts/herdr/tts-plugin/docs/prds/HT-11-audicion-voces.md`: prioridad histórica P1; actualizar a baja según D4 en una tarea autorizada posterior.
- `docs/voice-stack/MANUAL-TESTS.md`: instrucciones históricas de worktree/canónico v1 y commits pendientes, anteriores a la integración del stack.

No se repiten suites, mediciones, installs ni pruebas de teléfono en documentación pasiva. No se abre la base de datos personal OpenCode para repetir AT-03, ni historiales personales, artefactos locales del run-dir o remotos para recertificar AT-10. Los pendientes físicos, métricas edge/kokoro/8 h y cobertura Windows real conservan su condición documentada; F1 no los cierra.

## Evidencia de verificación
Verificación de fuente: `git log --date=short --format='%h %ad %s' --regexp-ignore-case --extended-regexp --grep='AT-01|AT-03|AT-04|AT-08|AT-09|AT-10|stream.*frame|monorepo|cola|daemon|approval|history|podcast|on-demand|voice-stack|VS[0-4X]|ht-(02|13|14|15|16)'` y logs por rutas; lectura de módulos y tests, sin ejecutarlos. CodeGraph no tiene índice en este worktree; lectura acotada como alternativa, sin crear índice.

`ls` de las 28 rutas de scripts/tests referenciadas: todas presentes, exit 0. TDD no aplica: documentación pasiva; verificación proporcional de fuente, lectura y estructura, sin RED/GREEN de comportamiento.

| Comando (cwd: worktree roadmap) | Resultado observado |
|---|---|
| `git status --short` | 27 Markdown tracked modificados, todos autorizados; solo `ROADMAP.md` y este task doc como untracked preexistentes. ROADMAP no editado. |
| `git diff --stat \| tail -40` | 27 ficheros, todas las rutas `.md`; salida mostrada al orquestador. El diff no incluye este documento untracked, revisado por lectura. |
| `git diff --name-only \| grep -v '\.md$'` | Sin salida: ninguna ruta no Markdown (ausencia de coincidencias es el resultado esperado). |
| `grep -rn "Postergada\|planning pending\|NOT started\|En curso" docs/prds hosts/herdr/tts-plugin/docs hosts/herdr/brain/docs hosts/herdr/brain/odd/tasks odd/tasks \| head -40` | 14 resultados: 13 estados clasificados abajo y este comando (autocoincidencia); ninguno oculto por el límite 40. |
| `ls docs/prds/{../voice-stack/{ROADMAP,README}.md,herdr-brain-on-demand-context.md,../../hosts/herdr/{tts-plugin/docs/prds/README,brain/docs/PRD-action-approval-gate}.md} hosts/herdr/tts-plugin/docs/prds/../../../../../docs/{voice-stack/ROADMAP,prds/README}.md docs/voice-stack/{MANUAL-TESTS.md,ROADMAP.md,../prds/README.md,../../hosts/herdr/tts-plugin/docs/prds/README.md}` | Exit 0; 11 resoluciones de rutas, cubren los siete destinos de todos los enlaces relativos nuevos. |
| `git diff --check` | Sin salida, exit 0; sin errores de espacios en el diff tracked. |

Clasificación de los 14 resultados: tres son historia conservada (nota de revisión AT-01, nota AT-02 del 22/09, revisión antigua del índice HT); diez son el backlog válido AT-05 y HT-06/07/08 en PRDs e índices; uno reproduce el patrón del propio comando de verificación. Se mantienen expresamente: ROADMAP difiere esas features y F1 no las ejecuta.

Marcadores restantes en EXECUTION/TASKS: cabeceras originales históricas, comentario genérico del pseudocódigo, runner opcional sin ruta y `[FUTURE-instalada]` de VS0.3 (instalación no verificada en F1). Ninguna fila con una ruta concreta comprobada conserva `[FUTURE]`/`[FUTURE-tests]`.

### Segunda pasada (2026-10-05)

- Base observada antes de editar: branch `docs/voice-stack-roadmap`, raíz `agent-tts-worktrees/roadmap`, 27 Markdown tracked modificados y dos untracked preexistentes (`ROADMAP.md` y este documento).
- AT-11: lectura completa del plan de tareas y recuento con `rg -c '^- \[x\] [1-4]\.[0-9]+ ' openspec/changes/at-11-instalable/tasks.md && rg -c '^- \[ \] [1-4]\.[0-9]+ ' openspec/changes/at-11-instalable/tasks.md`: 12 y 14, exit 0. Contraste con `scripts/install.sh`, `scripts/bootstrap.sh`, `tools/herdr_onboarding/resolve.py`, resolvers del launcher y Git (`af8680d`, `c577041`). No se confunden tareas funcionales con slices de revisión.
- HT-02/HT-11: código de `resolve_voice`, `voice_map_set`, `run_voice_for_picker` y sus llamadas leído; el picker permite aplicar/cancelar, no preview de navegación. Fecha y entrega de HT-02 contrastadas con Git (`d94edf7`).
- HT-14: `config_set`, `theme_sync`, `theme_color` y `settings_render` leídos; la categoría no está en el render actual. El diff de `e75d55e` retira dispatch, render e i18n de Apariencia; `d0de97d` conserva adopción del tema. Se leyó el archivo del 23/09 sin tomar su snapshot como estado actual.
- Brain: `ConsultService` compone providers, `ReportStore`, `FreshnessChecker`, `QueryEngine` y `FollowupStore`; `server.py` lo conecta a `BrainTools`. Lectura del registro ODD y cobertura contrastada con el código y logs de `d7de194`/`2cb3a50`; contexto entregado no equivale a bucle autónomo de implementación.
- CodeGraph confirmó ausencia de índice en este worktree; lectura acotada como alternativa, sin crear índice. TDD no aplica a documentación pasiva; no se ejecutan suites, instaladores, servicios, pruebas físicas ni evaluaciones con modelo real, y no se recertifican resultados históricos.

Comandos de verificación ejecutados por separado, en primer plano y desde el worktree `roadmap`:

| Comando | Resultado observado |
|---|---|
| `git status --short` | 32 Markdown tracked modificados (27 de primera pasada + cinco PRDs); mismos dos untracked preexistentes. Esta pasada solo edita las nueve rutas autorizadas; resto conservado. |
| `git diff --name-only \| grep -v '\.md$'` | Sin salida: ninguna ruta tracked no Markdown; ausencia de coincidencias esperada. |
| `git diff --check` | Sin salida ni errores de espacios en el diff tracked. |
| `git cat-file -e af8680d^{commit}` | Éxito, sin salida. |
| `git cat-file -e c577041^{commit}` | Éxito, sin salida. |
| `git cat-file -e d94edf7^{commit}` | Éxito, sin salida. |
| `git cat-file -e d0de97d^{commit}` | Éxito, sin salida. |
| `git cat-file -e e75d55e^{commit}` | Éxito, sin salida. |
| `git cat-file -e d7de194^{commit}` | Éxito, sin salida. |
| `git cat-file -e 2cb3a50^{commit}` | Éxito, sin salida. |
| `ls docs/prds/../../{openspec/changes/at-11-instalable/tasks.md,hosts/herdr/brain/{odd/tasks/herdr-brain-on-demand-context.md,docs/on-demand-context-coverage.md}} hosts/herdr/tts-plugin/docs/prds/{HT-11-audicion-voces.md,../../openspec/changes/archive/2026-09-23-ht-14-light-theme/archive-report.md}` | Cinco destinos presentes; cubre todos los enlaces relativos nuevos de esta pasada. |

`ROADMAP.md` y este documento siguen sin trackear; se revisan por lectura, no se incluyen en `git diff --check`. Ninguna verificación estructural requerida se omite. Árbol sin commit, stage, push, stash ni cambios de configuración Git.

Documentos fuera de superficie aún pendientes: `docs/voice-stack/MANUAL-TESTS.md` conserva arranque en el worktree antiguo y canónico v1-only (frente a integración del stack en Git); `hosts/herdr/brain/odd/tasks/herdr-brain-on-demand-context.md` conserva T4 sin marcar aunque el adapter está implementado y conectado. Solo se reportan, sin editar.

## Siguiente paso
Entregar el árbol modificado, sin commit, al orquestador. Revisar los documentos fuera de superficie listados y avanzar a F2/F3 según ROADMAP; resolver el verbo de inyección de HT-01 al planificar F4. La revisión y cualquier entrega posterior son decisiones del maintainer.
