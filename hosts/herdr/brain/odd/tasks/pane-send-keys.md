# Feature: Answer a Blocked Agent's Dialog from the Phone (key pad)

**Repo:** `hosts/herdr/brain` (monorepo `agent-tts`)
**Created:** 2026-10-16 · **Status:** specs congeladas; implementación pendiente
**Design:** `herdr --skill` (§ "Use logical keys for interactive agent UI controls")
**Engram mirror:** `odd/pane-send-keys/tasks`
**Risk:** item 2 (seguridad: permisos/guards) → exige verificador independiente

## Objective

Poder responder, desde el móvil, el diálogo de aprobación/pregunta en el que un
agente está bloqueado, enviando **teclas lógicas** a ese pane vía
`herdr agent send-keys <target> <keys...>`.

## Problem / Why

Cuando un agente queda `blocked` esperando un permiso, el cockpit solo muestra
un aviso: *"El agente espera en un diálogo — respóndele con teclas desde la
terminal."* El usuario reportó que **no tiene forma de aceptarla** desde el
teléfono (captura 2026-10-16 12:48: el banner espeja el diálogo de OpenCode —
`Allow once / Allow always / Reject`, `⇄ select  enter confirm` — pero son texto
plano, no botones).

Hechos verificados:

- `herdr agent prompt` **rechaza** un agente bloqueado (`agent_blocked`) antes de
  escribir un solo byte: por eso el input de respuesta se oculta ahí.
- La superficie documentada para responder un diálogo es
  `herdr agent send-keys <target> <keys...>`, con teclas lógicas. Herdr
  **valida todas las teclas antes de escribir**. El brain **no la usa en
  absoluto** hoy.

## Scope / Constraints

- **In scope:** `send_keys` en el cliente herdr, método en `BrainTools`,
  endpoint, y un **teclado de teclas** en el cockpit que sustituye al aviso
  cuando el pane mostrado está bloqueado. Tests + docs.
- **Out of scope:** parsear las opciones del diálogo y pintarlas como botones
  (**descartado**: si el mapeo etiqueta→tecla falla se responde la opción
  equivocada, y en permisos eso puede conceder "Allow always" permanente);
  el camino de voz (`send_to_session` comparte la limitación: no puede
  responder un bloqueado — feature aparte); `pane send-text`.
- **Vocabulario de teclas CERRADO** (allowlist en el servidor, nunca se
  reenvía texto libre): `enter`, `esc`, `left`, `right`, `up`, `down`, `tab`.
  Verificado contra el binario 0.9.1 (`enter`/`esc`/`escape`/`left`/`right`/
  `up`/`down`/`tab`/`space`/`ctrl+*`/`f1..f25` existen; el contrato exige
  `esc` como nombre canónico).
- Cada tecla viaja como **un argv propio**: nunca se unen en una cadena.
- Estilo: código/comentarios/tests/commits en inglés; copy de UI en español.
- Runners: `uv run python -m pytest tests/... -q` y `node --test tests/js/`.
- Entrega: unidades de trabajo (cliente+backend / frontend / docs); **sin push**.

## Specs

- **S1** — `HerdrClient.send_keys(pane_id, keys)` MUST invocar
  `herdr agent send-keys <pane_id> <key...>` con **una entrada de argv por
  tecla**. MUST NOT lanzar por un fallo del CLI: devuelve un resultado
  clasificado.
- **S2** — Las teclas MUST validarse contra la allowlist **antes** de invocar el
  CLI. Una tecla fuera de la lista MUST ser rechazada sin ejecutar nada; una
  lista vacía o desmesurada también.
- **S3** — `BrainTools.send_keys_to_pane(pane_id, keys)` MUST devolver
  `sent` | `unknown_pane` | `error`, MUST NOT lanzar, y un pane desconocido
  MUST NOT redirigirse nunca al pane activo.
- **S4** — `POST /pane/keys` con `{pane_id, keys: [...]}` MUST responder 200
  `{ok: true, pane_id}`; 404 pane desconocido; 502 fallo de herdr; 422 lista
  vacía / tecla inválida / exceso de teclas.
- **S5** — Cuando el pane mostrado está bloqueado, el cockpit MUST mostrar el
  teclado de teclas **en lugar del aviso** actual, con botones `Enter`, `Esc`,
  `←`, `→`, `↑`, `↓`, `Tab`.
- **S6** — El teclado MUST quedar deshabilitado mientras hay un envío en vuelo
  (sin doble envío); un error MUST avisar por toast y re-habilitar.
- **S7** — El envío MUST fijar el pane objetivo en el momento del toque (igual
  disciplina anti-reapuntado que el input de respuesta: un poll tardío no puede
  redirigir las teclas a otro agente).
- **S8** — Docs: el texto del aviso, `README.md` y el PRD del cockpit
  (`docs/PRD-call-drawer-redesign.md` §4.2) MUST describir el teclado.

## Tasks

- **T1** (S1, S2) — `herdr.py`: `send_keys` + allowlist + validación previa.
  *Ruta:* inline. *Estado:* pendiente.
- **T2** (S3) — `tools.py`: `send_keys_to_pane` (misma disciplina que
  `reply_to_pane`). *Ruta:* inline. *Estado:* pendiente.
- **T3** (S4) — `server.py`: `POST /pane/keys` + modelo con validador de lista.
  *Ruta:* inline. *Estado:* pendiente.
- **T4** (S5, S6, S7) — `index.html` + `app.js`: teclado, estados, target fijo.
  *Ruta:* inline. *Estado:* pendiente.
- **T5** (S1–S7) — tests: `test_herdr.py`, `test_tools.py`, `test_server.py`,
  `tests/js/` (forma del cableado) y `tests/browser/` (visibilidad bloqueado vs
  no bloqueado). *Ruta:* inline. *Estado:* pendiente.
- **T6** (S8) — docs: aviso, `README.md`, PRD de la limitación. *Ruta:* inline.
  *Estado:* pendiente.
- **T7** — **verificador independiente** sobre el diff (alto riesgo: item 2).
  *Ruta:* delegada. *Estado:* pendiente.

## Log

- **L1** — petición original (verbatim, usuario): «por otra parte, cuando me
  salta un peticion de permisos no tengo forma de aceptarla:
  /mnt/c/Users/Bruno/Screenshots/screenshot_20261016_124833.png»
- **L2** — confirmación de alcance (verbatim, usuario): «ya no me sale el
  mensaje de timeout» (arreglo previo validado en el móvil).
- **L3** — decisión de UX (verbatim, usuario): «Teclado de teclas
  (Recommended)» — elegido sobre "botones con las opciones del diálogo"
  (frágil y peligroso) y "campo de texto para teclas" (erratas que responden
  el diálogo sin querer).
- **L4** — evidencia: `herdr --skill`, línea "It rejects an agent already
  waiting at an approval or question dialog with `agent_blocked` before sending
  any input. Inspect the blocked UI and ask the user before answering it.", y
  los ejemplos `herdr agent send-keys reviewer esc` / `ctrl+c`; vocabulario de
  teclas extraído del binario 0.9.1; `grep` de `send_keys` en el brain: cero
  coincidencias.
- **L5** — **asunción marcada**: que el diálogo de OpenCode se responde con
  `left`/`right` + `enter` (deducido del espejo del banner: «⇄ select enter
  confirm»). Si en la práctica el diálogo pide otra cosa, el teclado cubre
  igualmente `up`/`down`/`tab`/`esc` y se ajusta sin cambiar el diseño.
- **L6** — gotcha heredado: cualquier edición de un fichero estático invalida
  `EXPECTED_DIGESTS` en `tests/browser/test_karaoke_harness.py` (pinnea los
  bytes servidos); recalcular desde disco y re-verificar tras cada edición.
- **L7** — corrección de S8 (2026-10-16): la redacción original exigía «el PRD
  que documenta la limitación», y ese PRD **no existía** — ninguno de los
  cuatro PRD del brain mencionaba `agent_blocked`, `send-keys` ni el aviso
  (comprobado con `grep`). Lo detectó el verificador independiente como
  blocker. S8 se reescribe para apuntar al PRD del cockpit, que es donde un
  lector buscaría la UI, y allí se añade §4.2.
- **L8** — corrección del targeting (2026-10-16): el verificador reprodujo que
  el pad podía responder al pane **B** mientras en pantalla estaba el diálogo
  del pane **A** (un poll de `/herd` mueve el foco, `/view` aún no ha
  respondido). El objetivo se ata ahora al pane que describe el payload de
  `/view` guardado (`lastView`), y `renderHerd` re-renderiza con ese mismo
  payload en vez de con el objeto `pending` suelto. Regresión en
  `tests/browser/test_pane_keys.py`.
