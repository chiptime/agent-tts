# EXECUTION — Contrato de ejecución del bucle voice-stack

> **Estado: DRAFT.** Define CÓMO ejecutar TASKS.md. No autoriza nada por sí mismo: la
> autorización la da el usuario pegando §1. **No hay runtime instalado**: este contrato lo
> ejecuta la sesión futura leyéndolo y obedeciéndolo; los scripts FUTURE de TASKS.md no
> existen hasta que su tarea los crea. No crea estado SDD; si el usuario elige SDD, corre
> su preflight nativo aparte.

## 1. Solicitud copiable (el usuario la pega SOLO si decide autorizar)

```text
Arranca en /home/bruno/Code/personal/agent-tts (monorepo canónico). Lee completos, antes de
tocar nada, en este orden:
  docs/voice-stack/README.md
  docs/voice-stack/EXECUTION.md
  docs/voice-stack/TECHNICAL-PLAN.md
  docs/voice-stack/TASKS.md
  docs/voice-stack/prds/01..04 (los cuatro)

Apruebo los requisitos de producto D1–D9 (DRAFT) del paquete voice-stack y las decisiones
técnicas LOCKED de TECHNICAL-PLAN.md y TASKS.md, y te autorizo a ejecutar TASKS.md
localmente (VS0→VS1→VS2→VS3→VS4→VSX) SOLO dentro del monorepo, bajo este contrato. Con
esto quedan autorizadas de antemano la implementación local, la instalación de la
instrumentación/dependencias de test ahí especificada (versiones fijadas por lockfile y
registradas en evidencia) y las ejecuciones rutinarias de tests locales (pronóstico
informativo previo para suites largas; aprobación extra sólo si aparece coste o efecto
secundario NUEVO no cubierto aquí). NO quedan autorizados: red de pago/proveedores
reales/credenciales nuevas; commits, push, PR o merge (pídelos aparte y expresamente);
review nativa cambiada o bypasseada. En el camino feliz NO me vuelvas a preguntar
decisiones de producto: todo lo trazarable a D1–D9/T§/TASKS es ejecutable; una decisión
REAL nueva = parada según §3. El modo de review es mío: léelo tal cual, no lo cambies ni
lo actives; detente sólo ante consentimiento humano real exigido por el contrato nativo
activo; sigue §7 en todo. NO requiero commit de la paquetería: su identidad es el
inventario+hashes de §4.1. No crees rama SDD nueva salvo que yo la elija explícitamente.
Antes de editar código, ejecuta la Fase 0 (§2) tal cual.
```

## 2. Fase 0 — verificación de entorno (obligatoria, read-only, antes de editar)

### 2.0 Bootstrap VS0.B (PRIMERO, antes de CUALQUIER escritura de repo — resuelve la circularidad)

`snapshot.py`/`runlock.py` son FUTURE (los crean VS0.1/VS0.2): la Fase 0 NO puede
exigirlos. El bootstrap usa un snippet inline **sólo-stdlib de Python** (texto pasivo de
este documento; no es un fichero de script ahora) que crea el run-dir machine-local, el
inventario inicial y el lock de ejecución:

```bash
RUN="$HOME/.local/state/voice-stack-runs/$(date -u +%Y%m%dT%H%M%SZ)-vs0b"
mkdir -p "$RUN"
python3 - "$RUN" <<'PYEOF'
import hashlib, json, os, secrets, sys, time
run = sys.argv[1]
ROOTS = ["docs/voice-stack", "engine", "hosts/herdr/brain", "hosts/herdr/tts-plugin", "contracts", ".github/workflows"]
EXCL = {".git", ".venv", "__pycache__", "node_modules", "dist", ".pytest_cache", "private", "secrets", "credentials"}
GENERATED = {".coverage", "coverage.json", "lcov.info"}
inv = []
for root in ROOTS:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCL]
        for f in sorted(filenames):
            if f in GENERATED or f.startswith((".env", ".coverage")) or f.endswith((".pem", ".key", ".p12")):
                continue
            p = os.path.join(dirpath, f)
            b = open(p, "rb").read()
            inv.append({"path": p, "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest(),
                        "mode": oct(os.stat(p).st_mode & 0o777)})
lock = os.path.join(os.path.dirname(run), "run.lock")
try:
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)   # existe => OWNED: STOP
except FileExistsError:
    print("LOCK_EXISTS_STOP"); sys.exit(3)
token = secrets.token_hex(32)
os.write(fd, json.dumps({"pid": os.getpid(), "started_ts": time.time(),
                         "token": token, "session": "voice-stack"}).encode()); os.close(fd)
json.dump({"package_and_candidate_scopes": sorted(inv, key=lambda x: x["path"])},
          open(os.path.join(run, "bootstrap-inventory.json"), "w"), indent=1)
print("OWNER_TOKEN=" + token)
PYEOF
```

Reglas del bootstrap: `$RUN` queda definido UNA vez aquí y todos los comandos del paquete
lo usan tal cual. El inventario cubre docs + scopes candidatos de
producción/tests/config/fixtures/deps, tracked Y untracked (bytes/modos), SIN secretos
(los ficheros listados son código/docs del repo). `snapshot.py` NO se invoca aquí — sólo
tras completar VS0.1. La identidad del PAQUETE para README/checkpoints = los hashes de
`docs/voice-stack/` de este inventario (metadatos de proceso: informativos). **No se
inventa session ID de runtime ni helper persistente: el lock es un fichero, el bucle es
esta sesión.** VS0.2 (`runlock.py`) MIGRA validando el token del bootstrap — NO readquiere.

### 2.1–2.6 Pasos de Fase 0

1. Leer TODO el paquete (orden §1).
2. **Identidad del paquete:** verificar el inventario del bootstrap contra el árbol
   (§4.1). El paquete NO requiere commit; si el árbol difiere del inventario registrado en
   el último checkpoint válido ⇒ los gates ligados al paquete se re-vinculan; nunca se
   continúa en silencio con hashes viejos.
3. **Puerta D8 (prerrequisito externo):** la reconciliación standalone→canónico es trabajo
   EXTERNO paralelo, ajeno a este bucle. Validación read-only: (a) inspeccionar estado
   vigente (`git status/diff/log` del monorepo; los standalone pueden NO existir — su
   ausencia NO es error ni orden de recreación); (b) confirmar que no hay escritor activo
   en el árbol fuente (WIP/ramas en curso que toque los allowlists); (c) la COMPLETION de
   D8 la confirma el usuario/propietario externo con evidencia — jamás se deduce de la
   ausencia de conflictos. Si está en curso, conflicta, es incierto o hay otro escritor:
   STOP y reporte del prerrequisito. PROHIBIDO: merge/cherry-pick/copy/redeploy/
   normalización de standalones por cuenta propia; ante desalineación runtime/fuente:
   escalar, no reparar. Conflicto que exija cambiar comportamiento = parada (decisión).
4. `git status` + `git diff` + `git log --oneline -10`; el WIP existente es evidencia del
   estado: no se revierte ni se pisa; se respeta o se escala.
5. Verificar runners/config vigentes (los de README "Hechos verificados" son snapshot:
   re-anclar citas que hayan cambiado, sin editar código para "arreglarlas").
6. El lock del bootstrap (§2.0/§5) debe estar TOMADO por esta sesión. Sin lock, no hay bucle.

## 3. Bucle por tarea (autoridad operativa)

Pseudocódigo vinculante:

```
validar_lock_ya_tomado_por_bootstrap(§5)   # NO adquirirlo otra vez
para cada tarea T en orden DAG de TASKS.md (dep satisfechas):
    snapshot(§4.1) -> binding_previo
    editar SOLO allowlist de T
    ejecutar gate(T) con cwd exacto        # [FUTURE] scripts sólo tras su tarea VS0
    evidencia -> run_dir/tasks/T/          # log cmd+salida+binding hash
    si gate FAIL y rondas_remediacion(hito(T)) < 2:
        rondas++ ; remediar DENTRO del allowlist ; re-ejecutar gate(T)
    si gate FAIL y techo agotado: estado=blocked ; pasar a §3-stop
    si gate BLOCKED (verificación no medible): estado=blocked ; §3-stop
    checkpoint(§4.3) actualizado (puntero a T, rondas, binding)
cerrar_hitos: VSX re-ejecuta TODOS los gates sobre bytes finales re-vinculados (§6)
en salida normal, fallo o bloqueo: guardar_checkpoint_y_liberar_solo_lock_propio
```

- **Presupuestos DUROS:** ≤2 rondas de remediación funcional EN TOTAL por hito
  (VS1/VS2/VS3/VS4 contadas INDEPENDIENTEMENTE) y ≤2 rondas para VS0 completo. El contador
  vive en el checkpoint; NO se reinicia por cambiar el fallo de nombre ni al reanudar
  sesión. Los presupuestos de actores nativos (review, SDD, proveedores) son separados,
  les pertenecen a ellos y NUNCA se reinician desde aquí.
- **Estados:** por tarea `pass|fail|blocked`; por hito y global
  `complete|partial|blocked`. Ningún gate requerido fallado/omitido permite `complete`;
  gate no aplicable = pregunta al usuario, no silencio.
- **Reutilización de evidencia:** un gate sólo se reutiliza si su binding (§4.2) está
  íntegro; cualquier byte/config/entorno pertinente cambiado ⇒ re-ejecutar los gates
  dependientes. No existe "pass huérfano". La regresión final SIEMPRE corre sobre los
  bytes finales.
- **Presupuesto de VSX (cierre global):** es read-only — re-ejecuta la regresión
  integrada y NO introduce cambios. Un fallo en VSX se ASIGNA al hito originante y
  consume el allowance REMANENTE de ese hito; si no hay asignación causal posible ⇒
  `blocked` seguro con evidencia. JAMÁS genera rondas nuevas gratis.
- **Paradas obligatorias (sin excepciones):** techo agotado; crash de runtime o
  verificación indisponible; credenciales/permisos/consentimiento ausentes; decisión real
  de producto o técnica fuera de D1–D9/T§/TASKS; conflicto D8 que exija cambiar
  comportamiento; verificación no medible de forma fiable (denominador conflicto ⇒ bloqueo
  visible, jamás pass falso); WIP no documentado en conflicto; lock ajeno vivo (§5).
- **Prohibido:** loop-hasta-limpio sin presupuesto; gasto de tests ilimitado; invocar
  comandos/gates inexistentes (verificar antes); crear evidencia sin ejecución; prometer
  durabilidad en background (no hay servicio: este contrato ES la sesión).

## 4. Identidad, manifiesto y checkpoint (AUTORIDAD ÚNICA de esquemas)

### 4.1 Identidad/snapshot [FUTURE `scripts/voice-stack/snapshot.py`, tarea VS0.1]

`snapshot.json`: `{schema:"1", ts, repo:{head_sha, status_porcelain_sha256},
package:{files:[{path, sha256, mode}] (ordenado, TODO docs/voice-stack/)},
scopes:{<gate>:{files:[{path, sha256, mode, blob_ref}]}} , env:{os, python, node, coverage_tools}}`.
La identidad del paquete = `package` (inventario ordenado + hashes de contenido): NO
requiere commit y detecta drift/resume. La identidad de fuente de un gate = su `scope`.
**Normalización obligatoria:** el binding estable EXCLUYE campos volátiles (`ts`,
salidas/logs, run-dirs) — dos snapshots del mismo árbol se comparan por binding
normalizado, jamás por bytes de tiempo crudos. El bootstrap de §2.0 provee el inventario
inicial hasta que VS0.1 exista (sin invocar snapshot.py antes). Sin valores de
credenciales JAMÁS. `snapshot.py --blob-dir "$RUN/blobs"` guarda una copia inmutable
por sha256 de los ficheros de fuente admitidos en el inventario; `blob_ref` apunta a ella.
Los checkers calculan los diffs contra esos bytes, no contra HEAD ni contra un fichero
actual que haya cambiado. No archivar secretos ni artefactos generados: excluir las rutas
privadas y ficheros `.env*`, claves y salidas de cobertura indicados en el bootstrap.
Estos blobs son evidencia de verificación funcional, NUNCA sustitutos de los árboles
inmutables ni del contexto de inspección emitidos por el proveedor de review nativa.

### 4.2 Manifiesto de run — `~/.local/state/voice-stack-runs/<run-id>/manifest.json`

```json
{
  "schema": "1", "run_id": "20260930T170000-hito1", "milestone": "vs1",
  "started_at": "…", "finished_at": "…", "status": "complete|partial|blocked",
  "snapshot_ref": "snapshot.json (relativo al run-dir)",
  "remediation_rounds_used": {"vs0": 0, "vs1": 0, "vs2": 0, "vs3": 0, "vs4": 0},
  "tasks": [{"id": "VS1.1", "status": "pass|fail|blocked",
    "gate": {"name": "G-BRN-PY", "cmd": "coverage run --branch -m pytest tests/ -q",
             "cwd": "hosts/herdr/brain", "outcome": "pass|fail|blocked",
             "log": "tasks/VS1.1/gate.log", "binding_hash": "…",
             "coverage": {"lines_pct": 0.0, "branches_pct": 0.0,
                          "scope": "changed|module-floor", "bash": {"modified_lines_pct": 0.0,
                          "matrix_complete": true}}}}],
  "skipped_required_gates": [], "blockers": [],
  "next_checkpoint": "checkpoints/vs1.json"
}
```

Reglas: cada gate requerido aparece con comando ejecutado, salida referenciada, binding y
resultado; gate sin evidencia = no-`complete`; nada se pre-crea; `module-floor` y
`bash.matrix` se etiquetan como lo que son (piso aprobado por D4; matriz de alternativas
D9 — NUNCA presentar la matriz como porcentaje de ramas).

### 4.3 Checkpoint — `…/voice-stack-runs/checkpoints/<vsN>.json`

```json
{"schema": "1", "milestone": "vs1", "task_pointer": "VS1.4",
 "remediation_rounds": {"vs0": 0, "vs1": 1},
 "last_failed_gate": "G-BRN-PY/tasks/VS1.3",
 "binding": {"snapshot_ref": "../../<run-id>/snapshot.json"},
 "run_dir": "20260930T170000-hito1", "package_hash": "…", "status": "partial"}
```

Reanudación: leer checkpoint → re-verificar snapshot → reutilizar SÓLO gates con binding
íntegro → re-ejecutar fallado/bloqueado y todo lo dependiente de bytes cambiados. El
checkpoint lo escribe la sesión (runner script FUTURE opcional, jamás "ya instalado").

## 5. Concurrencia y cambio de fuente durante la ejecución

- **Lock de run (creado por el bootstrap §2.0; `runlock.py` FUTURE de VS0.2 MIGRA
  validando el token existente, sin re-adquisición):**
  `~/.local/state/voice-stack-runs/run.lock`, creado con `O_CREAT|O_EXCL` (atómico),
  contenido `{"pid", "started_ts", "token" (aleatorio único por owner), "session":"voice-stack"}`.
  - **Existencia del lock = OWNED:** otra sesión ⇒ STOP limpio reportando PID/ts del dueño.
  - **Liberación:** SÓLO con coincidencia exacta del token del owner (unlink condicionado
    a token); nadie libera un lock que no es suyo.
  - **Recuperación tras crash = EXPLÍCITA y confirmada, jamás automática:** un PID muerto
    NO es por sí solo permiso de reclaim; un PID VIVO cuyo cmdline no contiene
    "voice-stack" NUNCA es prueba de stale (el owner legítimo puede ser la sesión
    opencode). La única vía: parada visible, verificación humana/confirmada del estado,
    y renombre a `run.lock.stale.<ts>` CONSERVANDO copia del original, documentado en el
    manifiesto. PROHIBIDO auto-matar procesos, tomar el lock por fuerza o borrar evidencia.
- **Cambio mientras se ejecuta:** antes de CADA gate se re-verifica el snapshot del scope;
  si cambió fuera del allowlist de la tarea corriente (otro escritor) ⇒ STOP y reporte
  (regla Fase 0.3); si cambió DENTRO del flujo propio ⇒ invalidar bindings dependientes y
  re-ejecutar. Cambios en `docs/voice-stack/` ajenos a la sesión ⇒ re-vincular gates de
  paquete y re-leer el contrato si el hash del README/EXECUTION/TECHNICAL-PLAN/TASKS
  cambió. Nunca continuar en silencio.

## 6. Cierre global (VSX)

`automated_complete` SOLO si: todos los gates requeridos de TODOS los hitos re-ejecutados
sobre los bytes finales con binding final íntegro, matriz Bash completa, sin
`skipped_required_gates`, evidencias en manifiesto. Los gates de cobertura/matriz sólo
son exigibles TRAS su fundación (VS0.3–VS0.9): antes de eso su ausencia es `blocked`
visible, no un pass. Cualquier otra cosa = estado honesto (`partial|blocked`) con
blockers nombrados.

## 7. Seguridad, alcance y review nativa

- Stop de voz ≠ cancelar trabajo del agente (D1/D7): ninguna acción aprobada se cancela.
- Sin red de pago/credenciales por defecto (D3): fakes deterministas; proveedores reales
  sólo con autorización expresa del operador, fuera de gates.
- Sin push/PR/merge; commits sólo con autorización expresa separada (nunca ahora).
- **Review nativa — deferencia exacta:** la sesión LEE el modo efectivo configurado y
  nunca lo cambia ni lo activa. Deshabilitado ⇒ verificación ordinaria sin prompt extra.
  Habilitado ⇒ sigue el estado/assessment nativo del proveedor y el consentimiento exacto
  del candidato SÓLO si el flujo nativo lo devuelve; el runtime nativo es dueño de riesgo,
  lentes, corrección y acknowledge. La sesión se detiene SOLO ante requisito real de
  consentimiento humano del contrato nativo activo — sin prompts incondicionales extra.
  La review NO autoriza la entrega: la entrega es del usuario. Normalización ANTES de
  congelar candidato; después, sólo lectura.
- Independencia de canales PC/teléfono en todo momento (D2/D7); no-objetivos D7 vigentes.

## 8. Checklist final manual-física (usuario, tras `complete`)

- [ ] Auricular/Bluetooth: cancelación perceptible sin cortar otros audios.
- [ ] Acústica real PC+teléfono: pendientes audibles en orden, sin solapados ni duplicados.
- [ ] Proveedores reales (si configurados): fallback audible sin doble reproducción.
- [ ] Latencia subjetiva del primer segmento en red real.
- [ ] Ninguna acción de agente aprobada cancelada al usar el stop de voz.

## Matriz de dependencias

| Trabajo | Depende de | Razón |
|---|---|---|
| Cualquier edición de código | Fase 0 completa (incl. lock §5 y D8 confirmado) | Prerrequisito externo + exclusión de escritores |
| VS0 completo | — (primera tarea del bucle) | Identidad/instrumentación antes de tocar producción |
| VS1.* | VS0 + gate D8 | Primitiva que los demás consumen |
| VS2.* | VS1 cerrado | Cancelar segmentos requiere identidad por solicitud |
| VS3.* | VS1 cerrado | Semántica cancelación/no-interrupción diferenciada |
| VS4.* | VS1–VS3 cerrados | Sin duplicados tras parciales audibles |
| VSX | VS1–VS4 | Regresión integrada sobre bytes finales |
