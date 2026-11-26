# POST-05 — Superficie GLSDK / sesiones sin pantalla `/sdk/v1`

Objetivo: superficie programática versionada `/sdk/v1` sobre el brain daemon + subcomando
`herdr-brain sdk` para operar el rebaño sin PWA. Rama `feat/post-05-sdk` (base `main` @ `975c1ac`).
Entrega: work-unit commits en la rama (sin merge/push/PR). Test runner: `PYTHONPATH=<wt>/hosts/herdr/brain/src /home/bruno/Code/personal/agent-tts/hosts/herdr/brain/.venv/bin/python -m pytest` desde `hosts/herdr/brain/`.

## Specs

Los verbos y contratos citan textualmente el brief del hito (§1/§2) y el plan aprobado por el
operador ("dale", 2026-11-26): transporte HTTP JSON bajo `/sdk/v1` en el puerto del brain, sin WS
ni stdio en V1, no JSON-RPC, los MISMOS handlers de FastAPI re-registrados bajo la ruta versionada.

- **S1 — Paridad por construcción (mismos handlers).** «reutilizando exactamente los mismos
  handlers, auth (token de dispositivo) y auditoría que HTTP». `register_sdk_surface(app, ...)`
  en `sdk.py` re-registra los objetos función de `/herd`, `/view`, `/approval/current`, `/ask` y
  `/approval/{gate_id}/action` bajo `/sdk/v1/...` con `include_in_schema=False`. Aceptación: en
  solo, `GET /sdk/v1/herd` y `GET /herd` devuelven payload byte-idéntico; ídem `view` y
  `approval/current`; `ask` y `approval action` responden los mismos contratos/status que sus rutas
  PWA. Chequeo: tests S1 en `tests/test_sdk.py` + un assert de identidad de handler
  (`app.routes` apunta a la misma función).
- **S2 — Matriz de acceso intacta (C1).** «Misama matriz de acceso y auditoría que doc 01/02
  (`check_access`, `audit`)». Las rutas `/sdk/v1/*` se registran en la misma app → las cubre el
  `AccessMiddleware` ASGI existente. Dos reglas write ADITIVAS en `access.py` (`^/sdk/v1/ask$`,
  `^/sdk/v1/approval/[^/]+/action$`) para que el SDK write-classifique igual que sus rutas PWA; el
  resto clasifica read por defecto. `POST /sdk/v1/approval/{id}/action` NO hereda la exención ntfy
  (anclada a `^/approval/...`): exige bearer de dispositivo con pairing activo — estrictamente más
  fuerte, sin bypass. Writes remotos sin `Origin` → `403 origin requerido` (doc 01, igual que el
  PWA). Aceptación: 403 «dispositivo no emparejado» en read/write SDK sin credencial con pairing
  activo; 200 con bearer válido; origin requerido en write remoto sin Origin. Enmascarado
  `secrets_mask.py`: paridad heredada — la respuesta SDK ES la respuesta del handler HTTP (mismo
  objeto función, mismas costuras de máscara).
- **S3 — Version skew legible.** «Contrato versionado (`/sdk/v1`) con negación legible ante skew,
  como el de crew». Versión clavada en la ruta; catch-all `/sdk/{version}/{rest:path}` (y
  `/sdk/{version}`) registrado ANTES del mount estático responde `404`
  `{"detail": "unknown sdk surface", "supported": "v1"}` para versiones/operaciones desconocidas —
  nunca el 404 de texto plano de StaticFiles. Aceptación: `GET /sdk/v9/herd` → 404 JSON con
  `"supported": "v1"`; `GET /sdk/v1/inventado` → 404 JSON parseable.
- **S4 — `ask` por SDK.** Mismo `TextRequest` (texto, session_id, pane_id, reset), camino legacy
  sin speech ids (respuesta textual completa para scripts). Aceptación: `POST /sdk/v1/ask` deja la
  audit line `action: "ask"` con `chars` + `device` y responde el contrato completo; mismo
  comportamiento que `POST /ask` con el mismo body.
- **S5 — Gates de aprobación por SDK, atómicos.** «Comprobar lecturas y acciones concurrentes con
  los mismos gates existentes» (ficha 09-sdk). `POST /sdk/v1/approval/{id}/action` con decision
  inválida → 422; gate muerto → 404; **resolución concurrente doble (2 threads sobre el mismo
  gate) → exactamente un 200 y un 404** (atomicidad de `approval_store.resolve`); en approve el
  replay congelado se despacha exactamente una vez (`speak=False`); audit line `approval.action`
  con `decision`, `gate_id` y `device` (el bearer del CLI es la atribución; ntfy no la lleva).
- **S6 — CLI `herdr-brain sdk` (stdlib-only, en `ops_cli.py`).** Verbos: `pair <code> [--label]`,
  `herd`, `view [pane_id] [--modal]`, `ask <text> [--pane ID] [--session ID] [--reset]`,
  `approval-current`, `approval-action <gate_id> approve|reject`. HTTP via `urllib`; URL default
  `http://127.0.0.1:<HERDR_BRAIN_PORT|8741>`, override `HERDR_BRAIN_SDK_URL`/`--url`; bearer del
  state file `<state_dir>/sdk-device-token` (0600) o `HERDR_BRAIN_SDK_TOKEN` — **nunca argv ni
  logs**; writes espejan `Origin: <base url>` (same-origin, regla doc 01). Salida: body JSON
  verbatim a stdout; errores `{"detail": ...}` a stderr con status; exit `0` ok / `1` fallo / `2`
  uso. `pair` gasta el código vía `POST /pair` (loopback) y guarda el token 0600 sin imprimirlo.
  Aceptación: tests unitarios con HTTP stubbeado (seam `_sdk_http_request`); token jamás en
  stdout/stderr; permisos 0600 del file; códigos de salida.
- **S7 — Wiring `bin/herdr-brain`.** Caso `sdk` despacha a `python -m herdr_brain.ops_cli sdk ...`
  (mismo patrón que `serve|qr|logs|config`); help actualizado con la línea `sdk`. Aceptación:
  smoke test del dispatcher del bin no rompe verbos existentes; `herdr-brain sdk` sin args →
  usage exit 2 vía ops_cli.
- **S8 — Superficie intacta y suite verde.** «No toques static PWA (index.html/app.js)»;
  «PROHIBIDO editar ROADMAP.md». `git diff --stat` confirma static/ y ROADMAP.md intactos. Suite
  completa C4: mismos 14 fallos de entorno preexistentes (baseline `test_doctor`,
  `test_launcher_repair`, `test_resolve`, `test_systemd_template`), ningún fallo nuevo.
  `ruff check` limpio en archivos nuevos/tocados.

## Tasks

- T1 — Doc ODD + espejo Engram (`odd/post-05-sdk/tasks`). Route: inline. Commit: con T2 (`10e2cc6`). ✅
- T2 — `sdk.py` (`register_sdk_surface` + catch-all skew) + `access.py` reglas write + hook en
  `server.py` + `tests/test_sdk.py` S1-S5. Route: inline (TDD RED→GREEN observado). Commit:
  `10e2cc6`. Specs: S1, S2, S3, S4, S5. ✅
- T3 — CLI `cmd_sdk` en `ops_cli.py` + wiring `bin/herdr-brain` + tests CLI (S6, S7). Route:
  inline (TDD RED→GREEN observado). Commit: `03da316`. ✅
- T4 — Suite completa + lint + verificación S8 + registro de veredictos por S# con evidencia.
  Route: inline. Commit: (este). ✅
- T5 — Verificador independiente (`odd-verifier` vía lead): veredicto sobre `093dc39` =
  **NEEDS_FIX (3 bloqueantes)** → corregido con TDD (L7), S2/S5/S6/S8 re-verificados. Re-report
  `done` enviado; segunda pasada del verificador pendiente. ⏳

## Veredictos por S# (self-verification con evidencia observada)

- **S1 ✅ paridad por construcción.** Evidencia: `TestSdkParity` (4 tests). Payloads byte-idénticos
  `/herd` vs `/sdk/v1/herd` (content bytes), `/view` y `/approval/current` (con `session_id=s1`);
  identidad de handler verificada vía `route.endpoint` — los pares legacy/SDK apuntan al MISMO
  objeto función (`test_sdk_routes_bind_the_same_handler_objects`). 20/20 tests del server surface.
- **S2 ✅ matriz intacta** *(re-verificada tras veredicto NEEDS_FIX, ver L7-1)*. Evidencia:
  `TestSdkAccess` (3 tests) con gate real (`allow_any_host=False`): read SDK sin credencial con
  pairing activo → `403 dispositivo no emparejado`, con bearer → 200; `POST /sdk/v1/ask` remoto
  sin Origin → `403 origin requerido` (idéntico al refusal de `POST /ask`, cuerpo igual), con
  Origin espejado → 200, loopback sin Origin → 200; `POST /sdk/v1/approval/{id}/action` sin
  bearer con pairing activo → 403 y gate intacto (`proposed`), con bearer → `approved`.
  **Deadlock del approval token corregido**: con `approval_token` + pairing, SDK autentica con
  device bearer (200) mientras la ruta ntfy legacy conserva AC8 (device bearer → 401; token ntfy
  → 200). Enmascarado: heredado — la respuesta SDK es la del mismo handler. Reglas write: 2
  entradas aditivas ancladas a `^/sdk/v1/...` (access.py:267-268).
- **S3 ✅ skew legible** *(+ endurecimiento L7-4)*. Evidencia: `TestSdkSkew` (6 tests):
  `/sdk/v9/herd`, `/sdk/v2/ask`, `/sdk/v9`, **`/sdk` pelado**, `/sdk/v1/inventado` → 404 JSON
  `{"detail": "unknown sdk surface", "supported": "v1"}` con `content-type: application/json`
  (nunca el 404 plano del StaticFiles); `POST /sdk/v9/ask` → misma negación antes de cualquier
  handler; **sin credencial y pairing activo, `/sdk/v9/herd` → 403 antes de revelar nada**.
- **S4 ✅ ask por SDK.** Evidencia: `TestSdkAsk` (3 tests): contrato completo de respuesta
  (`answer`, `pane_id`) con FakeLLM (`calls == ["en que estas?"]`); audit line `ask` única con
  `detail.chars` y sin `device` (omiso, no null); texto vacío → 422 igual que la ruta PWA.
- **S5 ✅ gates atómicos** *(re-verificado tras NEEDS_FIX, ver L7-1/L7-4)*. Evidencia:
  `TestSdkApprovalAction` (6 tests): `maybe` → 422 con gate live; gate muerto → 404; reject sin
  dispatch; **concurrencia 2 threads con `Barrier(2)` → statuscodes `[200, 404]`, frozen send
  despachado exactamente 1 vez**; audit `approval.action` con `decision`, `gate_id` y
  `device: "sdk-cli"` (línea ntfy sin credencial byte-idéntica —
  `test_ntfy_action_line_and_replay_line_carry_no_device` verde); **sin deadlock del approval
  token** (test AC8+SDK con ambas credenciales en sesiones distintas).
- **S6 ✅ CLI** *(re-verificado tras NEEDS_FIX, ver L7-2/L7-3)*. Evidencia: `TestSdkCli` (18
  tests) con seam `ops_cli._sdk_http_request` stubbeado: bearer del file y del env (env gana),
  URL default por `HERDR_BRAIN_PORT` (127.0.0.1:9999), sin credencial → exit 1 con hint
  `sdk pair`; **token store atómico** (0644 preexistente → 0600 vía temp O_EXCL + os.replace, sin
  hermanos remanentes; token vacío/whitespace rechazado, credencial intacta); **URL validada
  fail-closed** (file:// y `http://[::1` → stderr limpio, exit 1, cero requests); **Origin
  estricto scheme://netloc** (subpath no filtra; todo método != GET/HEAD); **gate_id validado y
  quoted** (traversal/espacios/vacíos → usage 2; `g+1` → `g%2B1`); `pair` → POST /pair, token
  jamás en stdout/stderr; `ask` body completo; `view` query `pane_id=w1%3Ap9&modal=1`;
  `approval-current`/`approval-action` mapean sus paths; HTTP 403 → stderr + exit 1 con stdout
  vacío; cerebro inalcanzable → exit 1; `sdk` pelado → 2, `sdk bogus` → SystemExit(2).
- **S7 ✅ wiring del bin.** Evidencia: caso `sdk|--sdk` en `bin/herdr-brain` → `ops_cli sdk ...`
  (mismo patrón que serve/logs/config), línea de help agregada; `bash -n` OK. Nota: el script
  bash no tiene suite propia en el repo (ningún test existente lo ejecuta) — verificación
  estructural, igual que los otros verbos.
- **S8 ✅ superficie intacta y suite verde** *(re-verificado tras NEEDS_FIX)*. Evidencia:
  `git diff` contra base contiene 0 archivos de `static/` o `ROADMAP.md`; suites requeridas por
  el verificador tras la corrección (test_sdk + test_access + test_audit + test_access_gate +
  test_approval + test_approval_ntfy + test_audit_routes + test_server + test_cutover): **413
  passed, 0 failed**; suite completa previa a la corrección: 4888 passed / 0 failed. Lint: ruff
  F,E9 limpio en sdk.py/ops_cli.py/access.py/test_sdk.py (los 6 hallazgos de server.py
  preexisten en base `975c1ac`); `bash -n` del bin OK. JS tests no aplican (cero cambios de
  JS/static).

## Log

- L1 (2026-11-26, brief del lead verbatim): «Objetivo: exponer una superficie programática durable
  y versionada (`/sdk/v1`, transporte WebSocket o stdio — a estudiar y proponer) + subcomando
  `herdr-brain sdk` para operar el rebaño sin PWA (`herd`, `view`, `ask`, `approval/current`,
  `approval/{id}/action`), reutilizando exactamente los mismos handlers, auth (token de
  dispositivo) y auditoría que HTTP.» Contratos C1-C6 del brief en §2 y límites de owns en §4.
- L2 (2026-11-26): Investigación solo-lectura completa (mapa en Engram `odd/post-05-sdk/investigation`).
  Plan propuesto en pestaña con 4 decisiones abiertas (transporte HTTP-only, toque access.py
  fuera del owns, verbo `sdk pair`, label `sdk-cli`).
- L3 (2026-11-26): Operador aprobó: «dale» — defaults aceptados (HTTP-only, +2 reglas write en
  access.py, `sdk pair` incluido, label `sdk-cli`). Arranca implementación TDD.
- L4 (2026-11-26): T2 GREEN: commit `10e2cc6` (sdk.py + access.py + hook server.py + tests S1-S5;
  20/20). Durante GREEN: el catch-all necesitó `status_code=404` explícito (FastAPI default 200);
  `pair_device` de tests reclama vía store (el `/pair` HTTP con `allow_any_host=False` exige Host
  loopback); la paridad literal del handler `/action` heredaba la línea de audit SIN device —
  fix de 1 línea en server.py: `device: audit_device(request)` en el envelope (ntfy no presenta
  credencial → línea byte-idéntica; SDK con bearer → atribución), prometido en el plan aprobado.
- L5 (2026-11-26): T3 GREEN: commit `03da316` (cmd_sdk + wiring bin + 12 tests CLI). RED→GREEN
  observado (12 fallos de atributos faltantes → implementación → 32/32).
- L6 (2026-11-26): T4: suite completa 4888/0, lint F,E9 limpio en archivos nuevos (1 F401 propio
  corregido), static/ROADMAP intactos (0 archivos en diff), scratch `.venv` de uv removido del
  worktree. Veredictos S1-S8 registrados arriba. Pendiente: verificador independiente (T5, vía
  lead tras `done`).
- L7 (2026-11-26): **Verificador independiente `odd-verifier` sobre `093dc39`: NEEDS_FIX** (3
  bloqueantes + endurecimiento). Corrección con TDD (RED→GREEN observado, 43/43 en test_sdk.py):
  1. [CRITICAL S2/S5] Deadlock de auth: con `HERDR_BRAIN_APPROVAL_TOKEN` + pairing, toda
     decisión SDK terminaba en 401/403. Fix: AC8 intacto en la ruta ntfy legacy; la ruta
     `/sdk/v1/approval/{id}/action` omite el chequeo del approval token (write-classified, exige
     device bearer, sin exención ntfy). Test: ambos canales con ambas credenciales — SDK con
     device bearer → 200; legacy con device bearer → 401 y gate intacto; legacy con token ntfy →
     200.
  2. [HIGH S6/C1] `_store_sdk_token` reescrito con el patrón atómico de pairing.py (temp hermano
     O_EXCL 0600 + os.replace + unlink en error; sin O_TRUNC sobre el target, sin ventana 0644, sin
     seguir symlinks) + validación de token (`str` no vacío) antes de guardar. Tests: fichero
     preexistente 0644 → resultado 0600 y sin temporales hermanos; respuestas sin token o con
     whitespace → exit 1 y credencial intacta.
  3. [HIGH S6] `_sdk_base_url_error` valida scheme http/https + host + caracteres de control
     (file:// y URLs malformadas → stderr limpio, exit 1, sin request); Origin computado estricto
     `scheme://netloc` (`_sdk_origin`) y enviado para todo método != GET/HEAD; gate_id validado
     (un segmento, sin `/`, `..`, espacios → usage 2) y percent-quoted (`quote(safe='')`);
     `ValueError` capturado en `_sdk_call`/`_sdk_verb_pair`; `HTTPError` cerrado en `finally`.
     Tests: file://, `http://[::1`, Origin con subpath, traversal/espacios/vacíos, quoting de
     `g+1` → `g%2B1`.
  4. [Endurecimiento] `threading.Barrier(2)` en la concurrencia de S5 (contención real);
     `/approval/{gate_id}/action` en el test de identidad de handlers; test de skew SIN
     credencial con pairing activo → 403 antes de revelar nada; audit de `/sdk/v1/ask`
     emparejado con `device` (doble LLM local que acepta el kwarg T3-fu `device` — el server ya
     lo pasaba y el doble compartido era el corto); ruta `/sdk` pelada registrada en el catch-all
     de skew; `sdk` agregado a la línea `Usage:` del bin. Nota de test: dos gates en la MISMA
     sesión se retiran entre sí (regla de nuevo trabajo deliberado) — el test AC8 usa sesiones
     `s1`/`s2`.
  Re-verificación: test_sdk.py 43/43 + test_access + test_audit + test_access_gate +
  test_approval + test_approval_ntfy + test_audit_routes + test_server + test_cutover = **413
  passed, 0 failed**; ruff F,E9 limpio en sdk.py/ops_cli.py/access.py/test_sdk.py (los 6
  hallazgos de server.py preexisten en base `975c1ac`); `bash -n` del bin OK.
